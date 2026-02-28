"""ChromaDB vector store for semantic search.

Provides embedding-based similarity search as a complement to FTS5 keyword search.
Uses sentence-transformers for local embedding generation with all-MiniLM-L6-v2
as the default model.

This module is optional — InfoMesh works with FTS5 alone.
Enable via ``config.index.vector_search = true``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import chromadb

logger = structlog.get_logger()

# Default embedding model (384-dimensional, ~80 MB, fast on CPU)
DEFAULT_MODEL = "all-MiniLM-L6-v2"


@dataclass(frozen=True)
class VectorSearchResult:
    """A single result from the vector store."""

    doc_id: str
    url: str
    title: str
    text_preview: str
    score: float  # cosine similarity (0–1, higher = better)


class VectorStore:
    """ChromaDB-backed vector store for semantic search.

    Documents are embedded using sentence-transformers and stored in a
    persistent ChromaDB collection. Queries are embedded at search time
    and matched via cosine similarity.

    Usage::

        store = VectorStore(persist_dir=Path("~/.infomesh/chroma"))
        store.add_document(doc_id=1, url="...", title="...", text="...")
        results = store.search("how does DHT routing work?", limit=5)
    """

    def __init__(
        self,
        persist_dir: Path | str | None = None,
        model_name: str = DEFAULT_MODEL,
        collection_name: str = "infomesh_docs",
    ) -> None:
        self._persist_dir = str(persist_dir) if persist_dir else None
        self._model_name = model_name
        self._collection_name = collection_name

        # Lazy-load heavy dependencies so non-vector builds stay fast
        self._client: chromadb.ClientAPI | None = None
        self._collection: chromadb.Collection | None = None
        self._embedder: object | None = None

        self._init_store()

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def _init_store(self) -> None:
        """Initialize ChromaDB client and collection."""
        try:
            import chromadb
        except ImportError as exc:
            msg = (
                "chromadb is required for vector search. "
                "Install with: uv sync --extra vector"
            )
            raise ImportError(msg) from exc

        if self._persist_dir:
            Path(self._persist_dir).mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self._persist_dir)
        else:
            self._client = chromadb.EphemeralClient()

        self._collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        logger.info(
            "vector_store_initialized",
            persist_dir=self._persist_dir,
            collection=self._collection_name,
            model=self._model_name,
        )

    def _get_embedder(self) -> object:
        """Lazy-load the sentence-transformer model on first use."""
        if self._embedder is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                msg = (
                    "sentence-transformers is required for vector search. "
                    "Install with: uv sync --extra vector"
                )
                raise ImportError(msg) from exc

            self._embedder = SentenceTransformer(self._model_name)
            logger.info("embedding_model_loaded", model=self._model_name)
        return self._embedder

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a list of texts."""
        model = self._get_embedder()
        # sentence-transformers returns numpy arrays — convert to lists
        embeddings = model.encode(texts, show_progress_bar=False)  # type: ignore[attr-defined]
        return [emb.tolist() for emb in embeddings]

    # ------------------------------------------------------------------
    # Document management
    # ------------------------------------------------------------------

    def add_document(
        self,
        doc_id: int,
        url: str,
        title: str,
        text: str,
        *,
        language: str | None = None,
    ) -> None:
        """Add or update a document in the vector store.

        The text is truncated to ~512 tokens (~2000 chars) for embedding
        since all-MiniLM-L6-v2 has a 256-token window. We keep more context
        than the window to capture important information.

        Args:
            doc_id: Unique document ID (matches LocalStore doc_id).
            url: Source URL.
            title: Page title.
            text: Full extracted text (will be truncated for embedding).
            language: ISO language code.
        """
        assert self._collection is not None

        # Combine title + text for richer embedding
        embed_text = f"{title}. {text}"[:2000]
        embeddings = self._embed([embed_text])

        str_id = str(doc_id)
        metadata: dict[str, str | int | float] = {
            "url": url,
            "title": title,
            "text_preview": text[:500],
        }
        if language:
            metadata["language"] = language

        self._collection.upsert(
            ids=[str_id],
            embeddings=embeddings,
            metadatas=[metadata],
            documents=[embed_text],
        )

        logger.debug("vector_doc_added", doc_id=doc_id, url=url)

    def delete_document(self, doc_id: int) -> None:
        """Remove a document from the vector store."""
        assert self._collection is not None
        self._collection.delete(ids=[str(doc_id)])
        logger.debug("vector_doc_deleted", doc_id=doc_id)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> list[VectorSearchResult]:
        """Search by semantic similarity.

        Args:
            query: Natural language search query.
            limit: Maximum number of results.
            min_score: Minimum cosine similarity threshold (0–1).

        Returns:
            List of results ordered by similarity (highest first).
        """
        assert self._collection is not None

        start = time.monotonic()

        # Empty collection — return early
        count = self._collection.count()
        if count == 0:
            logger.info("vector_search", query=query[:80], results=0, elapsed_ms=0.0)
            return []

        query_embedding = self._embed([query])

        raw = self._collection.query(
            query_embeddings=query_embedding,
            n_results=min(limit, count),
            include=["metadatas", "distances"],
        )

        results: list[VectorSearchResult] = []
        if raw["ids"] and raw["ids"][0]:
            ids = raw["ids"][0]
            distances = raw["distances"][0] if raw["distances"] else []
            metadatas = raw["metadatas"][0] if raw["metadatas"] else []

            for i, doc_id in enumerate(ids):
                # ChromaDB returns cosine distance; similarity = 1 - distance
                distance = distances[i] if i < len(distances) else 1.0
                similarity = 1.0 - distance
                if similarity < min_score:
                    continue

                meta = metadatas[i] if i < len(metadatas) else {}
                results.append(
                    VectorSearchResult(
                        doc_id=doc_id,
                        url=meta.get("url", ""),
                        title=meta.get("title", ""),
                        text_preview=meta.get("text_preview", ""),
                        score=round(similarity, 4),
                    )
                )

        elapsed = (time.monotonic() - start) * 1000
        logger.info(
            "vector_search",
            query=query[:80],
            results=len(results),
            elapsed_ms=round(elapsed, 1),
        )

        return results

    # ------------------------------------------------------------------
    # Stats & lifecycle
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, int | str]:
        """Get vector store statistics."""
        assert self._collection is not None
        return {
            "document_count": self._collection.count(),
            "model": self._model_name,
            "collection": self._collection_name,
        }

    def close(self) -> None:
        """Release resources."""
        self._embedder = None
        self._collection = None
        self._client = None
        logger.debug("vector_store_closed")
