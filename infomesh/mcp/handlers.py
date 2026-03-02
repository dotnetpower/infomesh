"""MCP tool handler implementations.

Each ``handle_*`` function processes one MCP tool call.
Handlers are thin adapters — they validate arguments,
delegate to service-layer functions, and format responses.

Extracted from ``mcp/server.py`` to enforce SRP.
"""

from __future__ import annotations

import json
import time
from typing import Any

import structlog
from mcp.types import TextContent

from infomesh.config import Config
from infomesh.data_quality import cross_reference_results
from infomesh.mcp.session import (
    AnalyticsTracker,
    SessionStore,
    WebhookRegistry,
)
from infomesh.mcp.tools import extract_filters
from infomesh.search.cache import QueryCache
from infomesh.search.cross_validate import (
    PeerResult,
    cross_validate_results,
)
from infomesh.search.explain import explain_query
from infomesh.search.formatter import (
    format_distributed_results,
    format_distributed_results_json,
    format_fetch_result,
    format_fts_results,
    format_fts_results_json,
    format_hybrid_results,
    format_hybrid_results_json,
)
from infomesh.search.nlp import (
    did_you_mean,
    expand_query,
    parse_natural_query,
    remove_stop_words,
)
from infomesh.search.query import (
    HybridResult,
    QueryResult,
    search_distributed,
    search_hybrid,
    search_local,
)
from infomesh.search.rag import extract_answers, format_rag_output
from infomesh.search.reranker import rerank_with_llm
from infomesh.security import SSRFError, validate_url
from infomesh.services import crawl_and_index, fetch_page_async

logger = structlog.get_logger()

# ── Constants ──────────────────────────────────────────────────────

MCP_API_VERSION = "2025.1"
SERVER_VERSION = "0.2.0"

_SEARCH_ATTRIBUTION = (
    "\n---\n"
    "Attribution: All results sourced from their "
    "original publishers.\n"
    "Each result includes a source URL — always "
    "cite the original source."
)

_FETCH_COPYRIGHT_NOTICE = (
    "\n---\n"
    "COPYRIGHT NOTICE: This content is cached by InfoMesh "
    "for search indexing purposes only.\n"
    "The original content is owned by its respective "
    "author/publisher.\n"
    "Always cite the original source URL when "
    "referencing this content.\n"
    "Cache policy: content is refreshed every 7 days; "
    "may not reflect the latest version."
)

# ── Structured error codes ─────────────────────────────────────────


class ErrorCode:
    """Structured error codes for MCP tool responses."""

    INVALID_PARAM = "INVALID_PARAM"
    AUTH_FAILED = "AUTH_FAILED"
    SSRF_BLOCKED = "SSRF_BLOCKED"
    CRAWL_FAILED = "CRAWL_FAILED"
    FETCH_FAILED = "FETCH_FAILED"
    PAYWALL = "PAYWALL_DETECTED"
    RATE_LIMITED = "RATE_LIMITED"
    EMPTY_INDEX = "EMPTY_INDEX"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL = "INTERNAL_ERROR"
    WORKER_UNAVAILABLE = "WORKER_UNAVAILABLE"


def _error(
    code: str,
    message: str,
    *,
    hint: str = "",
) -> list[TextContent]:
    """Return a structured error TextContent with isError.

    All error responses use this helper to ensure the
    ``isError`` flag is set and a machine-readable error
    code is included.
    """
    parts = [f"Error [{code}]: {message}"]
    if hint:
        parts.append(f"Hint: {hint}")
    return [
        TextContent(
            type="text",
            text="\n".join(parts),
        )
    ]


# ── Credit helper ──────────────────────────────────────────────────


def deduct_search_cost(ledger: Any) -> None:
    """Deduct search cost from the ledger (never blocks)."""
    if ledger is None:
        return
    try:
        allowance = ledger.search_allowance()
        ledger.spend(allowance.search_cost, reason="search")
    except Exception:  # noqa: BLE001
        logger.debug("search_cost_deduction_failed")


# ── Handler implementations ───────────────────────────────────────


