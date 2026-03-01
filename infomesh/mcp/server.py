"""InfoMesh MCP server — exposes search tools to LLMs via MCP protocol."""

from __future__ import annotations

from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from infomesh.config import Config, load_config
from infomesh.search.formatter import (
    format_distributed_results,
    format_fetch_result,
    format_fts_results,
    format_hybrid_results,
)
from infomesh.search.query import search_distributed, search_hybrid, search_local
from infomesh.security import SSRFError, validate_url
from infomesh.services import (
    AppContext,
    crawl_and_index,
    fetch_page_async,
)

logger = structlog.get_logger()

# ── Attribution & copyright notices ────────────────────────────────

_SEARCH_ATTRIBUTION = (
    "\n---\n"
    "Attribution: All results sourced from their original publishers.\n"
    "Each result includes a source URL — always cite the original source."
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


def _create_app(
    config: Config,
    distributed_index: Any | None = None,
    p2p_node: Any | None = None,
) -> tuple[Server, AppContext]:
    """Create and configure the MCP server with all tools.

    Args:
        config: Application configuration.
        distributed_index: Optional DistributedIndex for DHT search.
        p2p_node: Optional P2P Node for network stats.

    Returns the ``(Server, AppContext)`` pair so the caller can manage
    the context lifecycle.
    """
    app = Server("infomesh")

    # Initialize all components via service layer
    ctx = AppContext(config)
    try:
        store = ctx.store
        vector_store = ctx.vector_store
        worker = ctx.worker
        scheduler = ctx.scheduler
        link_graph = ctx.link_graph
        ledger = ctx.ledger
    except Exception:
        ctx.close()
        raise

    @app.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search",
                description=(
                    "Search the InfoMesh P2P network for web content. "
                    "Returns relevant text snippets from crawled pages. "
                    "Searches local index and, when available, the "
                    "distributed DHT index across peers."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query text",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 10)",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="search_local",
                description=(
                    "Search only the local index (works offline). "
                    "Returns relevant text snippets from locally crawled pages."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query text",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results (default: 10)",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="fetch_page",
                description=(
                    "Fetch the full text content of a URL. "
                    "Returns cached content if available, otherwise crawls live. "
                    "Max 100KB per response."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to fetch",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="crawl_url",
                description=(
                    "Add a URL to the crawl queue. The page will be crawled, "
                    "indexed, and made searchable. Rate limited to 60 URLs/hour."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to crawl",
                        },
                        "depth": {
                            "type": "integer",
                            "description": (
                                "How many levels of links to follow "
                                "(default: 0, max: 3)"
                            ),
                            "default": 0,
                        },
                        "force": {
                            "type": "boolean",
                            "description": (
                                "Force re-crawl even if the URL "
                                "was previously crawled. Useful "
                                "for refreshing content or "
                                "discovering new child links."
                            ),
                            "default": False,
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="network_stats",
                description=(
                    "Get InfoMesh network status: index size, peer count, credits."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @app.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        match name:
            case "search" | "search_local":
                query = arguments["query"]
                limit = arguments.get("limit", 10)

                # Input validation
                if not isinstance(query, str) or not query.strip():
                    return [
                        TextContent(
                            type="text", text="Error: query must be a non-empty string"
                        )
                    ]
                query = query[:1000]  # Cap query length
                limit = max(1, min(int(limit), 100))  # Cap limit

                # Deduct search cost (debt-aware — never blocks)
                _deduct_search_cost(ledger)

                # Authority lookup function from link graph
                authority_fn = link_graph.url_authority if link_graph else None

                # Distributed search (network-wide) when available
                if name == "search" and distributed_index is not None:
                    dist_result = await search_distributed(
                        store,
                        distributed_index,
                        query,
                        limit=limit,
                        authority_fn=authority_fn,
                        vector_store=vector_store,
                    )
                    text = format_distributed_results(dist_result)
                # Use hybrid search when vector store is available
                elif vector_store is not None and name == "search":
                    hybrid = search_hybrid(
                        store,
                        vector_store,
                        query,
                        limit=limit,
                        authority_fn=authority_fn,
                    )
                    text = format_hybrid_results(hybrid)
                else:
                    result = search_local(
                        store,
                        query,
                        limit=limit,
                        authority_fn=authority_fn,
                    )
                    text = format_fts_results(result)

                if text != "No results found.":
                    text += _SEARCH_ATTRIBUTION
                return [TextContent(type="text", text=text)]

            case "fetch_page":
                url = arguments["url"]

                if worker is None:
                    return [
                        TextContent(
                            type="text",
                            text=(
                                "Error: fetch_page requires"
                                " a crawler worker."
                                " This node is not configured"
                                " for crawling."
                            ),
                        )
                    ]

                # SSRF protection
                try:
                    validate_url(url)
                except SSRFError as exc:
                    return [
                        TextContent(
                            type="text",
                            text=f"Error: URL blocked for security reasons: {exc}",
                        )
                    ]

                max_size = config.index.max_doc_size_kb * 1024
                cache_ttl = config.storage.cache_ttl_days * 86400

                fp = await fetch_page_async(
                    url,
                    store=store,
                    worker=worker,  # type: ignore[arg-type]
                    vector_store=vector_store,
                    max_size_bytes=max_size,
                    cache_ttl_seconds=cache_ttl,
                )

                if not fp.success:
                    if fp.is_paywall:
                        return [
                            TextContent(
                                type="text",
                                text=(
                                    f"Paywall detected for {url}:"
                                    f" {fp.error}."
                                    " Cannot retrieve content."
                                ),
                            )
                        ]
                    return [
                        TextContent(
                            type="text",
                            text=f"Failed to fetch {url}: content unavailable",
                        )
                    ]

                # Update link graph if we just crawled
                if not fp.is_cached and link_graph:
                    doc = store.get_document_by_url(url)
                    if doc:
                        link_graph.add_links(url, [])

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

            case "crawl_url":
                url = arguments["url"]
                depth = min(arguments.get("depth", 0), config.crawl.max_depth)
                force = bool(arguments.get("force", False))

                if worker is None:
                    return [
                        TextContent(
                            type="text",
                            text=(
                                "Error: crawl_url requires"
                                " a crawler worker."
                                " This node is not configured"
                                " for crawling."
                            ),
                        )
                    ]

                # SSRF protection
                try:
                    validate_url(url)
                except SSRFError as exc:
                    return [
                        TextContent(
                            type="text",
                            text=f"Error: URL blocked for security reasons: {exc}",
                        )
                    ]

                ci = await crawl_and_index(
                    url,
                    worker=worker,  # type: ignore[arg-type]
                    store=store,
                    vector_store=vector_store,
                    link_graph=link_graph,
                    depth=depth,
                    force=force,
                )
                if ci.success:
                    return [
                        TextContent(
                            type="text",
                            text=(
                                f"Crawled successfully: {url}\n"
                                f"Title: {ci.title}\n"
                                f"Text length: {ci.text_length} chars\n"
                                f"Links discovered: {ci.links_discovered}\n"
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

            case "network_stats":
                stats = store.get_stats()
                vec_info = ""
                if vector_store is not None:
                    vec_stats = vector_store.get_stats()
                    vec_info = (
                        f"Vector documents: {vec_stats['document_count']}\n"
                        f"Embedding model: {vec_stats['model']}\n"
                    )
                else:
                    vec_info = "Vector search: disabled\n"

                link_info = ""
                if link_graph:
                    lg_stats = link_graph.get_stats()
                    link_info = (
                        f"Link graph: {lg_stats['link_count']} links, "
                        f"{lg_stats['domain_count']} domains scored\n"
                    )
                else:
                    link_info = "Link graph: disabled\n"

                # Credit & debt info
                credit_info = ""
                if ledger is not None:
                    allowance = ledger.search_allowance()
                    credit_info = (
                        f"Credit balance: {ledger.balance():.2f}\n"
                        f"Credit state: {allowance.state.value}\n"
                        f"Search cost: {allowance.search_cost:.3f}\n"
                    )
                    if allowance.state.value == "grace":
                        credit_info += (
                            f"Grace remaining: {allowance.grace_remaining_hours:.1f}h\n"
                        )
                    elif allowance.state.value == "debt":
                        credit_info += f"Debt amount: {allowance.debt_amount:.2f}\n"

                # P2P network info
                p2p_info = ""
                if p2p_node is not None:
                    try:
                        peer_count = len(p2p_node.connected_peers)
                        p2p_info = f"P2P peers: {peer_count}\n"
                        if distributed_index is not None:
                            di_stats = distributed_index.stats
                            p2p_info += (
                                "DHT documents published:"
                                f" {di_stats.documents_published}\n"
                                "DHT keywords published:"
                                f" {di_stats.keywords_published}\n"
                                "DHT queries performed:"
                                f" {di_stats.queries_performed}\n"
                            )
                    except Exception:  # noqa: BLE001
                        p2p_info = "P2P peers: error reading status\n"
                else:
                    p2p_info = "P2P peers: 0 (local mode)\n"

                return [
                    TextContent(
                        type="text",
                        text=(
                            f"InfoMesh Node Status\n"
                            f"====================\n"
                            f"Phase: 4 (Production)\n"
                            f"Documents indexed: {stats['document_count']}\n"
                            f"{vec_info}"
                            f"{link_info}"
                            f"{credit_info}"
                            f"Pending crawl URLs: "
                            f"{scheduler.pending_count if scheduler else 0}\n"
                            f"Ranking: BM25 + freshness + trust + authority\n"
                            f"{p2p_info}"
                        ),
                    )
                ]

            case _:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return app, ctx


def _deduct_search_cost(ledger: Any) -> None:
    """Deduct search cost from the credit ledger (debt-aware).

    Never blocks — if balance is negative, the node enters grace/debt mode.
    During debt mode the cost is doubled, but search is always allowed.
    """
    if ledger is None:
        return
    try:
        allowance = ledger.search_allowance()
        ledger.spend(allowance.search_cost, reason="search")
    except Exception:  # noqa: BLE001
        logger.debug("search_cost_deduction_failed")


async def run_mcp_server(config: Config | None = None) -> None:
    """Run the MCP server on stdio.

    Args:
        config: Configuration. Loads default if None.
    """
    if config is None:
        config = load_config()

    app, ctx = _create_app(config)
    logger.info("mcp_server_starting")

    async with ctx, stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
