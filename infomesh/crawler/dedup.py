"""Deduplication pipeline.

URL normalization + SHA-256 content hash + SimHash near-dedup.
"""

from __future__ import annotations

import contextlib
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import structlog

from infomesh.crawler.simhash import SimHashIndex, simhash
from infomesh.hashing import content_hash

logger = structlog.get_logger()

# SQLite INTEGER is signed 64-bit (-2^63 .. 2^63-1).
# SimHash produces unsigned 64-bit values, so we convert before storage.
_SIGN_BIT = 1 << 63
_MASK64 = (1 << 64) - 1


def _to_signed64(val: int) -> int:
    """Convert unsigned 64-bit int to signed for SQLite storage."""
    if val >= _SIGN_BIT:
        return val - (1 << 64)
    return val


def _to_unsigned64(val: int) -> int:
    """Convert signed 64-bit int back to unsigned."""
    return val & _MASK64


# Common tracking parameters to strip
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "source",
        "mc_cid",
        "mc_eid",
    }
)


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication.

    - Lowercase scheme and host
    - Remove fragments
    - Remove tracking parameters
    - Sort remaining query parameters
    - Remove trailing slashes (except root)

    Args:
        url: Raw URL string.

    Returns:
        Normalized URL string.
    """
    parsed = urlparse(url)

    # Lowercase scheme and host
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()

    # Remove fragment
    # Sort query params, strip tracking
    params = parse_qs(parsed.query, keep_blank_values=True)
    filtered = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
    sorted_query = urlencode(sorted(filtered.items()), doseq=True)

    # Normalize path — remove trailing slash except for root
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    if not path:
        path = "/"

    return urlunparse((scheme, netloc, path, parsed.params, sorted_query, ""))


class DeduplicatorDB:
    """URL and content hash based deduplication using SQLite.

    Supports three dedup layers:
      1. URL normalization (canonical form)
      2. SHA-256 exact content hash
      3. SimHash near-duplicate detection (Hamming distance ≤ 3)
    """

    def __init__(self, db_path: str | None = None) -> None:
        import sqlite3

        self._conn = sqlite3.connect(db_path or ":memory:")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_urls (
                url_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                content_hash TEXT,
                simhash INTEGER,
                crawled_at REAL NOT NULL
            )
        """)
        # Add simhash column if upgrading from older schema
        with contextlib.suppress(sqlite3.OperationalError):
            self._conn.execute("ALTER TABLE seen_urls ADD COLUMN simhash INTEGER")
        self._conn.commit()
        self._simhash_index = SimHashIndex()

    def is_url_seen(self, url: str) -> bool:
        """Check if a normalized URL has been seen before."""
        normalized = normalize_url(url)
        url_hash = content_hash(normalized)
        row = self._conn.execute(
            "SELECT 1 FROM seen_urls WHERE url_hash = ?", (url_hash,)
        ).fetchone()
        return row is not None

    def is_content_seen(self, text_hash: str) -> bool:
        """Check if content with this hash has been seen before."""
        row = self._conn.execute(
            "SELECT 1 FROM seen_urls WHERE content_hash = ?", (text_hash,)
        ).fetchone()
        return row is not None

    def is_near_duplicate(self, text: str, *, threshold: int = 3) -> bool:
        """Check if text is a near-duplicate of any indexed document.

        Uses SimHash fingerprinting with Hamming distance comparison.

        Args:
            text: Document text to check.
            threshold: Max Hamming distance (default 3).

        Returns:
            True if a near-duplicate exists.
        """
        fp = simhash(text)
        matches = self._simhash_index.find_near_duplicates(fp, threshold=threshold)
        return len(matches) > 0

    def mark_seen(
        self, url: str, text_hash: str, text: str = "", *, commit: bool = True
    ) -> None:
        """Mark a URL and its content hash as seen.

        If *text* is provided, also computes and stores the SimHash fingerprint.

        Args:
            url: Page URL.
            text_hash: SHA-256 content hash.
            text: Optional extracted text for SimHash indexing.
            commit: If ``False``, skip the SQLite commit (caller must
                commit later). Useful for batch inserts.
        """
        import time

        normalized = normalize_url(url)
        url_hash = content_hash(normalized)
        fp: int | None = None
        if text:
            fp = simhash(text)
        # Convert unsigned 64-bit to signed for SQLite INTEGER storage
        fp_signed = _to_signed64(fp) if fp is not None else None
        self._conn.execute(
            "INSERT OR REPLACE INTO seen_urls"
            " (url_hash, url, content_hash, simhash, crawled_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (url_hash, normalized, text_hash, fp_signed, time.time()),
        )
        if commit:
            self._conn.commit()

        # Add to in-memory SimHash index (use url_hash as pseudo doc_id)
        if fp is not None:
            # Use first 4 bytes of url_hash (hex) for a stable 31-bit doc_id.
            # hash() is non-deterministic across Python sessions (PYTHONHASHSEED).
            doc_id = int(url_hash[:8], 16) & 0x7FFFFFFF
            self._simhash_index.add(doc_id, fp)

    @property
    def simhash_index(self) -> SimHashIndex:
        """Access the in-memory SimHash index."""
        return self._simhash_index

    def flush(self) -> None:
        """Commit any pending database writes (for batch mode)."""
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
