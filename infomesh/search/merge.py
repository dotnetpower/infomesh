"""Multi-source result merging for hybrid search (FTS5 + vector).

Merges keyword-based (BM25) and semantic (cosine similarity) results
using Reciprocal Rank Fusion (RRF) for robust re-ranking.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from infomesh.index.local_store import SearchResult
from infomesh.index.vector_store import VectorSearchResult

logger = structlog.get_logger()

# RRF constant — higher values smooth rank differences
_RRF_K = 60


@dataclass(frozen=True)
class MergedResult:
    """Unified search result from hybrid search."""

    doc_id: str
    url: str
    title: str
    snippet: str
    fts_score: float | None  # BM25 score (if matched by FTS5)
    vector_score: float | None  # Cosine similarity (if matched by vector)
    combined_score: float  # RRF-fused score
    source: str  # "fts", "vector", or "hybrid"


def merge_results(
    fts_results: list[SearchResult],
    vector_results: list[VectorSearchResult],
    *,
    limit: int = 10,
    fts_weight: float = 1.0,
    vector_weight: float = 1.0,
) -> list[MergedResult]:
    """Merge FTS5 and vector search results using Reciprocal Rank Fusion.

    RRF score for a document ``d`` across rankings ``R``:

    .. math::

        RRF(d) = \\sum_{R} \\frac{w_R}{k + rank_R(d)}

    where ``k`` is a smoothing constant (60) and ``w_R`` is the weight
    for each ranking source.

    Args:
        fts_results: BM25-ranked keyword search results.
        vector_results: Cosine-similarity ranked vector results.
        limit: Maximum merged results to return.
        fts_weight: Weight for FTS results in RRF.
        vector_weight: Weight for vector results in RRF.

    Returns:
        Merged results sorted by combined RRF score (descending).
    """
    # Build per-document score accumulator keyed by URL (canonical identifier)
    scores: dict[str, dict] = {}

    # Process FTS5 results
    for rank, r in enumerate(fts_results, 1):
        rrf = fts_weight / (_RRF_K + rank)
        key = r.url
        if key not in scores:
            scores[key] = {
                "doc_id": str(r.doc_id),
                "url": r.url,
                "title": r.title,
                "snippet": r.snippet,
                "fts_score": r.score,
                "vector_score": None,
                "rrf": 0.0,
                "source": "fts",
            }
        scores[key]["rrf"] += rrf
        scores[key]["fts_score"] = r.score

    # Process vector results
    for rank, r in enumerate(vector_results, 1):
        rrf = vector_weight / (_RRF_K + rank)
        key = r.url
        if key not in scores:
            scores[key] = {
                "doc_id": r.doc_id,
                "url": r.url,
                "title": r.title,
                "snippet": r.text_preview[:200] if r.text_preview else "",
                "fts_score": None,
                "vector_score": r.score,
                "rrf": 0.0,
                "source": "vector",
            }
        else:
            # Merge — document found in both FTS and vector
            scores[key]["source"] = "hybrid"
        scores[key]["rrf"] += rrf
        scores[key]["vector_score"] = r.score

    # Sort by RRF score descending
    ranked = sorted(scores.values(), key=lambda d: d["rrf"], reverse=True)

    merged = [
        MergedResult(
            doc_id=d["doc_id"],
            url=d["url"],
            title=d["title"],
            snippet=d["snippet"],
            fts_score=d["fts_score"],
            vector_score=d["vector_score"],
            combined_score=round(d["rrf"], 6),
            source=d["source"],
        )
        for d in ranked[:limit]
    ]

    logger.info(
        "results_merged",
        fts_count=len(fts_results),
        vector_count=len(vector_results),
        merged_count=len(merged),
        hybrid_count=sum(1 for m in merged if m.source == "hybrid"),
    )

    return merged
