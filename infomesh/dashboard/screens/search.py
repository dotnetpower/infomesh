"""Search pane — interactive search with score breakdown."""

from __future__ import annotations

from datetime import UTC

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Input, Static

from infomesh.config import Config


class SearchResultsPanel(Static):
    """Displays search results with score breakdown and scrollable content."""

    DEFAULT_CSS = """
    SearchResultsPanel {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def display_results(
        self,
        query: str,
        results: list[dict[str, object]],
        elapsed_ms: float,
        source: str = "local",
    ) -> None:
        """Render search results (top 5) with extended snippets."""
        if not results:
            self.update(f"No results found for: [bold]{query}[/bold]")
            return

        top_n = results[:5]
        lines: list[str] = []
        lines.append(f"[bold]Query:[/bold] {query}")
        lines.append(
            f"Found [bold]{len(results)}[/bold] results "
            f"({elapsed_ms:.0f}ms, {source}) — showing top {len(top_n)}:\n"
        )

        for i, r in enumerate(top_n, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            score = r.get("score", 0.0)
            snippet = str(r.get("snippet", ""))[:600]
            crawled = r.get("crawled_at", "")

            lines.append(f"{'─' * 56}")
            lines.append(f"[bold cyan]{i}. {title}[/bold cyan]")
            lines.append(f"   [dim]{url}[/dim]")
            if crawled:
                lines.append(f"   [dim]Crawled: {crawled}[/dim]")

            # Score breakdown
            score_parts: list[str] = []
            for key in ("bm25", "freshness", "trust", "authority"):
                val = r.get(key)
                if val is not None:
                    score_parts.append(f"{key.title()}={val:.3f}")
            if score_parts:
                lines.append(f"   📊 {', '.join(score_parts)}")
            lines.append(f"   Score: [bold green]{score:.4f}[/bold green]")
            lines.append("")

            # Extended content preview — word-wrapped, up to 600 chars
            if snippet:
                lines.append("   [italic]Preview:[/italic]")
                # Wrap long snippet into ~70-char lines
                words = snippet.split()
                line_buf = "   "
                for word in words:
                    if len(line_buf) + len(word) + 1 > 72:
                        lines.append(line_buf)
                        line_buf = "   " + word
                    else:
                        line_buf += (" " if len(line_buf) > 3 else "") + word
                if line_buf.strip():
                    lines.append(line_buf)
            lines.append("")

        lines.append(f"{'─' * 56}")
        lines.append(f"[dim]Scroll ↑↓ for more • {len(results)} total matches[/dim]")

        self.update("\n".join(lines))

    def display_error(self, message: str) -> None:
        """Display an error message."""
        self.update(f"[bold red]Error:[/bold red] {message}")


class SearchPane(Widget):
    """Interactive search pane with input and results."""

    DEFAULT_CSS = """
    SearchPane {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("slash", "focus_search", "Search"),
        ("escape", "blur_search", "Back"),
    ]

    def __init__(self, config: Config, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._config = config

    def compose(self) -> ComposeResult:
        yield Static("[bold]🔍 Search[/bold]", classes="panel-title")
        yield Input(
            placeholder="Enter search query...",
            id="search-input",
        )
        with VerticalScroll():
            yield SearchResultsPanel(self._config, id="search-results")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle search query submission."""
        query = event.value.strip()
        if not query:
            return
        self._run_search(query)

    def action_focus_search(self) -> None:
        """Focus the search input."""
        import contextlib

        with contextlib.suppress(Exception):
            self.query_one("#search-input", Input).focus()

    def action_blur_search(self) -> None:
        """Remove focus from search input and return to tab navigation."""
        import contextlib

        with contextlib.suppress(Exception):
            self.screen.focus_next()

    def _run_search(self, query: str) -> None:
        """Execute a local search and display results."""
        results_panel = self.query_one("#search-results", SearchResultsPanel)

        try:
            import time

            from infomesh.index.local_store import LocalStore

            store = LocalStore(
                db_path=self._config.index.db_path,
                compression_enabled=self._config.storage.compression_enabled,
                compression_level=self._config.storage.compression_level,
            )

            start = time.perf_counter()
            search_results = store.search(query, limit=10)
            elapsed_ms = (time.perf_counter() - start) * 1000

            from datetime import datetime

            result_dicts = [
                {
                    "title": r.title,
                    "url": r.url,
                    "score": r.score,
                    "snippet": r.snippet,
                    "bm25": r.score,
                    "crawled_at": datetime.fromtimestamp(
                        r.crawled_at,
                        tz=UTC,
                    ).strftime("%Y-%m-%d %H:%M")
                    if r.crawled_at
                    else "",
                }
                for r in search_results
            ]

            results_panel.display_results(
                query, result_dicts, elapsed_ms, source="local"
            )
            store.close()
        except Exception as exc:  # noqa: BLE001
            results_panel.display_error(str(exc))

    def refresh_data(self) -> None:
        """No-op for search pane (user-driven)."""
