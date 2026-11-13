"""The three seams the service is built on, as Protocols, plus the plain
data types that cross them.

- LLMProvider: text completion, streaming and embeddings (Gemini).
- VectorStore: stores chunks with their vectors and searches them (Chroma).
- SchemaSource: the reference schema that generation is grounded in (SQLite).

Nothing outside `app/adapters` imports a vendor SDK for these jobs; it asks
`app.adapters` for the configured implementation. Error *mapping* is the one
exception: `app/errors.py` recognises google-genai, httpx and Chroma
exceptions, so a new implementation must raise those or be added there.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol

Purpose = Literal["classify", "generate"]


@dataclass
class RetrievedChunk:
    id: str
    text: str
    metadata: dict
    score: float | None = None


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    primary_key: bool
    not_null: bool
    description: str


@dataclass(frozen=True)
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass(frozen=True)
class Table:
    name: str
    purpose: str
    columns: tuple[Column, ...]
    foreign_keys: tuple[ForeignKey, ...]


class LLMProvider(Protocol):
    def complete(self, prompt: str, purpose: Purpose) -> str:
        """The whole response text. Timeouts and the provider's own retries
        are the implementation's; the generation retry policy is the caller's
        (app/rag/retry.py)."""
        ...

    def stream(self, prompt: str, purpose: Purpose) -> Iterator[str]:
        """Response text as it arrives, in non-empty pieces."""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def log_fields(self, purpose: Purpose) -> dict:
        """What to record about the model on the classification and
        generation log lines (e.g. model name, thinking level)."""
        ...


class VectorStore(Protocol):
    def sync(self, collection: str, chunks: list[RetrievedChunk], vectors: list[list[float]]) -> int:
        """Makes `collection` hold exactly these chunks; returns its size."""
        ...

    def query(self, collection: str, vector: list[float], k: int) -> list[RetrievedChunk]:
        """The `k` nearest chunks, nearest first, `score` set to the distance."""
        ...

    def get(self, collection: str, ids: list[str]) -> list[RetrievedChunk]:
        """These chunks, in this order. Raises KeyError if one is missing."""
        ...

    def count(self, collection: str) -> int | None:
        """Chunks held, or None if the collection cannot be opened."""
        ...


class SchemaSource(Protocol):
    def tables(self) -> list[Table]: ...

    def available(self) -> bool:
        """Whether the schema can be read, for the readiness probe. Local
        and cheap: no network call."""
        ...