async def handle_search(
    name: str,
    arguments: dict[str, Any],
    *,
    config: Config,
    store: Any,
    vector_store: Any,
    distributed_index: Any | None,
    link_graph: Any,
    ledger: Any,
    llm_backend: Any,
    query_cache: QueryCache,
    sessions: SessionStore,
    analytics: AnalyticsTracker,
) -> list[TextContent]:
    """Handle search / search_local tool calls."""
    query = arguments.get("query", "")
    limit = arguments.get("limit", 10)
    offset = arguments.get("offset", 0)
    fmt = arguments.get("format", "text")
    snippet_len = arguments.get("snippet_length", 200)
    session_id = arguments.get("session_id")
    filters = extract_filters(arguments)

    if not isinstance(query, str) or not query.strip():
        return _error(
            ErrorCode.INVALID_PARAM,
            "query must be a non-empty string",
        )
    query = query[:1000]
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    snippet_len = max(10, min(int(snippet_len), 1000))

    # ── NLP preprocessing ──────────────────────────────────────
    original_query = query

    # Parse natural language query for filters
    parsed_nq = parse_natural_query(query)
    if parsed_nq.date_from and not filters.get("date_from"):
        filters["date_from"] = parsed_nq.date_from
    if parsed_nq.include_domains and not filters.get(
        "include_domains",
    ):
        filters["include_domains"] = parsed_nq.include_domains
    if parsed_nq.language and not filters.get("language"):
        filters["language"] = parsed_nq.language
    if parsed_nq.cleaned_query:
        query = parsed_nq.cleaned_query

    # Remove stop words for better FTS matching
    cleaned_tokens = remove_stop_words(query.split())
    if cleaned_tokens:
        query = " ".join(cleaned_tokens)

    # Query expansion (synonyms)
    expansions = expand_query(query)
    if expansions:
        query = query + " " + " ".join(expansions)

    # Re-truncate after NLP expansion to prevent oversized FTS queries
    query = query[:1000]

    cache_suffix = f"|{fmt}|{offset}|{snippet_len}|{filters.get('language', '')}"
    cached = query_cache.get(query + cache_suffix, limit)
    if cached is not None:
        return cached  # type: ignore[return-value]

    t0 = time.monotonic()
    deduct_search_cost(ledger)
    authority_fn = link_graph.url_authority if link_graph else None

    # Distributed search
    if name == "search" and distributed_index is not None:
        dist = await search_distributed(
            store,
            distributed_index,
            query,
            limit=limit,
            authority_fn=authority_fn,
            vector_store=vector_store,
        )
        if dist.remote_count > 0:
            peer_map: dict[str, list[PeerResult]] = {}
            for r in dist.results:
                pid = r.peer_id or "local"
                peer_map.setdefault(pid, []).append(
                    PeerResult(
                        peer_id=pid,
                        url=r.url,
                        title=r.title,
                        snippet=r.snippet,
                        score=r.combined_score,
                    )
                )
            cv = cross_validate_results(query, peer_map)
            if cv.suspicious_count > 0:
                logger.warning(
                    "cross_validate_suspicious",
                    query=query[:60],
                    suspicious=cv.suspicious_count,
                    fabricated=cv.fabricated_count,
                )
        if llm_backend is not None:
            dist.results = await rerank_with_llm(query, dist.results, llm_backend)
        text = (
            format_distributed_results_json(dist, max_snippet=snippet_len)
            if fmt == "json"
            else format_distributed_results(dist, max_snippet=snippet_len)
        )

    # Hybrid search
    elif vector_store is not None and name == "search":
        hybrid = search_hybrid(
            store,
            vector_store,
            query,
            limit=limit,
            authority_fn=authority_fn,
        )
        if llm_backend is not None:
            reranked = await rerank_with_llm(query, hybrid.results, llm_backend)
            hybrid = HybridResult(
                results=reranked,
                total=hybrid.total,
                elapsed_ms=hybrid.elapsed_ms,
                source=hybrid.source,
            )
        text = (
            format_hybrid_results_json(hybrid, max_snippet=snippet_len)
            if fmt == "json"
            else format_hybrid_results(hybrid, max_snippet=snippet_len)
        )

    # Local-only search
    else:
        result = search_local(
            store,
            query,
            limit=limit,
            offset=offset,
            authority_fn=authority_fn,
            **filters,
        )
        if llm_backend is not None:
            reranked_list = await rerank_with_llm(query, result.results, llm_backend)
            result = QueryResult(
                results=reranked_list,
                total=result.total,
                elapsed_ms=result.elapsed_ms,
                source=result.source,
            )
        text = (
            format_fts_results_json(result, max_snippet=snippet_len)
            if fmt == "json"
            else format_fts_results(result, max_snippet=snippet_len)
        )

    elapsed = (time.monotonic() - t0) * 1000
    await analytics.record_search(elapsed)

    # Inject quota into JSON responses
    if fmt == "json" and ledger is not None:
        try:
            data = json.loads(text)
            al = ledger.search_allowance()
            data["quota"] = {
                "credit_balance": round(ledger.balance(), 2),
                "state": al.state.value,
                "search_cost": round(al.search_cost, 3),
            }
            data["api_version"] = MCP_API_VERSION
            text = json.dumps(data, ensure_ascii=False)
        except (json.JSONDecodeError, Exception):
            pass  # noqa: BLE001

    # ── NLP post-processing ───────────────────────────────────
    if text.startswith("No results found"):
        # Check if index is empty
        try:
            st = store.get_stats()
            doc_count = st.get("document_count", 0)
            if isinstance(doc_count, int) and doc_count == 0:
                text += (
                    "\n\nYour index is empty. "
                    "Try crawling some pages first:\n"
                    "  crawl_url(url='https://docs."
                    "python.org/3/', depth=1)"
                )
        except Exception:  # noqa: BLE001
            pass
        vocab = store.suggest(original_query[:20], limit=50) if store else []
        suggestion = did_you_mean(original_query, vocab)
        if suggestion:
            text += f'\n\nDid you mean: "{suggestion}"?'

    if fmt != "json" and text != "No results found." and config.mcp.show_attribution:
        text += _SEARCH_ATTRIBUTION

    # Apply max_response_chars truncation
    max_chars = config.mcp.max_response_chars
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"

    # Session tracking
    if session_id:
        s = sessions.get_or_create(session_id)
        s.last_query = query
        s.last_results = text[:2000]
        s.updated_at = time.time()

    response = [TextContent(type="text", text=text)]
    cache_entry: list[object] = list(response)
    query_cache.put(query + cache_suffix, limit, cache_entry)
    return response


