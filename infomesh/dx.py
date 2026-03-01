"""Developer experience utilities — plugin system, tokenizer, changelog.

Features:
- #59: Migration guide
- #60: Plugin system
- #61: Custom tokenizer hook
- #63: MCP tool guide generator
- #64: Changelog automation
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

logger = structlog.get_logger()


# ── #60: Plugin system ───────────────────────────────────────────


class PluginProtocol(Protocol):
    """Protocol for InfoMesh plugins."""

    name: str

    def setup(self, app: Any) -> None:
        """Initialize the plugin with the app context."""
        ...

    def teardown(self) -> None:
        """Clean up plugin resources."""
        ...


@dataclass
class PluginInfo:
    """Metadata about a loaded plugin."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    module_path: str = ""
    enabled: bool = True


class PluginManager:
    """Plugin manager for InfoMesh extensions.

    Loads plugins from Python modules or entry points.

    Usage::

        pm = PluginManager()
        pm.register(my_plugin)
        pm.setup_all(app)
    """

    def __init__(self) -> None:
        self._plugins: dict[str, PluginProtocol] = {}
        self._info: dict[str, PluginInfo] = {}

    def register(
        self,
        plugin: PluginProtocol,
        *,
        info: PluginInfo | None = None,
    ) -> None:
        """Register a plugin.

        Args:
            plugin: Plugin instance implementing PluginProtocol.
            info: Optional metadata.
        """
        name = plugin.name
        self._plugins[name] = plugin
        self._info[name] = info or PluginInfo(name=name)
        logger.info("plugin_registered", name=name)

    def load_module(self, module_path: str) -> None:
        """Load a plugin from a Python module path.

        The module must define a ``plugin`` variable
        implementing PluginProtocol.

        Args:
            module_path: Dotted Python module path.
        """
        try:
            mod = importlib.import_module(module_path)
            plugin = getattr(mod, "plugin", None)
            if plugin is None:
                logger.warning(
                    "plugin_missing_plugin_var",
                    module=module_path,
                )
                return
            self.register(
                plugin,
                info=PluginInfo(
                    name=plugin.name,
                    module_path=module_path,
                ),
            )
        except Exception as exc:
            logger.error(
                "plugin_load_error",
                module=module_path,
                error=str(exc),
            )

    def setup_all(self, app: Any) -> None:
        """Initialize all registered plugins.

        Args:
            app: Application context to pass to plugins.
        """
        for name, plugin in self._plugins.items():
            try:
                plugin.setup(app)
                logger.info("plugin_setup_ok", name=name)
            except Exception as exc:
                logger.error(
                    "plugin_setup_error",
                    name=name,
                    error=str(exc),
                )

    def teardown_all(self) -> None:
        """Tear down all plugins."""
        for name, plugin in self._plugins.items():
            try:
                plugin.teardown()
            except Exception as exc:
                logger.error(
                    "plugin_teardown_error",
                    name=name,
                    error=str(exc),
                )

    def list_plugins(self) -> list[PluginInfo]:
        """List all registered plugins."""
        return list(self._info.values())


# ── #61: Custom tokenizer hook ───────────────────────────────────


class TokenizerHook(Protocol):
    """Protocol for custom search tokenizers."""

    def tokenize(self, text: str) -> list[str]:
        """Tokenize text into searchable terms."""
        ...


class DefaultTokenizer:
    """Default whitespace + punctuation tokenizer."""

    def tokenize(self, text: str) -> list[str]:
        """Split text into lowercase tokens."""
        import re

        return [w for w in re.findall(r"\w+", text.lower()) if len(w) >= 2]


_active_tokenizer: TokenizerHook = DefaultTokenizer()


def set_tokenizer(tokenizer: TokenizerHook) -> None:
    """Set a custom tokenizer for search operations.

    Args:
        tokenizer: Tokenizer implementing TokenizerHook.
    """
    global _active_tokenizer  # noqa: PLW0603
    _active_tokenizer = tokenizer
    logger.info(
        "custom_tokenizer_set",
        type=type(tokenizer).__name__,
    )


def get_tokenizer() -> TokenizerHook:
    """Get the active tokenizer."""
    return _active_tokenizer


