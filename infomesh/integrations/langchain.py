"""LangChain retriever integration.

Feature #65: Provides a LangChain-compatible retriever that
uses InfoMesh as the backend.

Usage::

    from infomesh.integrations.langchain import InfoMeshRetriever

    retriever = InfoMeshRetriever(data_dir="~/.infomesh")
    docs = retriever.invoke("python asyncio tutorial")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    """LangChain-compatible Document model.

    Works standalone if langchain is not installed.
    """

    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class InfoMeshRetriever:
    """LangChain-compatible retriever backed by InfoMesh.

    Implements the retriever interface: ``invoke(query) -> list[Document]``.

    Args:
        data_dir: InfoMesh data directory.
        limit: Max documents to retrieve.
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

    def invoke(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[Document]:
        """Retrieve documents for a query.

        Args:
            query: Search query text.
            **kwargs: Additional search parameters.

        Returns:
            List of Document objects.
        """
        self._ensure_client()
        results = self._client.search(
            query,
            limit=kwargs.get("limit", self._limit),
        )
        return [
            Document(
                page_content=r.snippet,
                metadata={
                    "title": r.title,
                    "url": r.url,
                    "score": r.score,
                    "source": "infomesh",
                },
            )
            for r in results
        ]

    def get_relevant_documents(
        self,
        query: str,
    ) -> list[Document]:
        """Legacy LangChain retriever method."""
        return self.invoke(query)

    async def ainvoke(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[Document]:
        """Async retrieval."""
        self._ensure_client()
        results = await self._client.search_async(
            query,
            limit=kwargs.get("limit", self._limit),
        )
        return [
            Document(
                page_content=r.snippet,
                metadata={
                    "title": r.title,
                    "url": r.url,
                    "score": r.score,
                    "source": "infomesh",
                },
            )
            for r in results
        ]