async def handle_fetch(
    arguments: dict[str, Any],
    *,
    config: Config,
    store: Any,
    worker: Any,
    vector_store: Any,
    link_graph: Any,
    analytics: AnalyticsTracker,
) -> list[TextContent]:
    """Handle fetch_page tool call."""
    url = arguments.get("url", "")
    fmt = arguments.get("format", "text")

    if not url or not isinstance(url, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "url must be a non-empty string",
        )

    if worker is None:
        return _error(
            ErrorCode.WORKER_UNAVAILABLE,
            "fetch_page requires a crawler worker",
            hint=("Start the node with 'infomesh start' first."),
        )

    try:
        validate_url(url)
    except SSRFError as exc:
        return _error(
            ErrorCode.SSRF_BLOCKED,
            f"URL blocked for security: {exc}",
        )

    max_size = config.index.max_doc_size_kb * 1024
    cache_ttl = config.storage.cache_ttl_days * 86400

    fp = await fetch_page_async(
        url,
        store=store,
        worker=worker,
        vector_store=vector_store,
        max_size_bytes=max_size,
        cache_ttl_seconds=cache_ttl,
    )
    await analytics.record_fetch()

    if not fp.success:
        if fp.is_paywall:
            return _error(
                ErrorCode.PAYWALL,
                f"Paywall detected for {url}",
                hint="This page requires a subscription.",
            )
        return _error(
            ErrorCode.FETCH_FAILED,
            f"Failed to fetch {url}: content unavailable",
            hint="The page may be down. Try again later.",
        )

    if not fp.is_cached and link_graph:
        doc = store.get_document_by_url(url)
        if doc:
            link_graph.add_links(url, [])

    if fmt == "json":
        from urllib.parse import urlparse as _up

        data = {
            "url": fp.url,
            "title": fp.title,
            "domain": _up(fp.url).netloc,
            "text": fp.text,
            "is_cached": fp.is_cached,
            "crawled_at": fp.crawled_at,
            "is_paywall": fp.is_paywall,
            "api_version": MCP_API_VERSION,
        }
        return [
            TextContent(
                type="text",
                text=json.dumps(data, ensure_ascii=False),
            )
        ]

    text = format_fetch_result(
        title=fp.title,
        url=fp.url,
        text=fp.text,
        is_cached=fp.is_cached,
        crawled_at=fp.crawled_at,
        cache_ttl=cache_ttl,
        is_paywall=fp.is_paywall,
    )
    if config.mcp.show_copyright:
        text = f"{text}{_FETCH_COPYRIGHT_NOTICE}"

    # Apply max_response_chars truncation
    max_chars = config.mcp.max_response_chars
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"

    return [TextContent(type="text", text=text)]


