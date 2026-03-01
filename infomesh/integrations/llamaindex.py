"""LlamaIndex reader integration.

Feature #66: Provides a LlamaIndex-compatible reader/retriever
that uses InfoMesh as the backend.

Usage::

    from infomesh.integrations.llamaindex import InfoMeshReader

    reader = InfoMeshReader(data_dir="~/.infomesh")
    docs = reader.load_data("python asyncio tutorial")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LlamaDocument:
    """LlamaIndex-compatible Document model.

    Works standalone if llama_index is not installed.
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id_: str = ""
    extra_info: dict[str, Any] = field(default_factory=dict)


class InfoMeshReader:
    """LlamaIndex-compatible reader backed by InfoMesh.

    Args:
        data_dir: InfoMesh data directory.
        limit: Max documents per query.
    """

    def __init__(
        self,
        data_dir: str = "~/.infomesh",
        limit: int = 5,
    ) -> None:
        self._data_dir = data_dir
        self._limit = limit
        self._client: Any = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        from infomesh.sdk.client import InfoMeshClient

        self._client = InfoMeshClient(data_dir=self._data_dir)

    def load_data(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[LlamaDocument]:
        """Load documents from InfoMesh search.

        Args:
            query: Search query.
            **kwargs: Additional parameters.

        Returns:
            List of LlamaDocument objects.
        """
        self._ensure_client()
        results = self._client.search(
            query,
            limit=kwargs.get("limit", self._limit),
        )
        return [
            LlamaDocument(
                text=r.snippet,
                metadata={
                    "title": r.title,
                    "url": r.url,
                    "score": r.score,
                    "source": "infomesh",
                },
                id_=r.url,
            )
            for r in results
        ]

    def lazy_load_data(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[LlamaDocument]:
        """Alias for load_data (no streaming in local mode)."""
        return self.load_data(query, **kwargs)
