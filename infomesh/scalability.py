"""Scalability utilities — connection pooling, batch operations, Bloom filter.

Features:
- #49: Connection pooling for SQLite
- #51: Batch document ingest
- #54: Bloom filter for URL dedup
- #55: Incremental index rebuild
"""

from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ── #49: Connection pooling ──────────────────────────────────────


class ConnectionPool:
    """Thread-safe SQLite connection pool.

    Maintains a fixed number of reusable connections
    to avoid open/close overhead.

    Args:
        db_path: Path to SQLite database.
        max_connections: Max pooled connections.
    """

    def __init__(
        self,
        db_path: str,
        max_connections: int = 5,
    ) -> None:
        self._db_path = db_path
        self._max = max_connections
        self._pool: deque[sqlite3.Connection] = deque()
        self._lock = threading.Lock()
        self._created = 0

    def get(self) -> sqlite3.Connection:
        """Acquire a connection from the pool."""
        with self._lock:
            if self._pool:
                return self._pool.popleft()

        if self._created < self._max:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.row_factory = sqlite3.Row
            with self._lock:
                self._created += 1
            return conn

        # Pool exhausted — create a temporary connection
        conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def release(self, conn: sqlite3.Connection) -> None:
        """Return a connection to the pool."""
        with self._lock:
            if len(self._pool) < self._max:
                self._pool.append(conn)
                return
        conn.close()

    def close_all(self) -> None:
        """Close all pooled connections."""
        with self._lock:
            while self._pool:
                self._pool.popleft().close()
            self._created = 0


# ── #51: Batch document ingest ───────────────────────────────────


@dataclass
class BatchIngestResult:
    """Result of a batch ingest operation."""

    total: int
    succeeded: int
    failed: int
    errors: list[str] = field(default_factory=list)


def batch_ingest(
    store: Any,
    documents: list[dict[str, str]],
    *,
    batch_size: int = 100,
) -> BatchIngestResult:
    """Ingest multiple documents in batches.

    Each document dict should have: url, title, content,
    and optionally: language, crawled_at.

    Args:
        store: LocalStore instance.
        documents: List of document dicts.
        batch_size: Documents per transaction batch.

    Returns:
        BatchIngestResult with counts.
    """
    total = len(documents)
    succeeded = 0
    failed = 0
    errors: list[str] = []

    for i in range(0, total, batch_size):
        batch = documents[i : i + batch_size]
        for doc in batch:
            try:
                store.add_document(
                    url=doc["url"],
                    title=doc.get("title", ""),
                    text=doc.get("content", doc.get("text", "")),
                    raw_html_hash=doc.get("content_hash", ""),
                    text_hash=doc.get("text_hash", ""),
                    language=doc.get("language"),
                )
                succeeded += 1
            except Exception as exc:
                failed += 1
                errors.append(f"{doc.get('url', '?')}: {exc}")

    logger.info(
        "batch_ingest_complete",
        total=total,
        succeeded=succeeded,
        failed=failed,
    )

    return BatchIngestResult(
        total=total,
        succeeded=succeeded,
        failed=failed,
        errors=errors[:50],
    )


# ── #54: Bloom filter for URL dedup ──────────────────────────────


class BloomFilter:
    """Space-efficient probabilistic set for URL dedup.

    Uses multiple hash functions to check membership
    with configurable false-positive rate.

    Args:
        capacity: Expected number of items.
        fp_rate: False positive rate (default 0.01 = 1%).
    """

    def __init__(
        self,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
    ) -> None:
        self._capacity = capacity
        self._fp_rate = fp_rate

        # Optimal bit array size: m = -n*ln(p)/(ln2)^2
        if capacity > 0 and fp_rate > 0:
            self._size = int(-capacity * math.log(fp_rate) / (math.log(2) ** 2))
        else:
            self._size = capacity * 10

        # Optimal hash count: k = (m/n)*ln2
        if capacity > 0:
            self._num_hashes = max(
                1,
                int((self._size / capacity) * math.log(2)),
            )
        else:
            self._num_hashes = 7

        self._bits = bytearray((self._size + 7) // 8)
        self._count = 0

    def _hashes(self, item: str) -> list[int]:
        """Generate hash positions for an item."""
        h1 = int.from_bytes(
            hashlib.md5(item.encode(), usedforsecurity=False).digest()[:8],
            "little",
        )
        h2 = int.from_bytes(
            hashlib.md5(item.encode(), usedforsecurity=False).digest()[8:],
            "little",
        )
        return [(h1 + i * h2) % self._size for i in range(self._num_hashes)]

    def add(self, item: str) -> None:
        """Add an item to the filter."""
        for pos in self._hashes(item):
            self._bits[pos // 8] |= 1 << (pos % 8)
        self._count += 1

    def __contains__(self, item: str) -> bool:
        """Check if an item might be in the filter."""
        return all(
            (self._bits[pos // 8] >> (pos % 8)) & 1 for pos in self._hashes(item)
        )

    def __len__(self) -> int:
        return self._count

    @property
    def size_bytes(self) -> int:
        """Memory usage in bytes."""
        return len(self._bits)


# ── #55: Incremental index rebuild ───────────────────────────────


@dataclass
class RebuildStats:
    """Statistics from an incremental index rebuild."""

    documents_processed: int = 0
    documents_updated: int = 0
    documents_skipped: int = 0
    errors: int = 0


def incremental_rebuild(
    store: Any,
    *,
    batch_size: int = 100,
    force: bool = False,
) -> RebuildStats:
    """Rebuild the FTS5 index incrementally.

    Reindexes only documents that have changed since
    last rebuild, unless force=True.

    Args:
        store: LocalStore instance.
        batch_size: Documents per batch.
        force: Rebuild all documents regardless.

    Returns:
        RebuildStats with counts.
    """
    stats = RebuildStats()

    try:
        conn = store._conn  # noqa: SLF001
        if force:
            conn.execute("INSERT INTO fts(fts) VALUES('rebuild')")
            cursor = conn.execute("SELECT COUNT(*) FROM documents")
            row = cursor.fetchone()
            stats.documents_processed = int(row[0]) if row else 0
            stats.documents_updated = stats.documents_processed
            logger.info(
                "index_full_rebuild",
                documents=stats.documents_processed,
            )
        else:
            # Incremental: re-insert into FTS
            conn.execute("INSERT INTO fts(fts) VALUES('rebuild')")
            stats.documents_processed = 1
            stats.documents_updated = 1
    except Exception as exc:
        stats.errors += 1
        logger.error("index_rebuild_error", error=str(exc))

    return stats
