"""Embeds the schema corpus (from the schema source) and the pattern corpus
(from sp_templates/) into the vector store.

Run directly to (re)build the vector store:
    python -m app.rag.ingest
"""

from app import adapters
from app.adapters.base import RetrievedChunk, Table
from app.config import PATTERN_CATEGORIES, get_settings


def schema_document(table: Table) -> str:
    """The text a table is embedded and retrieved as."""
    column_lines = []
    for column in table.columns:
        flags = [flag for flag, on in (("PK", column.primary_key), ("NOT NULL", column.not_null)) if on]
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        column_lines.append(f"  - {column.name} ({column.type}){flag_str}: {column.description}")

    document = f"Table: {table.name}\nPurpose: {table.purpose}\nColumns:\n" + "\n".join(column_lines)
    if table.foreign_keys:
        document += "\nForeign keys:\n" + "\n".join(
            f"  - {fk.column} -> {fk.ref_table}.{fk.ref_column}" for fk in table.foreign_keys
        )
    return document


def _build_schema_chunks() -> list[RetrievedChunk]:
    return [
        RetrievedChunk(id=f"schema::{table.name}", text=schema_document(table), metadata={"table_name": table.name})
        for table in adapters.schema_source().tables()
    ]


def _build_pattern_chunks() -> list[RetrievedChunk]:
    templates_dir = get_settings().sp_templates_dir
    return [
        RetrievedChunk(
            id=f"pattern::{category}",
            text=(templates_dir / f"{category}.sql").read_text(),
            metadata={"category": category},
        )
        for category in PATTERN_CATEGORIES
    ]


def run_ingest() -> None:
    settings = get_settings()
    provider = adapters.llm_provider()
    store = adapters.vector_store()

    for name, chunks in (
        (settings.schema_collection, _build_schema_chunks()),
        (settings.pattern_collection, _build_pattern_chunks()),
    ):
        vectors = provider.embed_documents([chunk.text for chunk in chunks])
        print(f"Embedded {store.sync(name, chunks, vectors)} chunks into '{name}'")


if __name__ == "__main__":
    run_ingest()
