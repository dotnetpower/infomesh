"""MCP tool schema definitions.

Contains all MCP tool schemas (JSON Schema ``inputSchema``) returned
by ``list_tools()``.  Extracted from ``mcp/server.py`` so tool
definitions can be reviewed independently of handler logic.
"""

from __future__ import annotations

from typing import Any

from mcp.types import Tool, ToolAnnotations

# ── Shared filter property definitions ─────────────────────

FILTER_PROPS: dict[str, dict[str, object]] = {
    "language": {
        "type": "string",
        "description": ("ISO 639-1 language code to filter (e.g. 'en', 'ko', 'ja')"),
    },
    "date_from": {
        "type": "number",
        "description": ("Unix timestamp — only documents crawled after this time"),
    },
    "date_to": {
        "type": "number",
        "description": ("Unix timestamp — only documents crawled before this time"),
    },
    "include_domains": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Only include results from these domains",
    },
    "exclude_domains": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Exclude results from these domains",
    },
    "offset": {
        "type": "integer",
        "description": "Results to skip (pagination)",
        "default": 0,
    },
    "format": {
        "type": "string",
        "enum": ["text", "json"],
        "description": ("Output format: 'text' (default) or 'json'"),
        "default": "text",
    },
    "snippet_length": {
        "type": "integer",
        "description": ("Max snippet chars (default 200, max 1000)"),
        "default": 200,
    },
    "session_id": {
        "type": "string",
        "description": ("Session ID for conversational refinement"),
    },
}


def build_search_props() -> dict[str, object]:
    """Return the shared search input properties."""
    return {
        "query": {
            "type": "string",
            "description": "Search query text",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default: 10)",
            "default": 10,
        },
        **FILTER_PROPS,
    }


