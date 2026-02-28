"""Text-mode dashboard — Rich console output without TUI.

Provides a static snapshot of all dashboard data using Rich panels,
tables, and formatting. Works in any terminal without requiring
TUI capabilities (alternate screen, mouse support, etc.).

Usage:
    infomesh dashboard --text
"""

from __future__ import annotations

import os
import shutil
import time

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from infomesh import __version__
from infomesh.config import Config, load_config


def _get_peer_id(config: Config) -> str:
    """Load peer ID from key file if available."""
    keys_dir = config.node.data_dir / "keys"
    if (keys_dir / "private.pem").exists():
        try:
            from infomesh.p2p.keys import KeyPair

            pair = KeyPair.load(keys_dir)
            return pair.peer_id
        except Exception:  # noqa: BLE001
            pass
    return "(not generated)"


def _is_node_running(config: Config) -> tuple[bool, float]:
    """Check if InfoMesh node process is running. Returns (running, uptime_secs)."""
    pid_file = config.node.data_dir / "infomesh.pid"
    if not pid_file.exists():
        return False, 0.0
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        uptime = time.time() - pid_file.stat().st_mtime
        return True, uptime
    except (ValueError, ProcessLookupError, PermissionError):
        return False, 0.0


def _format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime string."""
    if seconds <= 0:
        return "—"
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    parts: list[str] = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_bytes(n: int) -> str:
    """Format byte count to human readable."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _make_bar(ratio: float, width: int = 20, color: str = "green") -> Text:
    """Create a progress bar using Unicode blocks."""
    ratio = max(0.0, min(1.0, ratio))
    filled = int(ratio * width)
    empty = width - filled

    if ratio >= 0.9:
        bar_color = "red"
    elif ratio >= 0.7:
        bar_color = "yellow"
    else:
        bar_color = color

    bar = Text()
    bar.append("█" * filled, style=bar_color)
    bar.append("░" * empty, style="dim")
    bar.append(f" {ratio * 100:.0f}%", style=bar_color)
    return bar


def _node_section(config: Config) -> Panel:
    """Build the Node Info panel."""
    peer_id = _get_peer_id(config)
    running, uptime = _is_node_running(config)
    state = "[bold green]🟢 Running[/]" if running else "[bold red]🔴 Stopped[/]"

    table = Table.grid(padding=(0, 2))
    table.add_column("key", style="bold", min_width=12)
    table.add_column("value")

    short_id = peer_id[:20] + "..." if len(peer_id) > 20 else peer_id
    table.add_row("Peer ID", short_id)
    table.add_row("State", state)
    table.add_row("Uptime", _format_uptime(uptime))
    table.add_row("Version", __version__)
    table.add_row("Data dir", str(config.node.data_dir))
    table.add_row("Port", str(config.node.listen_port))

    return Panel(table, title="[bold]Node[/]", border_style="cyan")


def _resource_section(config: Config) -> Panel:
    """Build the Resources panel."""
    table = Table.grid(padding=(0, 1))
    table.add_column("label", min_width=8, style="bold")
    table.add_column("bar", min_width=25)

    # Disk
    try:
        disk = shutil.disk_usage(str(config.node.data_dir))
        disk_ratio = disk.used / disk.total if disk.total > 0 else 0
        bar = _make_bar(disk_ratio, color="yellow")
        label = f"  {_format_bytes(disk.used)} / {_format_bytes(disk.total)}"
        bar.append(label, style="dim")
        table.add_row("Disk", bar)
    except Exception:  # noqa: BLE001
        table.add_row("Disk", Text("N/A", style="dim"))

    # CPU & RAM (optional psutil)
    try:
        import psutil

        cpu_pct = psutil.cpu_percent(interval=0.1)
        bar = _make_bar(cpu_pct / 100, color="cyan")
        table.add_row("CPU", bar)

        mem = psutil.virtual_memory()
        bar = _make_bar(mem.percent / 100, color="green")
        label = f"  {_format_bytes(mem.used)} / {_format_bytes(mem.total)}"
        bar.append(label, style="dim")
        table.add_row("RAM", bar)
    except ImportError:
        table.add_row("CPU", Text("psutil not installed", style="dim"))
        table.add_row("RAM", Text("psutil not installed", style="dim"))

    return Panel(table, title="[bold]Resources[/]", border_style="cyan")


