"""MCP tool schema definitions.

Contains all MCP tool schemas (JSON Schema ``inputSchema``) returned
by ``list_tools()``.  Extracted from ``mcp/server.py`` so tool
definitions can be reviewed independently of handler logic.
"""

from __future__ import annotations

from typing import Any

from mcp.types import Tool

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


def get_all_tools() -> list[Tool]:
    """Return all MCP tool definitions."""
    search_props = build_search_props()

    return [
        Tool(
            name="search",
            description=(
                "Search the InfoMesh P2P network. "
                "Supports language, date, domain "
                "filtering, pagination, and JSON output."
            ),
            inputSchema={
                "type": "object",
                "properties": search_props,
                "required": ["query"],
            },
        ),
        Tool(
            name="search_local",
            description=(
                "Search local index only (works offline). All search filters supported."
            ),
            inputSchema={
                "type": "object",
                "properties": search_props,
                "required": ["query"],
            },
        ),
        Tool(
            name="fetch_page",
            description=(
                "Fetch full text of a URL. Returns "
                "cached content or crawls live. "
                "Max 100KB."
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
                        "default": "text",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="crawl_url",
            description=("Add a URL to the crawl queue. Rate limited to 60 URLs/hour."),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to crawl",
                    },
                    "depth": {
                        "type": "integer",
                        "description": ("Link-follow depth (default: 0, max: 3)"),
                        "default": 0,
                    },
                    "force": {
                        "type": "boolean",
                        "description": ("Force re-crawl if previously crawled."),
                        "default": False,
                    },
                    "webhook_url": {
                        "type": "string",
                        "description": ("URL to POST when crawl completes"),
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="network_stats",
            description=("Node status: index size, peers, credits, quota."),
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
        ),
        Tool(
            name="batch_search",
            description=(
                "Run multiple search queries in one "
                "call. More efficient than sequential "
                "search calls."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Search queries (max 10)",
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
                },
                "required": ["queries"],
            },
        ),
        Tool(
            name="suggest",
            description=("Get search suggestions / autocomplete for a partial query."),
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
                },
                "required": ["prefix"],
            },
        ),
        Tool(
            name="register_webhook",
            description=("Register a webhook URL for crawl completion notifications."),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Webhook URL to POST to",
                    },
                },
                "required": ["url"],
            },
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
        ),
        Tool(
            name="explain",
            description=(
                "Explain how search ranking works for a query. Shows score breakdown."
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
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="search_history",
            description="View recent search history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "default": 20,
                    },
                },
            },
        ),
        Tool(
            name="search_rag",
            description=("Search with RAG-optimized output for LLM context injection."),
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
                        "description": "Max chars per chunk",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="extract_answer",
            description=("Extract direct answers from search results for a question."),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Question",
                    },
                    "limit": {
                        "type": "integer",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="fact_check",
            description=("Cross-reference a claim against indexed content."),
            inputSchema={
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": "Claim to verify",
                    },
                },
                "required": ["claim"],
            },
        ),
    ]


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
