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
        max_depth: int = 3,
    ) -> None:
        self._politeness_delay = politeness_delay
        self._urls_per_hour = urls_per_hour
        self._pending_per_domain = pending_per_domain
        self._max_depth = max_depth

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
        if depth > self._max_depth:
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

    async def get_url(self) -> tuple[str, int]:
        """Get the next URL to crawl, respecting politeness delays.

        Returns:
            Tuple of (url, depth).
        """
        while True:
            url, depth = await self._queue.get()
            domain = urlparse(url).netloc
            state = self._domains[domain]

            # Enforce politeness delay
            elapsed = time.monotonic() - state.last_request_at
            if elapsed < self._politeness_delay:
                wait = self._politeness_delay - elapsed
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

    @property
    def pending_count(self) -> int:
        """Total number of URLs in the queue."""
        return self._queue.qsize()