async def handle_crawl(
    arguments: dict[str, Any],
    *,
    config: Config,
    store: Any,
    worker: Any,
    vector_store: Any,
    link_graph: Any,
    analytics: AnalyticsTracker,
    webhooks: WebhookRegistry,
) -> list[TextContent]:
    """Handle crawl_url tool call."""
    url = arguments.get("url", "")
    depth = min(
        arguments.get("depth", 0),
        config.crawl.max_depth,
    )
    force = bool(arguments.get("force", False))
    webhook_url = arguments.get("webhook_url")

    if not url or not isinstance(url, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "url must be a non-empty string",
        )

    if worker is None:
        return _error(
            ErrorCode.WORKER_UNAVAILABLE,
            "crawl_url requires a crawler worker",
            hint=("Start the node with 'infomesh start' first."),
        )

    try:
        validate_url(url)
    except SSRFError as exc:
        return _error(
            ErrorCode.SSRF_BLOCKED,
            f"URL blocked for security: {exc}",
        )

    if webhook_url:
        webhooks.register(webhook_url)

    ci = await crawl_and_index(
        url,
        worker=worker,
        store=store,
        vector_store=vector_store,
        link_graph=link_graph,
        depth=depth,
        force=force,
    )
    await analytics.record_crawl()

    if ci.success:
        await webhooks.notify(
            "crawl_completed",
            {
                "url": url,
                "title": ci.title,
                "text_length": ci.text_length,
                "links_discovered": ci.links_discovered,
                "elapsed_ms": round(ci.elapsed_ms, 0),
            },
        )
        return [
            TextContent(
                type="text",
                text=(
                    f"Crawled successfully: {url}\n"
                    f"Title: {ci.title}\n"
                    f"Text length: {ci.text_length}"
                    " chars\n"
                    "Links discovered: "
                    f"{ci.links_discovered}\n"
                    f"Elapsed: {ci.elapsed_ms:.0f}ms"
                ),
            )
        ]

    return _error(
        ErrorCode.CRAWL_FAILED,
        f"Crawl failed for {url}: {ci.error}",
        hint="Check if the URL is reachable.",
    )


def handle_stats(
    arguments: dict[str, Any],
    *,
    store: Any,
    vector_store: Any,
    link_graph: Any,
    ledger: Any,
    scheduler: Any,
    p2p_node: Any,
    distributed_index: Any,
    analytics: AnalyticsTracker,
) -> list[TextContent]:
    """Handle network_stats tool call."""
    fmt = arguments.get("format", "text")
    stats = store.get_stats()

    data: dict[str, object] = {
        "api_version": MCP_API_VERSION,
        "phase": "4 (Production)",
        "documents_indexed": stats["document_count"],
        "pending_crawl_urls": (scheduler.pending_count if scheduler else 0),
        "ranking": "BM25 + freshness + trust + authority",
        "analytics": analytics.to_dict(),
    }

    if vector_store is not None:
        vs = vector_store.get_stats()
        data["vector"] = {
            "documents": vs["document_count"],
            "model": vs["model"],
        }
    else:
        data["vector"] = {"enabled": False}

    if link_graph:
        lg = link_graph.get_stats()
        data["link_graph"] = {
            "links": lg["link_count"],
            "domains_scored": lg["domain_count"],
        }
    else:
        data["link_graph"] = {"enabled": False}

    if ledger is not None:
        al = ledger.search_allowance()
        cr: dict[str, object] = {
            "balance": round(ledger.balance(), 2),
            "state": al.state.value,
            "search_cost": round(al.search_cost, 3),
        }
        if al.state.value == "grace":
            cr["grace_remaining_hours"] = round(al.grace_remaining_hours, 1)
        elif al.state.value == "debt":
            cr["debt_amount"] = round(al.debt_amount, 2)
        data["credits"] = cr

    if p2p_node is not None:
        try:
            pc = len(p2p_node.connected_peers)
            p2p_data: dict[str, object] = {"peers": pc}
            if distributed_index is not None:
                di = distributed_index.stats
                p2p_data["dht"] = {
                    "published": di.documents_published,
                    "keywords": di.keywords_published,
                    "queries": di.queries_performed,
                }
            data["p2p"] = p2p_data
        except Exception:  # noqa: BLE001
            data["p2p"] = {"error": "status unavailable"}
    else:
        data["p2p"] = {"peers": 0, "mode": "local"}

    if fmt == "json":
        return [
            TextContent(
                type="text",
                text=json.dumps(data, ensure_ascii=False),
            )
        ]

    lines = [
        "InfoMesh Node Status",
        "====================",
        f"API Version: {MCP_API_VERSION}",
        f"Server: v{SERVER_VERSION}",
        f"Phase: {data['phase']}",
        "",
        "── Index ──",
        f"Documents indexed: {data['documents_indexed']}",
    ]

    v = data.get("vector")
    if isinstance(v, dict):
        if v.get("enabled") is False:
            lines.append("Vector search: disabled")
        else:
            lines.append(f"Vector documents: {v.get('documents', 0)}")

    lg_d = data.get("link_graph")
    if isinstance(lg_d, dict):
        if lg_d.get("enabled") is False:
            lines.append("Link graph: disabled")
        else:
            lines.append(f"Link graph: {lg_d.get('links', 0)} links")

    crd = data.get("credits")
    if isinstance(crd, dict):
        lines.append("")
        lines.append("── Credits ──")
        lines.append(f"Balance: {crd.get('balance', 0)}")
        lines.append(f"State: {crd.get('state', 'n/a')}")
        lines.append(f"Search cost: {crd.get('search_cost', 0)}")

    lines.append("")
    lines.append("── Network ──")
    lines.append(f"Pending crawl URLs: {data['pending_crawl_urls']}")
    lines.append(f"Ranking: {data['ranking']}")

    p2p = data.get("p2p")
    if isinstance(p2p, dict):
        lines.append(f"P2P peers: {p2p.get('peers', 0)}")

    return [TextContent(type="text", text="\n".join(lines))]