def _index_section(config: Config) -> Panel:
    """Build the Index panel."""
    table = Table.grid(padding=(0, 2))
    table.add_column("key", style="bold", min_width=14)
    table.add_column("value")

    try:
        from infomesh.index.local_store import LocalStore

        store = LocalStore(
            db_path=config.index.db_path,
            compression_enabled=config.storage.compression_enabled,
            compression_level=config.storage.compression_level,
        )
        stats = store.get_stats()

        table.add_row("Documents", f"{stats['document_count']:,}")

        # DB file size
        db_path = config.index.db_path
        if db_path.exists():
            table.add_row("DB size", _format_bytes(db_path.stat().st_size))

        # Top domains
        rows = store._conn.execute(
            """SELECT
                   SUBSTR(
                       url,
                       INSTR(url, '://') + 3,
                       CASE
                           WHEN INSTR(
                               SUBSTR(url, INSTR(url, '://') + 3), '/'
                           ) > 0
                           THEN INSTR(
                               SUBSTR(url, INSTR(url, '://') + 3), '/'
                           ) - 1
                           ELSE LENGTH(url)
                       END
                   ) AS domain,
                   COUNT(*) as cnt
               FROM documents
               GROUP BY domain
               ORDER BY cnt DESC
               LIMIT 5"""
        ).fetchall()

        if rows:
            domains = ", ".join(f"{r[0]} ({r[1]})" for r in rows)
            table.add_row("Top domains", domains)

        store.close()
    except Exception as exc:  # noqa: BLE001
        table.add_row("Error", str(exc))

    return Panel(table, title="[bold]Index[/]", border_style="cyan")


def _network_section(config: Config) -> Panel:
    """Build the Network panel."""
    table = Table.grid(padding=(0, 2))
    table.add_column("key", style="bold", min_width=14)
    table.add_column("value")

    table.add_row("Port", f"{config.node.listen_port} TCP")
    table.add_row(
        "Bootstrap",
        f"{len(config.network.bootstrap_nodes)} nodes configured",
    )
    table.add_row("Replication", f"{config.network.replication_factor}x")
    table.add_row(
        "Upload limit",
        f"{config.network.upload_limit_mbps:.1f} Mbps",
    )
    table.add_row(
        "Download limit",
        f"{config.network.download_limit_mbps:.1f} Mbps",
    )

    return Panel(table, title="[bold]Network[/]", border_style="cyan")


def _credits_section(config: Config) -> Panel:
    """Build the Credits panel."""
    table = Table.grid(padding=(0, 2))
    table.add_column("key", style="bold", min_width=14)
    table.add_column("value")

    db_path = config.node.data_dir / "credits.db"
    if not db_path.exists():
        table.add_row("Status", "[dim]No credit history yet[/]")
        table.add_row("Hint", "Start crawling to earn credits!")
        return Panel(table, title="[bold]Credits[/]", border_style="cyan")

    try:
        from infomesh.credits.ledger import ContributionTier, CreditLedger

        ledger = CreditLedger(db_path)
        stats = ledger.stats()
        ledger.close()

        tier_map = {
            ContributionTier.TIER_1: "⭐ Tier 1",
            ContributionTier.TIER_2: "⭐⭐ Tier 2",
            ContributionTier.TIER_3: "⭐⭐⭐ Tier 3",
        }
        tier_str = tier_map.get(stats.tier, "Unknown")

        table.add_row("Balance", f"[bold green]{stats.balance:,.2f}[/] credits")
        table.add_row("Tier", tier_str)
        table.add_row("Earned", f"{stats.total_earned:,.2f}")
        table.add_row("Spent", f"{stats.total_spent:,.2f}")
        table.add_row("Search cost", f"{stats.search_cost:.3f}")
        table.add_row("Score", f"{stats.contribution_score:,.2f}")
    except Exception as exc:  # noqa: BLE001
        table.add_row("Error", str(exc))

    return Panel(table, title="[bold]Credits[/]", border_style="cyan")


def print_dashboard(
    config: Config | None = None,
    *,
    tab: str | None = None,
) -> None:
    """Print a Rich-formatted dashboard snapshot to the console.

    Args:
        config: InfoMesh configuration. Defaults to ``load_config()``.
        tab: Specific tab to show (overview, crawl, search, network, credits).
             If None, shows all sections.
    """
    if config is None:
        config = load_config()

    console = Console()

    # Title
    console.print()
    console.rule(
        f"[bold cyan]InfoMesh Dashboard[/] — v{__version__}",
        style="cyan",
    )
    console.print()

    sections = {
        "overview": (_node_section, _resource_section),
        "crawl": (_index_section,),
        "search": (),
        "network": (_network_section,),
        "credits": (_credits_section,),
    }

    if tab and tab in sections:
        # Show specific tab
        builders = sections[tab]
        for builder in builders:
            console.print(builder(config))
            console.print()
    else:
        # Show all
        console.print(_node_section(config))
        console.print()
        console.print(_resource_section(config))
        console.print()
        console.print(_index_section(config))
        console.print()
        console.print(_network_section(config))
        console.print()
        console.print(_credits_section(config))
        console.print()

    console.rule(style="dim")
    console.print(
        "[dim]Tip: Use [bold]infomesh dashboard[/bold] without --text "
        "for interactive TUI mode[/]"
    )
    console.print()
