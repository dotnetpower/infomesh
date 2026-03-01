"""robots.txt compliance — strict opt-out enforcement.

Features:
- Per-domain caching with configurable TTL
- Crawl-delay: extracts and applies per-domain crawl delay from robots.txt
- Sitemap discovery: extracts Sitemap URLs from robots.txt
"""

from __future__ import annotations

import asyncio
import re
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

logger = structlog.get_logger()

# Regex to extract Sitemap URLs from robots.txt
_SITEMAP_RE = re.compile(r"^Sitemap:\s*(\S+)", re.IGNORECASE | re.MULTILINE)

# Regex to extract Crawl-delay for any user-agent block
_CRAWL_DELAY_RE = re.compile(
    r"^Crawl-delay:\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE | re.MULTILINE,
)


class RobotsChecker:
    """Async-friendly robots.txt checker with per-domain caching.

    Also extracts Sitemap URLs and Crawl-delay directives.
    """

    # Maximum cached domains to prevent unbounded memory growth
    MAX_CACHE_SIZE: int = 10_000

    def __init__(self, user_agent: str, *, cache_ttl: int = 3600) -> None:
        self._user_agent = user_agent
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Extra caches for sitemap URLs and crawl-delay per domain
        self._sitemaps: dict[str, list[str]] = {}
        self._crawl_delays: dict[str, float | None] = {}

    def _get_lock(self, domain: str) -> asyncio.Lock:
        # Use setdefault for thread-safe lock creation
        return self._locks.setdefault(domain, asyncio.Lock())

    async def _fetch_robots(
        self, client: httpx.AsyncClient, base_url: str
    ) -> tuple[RobotFileParser, list[str], float | None]:
        """Fetch and parse robots.txt for a domain.

        Returns:
            Tuple of (parser, sitemap_urls, crawl_delay).
        """
        robots_url = f"{base_url}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        sitemaps: list[str] = []
        crawl_delay: float | None = None

        try:
            resp = await client.get(
                robots_url,
                timeout=10.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                raw_text = resp.text
                parser.parse(raw_text.splitlines())

                # Extract Sitemap URLs
                sitemaps = _SITEMAP_RE.findall(raw_text)

                # Extract Crawl-delay (use the first value found)
                delay_match = _CRAWL_DELAY_RE.search(raw_text)
                if delay_match:
                    crawl_delay = float(delay_match.group(1))

                logger.debug(
                    "robots_fetched",
                    url=robots_url,
                    sitemaps=len(sitemaps),
                    crawl_delay=crawl_delay,
                )
            else:
                # If robots.txt not found, allow everything
                parser.parse([])
                logger.debug(
                    "robots_not_found",
                    url=robots_url,
                    status=resp.status_code,
                )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            # On fetch error (network, SSL, timeout, etc.) be permissive —
            # RFC 9309 §2.4: if robots.txt is unreachable, assume allowed.
            # Only an explicit Disallow in a successfully-fetched robots.txt
            # should block crawling.
            parser.parse([])
            logger.warning(
                "robots_fetch_error",
                url=robots_url,
                error=str(exc),
            )

        return parser, sitemaps, crawl_delay

    async def is_allowed(self, client: httpx.AsyncClient, url: str) -> bool:
        """Check if URL is allowed by robots.txt.

        Args:
            client: Async HTTP client.
            url: URL to check.

        Returns:
            True if crawling is allowed.
        """
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        domain = parsed.netloc

        lock = self._get_lock(domain)
        async with lock:
            # Check cache
            if domain in self._cache:
                parser, cached_at = self._cache[domain]
                if time.monotonic() - cached_at < self._cache_ttl:
                    return parser.can_fetch(self._user_agent, url)

            # Fetch fresh
            parser, sitemaps, crawl_delay = await self._fetch_robots(
                client,
                base_url,
            )
            # Evict oldest entries if cache is full
            if len(self._cache) >= self.MAX_CACHE_SIZE:
                self._evict_oldest()
            self._cache[domain] = (parser, time.monotonic())
            self._sitemaps[domain] = sitemaps
            self._crawl_delays[domain] = crawl_delay

        return parser.can_fetch(self._user_agent, url)

    def get_sitemaps(self, domain: str) -> list[str]:
        """Return Sitemap URLs discovered from robots.txt for a domain.

        Must call ``is_allowed()`` first to populate the cache.
        """
        return self._sitemaps.get(domain, [])

    def get_crawl_delay(self, domain: str) -> float | None:
        """Return Crawl-delay from robots.txt for a domain (seconds).

        Returns None if no Crawl-delay directive was found.
        Must call ``is_allowed()`` first to populate the cache.
        """
        return self._crawl_delays.get(domain)

    def _evict_oldest(self) -> None:
        """Remove the oldest 10% of cache entries."""
        if not self._cache:
            return
        evict_count = max(1, len(self._cache) // 10)
        oldest = sorted(
            self._cache.items(),
            key=lambda x: x[1][1],
        )[:evict_count]
        for domain, _ in oldest:
            self._cache.pop(domain, None)
            self._locks.pop(domain, None)
            self._sitemaps.pop(domain, None)
            self._crawl_delays.pop(domain, None)

    def clear_cache(self) -> None:
        """Clear the robots.txt cache."""
        self._cache.clear()
        self._sitemaps.clear()
        self._crawl_delays.clear()
