"""Crawl intelligence — auto-tuning, robots cache sharing, image indexing.

Features:
- #9: robots.txt result sharing/caching
- #13: Crawl speed auto-tuning based on system load
- #12: Image alt text indexing
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


# ── #9: Robots.txt Cache ───────────────────────────────────────────


@dataclass
class RobotsCacheEntry:
    """Cached robots.txt parse result for a domain."""

    domain: str
    allowed: bool
    crawl_delay: float
    sitemaps: list[str]
    cached_at: float
    expires_at: float


class RobotsCache:
    """In-memory robots.txt result cache (shareable via DHT).

    Caches parsed robots.txt results to avoid re-fetching for
    the same domain across crawl sessions.
    """

    def __init__(self, ttl_seconds: float = 86400) -> None:
        self._cache: dict[str, RobotsCacheEntry] = {}
        self._ttl = ttl_seconds

    def get(self, domain: str) -> RobotsCacheEntry | None:
        entry = self._cache.get(domain)
        if entry and time.time() < entry.expires_at:
            return entry
        if entry:
            del self._cache[domain]
        return None

    def put(
        self,
        domain: str,
        allowed: bool,
        crawl_delay: float = 0.0,
        sitemaps: list[str] | None = None,
    ) -> RobotsCacheEntry:
        now = time.time()
        entry = RobotsCacheEntry(
            domain=domain,
            allowed=allowed,
            crawl_delay=crawl_delay,
            sitemaps=sitemaps or [],
            cached_at=now,
            expires_at=now + self._ttl,
        )
        self._cache[domain] = entry
        return entry

    def export_for_dht(self) -> list[dict[str, object]]:
        """Export cache entries for DHT sharing."""
        now = time.time()
        return [
            {
                "domain": e.domain,
                "allowed": e.allowed,
                "crawl_delay": e.crawl_delay,
                "sitemaps": e.sitemaps,
                "cached_at": e.cached_at,
            }
            for e in self._cache.values()
            if now < e.expires_at
        ]

    def import_from_dht(self, entries: list[dict[str, object]]) -> int:
        """Import cache entries from DHT peer."""
        imported = 0
        for entry in entries:
            domain = str(entry.get("domain", ""))
            if domain and domain not in self._cache:
                cd_raw = entry.get("crawl_delay", 0)
                sm_raw = entry.get("sitemaps", [])
                self.put(
                    domain,
                    bool(entry.get("allowed", True)),
                    float(cd_raw) if isinstance(cd_raw, (int, float, str)) else 0.0,
                    list(sm_raw) if isinstance(sm_raw, list) else [],
                )
                imported += 1
        return imported

    @property
    def size(self) -> int:
        return len(self._cache)

    def cleanup(self) -> int:
        now = time.time()
        expired = [k for k, v in self._cache.items() if now >= v.expires_at]
        for k in expired:
            del self._cache[k]
        return len(expired)


# ── #13: Crawl Speed Auto-Tuning ──────────────────────────────────


@dataclass
class CrawlTuningState:
    """Current crawl speed tuning state."""

    base_delay: float = 1.0
    current_delay: float = 1.0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    adjustment_reason: str = ""


class CrawlSpeedTuner:
    """Dynamically adjust crawl delay based on system load.

    Increases delay when CPU/memory is high, decreases when idle.
    """

    def __init__(
        self,
        base_delay: float = 1.0,
        min_delay: float = 0.2,
        max_delay: float = 10.0,
    ) -> None:
        self._base = base_delay
        self._min = min_delay
        self._max = max_delay
        self._current = base_delay

    def adjust(self) -> CrawlTuningState:
        """Check system load and adjust crawl delay."""
        cpu = 0.0
        mem = 0.0
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory().percent
        except ImportError:
            pass

        reason = ""
        if cpu > 90 or mem > 90:
            self._current = min(self._current * 1.5, self._max)
            reason = f"high load (CPU={cpu:.0f}%, MEM={mem:.0f}%)"
        elif cpu > 70 or mem > 80:
            self._current = min(self._current * 1.2, self._max)
            reason = f"moderate load (CPU={cpu:.0f}%, MEM={mem:.0f}%)"
        elif cpu < 30 and mem < 50:
            self._current = max(self._current * 0.8, self._min)
            reason = f"low load (CPU={cpu:.0f}%, MEM={mem:.0f}%)"
        else:
            reason = "stable"

        return CrawlTuningState(
            base_delay=self._base,
            current_delay=round(self._current, 2),
            cpu_usage=cpu,
            memory_usage=mem,
            adjustment_reason=reason,
        )

    @property
    def current_delay(self) -> float:
        return self._current


# ── #12: Image Alt Text Extraction ─────────────────────────────────

_IMG_ALT_RE = re.compile(
    r'<img\b[^>]*\balt=["\']([^"\']{3,200})["\']',
    re.IGNORECASE,
)


def extract_image_alt_texts(html: str) -> list[str]:
    """Extract meaningful alt texts from HTML img tags.

    Filters out common placeholder alts like "image", "photo", etc.
    """
    _PLACEHOLDER_ALTS = frozenset(
        {
            "image",
            "photo",
            "picture",
            "img",
            "icon",
            "logo",
            "banner",
            "thumbnail",
            "avatar",
        }
    )

    alts: list[str] = []
    for m in _IMG_ALT_RE.finditer(html):
        alt = m.group(1).strip()
        if alt.lower() not in _PLACEHOLDER_ALTS and len(alt) > 3:
            alts.append(alt)
    return alts[:50]  # Cap to prevent abuse
