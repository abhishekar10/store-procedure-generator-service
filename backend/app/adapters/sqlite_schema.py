"""SchemaSource over the SQLite reference database (db/reference.db) plus
the hand-written descriptions in db/metadata.json.

This is the one place the schema is read. It used to be read twice -- once
for the schema endpoint, once for ingest -- with slightly different shapes.
"""

import json
import sqlite3
from contextlib import closing

from app.adapters.base import Column, ForeignKey, Table
from app.config import get_settings


class SqliteSchemaSource:
    def available(self) -> bool:
        return get_settings().reference_db_path.is_file()

    def tables(self) -> list[Table]:
        settings = get_settings()
        table_metadata = json.loads(settings.schema_metadata_path.read_text())["tables"]
        # Read-only: a plain connect() would silently create an empty
        # database if the file were missing.
        uri = f"{settings.reference_db_path.as_uri()}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            return [self._table(connection, name, meta) for name, meta in table_metadata.items()]

    @staticmethod
    def _table(connection: sqlite3.Connection, name: str, meta: dict) -> Table:
        columns = connection.execute('SELECT name, type, "notnull", pk FROM pragma_table_info(?)', (name,)).fetchall()
        foreign_keys = connection.execute(
            'SELECT "from", "table", "to" FROM pragma_foreign_key_list(?)', (name,)
        ).fetchall()
        return Table(
            name=name,
            purpose=meta["purpose"],
            columns=tuple(
                Column(
                    name=col_name,
                    type=col_type,
                    primary_key=bool(is_pk),
                    not_null=bool(not_null),
                    description=meta["columns"].get(col_name, ""),
                )
                for col_name, col_type, not_null, is_pk in columns
            ),
            foreign_keys=tuple(ForeignKey(column=c, ref_table=t, ref_column=r) for c, t, r in foreign_keys),
        )
