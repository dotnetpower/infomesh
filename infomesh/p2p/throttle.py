"""Bandwidth throttling — enforces upload/download Mbps limits.

Uses a token-bucket algorithm to limit throughput per direction.
Each direction (upload / download) gets its own ``BandwidthBucket`` that
refills at the configured rate.

Usage::

    throttle = BandwidthThrottle(upload_mbps=5.0, download_mbps=10.0)
    await throttle.acquire_upload(nbytes)    # blocks until quota available
    await throttle.acquire_download(nbytes)  # blocks until quota available
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

_BITS_PER_BYTE = 8
_MEGABIT = 1_000_000


@dataclass
class BandwidthStats:
    """Bandwidth usage statistics."""

    upload_bytes: int = 0
    download_bytes: int = 0
    upload_waits: int = 0
    download_waits: int = 0


class BandwidthBucket:
    """Token-bucket rate limiter for one direction (upload or download).

    Tokens represent bytes.  The bucket refills at ``rate_bytes_per_sec``
    and has a burst capacity of ``1 second`` worth of tokens.

    Args:
        rate_mbps: Maximum throughput in megabits per second.
    """

    def __init__(self, rate_mbps: float) -> None:
        # Convert Mbps → bytes/sec
        self._rate_bps: float = (rate_mbps * _MEGABIT) / _BITS_PER_BYTE
        self._tokens: float = self._rate_bps  # start full
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def rate_bytes_per_sec(self) -> float:
        """Maximum bytes per second."""
        return self._rate_bps

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._rate_bps,  # cap at 1-second burst
            self._tokens + elapsed * self._rate_bps,
        )
        self._last_refill = now

    async def acquire(self, nbytes: int) -> float:
        """Wait until *nbytes* tokens are available, then consume them.

        Args:
            nbytes: Number of bytes to transfer.

        Returns:
            Seconds spent waiting (0.0 if no wait needed).
        """
        if nbytes <= 0:
            return 0.0

        # If rate is 0, no throttling
        if self._rate_bps <= 0:
            return 0.0

        waited = 0.0
        async with self._lock:
            self._refill()
            while self._tokens < nbytes:
                # How long until enough tokens?
                deficit = nbytes - self._tokens
                sleep_time = deficit / self._rate_bps
                await asyncio.sleep(sleep_time)
                waited += sleep_time
                self._refill()
            self._tokens -= nbytes
        return waited


class BandwidthThrottle:
    """Two-directional bandwidth throttle (upload + download).

    Enforces upload/download throughput limits using token-bucket
    rate limiters.  A limit of ``0`` disables throttling for that
    direction.

    Args:
        upload_mbps: Maximum upload throughput in Mbps (0 = unlimited).
        download_mbps: Maximum download throughput in Mbps (0 = unlimited).
    """

    def __init__(
        self,
        upload_mbps: float = 5.0,
        download_mbps: float = 10.0,
    ) -> None:
        self._upload: BandwidthBucket | None = (
            BandwidthBucket(upload_mbps) if upload_mbps > 0 else None
        )
        self._download: BandwidthBucket | None = (
            BandwidthBucket(download_mbps) if download_mbps > 0 else None
        )
        self._stats = BandwidthStats()

    @property
    def stats(self) -> BandwidthStats:
        """Cumulative bandwidth statistics."""
        return self._stats

    async def acquire_upload(self, nbytes: int) -> float:
        """Acquire upload quota for *nbytes*.

        Returns seconds waited.  If throttling is disabled, returns 0.0.
        """
        self._stats.upload_bytes += nbytes
        if self._upload is None:
            return 0.0
        waited = await self._upload.acquire(nbytes)
        if waited > 0:
            self._stats.upload_waits += 1
        return waited

    async def acquire_download(self, nbytes: int) -> float:
        """Acquire download quota for *nbytes*.

        Returns seconds waited.  If throttling is disabled, returns 0.0.
        """
        self._stats.download_bytes += nbytes
        if self._download is None:
            return 0.0
        waited = await self._download.acquire(nbytes)
        if waited > 0:
            self._stats.download_waits += 1
        return waited
