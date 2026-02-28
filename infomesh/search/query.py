"""Query parsing and local search orchestration.

Supports keyword-only (FTS5), semantic-only (vector), hybrid, and
distributed (DHT) search modes.

- **Local**: FTS5-only, fast (< 10ms target).
- **Hybrid**: FTS5 + vector via Reciprocal Rank Fusion (RRF).
- **Distributed**: Local + DHT keyword lookup + cross-node result merge.

All search paths apply BM25 + freshness + trust + authority ranking.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from infomesh.index.local_store import LocalStore
from infomesh.index.ranking import RankedResult, rank_local_results
from infomesh.types import VectorStoreLike

if TYPE_CHECKING:
    from infomesh.index.distributed import DistributedIndex

logger = structlog.get_logger()


@dataclass(frozen=True)
class QueryResult:
    """Aggregated search result with ranking applied."""

    results: list[RankedResult]
    total: int
    elapsed_ms: float
    source: str  # "local" or "network"


@dataclass(frozen=True)
class HybridResult:
    """Aggregated hybrid search result with merged scoring."""

    results: list  # list[MergedResult]
    total: int
    elapsed_ms: float
    source: str  # "hybrid", "fts", or "vector"


def _sanitize_fts_query(query: str) -> str:
    """Sanitize query for FTS5 syntax.

    Removes special FTS5 characters and operators that could cause
    syntax errors or be used for injection attacks.
    """
    # Cap query length to prevent abuse
    query = query[:1000]

    # Remove FTS5 special characters
    sanitized = re.sub(r'["\(\)\*\{\}\^:]', " ", query)

    # Remove FTS5 boolean/proximity operators (case-insensitive, whole words)
    sanitized = re.sub(r"\b(AND|OR|NOT|NEAR)\b", " ", sanitized, flags=re.IGNORECASE)

    # Collapse multiple spaces
    sanitized = re.sub(r"\s+", " ", sanitized).strip()

    if not sanitized:
        return query.strip()[:100]

    return sanitized


def search_local(
    store: LocalStore,
    query: str,
    *,
    limit: int = 10,
    authority_fn: Callable[[str], float] | None = None,
) -> QueryResult:
    """Search the local FTS5 index with full ranking.

    Applies BM25 + freshness + trust + domain authority ranking
    to FTS5 results.

    Args:
        store: Local document store.
        query: User search query.
        limit: Maximum results.
        authority_fn: Optional ``(url) -> float`` for domain authority lookup.

    Returns:
        QueryResult with ranked search results.
    """
    start = time.monotonic()

    sanitized = _sanitize_fts_query(query)
    raw_results = store.search(sanitized, limit=limit * 2)  # over-fetch for ranking

    # Apply full ranking pipeline
    ranked = rank_local_results(
        raw_results,
        authority_fn=authority_fn,
        limit=limit,
    )

    elapsed = (time.monotonic() - start) * 1000
    logger.info(
        "query_local",
        query=query,
        raw=len(raw_results),
        ranked=len(ranked),
        elapsed_ms=round(elapsed, 1),
    )

    return QueryResult(
        results=ranked,
        total=len(ranked),
        elapsed_ms=elapsed,
        source="local",
    )


def search_hybrid(
    store: LocalStore,
    vector_store: VectorStoreLike,
    query: str,
    *,
    limit: int = 10,
    fts_weight: float = 1.0,
    vector_weight: float = 1.0,
    authority_fn: Callable[[str], float] | None = None,
) -> HybridResult:
    """Search using both FTS5 and vector store, merge via RRF.

    Args:
        store: Local FTS5 document store.
        vector_store: VectorStore instance (from infomesh.index.vector_store).
        query: User search query.
        limit: Maximum results.
        fts_weight: Weight for FTS results in RRF fusion.
        vector_weight: Weight for vector results in RRF fusion.
        authority_fn: Optional ``(url) -> float`` for domain authority lookup.

    Returns:
        HybridResult with merged results from both engines.
    """
    from infomesh.index.vector_store import VectorStore
    from infomesh.search.merge import merge_results

    start = time.monotonic()

    # FTS5 keyword search
    sanitized = _sanitize_fts_query(query)
    fts_results = store.search(sanitized, limit=limit)

    # Vector semantic search
    if not isinstance(vector_store, VectorStore):
        raise TypeError(
            f"vector_store must be a VectorStore, got {type(vector_store).__name__}"
        )
    vec_results = vector_store.search(query, limit=limit)

    # Merge via RRF
    merged = merge_results(
        fts_results,
        vec_results,
        limit=limit,
        fts_weight=fts_weight,
        vector_weight=vector_weight,
    )

    elapsed = (time.monotonic() - start) * 1000

    # Determine source label
    has_fts = any(m.fts_score is not None for m in merged)
    has_vec = any(m.vector_score is not None for m in merged)
    if has_fts and has_vec:
        source = "hybrid"
    elif has_vec:
        source = "vector"
    else:
        source = "fts"

    logger.info(
        "query_hybrid",
        query=query,
        fts_count=len(fts_results),
        vec_count=len(vec_results),
        merged_count=len(merged),
        elapsed_ms=round(elapsed, 1),
    )

    return HybridResult(
        results=merged,
        total=len(merged),
        elapsed_ms=elapsed,
        source=source,
    )


# ── Distributed search ──────────────────────────────────────────


@dataclass
class DistributedResult:
    """Aggregated result from local + distributed (DHT) search."""

    results: list[RankedResult]
    total: int
    elapsed_ms: float
    source: str  # "distributed" | "local_only"
    local_count: int = 0
    remote_count: int = 0


async def search_distributed(
    store: LocalStore,
    distributed_index: DistributedIndex,
    query: str,
    *,
    limit: int = 10,
    authority_fn: Callable[[str], float] | None = None,
    vector_store: VectorStoreLike | None = None,
) -> DistributedResult:
    """Search local index + DHT distributed index, merge results.

    1. Run local FTS5 search.
    2. Extract keywords and query DHT for peer pointers.
    3. Convert peer pointers to ``RankedResult`` stubs.
    4. Merge & deduplicate by URL, keeping best score.
    5. Optionally include vector results via hybrid path.

    Args:
        store: Local FTS5 document store.
        distributed_index: DHT-backed distributed index.
        query: User search query.
        limit: Maximum results.
        authority_fn: Optional ``(url) -> float`` for domain authority lookup.
        vector_store: Optional vector store for hybrid local search.

    Returns:
        DistributedResult with merged local + remote results.
    """
    from infomesh.index.distributed import extract_keywords

    start = time.monotonic()

    # 1. Local search
    _sanitize_fts_query(query)
    local_results = search_local(
        store,
        query,
        limit=limit,
        authority_fn=authority_fn,
    )

    # 2. DHT keyword query
    keywords = extract_keywords(query, max_keywords=10)
    remote_pointers = await distributed_index.query(keywords) if keywords else []

    # 3. Convert peer pointers to RankedResult stubs
    remote_results: list[RankedResult] = []
    for ptr in remote_pointers:
        remote_results.append(
            RankedResult(
                doc_id=ptr.doc_id,
                url=ptr.url,
                title=ptr.title,
                snippet="",  # snippet unavailable from DHT pointer
                bm25_score=0.0,
                freshness_score=0.0,
                trust_score=0.0,
                authority_score=0.0,
                combined_score=ptr.score,
                crawled_at=0.0,
                peer_id=ptr.peer_id,
            )
        )

    # 4. Merge & deduplicate by URL (local results take priority)
    seen_urls: set[str] = set()
    merged: list[RankedResult] = []

    for r in local_results.results:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            merged.append(r)

    for r in remote_results:
        if r.url not in seen_urls:
            seen_urls.add(r.url)
            merged.append(r)

    # 5. Sort by combined_score descending, trim to limit
    merged.sort(key=lambda r: r.combined_score, reverse=True)
    merged = merged[:limit]

    elapsed = (time.monotonic() - start) * 1000
    local_count = local_results.total
    remote_count = len(remote_pointers)

    source = "distributed" if remote_count > 0 else "local_only"

    logger.info(
        "query_distributed",
        query=query,
        local_count=local_count,
        remote_pointers=remote_count,
        merged_count=len(merged),
        elapsed_ms=round(elapsed, 1),
    )

    return DistributedResult(
        results=merged,
        total=len(merged),
        elapsed_ms=elapsed,
        source=source,
        local_count=local_count,
        remote_count=remote_count,
    )
