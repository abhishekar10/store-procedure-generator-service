"""Retrieves grounding context for a generation request:
- schema chunks via similarity search against the user's prompt
- pattern chunks via a deterministic pull of one chunk per category
"""

from dataclasses import asdict

from app import adapters
from app.adapters.base import RetrievedChunk
from app.config import PATTERN_CATEGORIES, get_settings


def retrieve_schema_chunks(prompt: str, top_k: int | None = None) -> list[RetrievedChunk]:
    settings = get_settings()
    top_k = settings.retrieval_top_k if top_k is None else top_k
    vector = adapters.llm_provider().embed_query(prompt)
    return adapters.vector_store().query(settings.schema_collection, vector, top_k)


def retrieve_pattern_chunks() -> list[RetrievedChunk]:
    """Returns exactly one chunk per category, in a fixed order -- every
    generation gets grounding for all seven sections regardless of prompt
    wording, so this is a direct lookup rather than a similarity search."""
    ids = [f"pattern::{category}" for category in PATTERN_CATEGORIES]
    return adapters.vector_store().get(get_settings().pattern_collection, ids)


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

    Reads the local vector store only -- no embeddings call, so it needs no
    API key and costs nothing. A value of None means the collection could not
    be opened at all.
    """
    settings = get_settings()
    store = adapters.vector_store()
    return {name: store.count(name) for name in (settings.schema_collection, settings.pattern_collection)}
