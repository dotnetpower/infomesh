"""InfoMesh MCP server — exposes search tools to LLMs via MCP protocol.

Supports both stdio and HTTP (Streamable) transports.

This module is the thin wiring layer that:
1. Creates the ``Server`` instance and ``AppContext``.
2. Registers tool schemas from ``mcp.tools``.
3. Dispatches tool calls to handlers in ``mcp.handlers``.
4. Provides server runner functions (stdio / HTTP).

Business logic lives in ``mcp.handlers``; tool definitions in
``mcp.tools``; session/analytics helpers in ``mcp.session``.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from infomesh.config import Config, load_config
from infomesh.mcp.handlers import (
    deduct_search_cost,
    handle_batch,
    handle_crawl,
    handle_credit_balance,
    handle_explain,
    handle_extract_answer,
    handle_fact_check,
    handle_fetch,
    handle_index_stats,
    handle_ping,
    handle_remove_url,
    handle_search,
    handle_search_rag,
    handle_stats,
    handle_suggest,
)
from infomesh.mcp.session import (
    AnalyticsTracker,
    SearchSession,
    SessionStore,
    WebhookRegistry,
)
from infomesh.mcp.tools import (
    check_api_key,
    extract_filters,
    get_all_tools,
)
from infomesh.persistence.store import PersistentStore
from infomesh.search.cache import QueryCache
from infomesh.services import AppContext

logger = structlog.get_logger()

# Backward-compatible aliases for external consumers
_SearchSession = SearchSession
_AnalyticsTracker = AnalyticsTracker
_WebhookRegistry = WebhookRegistry
_check_api_key = check_api_key
_extract_filters = extract_filters
_deduct_search_cost = deduct_search_cost


def _create_app(
    config: Config,
    distributed_index: Any | None = None,
    p2p_node: Any | None = None,
    *,
    api_key: str | None = None,
) -> tuple[Server, AppContext, PersistentStore]:
    """Create and configure the MCP server with all tools.

    Args:
        config: Application configuration.
        distributed_index: Optional DistributedIndex for DHT.
        p2p_node: Optional P2P Node for network stats.
        api_key: Optional API key for authentication.

    Returns the ``(Server, AppContext, PersistentStore)`` tuple.
    """
    app = Server("infomesh")
    api_key_required = api_key is not None

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

    cache_size = getattr(getattr(config, "search", None), "cache_max_size", 1000)
    cache_ttl = getattr(getattr(config, "search", None), "cache_ttl_seconds", 300.0)
    query_cache = QueryCache(
        max_size=int(cache_size),
        ttl_seconds=float(cache_ttl),
    )
    llm_backend = ctx.llm_backend
    sessions = SessionStore()
    analytics = AnalyticsTracker()
    webhooks = WebhookRegistry()
    pstore = PersistentStore(
        str(config.node.data_dir / "persistent.db"),
    )

    @app.list_tools()  # type: ignore[no-untyped-call, untyped-decorator]
    async def list_tools() -> list[Tool]:
        return get_all_tools(
            api_key_required=api_key_required,
        )

    @app.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(
        name: str,
        arguments: dict[str, Any],
    ) -> list[TextContent]:
        auth_err = check_api_key(arguments, api_key)
        if auth_err is not None:
            return [TextContent(type="text", text=auth_err)]

        match name:
            case "search" | "search_local":
                return await handle_search(
                    name,
                    arguments,
                    config=config,
                    store=store,
                    vector_store=vector_store,
                    distributed_index=distributed_index,
                    link_graph=link_graph,
                    ledger=ledger,
                    llm_backend=llm_backend,
                    query_cache=query_cache,
                    sessions=sessions,
                    analytics=analytics,
                )
            case "fetch_page":
                return await handle_fetch(
                    arguments,
                    config=config,
                    store=store,
                    worker=worker,
                    vector_store=vector_store,
                    link_graph=link_graph,
                    analytics=analytics,
                )
            case "crawl_url":
                return await handle_crawl(
                    arguments,
                    config=config,
                    store=store,
                    worker=worker,
                    vector_store=vector_store,
                    link_graph=link_graph,
                    analytics=analytics,
                    webhooks=webhooks,
                )
            case "network_stats":
                return handle_stats(
                    arguments,
                    store=store,
                    vector_store=vector_store,
                    link_graph=link_graph,
                    ledger=ledger,
                    scheduler=scheduler,
                    p2p_node=p2p_node,
                    distributed_index=distributed_index,
                    analytics=analytics,
                )
            case "batch_search":
                return await handle_batch(
                    arguments,
                    store=store,
                    link_graph=link_graph,
                    ledger=ledger,
                    analytics=analytics,
                )
            case "suggest":
                return handle_suggest(
                    arguments,
                    store=store,
                )
            case "register_webhook":
                url = arguments.get("url", "")
                if not url:
                    return [
                        TextContent(
                            type="text",
                            text=("Error [INVALID_PARAM]: url required"),
                        )
                    ]
                reg_err = webhooks.register(url)
                if reg_err is not None:
                    return [
                        TextContent(
                            type="text",
                            text=f"Error: {reg_err}",
                        )
                    ]
                return [
                    TextContent(
                        type="text",
                        text=f"Webhook registered: {url}",
                    )
                ]
            case "unregister_webhook":
                url = arguments.get("url", "")
                if not url:
                    return [
                        TextContent(
                            type="text",
                            text=("Error [INVALID_PARAM]: url required"),
                        )
                    ]
                removed = webhooks.unregister(url)
                msg = (
                    f"Webhook removed: {url}"
                    if removed
                    else f"Webhook not found: {url}"
                )
                return [
                    TextContent(
                        type="text",
                        text=msg,
                    )
                ]
            case "analytics":
                fmt = arguments.get("format", "json")
                data = analytics.to_dict()
                pstore.record_search(0.0)
                if fmt == "json":
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(data),
                        )
                    ]
                lines = [
                    "Search Analytics",
                    "================",
                    (f"Total searches: {data['total_searches']}"),
                    f"Total crawls: {data['total_crawls']}",
                    (f"Total fetches: {data['total_fetches']}"),
                    (f"Avg latency: {data['avg_latency_ms']}ms"),
                ]
                return [
                    TextContent(
                        type="text",
                        text="\n".join(lines),
                    )
                ]
            case "explain":
                return await handle_explain(
                    arguments,
                    store=store,
                    link_graph=link_graph,
                )
            case "search_history":
                action = arguments.get(
                    "action",
                    "list",
                )
                if action == "clear":
                    cleared = pstore.clear_history()
                    return [
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "cleared": cleared,
                                    "message": (f"Cleared {cleared} history entries"),
                                },
                                ensure_ascii=False,
                            ),
                        )
                    ]
                limit = min(
                    int(arguments.get("limit", 20)),
                    100,
                )
                history = pstore.get_history(
                    limit=limit,
                )
                return [
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"history": history},
                            ensure_ascii=False,
                        ),
                    )
                ]
            case "search_rag":
                return await handle_search_rag(
                    arguments,
                    store=store,
                    link_graph=link_graph,
                    analytics=analytics,
                    ledger=ledger,
                )
            case "extract_answer":
                return await handle_extract_answer(
                    arguments,
                    store=store,
                    link_graph=link_graph,
                    ledger=ledger,
                )
            case "fact_check":
                return await handle_fact_check(
                    arguments,
                    store=store,
                    link_graph=link_graph,
                )
            case "ping":
                return handle_ping()
            case "credit_balance":
                return handle_credit_balance(
                    arguments,
                    ledger=ledger,
                )
            case "index_stats":
                return handle_index_stats(
                    arguments,
                    store=store,
                    vector_store=vector_store,
                )
            case "remove_url":
                return handle_remove_url(
                    arguments,
                    store=store,
                )
            case _:
                return [
                    TextContent(
                        type="text",
                        text=f"Unknown tool: {name}",
                    )
                ]

    return app, ctx, pstore


# ── Server runners ─────────────────────────────────────────────────


def _env_api_key() -> str | None:
    """Read optional API key from environment."""
    return os.environ.get("INFOMESH_API_KEY")


async def run_mcp_server(
    config: Config | None = None,
) -> None:
    """Run the MCP server on stdio transport.

    Args:
        config: Configuration. Loads default if None.
    """
    if config is None:
        config = load_config()

    app, ctx, pstore = _create_app(config, api_key=_env_api_key())
    logger.info("mcp_server_starting", transport="stdio")

    async with ctx, stdio_server() as (rs, ws):
        try:
            await app.run(rs, ws, app.create_initialization_options())
        finally:
            pstore.close()


async def run_mcp_http_server(
    config: Config | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8081,
) -> None:
    """Run the MCP server on HTTP Streamable transport.

    Enables remote agents and containers to connect
    over HTTP instead of stdio.

    Args:
        config: Configuration. Loads default if None.
        host: Bind address (default: localhost only).
        port: HTTP port (default: 8081).
    """
    if config is None:
        config = load_config()

    app, ctx, pstore = _create_app(config, api_key=_env_api_key())
    logger.info(
        "mcp_server_starting",
        transport="http",
        host=host,
        port=port,
    )

    try:
        from mcp.server.streamable_http import (
            StreamableHTTPServerTransport,
        )
    except ImportError:
        logger.warning(
            "streamable_http_unavailable",
            detail=("mcp HTTP deps missing. Install with: uv add starlette uvicorn"),
        )
        raise

    transport = StreamableHTTPServerTransport(
        mcp_session_id=None,
    )

    _CORS_HEADERS: list[list[bytes]] = [
        [b"access-control-allow-origin", b"*"],
        [
            b"access-control-allow-methods",
            b"GET, POST, OPTIONS",
        ],
        [
            b"access-control-allow-headers",
            b"content-type, authorization",
        ],
    ]

    async def _asgi_app(
        scope: dict[str, object],
        receive: object,
        send: object,
    ) -> None:
        """Minimal ASGI app with CORS + routing."""
        if scope.get("type") == "http":
            method = scope.get("method", "")
            path = scope.get("path", "")

            # CORS preflight
            if method == "OPTIONS":
                await send(  # type: ignore[operator]
                    {
                        "type": "http.response.start",
                        "status": 204,
                        "headers": _CORS_HEADERS,
                    }
                )
                await send(  # type: ignore[operator]
                    {
                        "type": "http.response.body",
                        "body": b"",
                    }
                )
                return

            if path == "/health":
                import json as _json

                body = _json.dumps(
                    {"status": "ok"},
                ).encode()
                await send(  # type: ignore[operator]
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            [
                                b"content-type",
                                b"application/json",
                            ],
                            *_CORS_HEADERS,
                        ],
                    }
                )
                await send(  # type: ignore[operator]
                    {
                        "type": "http.response.body",
                        "body": body,
                    }
                )
                return
            if path == "/mcp":
                await transport.handle_request(
                    scope,
                    receive,  # type: ignore[arg-type]
                    send,  # type: ignore[arg-type]
                )
                return
        # 404 for other paths
        if scope.get("type") == "http":
            await send(  # type: ignore[operator]
                {
                    "type": "http.response.start",
                    "status": 404,
                    "headers": _CORS_HEADERS,
                }
            )
            await send(  # type: ignore[operator]
                {
                    "type": "http.response.body",
                    "body": b"Not Found",
                }
            )

    import uvicorn

    uvi_config = uvicorn.Config(
        _asgi_app,
        host=host,
        port=port,
        log_level="info",
    )
    uvi_server = uvicorn.Server(uvi_config)

    async with ctx, transport.connect() as (rs, ws):
        task = asyncio.create_task(
            app.run(
                rs,
                ws,
                app.create_initialization_options(),
            )
        )
        try:
            await uvi_server.serve()
        finally:
            task.cancel()
            pstore.close()
