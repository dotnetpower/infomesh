"""CLI commands: crawl, mcp, dashboard."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import click

from infomesh.config import load_config

if TYPE_CHECKING:
    from infomesh.services import AppContext


@click.command()
@click.argument("url")
@click.option(
    "--depth",
    "-d",
    default=None,
    type=int,
    help=(
        "Link follow depth. "
        "If omitted, follows links unlimited (controlled by "
        "rate limits & dedup). "
        "Use --depth 0 for single page only."
    ),
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    default=False,
    help=(
        "Force re-crawl even if the URL was previously crawled. "
        "Useful for refreshing content or discovering new child links."
    ),
)
def crawl(url: str, depth: int | None, force: bool) -> None:
    """Crawl a URL and add it to the local index."""

    async def _crawl() -> None:
        from dataclasses import replace

        from infomesh.services import AppContext, index_document

        config = load_config()

        # depth=None → use config default (0=unlimited)
        # depth=0   → single page only
        # depth=N   → follow links N levels deep
        if depth is None:
            max_depth = config.crawl.max_depth
        else:
            if config.crawl.max_depth > 0:
                max_depth = min(depth, config.crawl.max_depth)
            else:
                max_depth = depth

        # Override max_depth so the worker only follows links up to user's limit
        if max_depth != config.crawl.max_depth:
            crawl_cfg = replace(config.crawl, max_depth=max_depth)
            config_adj = replace(config, crawl=crawl_cfg)
        else:
            config_adj = config

        ctx = AppContext(config_adj)

        try:
            if depth == 0:
                # Single URL — simple output
                # Pass depth=max_depth so worker won't schedule child links
                crawl_depth = config_adj.crawl.max_depth
                if ctx.worker is None:
                    click.echo("✗ Crawler not available")
                    return
                result = await ctx.worker.crawl_url(url, depth=crawl_depth, force=force)
                if (
                    not result.success
                    and result.error == "already_seen"
                    and not force
                    and click.confirm(
                        "This URL was already crawled. Re-crawl?",
                        default=True,
                    )
                ):
                    result = await ctx.worker.crawl_url(
                        url, depth=crawl_depth, force=True
                    )
                if result.success and result.page:
                    index_document(result.page, ctx.store, ctx.vector_store)
                    click.echo(f"✓ Crawled: {url}")
                    click.echo(f"  Title: {result.page.title}")
                    click.echo(f"  Text: {len(result.page.text)} chars")
                    click.echo(f"  Links discovered: {len(result.discovered_links)}")
                    click.echo(f"  Time: {result.elapsed_ms:.0f}ms")
                else:
                    click.echo(f"✗ Failed: {url} — {result.error}")
            else:
                # Multi-page mode with progress output
                # max_depth 0 = unlimited (scheduler enforces via dedup/rate)
                await _crawl_with_progress(
                    ctx, url, max_depth, index_document, force=force
                )
        finally:
            await ctx.close_async()

    asyncio.run(_crawl())


async def _crawl_with_progress(
    ctx: AppContext,
    url: str,
    max_depth: int,
    index_document: object,
    *,
    force: bool = False,
) -> None:
    """Crawl with link following and real-time progress output."""
    start_time = time.monotonic()
    crawled = 0
    failed = 0
    skipped = 0
    total_chars = 0

    depth_label = "unlimited" if max_depth == 0 else str(max_depth)
    click.echo(f"\n🕷️  Crawling {url} (depth: {depth_label})\n")

    # Phase 1: Crawl the initial URL (depth=0 so links are discovered)
    assert ctx.worker is not None
    assert ctx.scheduler is not None

    # Restrict link following to the same domain + path prefix
    ctx.worker.set_scope(url)

    result = await ctx.worker.crawl_url(url, depth=0, force=force)

    if (
        not result.success
        and result.error == "already_seen"
        and not force
        and click.confirm(
            "This URL was already crawled. Re-crawl?",
            default=True,
        )
    ):
        force = True
        result = await ctx.worker.crawl_url(url, depth=0, force=True)

    pending = ctx.scheduler.pending_count

    if result.success and result.page:
        index_document(result.page, ctx.store, ctx.vector_store)  # type: ignore[operator]
        crawled += 1
        total_chars += len(result.page.text)
        title = result.page.title[:60] if result.page.title else "(no title)"
        click.echo(
            f"  [1] ✓ {url}\n"
            f"       {title}\n"
            f"       {len(result.page.text):,} chars · {result.elapsed_ms:.0f}ms "
            f"· {len(result.discovered_links)} links → {pending} queued"
        )
    else:
        failed += 1
        click.echo(f"  [1] ✗ {url} — {result.error}")

    # Phase 2: Process queued child URLs
    while ctx.scheduler.pending_count > 0:
        child_url, child_depth = await ctx.scheduler.get_url()
        child_result = await ctx.worker.crawl_url(child_url, depth=child_depth)

        page_num = crawled + failed + skipped + 1
        remaining = ctx.scheduler.pending_count

        if child_result.success and child_result.page:
            index_document(child_result.page, ctx.store, ctx.vector_store)  # type: ignore[operator]  # noqa: E501
            crawled += 1
            total_chars += len(child_result.page.text)
            click.echo(
                f"  [{page_num}] ✓ {_trunc(child_url, 70)}  "
                f"({len(child_result.page.text):,} chars, "
                f"{child_result.elapsed_ms:.0f}ms) "
                f"[{remaining} queued]"
            )
        elif child_result.error in (
            "already_seen",
            "duplicate_content",
            "near_duplicate",
        ):
            skipped += 1
            # Don't print skip noise unless few pages
            if page_num <= 5:
                click.echo(
                    f"  [{page_num}] ⊘ "
                    f"{_trunc(child_url, 70)}"
                    f"  (skip: {child_result.error})"
                )
        else:
            failed += 1
            click.echo(
                f"  [{page_num}] ✗ {_trunc(child_url, 70)}  — {child_result.error}"
            )

    # Summary
    elapsed = time.monotonic() - start_time
    speed = crawled / elapsed if elapsed > 0 else 0
    click.echo(
        f"\n✅ Done! {crawled} pages crawled"
        + (f", {failed} failed" if failed else "")
        + (f", {skipped} skipped" if skipped else "")
        + f", {total_chars:,} chars indexed in {elapsed:.1f}s"
        + f" ({speed:.1f} pages/s)"
    )


def _trunc(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if it exceeds max_len."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


@click.command("mcp")
@click.option(
    "--http",
    is_flag=True,
    default=False,
    help=(
        "Use HTTP Streamable transport instead of "
        "stdio. Enables remote/container access."
    ),
)
@click.option(
    "--host",
    default="127.0.0.1",
    help="HTTP bind address (default: 127.0.0.1)",
)
@click.option(
    "--port",
    default=8081,
    type=int,
    help="HTTP port (default: 8081)",
)
def mcp_cmd(http: bool, host: str, port: int) -> None:
    """Run the MCP server (stdio or HTTP mode)."""
    config = load_config()

    # ── Best-effort P2P bootstrap for distributed search ──
    from infomesh.services import bootstrap_p2p

    p2p_node, distributed_index = bootstrap_p2p(config)

    try:
        if http:
            from infomesh.mcp.server import run_mcp_http_server

            asyncio.run(
                run_mcp_http_server(
                    config,
                    host=host,
                    port=port,
                    distributed_index=distributed_index,
                    p2p_node=p2p_node,
                )
            )
        else:
            from infomesh.mcp.server import run_mcp_server

            asyncio.run(
                run_mcp_server(
                    config,
                    distributed_index=distributed_index,
                    p2p_node=p2p_node,
                )
            )
    finally:
        if p2p_node is not None and hasattr(p2p_node, "stop"):
            p2p_node.stop()


@click.command()
@click.option(
    "--tab",
    "-t",
    default="overview",
    type=click.Choice(["overview", "crawl", "search", "network", "credits"]),
    help="Initial tab to display",
)
@click.option(
    "--text",
    is_flag=True,
    default=False,
    help="Print a static dashboard snapshot (no TUI). Works in any terminal.",
)
def dashboard(tab: str, text: bool) -> None:
    """Launch the interactive console dashboard (TUI)."""
    if text:
        from infomesh.dashboard.text_report import print_dashboard

        config = load_config()
        print_dashboard(config=config, tab=tab if tab != "overview" else None)
        return

    from infomesh.dashboard.app import run_dashboard

    config = load_config()
    run_dashboard(config=config, initial_tab=tab)
