"""Network pane — P2P status, connected peers, bandwidth."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Static

from infomesh.config import Config
from infomesh.dashboard.widgets.sparkline import SparklineChart


class P2PStatusPanel(Static):
    """P2P network status summary."""

    DEFAULT_CSS = """
    P2PStatusPanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 6;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config

    def on_mount(self) -> None:
        self._update()

    def _update(self) -> None:
        port = self._config.node.listen_port
        bootstrap = len(self._config.network.bootstrap_nodes)

        # Try to get live node info
        state = "🔴 Offline"
        peers = 0

        text = (
            f"[bold]P2P Status[/bold]\n"
            f"  State:      {state}\n"
            f"  Peers:      {peers} connected\n"
            f"  Bootstrap:  {bootstrap} nodes configured\n"
            f"  Port:       {port} TCP\n"
            f"  Replication: {self._config.network.replication_factor}x"
        )
        self.update(text)

    def refresh_data(self) -> None:
        self._update()


class DHTPanel(Static):
    """DHT statistics panel."""

    DEFAULT_CSS = """
    DHTPanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 6;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._keys_stored = 0
        self._lookups_hr = 0
        self._publications = 0

    def on_mount(self) -> None:
        self._update()

    def update_stats(
        self,
        keys_stored: int = 0,
        lookups_hr: int = 0,
        publications: int = 0,
    ) -> None:
        self._keys_stored = keys_stored
        self._lookups_hr = lookups_hr
        self._publications = publications
        self._update()

    def _update(self) -> None:
        text = (
            f"[bold]DHT[/bold]\n"
            f"  Keys stored:    {self._keys_stored:,}\n"
            f"  Lookups/hr:     {self._lookups_hr:,}\n"
            f"  Publications:   {self._publications:,}"
        )
        self.update(text)

    def refresh_data(self) -> None:
        self._update()


class PeerTable(Widget):
    """Table showing connected peers."""

    DEFAULT_CSS = """
    PeerTable {
        border: round $accent;
        height: auto;
        min-height: 6;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("[bold]Connected Peers[/bold]", classes="panel-title")
        table: DataTable[str] = DataTable(id="peers-table")
        table.add_columns("Peer ID", "Latency", "Trust", "State")
        yield table

    def set_peers(self, peers: list[dict[str, object]]) -> None:
        """Update the peer table with current data."""
        try:
            table = self.query_one("#peers-table", DataTable)
            table.clear()
            for peer in peers:
                peer_id = str(peer.get("peer_id", ""))
                short_id = peer_id[:12] + "..." if len(peer_id) > 12 else peer_id
                latency = peer.get("latency_ms", "—")
                trust = peer.get("trust", "—")
                state = peer.get("state", "unknown")

                latency_str = (
                    f"{latency}ms"
                    if isinstance(latency, (int, float))
                    else str(latency)
                )
                trust_str = f"{trust:.2f}" if isinstance(trust, float) else str(trust)

                table.add_row(short_id, latency_str, trust_str, str(state))
        except Exception:  # noqa: BLE001
            pass

    def refresh_data(self) -> None:
        """Clear and show empty state when no peers available."""
        try:
            table = self.query_one("#peers-table", DataTable)
            if table.row_count == 0:
                pass  # Table stays empty, which is fine
        except Exception:  # noqa: BLE001
            pass


class BandwidthPanel(Widget):
    """Bandwidth usage with sparklines."""

    DEFAULT_CSS = """
    BandwidthPanel {
        border: round $accent;
        padding: 1;
        height: auto;
        min-height: 5;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("[bold]Bandwidth[/bold]", classes="panel-title")
        with Horizontal(classes="bandwidth-row"):
            yield Static("Upload:   ", classes="bw-label")
            yield SparklineChart(color="blue", id="bw-upload-spark")
            yield Static(
                f"0.0/{self._config.network.upload_limit_mbps:.1f} Mbps",
                id="bw-upload-val",
                classes="bw-value",
            )
        with Horizontal(classes="bandwidth-row"):
            yield Static("Download: ", classes="bw-label")
            yield SparklineChart(color="magenta", id="bw-download-spark")
            yield Static(
                f"0.0/{self._config.network.download_limit_mbps:.1f} Mbps",
                id="bw-download-val",
                classes="bw-value",
            )

    def update_bandwidth(self, upload_mbps: float, download_mbps: float) -> None:
        """Update bandwidth display and sparklines."""
        try:
            up_spark = self.query_one("#bw-upload-spark", SparklineChart)
            up_spark.push_value(upload_mbps)
            dn_spark = self.query_one("#bw-download-spark", SparklineChart)
            dn_spark.push_value(download_mbps)
            up_limit = self._config.network.upload_limit_mbps
            dn_limit = self._config.network.download_limit_mbps
            self.query_one("#bw-upload-val", Static).update(
                f"{upload_mbps:.1f}/{up_limit:.1f} Mbps"
            )
            self.query_one("#bw-download-val", Static).update(
                f"{download_mbps:.1f}/{dn_limit:.1f} Mbps"
            )
        except Exception:  # noqa: BLE001
            pass


class NetworkPane(Widget):
    """Main network monitoring pane."""

    DEFAULT_CSS = """
    NetworkPane {
        height: 1fr;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Horizontal(id="network-top"):
                yield P2PStatusPanel(self._config, id="p2p-status")
                yield DHTPanel(id="dht-status")
            yield PeerTable(id="peer-table")
            yield BandwidthPanel(self._config, id="bandwidth")

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(2.0, self._tick)

    def _tick(self) -> None:
        """Periodic refresh."""
        import contextlib

        with contextlib.suppress(Exception):
            self.query_one("#p2p-status", P2PStatusPanel).refresh_data()

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._tick()
