"""Plugin system for InfoMesh extensibility.

Feature #13: Register custom crawlers, rankers, tokenizers, and
search processors via a simple hook-based API.

Usage::

    from infomesh.plugins import PluginRegistry, HookPoint

    registry = PluginRegistry()

    @registry.hook(HookPoint.PRE_INDEX)
    def my_filter(doc):
        # Filter or transform document before indexing
        if "spam" in doc.text:
            return None  # skip
        return doc

    @registry.hook(HookPoint.POST_SEARCH)
    def add_metadata(results):
        for r in results:
            r["custom_score"] = compute_custom(r)
        return results
"""

from __future__ import annotations

from collections import defaultdict
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger()


class HookPoint(StrEnum):
    """Extension points in the InfoMesh pipeline."""

    PRE_CRAWL = "pre_crawl"  # Before URL is crawled
    POST_CRAWL = "post_crawl"  # After page is crawled
    PRE_INDEX = "pre_index"  # Before document is indexed
    POST_INDEX = "post_index"  # After document is indexed
    PRE_SEARCH = "pre_search"  # Before search query is executed
    POST_SEARCH = "post_search"  # After results are ranked
    PRE_RANK = "pre_rank"  # Before ranking is applied
    POST_RANK = "post_rank"  # After ranking is applied
    CUSTOM_TOKENIZER = "custom_tokenizer"  # Custom tokenization
    CUSTOM_SCORER = "custom_scorer"  # Custom scoring function


class PluginRegistry:
    """Registry for InfoMesh plugins.

    Plugins register hooks that are called at specific points
    in the crawl/index/search pipeline.
    """

    def __init__(self) -> None:
        self._hooks: dict[HookPoint, list[Any]] = defaultdict(list)
        self._plugins: dict[str, dict[str, Any]] = {}

    def hook(self, point: HookPoint) -> Any:
        """Decorator to register a function at a hook point."""

        def decorator(fn: Any) -> Any:
            self._hooks[point].append(fn)
            logger.debug(
                "plugin_hook_registered",
                point=point.value,
                fn=fn.__name__,
            )
            return fn

        return decorator

    def register_plugin(
        self,
        name: str,
        version: str = "0.0.1",
        hooks: dict[HookPoint, Any] | None = None,
    ) -> None:
        """Register a named plugin with optional hooks."""
        self._plugins[name] = {"version": version, "hooks": hooks or {}}
        if hooks:
            for point, fn in hooks.items():
                self._hooks[point].append(fn)
        logger.info("plugin_registered", name=name, version=version)

    def run_hook(self, point: HookPoint, data: Any) -> Any:
        """Run all registered hooks for a given point.

        Each hook receives the data and returns modified data.
        If a hook returns None, the item is skipped (filtered out).
        """
        for fn in self._hooks.get(point, []):
            try:
                result = fn(data)
                if result is None:
                    return None
                data = result
            except Exception:  # noqa: BLE001
                logger.warning(
                    "plugin_hook_error",
                    point=point.value,
                    fn=getattr(fn, "__name__", str(fn)),
                    exc_info=True,
                )
        return data

    async def run_hook_async(self, point: HookPoint, data: Any) -> Any:
        """Run hooks (supports both sync and async hooks)."""
        import asyncio

        for fn in self._hooks.get(point, []):
            try:
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(data)
                else:
                    result = fn(data)
                if result is None:
                    return None
                data = result
            except Exception:  # noqa: BLE001
                logger.warning(
                    "plugin_hook_error",
                    point=point.value,
                    exc_info=True,
                )
        return data

    @property
    def registered_plugins(self) -> list[dict[str, Any]]:
        """List registered plugins."""
        return [{"name": n, "version": p["version"]} for n, p in self._plugins.items()]

    @property
    def hook_counts(self) -> dict[str, int]:
        """Count of registered hooks per point."""
        return {p.value: len(fns) for p, fns in self._hooks.items() if fns}


# Global registry instance
_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Get or create the global plugin registry."""
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = PluginRegistry()
    return _registry
