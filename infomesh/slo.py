"""Service Level Objectives (SLO) definitions and monitoring.

Feature #11: Define and track SLOs for InfoMesh node operations.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

_MAX_MEASUREMENTS_PER_SLO = 10_000


@dataclass
class SLODefinition:
    """A Service Level Objective."""

    name: str
    description: str
    target: float  # Target value (e.g., 0.99 for 99%)
    unit: str  # "ratio", "ms", "seconds"
    window_seconds: float = 3600.0  # Measurement window


# Default SLOs for InfoMesh
DEFAULT_SLOS: list[SLODefinition] = [
    SLODefinition(
        name="search_latency_p99",
        description="99th percentile search latency",
        target=1000.0,
        unit="ms",
    ),
    SLODefinition(
        name="search_availability",
        description="Search success rate",
        target=0.99,
        unit="ratio",
    ),
    SLODefinition(
        name="crawl_success_rate",
        description="Crawl page success rate",
        target=0.90,
        unit="ratio",
    ),
    SLODefinition(
        name="node_uptime",
        description="Node availability",
        target=0.995,
        unit="ratio",
    ),
    SLODefinition(
        name="p2p_connectivity",
        description="Time connected to ≥1 peer",
        target=0.95,
        unit="ratio",
    ),
]


@dataclass
class SLOStatus:
    """Current status of an SLO."""

    slo: SLODefinition
    current_value: float
    target: float
    met: bool
    error_budget_remaining: float  # 0.0–1.0
    window_start: float = 0.0


class SLOTracker:
    """Track SLO metrics over a sliding window."""

    def __init__(
        self,
        slos: list[SLODefinition] | None = None,
    ) -> None:
        self._slos = slos or DEFAULT_SLOS
        self._measurements: dict[str, deque[tuple[float, float]]] = {}
        self._successes: dict[str, int] = {}
        self._totals: dict[str, int] = {}

    def record(self, slo_name: str, value: float) -> None:
        """Record a measurement for an SLO."""
        now = time.time()
        measurements: deque[tuple[float, float]] = self._measurements.setdefault(
            slo_name,
            deque(maxlen=_MAX_MEASUREMENTS_PER_SLO),
        )
        measurements.append((now, value))

    def record_success(self, slo_name: str, success: bool) -> None:
        """Record a success/failure for ratio-based SLOs."""
        self._totals[slo_name] = self._totals.get(slo_name, 0) + 1
        if success:
            self._successes[slo_name] = self._successes.get(slo_name, 0) + 1

    def get_status(self) -> list[SLOStatus]:
        """Get current status of all SLOs."""
        now = time.time()
        statuses: list[SLOStatus] = []

        for slo in self._slos:
            if slo.unit == "ratio":
                total = self._totals.get(slo.name, 0)
                success = self._successes.get(slo.name, 0)
                current = success / total if total > 0 else 1.0
                met = current >= slo.target
                if slo.target < 1.0:
                    budget = max(
                        0.0,
                        (current - slo.target) / (1.0 - slo.target),
                    )
                else:
                    budget = 1.0 if met else 0.0
            elif slo.unit == "ms":
                window_start = now - slo.window_seconds
                measurements = self._measurements.get(slo.name)
                recent = (
                    [v for t, v in measurements if t > window_start]
                    if measurements is not None
                    else []
                )
                if recent:
                    recent.sort()
                    p99_idx = int(len(recent) * 0.99)
                    current = recent[min(p99_idx, len(recent) - 1)]
                else:
                    current = 0.0
                met = current <= slo.target
                budget = max(0.0, 1.0 - current / slo.target) if slo.target > 0 else 1.0
            else:
                current = 0.0
                met = True
                budget = 1.0

            statuses.append(
                SLOStatus(
                    slo=slo,
                    current_value=round(current, 4),
                    target=slo.target,
                    met=met,
                    error_budget_remaining=round(budget, 4),
                    window_start=now - slo.window_seconds,
                )
            )

        return statuses

    def summary(self) -> dict[str, object]:
        """Get summary of all SLOs."""
        statuses = self.get_status()
        return {
            "total_slos": len(statuses),
            "slos_met": sum(1 for s in statuses if s.met),
            "slos_violated": sum(1 for s in statuses if not s.met),
            "details": [
                {
                    "name": s.slo.name,
                    "target": s.target,
                    "current": s.current_value,
                    "met": s.met,
                    "budget_remaining": s.error_budget_remaining,
                }
                for s in statuses
            ],
        }