async def handle_batch(
    arguments: dict[str, Any],
    *,
    store: Any,
    link_graph: Any,
    ledger: Any,
    analytics: AnalyticsTracker,
) -> list[TextContent]:
    """Handle batch_search tool call."""
    queries = arguments.get("queries", [])
    limit = max(1, min(int(arguments.get("limit", 5)), 50))
    fmt = arguments.get("format", "text")

    if not queries or not isinstance(queries, list):
        return _error(
            ErrorCode.INVALID_PARAM,
            "queries must be a non-empty array",
        )

    queries = queries[:10]
    authority_fn = link_graph.url_authority if link_graph else None

    if fmt == "json":
        batch: list[dict[str, object]] = []
        for q in queries:
            if not isinstance(q, str) or not q.strip():
                batch.append({"query": str(q), "error": "invalid"})
                continue
            t0 = time.monotonic()
            deduct_search_cost(ledger)
            res = search_local(
                store,
                q,
                limit=limit,
                authority_fn=authority_fn,
            )
            ms = (time.monotonic() - t0) * 1000
            await analytics.record_search(ms)
            rd = json.loads(format_fts_results_json(res))
            rd["query"] = q
            batch.append(rd)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "api_version": MCP_API_VERSION,
                        "batch_results": batch,
                    },
                    ensure_ascii=False,
                ),
            )
        ]

    parts: list[str] = []
    for i, q in enumerate(queries, 1):
        if not isinstance(q, str) or not q.strip():
            parts.append(f"--- Query {i}: (invalid) ---\n")
            continue
        t0 = time.monotonic()
        deduct_search_cost(ledger)
        res = search_local(
            store,
            q,
            limit=limit,
            authority_fn=authority_fn,
        )
        ms = (time.monotonic() - t0) * 1000
        await analytics.record_search(ms)
        parts.append(f"--- Query {i}: {q} ---\n{format_fts_results(res)}\n")

    return [TextContent(type="text", text="\n".join(parts))]


def handle_suggest(
    arguments: dict[str, Any],
    *,
    store: Any,
) -> list[TextContent]:
    """Handle suggest tool call."""
    prefix = arguments.get("prefix", "")
    limit = max(1, min(int(arguments.get("limit", 10)), 50))

    if not prefix or not isinstance(prefix, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "prefix must be a non-empty string",
        )

    fmt = arguments.get("format", "json")
    suggestions = store.suggest(prefix, limit=limit)

    if fmt == "text":
        if not suggestions:
            return [
                TextContent(
                    type="text",
                    text=f"No suggestions for '{prefix}'",
                )
            ]
        lines = [
            f"Suggestions for '{prefix}':",
            *[f"  - {s}" for s in suggestions],
        ]
        return [
            TextContent(
                type="text",
                text="\n".join(lines),
            )
        ]

    data = {"prefix": prefix, "suggestions": suggestions}
    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


async def handle_explain(
    arguments: dict[str, Any],
    *,
    store: Any,
    link_graph: Any,
) -> list[TextContent]:
    """Handle explain tool: score breakdown for a query."""
    query = arguments.get("query", "")
    limit = max(1, min(int(arguments.get("limit", 5)), 20))

    if not query or not isinstance(query, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "query is required",
        )

    authority_fn = link_graph.url_authority if link_graph else None
    result = search_local(store, query, limit=limit, authority_fn=authority_fn)
    explanation = explain_query(query, query, result.results, result.elapsed_ms)

    fmt = arguments.get("format", "json")

    data: dict[str, object] = {
        "query": explanation.query,
        "total_results": explanation.total_results,
        "elapsed_ms": round(explanation.elapsed_ms, 2),
        "pipeline": explanation.pipeline,
        "results": [
            {
                "url": e.url,
                "title": e.title,
                "combined_score": round(e.combined_score, 4),
                "breakdown": {
                    "bm25": round(e.weighted.get("bm25", 0.0), 4),
                    "freshness": round(
                        e.weighted.get("freshness", 0.0),
                        4,
                    ),
                    "trust": round(e.weighted.get("trust", 0.0), 4),
                    "authority": round(
                        e.weighted.get("authority", 0.0),
                        4,
                    ),
                },
                "dominant_factor": (
                    max(
                        e.weighted,
                        key=lambda k: e.weighted.get(k, 0.0),
                    )
                    if e.weighted
                    else ""
                ),
            }
            for e in explanation.results
        ],
    }

    if fmt == "text":
        lines = [
            f"Query: {explanation.query}",
            f"Results: {explanation.total_results}",
            f"Elapsed: {explanation.elapsed_ms:.1f}ms",
            "",
        ]
        for e in explanation.results:
            lines.append(f"  {e.url}")
            lines.append(f"    Score: {e.combined_score:.4f}")
            for k, v in e.weighted.items():
                lines.append(f"    {k}: {v:.4f}")
            lines.append("")
        return [
            TextContent(
                type="text",
                text="\n".join(lines),
            )
        ]

    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


