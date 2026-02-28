"""CLI commands: start, stop, _serve (background node process)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

import click
import structlog

from infomesh import __version__
from infomesh.config import NodeRole, load_config

logger = structlog.get_logger()

_PID_FILE_NAME = "infomesh.pid"


def _pid_path(data_dir: Path) -> Path:
    return data_dir / _PID_FILE_NAME


# ---------------------------------------------------------------------------
# infomesh start
# ---------------------------------------------------------------------------
@click.command()
@click.option(
    "--seeds",
    "-s",
    default=None,
    help="Seed category (tech-docs, academic, encyclopedia)",
)
@click.option(
    "--background",
    "-b",
    is_flag=True,
    default=False,
    help="Run in background: start node, show log path, and exit (no dashboard, no live log)",
)
@click.option(
    "--no-dashboard",
    is_flag=True,
    default=False,
    help="Start node without launching dashboard (kept for backward compat, same as --background)",
)
@click.option(
    "--role",
    "-r",
    type=click.Choice(["full", "crawler", "search"], case_sensitive=False),
    default=None,
    help="Node role: full (default), crawler (DMZ), or search (private)",
)
def start(
    seeds: str | None,
    background: bool,
    no_dashboard: bool,
    role: str | None,
) -> None:
    """Start the InfoMesh node.

    \b
    Background mode (--background):
      Starts the node as a background process, prints the log file path,
      and exits immediately. No live log output.

    \b
    Foreground mode (default):
      Runs an initial crawl pass with live progress, then launches the
      interactive dashboard (TUI) with BGM off.

    In both modes, the P2P listen port is checked and cloud firewall
    auto-open is offered if the port appears blocked.
    """
    import subprocess
    from dataclasses import replace as dc_replace

    # --no-dashboard is an alias for --background (backward compat)
    if no_dashboard:
        background = True

    config = load_config()
    if role:
        config = dc_replace(config, node=dc_replace(config.node, role=role))

    from infomesh.p2p.keys import ensure_keys

    keys = ensure_keys(config.node.data_dir)
    click.echo(f"InfoMesh v{__version__} starting...")
    click.echo(f"  Peer ID: {keys.peer_id}")
    click.echo(f"  Data dir: {config.node.data_dir}")

    # ── Preflight checks ──────────────────────────────────────────
    from infomesh.resources.preflight import IssueSeverity, run_preflight_checks

    issues = run_preflight_checks(config.node.data_dir)
    has_error = False
    for issue in issues:
        icon = "✖" if issue.severity == IssueSeverity.ERROR else "⚠"
        style = "red" if issue.severity == IssueSeverity.ERROR else "yellow"
        click.secho(f"  {icon} [{issue.check}] {issue.message}", fg=style)
        if issue.severity == IssueSeverity.ERROR:
            has_error = True
    if has_error:
        click.secho("\nCannot start: fix the errors above first.", fg="red", bold=True)
        raise SystemExit(1)

    # ── Port accessibility check ──────────────────────────────────
    from infomesh.resources.port_check import check_port_and_offer_fix

    check_port_and_offer_fix(config.node.listen_port)

    # ── Launch node process ───────────────────────────────────────
    serve_cmd = [sys.executable, "-m", "infomesh", "_serve"]
    if seeds:
        serve_cmd.extend(["--seeds", seeds])
    if role:
        serve_cmd.extend(["--role", role])

    log_path = config.node.data_dir / "node.log"
    log_file = open(log_path, "a")  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            serve_cmd,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        click.echo(f"  Node process started (PID {proc.pid})")
        click.echo(f"  Log: {log_path}")

        if background:
            # ── Background mode ───────────────────────────────
            click.echo()
            click.echo("  Node running in background (no live log).")
            click.echo(f"  View logs:   tail -f {log_path}")
            click.echo("  Stop node:   infomesh stop")
            return

        # ── Foreground mode: crawl → dashboard ────────────────
        click.echo()
        click.echo("  Running initial crawl pass...")
        seed_cat = seeds or "tech-docs"
        _run_foreground_crawl(config, seed_cat)

        # Launch dashboard with BGM OFF for foreground mode
        config = dc_replace(
            config,
            dashboard=dc_replace(config.dashboard, bgm_auto_start=False),
        )

        click.echo()
        click.echo("  Launching dashboard (BGM off)...")
        from infomesh.dashboard.app import run_dashboard

        exit_action = run_dashboard(config=config, node_pid=proc.pid)
    finally:
        log_file.close()

    if exit_action == "stop_all":
        try:
            os.kill(proc.pid, signal.SIGTERM)
            click.echo(f"\nInfoMesh node stopped (PID {proc.pid}).")
        except ProcessLookupError:
            pass
        pid_file = _pid_path(config.node.data_dir)
        pid_file.unlink(missing_ok=True)
    else:
        click.echo(f"\nDashboard closed. Node still running (PID {proc.pid}).")
        click.echo("  Use 'infomesh stop' to stop the node.")


def _run_foreground_crawl(config: "Config", seed_category: str) -> None:
    """Run a quick initial crawl pass before launching the dashboard."""
    import time

    from infomesh.crawler.seeds import load_seeds
    from infomesh.services import AppContext, index_document

    seed_urls = load_seeds(category=seed_category)
    if not seed_urls:
        click.echo(f"    No seeds found for category '{seed_category}'.")
        return

    # Crawl a small batch to warm up the index
    batch_size = min(5, len(seed_urls))
    click.echo(f"    Seeds: {seed_category} ({len(seed_urls)} URLs, crawling first {batch_size})\n")

    ctx = AppContext(config)
    crawled = 0
    start_time = time.monotonic()

    async def _crawl_batch() -> int:
        nonlocal crawled
        for i, url in enumerate(seed_urls[:batch_size], 1):
            try:
                result = await ctx.worker.crawl_url(url, depth=0)  # type: ignore[union-attr]
                if result.success and result.page:
                    index_document(result.page, ctx.store, ctx.vector_store)
                    crawled += 1
                    title = result.page.title[:55] if result.page.title else "(no title)"
                    click.echo(
                        f"    [{i}/{batch_size}] ✓ {title}\n"
                        f"              {url}"
                    )
                else:
                    click.echo(f"    [{i}/{batch_size}] ✗ {url} — {result.error}")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"    [{i}/{batch_size}] ✗ {url} — {exc}")
        return crawled

    asyncio.run(_crawl_batch())
    elapsed = time.monotonic() - start_time
    click.echo(f"\n    Initial crawl: {crawled} pages indexed in {elapsed:.1f}s")


# ---------------------------------------------------------------------------
# infomesh stop
# ---------------------------------------------------------------------------
@click.command()
def stop() -> None:
    """Stop the running InfoMesh node."""
    config = load_config()
    pid_file = _pid_path(config.node.data_dir)

    if not pid_file.exists():
        click.echo("No running InfoMesh node found.")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        click.echo(f"Sent SIGTERM to InfoMesh node (PID {pid}).")
        pid_file.unlink(missing_ok=True)
    except ProcessLookupError:
        click.echo("Node process not found (stale PID file). Cleaning up.")
        pid_file.unlink(missing_ok=True)
    except ValueError:
        click.echo("Invalid PID file. Cleaning up.")
        pid_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# infomesh _serve  (internal — background node process)
# ---------------------------------------------------------------------------
@click.command(name="_serve", hidden=True)
@click.option("--seeds", "-s", default=None)
@click.option(
    "--role",
    "-r",
    type=click.Choice(["full", "crawler", "search"], case_sensitive=False),
    default=None,
)
def serve(seeds: str | None, role: str | None) -> None:
    """Internal: run the crawl loop as a background process."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )
    _serve_logger = structlog.get_logger()

    config = load_config()
    if role:
        from dataclasses import replace as dc_replace

        config = dc_replace(config, node=dc_replace(config.node, role=role))

    pid_file = _pid_path(config.node.data_dir)
    pid_file.write_text(str(os.getpid()))
    _serve_logger.info("serve_started", pid=os.getpid(), role=config.node.role)

    async def _run() -> None:
        from infomesh.services import AppContext, seed_and_crawl_loop

        ctx = AppContext(config)

        if config.node.role == NodeRole.SEARCH:
            # Search-only nodes don't crawl — wait for index submissions
            _serve_logger.info("search_mode", msg="Waiting for index submissions")
            import asyncio

            try:
                while True:
                    await asyncio.sleep(60)
            except asyncio.CancelledError:
                pass
        else:
            await seed_and_crawl_loop(ctx, seed_category=seeds or "tech-docs")

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        pid_file.unlink(missing_ok=True)
        _serve_logger.info("serve_stopped")


