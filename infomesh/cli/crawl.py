"""CLI commands: crawl, mcp, dashboard."""

from __future__ import annotations

import asyncio
import time

import click

from infomesh.config import load_config


@click.command()
@click.argument("url")
@click.option(
    "--depth",
    "-d",
    default=0,
    help="Link follow depth (0=single page, 1-3=follow links)",
)
def crawl(url: str, depth: int) -> None:
    """Crawl a URL and add it to the local index."""

    async def _crawl() -> None:
        from dataclasses import replace

        from infomesh.services import AppContext, index_document

        config = load_config()
        max_depth = min(depth, config.crawl.max_depth)

        # Override max_depth so the worker only follows links up to user's limit
        if max_depth != config.crawl.max_depth:
            crawl_cfg = replace(config.crawl, max_depth=max_depth)
            config_adj = replace(config, crawl=crawl_cfg)
        else:
            config_adj = config

        ctx = AppContext(config_adj)

        if max_depth == 0:
            # Single URL — simple output
            # Pass depth=max_depth so worker won't schedule child links
            result = await ctx.worker.crawl_url(url, depth=config_adj.crawl.max_depth)  # type: ignore[union-attr]
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
            await _crawl_with_progress(ctx, url, max_depth, index_document)

        await ctx.close_async()

    asyncio.run(_crawl())


async def _crawl_with_progress(
    ctx: object,
    url: str,
    max_depth: int,
    index_document: object,
) -> None:
    """Crawl with link following and real-time progress output."""
    start_time = time.monotonic()
    crawled = 0
    failed = 0
    skipped = 0
    total_chars = 0

    click.echo(f"\n🕷️  Crawling {url} (depth: {max_depth})\n")

    # Phase 1: Crawl the initial URL (depth=0 so links are discovered)
    result = await ctx.worker.crawl_url(url, depth=0)  # type: ignore[union-attr]
    pending = ctx.scheduler.pending_count  # type: ignore[union-attr]

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
    while ctx.scheduler.pending_count > 0:  # type: ignore[union-attr]
        child_url, child_depth = await ctx.scheduler.get_url()  # type: ignore[union-attr]
        child_result = await ctx.worker.crawl_url(child_url, depth=child_depth)  # type: ignore[union-attr]

        page_num = crawled + failed + skipped + 1
        time.monotonic() - start_time
        remaining = ctx.scheduler.pending_count  # type: ignore[union-attr]

        if child_result.success and child_result.page:
            index_document(child_result.page, ctx.store, ctx.vector_store)  # type: ignore[operator]
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
def mcp_cmd() -> None:
    """Run the MCP server (stdio mode for VS Code / Claude)."""
    from infomesh.mcp.server import run_mcp_server

    config = load_config()
    asyncio.run(run_mcp_server(config))


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
