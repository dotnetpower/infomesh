"""Sparkline widget — compact inline mini-chart."""

from __future__ import annotations

from rich.text import Text
from textual.app import RenderResult
from textual.reactive import reactive
from textual.widget import Widget

# Unicode block characters for sparkline rendering (low → high)
_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


class SparklineChart(Widget):
    """A compact sparkline chart rendered with Unicode block characters.

    Displays a series of numeric values as a single-line bar chart.
    Useful for showing activity trends (crawl rate, query rate, etc.).

    Args:
        data: List of numeric values to plot.
        max_width: Maximum number of characters to display.
        color: Rich color name for the sparkline.
    """

    DEFAULT_CSS = """
    SparklineChart {
        height: 1;
        min-width: 10;
    }
    """

    data: reactive[list[float]] = reactive(list, layout=True)
    color: reactive[str] = reactive("green")

    def __init__(
        self,
        data: list[float] | None = None,
        *,
        color: str = "green",
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        if data:
            self.data = list(data)
        self.color = color

    def render(self) -> RenderResult:
        if not self.data:
            return Text("no data", style="dim")

        values = self.data
        max_val = max(values) if values else 1
        min_val = min(values) if values else 0
        val_range = max_val - min_val if max_val != min_val else 1

        # Map values to sparkline characters
        chars: list[str] = []
        for v in values:
            normalized = (v - min_val) / val_range
            idx = int(normalized * (len(_SPARK_CHARS) - 1))
            idx = max(0, min(idx, len(_SPARK_CHARS) - 1))
            chars.append(_SPARK_CHARS[idx])

        return Text("".join(chars), style=self.color)

    def push_value(self, value: float, *, max_points: int = 30) -> None:
        """Add a value and trim to max_points."""
        new_data = list(self.data)
        new_data.append(value)
        if len(new_data) > max_points:
            new_data = new_data[-max_points:]
        self.data = new_data
