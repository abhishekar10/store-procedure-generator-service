"""Embeds the schema corpus (from db/metadata.json + reference.db) and the
pattern corpus (from sp_templates/) into the local Chroma vector store.

Run directly to (re)build the vector store:
    python -m app.rag.ingest
"""

import json
import sqlite3

import chromadb

from app.config import PATTERN_CATEGORIES, get_settings
from app.rag.llm import build_embeddings


def _load_table_metadata() -> dict:
    return json.loads(get_settings().schema_metadata_path.read_text())["tables"]


def _build_schema_chunks() -> tuple[list[str], list[str], list[dict]]:
    table_metadata = _load_table_metadata()
    connection = sqlite3.connect(get_settings().reference_db_path)

    ids, documents, metadatas = [], [], []
    for table_name, table_meta in table_metadata.items():
        columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        foreign_keys = connection.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()

        column_lines = []
        for _, col_name, col_type, notnull, _, is_pk in columns:
            flags = []
            if is_pk:
                flags.append("PK")
            if notnull:
                flags.append("NOT NULL")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            description = table_meta["columns"].get(col_name, "")
            column_lines.append(f"  - {col_name} ({col_type}){flag_str}: {description}")

        fk_lines = [f"  - {from_col} -> {ref_table}.{to_col}" for _, _, ref_table, from_col, to_col, *_ in foreign_keys]

        document = (
            f"Table: {table_name}\n"
            f"Purpose: {table_meta['purpose']}\n"
            f"Columns:\n" + "\n".join(column_lines)
        )
        if fk_lines:
            document += "\nForeign keys:\n" + "\n".join(fk_lines)

        ids.append(f"schema::{table_name}")
        documents.append(document)
        metadatas.append({"table_name": table_name})

    connection.close()
    return ids, documents, metadatas


def _build_pattern_chunks() -> tuple[list[str], list[str], list[dict]]:
    ids, documents, metadatas = [], [], []
    for category in PATTERN_CATEGORIES:
        path = get_settings().sp_templates_dir / f"{category}.sql"
        ids.append(f"pattern::{category}")
        documents.append(path.read_text())
        metadatas.append({"category": category})
    return ids, documents, metadatas


def _sync_collection(
    client: chromadb.ClientAPI,
    name: str,
    ids: list[str],
    documents: list[str],
    metadatas: list[dict],
    embeddings: list[list[float]],
) -> chromadb.Collection:
    """Makes the collection hold exactly these chunks, in place.

    The old version deleted and recreated the collection on every run. Each
    recreate mints new segment UUIDs and chromadb leaves the old segment
    directories on disk: one local store had grown to 10 segment dirs / 13 MB
    for 15 embeddings, with only 2 live. Upserting into the existing
    collection and deleting only the ids that are gone keeps the same
    segments, so repeated ingests do not grow the store.

    Changing the embedding model changes the vector dimension, which an
    existing collection rejects -- delete the vector store directory first.
    """
    collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    stale = set(collection.get(include=[])["ids"]) - set(ids)
    if stale:
        collection.delete(ids=sorted(stale))
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)
    return collection


def run_ingest() -> None:
    settings = get_settings()
    embeddings = build_embeddings()
    client = chromadb.PersistentClient(path=str(settings.vector_store_dir))

    for name, (ids, documents, metadatas) in (
        (settings.schema_collection, _build_schema_chunks()),
        (settings.pattern_collection, _build_pattern_chunks()),
    ):
        vectors = embeddings.embed_documents(documents, task_type="retrieval_document")
        collection = _sync_collection(client, name, ids, documents, metadatas, vectors)
        print(f"Embedded {collection.count()} chunks into '{name}'")


if __name__ == "__main__":
    run_ingest()
