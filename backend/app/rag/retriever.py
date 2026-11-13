"""Retrieves grounding context for a generation request:
- schema chunks via similarity search against the user's prompt
- pattern chunks via a deterministic pull of one chunk per category
"""

from dataclasses import asdict, dataclass

import chromadb
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.config import PATTERN_CATEGORIES, get_settings


@dataclass
class RetrievedChunk:
    id: str
    text: str
    metadata: dict
    score: float | None = None


def _get_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(get_settings().vector_store_dir))


def _get_embeddings() -> GoogleGenerativeAIEmbeddings:
    settings = get_settings()
    return GoogleGenerativeAIEmbeddings(
        model=settings.embedding_model,
        google_api_key=settings.require_gemini_api_key(),
    )


def retrieve_schema_chunks(prompt: str, top_k: int | None = None) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = settings.retrieval_top_k if top_k is None else top_k
    collection = _get_client().get_collection(settings.schema_collection)
    query_embedding = _get_embeddings().embed_query(prompt, task_type="retrieval_query")

    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)

    return [
        RetrievedChunk(id=id_, text=document, metadata=metadata, score=distance)
        for id_, document, metadata, distance in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
            strict=True,
        )
    ]


def retrieve_pattern_chunks() -> list[RetrievedChunk]:
    """Returns exactly one chunk per category, in a fixed order -- every
    generation gets grounding for all seven sections regardless of prompt
    wording, so this is a direct lookup rather than a similarity search."""
    collection = _get_client().get_collection(get_settings().pattern_collection)

    ids = [f"pattern::{category}" for category in PATTERN_CATEGORIES]
    results = collection.get(ids=ids)

    chunks_by_id = {
        id_: RetrievedChunk(id=id_, text=document, metadata=metadata)
        for id_, document, metadata in zip(
            results["ids"], results["documents"], results["metadatas"], strict=True
        )
    }
    return [chunks_by_id[chunk_id] for chunk_id in ids]


def retrieve_context(prompt: str, top_k: int | None = None) -> dict[str, list[RetrievedChunk]]:
    return {
        "schema_chunks": retrieve_schema_chunks(prompt, top_k=top_k),
        "pattern_chunks": retrieve_pattern_chunks(),
    }


def serialize_context(context: dict[str, list[RetrievedChunk]]) -> dict[str, list[dict]]:
    """Converts a retrieve_context() result into plain dicts, for JSON
    responses and history storage."""
    return {key: [asdict(chunk) for chunk in chunks] for key, chunks in context.items()}


def check_collections() -> dict[str, int | None]:
    """Reports how many chunks each collection holds, for the readiness probe.

    Reads the local Chroma store only -- no embeddings call, so it needs no
    API key and costs nothing. A value of None means the collection could not
    be opened at all.
    """
    settings = get_settings()
    counts: dict[str, int | None] = {}
    for name in (settings.schema_collection, settings.pattern_collection):
        try:
            counts[name] = _get_client().get_collection(name).count()
        except Exception:  # noqa: BLE001 -- any failure here just means "not ready"
            counts[name] = None
    return counts
