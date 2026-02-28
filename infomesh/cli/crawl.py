"""CLI commands: crawl, mcp, dashboard."""

from __future__ import annotations

import asyncio

import click

from infomesh.config import load_config


@click.command()
@click.argument("url")
@click.option("--depth", "-d", default=0, help="Link follow depth (max 3)")
def crawl(url: str, depth: int) -> None:
    """Crawl a URL and add it to the local index."""

    async def _crawl() -> None:
        from infomesh.services import AppContext, index_document

        config = load_config()
        ctx = AppContext(config)

        clamped_depth = min(depth, config.crawl.max_depth)
        result = await ctx.worker.crawl_url(url, depth=clamped_depth)

        if result.success and result.page:
            index_document(result.page, ctx.store, ctx.vector_store)
            click.echo(f"✓ Crawled: {url}")
            click.echo(f"  Title: {result.page.title}")
            click.echo(f"  Text: {len(result.page.text)} chars")
            click.echo(f"  Links discovered: {len(result.discovered_links)}")
            click.echo(f"  Time: {result.elapsed_ms:.0f}ms")
        else:
            click.echo(f"✗ Failed: {url} — {result.error}")

        await ctx.close_async()

    asyncio.run(_crawl())


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