# ---------------------------------------------------------------------------
# infomesh status
# ---------------------------------------------------------------------------
@click.command()
def status() -> None:
    """Show node status."""
    from infomesh.services import AppContext

    config = load_config()
    with AppContext(config) as ctx:
        stats = ctx.store.get_stats()

        pid_file = _pid_path(config.node.data_dir)
        running = pid_file.exists()

        click.echo(f"InfoMesh v{__version__}")
        click.echo(f"{'=' * 30}")
        click.echo("Phase:           0 (MVP)")
        click.echo(f"Running:         {'yes' if running else 'no'}")
        click.echo(f"Data dir:        {config.node.data_dir}")
        click.echo(f"Index DB:        {config.index.db_path}")
        click.echo(f"Documents:       {stats['document_count']}")
        comp_on = "on" if config.storage.compression_enabled else "off"
        comp_lvl = config.storage.compression_level
        click.echo(f"Compression:     {comp_on} (zstd level {comp_lvl})")
        click.echo(f"Vector search:   {'on' if config.index.vector_search else 'off'}")
        if config.index.vector_search:
            click.echo(f"Embedding model: {config.index.embedding_model}")
            if ctx.vector_store is not None:
                vec_stats = ctx.vector_store.get_stats()
                click.echo(f"Vector docs:     {vec_stats['document_count']}")
            else:
                click.echo("Vector docs:     (chromadb not installed)")
        click.echo(f"LLM:             {'on' if config.llm.enabled else 'off'}")
        click.echo("P2P peers:       0 (Phase 2)")

        # Credits
        if ctx.ledger is not None:
            ls = ctx.ledger.stats()
            click.echo(
                f"Credits:         {ls.balance:.1f}"
                f" (earned {ls.total_earned:.1f}"
                f" / spent {ls.total_spent:.1f})"
            )
            click.echo(
                f"Tier:            {ls.tier.value}"
                f" (score {ls.contribution_score:.1f},"
                f" search cost {ls.search_cost:.3f})"
            )
            if ls.credit_state.value != "normal":
                click.echo(f"Credit state:    {ls.credit_state.value}")
        else:
            click.echo("Credits:         N/A (ledger unavailable)")

        keys_dir = config.node.data_dir / "keys"
        if (keys_dir / "private.pem").exists():
            from infomesh.p2p.keys import KeyPair

            pair = KeyPair.load(keys_dir)
            click.echo(f"Peer ID:         {pair.peer_id}")
        else:
            click.echo("Peer ID:         (not generated yet — run 'infomesh start')")
