"""MCP session, analytics, and webhook helper classes.

Extracted from ``mcp/server.py`` to enforce SRP — each class has
a single concern:
- ``SearchSession``: conversational search state
- ``SessionStore``: bounded session storage with TTL eviction
- ``AnalyticsTracker``: in-memory search/crawl/fetch counters
- ``WebhookRegistry``: crawl-event webhook management (SSRF-safe)
"""

from __future__ import annotations

import asyncio
import time

import structlog

logger = structlog.get_logger()

# ── Defaults ───────────────────────────────────────────────────────

_SESSION_MAX_SIZE = 1000
_SESSION_TTL_SECONDS = 3600.0  # 1 hour
_WEBHOOK_MAX_REGISTRATIONS = 20


class SearchSession:
    """Lightweight session for conversational search."""

    __slots__ = ("last_query", "last_results", "updated_at")

    def __init__(self) -> None:
        self.last_query: str = ""
        self.last_results: str = ""
        self.updated_at: float = 0.0


class SessionStore:
    """Bounded session store with TTL eviction.

    Prevents unbounded memory growth from unique session IDs.
    Evicts the oldest sessions when ``max_size`` is reached and
    removes stale entries older than ``ttl_seconds``.
    """

    __slots__ = ("_sessions", "_max_size", "_ttl")

    def __init__(
        self,
        max_size: int = _SESSION_MAX_SIZE,
        ttl_seconds: float = _SESSION_TTL_SECONDS,
    ) -> None:
        self._sessions: dict[str, SearchSession] = {}
        self._max_size = max_size
        self._ttl = ttl_seconds

    def get_or_create(self, session_id: str) -> SearchSession:
        """Return existing session or create a new one.

        Automatically evicts expired entries and enforces the
        maximum size limit by dropping the oldest sessions.
        """
        now = time.time()
        # Return existing if still valid
        existing = self._sessions.get(session_id)
        if existing is not None:
            if now - existing.updated_at < self._ttl:
                return existing
            # Expired — remove and create fresh
            del self._sessions[session_id]

        # Evict expired entries periodically (every 100 creates)
        if len(self._sessions) >= self._max_size:
            self._evict(now)

        # If still at capacity after eviction, drop oldest
        if len(self._sessions) >= self._max_size:
            oldest_key = min(
                self._sessions,
                key=lambda k: self._sessions[k].updated_at,
            )
            del self._sessions[oldest_key]

        session = SearchSession()
        self._sessions[session_id] = session
        return session

    def _evict(self, now: float) -> None:
        """Remove all sessions older than TTL."""
        expired = [
            k for k, v in self._sessions.items() if now - v.updated_at >= self._ttl
        ]
        for k in expired:
            del self._sessions[k]

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._sessions


class AnalyticsTracker:
    """In-memory search analytics (concurrency-safe).

    Uses an ``asyncio.Lock`` to protect counter updates
    under concurrent HTTP requests.
    """

    __slots__ = (
        "total_searches",
        "total_crawls",
        "total_fetches",
        "avg_latency_ms",
        "_latency_sum",
        "_lock",
    )

    def __init__(self) -> None:
        self.total_searches: int = 0
        self.total_crawls: int = 0
        self.total_fetches: int = 0
        self.avg_latency_ms: float = 0.0
        self._latency_sum: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def record_search(self, latency_ms: float) -> None:
        async with self._lock:
            self.total_searches += 1
            self._latency_sum += latency_ms
            self.avg_latency_ms = self._latency_sum / self.total_searches

    async def record_crawl(self) -> None:
        async with self._lock:
            self.total_crawls += 1

    async def record_fetch(self) -> None:
        async with self._lock:
            self.total_fetches += 1

    def to_dict(self) -> dict[str, object]:
        return {
            "total_searches": self.total_searches,
            "total_crawls": self.total_crawls,
            "total_fetches": self.total_fetches,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
        }


class WebhookRegistry:
    """In-memory webhook URL registry for crawl events.

    Limits registrations to ``max_registrations`` and validates
    URLs against SSRF before registering.
    """

    __slots__ = ("_urls", "_max_registrations")

    def __init__(
        self,
        max_registrations: int = _WEBHOOK_MAX_REGISTRATIONS,
    ) -> None:
        self._urls: list[str] = []
        self._max_registrations = max_registrations

    def register(self, url: str) -> str | None:
        """Register a webhook URL.

        Returns an error string if registration fails, else None.
        """
        from infomesh.security import SSRFError, validate_url

        try:
            validate_url(url)
        except SSRFError:
            return f"Webhook URL blocked for security: {url}"

        if url in self._urls:
            return None  # already registered

        if len(self._urls) >= self._max_registrations:
            return (
                f"Max webhooks ({self._max_registrations}) "
                "reached. Unregister one first."
            )

        self._urls.append(url)
        return None

    def unregister(self, url: str) -> bool:
        if url in self._urls:
            self._urls.remove(url)
            return True
        return False

    @property
    def urls(self) -> list[str]:
        return list(self._urls)

    async def notify(
        self,
        event: str,
        payload: dict[str, object],
    ) -> int:
        """POST event to registered webhooks concurrently.

        Returns count of successfully notified webhooks.
        Uses ``asyncio.gather`` for parallel delivery
        instead of sequential blocking.
        """
        if not self._urls:
            return 0
        import httpx

        body: dict[str, object] = {
            "event": event,
            "data": payload,
            "timestamp": time.time(),
        }

        async def _post(
            client: httpx.AsyncClient,
            url: str,
        ) -> bool:
            try:
                resp = await client.post(url, json=body)
                return resp.status_code < 400
            except Exception:  # noqa: BLE001
                logger.debug("webhook_failed", url=url)
                return False

        async with httpx.AsyncClient(timeout=5.0) as client:
            results = await asyncio.gather(*(_post(client, u) for u in self._urls))
        return sum(results)
