"""Async crawl worker — fetches pages, parses, deduplicates, stores."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx
import structlog

from infomesh.config import CrawlConfig
from infomesh.crawler import MAX_RESPONSE_BYTES
from infomesh.crawler.dedup import DeduplicatorDB
from infomesh.crawler.parser import ParsedPage, extract_content, extract_links
from infomesh.crawler.robots import RobotsChecker
from infomesh.crawler.scheduler import Scheduler
from infomesh.hashing import content_hash
from infomesh.security import SSRFError, validate_url

if TYPE_CHECKING:
    from infomesh.p2p.dht import InfoMeshDHT

logger = structlog.get_logger()


@dataclass
class CrawlResult:
    """Result of crawling a single URL."""

    url: str
    success: bool
    page: ParsedPage | None = None
    error: str | None = None
    elapsed_ms: float = 0.0
    discovered_links: list[str] = field(default_factory=list)


class CrawlWorker:
    """Async crawl worker that fetches, parses, and deduplicates pages.

    Usage:
        worker = CrawlWorker(config, scheduler, dedup, robots)
        result = await worker.crawl_url("https://example.com")
    """

    def __init__(
        self,
        config: CrawlConfig,
        scheduler: Scheduler,
        dedup: DeduplicatorDB,
        robots: RobotsChecker,
        *,
        dht: InfoMeshDHT | None = None,
    ) -> None:
        self._config = config
        self._scheduler = scheduler
        self._dedup = dedup
        self._robots = robots
        self._dht = dht
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self._config.user_agent},
                follow_redirects=True,
                timeout=30.0,
                limits=httpx.Limits(
                    max_connections=self._config.max_concurrent,
                    max_keepalive_connections=self._config.max_concurrent,
                ),
            )
        return self._client

    async def get_http_client(self) -> httpx.AsyncClient:
        """Public accessor for the shared HTTP client."""
        return await self._get_client()

    async def crawl_url(self, url: str, depth: int = 0) -> CrawlResult:
        """Crawl a single URL.

        Args:
            url: URL to crawl.
            depth: Current crawl depth for link following.

        Returns:
            CrawlResult with parsed page or error.
        """
        start = time.monotonic()
        lock_acquired = False

        # DHT crawl lock — prevent duplicate crawling across P2P network
        if self._dht is not None:
            try:
                lock_acquired = await self._dht.acquire_crawl_lock(url)
                if not lock_acquired:
                    return CrawlResult(
                        url=url,
                        success=False,
                        error="locked_by_peer",
                        elapsed_ms=_elapsed(start),
                    )
            except Exception:
                logger.debug("crawl_lock_attempt_failed", url=url)
                # Proceed without lock if DHT is unavailable

        try:
            return await self._crawl_url_inner(url, depth, start, lock_acquired)
        finally:
            # Release crawl lock regardless of success/failure
            if lock_acquired and self._dht is not None:
                try:
                    await self._dht.release_crawl_lock(url)
                except Exception:
                    logger.debug("crawl_lock_release_failed", url=url)

    async def _crawl_url_inner(
        self, url: str, depth: int, start: float, lock_acquired: bool
    ) -> CrawlResult:
        """Inner crawl logic, separated to ensure lock release in finally."""
        # SSRF protection — validate URL before any network request
        try:
            validate_url(url)
        except SSRFError as exc:
            logger.warning("crawl_ssrf_blocked", url=url, reason=str(exc))
            return CrawlResult(
                url=url,
                success=False,
                error=f"blocked: {exc}",
                elapsed_ms=_elapsed(start),
            )

        # Check dedup (URL)
        if self._dedup.is_url_seen(url):
            return CrawlResult(
                url=url,
                success=False,
                error="already_seen",
                elapsed_ms=_elapsed(start),
            )

        client = await self._get_client()

        # Check robots.txt
        if self._config.respect_robots:
            allowed = await self._robots.is_allowed(client, url)
            if not allowed:
                logger.info("crawl_blocked_robots", url=url)
                return CrawlResult(
                    url=url,
                    success=False,
                    error="blocked_by_robots",
                    elapsed_ms=_elapsed(start),
                )

        # Fetch page
        try:
            resp = await client.get(url, timeout=30.0)
            resp.raise_for_status()
            # Post-redirect SSRF check
            from infomesh.security import validate_url_post_redirect

            validate_url_post_redirect(str(resp.url))
        except SSRFError as exc:
            logger.warning(
                "crawl_ssrf_redirect", url=url, final=str(resp.url), reason=str(exc)
            )
            return CrawlResult(
                url=url,
                success=False,
                error=f"redirect_blocked: {exc}",
                elapsed_ms=_elapsed(start),
            )
        except httpx.HTTPStatusError as exc:
            logger.warning("crawl_http_error", url=url, status=exc.response.status_code)
            self._scheduler.mark_error(url)
            return CrawlResult(
                url=url,
                success=False,
                error=f"http_{exc.response.status_code}",
                elapsed_ms=_elapsed(start),
            )
        except httpx.HTTPError as exc:
            logger.warning("crawl_network_error", url=url, error=str(exc))
            self._scheduler.mark_error(url)
            return CrawlResult(
                url=url,
                success=False,
                error=str(exc),
                elapsed_ms=_elapsed(start),
            )

        # Check content type
        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/plain" not in content_type:
            logger.debug("crawl_skip_content_type", url=url, content_type=content_type)
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error=f"unsupported_content_type: {content_type}",
                elapsed_ms=_elapsed(start),
            )

        html = resp.text
        # Enforce response size limit
        html_bytes = html.encode("utf-8", errors="replace")
        if len(html_bytes) > MAX_RESPONSE_BYTES:
            logger.warning(
                "crawl_response_too_large",
                url=url,
                size=len(html_bytes),
            )
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error="response_too_large",
                elapsed_ms=_elapsed(start),
            )
        raw_hash = content_hash(html)

        # Parse content
        page = extract_content(html, url, raw_hash=raw_hash)
        if page is None:
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error="extraction_failed",
                elapsed_ms=_elapsed(start),
            )

        # Check dedup (content hash)
        if self._dedup.is_content_seen(page.text_hash):
            logger.debug("crawl_duplicate_content", url=url)
            self._dedup.mark_seen(url, page.text_hash, page.text)
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error="duplicate_content",
                elapsed_ms=_elapsed(start),
            )

        # Check near-duplicate (SimHash)
        if self._dedup.is_near_duplicate(page.text):
            logger.debug("crawl_near_duplicate", url=url)
            self._dedup.mark_seen(url, page.text_hash, page.text)
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error="near_duplicate",
                elapsed_ms=_elapsed(start),
            )

        # Mark as seen
        self._dedup.mark_seen(url, page.text_hash, page.text)
        self._scheduler.mark_done(url)

        # Extract and schedule child links (BFS)
        discovered: list[str] = []
        if depth < self._config.max_depth:
            discovered = extract_links(html, url)
            scheduled = 0
            for link in discovered:
                if not self._dedup.is_url_seen(link):
                    added = await self._scheduler.add_url(link, depth=depth + 1)
                    if added:
                        scheduled += 1
            if scheduled:
                logger.info(
                    "links_scheduled",
                    url=url,
                    discovered=len(discovered),
                    scheduled=scheduled,
                    next_depth=depth + 1,
                )

        elapsed = _elapsed(start)
        logger.info(
            "crawl_success",
            url=url,
            title=page.title[:60] if page.title else "",
            text_len=len(page.text),
            elapsed_ms=round(elapsed, 1),
        )

        return CrawlResult(
            url=url,
            success=True,
            page=page,
            elapsed_ms=elapsed,
            discovered_links=discovered,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _elapsed(start: float) -> float:
    """Calculate elapsed milliseconds."""
    return (time.monotonic() - start) * 1000
