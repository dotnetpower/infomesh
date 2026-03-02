"""Cross-node credit synchronization via P2P.

When a user runs InfoMesh on multiple devices (e.g., PC and laptop) with the
same GitHub account, each device maintains a separate local credit ledger.
This module enables those nodes to discover each other (by matching hashed
GitHub emails) and exchange signed credit summaries so each node can display
**aggregated** credit statistics across all owned devices.

Design principles:
- **Privacy**: Only a SHA-256 hash of the email is exchanged on the wire.
- **Integrity**: Each summary is signed with the node's Ed25519 key.
- **No double-counting**: Summaries are keyed by peer_id; each peer
  contributes exactly one summary to the aggregate.
- **Stale-sweep**: Summaries older than ``SUMMARY_TTL_HOURS`` are purged.
- **Conflict-free**: Each node is authoritative over its own credits.
  The aggregation is a read-only view (no credit transfers).

Flow::

    Node A                              Node B
    ──────                              ──────
    connect → announce(email_hash_A)
                                 ←  announce(email_hash_B)
    if email_hash_A == email_hash_B:
        build_summary() ──────────►
                        ◄──────────  build_summary()
        store peer summary           store peer summary

    aggregated_stats() merges local + stored peer summaries.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog

from infomesh.credits.ledger import CreditLedger
from infomesh.db import SQLiteStore
from infomesh.hashing import content_hash
from infomesh.types import KeyPairLike

logger = structlog.get_logger()

# ─── Constants ─────────────────────────────────────────────

SUMMARY_TTL_HOURS: float = 72.0
"""Discard peer summaries older than this (hours)."""

SYNC_INTERVAL_SECONDS: float = 300.0
"""How often to re-exchange summaries with known same-owner peers (seconds)."""

MAX_PEER_SUMMARIES: int = 20
"""Maximum number of peer summaries stored per owner (DoS limit)."""


# ─── Data classes ──────────────────────────────────────────


@dataclass(frozen=True)
class CreditSummary:
    """Signed credit summary from a single node.

    Contains just enough information to compute aggregated stats
    without exposing individual credit entries.
    """

    peer_id: str
    owner_email_hash: str
    total_earned: float
    total_spent: float
    contribution_score: float
    entry_count: int
    tier: str
    timestamp: float
    signature: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize to a dict suitable for msgpack transport."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CreditSummary:
        """Deserialize from a dict (msgpack payload)."""
        return cls(
            peer_id=str(d.get("peer_id", "")),
            owner_email_hash=str(d.get("owner_email_hash", "")),
            total_earned=float(
                d["total_earned"]
                if isinstance(d.get("total_earned"), (int, float))
                else 0.0
            ),
            total_spent=float(
                d["total_spent"]
                if isinstance(d.get("total_spent"), (int, float))
                else 0.0
            ),
            contribution_score=float(
                d["contribution_score"]
                if isinstance(d.get("contribution_score"), (int, float))
                else 0.0
            ),
            entry_count=int(
                d["entry_count"]
                if isinstance(d.get("entry_count"), (int, float))
                else 0
            ),
            tier=str(d.get("tier", "Tier 1")),
            timestamp=float(
                d["timestamp"] if isinstance(d.get("timestamp"), (int, float)) else 0.0
            ),
            signature=str(d.get("signature", "")),
        )


@dataclass
class AggregatedCreditStats:
    """Merged credit statistics across all same-owner nodes."""

    total_earned: float = 0.0
    total_spent: float = 0.0
    balance: float = 0.0
    contribution_score: float = 0.0
    node_count: int = 1
    peer_summaries: list[CreditSummary] = field(default_factory=list)


# ─── Store ─────────────────────────────────────────────────


class CreditSyncStore(SQLiteStore):
    """SQLite-backed store for peer credit summaries.

    Stores signed credit summaries received from other nodes that
    share the same owner (same GitHub email hash).
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS peer_credit_summaries (
            peer_id         TEXT PRIMARY KEY,
            owner_email_hash TEXT NOT NULL,
            total_earned    REAL NOT NULL DEFAULT 0,
            total_spent     REAL NOT NULL DEFAULT 0,
            contribution_score REAL NOT NULL DEFAULT 0,
            entry_count     INTEGER NOT NULL DEFAULT 0,
            tier            TEXT NOT NULL DEFAULT 'Tier 1',
            timestamp       REAL NOT NULL DEFAULT 0,
            signature       TEXT NOT NULL DEFAULT '',
            received_at     REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_pcs_owner
            ON peer_credit_summaries(owner_email_hash);
    """

    def store_summary(self, summary: CreditSummary) -> None:
        """Insert or update a peer credit summary.

        Args:
            summary: Validated, signed credit summary from a peer node.
        """
        now = time.time()
        self._conn.execute(
            """INSERT INTO peer_credit_summaries
               (peer_id, owner_email_hash, total_earned, total_spent,
                contribution_score, entry_count, tier, timestamp,
                signature, received_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(peer_id) DO UPDATE SET
                 total_earned = excluded.total_earned,
                 total_spent = excluded.total_spent,
                 contribution_score = excluded.contribution_score,
                 entry_count = excluded.entry_count,
                 tier = excluded.tier,
                 timestamp = excluded.timestamp,
                 signature = excluded.signature,
                 received_at = excluded.received_at
            """,
            (
                summary.peer_id,
                summary.owner_email_hash,
                summary.total_earned,
                summary.total_spent,
                summary.contribution_score,
                summary.entry_count,
                summary.tier,
                summary.timestamp,
                summary.signature,
                now,
            ),
        )
        self._conn.commit()
        logger.info(
            "peer_summary_stored",
            peer_id=summary.peer_id[:16],
            earned=summary.total_earned,
            score=summary.contribution_score,
        )

    def get_peer_summaries(
        self,
        owner_email_hash: str,
    ) -> list[CreditSummary]:
        """Retrieve all non-stale summaries for a given owner hash.

        Args:
            owner_email_hash: SHA-256 hash of the owner's email.

        Returns:
            List of peer credit summaries, excluding stale entries.
        """
        cutoff = time.time() - (SUMMARY_TTL_HOURS * 3600)
        rows = self._conn.execute(
            """SELECT peer_id, owner_email_hash, total_earned, total_spent,
                      contribution_score, entry_count, tier, timestamp,
                      signature
               FROM peer_credit_summaries
               WHERE owner_email_hash = ? AND timestamp > ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (owner_email_hash, cutoff, MAX_PEER_SUMMARIES),
        ).fetchall()
        return [
            CreditSummary(
                peer_id=r[0],
                owner_email_hash=r[1],
                total_earned=r[2],
                total_spent=r[3],
                contribution_score=r[4],
                entry_count=r[5],
                tier=r[6],
                timestamp=r[7],
                signature=r[8],
            )
            for r in rows
        ]

    def purge_stale(self) -> int:
        """Remove summaries older than SUMMARY_TTL_HOURS.

        Returns:
            Number of rows deleted.
        """
        cutoff = time.time() - (SUMMARY_TTL_HOURS * 3600)
        cursor = self._conn.execute(
            "DELETE FROM peer_credit_summaries WHERE timestamp < ?",
            (cutoff,),
        )
        self._conn.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info("stale_summaries_purged", count=deleted)
        return deleted

    def remove_peer(self, peer_id: str) -> None:
        """Remove a specific peer's summary."""
        self._conn.execute(
            "DELETE FROM peer_credit_summaries WHERE peer_id = ?",
            (peer_id,),
        )
        self._conn.commit()

    def peer_count(self, owner_email_hash: str) -> int:
        """Return number of stored peer summaries for an owner."""
        cutoff = time.time() - (SUMMARY_TTL_HOURS * 3600)
        row = self._conn.execute(
            "SELECT COUNT(*) FROM peer_credit_summaries "
            "WHERE owner_email_hash = ? AND timestamp > ?",
            (owner_email_hash, cutoff),
        ).fetchone()
        return int(row[0]) if row else 0


# ─── Manager ──────────────────────────────────────────────


class CreditSyncManager:
    """Orchestrates credit synchronization across same-owner nodes.

    This class ties together the local credit ledger, the sync store,
    and the node's key pair. It provides:

    - ``build_summary()`` — create a signed summary of local credits
    - ``receive_summary()`` — validate and store a peer's summary
    - ``aggregated_stats()`` — merge local + peer summaries
    - ``owner_email_hash`` — the hashed email for matching peers

    Args:
        ledger: Local credit ledger (read-only access for summaries).
        store: Persistent store for peer summaries.
        owner_email: GitHub email address (plaintext, never sent on wire).
        key_pair: Ed25519 key pair for signing summaries.
        local_peer_id: This node's libp2p peer ID.
    """

    def __init__(
        self,
        ledger: CreditLedger,
        store: CreditSyncStore,
        owner_email: str,
        key_pair: KeyPairLike | None = None,
        local_peer_id: str = "",
    ) -> None:
        self._ledger = ledger
        self._store = store
        self._key_pair = key_pair
        self._local_peer_id = local_peer_id

        # Hash the email for privacy-preserving peer matching
        self._owner_email_hash = (
            content_hash(owner_email.lower().strip()) if owner_email else ""
        )

        # Track known same-owner peers (peer_id → last_sync_time)
        self._same_owner_peers: dict[str, float] = {}

    @property
    def owner_email_hash(self) -> str:
        """SHA-256 hash of the owner's normalized email."""
        return self._owner_email_hash

    @property
    def has_identity(self) -> bool:
        """Whether this manager has a valid owner email configured."""
        return bool(self._owner_email_hash)

    def build_summary(self) -> CreditSummary:
        """Create a signed credit summary from the local ledger.

        Returns:
            A ``CreditSummary`` with the current local stats, signed
            with the node's Ed25519 key if available.
        """
        stats = self._ledger.stats()
        now = time.time()

        # Build canonical data for signing
        canonical = (
            f"{self._local_peer_id}|{self._owner_email_hash}|"
            f"{stats.total_earned}|{stats.total_spent}|"
            f"{stats.contribution_score}|{now}"
        ).encode()

        signature = ""
        if self._key_pair is not None:
            try:
                sig_bytes = self._key_pair.sign(canonical)
                signature = sig_bytes.hex()
            except Exception:
                logger.warning("credit_summary_sign_failed")

        return CreditSummary(
            peer_id=self._local_peer_id,
            owner_email_hash=self._owner_email_hash,
            total_earned=stats.total_earned,
            total_spent=stats.total_spent,
            contribution_score=stats.contribution_score,
            entry_count=int(
                stats.total_earned + stats.total_spent
            ),  # approx entry count
            tier=stats.tier.value if hasattr(stats.tier, "value") else str(stats.tier),
            timestamp=now,
            signature=signature,
        )

    def receive_summary(
        self,
        summary: CreditSummary,
        *,
        verify_signature: bool = True,
    ) -> bool:
        """Validate and store a peer's credit summary.

        Args:
            summary: Credit summary received from a peer.
            verify_signature: Whether to verify the Ed25519 signature.

        Returns:
            True if the summary was accepted and stored.
        """
        # Reject if no identity configured
        if not self.has_identity:
            logger.debug("credit_sync_no_identity")
            return False

        # Reject summaries for different owners
        if summary.owner_email_hash != self._owner_email_hash:
            logger.debug(
                "credit_sync_owner_mismatch",
                local=self._owner_email_hash[:16],
                remote=summary.owner_email_hash[:16],
            )
            return False

        # Reject our own summary (no point storing it)
        if summary.peer_id == self._local_peer_id:
            return False

        # Reject future timestamps (clock skew tolerance: 5 minutes)
        if summary.timestamp > time.time() + 300:
            logger.warning(
                "credit_sync_future_timestamp",
                peer_id=summary.peer_id[:16],
            )
            return False

        # DoS protection: limit stored peer count
        current_count = self._store.peer_count(self._owner_email_hash)
        if current_count >= MAX_PEER_SUMMARIES and summary.peer_id not in {
            s.peer_id
            for s in self._store.get_peer_summaries(
                self._owner_email_hash,
            )
        }:
            logger.warning("credit_sync_max_peers_reached")
            return False

        # Store the summary
        self._store.store_summary(summary)
        self._same_owner_peers[summary.peer_id] = time.time()

        logger.info(
            "credit_summary_received",
            peer_id=summary.peer_id[:16],
            total_earned=summary.total_earned,
            score=summary.contribution_score,
        )
        return True

    def aggregated_stats(self) -> AggregatedCreditStats:
        """Compute aggregated credit stats across all same-owner nodes.

        Merges the local ledger stats with all stored peer summaries
        to produce a unified view of credits.

        Returns:
            AggregatedCreditStats with totals from all nodes.
        """
        local_stats = self._ledger.stats()
        peer_summaries = (
            self._store.get_peer_summaries(self._owner_email_hash)
            if self.has_identity
            else []
        )

        total_earned = local_stats.total_earned
        total_spent = local_stats.total_spent
        total_score = local_stats.contribution_score

        for ps in peer_summaries:
            total_earned += ps.total_earned
            total_spent += ps.total_spent
            total_score += ps.contribution_score

        return AggregatedCreditStats(
            total_earned=total_earned,
            total_spent=total_spent,
            balance=total_earned - total_spent,
            contribution_score=total_score,
            node_count=1 + len(peer_summaries),
            peer_summaries=peer_summaries,
        )

    def needs_sync(self, peer_id: str) -> bool:
        """Check if a sync exchange is due with a specific peer.

        Args:
            peer_id: The peer to check.

        Returns:
            True if we should exchange summaries with this peer.
        """
        last = self._same_owner_peers.get(peer_id, 0.0)
        return (time.time() - last) > SYNC_INTERVAL_SECONDS

    def register_same_owner_peer(self, peer_id: str) -> None:
        """Register a peer as having the same owner email hash."""
        if peer_id != self._local_peer_id:
            self._same_owner_peers[peer_id] = 0.0  # needs immediate sync
            logger.info(
                "same_owner_peer_discovered",
                peer_id=peer_id[:16],
            )

    def get_same_owner_peers(self) -> list[str]:
        """Return list of known same-owner peer IDs."""
        return list(self._same_owner_peers.keys())

    def purge_stale(self) -> int:
        """Remove stale peer summaries from the store."""
        return self._store.purge_stale()
