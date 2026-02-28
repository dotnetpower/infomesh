"""Credit farming detection for InfoMesh.

Detects and prevents credit farming through:
1. **New node probation**: 24-hour probation period with higher audit frequency
   and reduced credit rates.
2. **Statistical anomaly detection**: Flags nodes with abnormal earning patterns
   (e.g. unusually high crawl rate, identical action intervals, burst patterns).
3. **Rate-limit enforcement**: Hard caps on credits per action per time window.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog

from infomesh.db import SQLiteStore

logger = structlog.get_logger()


# --- Constants -------------------------------------------------------------

# Probation period for new nodes (hours)
PROBATION_HOURS: float = 24.0

# Credit multiplier during probation (earn less)
PROBATION_CREDIT_MULTIPLIER: float = 0.5

# Maximum actions per hour per type (hard cap)
MAX_CRAWLS_PER_HOUR: int = 120  # 2/min max
MAX_QUERIES_PER_HOUR: int = 300
MAX_LLM_PER_HOUR: int = 60

# Anomaly detection thresholds
# Coefficient of variation below this → suspiciously regular intervals
MIN_INTERVAL_CV: float = 0.15
# Minutes of history to analyze for burst detection
BURST_WINDOW_MINUTES: float = 5.0
# Max actions in a burst window before flagging
BURST_THRESHOLD: int = 30

# How many anomalies before a node gets flagged
ANOMALY_FLAG_THRESHOLD: int = 3


class FarmingVerdict(StrEnum):
    """Result of a farming check."""

    CLEAN = "clean"
    PROBATION = "probation"  # New node, limited credits
    RATE_LIMITED = "rate_limited"  # Exceeding rate limits
    SUSPICIOUS = "suspicious"  # Anomaly detected
    BLOCKED = "blocked"  # Confirmed farming, credits frozen


@dataclass(frozen=True)
class FarmingCheck:
    """Result of checking a node for farming behavior."""

    peer_id: str
    verdict: FarmingVerdict
    probation_remaining_hours: float
    rate_limit_exceeded: bool
    anomaly_count: int
    detail: str


@dataclass(frozen=True)
class AnomalyEvent:
    """A detected anomaly in credit farming behavior."""

    event_id: int
    peer_id: str
    anomaly_type: str
    detail: str
    timestamp: float


# --- Farming detector -------------------------------------------------------


class FarmingDetector(SQLiteStore):
    """Detects and tracks credit farming behavior.

    Uses an SQLite database to track node registration times,
    action patterns, and anomaly history.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS node_registry (
            peer_id         TEXT PRIMARY KEY,
            registered_at   REAL NOT NULL,
            blocked         INTEGER NOT NULL DEFAULT 0,
            anomaly_count   INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS action_log (
            log_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_id     TEXT NOT NULL,
            action      TEXT NOT NULL,
            timestamp   REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS anomaly_events (
            event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
            peer_id     TEXT NOT NULL,
            anomaly_type TEXT NOT NULL,
            detail      TEXT NOT NULL DEFAULT '',
            timestamp   REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_action_log_peer
            ON action_log(peer_id, action, timestamp);
        CREATE INDEX IF NOT EXISTS idx_anomaly_peer
            ON anomaly_events(peer_id);
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        super().__init__(db_path)

    # --- Node registration -------------------------------------------------

    def register_node(self, peer_id: str, *, now: float | None = None) -> None:
        """Register a new node with the current timestamp.

        If already registered, this is a no-op.
        """
        now = now or time.time()
        self._conn.execute(
            "INSERT OR IGNORE INTO node_registry"
            " (peer_id, registered_at) VALUES (?, ?)",
            (peer_id, now),
        )
        self._conn.commit()

    def is_on_probation(self, peer_id: str, *, now: float | None = None) -> bool:
        """Check if a node is still in its probation period."""
        now = now or time.time()
        row = self._conn.execute(
            "SELECT registered_at FROM node_registry WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        if row is None:
            return True  # Unregistered = on probation
        elapsed_hours = (now - row[0]) / 3600.0
        return bool(elapsed_hours < PROBATION_HOURS)

    def probation_remaining(self, peer_id: str, *, now: float | None = None) -> float:
        """Return remaining probation hours (0 if past probation)."""
        now = now or time.time()
        row = self._conn.execute(
            "SELECT registered_at FROM node_registry WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        if row is None:
            return PROBATION_HOURS
        elapsed = (now - row[0]) / 3600.0
        return float(max(0.0, PROBATION_HOURS - elapsed))

    # --- Action logging ----------------------------------------------------

    def log_action(
        self, peer_id: str, action: str, *, now: float | None = None
    ) -> None:
        """Log an action for rate-limit and anomaly tracking."""
        now = now or time.time()
        self._conn.execute(
            "INSERT INTO action_log (peer_id, action, timestamp) VALUES (?, ?, ?)",
            (peer_id, action, now),
        )
        self._conn.commit()

    # --- Rate limiting -----------------------------------------------------

    def actions_in_last_hour(
        self, peer_id: str, action: str, *, now: float | None = None
    ) -> int:
        """Count actions of a given type in the last hour."""
        now = now or time.time()
        cutoff = now - 3600.0
        row = self._conn.execute(
            "SELECT COUNT(*) FROM action_log"
            " WHERE peer_id = ? AND action = ?"
            " AND timestamp >= ?",
            (peer_id, action, cutoff),
        ).fetchone()
        return int(row[0])

    def is_rate_limited(
        self, peer_id: str, action: str, *, now: float | None = None
    ) -> bool:
        """Check if the node has exceeded its rate limit for this action."""
        count = self.actions_in_last_hour(peer_id, action, now=now)
        limits = {
            "crawl": MAX_CRAWLS_PER_HOUR,
            "query_process": MAX_QUERIES_PER_HOUR,
            "llm_own": MAX_LLM_PER_HOUR,
            "llm_peer": MAX_LLM_PER_HOUR,
        }
        max_allowed = limits.get(action, MAX_CRAWLS_PER_HOUR)
        return count >= max_allowed

    # --- Anomaly detection -------------------------------------------------

    def detect_regular_intervals(
        self,
        peer_id: str,
        action: str,
        *,
        window_hours: float = 1.0,
        now: float | None = None,
    ) -> bool:
        """Detect suspiciously regular action intervals.

        Bot-like behavior often has near-constant inter-action intervals.
        We measure the coefficient of variation (CV = std/mean); if CV is
        below MIN_INTERVAL_CV, the pattern is flagged.

        Returns:
            True if the pattern is suspiciously regular.
        """
        now = now or time.time()
        cutoff = now - window_hours * 3600.0
        rows = self._conn.execute(
            "SELECT timestamp FROM action_log"
            " WHERE peer_id = ? AND action = ?"
            " AND timestamp >= ?"
            " ORDER BY timestamp",
            (peer_id, action, cutoff),
        ).fetchall()

        if len(rows) < 10:  # Need enough data points
            return False

        timestamps = [r[0] for r in rows]
        intervals = [
            timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)
        ]

        mean_interval = sum(intervals) / len(intervals)
        if mean_interval <= 0:
            return True  # All at the same time = definitely suspicious

        variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
        std = variance**0.5
        cv = std / mean_interval

        if cv < MIN_INTERVAL_CV:
            logger.warning(
                "farming_regular_intervals",
                peer_id=peer_id[:12],
                action=action,
                cv=round(cv, 4),
            )
            return True
        return False

    def detect_burst(
        self,
        peer_id: str,
        action: str,
        *,
        now: float | None = None,
    ) -> bool:
        """Detect burst activity (many actions in a short window).

        Returns:
            True if a burst is detected.
        """
        now = now or time.time()
        cutoff = now - BURST_WINDOW_MINUTES * 60.0
        row = self._conn.execute(
            "SELECT COUNT(*) FROM action_log"
            " WHERE peer_id = ? AND action = ?"
            " AND timestamp >= ?",
            (peer_id, action, cutoff),
        ).fetchone()
        count = int(row[0])
        if count >= BURST_THRESHOLD:
            logger.warning(
                "farming_burst_detected",
                peer_id=peer_id[:12],
                action=action,
                count=count,
                window_minutes=BURST_WINDOW_MINUTES,
            )
            return True
        return False

    def record_anomaly(
        self,
        peer_id: str,
        anomaly_type: str,
        detail: str = "",
        *,
        now: float | None = None,
    ) -> int:
        """Record an anomaly event and increment the node's anomaly count.

        Returns:
            Updated anomaly count for the node.
        """
        now = now or time.time()
        self._conn.execute(
            "INSERT INTO anomaly_events"
            " (peer_id, anomaly_type, detail, timestamp)"
            " VALUES (?, ?, ?, ?)",
            (peer_id, anomaly_type, detail, now),
        )
        self._conn.execute(
            "UPDATE node_registry"
            " SET anomaly_count = anomaly_count + 1"
            " WHERE peer_id = ?",
            (peer_id,),
        )
        self._conn.commit()

        row = self._conn.execute(
            "SELECT anomaly_count FROM node_registry WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        count = int(row[0]) if row else 1

        # Auto-block if threshold exceeded
        if count >= ANOMALY_FLAG_THRESHOLD:
            self._conn.execute(
                "UPDATE node_registry SET blocked = 1 WHERE peer_id = ?",
                (peer_id,),
            )
            self._conn.commit()
            logger.warning(
                "farming_node_blocked", peer_id=peer_id[:12], anomaly_count=count
            )

        return count

    def is_blocked(self, peer_id: str) -> bool:
        """Check if a node is blocked for credit farming."""
        row = self._conn.execute(
            "SELECT blocked FROM node_registry WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        return bool(row[0]) if row else False

    def unblock(self, peer_id: str) -> None:
        """Manually unblock a node."""
        self._conn.execute(
            "UPDATE node_registry SET blocked = 0, anomaly_count = 0 WHERE peer_id = ?",
            (peer_id,),
        )
        self._conn.commit()
        logger.info("farming_node_unblocked", peer_id=peer_id[:12])

    # --- Comprehensive check -----------------------------------------------

    def check(
        self, peer_id: str, action: str, *, now: float | None = None
    ) -> FarmingCheck:
        """Run all farming checks for a node attempting an action.

        Args:
            peer_id: The node's peer ID.
            action: Action type string (e.g. "crawl", "llm_own").
            now: Override timestamp.

        Returns:
            FarmingCheck with verdict and details.
        """
        now = now or time.time()
        self.register_node(peer_id, now=now)

        # Check blocked
        if self.is_blocked(peer_id):
            anomaly_count = self._get_anomaly_count(peer_id)
            return FarmingCheck(
                peer_id=peer_id,
                verdict=FarmingVerdict.BLOCKED,
                probation_remaining_hours=0.0,
                rate_limit_exceeded=False,
                anomaly_count=anomaly_count,
                detail="node blocked for credit farming",
            )

        # Check rate limit
        rate_limited = self.is_rate_limited(peer_id, action, now=now)

        # Check probation
        on_probation = self.is_on_probation(peer_id, now=now)
        remaining = self.probation_remaining(peer_id, now=now)

        # Run anomaly checks
        anomalies: list[str] = []
        if self.detect_regular_intervals(peer_id, action, now=now):
            anomalies.append("regular_intervals")
        if self.detect_burst(peer_id, action, now=now):
            anomalies.append("burst")

        # Record any detected anomalies
        for atype in anomalies:
            self.record_anomaly(peer_id, atype, detail=f"action={action}", now=now)

        anomaly_count = self._get_anomaly_count(peer_id)

        # Determine verdict
        if self.is_blocked(peer_id):  # Re-check after recording anomalies
            verdict = FarmingVerdict.BLOCKED
            detail = "blocked after anomaly detection"
        elif anomalies:
            verdict = FarmingVerdict.SUSPICIOUS
            detail = f"anomalies: {', '.join(anomalies)}"
        elif rate_limited:
            verdict = FarmingVerdict.RATE_LIMITED
            detail = f"rate limit exceeded for {action}"
        elif on_probation:
            verdict = FarmingVerdict.PROBATION
            detail = f"probation: {remaining:.1f}h remaining"
        else:
            verdict = FarmingVerdict.CLEAN
            detail = "ok"

        return FarmingCheck(
            peer_id=peer_id,
            verdict=verdict,
            probation_remaining_hours=round(remaining, 2),
            rate_limit_exceeded=rate_limited,
            anomaly_count=anomaly_count,
            detail=detail,
        )

    def get_anomaly_history(
        self, peer_id: str, *, limit: int = 50
    ) -> list[AnomalyEvent]:
        """Get recent anomaly events for a node."""
        rows = self._conn.execute(
            "SELECT event_id, peer_id, anomaly_type, detail, timestamp "
            "FROM anomaly_events WHERE peer_id = ? ORDER BY timestamp DESC LIMIT ?",
            (peer_id, limit),
        ).fetchall()
        return [
            AnomalyEvent(
                event_id=r[0],
                peer_id=r[1],
                anomaly_type=r[2],
                detail=r[3],
                timestamp=r[4],
            )
            for r in rows
        ]

    def _get_anomaly_count(self, peer_id: str) -> int:
        row = self._conn.execute(
            "SELECT anomaly_count FROM node_registry WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        return int(row[0]) if row else 0

    # close() inherited from SQLiteStore
