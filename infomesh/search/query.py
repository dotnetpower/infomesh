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
from math import isfinite
from typing import TYPE_CHECKING, Any

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

    results: list[Any]  # list[MergedResult]
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
        # All meaningful characters were removed — strip to
        # alphanumeric only to prevent FTS5 syntax errors.
        fallback = re.sub(r"[^a-zA-Z0-9\s]", " ", query)
        fallback = re.sub(r"\s+", " ", fallback).strip()[:100]
        return fallback or "infomesh"

    return sanitized


def search_local(
    store: LocalStore,
    query: str,
    *,
    limit: int = 10,
    offset: int = 0,
    authority_fn: Callable[[str], float] | None = None,
    language: str | None = None,
    date_from: float | None = None,
    date_to: float | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> QueryResult:
    """Search the local FTS5 index with full ranking.

    Applies BM25 + freshness + trust + domain authority ranking
    to FTS5 results.

    Args:
        store: Local document store.
        query: User search query.
        limit: Maximum results.
        offset: Skip this many results (pagination).
        authority_fn: Optional ``(url) -> float`` for domain authority lookup.
        language: Filter by ISO language code.
        date_from: Unix timestamp — only include newer docs.
        date_to: Unix timestamp — only include older docs.
        include_domains: Restrict to these domains.
        exclude_domains: Exclude these domains.

    Returns:
        QueryResult with ranked search results.
    """
    from infomesh.search.cjk import tokenize_query_cjk
    from infomesh.search.nlp import expand_query
    from infomesh.search.passage import _tokenize

    start = time.monotonic()

    # CJK-aware query preprocessing (generates bigrams for CJK chars)
    preprocessed = tokenize_query_cjk(query)
    sanitized = _sanitize_fts_query(preprocessed)
    raw_results = store.search(
        sanitized,
        limit=limit * 2,
        offset=offset,
        language=language,
        date_from=date_from,
        date_to=date_to,
        include_domains=include_domains,
        exclude_domains=exclude_domains,
    )

    # Query expansion: if initial results are sparse, broaden search
    if len(raw_results) < limit:
        expansions = expand_query(query, max_expansions=3)
        seen_urls = {r.url for r in raw_results}
        for term in expansions:
            term_q = _sanitize_fts_query(term)
            if not term_q or term_q == "infomesh":
                continue
            extra = store.search(
                term_q,
                limit=limit,
                offset=0,
                language=language,
                date_from=date_from,
                date_to=date_to,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
            for r in extra:
                if r.url not in seen_urls:
                    raw_results.append(r)
                    seen_urls.add(r.url)

    # Extract query tokens for title/URL ranking signals
    query_tokens = _tokenize(query)

    # Apply full ranking pipeline (with title/URL bonuses)
    ranked = rank_local_results(
        raw_results,
        authority_fn=authority_fn,
        query_tokens=query_tokens,
        limit=limit,
    )

    # Passage selection: re-select snippets from full text for top results
    if ranked:
        _enhance_snippets(store, ranked, query)

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


def _enhance_snippets(
    store: LocalStore,
    results: list[RankedResult],
    query: str,
    *,
    max_enhance: int = 10,
) -> None:
    """Replace FTS5 snippets with passage-selected snippets.

    Loads full document text for the top results and selects the
    most relevant passage as the snippet.  Mutates the list in-place
    by replacing RankedResult objects (frozen dataclass → new instance).

    Args:
        store: Local document store (for full-text retrieval).
        results: Ranked results list (modified in-place).
        query: Original user query.
        max_enhance: Maximum results to enhance (to limit I/O).
    """
    from infomesh.search.passage import _tokenize, select_best_passage

    query_tokens = set(_tokenize(query))

    for i, r in enumerate(results[:max_enhance]):
        snippet_tokens = set(_tokenize(r.snippet))
        if len(r.snippet) >= 80 and query_tokens & snippet_tokens:
            continue

        doc = store.get_document(
            int(r.doc_id) if isinstance(r.doc_id, str) else r.doc_id,
        )
        if doc is None or not doc.text:
            continue

        passage = select_best_passage(doc.text, query, max_length=300)
        if passage and len(passage) > len(r.snippet):
            # Replace with better passage snippet
            results[i] = RankedResult(
                doc_id=r.doc_id,
                url=r.url,
                title=r.title,
                snippet=passage,
                bm25_score=r.bm25_score,
                freshness_score=r.freshness_score,
                trust_score=r.trust_score,
                authority_score=r.authority_score,
                combined_score=r.combined_score,
                crawled_at=r.crawled_at,
                peer_id=r.peer_id,
                title_match_score=r.title_match_score,
                url_path_score=r.url_path_score,
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


def _make_remote_result(
    *,
    url: str,
    title: str,
    snippet: str,
    score: object,
    doc_id: object,
    peer_id: str,
) -> RankedResult:
    """Build a ``RankedResult`` from remote search data.

    Shared by the QueryRouter (network_search_fn) path and the
    DHT pointer-stub fallback to avoid duplicated construction.
    """
    return RankedResult(
        doc_id=_safe_remote_int(doc_id),
        url=url,
        title=title,
        snippet=snippet,
        bm25_score=0.0,
        freshness_score=0.0,
        trust_score=0.0,
        authority_score=0.0,
        combined_score=_safe_remote_float(score),
        crawled_at=0.0,
        peer_id=peer_id,
    )


def _safe_remote_int(value: object, *, default: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = float(value) if isinstance(value, str) else value
        if not isfinite(float(parsed)):
            return default
        return int(parsed)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_remote_float(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if isfinite(parsed) else default


async def search_distributed(
    store: LocalStore,
    distributed_index: DistributedIndex,
    query: str,
    *,
    limit: int = 10,
    authority_fn: Callable[[str], float] | None = None,
    vector_store: VectorStoreLike | None = None,
    network_search_fn: (Callable[[str, list[str], int], Any] | None) = None,
) -> DistributedResult:
    """Search local index + P2P network, merge results.

    1. Run local FTS5 search.
    2. Extract keywords from query.
    3. If ``network_search_fn`` is provided (asyncio-safe bridge to
       the P2P QueryRouter), fan out SEARCH_REQUEST to peers and
       collect real search results with snippets and scores.
       Otherwise, fall back to DHT-only peer pointer stubs.
    4. Merge & deduplicate by URL, keeping best score.

    Args:
        store: Local FTS5 document store.
        distributed_index: DHT-backed distributed index.
        query: User search query.
        limit: Maximum results.
        authority_fn: Optional ``(url) -> float`` for domain authority.
        vector_store: Optional vector store for hybrid local search.
        network_search_fn: Optional asyncio-safe callable
            ``(query, keywords, limit) -> list[dict]`` that fans out
            search requests to peers via the P2P QueryRouter.
            Each dict has: url, title, snippet, score, peer_id, doc_id.

    Returns:
        DistributedResult with merged local + remote results.
    """
    from infomesh.index.distributed import extract_keywords

    start = time.monotonic()

    # 1. Local search
    query = _sanitize_fts_query(query)
    local_results = search_local(
        store,
        query,
        limit=limit,
        authority_fn=authority_fn,
    )

    # 2. Extract keywords
    keywords = extract_keywords(query, max_keywords=10)

    # 3. Fetch remote results — prefer QueryRouter (real content)
    remote_results: list[RankedResult] = []
    remote_count = 0

    if keywords and network_search_fn is not None:
        # Use P2P QueryRouter: sends SEARCH_REQUEST to peers,
        # returns results with actual snippets and scores.
        try:
            raw_results = await network_search_fn(
                query,
                keywords,
                limit,
            )
            for r in raw_results:
                if not isinstance(r, dict):
                    continue
                url = str(r.get("url", ""))
                if not url:
                    continue
                remote_results.append(
                    _make_remote_result(
                        url=url,
                        title=str(r.get("title", "")),
                        snippet=str(r.get("snippet", "")),
                        score=r.get("score", 0.0),
                        doc_id=r.get("doc_id", 0),
                        peer_id=str(r.get("peer_id", "")),
                    )
                )
            remote_count = len(remote_results)
        except Exception:
            logger.exception("network_search_failed")
    elif keywords:
        # Fallback: DHT pointer stubs (no snippets, metadata only)
        try:
            remote_pointers = await distributed_index.query(
                keywords,
            )
            for ptr in remote_pointers:
                remote_results.append(
                    _make_remote_result(
                        url=ptr.url,
                        title=ptr.title,
                        snippet="",
                        score=ptr.score,
                        doc_id=ptr.doc_id,
                        peer_id=ptr.peer_id,
                    )
                )
            remote_count = len(remote_pointers)
        except Exception:
            logger.exception("dht_query_failed")

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

    source = "distributed" if remote_count > 0 else "local_only"

    logger.info(
        "query_distributed",
        query=query,
        local_count=local_count,
        remote_count=remote_count,
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
