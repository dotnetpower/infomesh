"""Haystack document store integration.

Feature #67: Provides a Haystack-compatible document store
and retriever that uses InfoMesh as the backend.

Usage::

    from infomesh.integrations.haystack import InfoMeshDocumentStore

    store = InfoMeshDocumentStore(data_dir="~/.infomesh")
    docs = store.query("python asyncio")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HaystackDocument:
    """Haystack-compatible document model.

    Works standalone if haystack is not installed.
    """

    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    score: float | None = None


class InfoMeshDocumentStore:
    """Haystack-compatible document store backed by InfoMesh.

    Args:
        data_dir: InfoMesh data directory.
    """

    def __init__(
        self,
        data_dir: str = "~/.infomesh",
    ) -> None:
        self._data_dir = data_dir
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        from infomesh.sdk.client import InfoMeshClient

        self._client = InfoMeshClient(data_dir=self._data_dir)

    def query(
        self,
        query: str,
        *,
        top_k: int = 10,
        **kwargs: Any,
    ) -> list[HaystackDocument]:
        """Query the document store.

        Args:
            query: Search query.
            top_k: Maximum documents to return.
            **kwargs: Additional filters.

        Returns:
            List of HaystackDocument objects.
        """
        self._ensure_client()
        results = self._client.search(query, limit=top_k)
        return [
            HaystackDocument(
                content=r.snippet,
                meta={
                    "title": r.title,
                    "url": r.url,
                    "source": "infomesh",
                },
                id=r.url,
                score=r.score,
            )
            for r in results
        ]

    def count_documents(self) -> int:
        """Return approximate document count."""
        self._ensure_client()
        stats = self._client.get_stats()
        count = stats.get("total_documents", 0)
        return int(count) if isinstance(count, (int, float)) else 0

    def write_documents(
        self,
        documents: list[HaystackDocument],
    ) -> int:
        """Write documents to the store (crawl URLs).

        Args:
            documents: Documents with URLs in meta.

        Returns:
            Number of successfully indexed documents.
        """
        self._ensure_client()
        count = 0
        for doc in documents:
            url = doc.meta.get("url", "")
            if url:
                result = self._client.crawl(str(url))
                if result.success:
                    count += 1
        return count