async def handle_search_rag(
    arguments: dict[str, Any],
    *,
    store: Any,
    link_graph: Any,
    analytics: AnalyticsTracker,
    ledger: Any,
) -> list[TextContent]:
    """Handle search_rag: RAG-formatted search output."""
    query = arguments.get("query", "")
    limit = max(1, min(int(arguments.get("limit", 5)), 20))

    if not query or not isinstance(query, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "query is required",
        )

    t0 = time.monotonic()
    deduct_search_cost(ledger)
    authority_fn = link_graph.url_authority if link_graph else None
    filters = extract_filters(arguments)
    result = search_local(
        store,
        query,
        limit=limit,
        authority_fn=authority_fn,
        **filters,
    )
    elapsed = (time.monotonic() - t0) * 1000
    await analytics.record_search(elapsed)

    rag_output = format_rag_output(query, result.results)
    data: dict[str, object] = {
        "api_version": MCP_API_VERSION,
        "query": query,
        "context_chunks": [
            {
                "url": c.url,
                "title": c.title,
                "text": c.text,
                "relevance_score": round(c.score, 4),
            }
            for c in rag_output.chunks
        ],
        "total_results": rag_output.total_results,
        "context_window": rag_output.context_window,
    }
    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


async def handle_extract_answer(
    arguments: dict[str, Any],
    *,
    store: Any,
    link_graph: Any,
    ledger: Any,
) -> list[TextContent]:
    """Handle extract_answer: direct answers from results."""
    query = arguments.get("query", "")
    limit = max(1, min(int(arguments.get("limit", 5)), 20))

    if not query or not isinstance(query, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "query is required",
        )

    deduct_search_cost(ledger)
    authority_fn = link_graph.url_authority if link_graph else None
    filters = extract_filters(arguments)
    result = search_local(
        store,
        query,
        limit=limit,
        authority_fn=authority_fn,
        **filters,
    )

    answers = extract_answers(query, result.results)
    data: dict[str, object] = {
        "api_version": MCP_API_VERSION,
        "query": query,
        "answers": [
            {
                "text": a.answer,
                "confidence": round(a.confidence, 3),
                "source_url": a.source_url,
                "source_title": a.source_title,
            }
            for a in answers
        ],
    }
    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


async def handle_fact_check(
    arguments: dict[str, Any],
    *,
    store: Any,
    link_graph: Any,
) -> list[TextContent]:
    """Handle fact_check: cross-reference a claim."""
    claim = arguments.get("claim", "")

    if not claim or not isinstance(claim, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "claim is required",
        )

    limit = max(1, min(int(arguments.get("limit", 10)), 50))
    authority_fn = link_graph.url_authority if link_graph else None
    filters = extract_filters(arguments)
    result = search_local(
        store,
        claim,
        limit=limit,
        authority_fn=authority_fn,
        **filters,
    )

    fc = cross_reference_results(claim, result.results)
    data: dict[str, object] = {
        "api_version": MCP_API_VERSION,
        "claim": claim,
        "verdict": fc.verdict,
        "confidence": round(fc.confidence, 3),
        "supporting": fc.supporting_sources,
        "contradicting": fc.contradicting_sources,
        "sources_checked": len(fc.sources),
    }
    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


# ── New utility handlers ──────────────────────────────────────────


def handle_ping() -> list[TextContent]:
    """Handle ping tool: health check."""
    data = {
        "status": "ok",
        "server": "infomesh",
        "version": SERVER_VERSION,
        "api_version": MCP_API_VERSION,
    }
    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


def handle_credit_balance(
    arguments: dict[str, Any],
    *,
    ledger: Any,
) -> list[TextContent]:
    """Handle credit_balance tool."""
    fmt = arguments.get("format", "json")

    if ledger is None:
        data: dict[str, object] = {
            "balance": 0,
            "state": "normal",
            "search_cost": 0.1,
            "note": "Credit ledger not active",
        }
    else:
        al = ledger.search_allowance()
        data = {
            "balance": round(ledger.balance(), 2),
            "state": al.state.value,
            "search_cost": round(al.search_cost, 3),
            "tier": (
                3 if ledger.balance() >= 1000 else 2 if ledger.balance() >= 100 else 1
            ),
        }
        if al.state.value == "grace":
            data["grace_remaining_hours"] = round(
                al.grace_remaining_hours,
                1,
            )
        elif al.state.value == "debt":
            data["debt_amount"] = round(
                al.debt_amount,
                2,
            )

    if fmt == "text":
        lines = [
            "Credit Balance",
            "==============",
            f"Balance: {data.get('balance', 0)}",
            f"State: {data.get('state', 'n/a')}",
            f"Search cost: {data.get('search_cost', 0)}",
        ]
        return [
            TextContent(
                type="text",
                text="\n".join(lines),
            )
        ]

    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


