"""Tests for the InfoMesh console dashboard."""

from __future__ import annotations

from pathlib import Path

import pytest

from infomesh.config import (
    Config,
    CrawlConfig,
    IndexConfig,
    LLMConfig,
    NetworkConfig,
    NodeConfig,
    StorageConfig,
)

# ─── Fixtures ──────────────────────────────────────────


@pytest.fixture
def tmp_config(tmp_path: Path) -> Config:
    """Create a test config with temp directories."""
    data_dir = tmp_path / ".infomesh"
    data_dir.mkdir()
    db_path = data_dir / "index.db"
    return Config(
        node=NodeConfig(data_dir=data_dir),
        crawl=CrawlConfig(),
        network=NetworkConfig(),
        index=IndexConfig(db_path=db_path),
        llm=LLMConfig(),
        storage=StorageConfig(compression_enabled=False),
    )


@pytest.fixture
def store_with_docs(tmp_config: Config):
    """Create a local store with some sample documents."""
    from infomesh.index.local_store import LocalStore

    store = LocalStore(
        db_path=tmp_config.index.db_path,
        compression_enabled=False,
    )
    # Add sample documents
    docs = [
        (
            "https://docs.python.org/3/tutorial/",
            "Python Tutorial",
            "Learn Python programming",
            "py",
        ),
        (
            "https://en.wikipedia.org/wiki/P2P",
            "Peer-to-peer",
            "Peer-to-peer computing overview",
            "en",
        ),
        (
            "https://developer.mozilla.org/en/JS",
            "JavaScript Guide",
            "JavaScript reference guide",
            "en",
        ),
        (
            "https://arxiv.org/abs/2401.01234",
            "Machine Learning Paper",
            "Deep learning research paper",
            "en",
        ),
        (
            "https://docs.python.org/3/asyncio/",
            "Python Asyncio",
            "Asynchronous I/O support",
            "py",
        ),
    ]
    for url, title, text, lang in docs:
        import hashlib

        h = hashlib.sha256(text.encode()).hexdigest()
        store.add_document(
            url=url,
            title=title,
            text=text,
            raw_html_hash=h,
            text_hash=h + url[:8],
            language=lang,
        )
    yield store
    store.close()


@pytest.fixture
def ledger_with_data(tmp_config: Config):
    """Create a credit ledger with sample entries."""
    from infomesh.credits.ledger import ActionType, CreditLedger

    db_path = tmp_config.node.data_dir / "credits.db"
    ledger = CreditLedger(db_path)

    # Record various actions
    ledger.record_action(ActionType.CRAWL, quantity=50, note="batch crawl")
    ledger.record_action(ActionType.NETWORK_UPTIME, quantity=24, note="24h uptime")
    ledger.record_action(ActionType.QUERY_PROCESS, quantity=30, note="search queries")
    ledger.record_action(ActionType.LLM_SUMMARIZE_OWN, quantity=10, note="summaries")
    ledger.record_action(ActionType.DOC_HOSTING, quantity=100, note="hosting")

    # Spend some credits
    ledger.spend(5.0, reason="search")

    yield ledger
    ledger.close()


# ─── Widget Tests ──────────────────────────────────────


