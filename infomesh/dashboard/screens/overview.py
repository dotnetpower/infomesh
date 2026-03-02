"""Overview pane — node status, resource usage, activity sparklines, events."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from infomesh import __version__
from infomesh.config import Config
from infomesh.credits.github_identity import resolve_github_email
from infomesh.dashboard.data_cache import DashboardDataCache, RecentDoc
from infomesh.dashboard.utils import (
    format_uptime,
    get_peer_id,
    is_node_running,
)
from infomesh.dashboard.widgets.live_log import LiveLog
from infomesh.dashboard.widgets.resource_bar import ResourceBar
from infomesh.dashboard.widgets.sparkline import SparklineChart


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
        super().__init__("", **kwargs)  # type: ignore[arg-type]
        self._config = config
        # Resolve GitHub email once (avoids subprocess spawn on every tick)
        self._github_email: str | None = resolve_github_email(self._config)

    def on_mount(self) -> None:
        self._update()

    def _update(self) -> None:
        peer_id = get_peer_id(self._config)
        running = is_node_running(self._config)
        state_icon = "🟢 Running" if running else "🔴 Stopped"

        # Calculate uptime from PID file modification time
        pid_file = self._config.node.data_dir / "infomesh.pid"
        uptime = 0.0
        if running and pid_file.exists():
            uptime = time.time() - pid_file.stat().st_mtime

        short_id = peer_id[:16] + "..." if len(peer_id) > 16 else peer_id

        # GitHub identity (cached from __init__)
        if self._github_email:
            github_line = f"  GitHub:   [green]{self._github_email}[/green]"
        else:
            github_line = "  GitHub:   [dim]not connected[/dim]"

        text = (
            f"[bold]Node[/bold]\n"
            f"  Peer ID:  {short_id}\n"
            f"  State:    {state_icon}\n"
            f"  Uptime:   {format_uptime(uptime)}\n"
            f"  Version:  {__version__}\n"
            f"{github_line}\n"
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
        super().__init__(**kwargs)  # type: ignore[arg-type]
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

        # CPU, RAM, and Network require psutil (optional)
        try:
            import psutil

            # --- CPU & RAM (infomesh process, not system-wide) ---
            proc = psutil.Process()
            cpu = proc.cpu_percent(interval=None)
            mem_info = proc.memory_info()
            total_mem = psutil.virtual_memory().total
            mem_pct = (mem_info.rss / total_mem) * 100 if total_mem > 0 else 0
            self.query_one("#res-cpu", ResourceBar).update_value(cpu, 100)
            self.query_one("#res-ram", ResourceBar).update_value(mem_pct, 100)

            # --- Network usage (system-wide, Mbps) ---
            now = time.monotonic()
            counters = psutil.net_io_counters()
            # Store previous values on the widget instance
            prev_sent = getattr(self, "_net_bytes_sent", 0)
            prev_recv = getattr(self, "_net_bytes_recv", 0)
            prev_time = getattr(self, "_net_last_time", 0.0)

            if prev_time > 0:
                elapsed = now - prev_time
                if elapsed > 0.1:
                    up_mbps = ((counters.bytes_sent - prev_sent) * 8) / (
                        elapsed * 1_000_000
                    )
                    dn_mbps = ((counters.bytes_recv - prev_recv) * 8) / (
                        elapsed * 1_000_000
                    )
                    self.query_one("#res-net-up", ResourceBar).update_value(
                        round(up_mbps, 2)
                    )
                    self.query_one("#res-net-down", ResourceBar).update_value(
                        round(dn_mbps, 2)
                    )

            self._net_bytes_sent = counters.bytes_sent
            self._net_bytes_recv = counters.bytes_recv
            self._net_last_time = now
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
        super().__init__(**kwargs)  # type: ignore[arg-type]
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
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config
        self._data_cache = data_cache
        self._refresh_timer: Timer | None = None
        # Track doc IDs already logged so LiveLog only shows new arrivals
        self._seen_doc_ids: set[int] = set()
        self._last_doc_count: int = 0

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
                self._last_doc_count = stats.document_count
                # Seed seen IDs so initial docs aren't re-logged on next tick
                for doc in stats.recent_docs:
                    self._seen_doc_ids.add(doc.doc_id)
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
                self._last_doc_count = int(raw["document_count"])
                store.close()
        except Exception:  # noqa: BLE001
            pass

        # Log initial event
        try:
            log = self.query_one("#events-log", LiveLog)
            log.log_event("Dashboard started", style="bold green")
            # Show the most recent docs as initial log entries
            if self._data_cache is not None:
                stats = self._data_cache.get_stats()
                # Show last 5 in reverse-chronological order (oldest first)
                for doc in reversed(stats.recent_docs[:5]):
                    log.log_crawl(self._format_doc_line(doc), success=True)
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
                act_panel.update_crawl(stats.pages_last_hour)

                # Push newly crawled docs to LiveLog
                self._push_new_docs_to_log(stats.recent_docs, stats.document_count)
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
                act_panel.update_crawl(0)
                store.close()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _format_doc_line(doc: RecentDoc) -> str:
        """Format a recent doc for log display."""
        title = doc.title[:40] + "…" if len(doc.title) > 40 else doc.title
        if title:
            return f"{doc.url}  ({title})"
        return doc.url

    def _push_new_docs_to_log(
        self,
        recent_docs: list[RecentDoc],
        doc_count: int,
    ) -> None:
        """Detect newly indexed documents and log them to LiveLog."""
        if doc_count <= self._last_doc_count and not recent_docs:
            return

        new_docs = [d for d in recent_docs if d.doc_id not in self._seen_doc_ids]
        if not new_docs:
            self._last_doc_count = doc_count
            return

        try:
            log = self.query_one("#events-log", LiveLog)
            # Show oldest-first so log reads chronologically
            for doc in sorted(new_docs, key=lambda d: d.crawled_at):
                log.log_crawl(self._format_doc_line(doc), success=True)
                self._seen_doc_ids.add(doc.doc_id)
        except Exception:  # noqa: BLE001
            pass

        # Prevent unbounded growth — keep only the newest 500 IDs
        if len(self._seen_doc_ids) > 500:
            sorted_ids = sorted(self._seen_doc_ids)
            self._seen_doc_ids = set(sorted_ids[-300:])

        self._last_doc_count = doc_count

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._tick()
        self._load_initial_data()
