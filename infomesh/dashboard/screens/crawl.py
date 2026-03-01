"""Crawl pane — worker status, top domains, live crawl feed."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from infomesh.config import Config
from infomesh.dashboard.data_cache import DashboardDataCache
from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem
from infomesh.dashboard.widgets.live_log import LiveLog


def _fmt_elapsed(epoch: float) -> str:
    """Format time elapsed since *epoch* as a human-readable string."""
    if epoch <= 0:
        return "never"
    delta = time.time() - epoch
    if delta < 0:
        return "just now"
    if delta < 60:
        return f"{int(delta)}s ago"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


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
        super().__init__("", **kwargs)  # type: ignore[arg-type]
        self._config = config
        self._total_pages = 0
        self._pages_per_hour = 0
        self._domain_count = 0
        self._last_crawl_at = 0.0
        self._countdown = 0

    def on_mount(self) -> None:
        self._refresh_content()

    def update_stats(
        self,
        total_pages: int = 0,
        pages_per_hour: int = 0,
        domain_count: int = 0,
        last_crawl_at: float = 0.0,
        countdown: int = 0,
    ) -> None:
        self._total_pages = total_pages
        self._pages_per_hour = pages_per_hour
        self._domain_count = domain_count
        self._last_crawl_at = last_crawl_at
        self._countdown = countdown
        self._refresh_content()

    def _refresh_content(self) -> None:
        last_str = _fmt_elapsed(self._last_crawl_at)
        refresh_str = (
            f"  [dim]↻ {self._countdown}s[/dim]" if self._countdown > 0 else ""
        )
        text = (
            f"[bold]Crawl Stats[/bold]{refresh_str}\n"
            f"  Pages: {self._total_pages:,}    "
            f"Rate: {self._pages_per_hour:,} pages/hr\n"
            f"  Domains: {self._domain_count:,}    "
            f"Last crawl: {last_str}"
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
        self._data_cache: DashboardDataCache | None = kwargs.pop("data_cache", None)  # type: ignore[assignment]
        super().__init__(**kwargs)  # type: ignore[arg-type]
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
                top_domains = store.get_top_domains(limit=7)
                items = [
                    BarItem(
                        label=d[:20],
                        value=float(c),
                        color=colors[i % len(colors)],
                        suffix=" pages",
                    )
                    for i, (d, c) in enumerate(top_domains)
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

    # Interval for DB refresh (seconds). Tick runs faster for countdown.
    _REFRESH_INTERVAL = 5.0
    _TICK_INTERVAL = 1.0

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
        self._last_refresh: float = 0.0

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield CrawlStatsPanel(self._config, id="crawl-stats")
            yield TopDomainsPanel(
                self._config, data_cache=self._data_cache, id="top-domains"
            )
            yield Static("[bold]Live Feed[/bold]", classes="panel-title")
            yield LiveLog(id="crawl-feed")

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(self._TICK_INTERVAL, self._tick)
        self._load_initial()

    def _load_initial(self) -> None:
        """Load initial crawl data."""
        import contextlib

        with contextlib.suppress(Exception):
            self.query_one("#top-domains", TopDomainsPanel).refresh_data()

        self._refresh_from_db()

        try:
            log = self.query_one("#crawl-feed", LiveLog)
            log.log_event("Crawl feed started", style="bold cyan")
        except Exception:  # noqa: BLE001
            pass

    def _refresh_from_db(self) -> None:
        """Refresh crawl stats from the data cache / DB."""
        import contextlib

        self._last_refresh = time.monotonic()

        with contextlib.suppress(Exception):
            self.query_one("#top-domains", TopDomainsPanel).refresh_data()

        try:
            panel = self.query_one("#crawl-stats", CrawlStatsPanel)
            if self._data_cache is not None:
                stats = self._data_cache.get_stats()
                panel.update_stats(
                    total_pages=stats.document_count,
                    pages_per_hour=stats.pages_last_hour,
                    domain_count=stats.domain_count,
                    last_crawl_at=stats.last_crawl_at,
                    countdown=int(self._REFRESH_INTERVAL),
                )
            else:
                # Fallback: direct DB query
                with contextlib.suppress(Exception):
                    from infomesh.index.local_store import LocalStore

                    store = LocalStore(
                        db_path=self._config.index.db_path,
                        compression_enabled=self._config.storage.compression_enabled,
                        compression_level=self._config.storage.compression_level,
                    )
                    db_stats = store.get_stats()
                    total = db_stats["document_count"]
                    panel.update_stats(
                        total_pages=total,
                        countdown=int(self._REFRESH_INTERVAL),
                    )
                    store.close()
        except Exception:  # noqa: BLE001
            pass

    def _tick(self) -> None:
        """Periodic tick — refreshes DB data on interval, updates countdown."""
        import contextlib

        elapsed = time.monotonic() - self._last_refresh
        remaining = max(0, int(self._REFRESH_INTERVAL - elapsed))

        if elapsed >= self._REFRESH_INTERVAL:
            self._refresh_from_db()
        else:
            # Just update countdown on CrawlStatsPanel
            with contextlib.suppress(Exception):
                self.query_one("#crawl-stats", CrawlStatsPanel).update_stats(
                    countdown=remaining
                )

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._refresh_from_db()
