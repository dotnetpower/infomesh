"""Unified trust score computation for InfoMesh peers.

Trust = 0.15 * uptime + 0.25 * contribution
        + 0.40 * audit_pass_rate + 0.20 * summary_quality

Tiers:
    Trusted    ≥ 0.8
    Normal     0.5 – 0.8
    Suspect    0.3 – 0.5
    Untrusted  < 0.3
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from infomesh.db import SQLiteStore

if TYPE_CHECKING:
    from infomesh.trust.reputation import LLMReputationTracker

logger = structlog.get_logger()

# --- Trust weight constants ------------------------------------------------

W_UPTIME = 0.15
W_CONTRIBUTION = 0.25
W_AUDIT = 0.40
W_SUMMARY = 0.20


class TrustTier(StrEnum):
    """Trust tier classification."""

    TRUSTED = "trusted"  # ≥ 0.8
    NORMAL = "normal"  # 0.5 – 0.8
    SUSPECT = "suspect"  # 0.3 – 0.5
    UNTRUSTED = "untrusted"  # < 0.3


TIER_THRESHOLDS: list[tuple[float, TrustTier]] = [
    (0.8, TrustTier.TRUSTED),
    (0.5, TrustTier.NORMAL),
    (0.3, TrustTier.SUSPECT),
    (0.0, TrustTier.UNTRUSTED),
]

# How many consecutive audit failures trigger network isolation
AUDIT_FAILURE_ISOLATION_THRESHOLD = 3

# Maximum uptime hours for normalization (30 days online = 1.0 uptime)
MAX_UPTIME_HOURS: float = 30 * 24

# Maximum contribution score for normalization
MAX_CONTRIBUTION_SCORE: float = 5000.0


# --- Data classes ----------------------------------------------------------


@dataclass(frozen=True)
class PeerTrust:
    """Trust profile for a single peer."""

    peer_id: str
    uptime_score: float
    contribution_score: float
    audit_pass_rate: float
    summary_quality: float
    trust_score: float
    tier: TrustTier
    consecutive_audit_failures: int
    isolated: bool
    last_updated: float


@dataclass(frozen=True)
class TrustUpdate:
    """An event that modifies a peer's trust profile."""

    peer_id: str
    field: str  # "uptime", "contribution", "audit", "summary"
    value: float  # new raw value
    timestamp: float


# --- Trust store -----------------------------------------------------------


