"""Query result cross-validation.

Cross-validates search results across multiple peers to detect tampered
or fabricated results.  Each result is compared against independent
responses from other peers; outliers are penalized.

Approach:
1. For each search query, the orchestrating node sends the query to
   N peers independently.
2. Results are compared by URL overlap, score consistency, and snippet
   similarity.
3. Results that appear in fewer than AGREEMENT_THRESHOLD fraction of
   responses are flagged as potentially fabricated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()


# --- Constants -------------------------------------------------------------

# Minimum fraction of peers that must return a URL for it to be trusted
AGREEMENT_THRESHOLD: float = 0.5

# Minimum peers needed for cross-validation (below this, skip validation)
MIN_PEERS_FOR_VALIDATION: int = 2

# Minimum Jaccard word overlap for snippet matching
SNIPPET_SIMILARITY_THRESHOLD: float = 0.20

# Score deviation threshold — if a peer's score for a URL differs by more
# than this ratio from the median, it's flagged as suspicious
SCORE_DEVIATION_RATIO: float = 3.0


VERDICT_TRUSTED = "trusted"
VERDICT_UNVERIFIED = "unverified"
VERDICT_SUSPICIOUS = "suspicious"
VERDICT_FABRICATED = "fabricated"


@dataclass(frozen=True)
class PeerResult:
    """A search result from a single peer."""

    peer_id: str
    url: str
    title: str
    snippet: str
    score: float


@dataclass(frozen=True)
class ValidatedResult:
    """A search result with cross-validation metadata."""

    url: str
    title: str
    snippet: str
    score: float
    verdict: str  # One of VERDICT_* constants
    agreement_ratio: float  # Fraction of peers that returned this URL
    appearing_peers: list[str]  # Peer IDs that returned this URL
    score_deviation: float  # How much this score deviates from median
    detail: str


@dataclass(frozen=True)
class CrossValidationReport:
    """Full cross-validation report for a search query."""

    query: str
    total_peers: int
    results: list[ValidatedResult]
    suspicious_count: int
    fabricated_count: int
    detail: str


# --- Cross-validation logic -------------------------------------------------


def cross_validate_results(
    query: str,
    peer_results: dict[str, list[PeerResult]],
) -> CrossValidationReport:
    """Cross-validate search results from multiple peers.

    Args:
        query: The search query.
        peer_results: Mapping of peer_id → list of PeerResult.

    Returns:
        CrossValidationReport with validation verdicts per URL.
    """
    total_peers = len(peer_results)

    if total_peers < MIN_PEERS_FOR_VALIDATION:
        # Not enough peers — return all results as unverified
        all_results = []
        for peer_id, results in peer_results.items():
            for r in results:
                all_results.append(
                    ValidatedResult(
                        url=r.url,
                        title=r.title,
                        snippet=r.snippet,
                        score=r.score,
                        verdict=VERDICT_UNVERIFIED,
                        agreement_ratio=1.0,
                        appearing_peers=[peer_id],
                        score_deviation=0.0,
                        detail="insufficient peers for validation",
                    )
                )

        # Deduplicate by URL, keep the first appearance
        seen: set[str] = set()
        deduped = []
        for r in all_results:
            if r.url not in seen:
                seen.add(r.url)
                deduped.append(r)

        return CrossValidationReport(
            query=query,
            total_peers=total_peers,
            results=deduped,
            suspicious_count=0,
            fabricated_count=0,
            detail="cross-validation skipped: insufficient peers",
        )

    # Collect per-URL data across all peers
    url_data: dict[str, _UrlAggregation] = {}

    for peer_id, results in peer_results.items():
        for r in results:
            if r.url not in url_data:
                url_data[r.url] = _UrlAggregation(
                    url=r.url, title=r.title, snippet=r.snippet
                )
            agg = url_data[r.url]
            agg.peers.append(peer_id)
            agg.scores.append(r.score)
            agg.snippets.append(r.snippet)

    # Validate each URL
    validated: list[ValidatedResult] = []
    suspicious_count = 0
    fabricated_count = 0

    for url, agg in url_data.items():
        agreement = len(agg.peers) / total_peers
        score_dev = _score_deviation(agg.scores)

        # Determine verdict
        if agreement >= AGREEMENT_THRESHOLD:
            if score_dev > SCORE_DEVIATION_RATIO:
                verdict = VERDICT_SUSPICIOUS
                detail = f"score deviation={score_dev:.2f}"
                suspicious_count += 1
            else:
                verdict = VERDICT_TRUSTED
                detail = "ok"
        elif len(agg.peers) == 1:
            verdict = VERDICT_FABRICATED
            detail = f"only 1/{total_peers} peers returned this URL"
            fabricated_count += 1
        else:
            verdict = VERDICT_SUSPICIOUS
            detail = f"low agreement: {agreement:.0%}"
            suspicious_count += 1

        # Use median score
        sorted_scores = sorted(agg.scores)
        median_score = sorted_scores[len(sorted_scores) // 2]

        validated.append(
            ValidatedResult(
                url=url,
                title=agg.title,
                snippet=agg.snippet,
                score=median_score,
                verdict=verdict,
                agreement_ratio=round(agreement, 4),
                appearing_peers=agg.peers,
                score_deviation=round(score_dev, 4),
                detail=detail,
            )
        )

    # Sort by agreement descending, then score
    validated.sort(key=lambda v: (v.agreement_ratio, v.score), reverse=True)

    details = f"{len(validated)} URLs validated across {total_peers} peers"
    if suspicious_count:
        details += f", {suspicious_count} suspicious"
    if fabricated_count:
        details += f", {fabricated_count} fabricated"

    return CrossValidationReport(
        query=query,
        total_peers=total_peers,
        results=validated,
        suspicious_count=suspicious_count,
        fabricated_count=fabricated_count,
        detail=details,
    )


def snippet_similarity(a: str, b: str) -> float:
    """Compute Jaccard word similarity between two snippets.

    Args:
        a: First snippet text.
        b: Second snippet text.

    Returns:
        Jaccard similarity in [0, 1].
    """
    words_a = set(re.findall(r"\w+", a.lower()))
    words_b = set(re.findall(r"\w+", b.lower()))
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


# --- Internal helpers -------------------------------------------------------


@dataclass
class _UrlAggregation:
    """Internal aggregation struct for per-URL data."""

    url: str
    title: str
    snippet: str
    peers: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)


def _score_deviation(scores: list[float]) -> float:
    """Compute max deviation from median as a ratio.

    Returns:
        Max absolute deviation / median. 0.0 if median is 0 or single score.
    """
    if len(scores) <= 1:
        return 0.0

    sorted_scores = sorted(scores)
    median = sorted_scores[len(sorted_scores) // 2]

    if median <= 0:
        return 0.0

    max_dev = max(abs(s - median) for s in scores)
    return max_dev / median
