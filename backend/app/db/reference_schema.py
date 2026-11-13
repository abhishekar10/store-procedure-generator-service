"""Read-only access to the HR/payroll reference schema (db/reference.db),
used by the schema explorer endpoint."""

import json
import sqlite3

from app.config import get_settings


def list_tables() -> list[dict]:
    settings = get_settings()
    table_metadata = json.loads(settings.schema_metadata_path.read_text())["tables"]
    connection = sqlite3.connect(settings.reference_db_path)

    tables = []
    for table_name, table_meta in table_metadata.items():
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        tables.append(
            {
                "table_name": table_name,
                "purpose": table_meta["purpose"],
                "columns": [
                    {
                        "name": col_name,
                        "type": col_type,
                        "primary_key": bool(is_pk),
                        "not_null": bool(notnull),
                        "description": table_meta["columns"].get(col_name, ""),
                    }
                    for _, col_name, col_type, notnull, _, is_pk in columns
                ],
            }
        )

    connection.close()
    return tables
