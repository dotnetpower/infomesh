"""SimHash near-duplicate detection.

SimHash produces a 64-bit fingerprint from text.  Two documents are
near-duplicates when their Hamming distance (number of differing bits)
is ≤ ``HAMMING_THRESHOLD`` (default 3).

The implementation follows Charikar's 2002 algorithm:
  1. Tokenize text into shingles (word n-grams).
  2. Hash each shingle to a 64-bit value.
  3. Build a weighted bit-vector (counts).
  4. Collapse to a 64-bit fingerprint (majority vote per bit).

Usage::

    fp = simhash("The quick brown fox jumps over the lazy dog")
    is_near = hamming_distance(fp, other_fp) <= HAMMING_THRESHOLD
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Default near-duplicate threshold (Hamming distance ≤ 3)
HAMMING_THRESHOLD: int = 3

# Number of bits in the fingerprint
_NUM_BITS: int = 64

# Default shingle width (word n-gram size)
_SHINGLE_WIDTH: int = 3

# Regex for word tokenization
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str, width: int = _SHINGLE_WIDTH) -> list[str]:
    """Split text into overlapping word shingles.

    Args:
        text: Input text.
        width: Number of words per shingle.

    Returns:
        List of shingle strings (e.g., ["the quick brown", "quick brown fox"]).
    """
    words = _WORD_RE.findall(text.lower())
    if len(words) < width:
        return [" ".join(words)] if words else []
    return [" ".join(words[i : i + width]) for i in range(len(words) - width + 1)]


def _hash64(data: str) -> int:
    """Hash a string to a 64-bit unsigned integer using MD5 truncation."""
    digest = hashlib.md5(data.encode("utf-8")).digest()  # noqa: S324
    return int.from_bytes(digest[:8], byteorder="big")


def simhash(text: str, *, shingle_width: int = _SHINGLE_WIDTH) -> int:
    """Compute the 64-bit SimHash fingerprint of *text*.

    Args:
        text: Document text.
        shingle_width: Word n-gram size for shingling.

    Returns:
        64-bit unsigned integer fingerprint.
    """
    shingles = _tokenize(text, width=shingle_width)
    if not shingles:
        return 0

    # Weighted bit-vector: +1 for each 1-bit, -1 for each 0-bit
    vector = [0] * _NUM_BITS

    for shingle in shingles:
        h = _hash64(shingle)
        for i in range(_NUM_BITS):
            if h & (1 << i):
                vector[i] += 1
            else:
                vector[i] -= 1

    # Collapse: bit is 1 if weight ≥ 0
    fingerprint = 0
    for i in range(_NUM_BITS):
        if vector[i] >= 0:
            fingerprint |= 1 << i

    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    """Count the number of differing bits between two integers.

    Args:
        a: First fingerprint.
        b: Second fingerprint.

    Returns:
        Number of differing bits (0–64).
    """
    return bin(a ^ b).count("1")


def is_near_duplicate(
    a: int,
    b: int,
    *,
    threshold: int = HAMMING_THRESHOLD,
) -> bool:
    """Check whether two fingerprints are near-duplicates.

    Args:
        a: First SimHash fingerprint.
        b: Second SimHash fingerprint.
        threshold: Maximum Hamming distance to consider near-duplicate.

    Returns:
        True if Hamming distance ≤ threshold.
    """
    return hamming_distance(a, b) <= threshold


@dataclass
class SimHashIndex:
    """In-memory index for fast near-duplicate lookups.

    Stores fingerprint → document ID mappings and supports
    lookup by Hamming distance threshold.

    For small-to-medium indexes (< 1M docs), linear scan is fast enough.
    Phase 2+ can add bit-permutation tables for O(1) lookup.

    The index is capped at ``max_entries`` unique fingerprints to prevent
    unbounded memory growth on long-running nodes.  When the cap is reached,
    the oldest entries (by insertion order) are evicted.
    """

    _entries: dict[int, list[int]]  # fingerprint → [doc_id, ...]
    _max_entries: int

    def __init__(self, *, max_entries: int = 500_000) -> None:
        self._entries: dict[int, list[int]] = {}
        self._max_entries = max_entries

    @property
    def size(self) -> int:
        """Total number of indexed fingerprints."""
        return len(self._entries)

    def add(self, doc_id: int, fingerprint: int) -> None:
        """Add a document fingerprint to the index.

        Args:
            doc_id: Unique document identifier.
            fingerprint: SimHash 64-bit fingerprint.
        """
        # Evict oldest entries if at capacity
        while len(self._entries) >= self._max_entries:
            oldest_key = next(iter(self._entries))
            del self._entries[oldest_key]

        self._entries.setdefault(fingerprint, []).append(doc_id)

    def remove(self, doc_id: int, fingerprint: int) -> None:
        """Remove a document fingerprint from the index.

        Args:
            doc_id: Document identifier to remove.
            fingerprint: The fingerprint that was indexed.
        """
        ids = self._entries.get(fingerprint)
        if ids:
            with contextlib.suppress(ValueError):
                ids.remove(doc_id)
            if not ids:
                del self._entries[fingerprint]

    def find_near_duplicates(
        self,
        fingerprint: int,
        *,
        threshold: int = HAMMING_THRESHOLD,
    ) -> list[int]:
        """Find document IDs whose fingerprints are within *threshold* of *fingerprint*.

        Args:
            fingerprint: Query fingerprint.
            threshold: Maximum Hamming distance.

        Returns:
            List of matching document IDs (may be empty).
        """
        matches: list[int] = []
        for stored_fp, doc_ids in self._entries.items():
            if hamming_distance(fingerprint, stored_fp) <= threshold:
                matches.extend(doc_ids)
        return matches

    def get_stats(self) -> dict[str, int]:
        """Return index statistics."""
        total_docs = sum(len(ids) for ids in self._entries.values())
        return {
            "unique_fingerprints": len(self._entries),
            "total_documents": total_docs,
        }
