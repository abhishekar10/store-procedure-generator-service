"""Returns the configured implementation of each seam (see base.py).

Callers resolve these at call time, never at import time, so settings
changes (and tests) take effect without re-importing anything. One
implementation of each exists today; LLM_PROVIDER, VECTOR_STORE and
SCHEMA_SOURCE are where a second one would be selected.
"""

import threading

from app.adapters.base import LLMProvider, SchemaSource, VectorStore
from app.config import get_settings

_vector_store: VectorStore | None = None
_vector_store_lock = threading.Lock()


def llm_provider() -> LLMProvider:
    # Each setting is a Literal, so an unknown name fails at startup, not here.
    if get_settings().llm_provider == "gemini":
        from app.adapters.gemini import GeminiProvider

        return GeminiProvider()
    raise AssertionError("unreachable")


def vector_store() -> VectorStore:
    """One per process: see ChromaVectorStore for why the client is shared."""
    global _vector_store
    with _vector_store_lock:
        if _vector_store is None and get_settings().vector_store == "chroma":
            from app.adapters.chroma import ChromaVectorStore

            _vector_store = ChromaVectorStore(get_settings().vector_store_dir)
        return _vector_store


def schema_source() -> SchemaSource:
    if get_settings().schema_source == "sqlite":
        from app.adapters.sqlite_schema import SqliteSchemaSource

        return SqliteSchemaSource()
    raise AssertionError("unreachable")
