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
        return [
            TextContent(
                type="text",
                text="Error: query must be a non-empty string",
            )
        ]
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
        vocab = store.suggest(original_query[:20], limit=50) if store else []
        suggestion = did_you_mean(original_query, vocab)
        if suggestion:
            text += f'\n\nDid you mean: "{suggestion}"?'

    if fmt != "json" and text != "No results found.":
        text += _SEARCH_ATTRIBUTION

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
        return [
            TextContent(
                type="text",
                text="Error: url must be a non-empty string",
            )
        ]

    if worker is None:
        return [
            TextContent(
                type="text",
                text="Error: fetch_page requires a crawler worker.",
            )
        ]

    try:
        validate_url(url)
    except SSRFError as exc:
        return [
            TextContent(
                type="text",
                text=(f"Error: URL blocked for security reasons: {exc}"),
            )
        ]

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
            return [
                TextContent(
                    type="text",
                    text=(f"Paywall detected for {url}: {fp.error}. Cannot retrieve."),
                )
            ]
        return [
            TextContent(
                type="text",
                text=(f"Failed to fetch {url}: content unavailable"),
            )
        ]

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
    return [
        TextContent(
            type="text",
            text=f"{text}{_FETCH_COPYRIGHT_NOTICE}",
        )
    ]


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
        return [
            TextContent(
                type="text",
                text="Error: url must be a non-empty string",
            )
        ]

    if worker is None:
        return [
            TextContent(
                type="text",
                text="Error: crawl_url requires a crawler worker.",
            )
        ]

    try:
        validate_url(url)
    except SSRFError as exc:
        return [
            TextContent(
                type="text",
                text=(f"Error: URL blocked for security reasons: {exc}"),
            )
        ]

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

    return [
        TextContent(
            type="text",
            text=f"Crawl failed for {url}: {ci.error}",
        )
    ]


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
        f"Phase: {data['phase']}",
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
        lines.append(f"Credit balance: {crd.get('balance', 0)}")
        lines.append(f"Credit state: {crd.get('state', 'n/a')}")
        lines.append(f"Search cost: {crd.get('search_cost', 0)}")

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
        return [
            TextContent(
                type="text",
                text="Error: queries must be a non-empty array",
            )
        ]

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
                    {"batch_results": batch},
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
        return [
            TextContent(
                type="text",
                text="Error: prefix must be a non-empty string",
            )
        ]

    suggestions = store.suggest(prefix, limit=limit)
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
        return [
            TextContent(
                type="text",
                text="Error: query is required",
            )
        ]

    authority_fn = link_graph.url_authority if link_graph else None
    result = search_local(store, query, limit=limit, authority_fn=authority_fn)
    explanation = explain_query(query, query, result.results, result.elapsed_ms)

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
        return [
            TextContent(
                type="text",
                text="Error: query is required",
            )
        ]

    t0 = time.monotonic()
    deduct_search_cost(ledger)
    authority_fn = link_graph.url_authority if link_graph else None
    result = search_local(store, query, limit=limit, authority_fn=authority_fn)
    elapsed = (time.monotonic() - t0) * 1000
    await analytics.record_search(elapsed)

    rag_output = format_rag_output(query, result.results)
    data: dict[str, object] = {
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
        return [
            TextContent(
                type="text",
                text="Error: query is required",
            )
        ]

    deduct_search_cost(ledger)
    authority_fn = link_graph.url_authority if link_graph else None
    result = search_local(store, query, limit=limit, authority_fn=authority_fn)

    answers = extract_answers(query, result.results)
    data: dict[str, object] = {
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
        return [
            TextContent(
                type="text",
                text="Error: claim is required",
            )
        ]

    authority_fn = link_graph.url_authority if link_graph else None
    result = search_local(store, claim, limit=10, authority_fn=authority_fn)

    fc = cross_reference_results(claim, result.results)
    data: dict[str, object] = {
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
