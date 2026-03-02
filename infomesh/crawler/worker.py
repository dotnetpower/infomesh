"""Async crawl worker — fetches pages, parses, deduplicates, stores."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
import structlog

from infomesh.config import CrawlConfig
from infomesh.crawler import MAX_RESPONSE_BYTES, create_ssl_context
from infomesh.crawler.dedup import DeduplicatorDB
from infomesh.crawler.parser import (
    ParsedPage,
    extract_canonical,
    extract_content,
    extract_links,
)
from infomesh.crawler.robots import RobotsChecker
from infomesh.crawler.scheduler import Scheduler
from infomesh.hashing import content_hash
from infomesh.security import SSRFError, validate_url

if TYPE_CHECKING:
    from infomesh.p2p.dht import InfoMeshDHT

logger = structlog.get_logger()

# Retry constants for transient HTTP errors (5xx)
_MAX_RETRIES = 2
_RETRY_BACKOFF_BASE = 1.0  # seconds; doubles each retry


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
        self._scope_domain: str | None = None
        self._scope_path: str | None = None

    def set_scope(self, url: str) -> None:
        """Restrict link following to the same domain + path prefix."""
        parsed = urlparse(url)
        self._scope_domain = parsed.netloc
        # Use the directory of the URL as path prefix
        path = parsed.path.rstrip("/")
        self._scope_path = path if path else "/"

    def clear_scope(self) -> None:
        """Remove domain/path scope restriction."""
        self._scope_domain = None
        self._scope_path = None

    def _in_scope(self, link: str) -> bool:
        """Check if a link is within the crawl scope."""
        if self._scope_domain is None:
            return True
        parsed = urlparse(link)
        if parsed.netloc != self._scope_domain:
            return False
        if self._scope_path and self._scope_path != "/":
            return parsed.path.startswith(self._scope_path)
        return True

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers={"User-Agent": self._config.user_agent},
                follow_redirects=True,
                timeout=30.0,
                verify=create_ssl_context(),
                limits=httpx.Limits(
                    max_connections=self._config.max_concurrent,
                    max_keepalive_connections=self._config.max_concurrent,
                ),
            )
        return self._client

    async def get_http_client(self) -> httpx.AsyncClient:
        """Public accessor for the shared HTTP client."""
        return await self._get_client()

    async def crawl_url(
        self, url: str, depth: int = 0, *, force: bool = False
    ) -> CrawlResult:
        """Crawl a single URL.

        Args:
            url: URL to crawl.
            depth: Current crawl depth for link following.
            force: If True, bypass URL dedup check and re-crawl
                even if the URL was previously crawled.

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
            return await self._crawl_url_inner(
                url, depth, start, lock_acquired, force=force
            )
        finally:
            # Release crawl lock regardless of success/failure
            if lock_acquired and self._dht is not None:
                try:
                    await self._dht.release_crawl_lock(url)
                except Exception:
                    logger.debug("crawl_lock_release_failed", url=url)

    async def _crawl_url_inner(
        self,
        url: str,
        depth: int,
        start: float,
        lock_acquired: bool,
        *,
        force: bool = False,
    ) -> CrawlResult:
        """Inner crawl logic, separated to ensure lock release in finally."""
        # SSRF protection — validate URL before any network request
        try:
            validate_url(url)
        except SSRFError as exc:
            logger.warning("crawl_ssrf_blocked", url=url, reason=str(exc))
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error=f"blocked: {exc}",
                elapsed_ms=_elapsed(start),
            )

        # Check dedup (URL) — skip when force=True
        if not force and self._dedup.is_url_seen(url):
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error="already_seen",
                elapsed_ms=_elapsed(start),
            )

        client = await self._get_client()

        # Check robots.txt (also populates crawl-delay + sitemap caches)
        if self._config.respect_robots:
            allowed = await self._robots.is_allowed(client, url)
            if not allowed:
                logger.info("crawl_blocked_robots", url=url)
                self._scheduler.mark_done(url)
                return CrawlResult(
                    url=url,
                    success=False,
                    error="blocked_by_robots",
                    elapsed_ms=_elapsed(start),
                )

            # Apply Crawl-delay from robots.txt to scheduler
            domain = urlparse(url).netloc
            crawl_delay = self._robots.get_crawl_delay(domain)
            if crawl_delay is not None:
                self._scheduler.set_crawl_delay(domain, crawl_delay)

            # Schedule Sitemap URLs for discovery
            await self._schedule_sitemap_urls(domain)

        # Fetch page with retry on 5xx
        fetch_result = await self._fetch_with_retry(client, url, start)
        if isinstance(fetch_result, CrawlResult):
            self._scheduler.mark_done(url)
            return fetch_result  # error already wrapped
        resp = fetch_result

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

        # Early Content-Length check to avoid reading huge responses
        content_length = resp.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_RESPONSE_BYTES:
                    logger.warning(
                        "crawl_response_too_large_header",
                        url=url,
                        content_length=int(content_length),
                    )
                    self._scheduler.mark_done(url)
                    return CrawlResult(
                        url=url,
                        success=False,
                        error="response_too_large",
                        elapsed_ms=_elapsed(start),
                    )
            except ValueError:
                pass  # malformed Content-Length, proceed with body check

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

        # Canonical tag — if the page declares a different canonical URL,
        # skip indexing this URL to avoid duplicate content.
        canonical = extract_canonical(html, url)
        if canonical and canonical != url and canonical != url.rstrip("/"):
            logger.debug(
                "crawl_canonical_redirect",
                url=url,
                canonical=canonical,
            )
            # Mark current URL as seen so we don't revisit it
            self._dedup.mark_seen(url, page.text_hash, page.text)
            self._scheduler.mark_done(url)
            # Schedule the canonical URL for crawling instead
            if not self._dedup.is_url_seen(canonical):
                await self._scheduler.add_url(canonical, depth=depth)
            return CrawlResult(
                url=url,
                success=False,
                error=f"canonical_redirect:{canonical}",
                elapsed_ms=_elapsed(start),
            )

        # Check dedup (content hash) — skip when force=True
        if not force and self._dedup.is_content_seen(page.text_hash):
            logger.debug("crawl_duplicate_content", url=url)
            self._dedup.mark_seen(url, page.text_hash, page.text)
            self._scheduler.mark_done(url)
            return CrawlResult(
                url=url,
                success=False,
                error="duplicate_content",
                elapsed_ms=_elapsed(start),
            )

        # Check near-duplicate (SimHash) — skip when force=True
        if not force and self._dedup.is_near_duplicate(page.text):
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
        if self._config.max_depth == 0 or depth < self._config.max_depth:
            discovered = extract_links(html, url)
            scheduled = 0
            for link in discovered:
                if not self._in_scope(link):
                    continue
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

    async def _fetch_with_retry(
        self,
        client: httpx.AsyncClient,
        url: str,
        start: float,
    ) -> httpx.Response | CrawlResult:
        """Fetch a URL with retry on transient 5xx errors.

        Returns:
            ``httpx.Response`` on success, or ``CrawlResult`` on
            permanent failure.
        """
        from infomesh.security import validate_url_post_redirect

        last_error: str = ""
        resp: httpx.Response | None = None

        for attempt in range(_MAX_RETRIES + 1):
            resp = None
            try:
                resp = await client.get(url, timeout=30.0)
                resp.raise_for_status()
                validate_url_post_redirect(str(resp.url))
                return resp
            except SSRFError as exc:
                final_url = str(resp.url) if resp is not None else url
                logger.warning(
                    "crawl_ssrf_redirect",
                    url=url,
                    final=final_url,
                    reason=str(exc),
                )
                return CrawlResult(
                    url=url,
                    success=False,
                    error=f"redirect_blocked: {exc}",
                    elapsed_ms=_elapsed(start),
                )
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if 500 <= status < 600 and attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF_BASE * (2**attempt)
                    logger.info(
                        "crawl_retry",
                        url=url,
                        status=status,
                        attempt=attempt + 1,
                        wait=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                # Non-retryable or exhausted retries
                logger.warning(
                    "crawl_http_error",
                    url=url,
                    status=status,
                )
                self._scheduler.mark_error(url)
                last_error = f"http_{status}"
                break
            except httpx.HTTPError as exc:
                if attempt < _MAX_RETRIES:
                    wait = _RETRY_BACKOFF_BASE * (2**attempt)
                    logger.info(
                        "crawl_retry_network",
                        url=url,
                        attempt=attempt + 1,
                        wait=wait,
                        error=str(exc),
                    )
                    await asyncio.sleep(wait)
                    continue
                logger.warning(
                    "crawl_network_error",
                    url=url,
                    error=str(exc),
                )
                self._scheduler.mark_error(url)
                last_error = str(exc)
                break

        return CrawlResult(
            url=url,
            success=False,
            error=last_error,
            elapsed_ms=_elapsed(start),
        )

    async def _schedule_sitemap_urls(self, domain: str) -> None:
        """Schedule URLs discovered from robots.txt Sitemap directives.

        Fetches each sitemap XML and extracts ``<loc>`` URLs, then adds
        unseen URLs to the scheduler at depth 0.
        """
        sitemaps = self._robots.get_sitemaps(domain)
        if not sitemaps:
            return

        # Only process sitemaps once per domain (use dedup URL cache)
        sitemap_key = f"__sitemap_processed__{domain}"
        if self._dedup.is_url_seen(sitemap_key):
            return
        self._dedup.mark_seen(sitemap_key, "sitemap", "")

        client = await self._get_client()
        total_scheduled = 0

        for sitemap_url in sitemaps:
            try:
                resp = await client.get(
                    sitemap_url,
                    timeout=15.0,
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    continue

                # Extract <loc> URLs from sitemap XML
                import re

                loc_urls = re.findall(
                    r"<loc>\s*(https?://[^<]+?)\s*</loc>",
                    resp.text,
                    re.IGNORECASE,
                )

                for loc_url in loc_urls:
                    if not self._dedup.is_url_seen(loc_url):
                        added = await self._scheduler.add_url(loc_url, depth=0)
                        if added:
                            total_scheduled += 1

            except (httpx.HTTPError, OSError) as exc:
                logger.debug(
                    "sitemap_fetch_error",
                    sitemap=sitemap_url,
                    error=str(exc),
                )

        if total_scheduled:
            logger.info(
                "sitemap_urls_scheduled",
                domain=domain,
                sitemaps=len(sitemaps),
                scheduled=total_scheduled,
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


def _elapsed(start: float) -> float:
    """Calculate elapsed milliseconds."""
    return (time.monotonic() - start) * 1000
