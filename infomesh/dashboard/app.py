"""InfoMesh Dashboard — main Textual Application."""

from __future__ import annotations

import contextlib
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Label, TabbedContent, TabPane

from infomesh import __version__
from infomesh.config import Config, load_config
from infomesh.dashboard.bgm import BGMPlayer
from infomesh.dashboard.data_cache import DashboardDataCache
from infomesh.dashboard.screens.crawl import CrawlPane
from infomesh.dashboard.screens.credits import CreditsPane
from infomesh.dashboard.screens.network import NetworkPane
from infomesh.dashboard.screens.overview import OverviewPane
from infomesh.dashboard.screens.search import SearchPane
from infomesh.dashboard.screens.settings import SettingsPane

# CSS path relative to this module
_CSS_PATH = Path(__file__).parent / "dashboard.tcss"

# BGM assets directory (infomesh/assets/bgm — inside the package)
_BGM_DIR = Path(__file__).parent.parent / "assets" / "bgm"
_BGM_FILE = _BGM_DIR / "infomesh-bg-fade.mp3"
_COIN_SFX = _BGM_DIR / "coin-street-fighter.mp3"


class DashboardCommandProvider(Provider):
    """Command palette provider for InfoMesh dashboard actions."""

    _COMMANDS: list[tuple[str, str, str]] = [
        ("Overview", "Switch to Overview tab (1)", "app.tab_1"),
        ("Crawl", "Switch to Crawl tab (2)", "app.tab_2"),
        ("Search", "Switch to Search tab (3)", "app.tab_3"),
        ("Network", "Switch to Network tab (4)", "app.tab_4"),
        ("Credits", "Switch to Credits tab (5)", "app.tab_5"),
        ("Settings", "Switch to Settings tab (6)", "app.tab_6"),
        ("Refresh", "Refresh all panels (r)", "app.refresh"),
        ("Toggle BGM", "Toggle background music (m)", "app.toggle_bgm"),
        ("Help", "Show keyboard shortcuts (?)", "app.help"),
        ("Exit", "Quit the dashboard (q)", "app.quit"),
    ]

    async def discover(self) -> Hits:
        """Show all commands when the palette first opens."""
        for name, help_text, action in self._COMMANDS:
            yield DiscoveryHit(
                display=name,
                command=self.app.run_action(action),
                help=help_text,
            )

    async def search(self, query: str) -> Hits:
        """Yield commands matching the query."""
        matcher = self.matcher(query)
        for name, help_text, action in self._COMMANDS:
            score = matcher.match(name)
            if score > 0:
                yield Hit(
                    score=score,
                    match_display=matcher.highlight(name),
                    command=self.app.run_action(action),
                    help=help_text,
                )


