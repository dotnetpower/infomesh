"""Horizontal bar chart widget."""

from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table
from rich.text import Text
from textual.app import RenderResult
from textual.widget import Widget


@dataclass
class BarItem:
    """A single bar in the chart."""

    label: str
    value: float
    color: str = "green"
    suffix: str = ""


class BarChart(Widget):
    """Horizontal bar chart widget.

    Displays labeled horizontal bars with proportional widths.

    Usage::

        chart = BarChart(items=[
            BarItem("Crawling", 702, color="cyan"),
            BarItem("Uptime", 396, color="green"),
        ], bar_width=20)
    """

    DEFAULT_CSS = """
    BarChart {
        height: auto;
        min-height: 3;
    }
    """

    def __init__(
        self,
        items: list[BarItem] | None = None,
        *,
        bar_width: int = 20,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._items: list[BarItem] = items or []
        self._bar_width = bar_width

    def set_items(self, items: list[BarItem]) -> None:
        """Update the bar chart data."""
        self._items = items
        self.refresh()

    def render(self) -> RenderResult:
        if not self._items:
            return Text("No data", style="dim")

        max_val = max(item.value for item in self._items) if self._items else 1
        total = sum(item.value for item in self._items)

        table = Table.grid(padding=(0, 1))
        table.add_column("label", min_width=16, justify="left")
        table.add_column("bar", min_width=self._bar_width)
        table.add_column("value", justify="right", min_width=10)

        for item in self._items:
            # Calculate bar width
            ratio = item.value / max_val if max_val > 0 else 0
            filled = int(ratio * self._bar_width)
            bar_str = "█" * filled + "░" * (self._bar_width - filled)
            bar_text = Text(bar_str, style=item.color)

            # Format value with percentage
            pct = (item.value / total * 100) if total > 0 else 0
            value_text = f"{item.value:,.1f}{item.suffix}  ({pct:.1f}%)"

            table.add_row(
                Text(item.label, style="bold"),
                bar_text,
                value_text,
            )

        return table
