"""CLI commands: start, stop, _serve (background node process)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import click
import structlog

from infomesh import __version__
from infomesh.config import Config, NodeRole, load_config

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
    help=("Run in background: start node, show log path, and exit (no dashboard)"),
)
@click.option(
    "--no-dashboard",
    is_flag=True,
    default=False,
    help=(
        "Start node without launching dashboard (backward compat, same as --background)"
    ),
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

    # ── GitHub identity ───────────────────────────────────────
    from infomesh.credits.github_identity import (
        run_first_start_checks,
    )

    run_first_start_checks(config)

    # ── Preflight checks ──────────────────────────────────────────
    from infomesh.resources.preflight import IssueSeverity, run_preflight_checks

    click.echo("  ⏳ Running preflight checks...", nl=False)
    issues = run_preflight_checks(config.node.data_dir)
    if not any(i.severity == IssueSeverity.ERROR for i in issues):
        click.echo(" ✔")
    else:
        click.echo(" ✖")
    has_error = False
    for issue in issues:
        icon = "✖" if issue.severity == IssueSeverity.ERROR else "⚠"
        style = "red" if issue.severity == IssueSeverity.ERROR else "yellow"
        click.secho(f"  {icon} [{issue.check}] {issue.message}", fg=style)
        if issue.severity == IssueSeverity.ERROR:
            has_error = True
    if has_error:
        click.secho(
            "\nCannot start: fix the errors above first.",
            fg="red",
            bold=True,
        )
        raise SystemExit(1)

    # ── Port accessibility check ──────────────────────────────────
    from infomesh.resources.port_check import check_port_and_offer_fix

    port = config.node.listen_port
    click.echo(f"  ⏳ Checking port {port}...", nl=False)
    port_ok = check_port_and_offer_fix(port)
    click.echo(" ✔" if port_ok else " ⚠")
    if (
        not port_ok
        and sys.stdin.isatty()
        and not click.confirm(
            "  Continue without P2P port access? "
            "(local crawl & search will work, but peering won't)",
            default=False,
        )
    ):
        raise SystemExit(1)

    # ── Launch node process ───────────────────────────────────────
    click.echo("  ⏳ Launching node process...", nl=False)
    serve_cmd = [sys.executable, "-m", "infomesh", "_serve"]
    if seeds:
        serve_cmd.extend(["--seeds", seeds])
    if role:
        serve_cmd.extend(["--role", role])

    log_path = config.node.data_dir / "node.log"
    try:
        proc = subprocess.Popen(
            serve_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        click.echo(f" ✔ (PID {proc.pid})")
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

        click.echo()
        click.echo("  Launching dashboard...")
        from infomesh.dashboard.app import run_dashboard

        exit_action = run_dashboard(config=config, node_pid=proc.pid)
    finally:
        pass  # log rotation is handled by _serve subprocess

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


def _run_foreground_crawl(config: Config, seed_category: str) -> None:
    """Run a quick initial crawl pass before launching the dashboard.

    Smart crawl strategy:
    1. Load seeds from requested category, skip already-indexed URLs.
    2. If all seeds in that category are indexed, try other categories.
    3. If everything is indexed, skip with a friendly message.
    """
    import time

    from infomesh.crawler.seeds import CATEGORIES, load_seeds
    from infomesh.services import AppContext, index_document

    ctx = AppContext(config)
    dedup = ctx.dedup

    # ── Collect unseen URLs across categories ──────────────────
    unseen_urls: list[str] = []
    active_category = seed_category

    # Try requested category first
    seed_urls = load_seeds(category=seed_category)
    if seed_urls and dedup is not None:
        unseen_urls = [u for u in seed_urls if not dedup.is_url_seen(u)]

    # If all seeds in requested category are seen, try others
    if not unseen_urls and dedup is not None:
        for cat in CATEGORIES:
            if cat == seed_category:
                continue
            cat_urls = load_seeds(category=cat)
            cat_unseen = [u for u in cat_urls if not dedup.is_url_seen(u)]
            if cat_unseen:
                unseen_urls = cat_unseen
                active_category = cat
                break

    if not unseen_urls:
        total = len(seed_urls) if seed_urls else 0
        click.echo(
            f"    All {total} seed URLs already indexed."
            f" Background crawler will discover new links."
        )
        return

    # Crawl a small batch of unseen URLs
    batch_size = min(5, len(unseen_urls))
    click.echo(
        f"    Seeds: {active_category}"
        f" ({len(unseen_urls)} new URLs,"
        f" crawling {batch_size})\n"
    )

    crawled = 0
    start_time = time.monotonic()

    async def _crawl_batch() -> int:
        nonlocal crawled
        for i, url in enumerate(unseen_urls[:batch_size], 1):
            try:
                result = await ctx.worker.crawl_url(url, depth=0)  # type: ignore[union-attr]
                if result.success and result.page:
                    index_document(result.page, ctx.store, ctx.vector_store)
                    crawled += 1
                    title = (
                        result.page.title[:55] if result.page.title else "(no title)"
                    )
                    click.echo(f"    [{i}/{batch_size}] ✓ {title}\n              {url}")
                else:
                    click.echo(f"    [{i}/{batch_size}] ✗ {url} — {result.error}")
            except Exception as exc:  # noqa: BLE001
                click.echo(f"    [{i}/{batch_size}] ✗ {url} — {exc}")
        return crawled

    asyncio.run(_crawl_batch())
    elapsed = time.monotonic() - start_time
    click.echo(f"\n    Initial crawl: {crawled} pages indexed in {elapsed:.1f}s")
    ctx.close()


# ---------------------------------------------------------------------------
# infomesh stop
# ---------------------------------------------------------------------------
@click.command()
def stop() -> None:
    """Stop the running InfoMesh node and related processes."""
    config = load_config()
    pid_file = _pid_path(config.node.data_dir)

    stopped_any = False

    # ── Stop node process via PID file ────────────────────────
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, signal.SIGTERM)
            click.echo(f"Sent SIGTERM to InfoMesh node (PID {pid}).")
            stopped_any = True
        except ProcessLookupError:
            click.echo("Node process not found (stale PID file). Cleaning up.")
        except ValueError:
            click.echo("Invalid PID file. Cleaning up.")
        pid_file.unlink(missing_ok=True)

    # ── Kill orphaned BGM player processes ────────────────────
    _kill_bgm_processes()

    if not stopped_any:
        click.echo("No running InfoMesh node found.")


def _kill_bgm_processes() -> None:
    """Find and kill BGM player processes spawned by infomesh."""
    from infomesh.dashboard.bgm import kill_orphaned_bgm

    kill_orphaned_bgm()


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
    import logging
    from logging.handlers import RotatingFileHandler

    config = load_config()

    # ── Set up log rotation (10 MB × 5 files) ────────────────
    log_path = config.node.data_dir / "node.log"
    _file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    _file_handler.setLevel(logging.DEBUG)
    logging.basicConfig(
        handlers=[_file_handler],
        level=logging.DEBUG,
        format="%(message)s",
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    _serve_logger = structlog.get_logger()
    if role:
        from dataclasses import replace as dc_replace

        config = dc_replace(config, node=dc_replace(config.node, role=role))

    pid_file = _pid_path(config.node.data_dir)
    pid_file.write_text(str(os.getpid()))
    _serve_logger.info("serve_started", pid=os.getpid(), role=config.node.role)

    # ── Initialize credit sync (before P2P so the handler is wired) ──
    _credit_sync_mgr: object | None = None
    try:
        from infomesh.credits.github_identity import resolve_github_email
        from infomesh.credits.ledger import CreditLedger
        from infomesh.credits.sync import CreditSyncManager, CreditSyncStore

        _gh_email = resolve_github_email(config) or ""
        if _gh_email:
            _cs_ledger = CreditLedger(
                config.node.data_dir / "credits.db",
                owner_email=_gh_email,
            )
            _cs_store = CreditSyncStore(
                config.node.data_dir / "credit_sync.db",
            )
            _kp = None
            try:
                from infomesh.p2p.keys import ensure_keys

                _kp = ensure_keys(config.node.data_dir)
            except Exception:  # noqa: BLE001
                pass
            _credit_sync_mgr = CreditSyncManager(
                ledger=_cs_ledger,
                store=_cs_store,
                owner_email=_gh_email,
                key_pair=_kp,
            )
            _serve_logger.info(
                "credit_sync_initialized",
                email_hash=_credit_sync_mgr.owner_email_hash[:16],
            )
    except Exception:  # noqa: BLE001
        _serve_logger.debug("credit_sync_init_skipped")

    # ── P2P node (best-effort) ─────────────────────────────────
    # Build local_search_fn so peers can query our local index.
    from infomesh.services import bootstrap_p2p, create_local_search_fn

    _local_search_fn = create_local_search_fn(config)

    p2p_node, _distributed_index = bootstrap_p2p(
        config,
        credit_sync_manager=_credit_sync_mgr,
        local_search_fn=_local_search_fn,
    )

    async def _run() -> None:
        from infomesh.services import AppContext, seed_and_crawl_loop

        ctx = AppContext(config)
        # Attach P2P components so MCP and search can use them
        ctx.distributed_index = _distributed_index
        ctx.p2p_node = p2p_node

        # ── SIGTERM graceful shutdown ─────────────────────────
        loop = asyncio.get_running_loop()
        _shutdown_event = asyncio.Event()

        def _sigterm_handler() -> None:
            _serve_logger.info("sigterm_received", msg="Graceful shutdown starting")
            _shutdown_event.set()

        try:
            loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)
            loop.add_signal_handler(signal.SIGINT, _sigterm_handler)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

        if config.node.role == NodeRole.SEARCH:
            # Search-only nodes don't crawl — wait for index submissions
            _serve_logger.info("search_mode", msg="Waiting for index submissions")
            await _shutdown_event.wait()
        else:
            crawl_task = asyncio.create_task(
                seed_and_crawl_loop(ctx, seed_category=seeds or "tech-docs"),
            )
            shutdown_task = asyncio.create_task(_shutdown_event.wait())
            done, pending = await asyncio.wait(
                [crawl_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await t

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    finally:
        if p2p_node is not None and hasattr(p2p_node, "stop"):
            p2p_node.stop()
            _serve_logger.info("p2p_stopped")
        pid_file.unlink(missing_ok=True)
        _serve_logger.info("serve_stopped")


# ---------------------------------------------------------------------------
# infomesh status
# ---------------------------------------------------------------------------


def _render_p2p_status(
    config: Config,
    running: bool,
) -> None:
    """Render P2P status lines for the ``status`` command."""
    from infomesh.dashboard.utils import read_p2p_status

    p2p = read_p2p_status(config)
    p2p_state = p2p.get("state", "stopped")
    p2p_peers = p2p.get("peers", 0)

    if p2p_state == "running":
        click.echo(f"P2P:             running ({p2p_peers} peers)")
        addrs = p2p.get("listen_addrs", [])
        if addrs and isinstance(addrs, list):
            click.echo("P2P addrs:       " + ", ".join(str(a) for a in addrs))
        bs = p2p.get("bootstrap", {})
        if isinstance(bs, dict) and p2p_peers == 0:
            _render_bootstrap_hints(bs)
    elif p2p_state == "error":
        click.echo("P2P:             " + click.style("error", fg="red"))
        err = p2p.get("error", "")
        if err:
            click.echo(f"P2P error:       {err}")
    elif running:
        click.echo("P2P:             " + click.style("not connected", fg="yellow"))
        click.echo("                 (libp2p not installed or no bootstrap nodes)")
    else:
        click.echo("P2P:             stopped")


def _render_bootstrap_hints(
    bs: dict[str, object],
) -> None:
    """Render bootstrap troubleshooting hints."""
    bs_conf = bs.get("configured", 0)
    bs_conn = bs.get("connected", 0)
    bs_fail = bs.get("failed", 0)

    if bs_conf == 0:
        click.echo("Bootstrap:       " + click.style("none configured", fg="yellow"))
        click.echo("                 Add bootstrap nodes in ~/.infomesh/config.toml:")
        click.echo("                 [network]")
        click.echo(
            '                 bootstrap_nodes = ["/ip4/<IP>/tcp/4001/p2p/<PEER_ID>"]'
        )
    elif isinstance(bs_fail, int) and bs_fail > 0 and bs_conn == 0:
        click.echo(
            "Bootstrap:       "
            + click.style(
                f"all {bs_fail} nodes unreachable",
                fg="red",
            )
        )
        failed = bs.get("failed_addrs", [])
        if isinstance(failed, list):
            for fa in failed:
                click.echo(f"                 ✗ {fa}")
        click.echo(
            "                 Check: (1) node running?"
            " (2) port 4001 open?"
            " (3) correct IP?"
        )
        click.echo("                 Test: nc -zv <IP> 4001")


def _render_credit_status(ledger: object) -> None:
    """Render credit status lines for the ``status`` command."""
    if ledger is None:
        click.echo("Credits:         N/A (ledger unavailable)")
        return
    ls = ledger.stats()  # type: ignore[attr-defined]
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
    if ls.owner_email:
        click.echo(f"GitHub:          {ls.owner_email}")
        click.echo("                 Credits linked across all nodes.")
    else:
        click.echo("GitHub:          " + click.style("not connected", fg="yellow"))
        click.echo(
            "                 Run 'infomesh config github your@email.com' to link."
        )


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

        _render_p2p_status(config, running)
        _render_credit_status(ctx.ledger)

        keys_dir = config.node.data_dir / "keys"
        if (keys_dir / "private.pem").exists():
            from infomesh.p2p.keys import KeyPair

            pair = KeyPair.load(keys_dir)
            click.echo(f"Peer ID:         {pair.peer_id}")
        else:
            click.echo("Peer ID:         (not generated yet — run 'infomesh start')")
