"""Crawl pane — worker status, top domains, live crawl feed."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from infomesh.config import Config
from infomesh.dashboard.data_cache import DashboardDataCache
from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem
from infomesh.dashboard.widgets.live_log import LiveLog


class CrawlStatsPanel(Static):
    """Crawl worker statistics summary."""

    DEFAULT_CSS = """
    CrawlStatsPanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 4;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._active_workers = 0
        self._queue_size = 0
        self._pages_per_hour = 0
        self._error_rate = 0.0

    def on_mount(self) -> None:
        self._update()

    def update_stats(
        self,
        active_workers: int = 0,
        queue_size: int = 0,
        pages_per_hour: int = 0,
        error_rate: float = 0.0,
    ) -> None:
        self._active_workers = active_workers
        self._queue_size = queue_size
        self._pages_per_hour = pages_per_hour
        self._error_rate = error_rate
        self._update()

    def _update(self) -> None:
        max_workers = self._config.crawl.max_concurrent
        text = (
            f"[bold]Crawl Workers[/bold]\n"
            f"  Workers: {self._active_workers}/{max_workers} active    "
            f"Rate: {self._pages_per_hour} pages/hr\n"
            f"  Queue:   {self._queue_size} pending     "
            f"Errors: {self._error_rate:.1f}%"
        )
        self.update(text)


class TopDomainsPanel(Widget):
    """Displays a bar chart of most-crawled domains."""

    DEFAULT_CSS = """
    TopDomainsPanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 8;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        # Pop data_cache from kwargs so Widget.__init__ doesn't get it
        self._data_cache: DashboardDataCache | None = kwargs.pop("data_cache", None)  # type: ignore[arg-type]
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("[bold]Top Domains[/bold]", classes="panel-title")
        yield BarChart(id="domain-chart")

    def refresh_data(self) -> None:
        """Load domain statistics from the cache or local index."""
        colors = ["cyan", "green", "yellow", "blue", "magenta", "red", "white"]
        try:
            if self._data_cache is not None:
                stats = self._data_cache.get_stats()
                items = [
                    BarItem(
                        label=domain[:20],
                        value=float(cnt),
                        color=colors[i % len(colors)],
                        suffix=" pages",
                    )
                    for i, (domain, cnt) in enumerate(stats.top_domains)
                ]
            else:
                from infomesh.index.local_store import LocalStore

                store = LocalStore(
                    db_path=self._config.index.db_path,
                    compression_enabled=self._config.storage.compression_enabled,
                    compression_level=self._config.storage.compression_level,
                )
                rows = store._conn.execute(
                    """SELECT
                           SUBSTR(
                               url,
                               INSTR(url, '://') + 3,
                               CASE
                                   WHEN INSTR(
                                       SUBSTR(url, INSTR(url, '://') + 3),
                                       '/'
                                   ) > 0
                                   THEN INSTR(
                                       SUBSTR(url, INSTR(url, '://') + 3),
                                       '/'
                                   ) - 1
                                   ELSE LENGTH(url)
                               END
                           ) AS domain,
                           COUNT(*) as cnt
                       FROM documents
                       GROUP BY domain
                       ORDER BY cnt DESC
                       LIMIT 7"""
                ).fetchall()
                items = [
                    BarItem(
                        label=row[0][:20],
                        value=float(row[1]),
                        color=colors[i % len(colors)],
                        suffix=" pages",
                    )
                    for i, row in enumerate(rows)
                ]
                store.close()

            self.query_one("#domain-chart", BarChart).set_items(items)
        except Exception:  # noqa: BLE001
            pass


class CrawlPane(Widget):
    """Main crawl monitoring pane."""

    DEFAULT_CSS = """
    CrawlPane {
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
            yield CrawlStatsPanel(self._config, id="crawl-stats")
            yield TopDomainsPanel(
                self._config, data_cache=self._data_cache, id="top-domains"
            )
            yield Static("[bold]Live Feed[/bold]", classes="panel-title")
            yield LiveLog(id="crawl-feed")

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(0.5, self._tick)
        self._load_initial()

    def _load_initial(self) -> None:
        """Load initial crawl data."""
        import contextlib

        with contextlib.suppress(Exception):
            self.query_one("#top-domains", TopDomainsPanel).refresh_data()

        try:
            log = self.query_one("#crawl-feed", LiveLog)
            log.log_event("Crawl feed started", style="bold cyan")
        except Exception:  # noqa: BLE001
            pass

    def _tick(self) -> None:
        """Periodic refresh."""
        import contextlib

        with contextlib.suppress(Exception):
            self.query_one("#top-domains", TopDomainsPanel).refresh_data()

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._tick()