# ── #63: MCP tool reference guide ────────────────────────────────


MCP_TOOLS_GUIDE: list[dict[str, str]] = [
    {
        "name": "search",
        "description": "Full network search",
        "params": (
            "query (str), limit (int), format (str), "
            "language (str), date_from (float), "
            "date_to (float), include_domains (list), "
            "exclude_domains (list), offset (int), "
            "snippet_length (int), session_id (str)"
        ),
        "example": ('{"query": "python asyncio", "limit": 5, "format": "json"}'),
    },
    {
        "name": "search_local",
        "description": "Local-only search (offline capable)",
        "params": "Same as search",
        "example": ('{"query": "docker guide", "limit": 3}'),
    },
    {
        "name": "fetch_page",
        "description": "Fetch full text of a URL",
        "params": "url (str), format (str)",
        "example": '{"url": "https://example.com"}',
    },
    {
        "name": "crawl_url",
        "description": "Crawl and index a URL",
        "params": ("url (str), depth (int), force (bool), webhook_url (str)"),
        "example": ('{"url": "https://docs.python.org", "depth": 1}'),
    },
    {
        "name": "network_stats",
        "description": "Network status and statistics",
        "params": "format (str)",
        "example": '{"format": "json"}',
    },
    {
        "name": "batch_search",
        "description": "Multiple searches in one call",
        "params": ("queries (list[str]), limit (int), format (str)"),
        "example": ('{"queries": ["python", "rust"], "limit": 3}'),
    },
    {
        "name": "suggest",
        "description": "Search autocomplete suggestions",
        "params": "prefix (str), limit (int)",
        "example": '{"prefix": "pyth", "limit": 5}',
    },
    {
        "name": "register_webhook",
        "description": "Register crawl completion webhook",
        "params": "url (str)",
        "example": ('{"url": "https://example.com/webhook"}'),
    },
    {
        "name": "analytics",
        "description": "Search and crawl analytics",
        "params": "format (str)",
        "example": '{"format": "json"}',
    },
]


def generate_tool_guide(*, format: str = "text") -> str:
    """Generate MCP tool reference documentation.

    Args:
        format: Output format ("text" or "markdown").

    Returns:
        Formatted tool guide string.
    """
    if format == "markdown":
        lines = ["# InfoMesh MCP Tools Reference\n"]
        for tool in MCP_TOOLS_GUIDE:
            lines.append(f"## `{tool['name']}`\n")
            lines.append(f"{tool['description']}\n")
            lines.append(f"**Parameters**: {tool['params']}\n")
            lines.append(f"**Example**: `{tool['example']}`\n")
        return "\n".join(lines)

    lines = ["InfoMesh MCP Tools Reference", "=" * 30, ""]
    for tool in MCP_TOOLS_GUIDE:
        lines.append(f"  {tool['name']}")
        lines.append(f"    {tool['description']}")
        lines.append(f"    Params: {tool['params']}")
        lines.append(f"    Example: {tool['example']}")
        lines.append("")
    return "\n".join(lines)


# ── #64: Changelog format ────────────────────────────────────────


@dataclass
class ChangelogEntry:
    """A single changelog entry."""

    version: str
    date: str
    changes: list[str] = field(default_factory=list)
    breaking: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Format as markdown."""
        lines = [f"## [{self.version}] - {self.date}\n"]
        if self.breaking:
            lines.append("### Breaking Changes\n")
            for c in self.breaking:
                lines.append(f"- {c}")
            lines.append("")
        if self.changes:
            lines.append("### Changes\n")
            for c in self.changes:
                lines.append(f"- {c}")
        return "\n".join(lines)


def generate_changelog(
    entries: list[ChangelogEntry],
) -> str:
    """Generate a full changelog.

    Args:
        entries: List of changelog entries (newest first).

    Returns:
        Full changelog in markdown format.
    """
    header = (
        "# Changelog\n\n"
        "All notable changes to InfoMesh will be "
        "documented in this file.\n\n"
        "The format is based on "
        "[Keep a Changelog](https://keepachangelog.com/).\n"
    )
    body = "\n\n".join(e.to_markdown() for e in entries)
    return f"{header}\n{body}"
