"""Network pane — P2P status, connected peers, bandwidth."""

from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import DataTable, Static

from infomesh.config import Config
from infomesh.dashboard.utils import read_p2p_status
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
        super().__init__("", **kwargs)  # type: ignore[arg-type]
        self._config = config
        self._countdown = 0

    def on_mount(self) -> None:
        self._refresh_content({})

    def update_status(self, data: dict[str, object], countdown: int = 0) -> None:
        """Public API: update panel with new status data and countdown."""
        self._countdown = countdown
        self._refresh_content(data)

    def _refresh_content(self, data: dict[str, object]) -> None:
        port = self._config.node.listen_port
        bootstrap = len(self._config.network.bootstrap_nodes)

        raw_state = str(data.get("state", ""))
        raw_peers = data.get("peers", 0)
        peers = int(raw_peers) if isinstance(raw_peers, (int, float, str)) else 0
        peer_id = str(data.get("peer_id", ""))

        # Fallback: read peer_id from key file if not in P2P status
        if not peer_id:
            from infomesh.dashboard.utils import get_peer_id

            peer_id = get_peer_id(self._config)

        # Fallback: try reading stale p2p_status.json for peer_id
        if not peer_id or peer_id == "(not generated)":
            import contextlib
            import json

            status_path = self._config.node.data_dir / "p2p_status.json"
            with contextlib.suppress(Exception):
                if status_path.exists():
                    stale = json.loads(status_path.read_text())
                    if isinstance(stale, dict):
                        peer_id = str(stale.get("peer_id", peer_id))

        if not raw_state:
            # No fresh P2P data — check if node is running in local mode
            from infomesh.dashboard.utils import is_node_running

            if is_node_running(self._config):
                state = "🟡 Local mode (crawling active)"
            else:
                # Check if P2P was ever connected (stale status file)
                import contextlib
                import json

                status_path = self._config.node.data_dir / "p2p_status.json"
                had_peers = False
                with contextlib.suppress(Exception):
                    if status_path.exists():
                        stale = json.loads(status_path.read_text())
                        if isinstance(stale, dict) and stale.get("state") == "stopped":
                            state = "⚪ Stopped (was connected)"
                            had_peers = True
                if not had_peers:
                    state = "🔴 Offline"
        else:
            match raw_state:
                case "running":
                    state = "🟢 Online"
                case "starting":
                    state = "🟡 Starting"
                case "stopping":
                    state = "🟡 Stopping"
                case "stopped":
                    state = "⚪ Stopped"
                case "error":
                    err = data.get("error", "")
                    state = f"🔴 Error: {err}" if err else "🔴 Error"
                case _:
                    state = "🔴 Offline"

        short_id = f"{peer_id[:16]}..." if len(peer_id) > 16 else (peer_id or "—")
        refresh_str = (
            f"  [dim]↻ {self._countdown}s[/dim]" if self._countdown > 0 else ""
        )
        text = (
            f"[bold]P2P Status[/bold]{refresh_str}\n"
            f"  State:      {state}\n"
            f"  Node ID:    {short_id}\n"
            f"  Peers:      {peers} connected\n"
            f"  Bootstrap:  {bootstrap} nodes configured\n"
            f"  Port:       {port} TCP\n"
            f"  Replication: {self._config.network.replication_factor}x"
        )
        self.update(text)


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
        super().__init__("", **kwargs)  # type: ignore[arg-type]
        self._p2p_state: str = "stopped"

    def on_mount(self) -> None:
        self._refresh_content({})

    def update_data(
        self,
        dht_data: dict[str, int],
        *,
        p2p_state: str = "stopped",
    ) -> None:
        """Public API: update DHT panel with new data."""
        self._p2p_state = p2p_state
        self._refresh_content(dht_data)

    def _refresh_content(self, dht_data: dict[str, int]) -> None:
        keys = dht_data.get("keys_stored", 0)
        published = dht_data.get("keys_published", 0)
        gets = dht_data.get("gets_performed", 0)
        puts = dht_data.get("puts_performed", 0)
        if not any([keys, published, gets, puts]):
            if self._p2p_state == "running":
                hint = "No DHT activity yet"
            elif self._p2p_state in ("starting", "stopping"):
                hint = "P2P node is starting\u2026"
            elif self._p2p_state == "error":
                hint = "P2P node error — check logs"
            else:
                hint = "P2P not started — run infomesh start"
            text = f"[bold]DHT[/bold]\n\n  [dim]{hint}[/dim]"
        else:
            text = (
                f"[bold]DHT[/bold]\n"
                f"  Keys stored:    {keys:,}\n"
                f"  Published:      {published:,}\n"
                f"  GET ops:        {gets:,}\n"
                f"  PUT ops:        {puts:,}"
            )
        self.update(text)


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
        table.add_columns("Peer ID", "State")
        yield table

    def set_peers(self, peer_ids: list[str]) -> None:
        """Update the peer table with connected peer IDs."""
        try:
            table = self.query_one("#peers-table", DataTable)
            table.clear()
            if not peer_ids:
                table.add_row("No peers connected", "—")
                return
            for pid in peer_ids:
                short_id = f"{pid[:16]}...{pid[-6:]}" if len(pid) > 24 else pid
                table.add_row(short_id, "connected")
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
        self._prev_up: int = 0
        self._prev_dn: int = 0
        self._prev_time: float = 0.0

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

    def update_from_status(self, bw_data: dict[str, int]) -> None:
        """Calculate Mbps from cumulative byte counters and update display."""
        now = time.monotonic()
        up_bytes = int(bw_data.get("upload_bytes", 0))
        dn_bytes = int(bw_data.get("download_bytes", 0))

        upload_mbps = 0.0
        download_mbps = 0.0

        if self._prev_time > 0:
            dt = now - self._prev_time
            if dt > 0:
                up_delta = max(0, up_bytes - self._prev_up)
                dn_delta = max(0, dn_bytes - self._prev_dn)
                # bytes → megabits: * 8 / 1_000_000
                upload_mbps = (up_delta * 8) / (dt * 1_000_000)
                download_mbps = (dn_delta * 8) / (dt * 1_000_000)
        else:
            # First sample — record baseline, don't display zeros
            self._prev_up = up_bytes
            self._prev_dn = dn_bytes
            self._prev_time = now
            return

        self._prev_up = up_bytes
        self._prev_dn = dn_bytes
        self._prev_time = now

        self._update_display(upload_mbps, download_mbps)

    def _update_display(self, upload_mbps: float, download_mbps: float) -> None:
        """Push values to sparklines and labels."""
        try:
            up_spark = self.query_one("#bw-upload-spark", SparklineChart)
            up_spark.push_value(upload_mbps)
            dn_spark = self.query_one("#bw-download-spark", SparklineChart)
            dn_spark.push_value(download_mbps)
            up_limit = self._config.network.upload_limit_mbps
            dn_limit = self._config.network.download_limit_mbps
            self.query_one("#bw-upload-val", Static).update(
                f"{upload_mbps:.2f}/{up_limit:.1f} Mbps"
            )
            self.query_one("#bw-download-val", Static).update(
                f"{download_mbps:.2f}/{dn_limit:.1f} Mbps"
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

    _REFRESH_INTERVAL = 5.0
    _TICK_INTERVAL = 1.0

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._config = config
        self._refresh_timer: Timer | None = None
        self._last_refresh: float = 0.0
        self._last_data: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            with Horizontal(id="network-top"):
                yield P2PStatusPanel(self._config, id="p2p-status")
                yield DHTPanel(id="dht-status")
            yield PeerTable(id="peer-table")
            yield BandwidthPanel(self._config, id="bandwidth")

    def on_mount(self) -> None:
        self._refresh_timer = self.set_interval(self._TICK_INTERVAL, self._tick)
        self._refresh_from_status()

    def _refresh_from_status(self) -> None:
        """Read p2p_status.json and distribute data to all panels."""
        import contextlib

        self._last_refresh = time.monotonic()
        data = read_p2p_status(self._config)
        self._last_data = data
        countdown = int(self._REFRESH_INTERVAL)

        # P2P Status
        with contextlib.suppress(Exception):
            self.query_one("#p2p-status", P2PStatusPanel).update_status(
                data, countdown=countdown
            )

        # DHT
        with contextlib.suppress(Exception):
            dht_data = data.get("dht", {})
            p2p_state = str(data.get("state", "stopped"))
            if isinstance(dht_data, dict):
                self.query_one("#dht-status", DHTPanel).update_data(
                    dht_data, p2p_state=p2p_state
                )

        # Peer Table
        with contextlib.suppress(Exception):
            peer_ids = data.get("peer_ids", [])
            if isinstance(peer_ids, list):
                self.query_one("#peer-table", PeerTable).set_peers(peer_ids)

        # Bandwidth
        with contextlib.suppress(Exception):
            bw_data = data.get("bandwidth", {})
            if isinstance(bw_data, dict):
                self.query_one("#bandwidth", BandwidthPanel).update_from_status(bw_data)

    def _tick(self) -> None:
        """Periodic tick — refresh from file on interval, countdown otherwise."""
        import contextlib

        elapsed = time.monotonic() - self._last_refresh
        remaining = max(0, int(self._REFRESH_INTERVAL - elapsed))

        if elapsed >= self._REFRESH_INTERVAL:
            self._refresh_from_status()
        else:
            # Update countdown only
            with contextlib.suppress(Exception):
                self.query_one("#p2p-status", P2PStatusPanel).update_status(
                    self._last_data, countdown=remaining
                )

    def refresh_data(self) -> None:
        """Manual refresh."""
        self._refresh_from_status()