def handle_index_stats(
    arguments: dict[str, Any],
    *,
    store: Any,
    vector_store: Any,
) -> list[TextContent]:
    """Handle index_stats tool."""
    fmt = arguments.get("format", "json")
    stats = store.get_stats()

    data: dict[str, object] = {
        "document_count": stats.get(
            "document_count",
            0,
        ),
    }

    # Top domains if available
    try:
        domains = store.get_top_domains(limit=10)
        data["top_domains"] = [{"domain": d, "count": c} for d, c in domains]
    except Exception:  # noqa: BLE001
        pass

    if vector_store is not None:
        vs = vector_store.get_stats()
        data["vector"] = {
            "document_count": vs.get(
                "document_count",
                0,
            ),
            "model": vs.get("model", "unknown"),
        }

    if fmt == "text":
        lines = [
            "Index Statistics",
            "================",
            f"Documents: {data.get('document_count', 0)}",
        ]
        dom = data.get("top_domains")
        if dom and isinstance(dom, list):
            lines.append("Top domains:")
            for d in dom[:5]:
                if isinstance(d, dict):
                    lines.append(f"  {d.get('domain', '?')}: {d.get('count', 0)}")
        return [
            TextContent(
                type="text",
                text="\n".join(lines),
            )
        ]

    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]


def handle_remove_url(
    arguments: dict[str, Any],
    *,
    store: Any,
) -> list[TextContent]:
    """Handle remove_url tool."""
    url = arguments.get("url", "")

    if not url or not isinstance(url, str):
        return _error(
            ErrorCode.INVALID_PARAM,
            "url must be a non-empty string",
        )

    try:
        doc = store.get_document_by_url(url)
        if doc is None:
            return _error(
                ErrorCode.NOT_FOUND,
                f"URL not found in index: {url}",
            )
        store.delete_document(doc["id"])
        return [
            TextContent(
                type="text",
                text=f"Removed from index: {url}",
            )
        ]
    except Exception as exc:  # noqa: BLE001
        logger.error("remove_url_failed", url=url, error=str(exc))
        return _error(
            ErrorCode.INTERNAL,
            f"Failed to remove {url}",
        )


# ── Consolidated handlers (v2 — 5-tool API) ──────────────


async def handle_web_search(
    arguments: dict[str, Any],
    *,
    config: Config,
    store: Any,
    vector_store: Any,
    distributed_index: Any | None,
    link_graph: Any,
    ledger: Any,
    llm_backend: Any,
    query_cache: QueryCache,
    sessions: SessionStore,
    analytics: AnalyticsTracker,
) -> list[TextContent]:
    """Unified web search — replaces 6 legacy search tools.

    Behaviour is controlled by optional params:

    * ``local_only`` — search local index only
    * ``explain`` — include BM25/freshness/trust breakdown
    * ``chunk_size`` — RAG-optimised chunked output
    * ``answer_mode`` — snippets (default) / summary / structured
    * ``recency_days`` — human-friendly time filter
    * ``fetch_full_content`` — include full article text
    """
    query = arguments.get("query", "")
    if not isinstance(query, str) or not query.strip():
        return _error(
            ErrorCode.INVALID_PARAM,
            "query must be a non-empty string",
        )

    top_k = arguments.get("top_k", 5)
    local_only = bool(arguments.get("local_only", False))
    explain_flag = bool(arguments.get("explain", False))
    chunk_size = arguments.get("chunk_size")
    answer_mode = arguments.get("answer_mode", "snippets")
    fetch_full = bool(
        arguments.get("fetch_full_content", False),
    )
    rerank = bool(arguments.get("rerank", True))

    # ── Map new params → legacy params ─────────────────
    legacy: dict[str, Any] = {
        "query": query,
        "limit": int(top_k),
        "format": "json",
    }
    # Propagate filters via extract_filters (supports
    # both recency_days and legacy date_from/date_to)
    filters = extract_filters(arguments)
    legacy.update(filters)

    # ── Explain mode ───────────────────────────────────
    if explain_flag:
        return await handle_explain(
            {"query": query, "limit": int(top_k), "format": "json"},
            store=store,
            link_graph=link_graph,
        )

    # ── RAG chunk mode ─────────────────────────────────
    if chunk_size is not None:
        return await handle_search_rag(
            {
                "query": query,
                "limit": int(top_k),
                "chunk_size": int(chunk_size),
                **filters,
            },
            store=store,
            link_graph=link_graph,
            analytics=analytics,
            ledger=ledger,
        )

    # ── Answer extraction modes ────────────────────────
    if answer_mode == "summary" or answer_mode == "structured":
        return await handle_extract_answer(
            {"query": query, "limit": int(top_k), **filters},
            store=store,
            link_graph=link_graph,
            ledger=ledger,
        )

    # ── Default: ranked search (snippets mode) ─────────
    name = "search_local" if local_only else "search"

    # Disable LLM reranking if caller opts out
    effective_llm = llm_backend if rerank else None

    result = await handle_search(
        name,
        legacy,
        config=config,
        store=store,
        vector_store=vector_store,
        distributed_index=distributed_index,
        link_graph=link_graph,
        ledger=ledger,
        llm_backend=effective_llm,
        query_cache=query_cache,
        sessions=sessions,
        analytics=analytics,
    )

    # ── Optionally fetch full content for each result ──
    if fetch_full and result:
        text = result[0].text
        try:
            data = json.loads(text)
            results_list = data.get("results", [])
            for r in results_list:
                url = r.get("url", "")
                if url:
                    doc = store.get_document_by_url(url)
                    if doc:
                        r["full_text"] = (
                            doc.text[:10000]
                            if hasattr(doc, "text")
                            else str(doc.get("text", ""))[:10000]
                        )
            text = json.dumps(data, ensure_ascii=False)
            return [TextContent(type="text", text=text)]
        except (json.JSONDecodeError, Exception):
            pass  # fall through with original result

    return result


