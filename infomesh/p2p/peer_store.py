"""Persistent peer store — remembers successfully connected peers across restarts.

When the node restarts and all bootstrap servers are unreachable, the peer
store provides previously-known peer addresses so the node can reconnect
directly without relying on any central infrastructure.

Peers are stored in a lightweight SQLite database at
``<data_dir>/peer_store.db`` with the following lifecycle:

* **On successful connect**: ``upsert(peer_id, multiaddr)`` is called.
* **On startup**: ``load_recent(limit)`` returns the most recently seen
  peers, ordered by freshness.
* **Periodically**: ``prune(max_age_hours)`` removes stale entries.

Usage::

    store = PeerStore(Path("~/.infomesh"))
    store.upsert("12D3KooW...", "/ip4/1.2.3.4/tcp/4001/p2p/12D3KooW...")
    peers = store.load_recent(limit=20)
    store.close()
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from infomesh.db import SQLiteStore

logger = structlog.get_logger()

# ── Constants ───────────────────────────────────────────────────────────

DEFAULT_MAX_PEERS = 200  # Maximum cached peers
DEFAULT_MAX_AGE_HOURS = 168  # 7 days — prune peers older than this
DEFAULT_LOAD_LIMIT = 20  # How many peers to try on startup


@dataclass(frozen=True)
class CachedPeer:
    """A peer entry loaded from the persistent store."""

    peer_id: str
    multiaddr: str
    last_seen: float
    success_count: int
    fail_count: int

    @property
    def success_rate(self) -> float:
        """Fraction of successful connections (0.0–1.0)."""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.0
        return self.success_count / total


class PeerStore(SQLiteStore):
    """SQLite-backed persistent peer cache.

    Stores multiaddrs of successfully connected peers so they can be
    retried on next startup — even when bootstrap nodes are unavailable.
    """

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS peers (
            peer_id    TEXT PRIMARY KEY,
            multiaddr  TEXT NOT NULL,
            last_seen  REAL NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 1,
            fail_count    INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_peers_last_seen
            ON peers (last_seen DESC);
    """

    def __init__(self, data_dir: Path | str) -> None:
        db_path = Path(data_dir) / "peer_store.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        super().__init__(db_path)

    # ── Write operations ───────────────────────────────────────

    def upsert(self, peer_id: str, multiaddr: str) -> None:
        """Insert or update a peer after a successful connection.

        If the peer already exists, bumps ``last_seen`` and increments
        ``success_count``.
        """
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO peers (peer_id, multiaddr, last_seen, success_count, fail_count)
            VALUES (?, ?, ?, 1, 0)
            ON CONFLICT(peer_id) DO UPDATE SET
                multiaddr = excluded.multiaddr,
                last_seen = excluded.last_seen,
                success_count = success_count + 1
            """,
            (peer_id, multiaddr, now),
        )
        self._conn.commit()

    def record_failure(self, peer_id: str) -> None:
        """Record a failed connection attempt for a cached peer."""
        self._conn.execute(
            """
            UPDATE peers SET fail_count = fail_count + 1
            WHERE peer_id = ?
            """,
            (peer_id,),
        )
        self._conn.commit()

    def remove(self, peer_id: str) -> None:
        """Remove a peer from the store."""
        self._conn.execute("DELETE FROM peers WHERE peer_id = ?", (peer_id,))
        self._conn.commit()

    # ── Read operations ────────────────────────────────────────

    def load_recent(self, limit: int = DEFAULT_LOAD_LIMIT) -> list[CachedPeer]:
        """Load the most recently seen peers, ordered by freshness.

        Peers with a high failure rate (>80% failures with >=5 attempts)
        are excluded automatically.

        Args:
            limit: Maximum number of peers to return.

        Returns:
            List of :class:`CachedPeer` entries.
        """
        rows = self._conn.execute(
            """
            SELECT peer_id, multiaddr, last_seen, success_count, fail_count
            FROM peers
            WHERE (success_count + fail_count) < 5
               OR CAST(success_count AS REAL) / (success_count + fail_count) > 0.2
            ORDER BY last_seen DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            CachedPeer(
                peer_id=r[0],
                multiaddr=r[1],
                last_seen=r[2],
                success_count=r[3],
                fail_count=r[4],
            )
            for r in rows
        ]

    def count(self) -> int:
        """Total number of cached peers."""
        row = self._conn.execute("SELECT COUNT(*) FROM peers").fetchone()
        return int(row[0]) if row else 0

    # ── Maintenance ────────────────────────────────────────────

    def prune(
        self,
        max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
        max_peers: int = DEFAULT_MAX_PEERS,
    ) -> int:
        """Remove stale or excess peers.

        1. Deletes peers older than ``max_age_hours``.
        2. If still over ``max_peers``, trims the oldest entries.

        Returns:
            Number of peers removed.
        """
        cutoff = time.time() - (max_age_hours * 3600)
        cursor = self._conn.execute("DELETE FROM peers WHERE last_seen < ?", (cutoff,))
        removed = cursor.rowcount

        # Trim excess
        current = self.count()
        if current > max_peers:
            excess = current - max_peers
            self._conn.execute(
                """
                DELETE FROM peers WHERE peer_id IN (
                    SELECT peer_id FROM peers
                    ORDER BY last_seen ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
            removed += excess

        if removed > 0:
            self._conn.commit()
            logger.info("peer_store_pruned", removed=removed, remaining=self.count())

        return removed

    def save_connected(self, peers: list[tuple[str, str]]) -> None:
        """Batch-save a list of currently connected peers.

        Args:
            peers: List of ``(peer_id, multiaddr)`` tuples.
        """
        now = time.time()
        for peer_id, multiaddr in peers:
            self._conn.execute(
                """
                INSERT INTO peers (
                    peer_id, multiaddr, last_seen,
                    success_count, fail_count
                ) VALUES (?, ?, ?, 1, 0)
                ON CONFLICT(peer_id) DO UPDATE SET
                    multiaddr = excluded.multiaddr,
                    last_seen = excluded.last_seen,
                    success_count = success_count + 1
                """,
                (peer_id, multiaddr, now),
            )
        self._conn.commit()
        if peers:
            logger.info("peer_store_saved", count=len(peers))