class TestSparklineChart:
    """Tests for SparklineChart widget."""

    def test_create_empty(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart()
        assert chart.data == []

    def test_create_with_data(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        chart = SparklineChart(data=data, color="cyan")
        assert chart.data == data
        assert chart.color == "cyan"

    def test_push_value(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart()
        chart.push_value(1.0, max_points=5)
        chart.push_value(2.0, max_points=5)
        chart.push_value(3.0, max_points=5)
        assert len(chart.data) == 3

    def test_push_value_trims(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart()
        for i in range(10):
            chart.push_value(float(i), max_points=5)
        assert len(chart.data) == 5
        assert chart.data == [5.0, 6.0, 7.0, 8.0, 9.0]

    def test_render_empty(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart()
        result = chart.render()
        text = str(result)
        assert "—" in text or "no data" in text

    def test_render_with_data(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart(data=[0, 5, 10])
        result = chart.render()
        text = str(result)
        # Should contain block characters
        assert len(text) > 0


class TestBarChart:
    """Tests for BarChart widget."""

    def test_create_empty(self) -> None:
        from infomesh.dashboard.widgets.bar_chart import BarChart

        chart = BarChart()
        result = chart.render()
        assert "No data" in str(result)

    def test_create_with_items(self) -> None:
        from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem

        items = [
            BarItem("Crawling", 100, color="cyan"),
            BarItem("Uptime", 50, color="green"),
        ]
        chart = BarChart(items=items)
        assert len(chart._items) == 2

    def test_set_items(self) -> None:
        from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem

        chart = BarChart()
        items = [BarItem("Test", 42)]
        chart.set_items(items)
        assert len(chart._items) == 1

    def test_render_with_items(self) -> None:
        from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem

        items = [
            BarItem("Crawling", 100, color="cyan"),
            BarItem("Uptime", 50, color="green"),
        ]
        chart = BarChart(items=items, bar_width=10)
        result = chart.render()
        # Should be a Rich Table
        from rich.table import Table

        assert isinstance(result, Table)


class TestResourceBar:
    """Tests for ResourceBar widget."""

    def test_create_default(self) -> None:
        from infomesh.dashboard.widgets.resource_bar import ResourceBar

        bar = ResourceBar("CPU", 50, 100)
        result = bar.render()
        text = str(result)
        assert "CPU" in text
        assert "50%" in text

    def test_high_usage_color(self) -> None:
        from infomesh.dashboard.widgets.resource_bar import ResourceBar

        bar = ResourceBar("RAM", 95, 100, color="green")
        result = bar.render()
        # At 95%, color should switch to red
        # We just check the render doesn't crash
        assert "RAM" in str(result)

    def test_update_value(self) -> None:
        from infomesh.dashboard.widgets.resource_bar import ResourceBar

        bar = ResourceBar("Disk", 20, 100)
        bar.update_value(80)
        result = bar.render()
        assert "80%" in str(result)

    def test_custom_unit(self) -> None:
        from infomesh.dashboard.widgets.resource_bar import ResourceBar

        bar = ResourceBar("Net↑", 2.5, 5.0, unit="Mbps")
        result = bar.render()
        text = str(result)
        assert "Mbps" in text


class TestLiveLog:
    """Tests for LiveLog widget."""

    def test_create(self) -> None:
        from infomesh.dashboard.widgets.live_log import LiveLog

        log = LiveLog(max_lines=50)
        assert log is not None


# ─── Screen Component Tests ───────────────────────────


class TestOverviewHelpers:
    """Tests for overview helper functions."""

    def test_format_uptime_zero(self) -> None:
        from infomesh.dashboard.utils import format_uptime

        assert format_uptime(0) == "—"

    def test_format_uptime_minutes(self) -> None:
        from infomesh.dashboard.utils import format_uptime

        result = format_uptime(300)  # 5 minutes
        assert "5m" in result

    def test_format_uptime_hours(self) -> None:
        from infomesh.dashboard.utils import format_uptime

        result = format_uptime(7200)  # 2 hours
        assert "2h" in result

    def test_format_uptime_days(self) -> None:
        from infomesh.dashboard.utils import format_uptime

        result = format_uptime(3 * 86400 + 14 * 3600 + 22 * 60)
        assert "3d" in result
        assert "14h" in result
        assert "22m" in result

    def test_is_node_running_no_pid(self, tmp_config: Config) -> None:
        from infomesh.dashboard.utils import is_node_running

        assert is_node_running(tmp_config) is False

    def test_is_node_running_stale_pid(self, tmp_config: Config) -> None:
        from infomesh.dashboard.utils import is_node_running

        pid_file = tmp_config.node.data_dir / "infomesh.pid"
        pid_file.write_text("999999999")  # Non-existent PID
        assert is_node_running(tmp_config) is False

    def test_get_peer_id_no_keys(self, tmp_config: Config) -> None:
        from infomesh.dashboard.utils import get_peer_id

        assert get_peer_id(tmp_config) == "(not generated)"


# ─── Search Pane Tests ─────────────────────────────────


class TestSearchResultsPanel:
    """Tests for SearchResultsPanel rendering logic."""

    def test_display_empty_results(self, tmp_config: Config) -> None:
        from infomesh.dashboard.screens.search import SearchResultsPanel

        panel = SearchResultsPanel(tmp_config)
        panel.display_results("test", [], 5.0)
        # Should show "No results found"

    def test_display_results_with_data(self, tmp_config: Config) -> None:
        from infomesh.dashboard.screens.search import SearchResultsPanel

        panel = SearchResultsPanel(tmp_config)
        results = [
            {
                "title": "Test Doc",
                "url": "https://example.com",
                "score": 1.234,
                "snippet": "This is a test snippet",
                "bm25": 1.234,
            }
        ]
        panel.display_results("test", results, 5.0, source="local")
        # Panel should now contain result text

    def test_display_error(self, tmp_config: Config) -> None:
        from infomesh.dashboard.screens.search import SearchResultsPanel

        panel = SearchResultsPanel(tmp_config)
        panel.display_error("Something went wrong")
        # Should render error message


# ─── Credits Tests ─────────────────────────────────────


class TestCreditsHelpers:
    """Tests for credits pane data loading."""

    def test_balance_no_db(self, tmp_config: Config) -> None:
        """BalancePanel should handle missing credit DB gracefully."""
        from infomesh.dashboard.screens.credits import BalancePanel

        BalancePanel(tmp_config)  # Should not raise

    def test_ledger_data_available(self, tmp_config: Config, ledger_with_data) -> None:
        """Verify the fixture creates usable ledger data."""
        stats = ledger_with_data.stats()
        assert stats.total_earned > 0
        assert stats.balance > 0


# ─── Network Panel Tests ──────────────────────────────


class TestNetworkPanels:
    """Tests for network pane components."""

    def test_dht_panel_render(self) -> None:
        from infomesh.dashboard.screens.network import DHTPanel

        panel = DHTPanel()
        dht_data = {
            "keys_stored": 100,
            "keys_published": 25,
            "gets_performed": 50,
            "puts_performed": 30,
        }
        panel._refresh_content(dht_data)
        # The panel should accept any dict without error

    def test_peer_table_set_peers(self, tmp_config: Config) -> None:
        from infomesh.dashboard.screens.network import PeerTable

        table = PeerTable()
        # set_peers with empty list should not raise
        table.set_peers([])

    def test_bandwidth_panel_update(self, tmp_config: Config) -> None:
        from infomesh.dashboard.screens.network import BandwidthPanel

        panel = BandwidthPanel(tmp_config)
        # update_from_status should initialise counters without error
        panel.update_from_status({"upload_bytes": 1000, "download_bytes": 2000})
        assert panel._prev_up == 1000
        assert panel._prev_dn == 2000


# ─── Bar Chart Data Tests ─────────────────────────────


class TestBarItemPercentage:
    """Tests for bar chart percentage calculations."""

    def test_proportional_bars(self) -> None:
        from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem

        items = [
            BarItem("A", 100),
            BarItem("B", 200),
            BarItem("C", 300),
        ]
        chart = BarChart(items=items, bar_width=10)
        result = chart.render()
        # Render should produce a Rich table
        from rich.table import Table

        assert isinstance(result, Table)

    def test_single_item(self) -> None:
        from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem

        items = [BarItem("Only", 42)]
        chart = BarChart(items=items)
        result = chart.render()
        from rich.table import Table

        assert isinstance(result, Table)

    def test_zero_values(self) -> None:
        from infomesh.dashboard.widgets.bar_chart import BarChart, BarItem

        items = [BarItem("Zero", 0)]
        chart = BarChart(items=items)
        # Should not raise
        result = chart.render()
        assert result is not None


# ─── Dashboard App Tests ──────────────────────────────


class TestDashboardApp:
    """Tests for the main DashboardApp."""

    def test_create_app(self, tmp_config: Config) -> None:
        from infomesh.dashboard.app import DashboardApp

        app = DashboardApp(config=tmp_config)
        assert app.config == tmp_config
        assert app._initial_tab == "overview"

    def test_create_app_custom_tab(self, tmp_config: Config) -> None:
        from infomesh.dashboard.app import DashboardApp

        app = DashboardApp(config=tmp_config, initial_tab="credits")
        assert app._initial_tab == "credits"

    def test_css_file_exists(self) -> None:
        from infomesh.dashboard.app import _CSS_PATH

        assert _CSS_PATH.exists(), f"CSS file not found: {_CSS_PATH}"


# ─── Crawl Panel Tests ────────────────────────────────


class TestCrawlStatsPanel:
    """Tests for CrawlStatsPanel."""

    def test_update_stats(self, tmp_config: Config) -> None:
        from infomesh.dashboard.screens.crawl import CrawlStatsPanel

        panel = CrawlStatsPanel(tmp_config)
        panel.update_stats(
            total_pages=42,
            pages_per_hour=100,
            domain_count=5,
            last_crawl_at=1700000000.0,
            countdown=3,
        )
        assert panel._total_pages == 42
        assert panel._pages_per_hour == 100
        assert panel._domain_count == 5
        assert panel._countdown == 3

    def test_top_domains_with_data(self, tmp_config: Config, store_with_docs) -> None:
        """TopDomainsPanel should load domain stats from index."""
        from infomesh.dashboard.screens.crawl import TopDomainsPanel

        panel = TopDomainsPanel(tmp_config)
        # refresh_data queries the store, should not raise
        panel.refresh_data()


# ─── CLI Entry Point Test ────────────────────────────


class TestCLIDashboard:
    """Test the CLI dashboard command registration."""

    def test_dashboard_command_exists(self) -> None:
        """Verify the dashboard command is registered in CLI."""
        from infomesh.cli import cli

        commands = cli.list_commands(ctx=None)  # type: ignore[arg-type]
        assert "dashboard" in commands


# ─── Integration: Sparkline edge cases ─────────────────


class TestSparklineEdgeCases:
    """Edge cases for sparkline rendering."""

    def test_all_same_values(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart(data=[5.0, 5.0, 5.0, 5.0])
        result = chart.render()
        # All same → all should map to same block char
        assert len(str(result)) == 4

    def test_single_value(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart(data=[42.0])
        result = chart.render()
        assert len(str(result)) == 1

    def test_negative_values(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        chart = SparklineChart(data=[-10, -5, 0, 5, 10])
        result = chart.render()
        assert len(str(result)) == 5

    def test_large_dataset(self) -> None:
        from infomesh.dashboard.widgets.sparkline import SparklineChart

        data = [float(i) for i in range(100)]
        chart = SparklineChart(data=data)
        result = chart.render()
        assert len(str(result)) == 100


# ─── Text Report Tests ────────────────────────────────


class TestTextReport:
    """Tests for the --text dashboard mode (Rich console output)."""

    def test_print_dashboard_all(self, tmp_config: Config) -> None:
        """print_dashboard() runs without errors and produces output."""
        from io import StringIO

        from rich.console import Console

        from infomesh.dashboard.text_report import print_dashboard

        buf = StringIO()
        console = Console(file=buf, width=80, force_terminal=True)
        # Monkeypatch Console inside text_report
        import infomesh.dashboard.text_report as tr

        orig = tr.Console
        tr.Console = lambda **_kw: console  # type: ignore[assignment]
        try:
            print_dashboard(config=tmp_config)
        finally:
            tr.Console = orig  # type: ignore[assignment]

        output = buf.getvalue()
        assert "InfoMesh Dashboard" in output
        assert "Node" in output
        assert "Resources" in output
        assert "Index" in output
        assert "Network" in output
        assert "Credits" in output

    def test_print_dashboard_specific_tab(self, tmp_config: Config) -> None:
        """print_dashboard(tab='network') shows only network section."""
        from io import StringIO

        from rich.console import Console

        from infomesh.dashboard.text_report import print_dashboard

        buf = StringIO()
        console = Console(file=buf, width=80, force_terminal=True)
        import infomesh.dashboard.text_report as tr

        orig = tr.Console
        tr.Console = lambda **_kw: console  # type: ignore[assignment]
        try:
            print_dashboard(config=tmp_config, tab="network")
        finally:
            tr.Console = orig  # type: ignore[assignment]

        output = buf.getvalue()
        assert "Network" in output
        # Should NOT include other tabs' panels
        assert "Credits" not in output

    def test_node_section(self, tmp_config: Config) -> None:
        """_node_section returns a Panel with node info."""
        from infomesh.dashboard.text_report import _node_section

        panel = _node_section(tmp_config)
        assert panel.title is not None

    def test_resource_section(self, tmp_config: Config) -> None:
        """_resource_section returns a Panel with Disk bar at minimum."""
        from rich.console import Console

        from infomesh.dashboard.text_report import _resource_section

        panel = _resource_section(tmp_config)
        buf = __import__("io").StringIO()
        Console(file=buf, width=80, force_terminal=True).print(panel)
        output = buf.getvalue()
        assert "Disk" in output

    def test_index_section_empty(self, tmp_config: Config) -> None:
        """_index_section with empty DB shows 0 documents."""
        from rich.console import Console

        from infomesh.dashboard.text_report import _index_section

        panel = _index_section(tmp_config)
        buf = __import__("io").StringIO()
        Console(file=buf, width=80, force_terminal=True).print(panel)
        output = buf.getvalue()
        assert "Documents" in output

    def test_index_section_with_data(
        self, tmp_config: Config, store_with_docs: object
    ) -> None:
        """_index_section with data shows document count and domains."""
        from rich.console import Console

        from infomesh.dashboard.text_report import _index_section

        panel = _index_section(tmp_config)
        buf = __import__("io").StringIO()
        Console(file=buf, width=80, force_terminal=True).print(panel)
        output = buf.getvalue()
        assert "Documents" in output
        # Should show non-zero count
        assert "5" in output or "docs.python.org" in output

    def test_credits_section_no_db(self, tmp_config: Config) -> None:
        """_credits_section without credits.db shows 'no history'."""
        from rich.console import Console

        from infomesh.dashboard.text_report import _credits_section

        panel = _credits_section(tmp_config)
        buf = __import__("io").StringIO()
        Console(file=buf, width=80, force_terminal=True).print(panel)
        output = buf.getvalue()
        assert "No credit history" in output or "credits" in output.lower()

    def test_credits_section_with_data(
        self, tmp_config: Config, ledger_with_data: object
    ) -> None:
        """_credits_section with data shows balance and tier."""
        from rich.console import Console

        from infomesh.dashboard.text_report import _credits_section

        panel = _credits_section(tmp_config)
        buf = __import__("io").StringIO()
        Console(file=buf, width=80, force_terminal=True).print(panel)
        output = buf.getvalue()
        assert "Balance" in output
        assert "Tier" in output

    def test_format_uptime(self) -> None:
        from infomesh.dashboard.utils import format_uptime

        assert format_uptime(0) == "—"
        assert format_uptime(3660) == "1h 1m"
        assert format_uptime(90000) == "1d 1h 0m"

    def test_format_bytes(self) -> None:
        from infomesh.dashboard.utils import format_bytes

        assert "KB" in format_bytes(1024)
        assert "MB" in format_bytes(1024 * 1024)
        assert "GB" in format_bytes(1024**3)

    def test_make_bar(self) -> None:
        from infomesh.dashboard.text_report import _make_bar

        bar = _make_bar(0.5, width=10)
        text = str(bar)
        assert "█" in text
        assert "50%" in text

    def test_make_bar_high_usage(self) -> None:
        """Bar color changes for high usage."""
        from infomesh.dashboard.text_report import _make_bar

        bar = _make_bar(0.95, width=10)
        text = str(bar)
        assert "95%" in text
