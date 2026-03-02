"""MCP tool schema definitions — consolidated for LLM usability.

Provides **5 focused tools** instead of 18 scattered ones:

1. ``web_search``   — unified search (snippets/summary/structured + RAG)
2. ``fetch_page``   — retrieve full text of a specific URL
3. ``crawl_url``    — submit a URL for crawling & indexing
4. ``fact_check``   — cross-reference a claim against indexed sources
5. ``status``       — node status, credits, index stats (combined)

Design principles:

- One tool per *intent* — LLMs pick the right tool more easily.
- ``required: ["query"]`` only — everything else optional with sane defaults.
- ``recency_days`` instead of Unix timestamps — natural for LLMs.
- ``answer_mode`` enum replaces 3 separate search tools.
- ``fetch_full_content`` merges fetch behaviour into search.
"""

from __future__ import annotations

import hmac
import time as _time
from typing import Any

from mcp.types import Tool, ToolAnnotations


def get_all_tools(
    *,
    api_key_required: bool = False,
) -> list[Tool]:
    """Return all MCP tool definitions.

    Args:
        api_key_required: When True, adds ``api_key``
            property to every tool schema.
    """
    tools = [
        # ── 1. web_search ────────────────────────────────
        Tool(
            name="web_search",
            description=(
                "Search the web via InfoMesh P2P "
                "search engine. Returns ranked "
                "results with optional full content, "
                "RAG chunking, and score explanations."
                " Example: web_search("
                "query='python asyncio tutorial')"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": ("Search query string"),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": ("Number of results to return"),
                        "default": 5,
                    },
                    "recency_days": {
                        "type": "integer",
                        "description": (
                            "Filter results published within the last N days"
                        ),
                    },
                    "domain_allowlist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("Only include results from these domains"),
                    },
                    "domain_blocklist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("Exclude results from these domains"),
                    },
                    "language": {
                        "type": "string",
                        "description": (
                            "ISO 639-1 language code (e.g. 'en', 'ko', 'ja')"
                        ),
                    },
                    "fetch_full_content": {
                        "type": "boolean",
                        "description": (
                            "Fetch and return full article text for each result"
                        ),
                        "default": False,
                    },
                    "chunk_size": {
                        "type": "integer",
                        "description": (
                            "Chunk size for RAG context "
                            "splitting. When set, returns "
                            "source-attributed chunks."
                        ),
                    },
                    "rerank": {
                        "type": "boolean",
                        "description": ("Apply semantic re-ranking via local LLM"),
                        "default": True,
                    },
                    "answer_mode": {
                        "type": "string",
                        "enum": [
                            "snippets",
                            "summary",
                            "structured",
                        ],
                        "description": (
                            "Response mode: 'snippets' "
                            "(ranked results), 'summary' "
                            "(answer extraction), "
                            "'structured' (JSON with "
                            "scores and metadata)"
                        ),
                        "default": "snippets",
                    },
                    "local_only": {
                        "type": "boolean",
                        "description": ("Search local index only (offline, <10ms)"),
                        "default": False,
                    },
                    "explain": {
                        "type": "boolean",
                        "description": (
                            "Include score breakdown "
                            "(BM25, freshness, trust, "
                            "authority) per result"
                        ),
                        "default": False,
                    },
                },
                "required": ["query"],
            },
            annotations=ToolAnnotations(
                title="Web Search",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        # ── 2. fetch_page ────────────────────────────────
        Tool(
            name="fetch_page",
            description=(
                "Fetch full text of a specific URL. "
                "Returns cached content or crawls "
                "live. Max 100KB. "
                "Example: fetch_page("
                "url='https://...')"
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
            annotations=ToolAnnotations(
                title="Fetch Page",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        # ── 3. crawl_url ────────────────────────────────
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
                        "description": (
                            "Link-follow depth "
                            "(0=this page only). "
                            "Stays within same domain."
                        ),
                        "default": 0,
                    },
                    "force": {
                        "type": "boolean",
                        "description": (
                            "Force re-crawl even if "
                            "previously crawled "
                            "(bypasses all dedup checks)"
                        ),
                        "default": False,
                    },
                },
                "required": ["url"],
            },
            annotations=ToolAnnotations(
                title="Crawl URL",
                readOnlyHint=False,
            ),
        ),
        # ── 4. fact_check ────────────────────────────────
        Tool(
            name="fact_check",
            description=(
                "Cross-reference a claim against "
                "indexed web content. Returns verdict "
                "with supporting/contradicting "
                "sources. Example: fact_check("
                "claim='Python was created in 1991')"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "claim": {
                        "type": "string",
                        "description": ("Claim to verify"),
                    },
                    "top_k": {
                        "type": "integer",
                        "description": ("Max sources to check"),
                        "default": 10,
                    },
                },
                "required": ["claim"],
            },
            annotations=ToolAnnotations(
                title="Fact Check",
                readOnlyHint=True,
                openWorldHint=True,
            ),
        ),
        # ── 5. status ────────────────────────────────────
        Tool(
            name="status",
            description=(
                "Node status: index size, peer count, "
                "credit balance, search quota, and "
                "analytics. Example: status()"
            ),
            inputSchema={
                "type": "object",
                "properties": {},
            },
            annotations=ToolAnnotations(
                title="Node Status",
                readOnlyHint=True,
                idempotentHint=True,
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
    """Extract common search filter params from arguments.

    Supports both legacy (``date_from``/``include_domains``) and
    new (``recency_days``/``domain_allowlist``) parameter names.
    """
    filters: dict[str, Any] = {}

    # Language
    lang = args.get("language")
    if lang and isinstance(lang, str):
        filters["language"] = lang

    # Time — new: recency_days, legacy: date_from/date_to
    recency = args.get("recency_days")
    if recency is not None:
        try:
            days = int(recency)
            if days > 0:
                filters["date_from"] = _time.time() - days * 86400
        except (ValueError, TypeError):
            pass
    else:
        df = args.get("date_from")
        if df is not None:
            filters["date_from"] = float(df)
    dt = args.get("date_to")
    if dt is not None:
        filters["date_to"] = float(dt)

    # Domains — new: allowlist/blocklist, legacy: include/exclude
    inc = args.get("domain_allowlist") or args.get(
        "include_domains",
    )
    if inc and isinstance(inc, list):
        filters["include_domains"] = inc
    exc = args.get("domain_blocklist") or args.get(
        "exclude_domains",
    )
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
    if not isinstance(provided, str) or not hmac.compare_digest(provided, expected_key):
        return "Error: invalid or missing api_key"
    return None
