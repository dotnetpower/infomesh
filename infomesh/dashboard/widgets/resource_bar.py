"""Resource usage bar widget (CPU, RAM, Disk, Network)."""

from __future__ import annotations

from rich.text import Text
from textual.app import RenderResult
from textual.widget import Widget


class ResourceBar(Widget):
    """Displays a labeled resource usage bar with percentage.

    Example::

        bar = ResourceBar(label="CPU", value=38, max_value=100, color="cyan")
    """

    DEFAULT_CSS = """
    ResourceBar {
        height: 1;
    }
    """

    def __init__(
        self,
        label: str = "",
        value: float = 0,
        max_value: float = 100,
        *,
        unit: str = "%",
        color: str = "green",
        bar_width: int = 12,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._label = label
        self._value = value
        self._max_value = max_value
        self._unit = unit
        self._color = color
        self._bar_width = bar_width

    def update_value(self, value: float, max_value: float | None = None) -> None:
        """Update the displayed value."""
        self._value = value
        if max_value is not None:
            self._max_value = max_value
        self.refresh()

    def render(self) -> RenderResult:
        ratio = self._value / self._max_value if self._max_value > 0 else 0
        ratio = min(1.0, max(0.0, ratio))
        filled = int(ratio * self._bar_width)
        empty = self._bar_width - filled

        # Color changes based on usage level
        if ratio >= 0.9:
            bar_color = "red"
        elif ratio >= 0.7:
            bar_color = "yellow"
        else:
            bar_color = self._color

        bar = Text()
        bar.append(f"{self._label:>5}: ", style="bold")
        bar.append("█" * filled, style=bar_color)
        bar.append("░" * empty, style="dim")

        if self._unit == "%":
            bar.append(f"  {ratio * 100:.0f}%", style=bar_color)
        else:
            val_str = f"  {self._value:.1f}/{self._max_value:.1f} {self._unit}"
            bar.append(val_str, style=bar_color)

        return bar
