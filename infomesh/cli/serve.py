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
    "--no-dashboard",
    is_flag=True,
    default=False,
    help="Start node without launching dashboard",
)
@click.option(
    "--role",
    "-r",
    type=click.Choice(["full", "crawler", "search"], case_sensitive=False),
    default=None,
    help="Node role: full (default), crawler (DMZ), or search (private)",
)
def start(seeds: str | None, no_dashboard: bool, role: str | None) -> None:
    """Start the InfoMesh node and launch the dashboard."""
    import subprocess
    from dataclasses import replace as dc_replace

    config = load_config()
    if role:
        config = dc_replace(config, node=dc_replace(config.node, role=role))

    from infomesh.p2p.keys import ensure_keys

    keys = ensure_keys(config.node.data_dir)
    click.echo(f"InfoMesh v{__version__} starting...")
    click.echo(f"  Peer ID: {keys.peer_id}")
    click.echo(f"  Data dir: {config.node.data_dir}")

    # Preflight checks
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

        if no_dashboard:
            click.echo("  Node running in background. Use 'infomesh stop' to stop.")
            return

        from infomesh.dashboard.app import run_dashboard

        click.echo("  Launching dashboard...")
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
    ctx = AppContext(config)
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

    ctx.close()
