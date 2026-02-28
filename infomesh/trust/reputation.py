"""LLM reputation-based trust — long-term summary quality tracking.

Tracks per-peer summary quality over time and derives a reputation grade
that feeds into the unified trust score.  Peers with consistently high
quality summaries earn preference for LLM task routing.

The reputation is stored locally in SQLite and updated whenever a
verification report arrives (from self-check, cross-validation, or audit).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

from infomesh.db import SQLiteStore

logger = structlog.get_logger()

# ── Constants ─────────────────────────────────────────────────

# Minimum number of samples before a reputation grade is assigned
MIN_SAMPLES = 5

# Exponential moving average alpha — recent samples weigh more
EMA_ALPHA = 0.3

# Window for "recent" quality (seconds) — 7 days
RECENT_WINDOW = 7 * 24 * 3600


class ReputationGrade(StrEnum):
    """Reputation tier based on long-term summary quality."""

    EXCELLENT = "excellent"  # ≥ 0.85
    GOOD = "good"  # ≥ 0.70
    ACCEPTABLE = "acceptable"  # ≥ 0.50
    POOR = "poor"  # ≥ 0.30
    UNRELIABLE = "unreliable"  # < 0.30
    UNKNOWN = "unknown"  # < MIN_SAMPLES ratings


GRADE_THRESHOLDS: list[tuple[float, ReputationGrade]] = [
    (0.85, ReputationGrade.EXCELLENT),
    (0.70, ReputationGrade.GOOD),
    (0.50, ReputationGrade.ACCEPTABLE),
    (0.30, ReputationGrade.POOR),
    (0.0, ReputationGrade.UNRELIABLE),
]


@dataclass(frozen=True)
class PeerReputation:
    """Summary quality reputation for a single peer."""

    peer_id: str
    total_ratings: int
    recent_ratings: int
    avg_quality: float
    ema_quality: float
    recent_avg: float
    grade: ReputationGrade
    last_rated: float


def _grade_from_score(score: float, total: int) -> ReputationGrade:
    """Map a score to a grade, requiring minimum samples."""
    if total < MIN_SAMPLES:
        return ReputationGrade.UNKNOWN
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return ReputationGrade.UNRELIABLE


class LLMReputationTracker(SQLiteStore):
    """SQLite-backed tracker for per-peer LLM summary quality.

    Records individual quality ratings and maintains an EMA
    (exponential moving average) for responsive reputation updates.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS llm_reputation (
            peer_id         TEXT PRIMARY KEY,
            total_ratings   INTEGER NOT NULL DEFAULT 0,
            quality_sum     REAL NOT NULL DEFAULT 0.0,
            ema_quality     REAL NOT NULL DEFAULT 0.5,
            last_rated      REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS llm_quality_log (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_id     TEXT NOT NULL,
            quality     REAL NOT NULL,
            url         TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            timestamp   REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_llm_log_peer
            ON llm_quality_log(peer_id);
        CREATE INDEX IF NOT EXISTS idx_llm_log_ts
            ON llm_quality_log(timestamp);
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        super().__init__(db_path)

    def record_quality(
        self,
        peer_id: str,
        quality: float,
        *,
        url: str = "",
        content_hash: str = "",
    ) -> None:
        """Record a summary quality rating for a peer.

        Args:
            peer_id: Peer that produced the summary.
            quality: Quality score in ``[0.0, 1.0]``.
            url: URL of the summarized content.
            content_hash: SHA-256 of the source text.
        """
        quality = max(0.0, min(1.0, quality))
        now = time.time()

        # Ensure peer row exists
        self._conn.execute(
            "INSERT OR IGNORE INTO llm_reputation (peer_id, last_rated) VALUES (?, ?)",
            (peer_id, now),
        )

        # Update aggregate stats
        row = self._conn.execute(
            "SELECT ema_quality FROM llm_reputation WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        old_ema = row[0] if row else 0.5
        new_ema = EMA_ALPHA * quality + (1 - EMA_ALPHA) * old_ema

        self._conn.execute(
            """UPDATE llm_reputation
               SET total_ratings = total_ratings + 1,
                   quality_sum = quality_sum + ?,
                   ema_quality = ?,
                   last_rated = ?
               WHERE peer_id = ?""",
            (quality, new_ema, now, peer_id),
        )

        # Log the individual rating
        self._conn.execute(
            """INSERT INTO llm_quality_log
               (peer_id, quality, url, content_hash, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (peer_id, quality, url, content_hash, now),
        )
        self._conn.commit()

        logger.debug(
            "llm_quality_recorded",
            peer_id=peer_id[:16],
            quality=round(quality, 3),
            ema=round(new_ema, 3),
        )

    def get_reputation(self, peer_id: str) -> PeerReputation | None:
        """Get the reputation profile for a peer.

        Returns:
            ``PeerReputation`` or ``None`` if the peer has no ratings.
        """
        row = self._conn.execute(
            """SELECT peer_id, total_ratings, quality_sum, ema_quality, last_rated
               FROM llm_reputation WHERE peer_id = ?""",
            (peer_id,),
        ).fetchone()
        if row is None:
            return None

        peer_id_val, total, quality_sum, ema, last_rated = row
        avg = quality_sum / total if total > 0 else 0.0

        # Recent average (last 7 days)
        cutoff = time.time() - RECENT_WINDOW
        recent = self._conn.execute(
            """SELECT COUNT(*), COALESCE(AVG(quality), 0.0)
               FROM llm_quality_log
               WHERE peer_id = ? AND timestamp >= ?""",
            (peer_id, cutoff),
        ).fetchone()
        recent_count = recent[0] if recent else 0
        recent_avg = recent[1] if recent and recent[0] > 0 else 0.0

        # Grade based on EMA (more responsive to recent trends)
        grade = _grade_from_score(ema, total)

        return PeerReputation(
            peer_id=peer_id_val,
            total_ratings=total,
            recent_ratings=recent_count,
            avg_quality=round(avg, 4),
            ema_quality=round(ema, 4),
            recent_avg=round(recent_avg, 4),
            grade=grade,
            last_rated=last_rated,
        )

    def get_quality_score(self, peer_id: str) -> float:
        """Return the EMA quality score for a peer, or 0.5 default."""
        rep = self.get_reputation(peer_id)
        return rep.ema_quality if rep else 0.5

    def list_peers(
        self, *, min_ratings: int = 0, grade: ReputationGrade | None = None
    ) -> list[PeerReputation]:
        """List all tracked peers with reputation info.

        Args:
            min_ratings: Minimum total ratings to include.
            grade: Filter by specific grade (optional).

        Returns:
            List of ``PeerReputation`` sorted by EMA quality descending.
        """
        rows = self._conn.execute(
            """SELECT peer_id, total_ratings, quality_sum, ema_quality, last_rated
               FROM llm_reputation
               WHERE total_ratings >= ?
               ORDER BY ema_quality DESC""",
            (min_ratings,),
        ).fetchall()

        results: list[PeerReputation] = []
        cutoff = time.time() - RECENT_WINDOW

        for row in rows:
            pid, total, qsum, ema, lr = row
            avg = qsum / total if total > 0 else 0.0

            recent = self._conn.execute(
                """SELECT COUNT(*), COALESCE(AVG(quality), 0.0)
                   FROM llm_quality_log
                   WHERE peer_id = ? AND timestamp >= ?""",
                (pid, cutoff),
            ).fetchone()
            rc = recent[0] if recent else 0
            ra = recent[1] if recent and recent[0] > 0 else 0.0

            g = _grade_from_score(ema, total)
            if grade is not None and g != grade:
                continue

            results.append(
                PeerReputation(
                    peer_id=pid,
                    total_ratings=total,
                    recent_ratings=rc,
                    avg_quality=round(avg, 4),
                    ema_quality=round(ema, 4),
                    recent_avg=round(ra, 4),
                    grade=g,
                    last_rated=lr,
                )
            )

        return results

    def top_peers(self, n: int = 10) -> list[PeerReputation]:
        """Return the top N peers by EMA quality with at least MIN_SAMPLES ratings."""
        return self.list_peers(min_ratings=MIN_SAMPLES)[:n]

    # close() inherited from SQLiteStore
