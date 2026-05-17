"""Implicit search quality feedback — LLM-native signal tracking.

Tracks implicit quality signals from MCP tool usage patterns:
- ``web_search`` → ``fetch_page`` conversion (result #3 fetched but #1 not)
- Re-search detection (same query reformulated within 60 s)
- ``fact_check`` citations (results used as evidence)

All signals are stored locally in SQLite (never shared via P2P).
Query text is stored as SHA-256 hash only — zero plaintext leakage.

Privacy:
- Opt-out via config: ``[search] feedback_tracking = false``
- All data local-only, never transmitted
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

_REFORMULATION_WINDOW = 60.0  # seconds — re-search within this = poor results
_BOOST_DECAY = 0.95  # EMA factor for URL boost scores
_MAX_SIGNALS = 100_000  # max signal rows before pruning old entries


# ── Data types ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FeedbackSignal:
    """A single implicit feedback event."""

    query_hash: str
    result_url: str
    action: str  # "fetched", "skipped", "reformulated", "cited"
    result_rank: int  # 1-based position in original results
    timestamp: float


@dataclass(frozen=True)
class URLBoost:
    """Accumulated quality signal for a URL."""

    url: str
    boost_score: float  # positive = good, negative = demote
    fetch_count: int
    skip_count: int
    cite_count: int


# ── Feedback Store ──────────────────────────────────────────────────────


class FeedbackStore:
    """SQLite-backed implicit feedback signal store.

    Thread-safe (check_same_thread=False, WAL mode).
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or ":memory:"
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=3000")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS feedback_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT NOT NULL,
                result_url TEXT NOT NULL,
                action TEXT NOT NULL,
                result_rank INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_feedback_url
                ON feedback_signals(result_url);
            CREATE INDEX IF NOT EXISTS idx_feedback_query
                ON feedback_signals(query_hash);
            CREATE INDEX IF NOT EXISTS idx_feedback_time
                ON feedback_signals(created_at);

            CREATE TABLE IF NOT EXISTS url_boosts (
                url TEXT PRIMARY KEY,
                boost_score REAL NOT NULL DEFAULT 0.0,
                fetch_count INTEGER NOT NULL DEFAULT 0,
                skip_count INTEGER NOT NULL DEFAULT 0,
                cite_count INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
        """)
        self._conn.commit()

    # ── Signal recording ────────────────────────────────────

    @staticmethod
    def hash_query(query: str) -> str:
        """SHA-256 hash a query string for privacy."""
        return hashlib.sha256(query.strip().lower().encode()).hexdigest()

    def record_fetch(
        self,
        query: str,
        fetched_url: str,
        result_rank: int,
    ) -> None:
        """Record that a search result was fetched (positive signal)."""
        qh = self.hash_query(query)
        now = time.time()
        self._conn.execute(
            "INSERT INTO feedback_signals"
            " (query_hash, result_url, action, result_rank, created_at)"
            " VALUES (?, ?, 'fetched', ?, ?)",
            (qh, fetched_url, result_rank, now),
        )
        self._update_boost(fetched_url, fetch_delta=1)
        self._conn.commit()
        self._maybe_prune()

    def record_skip(
        self,
        query: str,
        skipped_urls: list[str],
    ) -> None:
        """Record that results were skipped (negative signal)."""
        qh = self.hash_query(query)
        now = time.time()
        for url in skipped_urls:
            self._conn.execute(
                "INSERT INTO feedback_signals"
                " (query_hash, result_url, action, result_rank, created_at)"
                " VALUES (?, ?, 'skipped', 0, ?)",
                (qh, url, now),
            )
            self._update_boost(url, skip_delta=1)
        self._conn.commit()

    def record_reformulation(self, query: str) -> None:
        """Record a query reformulation (previous results were poor)."""
        qh = self.hash_query(query)
        now = time.time()
        self._conn.execute(
            "INSERT INTO feedback_signals"
            " (query_hash, result_url, action, result_rank, created_at)"
            " VALUES (?, '', 'reformulated', 0, ?)",
            (qh, now),
        )
        self._conn.commit()

    def record_citation(self, query: str, cited_url: str) -> None:
        """Record that a URL was cited in fact-checking (strong signal)."""
        qh = self.hash_query(query)
        now = time.time()
        self._conn.execute(
            "INSERT INTO feedback_signals"
            " (query_hash, result_url, action, result_rank, created_at)"
            " VALUES (?, ?, 'cited', 0, ?)",
            (qh, cited_url, now),
        )
        self._update_boost(cited_url, cite_delta=1)
        self._conn.commit()

    # ── Boost management ────────────────────────────────────

    def _update_boost(
        self,
        url: str,
        fetch_delta: int = 0,
        skip_delta: int = 0,
        cite_delta: int = 0,
    ) -> None:
        """Update the URL boost score."""
        now = time.time()
        row = self._conn.execute(
            "SELECT boost_score, fetch_count, skip_count, cite_count"
            " FROM url_boosts WHERE url = ?",
            (url,),
        ).fetchone()

        if row is None:
            score = fetch_delta * 1.0 - skip_delta * 0.3 + cite_delta * 2.0
            self._conn.execute(
                "INSERT INTO url_boosts"
                " (url, boost_score, fetch_count, skip_count,"
                "  cite_count, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (url, score, fetch_delta, skip_delta, cite_delta, now),
            )
        else:
            old_score, fc, sc, cc = row
            new_score = (
                old_score * _BOOST_DECAY
                + fetch_delta * 1.0
                - skip_delta * 0.3
                + cite_delta * 2.0
            )
            self._conn.execute(
                "UPDATE url_boosts SET"
                " boost_score = ?, fetch_count = ?,"
                " skip_count = ?, cite_count = ?, updated_at = ?"
                " WHERE url = ?",
                (
                    new_score,
                    fc + fetch_delta,
                    sc + skip_delta,
                    cc + cite_delta,
                    now,
                    url,
                ),
            )

    def get_boost(self, url: str) -> float:
        """Get the boost score for a URL (0.0 if unknown)."""
        row = self._conn.execute(
            "SELECT boost_score FROM url_boosts WHERE url = ?",
            (url,),
        ).fetchone()
        return float(row[0]) if row else 0.0

    def get_url_stats(self, url: str) -> URLBoost | None:
        """Get full feedback stats for a URL."""
        row = self._conn.execute(
            "SELECT url, boost_score, fetch_count, skip_count, cite_count"
            " FROM url_boosts WHERE url = ?",
            (url,),
        ).fetchone()
        if row is None:
            return None
        return URLBoost(
            url=row[0],
            boost_score=row[1],
            fetch_count=row[2],
            skip_count=row[3],
            cite_count=row[4],
        )

    def is_reformulation(
        self,
        query: str,
        window: float = _REFORMULATION_WINDOW,
    ) -> bool:
        """Check if a similar query was searched recently."""
        qh = self.hash_query(query)
        cutoff = time.time() - window
        row = self._conn.execute(
            "SELECT COUNT(*) FROM feedback_signals"
            " WHERE query_hash = ? AND created_at > ?",
            (qh, cutoff),
        ).fetchone()
        return bool(row and row[0] > 0)

    def top_boosted_urls(self, limit: int = 50) -> list[URLBoost]:
        """Get URLs with highest positive boost scores."""
        rows = self._conn.execute(
            "SELECT url, boost_score, fetch_count, skip_count, cite_count"
            " FROM url_boosts WHERE boost_score > 0"
            " ORDER BY boost_score DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            URLBoost(
                url=r[0],
                boost_score=r[1],
                fetch_count=r[2],
                skip_count=r[3],
                cite_count=r[4],
            )
            for r in rows
        ]

    def signal_count(self) -> int:
        """Total number of recorded signals."""
        row = self._conn.execute("SELECT COUNT(*) FROM feedback_signals").fetchone()
        return int(row[0]) if row else 0

    # ── Maintenance ─────────────────────────────────────────

    def _maybe_prune(self) -> None:
        """Prune old signals if over limit."""
        count = self.signal_count()
        if count > _MAX_SIGNALS:
            cutoff_row = self._conn.execute(
                "SELECT created_at FROM feedback_signals"
                " ORDER BY created_at DESC LIMIT 1 OFFSET ?",
                (_MAX_SIGNALS // 2,),
            ).fetchone()
            if cutoff_row:
                self._conn.execute(
                    "DELETE FROM feedback_signals WHERE created_at < ?",
                    (cutoff_row[0],),
                )
                self._conn.commit()
                logger.info(
                    "feedback_pruned",
                    kept=_MAX_SIGNALS // 2,
                    removed=count - _MAX_SIGNALS // 2,
                )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
