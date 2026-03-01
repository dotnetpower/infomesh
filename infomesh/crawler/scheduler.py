"""URL scheduling — politeness, rate limiting, domain tracking."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlparse

import structlog

logger = structlog.get_logger()


@dataclass
class DomainState:
    """Per-domain crawl state for politeness enforcement."""

    last_request_at: float = 0.0
    pending_count: int = 0
    error_count: int = 0
    crawl_delay: float | None = None  # robots.txt Crawl-delay override


# Maximum tracked domains before stale eviction triggers
_MAX_TRACKED_DOMAINS = 50_000
# Domains not accessed for this many seconds are evictable
_DOMAIN_STALE_SECONDS = 3600.0


class Scheduler:
    """URL scheduler with politeness delays and rate limiting.

    Enforces:
    - Per-domain delay (default 1s)
    - Max pending URLs per domain
    - Global URLs-per-hour limit
    - Max crawl depth
    """

    def __init__(
        self,
        *,
        politeness_delay: float = 1.0,
        urls_per_hour: int = 60,
        pending_per_domain: int = 10,
        max_depth: int = 0,
    ) -> None:
        self._politeness_delay = politeness_delay
        self._urls_per_hour = urls_per_hour
        self._pending_per_domain = pending_per_domain
        self._max_depth = max_depth  # 0 = unlimited

        self._domains: dict[str, DomainState] = defaultdict(DomainState)
        self._queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue(maxsize=10_000)
        self._hourly_count: int = 0
        self._hour_start: float = time.monotonic()

    async def add_url(self, url: str, depth: int = 0) -> bool:
        """Add a URL to the crawl queue.

        Args:
            url: URL to crawl.
            depth: Current crawl depth.

        Returns:
            True if URL was added, False if rejected (rate limit, depth, etc.).
        """
        if self._max_depth > 0 and depth > self._max_depth:
            logger.debug("scheduler_depth_exceeded", url=url, depth=depth)
            return False

        domain = urlparse(url).netloc
        state = self._domains[domain]

        if state.pending_count >= self._pending_per_domain:
            logger.debug("scheduler_domain_full", url=url, domain=domain)
            return False

        state.pending_count += 1
        await self._queue.put((url, depth))
        return True

    def set_urls_per_hour(self, limit: int) -> None:
        """Update the hourly rate limit.

        Args:
            limit: New limit. 0 means unlimited.
        """
        self._urls_per_hour = limit

    def set_crawl_delay(self, domain: str, delay: float) -> None:
        """Set a per-domain crawl delay from robots.txt.

        Args:
            domain: Domain netloc (e.g. "example.com").
            delay: Crawl delay in seconds.  Capped at 60s to prevent abuse.
        """
        capped = min(delay, 60.0)
        state = self._domains[domain]
        state.crawl_delay = capped
        logger.debug(
            "scheduler_crawl_delay_set",
            domain=domain,
            delay=capped,
        )

    async def get_url(self) -> tuple[str, int]:
        """Get the next URL to crawl, respecting politeness delays.

        Uses per-domain Crawl-delay from robots.txt when available,
        otherwise falls back to the configured ``politeness_delay``.

        Returns:
            Tuple of (url, depth).
        """
        while True:
            url, depth = await self._queue.get()
            domain = urlparse(url).netloc
            state = self._domains[domain]

            # Use robots.txt Crawl-delay if available, else default
            delay = (
                state.crawl_delay
                if state.crawl_delay is not None
                else self._politeness_delay
            )

            # Enforce politeness delay
            elapsed = time.monotonic() - state.last_request_at
            if elapsed < delay:
                wait = delay - elapsed
                await asyncio.sleep(wait)

            # Check hourly rate limit (0 = unlimited)
            if self._urls_per_hour > 0:
                self._refresh_hour()
                if self._hourly_count >= self._urls_per_hour:
                    remaining = 3600 - (time.monotonic() - self._hour_start)
                    wait_secs = max(remaining, 1.0)
                    logger.info(
                        "scheduler_hourly_limit",
                        count=self._hourly_count,
                        wait_secs=round(wait_secs),
                    )
                    # Put it back and wait until the hour resets
                    await self._queue.put((url, depth))
                    await asyncio.sleep(wait_secs)
                    continue

            state.last_request_at = time.monotonic()
            if self._urls_per_hour > 0:
                self._hourly_count += 1
            return url, depth

    def mark_done(self, url: str) -> None:
        """Mark a URL as done (reduce pending count)."""
        domain = urlparse(url).netloc
        state = self._domains[domain]
        state.pending_count = max(0, state.pending_count - 1)

    def mark_error(self, url: str) -> None:
        """Mark a URL as errored."""
        domain = urlparse(url).netloc
        self._domains[domain].error_count += 1
        self.mark_done(url)

    def _refresh_hour(self) -> None:
        """Reset hourly counter if an hour has elapsed."""
        now = time.monotonic()
        if now - self._hour_start >= 3600:
            self._hourly_count = 0
            self._hour_start = now
            # Prune stale domains on hour boundary
            self._prune_stale_domains()

    def _prune_stale_domains(self) -> None:
        """Remove domains not accessed within ``_DOMAIN_STALE_SECONDS``."""
        if len(self._domains) <= _MAX_TRACKED_DOMAINS:
            return
        cutoff = time.monotonic() - _DOMAIN_STALE_SECONDS
        stale = [
            d
            for d, s in self._domains.items()
            if s.last_request_at < cutoff and s.pending_count == 0
        ]
        for d in stale:
            del self._domains[d]
        if stale:
            logger.debug("scheduler_domains_pruned", count=len(stale))

    @property
    def pending_count(self) -> int:
        """Total number of URLs in the queue."""
        return self._queue.qsize()
