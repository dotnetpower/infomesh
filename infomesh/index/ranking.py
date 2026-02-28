"""BM25 + freshness + trust + authority ranking for search results.

Combines FTS5 BM25 scores with time-based freshness decay,
peer trust scores, and domain authority for final result ranking.
Used by both local and distributed search paths.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()

# --- Tuning constants ---------------------------------------------------

# Weight factors for the four ranking signals (must sum to ~1.0)
WEIGHT_BM25 = 0.45
WEIGHT_FRESHNESS = 0.20
WEIGHT_TRUST = 0.15
WEIGHT_AUTHORITY = 0.20

# Freshness half-life in seconds (7 days).
# After one half-life the freshness component drops to 50 %.
FRESHNESS_HALF_LIFE_SECONDS: float = 7 * 24 * 3600

# Minimum freshness score to avoid total decay for old documents.
MIN_FRESHNESS: float = 0.05

# Default trust when no peer trust information is available.
DEFAULT_TRUST: float = 0.50


@dataclass(frozen=True)
class RankedResult:
    """A search result with a composite ranking score."""

    doc_id: str | int
    url: str
    title: str
    snippet: str
    bm25_score: float
    freshness_score: float
    trust_score: float
    authority_score: float
    combined_score: float
    crawled_at: float
    peer_id: str | None = None


# --- Scoring helpers -----------------------------------------------------


def freshness_score(crawled_at: float, *, now: float | None = None) -> float:
    """Compute a 0‒1 freshness score using exponential decay.

    .. math::

        f(t) = max(\\text{MIN}, 2^{-\\Delta t / T_{1/2}})

    Args:
        crawled_at: Unix timestamp when the document was crawled.
        now: Current timestamp (defaults to ``time.time()``).

    Returns:
        Freshness score in ``[MIN_FRESHNESS, 1.0]``.
    """
    now = now or time.time()
    age = max(0.0, now - crawled_at)
    decay = math.pow(2, -age / FRESHNESS_HALF_LIFE_SECONDS)
    return max(MIN_FRESHNESS, decay)


def normalize_bm25(score: float, *, max_score: float = 1.0) -> float:
    """Normalize a raw BM25 score to ``[0, 1]`` using saturation.

    Uses ``score / (score + k)`` sigmoid where *k* equals *max_score*
    so that a score equal to *max_score* maps to 0.5.

    Args:
        score: Raw BM25 score (non-negative).
        max_score: Saturation constant; a score equal to this maps to 0.5.

    Returns:
        Normalized score in ``[0, 1]``.
    """
    if score <= 0:
        return 0.0
    return score / (score + max_score)


def combined_score(
    bm25: float,
    freshness: float,
    trust: float,
    authority: float = 0.0,
    *,
    w_bm25: float = WEIGHT_BM25,
    w_fresh: float = WEIGHT_FRESHNESS,
    w_trust: float = WEIGHT_TRUST,
    w_authority: float = WEIGHT_AUTHORITY,
) -> float:
    """Compute the weighted sum of the four ranking signals.

    Args:
        bm25: Normalized BM25 score ``[0, 1]``.
        freshness: Freshness score ``[0, 1]``.
        trust: Trust score ``[0, 1]``.
        authority: Domain authority score ``[0, 1]``.
        w_bm25: Weight for relevance.
        w_fresh: Weight for freshness.
        w_trust: Weight for trust.
        w_authority: Weight for domain authority.

    Returns:
        Combined score (higher is better).
    """
    return (
        w_bm25 * bm25 + w_fresh * freshness + w_trust * trust + w_authority * authority
    )


# --- Batch ranking -------------------------------------------------------


@dataclass(frozen=True)
class _RawCandidate:
    """Internal container for an un-ranked candidate."""

    doc_id: str | int
    url: str
    title: str
    snippet: str
    bm25_raw: float
    crawled_at: float
    peer_id: str | None
    trust: float
    authority: float = 0.0


def rank_results(
    candidates: list[_RawCandidate],
    *,
    limit: int = 10,
    now: float | None = None,
) -> list[RankedResult]:
    """Rank a list of search candidates by composite score.

    Steps:
    1. Find max BM25 to use as normalizer.
    2. Compute per-candidate ``(norm_bm25, freshness, trust, authority)``.
    3. Combine via weighted sum.
    4. Sort descending, return top *limit*.

    Args:
        candidates: Raw search candidates (from local or remote).
        limit: Maximum results to return.
        now: Override current timestamp for testing.

    Returns:
        Sorted list of :class:`RankedResult`.
    """
    if not candidates:
        return []

    now = now or time.time()

    max_bm25 = max(c.bm25_raw for c in candidates) or 1.0

    scored: list[RankedResult] = []
    for c in candidates:
        norm_bm25 = normalize_bm25(c.bm25_raw, max_score=max_bm25)
        fresh = freshness_score(c.crawled_at, now=now)
        combo = combined_score(norm_bm25, fresh, c.trust, c.authority)
        scored.append(
            RankedResult(
                doc_id=c.doc_id,
                url=c.url,
                title=c.title,
                snippet=c.snippet,
                bm25_score=round(norm_bm25, 6),
                freshness_score=round(fresh, 6),
                trust_score=round(c.trust, 6),
                authority_score=round(c.authority, 6),
                combined_score=round(combo, 6),
                crawled_at=c.crawled_at,
                peer_id=c.peer_id,
            )
        )

    scored.sort(key=lambda r: r.combined_score, reverse=True)

    logger.info(
        "results_ranked",
        candidates=len(candidates),
        returned=min(limit, len(scored)),
    )

    return scored[:limit]


def rank_local_results(
    results: list[Any],  # list[SearchResult] from LocalStore
    *,
    trust: float = DEFAULT_TRUST,
    authority_fn: Callable[[str], float] | None = None,
    limit: int = 10,
    now: float | None = None,
) -> list[RankedResult]:
    """Convenience wrapper: rank LocalStore SearchResult objects.

    Args:
        results: SearchResult from ``LocalStore.search()``.
        trust: Trust score to apply (local results use default).
        authority_fn: Optional callable ``(url) -> float`` returning
            domain authority for a URL.  When ``None``, authority
            defaults to 0.0 for all results.
        limit: Maximum results.
        now: Override timestamp.

    Returns:
        Ranked results.
    """
    candidates = [
        _RawCandidate(
            doc_id=r.doc_id,
            url=r.url,
            title=r.title,
            snippet=r.snippet,
            bm25_raw=r.score,
            crawled_at=r.crawled_at,
            peer_id=None,
            trust=trust,
            authority=authority_fn(r.url) if authority_fn else 0.0,
        )
        for r in results
    ]
    return rank_results(candidates, limit=limit, now=now)
