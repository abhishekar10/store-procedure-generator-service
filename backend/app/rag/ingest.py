"""Embeds the schema corpus (from db/metadata.json + reference.db) and the
pattern corpus (from sp_templates/) into the local Chroma vector store.

Run directly to (re)build the vector store:
    python -m app.rag.ingest
"""

import json
import sqlite3

import chromadb
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import PATTERN_CATEGORIES, get_settings


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


def _replace_collection(client: chromadb.ClientAPI, name: str) -> chromadb.Collection:
    existing_names = {collection.name for collection in client.list_collections()}
    if name in existing_names:
        client.delete_collection(name)
    return client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})


def run_ingest() -> None:
    settings = get_settings()
    embeddings = GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model,
        google_api_key=settings.require_gemini_api_key(),
    )
    client = chromadb.PersistentClient(path=str(settings.vector_store_dir))

    schema_ids, schema_docs, schema_metadatas = _build_schema_chunks()
    pattern_ids, pattern_docs, pattern_metadatas = _build_pattern_chunks()

    schema_collection = _replace_collection(client, settings.schema_collection)
    schema_collection.add(
        ids=schema_ids,
        documents=schema_docs,
        metadatas=schema_metadatas,
        embeddings=embeddings.embed_documents(schema_docs, task_type="retrieval_document"),
    )

    pattern_collection = _replace_collection(client, settings.pattern_collection)
    pattern_collection.add(
        ids=pattern_ids,
        documents=pattern_docs,
        metadatas=pattern_metadatas,
        embeddings=embeddings.embed_documents(pattern_docs, task_type="retrieval_document"),
    )

    print(f"Embedded {schema_collection.count()} schema chunks into '{settings.schema_collection}'")
    print(f"Embedded {pattern_collection.count()} pattern chunks into '{settings.pattern_collection}'")


if __name__ == "__main__":
    run_ingest()
