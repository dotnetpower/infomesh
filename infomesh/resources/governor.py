"""ResourceGovernor — dynamic resource throttling based on system load.

Monitors CPU, memory, and network usage, then adjusts crawling and
P2P activity to stay within the configured :class:`ResourceProfile` limits.

Usage::

    governor = ResourceGovernor(profile)
    governor.apply_os_priority()       # one-time: nice / ionice
    governor.check_and_adjust()        # call periodically (~5 s)
    if governor.should_throttle_crawl:
        ...  # reduce crawl rate
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from enum import IntEnum

import structlog

from infomesh.resources.profiles import ResourceProfile

logger = structlog.get_logger()

# Attempt to import psutil (optional dependency)
try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:  # pragma: no cover
    psutil = None
    _HAS_PSUTIL = False


# ── Thresholds ──────────────────────────────────────────────────────────

CPU_HIGH_PCT = 80  # Start throttling above this
CPU_LOW_PCT = 30  # Resume full speed below this
MEMORY_HIGH_PCT = 85
NETWORK_HIGH_FACTOR = 0.9  # 90 % of configured limit


class DegradeLevel(IntEnum):
    """Graceful degradation levels."""

    NORMAL = 0
    WARNING = 1  # LLM off, new crawls paused
    OVERLOADED = 2  # remote search off, local only
    SEVERE = 3  # read-only mode, no new indexing
    DEFENSIVE = 4  # hard rate-limit, minimal operation


@dataclass
class GovernorState:
    """Observable state of the governor."""

    degrade_level: int = DegradeLevel.NORMAL
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    throttle_factor: float = 1.0  # 1.0 = full speed, 0.0 = paused
    last_check: float = 0.0
    checks_performed: int = 0


class ResourceGovernor:
    """Monitor system resources and dynamically throttle activities.

    The governor is **not** async — its :meth:`check_and_adjust` method
    is designed to be called from a periodic timer (``set_interval``).
    """

    def __init__(self, profile: ResourceProfile) -> None:
        self._profile = profile
        self._state = GovernorState()
        self._net_bytes_sent_prev: int = 0
        self._net_bytes_recv_prev: int = 0
        self._last_net_check: float = 0.0

    # ── Public properties ───────────────────────────────────────────────

    @property
    def profile(self) -> ResourceProfile:
        return self._profile

    @property
    def state(self) -> GovernorState:
        return self._state

    @property
    def should_throttle_crawl(self) -> bool:
        """Return ``True`` when crawling should be slowed down."""
        return self._state.throttle_factor < 1.0

    @property
    def should_pause_crawl(self) -> bool:
        """Return ``True`` when crawling should be completely paused."""
        return self._state.degrade_level >= DegradeLevel.WARNING

    @property
    def should_disable_llm(self) -> bool:
        """Return ``True`` when LLM processing should be suspended."""
        return self._state.degrade_level >= DegradeLevel.WARNING

    @property
    def should_disable_remote_search(self) -> bool:
        """Return ``True`` when remote (P2P) search should be skipped."""
        return self._state.degrade_level >= DegradeLevel.OVERLOADED

    @property
    def is_read_only(self) -> bool:
        """Return ``True`` when only reads are allowed (no indexing)."""
        return self._state.degrade_level >= DegradeLevel.SEVERE

    @property
    def effective_max_concurrent(self) -> int:
        """Max concurrent crawl connections adjusted for current load."""
        base = self._profile.max_concurrent_crawl
        return max(1, int(base * self._state.throttle_factor))

    # ── OS-level priority ───────────────────────────────────────────────

    def apply_os_priority(self) -> None:
        """Apply nice and ionice values from the profile.

        Safe to call on any OS — non-Unix platforms are silently skipped.
        """
        if sys.platform == "win32":
            logger.debug("os_priority_skip", reason="windows")
            return

        nice_value = self._profile.cpu_nice
        try:
            current = os.nice(0)
            if current < nice_value:
                os.nice(nice_value - current)
                logger.info("os_nice_set", nice=nice_value)
        except OSError as exc:
            logger.warning("os_nice_failed", error=str(exc))

    # ── Periodic check ──────────────────────────────────────────────────

    def check_and_adjust(self) -> GovernorState:
        """Sample system metrics and adjust throttle/degrade level.

        Call this every ~5 seconds from a periodic timer.

        Returns:
            Updated :class:`GovernorState`.
        """
        now = time.monotonic()
        self._state.last_check = now
        self._state.checks_performed += 1

        cpu = self._sample_cpu()
        mem = self._sample_memory()
        self._state.cpu_percent = cpu
        self._state.memory_percent = mem

        # Determine degrade level
        level = DegradeLevel.NORMAL
        if cpu > 95 or mem > 95:
            level = DegradeLevel.DEFENSIVE
        elif cpu > 90 or mem > 90:
            level = DegradeLevel.SEVERE
        elif cpu > CPU_HIGH_PCT or mem > MEMORY_HIGH_PCT:
            level = DegradeLevel.OVERLOADED
        elif cpu > 60 or mem > 70:
            level = DegradeLevel.WARNING

        if level != self._state.degrade_level:
            logger.info(
                "governor_level_change",
                old=self._state.degrade_level,
                new=level,
                cpu=round(cpu, 1),
                mem=round(mem, 1),
            )

        self._state.degrade_level = level

        # Compute throttle factor (0.0 – 1.0)
        if level >= DegradeLevel.SEVERE:
            self._state.throttle_factor = 0.0
        elif level == DegradeLevel.OVERLOADED:
            self._state.throttle_factor = 0.25
        elif level == DegradeLevel.WARNING:
            self._state.throttle_factor = 0.5
        elif cpu > CPU_LOW_PCT:
            # Gradual slowdown between LOW and HIGH
            ratio = (cpu - CPU_LOW_PCT) / (CPU_HIGH_PCT - CPU_LOW_PCT)
            self._state.throttle_factor = max(0.3, 1.0 - ratio * 0.7)
        else:
            self._state.throttle_factor = 1.0

        return self._state

    # ── Internal samplers ───────────────────────────────────────────────

    @staticmethod
    def _sample_cpu() -> float:
        """Return current CPU usage percentage (0–100)."""
        if not _HAS_PSUTIL:
            return 0.0
        return float(psutil.cpu_percent(interval=0))

    @staticmethod
    def _sample_memory() -> float:
        """Return current memory usage percentage (0–100)."""
        if not _HAS_PSUTIL:
            return 0.0
        return float(psutil.virtual_memory().percent)

    def sample_network_mbps(self) -> tuple[float, float]:
        """Return (upload_mbps, download_mbps) since last call.

        Returns ``(0.0, 0.0)`` if psutil is unavailable or first call.
        """
        if not _HAS_PSUTIL:
            return 0.0, 0.0

        counters = psutil.net_io_counters()
        now = time.monotonic()

        if self._last_net_check == 0.0:
            self._net_bytes_sent_prev = counters.bytes_sent
            self._net_bytes_recv_prev = counters.bytes_recv
            self._last_net_check = now
            return 0.0, 0.0

        elapsed = now - self._last_net_check
        if elapsed < 0.1:
            return 0.0, 0.0

        sent_delta = counters.bytes_sent - self._net_bytes_sent_prev
        recv_delta = counters.bytes_recv - self._net_bytes_recv_prev

        self._net_bytes_sent_prev = counters.bytes_sent
        self._net_bytes_recv_prev = counters.bytes_recv
        self._last_net_check = now

        upload_mbps = (sent_delta * 8) / (elapsed * 1_000_000)
        download_mbps = (recv_delta * 8) / (elapsed * 1_000_000)
        return round(upload_mbps, 3), round(download_mbps, 3)
