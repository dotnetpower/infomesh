"""Dashboard data cache — single read-only SQLite connection with in-memory cache.

Avoids opening/closing SQLite connections on every dashboard tick.
Uses WAL mode so reads never block the crawler's writes.
"""

from __future__ import annotations

import contextlib
import sqlite3
import time
from dataclasses import dataclass, field

import structlog

from infomesh.config import Config

logger = structlog.get_logger()


@dataclass
class CachedStats:
    """Cached dashboard statistics."""

    document_count: int = 0
    top_domains: list[tuple[str, int]] = field(default_factory=list)
    updated_at: float = 0.0


class DashboardDataCache:
    """Lightweight read-only cache for dashboard DB queries.

    Maintains a single long-lived read-only SQLite connection and
    caches query results with a configurable TTL.  The dashboard
    can call :meth:`get_stats` at any frequency (e.g. every 0.2 s)
    without extra cost — the actual DB query only fires when the
    cache has expired.

    Args:
        config: Application configuration.
        ttl: Minimum seconds between actual DB reads (default 0.5).
    """

    def __init__(self, config: Config, *, ttl: float = 0.5) -> None:
        self._config = config
        self._ttl = ttl
        self._conn: sqlite3.Connection | None = None
        self._cache = CachedStats()

    # ── connection management ──────────────────────────────

    def _ensure_conn(self) -> sqlite3.Connection:
        """Open (or reopen) a read-only SQLite connection."""
        if self._conn is not None:
            return self._conn

        db_path = str(self._config.index.db_path)
        # Open in read-only mode via URI to avoid accidental writes;
        # fall back to normal connect for :memory: DBs used in tests.
        try:
            uri = f"file:{db_path}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError:
            self._conn = sqlite3.connect(db_path)

        self._conn.row_factory = sqlite3.Row
        # WAL mode allows concurrent reads while crawler writes
        self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    # ── public API ─────────────────────────────────────────

    def get_stats(self) -> CachedStats:
        """Return cached stats, refreshing from DB only when TTL expires."""
        now = time.monotonic()
        if now - self._cache.updated_at < self._ttl:
            return self._cache

        try:
            conn = self._ensure_conn()

            # Document count
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM documents",
            ).fetchone()
            doc_count = row["cnt"] if row else 0

            # Top domains (GROUP BY)
            domain_rows = conn.execute(
                """SELECT
                       SUBSTR(
                           url,
                           INSTR(url, '://') + 3,
                           CASE
                               WHEN INSTR(
                                   SUBSTR(url, INSTR(url, '://') + 3), '/'
                               ) > 0
                               THEN INSTR(
                                   SUBSTR(url, INSTR(url, '://') + 3), '/'
                               ) - 1
                               ELSE LENGTH(url)
                           END
                       ) AS domain,
                       COUNT(*) AS cnt
                   FROM documents
                   GROUP BY domain
                   ORDER BY cnt DESC
                   LIMIT 7""",
            ).fetchall()

            self._cache = CachedStats(
                document_count=doc_count,
                top_domains=[(r["domain"], r["cnt"]) for r in domain_rows],
                updated_at=now,
            )
        except Exception:  # noqa: BLE001
            # DB not ready yet (e.g. node hasn't started) — return stale cache
            logger.debug("data_cache_refresh_failed")

        return self._cache

    def close(self) -> None:
        """Close the read-only connection."""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None
