"""VectorStore over a local Chroma persistent store (data/vector_store/),
which is built into the image at build time and only read at runtime."""

import threading
from pathlib import Path

import chromadb

from app.adapters.base import RetrievedChunk


class ChromaVectorStore:
    """Holds one Chroma client for the process. Creating PersistentClients
    concurrently from several threads races inside chromadb's shared-system
    setup ("Could not connect to tenant default_tenant") -- reproduced with
    four parallel generations -- and FastAPI runs sync handlers on a
    threadpool. `app.adapters.vector_store()` therefore makes one of these."""

    def __init__(self, path: Path):
        self._path = path
        self._client: chromadb.ClientAPI | None = None
        self._lock = threading.Lock()

    def _get_client(self) -> chromadb.ClientAPI:
        with self._lock:
            if self._client is None:
                self._client = chromadb.PersistentClient(path=str(self._path))
            return self._client

    def sync(self, collection: str, chunks: list[RetrievedChunk], vectors: list[list[float]]) -> int:
        """Upserts in place and deletes only the ids that are gone.

        Deleting and recreating the collection on every ingest -- the old
        approach -- mints new segment UUIDs and chromadb leaves the old
        segment directories on disk: one local store had grown to 10 segment
        dirs / 13 MB for 15 embeddings, with only 2 live. Upserting keeps the
        same segments, so repeated ingests do not grow the store.

        Changing the embedding model changes the vector dimension, which an
        existing collection rejects -- delete the store directory first.
        """
        target = self._get_client().get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})
        ids = [chunk.id for chunk in chunks]
        stale = set(target.get(include=[])["ids"]) - set(ids)
        if stale:
            target.delete(ids=sorted(stale))
        target.upsert(
            ids=ids,
            documents=[chunk.text for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
            embeddings=vectors,
        )
        return target.count()

    def query(self, collection: str, vector: list[float], k: int) -> list[RetrievedChunk]:
        results = self._get_client().get_collection(collection).query(query_embeddings=[vector], n_results=k)
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

    def get(self, collection: str, ids: list[str]) -> list[RetrievedChunk]:
        results = self._get_client().get_collection(collection).get(ids=ids)
        by_id = {
            id_: RetrievedChunk(id=id_, text=document, metadata=metadata)
            for id_, document, metadata in zip(results["ids"], results["documents"], results["metadatas"], strict=True)
        }
        return [by_id[chunk_id] for chunk_id in ids]

    def count(self, collection: str) -> int | None:
        try:
            return self._get_client().get_collection(collection).count()
        except Exception:  # noqa: BLE001 -- any failure here just means "not ready"
            return None
