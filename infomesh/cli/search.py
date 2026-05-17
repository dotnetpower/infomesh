"""CLI command: search (distributed / local / hybrid)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import click

from infomesh.config import load_config

_DISTRIBUTED_BOOTSTRAP_ATTEMPTS = 2


def _network_search_fn(p2p_node: object | None) -> Any | None:
    """Return the P2P network search bridge when the node exposes one."""
    if p2p_node is None:
        return None
    search_network = getattr(p2p_node, "search_network", None)
    return search_network if callable(search_network) else None


def _stop_p2p_node(p2p_node: object | None) -> None:
    """Stop a best-effort CLI P2P node."""
    if p2p_node is None:
        return
    stop = getattr(p2p_node, "stop", None)
    if callable(stop):
        stop()


def _wait_for_connected_peer(
    p2p_node: object | None,
    *,
    timeout_seconds: float = 20.0,
) -> bool | None:
    """Give the background P2P bootstrap a brief chance to connect."""
    if p2p_node is None:
        return None

    get_connected = getattr(p2p_node, "get_connected_peers", None)
    if not callable(get_connected):
        return None

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        peers = get_connected()
        if isinstance(peers, list) and peers:
            return True
        time.sleep(0.1)
    return False


def _format_local_search(
    store: Any,
    config: Any,
    query: str,
    limit: int,
    *,
    use_vector: bool,
) -> str:
    """Run the existing local or hybrid CLI search path."""
    from infomesh.search.formatter import format_fts_results, format_hybrid_results
    from infomesh.search.query import search_local

    index_config = config.index
    config_vector = bool(getattr(index_config, "vector_search", False))
    if use_vector or config_vector:
        try:
            from infomesh.index.vector_store import VectorStore
            from infomesh.search.query import search_hybrid

            node_config = config.node
            vec_store = VectorStore(
                persist_dir=node_config.data_dir / "chroma",
                model_name=index_config.embedding_model,
            )
            try:
                hybrid = search_hybrid(store, vec_store, query, limit=limit)  # type: ignore[arg-type]
                return format_hybrid_results(hybrid)
            finally:
                vec_store.close()
        except ImportError:
            click.echo(
                "Warning: chromadb not installed, falling back to keyword search.",
                err=True,
            )

    result = search_local(store, query, limit=limit)
    return format_fts_results(result)


def _format_distributed_search(
    store: Any,
    config: Any,
    query: str,
    limit: int,
) -> str | None:
    """Run distributed CLI search, returning None when P2P is unavailable."""
    from infomesh.search.formatter import format_distributed_results
    from infomesh.search.query import DistributedResult, search_distributed
    from infomesh.services import bootstrap_p2p

    last_result: DistributedResult | None = None
    for attempt in range(_DISTRIBUTED_BOOTSTRAP_ATTEMPTS):
        p2p_node: object | None = None
        try:
            p2p_node, distributed_index = bootstrap_p2p(config)
            if distributed_index is None:
                return None

            connected = _wait_for_connected_peer(p2p_node)

            result = asyncio.run(
                search_distributed(
                    store,
                    distributed_index,  # type: ignore[arg-type]
                    query,
                    limit=limit,
                    network_search_fn=_network_search_fn(p2p_node),
                )
            )
            last_result = result
            should_retry = (
                connected is False
                and result.local_count == 0
                and result.remote_count == 0
                and attempt + 1 < _DISTRIBUTED_BOOTSTRAP_ATTEMPTS
            )
            if not should_retry:
                return format_distributed_results(result)
        finally:
            _stop_p2p_node(p2p_node)

    return format_distributed_results(last_result) if last_result is not None else None


@click.command()
@click.argument("query")
@click.option(
    "--limit",
    "-n",
    default=10,
    type=click.IntRange(1, 100),
    help="Maximum results",
)
@click.option(
    "--local",
    "local_only",
    is_flag=True,
    default=False,
    help="Search local index only",
)
@click.option(
    "--local-only",
    "local_only_alias",
    is_flag=True,
    default=False,
    help="Alias for --local",
)
@click.option(
    "--vector",
    is_flag=True,
    default=False,
    help="Include vector/semantic search for local results",
)
def search(
    query: str,
    limit: int,
    local_only: bool,
    local_only_alias: bool,
    vector: bool,
) -> None:
    """Search the local index and peers when P2P is available."""
    from infomesh.index.local_store import LocalStore

    config = load_config()
    store = LocalStore(
        db_path=config.index.db_path,
        compression_enabled=config.storage.compression_enabled,
        compression_level=config.storage.compression_level,
    )

    try:
        if not (local_only or local_only_alias or vector):
            try:
                text = _format_distributed_search(store, config, query, limit)
                if text is not None:
                    click.echo(text)
                    return
                click.echo(
                    "Warning: P2P unavailable, falling back to local search.",
                    err=True,
                )
            except Exception as exc:  # noqa: BLE001
                click.echo(
                    f"Warning: network search failed, falling back to local: {exc}",
                    err=True,
                )

        click.echo(
            _format_local_search(
                store,
                config,
                query,
                limit,
                use_vector=vector,
            )
        )
    finally:
        store.close()


# ── Feedback subcommands ────────────────────────────────────────────


@click.group("feedback")
def feedback_group() -> None:
    """Inspect implicit search quality signals."""


@feedback_group.command("stats")
def feedback_stats() -> None:
    """Show feedback signal statistics."""
    config = load_config()
    feedback_db = config.node.data_dir / "feedback.db"
    if not feedback_db.exists():
        click.echo("No feedback data yet. Search more to collect signals.")
        return

    from infomesh.search.feedback import FeedbackStore

    store = FeedbackStore(str(feedback_db))
    try:
        count = store.signal_count()
        top = store.top_boosted_urls(limit=10)
        click.echo(f"Total signals: {count}")
        click.echo(f"Boosted URLs:  {len(top)}")
        if top:
            click.echo(f"\n{'URL':<60} {'Boost':>8} {'Fetch':>6} {'Cite':>6}")
            click.echo("─" * 82)
            for u in top:
                url_display = u.url[:58] + ".." if len(u.url) > 60 else u.url
                click.echo(
                    f"{url_display:<60} {u.boost_score:>8.2f}"
                    f" {u.fetch_count:>6} {u.cite_count:>6}"
                )
    finally:
        store.close()


@feedback_group.command("top-urls")
@click.option("--limit", "-n", default=20, help="Number of URLs to show")
def feedback_top_urls(limit: int) -> None:
    """Show URLs with highest quality signals."""
    config = load_config()
    feedback_db = config.node.data_dir / "feedback.db"
    if not feedback_db.exists():
        click.echo("No feedback data yet.")
        return

    from infomesh.search.feedback import FeedbackStore

    store = FeedbackStore(str(feedback_db))
    try:
        top = store.top_boosted_urls(limit=limit)
        if not top:
            click.echo("No boosted URLs yet.")
            return
        for u in top:
            click.echo(
                f"  {u.boost_score:+.2f}  {u.url}"
                f"  (fetch={u.fetch_count} cite={u.cite_count})"
            )
    finally:
        store.close()
