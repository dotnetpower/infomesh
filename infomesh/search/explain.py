"""Query explain mode — shows how ranking was computed.

Feature #62: Helps developers understand why results are
ranked in a particular order.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from infomesh.index.ranking import (
    WEIGHT_AUTHORITY,
    WEIGHT_BM25,
    WEIGHT_FRESHNESS,
    WEIGHT_TRUST,
    RankedResult,
)


@dataclass
class ScoreExplanation:
    """Breakdown of how a result's combined score was computed."""

    url: str
    title: str
    combined_score: float
    components: dict[str, float] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)
    weighted: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "combined_score": round(self.combined_score, 4),
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "weighted_contributions": {
                k: round(v, 4) for k, v in self.weighted.items()
            },
            "notes": self.notes,
        }


@dataclass
class QueryExplanation:
    """Full explanation of a search query's execution."""

    query: str
    sanitized_query: str
    total_results: int
    elapsed_ms: float
    results: list[ScoreExplanation]
    pipeline: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "sanitized_query": self.sanitized_query,
            "total_results": self.total_results,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "pipeline": self.pipeline,
            "results": [r.to_dict() for r in self.results],
        }


def explain_result(result: RankedResult) -> ScoreExplanation:
    """Generate a score explanation for a single result.

    Args:
        result: A ranked search result.

    Returns:
        ScoreExplanation with component breakdown.
    """
    components = {
        "bm25": result.bm25_score,
        "freshness": result.freshness_score,
        "trust": result.trust_score,
        "authority": result.authority_score,
    }
    weights = {
        "bm25": WEIGHT_BM25,
        "freshness": WEIGHT_FRESHNESS,
        "trust": WEIGHT_TRUST,
        "authority": WEIGHT_AUTHORITY,
    }
    weighted = {k: components[k] * weights[k] for k in components}

    notes: list[str] = []
    if result.bm25_score > 0.8:
        notes.append("Strong keyword match")
    if result.freshness_score > 0.8:
        notes.append("Recently crawled")
    elif result.freshness_score < 0.2:
        notes.append("Stale content — may need recrawl")
    if result.trust_score > 0.8:
        notes.append("High-trust peer")
    if result.authority_score > 0.5:
        notes.append("High domain authority")

    return ScoreExplanation(
        url=result.url,
        title=result.title,
        combined_score=result.combined_score,
        components=components,
        weights=weights,
        weighted=weighted,
        notes=notes,
    )


def explain_query(
    query: str,
    sanitized: str,
    results: list[RankedResult],
    elapsed_ms: float,
    *,
    pipeline: list[str] | None = None,
) -> QueryExplanation:
    """Generate a full query execution explanation.

    Args:
        query: Original query string.
        sanitized: Sanitized FTS5 query.
        results: Ranked results.
        elapsed_ms: Execution time.
        pipeline: List of processing steps applied.

    Returns:
        QueryExplanation with per-result breakdowns.
    """
    if pipeline is None:
        pipeline = [
            "sanitize_fts_query",
            "fts5_search",
            "bm25_ranking",
            "freshness_decay",
            "trust_scoring",
            "authority_scoring",
            "combined_ranking",
        ]

    explanations = [explain_result(r) for r in results]

    return QueryExplanation(
        query=query,
        sanitized_query=sanitized,
        total_results=len(results),
        elapsed_ms=elapsed_ms,
        results=explanations,
        pipeline=pipeline,
    )
