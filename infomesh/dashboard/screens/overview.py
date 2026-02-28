"""Overview pane — node status, resource usage, activity sparklines, events."""

from __future__ import annotations

import os
import time

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from infomesh import __version__
from infomesh.config import Config
from infomesh.dashboard.data_cache import DashboardDataCache
from infomesh.dashboard.widgets.live_log import LiveLog
from infomesh.dashboard.widgets.resource_bar import ResourceBar
from infomesh.dashboard.widgets.sparkline import SparklineChart


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


def _is_node_running(config: Config) -> bool:
    """Check if the InfoMesh node process is running."""
    pid_file = config.node.data_dir / "infomesh.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        return False


class NodeInfoPanel(Static):
    """Displays node identity and state information."""

    DEFAULT_CSS = """
    NodeInfoPanel {
        border: round $accent;
        padding: 1;
        height: auto;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def on_mount(self) -> None:
        self._update()

    def _update(self) -> None:
        peer_id = _get_peer_id(self._config)
        running = _is_node_running(self._config)
        state_icon = "🟢 Running" if running else "🔴 Stopped"

        # Calculate uptime from PID file modification time
        pid_file = self._config.node.data_dir / "infomesh.pid"
        uptime = 0.0
        if running and pid_file.exists():
            uptime = time.time() - pid_file.stat().st_mtime

        short_id = peer_id[:16] + "..." if len(peer_id) > 16 else peer_id
        text = (
            f"[bold]Node[/bold]\n"
            f"  Peer ID:  {short_id}\n"
            f"  State:    {state_icon}\n"
            f"  Uptime:   {_format_uptime(uptime)}\n"
            f"  Version:  {__version__}\n"
            f"  Data dir: {self._config.node.data_dir}"
        )
        self.update(text)

    def refresh_data(self) -> None:
        self._update()


class ResourcePanel(Widget):
    """Displays CPU, RAM, Disk, and Network usage bars."""

    DEFAULT_CSS = """
    ResourcePanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 8;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("[bold]Resources[/bold]", classes="panel-title")
        yield ResourceBar("CPU", 0, 100, color="cyan", id="res-cpu")
        yield ResourceBar("RAM", 0, 100, color="green", id="res-ram")
        yield ResourceBar("Disk", 0, 100, color="yellow", id="res-disk")
        yield ResourceBar(
            "Net↑",
            0,
            self._config.network.upload_limit_mbps,
            unit="Mbps",
            color="blue",
            id="res-net-up",
        )
        yield ResourceBar(
            "Net↓",
            0,
            self._config.network.download_limit_mbps,
            unit="Mbps",
            color="magenta",
            id="res-net-down",
        )

    def refresh_data(self) -> None:
        """Update resource bars with current system metrics."""
        try:
            import shutil

            # Disk usage
            disk = shutil.disk_usage(str(self._config.node.data_dir))
            disk_pct = (disk.used / disk.total) * 100 if disk.total > 0 else 0
            self.query_one("#res-disk", ResourceBar).update_value(disk_pct, 100)
        except Exception:  # noqa: BLE001
            pass

        # CPU and RAM require psutil (optional)
        try:
            import psutil

            cpu = psutil.cpu_percent(interval=0)
            self.query_one("#res-cpu", ResourceBar).update_value(cpu, 100)
            mem = psutil.virtual_memory()
            self.query_one("#res-ram", ResourceBar).update_value(mem.percent, 100)
        except ImportError:
            # psutil not available — show N/A
            pass


