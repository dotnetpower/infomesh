"""Live log feed widget — scrolling event log."""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import RichLog


class LiveLog(RichLog):
    """Real-time scrolling log widget for dashboard events.

    Displays timestamped events with color-coded severity.
    Automatically scrolls to the latest entry.

    Usage::

        log = LiveLog(max_lines=100)
        log.log_event("Crawled example.com", style="green")
        log.log_event("Connection failed", style="red")
    """

    DEFAULT_CSS = """
    LiveLog {
        height: 100%;
        border: round $accent;
    }
    """

    def __init__(
        self,
        *,
        max_lines: int = 200,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(
            max_lines=max_lines,
            highlight=True,
            markup=True,
            auto_scroll=True,
            name=name,
            id=id,
            classes=classes,
        )

    def log_event(self, message: str, *, style: str = "") -> None:
        """Add a timestamped event to the log.

        Args:
            message: Event description.
            style: Rich style for the message text.
        """
        ts = time.strftime("%H:%M:%S")
        line = Text()
        line.append(f" {ts} ", style="dim")
        line.append(" ")
        line.append(message, style=style)
        self.write(line)

    def log_crawl(self, url: str, *, success: bool = True, credits: float = 0) -> None:
        """Log a crawl event."""
        icon = "✓" if success else "✗"
        color = "green" if success else "red"
        msg = Text()
        msg.append(f" {icon} ", style=color)
        msg.append(url, style="bold" if success else "dim strike")
        if credits > 0:
            msg.append(f"  +{credits:.1f} cr", style="cyan")
        ts = time.strftime("%H:%M:%S")
        line = Text()
        line.append(f" {ts} ", style="dim")
        line.append(msg)
        self.write(line)

    def log_search(self, query: str, count: int, elapsed_ms: float) -> None:
        """Log a search event."""
        ts = time.strftime("%H:%M:%S")
        line = Text()
        line.append(f" {ts} ", style="dim")
        line.append(" 🔍 ", style="yellow")
        line.append(f'"{query}"', style="bold")
        line.append(f" ({count} results, {elapsed_ms:.0f}ms)", style="dim")
        self.write(line)

    def log_peer(self, peer_id: str, *, connected: bool = True) -> None:
        """Log a peer connection/disconnection event."""
        ts = time.strftime("%H:%M:%S")
        action = "connected" if connected else "disconnected"
        color = "green" if connected else "red"
        line = Text()
        line.append(f" {ts} ", style="dim")
        line.append(f" Peer {peer_id[:12]}... {action}", style=color)
        self.write(line)