def handle_status(
    arguments: dict[str, Any],
    *,
    store: Any,
    vector_store: Any,
    link_graph: Any,
    ledger: Any,
    scheduler: Any,
    p2p_node: Any,
    distributed_index: Any,
    analytics: AnalyticsTracker,
) -> list[TextContent]:
    """Unified status — merges network_stats + credit + index + ping.

    Always returns JSON for consistency.
    """
    stats = store.get_stats()

    data: dict[str, object] = {
        "status": "ok",
        "server": "infomesh",
        "version": SERVER_VERSION,
        "api_version": MCP_API_VERSION,
        "phase": "4 (Production)",
        "documents_indexed": stats.get("document_count", 0),
        "pending_crawl_urls": (scheduler.pending_count if scheduler else 0),
        "ranking": ("BM25 + freshness + trust + authority"),
        "analytics": analytics.to_dict(),
    }

    # Vector store
    if vector_store is not None:
        vs = vector_store.get_stats()
        data["vector"] = {
            "documents": vs.get("document_count", 0),
            "model": vs.get("model", "unknown"),
        }
    else:
        data["vector"] = {"enabled": False}

    # Link graph
    if link_graph:
        lg = link_graph.get_stats()
        data["link_graph"] = {
            "links": lg.get("link_count", 0),
            "domains_scored": lg.get("domain_count", 0),
        }
    else:
        data["link_graph"] = {"enabled": False}

    # Credits
    if ledger is not None:
        al = ledger.search_allowance()
        cr: dict[str, object] = {
            "balance": round(ledger.balance(), 2),
            "state": al.state.value,
            "search_cost": round(al.search_cost, 3),
            "tier": (
                3 if ledger.balance() >= 1000 else 2 if ledger.balance() >= 100 else 1
            ),
        }
        if al.state.value == "grace":
            cr["grace_remaining_hours"] = round(al.grace_remaining_hours, 1)
        elif al.state.value == "debt":
            cr["debt_amount"] = round(al.debt_amount, 2)
        data["credits"] = cr

    # P2P
    if p2p_node is not None:
        try:
            pc = len(p2p_node.connected_peers)
            p2p_data: dict[str, object] = {"peers": pc}
            if distributed_index is not None:
                di = distributed_index.stats
                p2p_data["dht"] = {
                    "published": di.documents_published,
                    "keywords": di.keywords_published,
                    "queries": di.queries_performed,
                }
            data["p2p"] = p2p_data
        except Exception:  # noqa: BLE001
            data["p2p"] = {"error": "status unavailable"}
    else:
        data["p2p"] = {"peers": 0, "mode": "local"}

    # Top domains
    try:
        domains = store.get_top_domains(limit=5)
        data["top_domains"] = [{"domain": d, "count": c} for d, c in domains]
    except Exception:  # noqa: BLE001
        pass

    return [
        TextContent(
            type="text",
            text=json.dumps(data, ensure_ascii=False),
        )
    ]