def get_all_tools(
    *,
    api_key_required: bool = False,
) -> list[Tool]:
    """Return all MCP tool definitions.

    Args:
        api_key_required: When True, adds ``api_key``
            property to every tool schema.
    """
    search_props = build_search_props()
    search_props["max_response_chars"] = {
        "type": "integer",
        "description": ("Max total response characters. Truncates output if exceeded."),
    }

    _sec_filters: dict[str, dict[str, object]] = {
        k: v
        for k, v in FILTER_PROPS.items()
        if k
        in (
            "language",
            "date_from",
            "date_to",
            "include_domains",
            "exclude_domains",
        )
    }

    tools = [
        Tool(
            name="search",
            description=(
                "Search the InfoMesh P2P network. "
                "Merges local + remote peer results "
                "with BM25 + freshness + trust ranking."
                " Example: search(query='python "
                "asyncio', limit=5, language='en')"
            ),
            inputSchema={
                "type": "object",
                "properties": search_props,
                "required": ["query"],
            },
            annotations=ToolAnnotations(
                title="Web Search",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        Tool(
            name="search_local",
            description=(
                "Search local index only (offline, "
                "<10ms). All search filters supported."
                " Example: search_local("
                "query='rust ownership', limit=3)"
            ),
            inputSchema={
                "type": "object",
                "properties": search_props,
                "required": ["query"],
            },
            annotations=ToolAnnotations(
                title="Local Search",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="fetch_page",
            description=(
                "Fetch full text of a URL. Returns "
                "cached content or crawls live. "
                "Max 100KB. Example: "
                "fetch_page(url='https://...')"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": (
                            "Output format: 'text' "
                            "(human-readable) or 'json' "
                            "(structured with metadata)"
                        ),
                        "default": "text",
                    },
                },
                "required": ["url"],
            },
            annotations=ToolAnnotations(
                title="Fetch Page",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        Tool(
            name="crawl_url",
            description=(
                "Add a URL to the crawl queue and "
                "index it. Rate limited to 60/hour. "
                "Example: crawl_url("
                "url='https://example.com', depth=1)"
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
                        "description": ("Link-follow depth (0=this page only, max: 3)"),
                        "default": 0,
                    },
                    "force": {
                        "type": "boolean",
                        "description": ("Force re-crawl even if previously crawled"),
                        "default": False,
                    },
                    "webhook_url": {
                        "type": "string",
                        "description": ("URL to POST when crawl completes"),
                    },
                },
                "required": ["url"],
            },
            annotations=ToolAnnotations(
                title="Crawl URL",
                readOnlyHint=False,
            ),
        ),
        Tool(
            name="network_stats",
            description=(
                "Node status: index size, peers, "
                "credits, quota, analytics. "
                "Example: network_stats(format='json')"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "text",
                    },
                },
            },
            annotations=ToolAnnotations(
                title="Network Stats",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="batch_search",
            description=(
                "Run multiple search queries in one "
                "call (max 10). More efficient than "
                "sequential search calls. "
                "Example: batch_search(queries="
                "['python', 'rust'], limit=3)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("Search queries (max 10)"),
                    },
                    "limit": {
                        "type": "integer",
                        "description": ("Max results per query (default: 5)"),
                        "default": 5,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "text",
                    },
                    **_sec_filters,
                },
                "required": ["queries"],
            },
            annotations=ToolAnnotations(
                title="Batch Search",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        Tool(
            name="suggest",
            description=(
                "Get search suggestions / autocomplete"
                " for a partial query. "
                "Example: suggest(prefix='pyth')"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "Partial query",
                    },
                    "limit": {
                        "type": "integer",
                        "description": ("Max suggestions (default: 10)"),
                        "default": 10,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": ("Output format (default: json)"),
                        "default": "json",
                    },
                },
                "required": ["prefix"],
            },
            annotations=ToolAnnotations(
                title="Suggest",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="register_webhook",
            description=(
                "Register a webhook URL for crawl completion notifications. Max 20."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": ("Webhook URL to POST to"),
                    },
                },
                "required": ["url"],
            },
            annotations=ToolAnnotations(
                title="Register Webhook",
                readOnlyHint=False,
            ),
        ),
        Tool(
            name="unregister_webhook",
            description=("Remove a previously registered webhook URL."),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": ("Webhook URL to remove"),
                    },
                },
                "required": ["url"],
            },
            annotations=ToolAnnotations(
                title="Unregister Webhook",
                readOnlyHint=False,
                destructiveHint=True,
            ),
        ),
        Tool(
            name="analytics",
            description=(
                "Search analytics: total searches, crawls, fetches, avg latency."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "json",
                    },
                },
            },
            annotations=ToolAnnotations(
                title="Analytics",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="explain",
            description=(
                "Explain search ranking for a query. "
                "Shows BM25, freshness, trust, "
                "authority score breakdown. "
                "Example: explain(query='ML', "
                "limit=3)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "description": ("Output format (default: json)"),
                        "default": "json",
                    },
                },
                "required": ["query"],
            },
            annotations=ToolAnnotations(
                title="Explain Ranking",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="search_history",
            description=(
                "View or clear search history. "
                "Example: search_history("
                "action='list', limit=10)"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "clear"],
                        "description": (
                            "'list' to view history, 'clear' to delete all"
                        ),
                        "default": "list",
                    },
                    "limit": {
                        "type": "integer",
                        "description": ("Max entries (default: 20, list only)"),
                        "default": 20,
                    },
                },
            },
            annotations=ToolAnnotations(
                title="Search History",
                readOnlyHint=False,
            ),
        ),
        Tool(
            name="search_rag",
            description=(
                "Search with RAG-optimized chunked "
                "output for LLM context injection. "
                "Returns source-attributed chunks."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 5,
                    },
                    "chunk_size": {
                        "type": "integer",
                        "default": 500,
                        "description": (
                            "Max characters per context "
                            "chunk. Smaller = more chunks "
                            "with finer granularity."
                        ),
                    },
                    **_sec_filters,
                },
                "required": ["query"],
            },
            annotations=ToolAnnotations(
                title="RAG Search",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        Tool(
            name="extract_answer",
            description=(
                "Extract direct answers from search "
                "results. Returns answers with "
                "confidence scores and source URLs."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": ("Question to answer"),
                    },
                    "limit": {
                        "type": "integer",
                        "default": 3,
                    },
                    **_sec_filters,
                },
                "required": ["query"],
            },
            annotations=ToolAnnotations(
                title="Extract Answer",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        Tool(
            name="fact_check",
            description=(
                "Cross-reference a claim against "
                "indexed content. Returns verdict "
                "with supporting/contradicting "
                "sources."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "Claim to verify",
                    },
                    "limit": {
                        "type": "integer",
                        "description": ("Max sources to check (default: 10)"),
                        "default": 10,
                    },
                    **_sec_filters,
                },
                "required": ["claim"],
            },
            annotations=ToolAnnotations(
                title="Fact Check",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        # ── Utility tools ──────────────────────────────
        Tool(
            name="ping",
            description=(
                "Health check. Returns server status "
                "and version. Use to verify the MCP "
                "connection is working."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
            annotations=ToolAnnotations(
                title="Ping",
                readOnlyHint=True,
                idempotentHint=True,
            ),
        ),
        Tool(
            name="credit_balance",
            description=(
                "View current credit balance, tier, "
                "search cost, and credit state "
                "(normal/grace/debt)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "json",
                    },
                },
            },
            annotations=ToolAnnotations(
                title="Credit Balance",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="index_stats",
            description=(
                "Detailed index statistics: document count, top domains, storage size."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "enum": ["text", "json"],
                        "default": "json",
                    },
                },
            },
            annotations=ToolAnnotations(
                title="Index Stats",
                readOnlyHint=True,
            ),
        ),
        Tool(
            name="remove_url",
            description=(
                "Remove a URL from the local index. Does not affect other peers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": ("URL to remove from index"),
                    },
                },
                "required": ["url"],
            },
            annotations=ToolAnnotations(
                title="Remove URL",
                readOnlyHint=False,
                destructiveHint=True,
            ),
        ),
    ]

    if api_key_required:
        _api_key: dict[str, object] = {
            "type": "string",
            "description": ("API key (required when INFOMESH_API_KEY is set)"),
        }
        for tool in tools:
            props = tool.inputSchema.get("properties", {})
            if isinstance(props, dict):
                props["api_key"] = _api_key

    return tools


def extract_filters(args: dict[str, Any]) -> dict[str, Any]:
    """Extract common search filter params from arguments."""
    filters: dict[str, Any] = {}
    lang = args.get("language")
    if lang and isinstance(lang, str):
        filters["language"] = lang
    df = args.get("date_from")
    if df is not None:
        filters["date_from"] = float(df)
    dt = args.get("date_to")
    if dt is not None:
        filters["date_to"] = float(dt)
    inc = args.get("include_domains")
    if inc and isinstance(inc, list):
        filters["include_domains"] = inc
    exc = args.get("exclude_domains")
    if exc and isinstance(exc, list):
        filters["exclude_domains"] = exc
    return filters


def check_api_key(
    arguments: dict[str, Any],
    expected_key: str | None,
) -> str | None:
    """Validate optional API key. Returns error or None.

    Uses ``.get()`` to avoid mutating the caller's dict.
    """
    if expected_key is None:
        return None
    provided = arguments.get("api_key")
    if provided != expected_key:
        return "Error: invalid or missing api_key"
    return None