class QuitConfirmScreen(ModalScreen[str]):
    """Modal dialog shown on quit when a node process is running.

    Returns one of: "dashboard_only", "stop_all", "cancel".
    """

    DEFAULT_CSS = """
    QuitConfirmScreen {
        align: center middle;
    }

    #quit-dialog {
        width: 60;
        height: auto;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #quit-dialog Label {
        width: 100%;
        text-align: center;
        margin-bottom: 1;
    }

    #quit-buttons {
        width: 100%;
        height: auto;
        align: center middle;
    }

    #quit-buttons Button {
        margin: 0 1;
        min-width: 24;
    }

    #btn-cancel {
        min-width: 24;
        border: tall $accent;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False, priority=True),
        Binding("left", "focus_previous", "Previous", show=False, priority=True),
        Binding("right", "focus_next", "Next", show=False, priority=True),
    ]

    def on_mount(self) -> None:
        """Auto-focus the Cancel button when the dialog opens."""
        with contextlib.suppress(Exception):
            self.query_one("#btn-cancel", Button).focus()

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical

        with Vertical(id="quit-dialog"):
            yield Label("[bold]Quit InfoMesh?[/bold]")
            yield Label("The node is running in the background.")
            yield Label("[dim]Use Tab / Shift+Tab to move between buttons[/dim]")
            with Horizontal(id="quit-buttons"):
                yield Button(
                    "Close Dashboard (keep node)",
                    variant="primary",
                    id="btn-dashboard-only",
                )
                yield Button(
                    "Stop Node & Exit",
                    variant="error",
                    id="btn-stop-all",
                )
                yield Button(
                    "✖ Cancel",
                    variant="warning",
                    id="btn-cancel",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-dashboard-only":
            self.dismiss("dashboard_only")
        elif event.button.id == "btn-stop-all":
            self.dismiss("stop_all")
        else:
            self.dismiss("cancel")

    def action_cancel(self) -> None:
        self.dismiss("cancel")


class DashboardApp(App[None]):
    """InfoMesh console dashboard application.

    Provides a tabbed interface for monitoring node status,
    crawl activity, search, network peers, and credits.
    """

    TITLE = "InfoMesh Dashboard"
    SUB_TITLE = f"v{__version__}"

    CSS_PATH = _CSS_PATH
    THEME = "catppuccin-mocha"
    COMMANDS = {DashboardCommandProvider}

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("1", "tab_1", "Overview", show=False),
        Binding("2", "tab_2", "Crawl", show=False),
        Binding("3", "tab_3", "Search", show=False),
        Binding("4", "tab_4", "Network", show=False),
        Binding("5", "tab_5", "Credits", show=False),
        Binding("6", "tab_6", "Settings", show=False),
        Binding("r", "refresh", "Refresh", show=True),
        Binding("m", "toggle_bgm", "BGM", show=True),
        Binding("question_mark", "help", "Help", show=True),
    ]

    def __init__(
        self,
        config: Config | None = None,
        initial_tab: str = "overview",
        node_pid: int | None = None,
    ) -> None:
        super().__init__()
        self.config = config or load_config()
        self._initial_tab = initial_tab
        self._node_pid = node_pid
        self.exit_action: str = "dashboard_only"
        self._bgm = BGMPlayer()
        self._data_cache = DashboardDataCache(self.config, ttl=0.5)

    def compose(self) -> ComposeResult:
        yield Header(icon="🌐")
        with TabbedContent(initial=self._initial_tab):
            with TabPane("Overview", id="overview"):
                yield OverviewPane(self.config, data_cache=self._data_cache)
            with TabPane("Crawl", id="crawl"):
                yield CrawlPane(self.config, data_cache=self._data_cache)
            with TabPane("Search", id="search"):
                yield SearchPane(self.config)
            with TabPane("Network", id="network"):
                yield NetworkPane(self.config)
            with TabPane("Credits", id="credits"):
                yield CreditsPane(self.config)
            with TabPane("Settings", id="settings"):
                yield SettingsPane(self.config)
        yield Footer()

    def on_mount(self) -> None:
        """Auto-start background music on dashboard launch."""
        if not self.config.dashboard.bgm_auto_start:
            return
        if not self._bgm.available:
            return
        if not _BGM_FILE.exists():
            return
        vol = self.config.dashboard.bgm_volume
        if self._bgm.play(_BGM_FILE, volume=vol):
            self.notify(
                f"🎵 {_BGM_FILE.name} (vol {vol}%)",
                title="BGM",
                timeout=3,
            )

    def on_credits_pane_credit_earned(
        self,
        event: CreditsPane.CreditEarned,
    ) -> None:
        """Play coin SFX when credits increase (only if BGM is playing)."""
        if self._bgm.is_playing and _COIN_SFX.exists():
            self._bgm.play_sfx(_COIN_SFX, volume=100)
        if event.delta > 0:
            self.notify(
                f"🪙 +{event.delta:.2f} credits!",
                title="Credit",
                timeout=2,
            )

    def action_tab_1(self) -> None:
        self.query_one(TabbedContent).active = "overview"

    def action_tab_2(self) -> None:
        self.query_one(TabbedContent).active = "crawl"

    def action_tab_3(self) -> None:
        self.query_one(TabbedContent).active = "search"

    def action_tab_4(self) -> None:
        self.query_one(TabbedContent).active = "network"

    def action_tab_5(self) -> None:
        self.query_one(TabbedContent).active = "credits"

    def action_tab_6(self) -> None:
        self.query_one(TabbedContent).active = "settings"

    def action_refresh(self) -> None:
        """Refresh all panes."""
        for pane_type in (
            OverviewPane,
            CrawlPane,
            SearchPane,
            NetworkPane,
            CreditsPane,
            SettingsPane,
        ):
            try:
                pane = self.query_one(pane_type)
                pane.refresh_data()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    def action_toggle_bgm(self) -> None:
        """Toggle background music on/off."""
        if not self._bgm.available:
            self.notify(
                "BGM disabled — install ffplay or mpv",
                title="BGM",
                severity="warning",
                timeout=3,
            )
            return

        if not _BGM_DIR.exists() or not _BGM_DIR.is_dir():
            self.notify(
                "No assets/bgm/ folder found",
                title="BGM",
                severity="warning",
            )
            return

        # Find first MP3/audio file in the bgm directory
        audio_exts = (".mp3", ".wav", ".ogg", ".flac", ".m4a")
        try:
            tracks = sorted(
                f for f in _BGM_DIR.iterdir() if f.suffix.lower() in audio_exts
            )
        except OSError:
            tracks = []

        if not tracks:
            self.notify(
                "No audio files in assets/bgm/",
                title="BGM",
                severity="warning",
            )
            return

        vol = self.config.dashboard.bgm_volume
        bgm_track = _BGM_FILE if _BGM_FILE.exists() else tracks[0]
        playing = self._bgm.toggle(bgm_track, volume=vol)
        icon = "🎵" if playing else "🔇"
        status = f"{bgm_track.name} (vol {vol}%)" if playing else "stopped"
        self.notify(f"{icon} {status}", title="BGM", timeout=2)

    def action_help(self) -> None:
        """Show help notification."""
        self.notify(
            "[1-6] Tabs | [r] Refresh | [m] BGM | [q] Quit",
            title="Keyboard Shortcuts",
            timeout=5,
        )

    def on_unmount(self) -> None:
        """Ensure BGM and data cache are cleaned up on any exit path."""
        self._cleanup()

    def _cleanup(self) -> None:
        """Stop BGM player and close data cache (idempotent)."""
        self._bgm.stop()
        self._data_cache.close()

    def action_quit(self) -> None:  # type: ignore[override]
        """Override quit to show confirmation when node is running."""
        self._cleanup()
        if self._node_pid is not None:
            self.push_screen(QuitConfirmScreen(), self._handle_quit_response)  # type: ignore[arg-type]
        else:
            self.exit()

    def _handle_quit_response(self, result: str) -> None:
        """Handle the quit confirmation dialog response."""
        if result == "cancel":
            return
        self.exit_action = result
        self.exit()


def _reset_terminal() -> None:
    """Reset terminal to sane state after TUI exit or crash.

    Disables mouse tracking and restores normal terminal mode so that
    partial ANSI escape sequences don't leak into the shell as commands.
    """
    import sys

    if not sys.stdout.isatty():
        return
    # Disable SGR mouse tracking + normal mouse tracking + alt screen
    sys.stdout.write(
        "\x1b[?1006l"  # disable SGR extended mouse
        "\x1b[?1003l"  # disable any-event mouse tracking
        "\x1b[?1002l"  # disable button-event mouse tracking
        "\x1b[?1000l"  # disable normal mouse tracking
        "\x1b[?25h"  # show cursor
        "\x1b[0m"  # reset attributes
    )
    sys.stdout.flush()


def run_dashboard(
    config: Config | None = None,
    initial_tab: str = "overview",
    node_pid: int | None = None,
) -> str:
    """Launch the dashboard TUI.

    Args:
        config: InfoMesh configuration. Defaults to ``load_config()``.
        initial_tab: Tab name to start on
            (overview, crawl, search, network, credits, settings).
        node_pid: PID of the background node process. When set, quit shows a
            confirmation dialog asking whether to stop the node.

    Returns:
        Exit action: ``"dashboard_only"`` or ``"stop_all"``.
    """
    app = DashboardApp(config=config, initial_tab=initial_tab, node_pid=node_pid)
    try:
        app.run()
    except Exception:
        _reset_terminal()
        raise
    finally:
        app._cleanup()
        _reset_terminal()
    return app.exit_action
