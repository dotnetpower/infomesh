"""robots.txt compliance — strict opt-out enforcement."""

from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

logger = structlog.get_logger()


class RobotsChecker:
    """Async-friendly robots.txt checker with per-domain caching."""

    # Maximum cached domains to prevent unbounded memory growth
    MAX_CACHE_SIZE: int = 10_000

    def __init__(self, user_agent: str, *, cache_ttl: int = 3600) -> None:
        self._user_agent = user_agent
        self._cache_ttl = cache_ttl
        self._cache: dict[str, tuple[RobotFileParser, float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, domain: str) -> asyncio.Lock:
        # Use setdefault for thread-safe lock creation
        return self._locks.setdefault(domain, asyncio.Lock())

    async def _fetch_robots(
        self, client: httpx.AsyncClient, base_url: str
    ) -> RobotFileParser:
        """Fetch and parse robots.txt for a domain."""
        robots_url = f"{base_url}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)

        try:
            resp = await client.get(robots_url, timeout=10.0, follow_redirects=True)
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
                logger.debug("robots_fetched", url=robots_url)
            else:
                # If robots.txt not found, allow everything
                parser.parse([])
                logger.debug(
                    "robots_not_found", url=robots_url, status=resp.status_code
                )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            # On error, be conservative — deny everything
            parser.parse(["User-agent: *", "Disallow: /"])
            logger.warning("robots_fetch_error", url=robots_url, error=str(exc))

        return parser

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
            parser = await self._fetch_robots(client, base_url)
            # Evict oldest entries if cache is full
            if len(self._cache) >= self.MAX_CACHE_SIZE:
                self._evict_oldest()
            self._cache[domain] = (parser, time.monotonic())

        return parser.can_fetch(self._user_agent, url)

    def _evict_oldest(self) -> None:
        """Remove the oldest 10% of cache entries."""
        if not self._cache:
            return
        evict_count = max(1, len(self._cache) // 10)
        oldest = sorted(self._cache.items(), key=lambda x: x[1][1])[:evict_count]
        for domain, _ in oldest:
            self._cache.pop(domain, None)
            self._locks.pop(domain, None)

    def clear_cache(self) -> None:
        """Clear the robots.txt cache."""
        self._cache.clear()