class ActivityPanel(Widget):
    """Displays activity counters with sparkline charts."""

    DEFAULT_CSS = """
    ActivityPanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 7;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._crawl_count = 0
        self._index_count = 0
        self._search_count = 0

    def compose(self) -> ComposeResult:
        yield Static("[bold]Activity (last 1h)[/bold]", classes="panel-title")

        with Horizontal(classes="activity-row"):
            yield Static("Crawled:  ", classes="activity-label")
            yield Static("0 pages", id="act-crawl-count", classes="activity-value")
            yield SparklineChart(color="green", id="spark-crawl")

        with Horizontal(classes="activity-row"):
            yield Static("Indexed:  ", classes="activity-label")
            yield Static("0 docs", id="act-index-count", classes="activity-value")
            yield SparklineChart(color="cyan", id="spark-index")

        with Horizontal(classes="activity-row"):
            yield Static("Searches: ", classes="activity-label")
            yield Static("0 queries", id="act-search-count", classes="activity-value")
            yield SparklineChart(color="yellow", id="spark-search")

    def update_crawl(self, count: int) -> None:
        self._crawl_count = count
        try:
            self.query_one("#act-crawl-count", Static).update(f"{count} pages")
            self.query_one("#spark-crawl", SparklineChart).push_value(float(count))
        except Exception:  # noqa: BLE001
            pass

    def update_index(self, count: int) -> None:
        self._index_count = count
        try:
            self.query_one("#act-index-count", Static).update(f"{count} docs")
            self.query_one("#spark-index", SparklineChart).push_value(float(count))
        except Exception:  # noqa: BLE001
            pass

    def update_search(self, count: int) -> None:
        self._search_count = count
        try:
            self.query_one("#act-search-count", Static).update(f"{count} queries")
            self.query_one("#spark-search", SparklineChart).push_value(float(count))
        except Exception:  # noqa: BLE001
            pass


class OverviewPane(Widget):
    """Main overview pane composing all overview sub-panels."""

    DEFAULT_CSS = """
    OverviewPane {
        height: 1fr;
    }
    """

    def __init__(
        self,
        config: Config,
        *,
        data_cache: DashboardDataCache | None = None,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._data_cache = data_cache
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Horizontal(id="overview-top"):
                yield NodeInfoPanel(self._config, id="node-info")
                yield ResourcePanel(self._config, id="resources")
            yield ActivityPanel(self._config, id="activity")
            yield LiveLog(id="events-log")

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(0.5, self._tick)
        self._load_initial_data()

    def _load_initial_data(self) -> None:
        """Load initial data from cache or local store."""
        try:
            if self._data_cache is not None:
                stats = self._data_cache.get_stats()
                act_panel = self.query_one("#activity", ActivityPanel)
                act_panel.update_index(stats.document_count)
            else:
                from infomesh.index.local_store import LocalStore

                store = LocalStore(
                    db_path=self._config.index.db_path,
                    compression_enabled=self._config.storage.compression_enabled,
                    compression_level=self._config.storage.compression_level,
                )
                raw = store.get_stats()
                act_panel = self.query_one("#activity", ActivityPanel)
                act_panel.update_index(raw["document_count"])
                store.close()
        except Exception:  # noqa: BLE001
            pass

        # Log initial event
        try:
            log = self.query_one("#events-log", LiveLog)
            log.log_event("Dashboard started", style="bold green")
        except Exception:  # noqa: BLE001
            pass

    def _tick(self) -> None:
        """Unified periodic refresh (0.5s).

        Resource bars and node info are always cheap.
        DB stats go through the DashboardDataCache which has its own
        internal TTL — so calling it at 0.5 s is safe; it only queries
        the DB when the cache has actually expired (default 1 s).
        """
        try:
            self.query_one("#resources", ResourcePanel).refresh_data()
            self.query_one("#node-info", NodeInfoPanel).refresh_data()
        except Exception:  # noqa: BLE001
            pass

        # Refresh activity counts via cache (cheap if TTL not expired)
        try:
            if self._data_cache is not None:
                stats = self._data_cache.get_stats()
                act_panel = self.query_one("#activity", ActivityPanel)
                act_panel.update_index(stats.document_count)
                act_panel.update_crawl(stats.document_count)
            else:
                from infomesh.index.local_store import LocalStore

                store = LocalStore(
                    db_path=self._config.index.db_path,
                    compression_enabled=self._config.storage.compression_enabled,
                    compression_level=self._config.storage.compression_level,
                )
                raw = store.get_stats()
                act_panel = self.query_one("#activity", ActivityPanel)
                act_panel.update_index(raw["document_count"])
                act_panel.update_crawl(raw["document_count"])
                store.close()
        except Exception:  # noqa: BLE001
            pass

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._tick()
        self._load_initial_data()
