"""Retrieves grounding context for a generation request:
- schema chunks via similarity search against the user's prompt
- pattern chunks via a deterministic pull of one chunk per category
"""

from dataclasses import asdict, dataclass

import chromadb
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app import config


@dataclass
class RetrievedChunk:
    id: str
    text: str
    metadata: dict
    score: float | None = None


def _get_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(config.VECTOR_STORE_DIR))


def _get_embeddings() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(model=config.EMBEDDING_MODEL, google_api_key=config.GEMINI_API_KEY)


def retrieve_schema_chunks(prompt: str, top_k: int = 5) -> list[RetrievedChunk]:
    collection = _get_client().get_collection(config.SCHEMA_COLLECTION)
    query_embedding = _get_embeddings().embed_query(prompt, task_type="retrieval_query")

    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)

    return [
        RetrievedChunk(id=id_, text=document, metadata=metadata, score=distance)
        for id_, document, metadata, distance in zip(
            results["ids"][0], results["documents"][0], results["metadatas"][0], results["distances"][0]
        )
    ]


def retrieve_pattern_chunks() -> list[RetrievedChunk]:
    """Returns exactly one chunk per category, in a fixed order -- every
    generation gets grounding for all six sections regardless of prompt
    wording, so this is a direct lookup rather than a similarity search."""
    collection = _get_client().get_collection(config.PATTERN_COLLECTION)

    ids = [f"pattern::{category}" for category in config.PATTERN_CATEGORIES]
    results = collection.get(ids=ids)

    chunks_by_id = {
        id_: RetrievedChunk(id=id_, text=document, metadata=metadata)
        for id_, document, metadata in zip(results["ids"], results["documents"], results["metadatas"])
    }
    return [chunks_by_id[chunk_id] for chunk_id in ids]


def retrieve_context(prompt: str, top_k: int = 5) -> dict[str, list[RetrievedChunk]]:
    return {
        "schema_chunks": retrieve_schema_chunks(prompt, top_k=top_k),
        "pattern_chunks": retrieve_pattern_chunks(),
    }


def serialize_context(context: dict[str, list[RetrievedChunk]]) -> dict[str, list[dict]]:
    """Converts a retrieve_context() result into plain dicts, for JSON
    responses and history storage."""
    return {key: [asdict(chunk) for chunk in chunks] for key, chunks in context.items()}