class TrustStore(SQLiteStore):
    """SQLite-backed trust score storage for known peers.

    Each peer has a row tracking the four trust signals.
    Optionally integrates with an ``LLMReputationTracker`` to forward
    summary quality ratings for EMA-based reputation grading.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS peer_trust (
            peer_id                     TEXT PRIMARY KEY,
            uptime_hours                REAL NOT NULL DEFAULT 0,
            contribution_raw            REAL NOT NULL DEFAULT 0,
            audit_total                 INTEGER NOT NULL DEFAULT 0,
            audit_passed                INTEGER NOT NULL DEFAULT 0,
            summary_ratings_sum         REAL NOT NULL DEFAULT 0,
            summary_ratings_count       INTEGER NOT NULL DEFAULT 0,
            consecutive_audit_failures  INTEGER NOT NULL DEFAULT 0,
            isolated                    INTEGER NOT NULL DEFAULT 0,
            last_updated                REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS trust_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_id     TEXT NOT NULL,
            field       TEXT NOT NULL,
            value       REAL NOT NULL,
            timestamp   REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trust_events_peer
            ON trust_events(peer_id);
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        reputation_tracker: LLMReputationTracker | None = None,
    ) -> None:
        self._reputation_tracker = reputation_tracker
        super().__init__(db_path)

    # --- Mutations ---------------------------------------------------------

    def _ensure_peer(self, peer_id: str) -> None:
        """Insert a default row if the peer is not yet tracked."""
        self._conn.execute(
            "INSERT OR IGNORE INTO peer_trust (peer_id, last_updated) VALUES (?, ?)",
            (peer_id, time.time()),
        )

    def update_uptime(self, peer_id: str, hours: float) -> None:
        """Record cumulative uptime hours for a peer."""
        self._ensure_peer(peer_id)
        now = time.time()
        self._conn.execute(
            "UPDATE peer_trust"
            " SET uptime_hours = ?,"
            " last_updated = ?"
            " WHERE peer_id = ?",
            (hours, now, peer_id),
        )
        self._conn.execute(
            "INSERT INTO trust_events"
            " (peer_id, field, value, timestamp)"
            " VALUES (?, 'uptime', ?, ?)",
            (peer_id, hours, now),
        )
        self._conn.commit()

    def update_contribution(self, peer_id: str, score: float) -> None:
        """Update the contribution score for a peer."""
        self._ensure_peer(peer_id)
        now = time.time()
        self._conn.execute(
            "UPDATE peer_trust"
            " SET contribution_raw = ?,"
            " last_updated = ?"
            " WHERE peer_id = ?",
            (score, now, peer_id),
        )
        self._conn.execute(
            "INSERT INTO trust_events"
            " (peer_id, field, value, timestamp)"
            " VALUES (?, 'contribution', ?, ?)",
            (peer_id, score, now),
        )
        self._conn.commit()

    def record_audit(self, peer_id: str, *, passed: bool) -> None:
        """Record an audit result for a peer.

        If the peer accumulates ``AUDIT_FAILURE_ISOLATION_THRESHOLD``
        consecutive failures, it is marked as isolated.
        """
        self._ensure_peer(peer_id)
        now = time.time()

        if passed:
            self._conn.execute(
                """UPDATE peer_trust
                   SET audit_total = audit_total + 1,
                       audit_passed = audit_passed + 1,
                       consecutive_audit_failures = 0,
                       last_updated = ?
                   WHERE peer_id = ?""",
                (now, peer_id),
            )
        else:
            self._conn.execute(
                """UPDATE peer_trust
                   SET audit_total = audit_total + 1,
                       consecutive_audit_failures = consecutive_audit_failures + 1,
                       last_updated = ?
                   WHERE peer_id = ?""",
                (now, peer_id),
            )
            # Check isolation
            row = self._conn.execute(
                "SELECT consecutive_audit_failures FROM peer_trust WHERE peer_id = ?",
                (peer_id,),
            ).fetchone()
            if row and row[0] >= AUDIT_FAILURE_ISOLATION_THRESHOLD:
                self._conn.execute(
                    "UPDATE peer_trust SET isolated = 1 WHERE peer_id = ?",
                    (peer_id,),
                )
                logger.warning("peer_isolated", peer_id=peer_id, failures=row[0])

        self._conn.execute(
            "INSERT INTO trust_events"
            " (peer_id, field, value, timestamp)"
            " VALUES (?, 'audit', ?, ?)",
            (peer_id, 1.0 if passed else 0.0, now),
        )
        self._conn.commit()

    def record_summary_rating(self, peer_id: str, quality: float) -> None:
        """Record a summary quality rating (0–1) for a peer.

        Also forwards the rating to the ``LLMReputationTracker`` (if set)
        for EMA-based reputation grading.

        Args:
            peer_id: Peer that produced the summary.
            quality: Quality score between 0.0 and 1.0.
        """
        quality = max(0.0, min(1.0, quality))
        self._ensure_peer(peer_id)
        now = time.time()
        self._conn.execute(
            """UPDATE peer_trust
               SET summary_ratings_sum = summary_ratings_sum + ?,
                   summary_ratings_count = summary_ratings_count + 1,
                   last_updated = ?
               WHERE peer_id = ?""",
            (quality, now, peer_id),
        )
        self._conn.execute(
            "INSERT INTO trust_events"
            " (peer_id, field, value, timestamp)"
            " VALUES (?, 'summary', ?, ?)",
            (peer_id, quality, now),
        )
        self._conn.commit()

        # Forward to reputation tracker for EMA grading
        if self._reputation_tracker is not None:
            try:
                from infomesh.trust.reputation import LLMReputationTracker

                if isinstance(self._reputation_tracker, LLMReputationTracker):
                    self._reputation_tracker.record_quality(peer_id, quality)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "reputation_forward_failed", peer_id=peer_id, error=str(exc)
                )

    def unisolate(self, peer_id: str) -> None:
        """Manually remove isolation flag (e.g. after successful re-audit)."""
        self._conn.execute(
            "UPDATE peer_trust"
            " SET isolated = 0,"
            " consecutive_audit_failures = 0"
            " WHERE peer_id = ?",
            (peer_id,),
        )
        self._conn.commit()
        logger.info("peer_unisolated", peer_id=peer_id)

    # --- Queries -----------------------------------------------------------

    def get_trust(self, peer_id: str) -> PeerTrust | None:
        """Compute the trust profile for a peer."""
        row = self._conn.execute(
            """SELECT peer_id, uptime_hours, contribution_raw,
                      audit_total, audit_passed,
                      summary_ratings_sum, summary_ratings_count,
                      consecutive_audit_failures, isolated, last_updated
               FROM peer_trust WHERE peer_id = ?""",
            (peer_id,),
        ).fetchone()

        if row is None:
            return None

        return _compute_trust(row)

    def get_trust_score(self, peer_id: str) -> float:
        """Return the numerical trust score for a peer, or default 0.5."""
        pt = self.get_trust(peer_id)
        return pt.trust_score if pt else 0.5

    def list_peers(self, *, include_isolated: bool = False) -> list[PeerTrust]:
        """List all tracked peers with computed trust."""
        sql = "SELECT * FROM peer_trust"
        if not include_isolated:
            sql += " WHERE isolated = 0"
        rows = self._conn.execute(sql).fetchall()
        return [_compute_trust(r) for r in rows]

    def list_isolated(self) -> list[PeerTrust]:
        """List all isolated peers."""
        rows = self._conn.execute(
            "SELECT * FROM peer_trust WHERE isolated = 1"
        ).fetchall()
        return [_compute_trust(r) for r in rows]

    def isolate_peer(self, peer_id: str) -> None:
        """Mark a peer as isolated (network ban).

        Creates the peer record if it does not already exist.
        """
        self._ensure_peer(peer_id)
        self._conn.execute(
            "UPDATE peer_trust SET isolated = 1 WHERE peer_id = ?",
            (peer_id,),
        )
        self._conn.commit()

    # close() inherited from SQLiteStore


# --- Computation helpers ---------------------------------------------------


def compute_trust_score(
    uptime_hours: float,
    contribution_raw: float,
    audit_total: int,
    audit_passed: int,
    summary_avg: float,
) -> float:
    """Pure function to compute a unified trust score.

    Args:
        uptime_hours: Cumulative hours online.
        contribution_raw: Raw credit contribution score.
        audit_total: Total audits received.
        audit_passed: Audits that passed.
        summary_avg: Average summary quality ``[0, 1]``.

    Returns:
        Trust score in ``[0, 1]``.
    """
    uptime_norm = min(1.0, uptime_hours / MAX_UPTIME_HOURS)
    contrib_norm = min(1.0, contribution_raw / MAX_CONTRIBUTION_SCORE)
    audit_rate = (audit_passed / audit_total) if audit_total > 0 else 0.5
    summary_norm = summary_avg if summary_avg > 0 else 0.5

    return (
        W_UPTIME * uptime_norm
        + W_CONTRIBUTION * contrib_norm
        + W_AUDIT * audit_rate
        + W_SUMMARY * summary_norm
    )


def trust_tier(score: float) -> TrustTier:
    """Map a numeric trust score to a tier."""
    for threshold, tier in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return TrustTier.UNTRUSTED


def _compute_trust(row: tuple) -> PeerTrust:
    """Build a PeerTrust from a DB row."""
    (
        peer_id,
        uptime_hours,
        contribution_raw,
        audit_total,
        audit_passed,
        summary_sum,
        summary_count,
        consec_fail,
        isolated,
        last_updated,
    ) = row

    summary_avg = (summary_sum / summary_count) if summary_count > 0 else 0.0
    score = compute_trust_score(
        uptime_hours, contribution_raw, audit_total, audit_passed, summary_avg
    )
    tier = trust_tier(score)

    return PeerTrust(
        peer_id=peer_id,
        uptime_score=round(min(1.0, uptime_hours / MAX_UPTIME_HOURS), 6),
        contribution_score=round(
            min(1.0, contribution_raw / MAX_CONTRIBUTION_SCORE), 6
        ),
        audit_pass_rate=round(
            (audit_passed / audit_total) if audit_total > 0 else 0.5, 6
        ),
        summary_quality=round(summary_avg, 6),
        trust_score=round(score, 6),
        tier=tier,
        consecutive_audit_failures=consec_fail,
        isolated=bool(isolated),
        last_updated=last_updated,
    )
