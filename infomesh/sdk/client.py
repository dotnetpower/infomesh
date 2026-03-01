"""InfoMesh Python SDK — high-level client.

Feature #21: A Pythonic SDK that wraps all InfoMesh operations
so developers can use ``infomesh`` as a library without MCP.

Usage::

    from infomesh.sdk.client import InfoMeshClient

    client = InfoMeshClient(data_dir="~/.infomesh")
    results = client.search("python asyncio tutorial", limit=5)
    for r in results:
        print(r.title, r.url, r.score)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class SearchResult:
    """A search result returned by the SDK."""

    title: str
    url: str
    snippet: str
    score: float
    crawled_at: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "score": self.score,
            "crawled_at": self.crawled_at,
        }


@dataclass
class CrawlResult:
    """Result of a crawl operation."""

    url: str
    success: bool
    title: str = ""
    word_count: int = 0
    error: str = ""


@dataclass
class NetworkInfo:
    """Network status information."""

    peer_count: int = 0
    index_size: int = 0
    credit_balance: float = 0.0
    uptime_hours: float = 0.0


class InfoMeshClient:
    """High-level InfoMesh SDK client.

    Provides synchronous and async methods for all InfoMesh
    operations. Manages its own store and services.

    Args:
        data_dir: Path to data directory (default: ~/.infomesh).
        config: Optional config overrides.
    """

    def __init__(
        self,
        data_dir: str = "~/.infomesh",
        config: dict[str, Any] | None = None,
    ) -> None:
        self._data_dir = Path(data_dir).expanduser()
        self._config = config or {}
        self._store: Any = None
        self._initialized = False

    def _ensure_init(self) -> None:
        """Lazy init — create store on first use."""
        if self._initialized:
            return

        from infomesh.index.local_store import LocalStore

        db_path = self._data_dir / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._store = LocalStore(str(db_path))
        self._initialized = True

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        offset: int = 0,
        language: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
    ) -> list[SearchResult]:
        """Search the local index.

        Args:
            query: Search query.
            limit: Max results.
            offset: Pagination offset.
            language: Filter by ISO language code.
            include_domains: Only include these domains.
            exclude_domains: Exclude these domains.

        Returns:
            List of SearchResult objects.
        """
        self._ensure_init()

        from infomesh.search.query import search_local

        qr = search_local(
            self._store,
            query,
            limit=limit,
            offset=offset,
            language=language,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
        )

        return [
            SearchResult(
                title=r.title,
                url=r.url,
                snippet=r.snippet,
                score=r.combined_score,
                crawled_at=r.crawled_at,
            )
            for r in qr.results
        ]

    async def search_async(
        self,
        query: str,
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Async version of search."""
        return await asyncio.to_thread(
            self.search,
            query,
            **kwargs,
        )

    def crawl(
        self,
        url: str,
        *,
        depth: int = 0,
        force: bool = False,
    ) -> CrawlResult:
        """Crawl a URL and index it.

        Args:
            url: URL to crawl.
            depth: Crawl depth (0 = just this page).
            force: Force re-crawl even if already indexed.

        Returns:
            CrawlResult with crawl outcome.
        """
        self._ensure_init()

        try:
            import httpx

            from infomesh.crawler.parser import extract_content
            from infomesh.services import index_document

            resp = httpx.get(url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            page = extract_content(resp.text, url)
            if page is not None:
                index_document(page, self._store)
                return CrawlResult(
                    url=url,
                    success=True,
                    title=page.title,
                    word_count=len(page.text.split()),
                )
            return CrawlResult(
                url=url,
                success=False,
                error="content_extraction_failed",
            )
        except Exception as exc:
            return CrawlResult(
                url=url,
                success=False,
                error=str(exc),
            )

    def fetch_page(self, url: str) -> str:
        """Fetch the full text of a page.

        First checks local index. If not cached,
        crawls the page live.

        Args:
            url: URL to fetch.

        Returns:
            Full extracted text.
        """
        self._ensure_init()

        # Try local index first
        doc = self._store.get_document_by_url(url)
        if doc:
            return str(doc.get("content", ""))

        # Crawl live
        result = self.crawl(url)
        if result.success:
            doc = self._store.get_document_by_url(url)
            if doc:
                return str(doc.get("content", ""))

        return ""

    def suggest(
        self,
        prefix: str,
        *,
        limit: int = 5,
    ) -> list[str]:
        """Get search suggestions for a prefix.

        Args:
            prefix: Partial query text.
            limit: Max suggestions.

        Returns:
            List of suggestion strings.
        """
        self._ensure_init()
        result: list[str] = self._store.suggest(prefix, limit=limit)
        return result

    def get_stats(self) -> dict[str, object]:
        """Get index and network statistics.

        Returns:
            Dict with index_size, document_count, etc.
        """
        self._ensure_init()
        raw = self._store.get_stats()
        out: dict[str, object] = dict(raw) if raw else {}
        return out

    def close(self) -> None:
        """Close the client and release resources."""
        if self._store is not None:
            self._store.close()
            self._store = None
        self._initialized = False

    def __enter__(self) -> InfoMeshClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
