"""OpenTelemetry and Prometheus instrumentation.

Features:
- #42: OpenTelemetry integration (traces + metrics)
- #43: Prometheus metrics endpoint
- #44: Structured log forwarding config
- #45: Distributed query tracing
- #46: Performance benchmark suite
- #47: Grafana dashboard template generation
- #48: Alert rule definitions
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

# ── #42: OpenTelemetry integration ─────────────────────────────────


def setup_otel(
    service_name: str = "infomesh",
    *,
    endpoint: str | None = None,
) -> bool:
    """Initialize OpenTelemetry tracing and metrics.

    Args:
        service_name: Service name for spans.
        endpoint: OTLP endpoint URL (e.g. http://localhost:4317).

    Returns:
        True if OTel was initialized successfully.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import (
            TracerProvider,
        )

        provider = TracerProvider()
        trace.set_tracer_provider(provider)

        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.trace.export import (
                BatchSpanProcessor,
            )

            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))

        return True
    except ImportError:
        return False


def get_tracer(name: str = "infomesh") -> Any:
    """Get an OTel tracer if available."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return None


# ── #43: Prometheus metrics ────────────────────────────────────────


class MetricsCollector:
    """In-process Prometheus-compatible metrics collector.

    Exposes counters, histograms, and gauges in Prometheus
    text exposition format.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._start_time = time.time()

    def inc(self, name: str, value: float = 1.0) -> None:
        """Increment a counter."""
        with self._lock:
            self._counters[name] += value

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge value."""
        with self._lock:
            self._gauges[name] = value

    def observe(self, name: str, value: float) -> None:
        """Record a histogram observation."""
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = []
            self._histograms[name].append(value)
            # Keep last 1000 observations
            if len(self._histograms[name]) > 1000:
                self._histograms[name] = self._histograms[name][-1000:]

    def format_prometheus(self) -> str:
        """Export metrics in Prometheus text format.

        Returns:
            Prometheus exposition text.
        """
        with self._lock:
            lines: list[str] = []

            # Counters
            for name, value in sorted(self._counters.items()):
                safe = _sanitize_metric_name(name)
                lines.append(f"# TYPE {safe} counter")
                lines.append(f"{safe} {value}")

            # Gauges
            for name, value in sorted(self._gauges.items()):
                safe = _sanitize_metric_name(name)
                lines.append(f"# TYPE {safe} gauge")
                lines.append(f"{safe} {value}")

            # Histograms (summary stats)
            for name, values in sorted(
                self._histograms.items(),
            ):
                safe = _sanitize_metric_name(name)
                if values:
                    count = len(values)
                    total = sum(values)
                    avg = total / count
                    lines.append(f"# TYPE {safe} summary")
                    lines.append(f"{safe}_count {count}")
                    lines.append(f"{safe}_sum {total:.3f}")
                    lines.append(f"{safe}_avg {avg:.3f}")

            # Uptime gauge
            uptime = time.time() - self._start_time
            lines.append("# TYPE infomesh_uptime_seconds gauge")
            lines.append(f"infomesh_uptime_seconds {uptime:.0f}")

            return "\n".join(lines) + "\n"

    def to_dict(self) -> dict[str, object]:
        """Export metrics as a dictionary."""
        with self._lock:
            result: dict[str, object] = {}
            result["counters"] = dict(self._counters)
            result["gauges"] = dict(self._gauges)
            hist_summary: dict[str, dict[str, float]] = {}
            for name, values in self._histograms.items():
                if values:
                    hist_summary[name] = {
                        "count": len(values),
                        "sum": sum(values),
                        "avg": sum(values) / len(values),
                    }
            result["histograms"] = hist_summary
            result["uptime_seconds"] = time.time() - self._start_time
            return result


def _sanitize_metric_name(name: str) -> str:
    """Sanitize metric name for Prometheus format."""
    import re

    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


# ── #44: Structured log forwarding ─────────────────────────────────


def configure_log_forwarding(
    *,
    format: str = "json",
    output: str = "stdout",
) -> dict[str, str]:
    """Get structlog configuration for log forwarding.

    Args:
        format: Output format ('json', 'logfmt', 'text').
        output: Output target ('stdout', 'file', 'syslog').

    Returns:
        Configuration dict for structlog setup.
    """
    return {
        "format": format,
        "output": output,
        "level": "INFO",
        "processors": (
            "structlog.stdlib.add_log_level,"
            "structlog.processors.TimeStamper(fmt='iso'),"
            "structlog.processors.JSONRenderer()"
            if format == "json"
            else "structlog.dev.ConsoleRenderer()"
        ),
    }


# ── #45: Distributed query tracing ────────────────────────────────


@dataclass
class QuerySpan:
    """A trace span for a query hop."""

    span_id: str
    peer_id: str
    operation: str
    start_time: float = 0.0
    end_time: float = 0.0
    latency_ms: float = 0.0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class QueryTrace:
    """Full trace of a distributed query."""

    trace_id: str
    query: str
    spans: list[QuerySpan] = field(default_factory=list)
    total_latency_ms: float = 0.0

    def add_span(self, span: QuerySpan) -> None:
        self.spans.append(span)
        self.total_latency_ms = sum(s.latency_ms for s in self.spans)

    def to_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "total_latency_ms": self.total_latency_ms,
            "spans": [
                {
                    "span_id": s.span_id,
                    "peer_id": s.peer_id,
                    "operation": s.operation,
                    "latency_ms": s.latency_ms,
                    "metadata": s.metadata,
                }
                for s in self.spans
            ],
        }


# ── #46: Performance benchmark suite ──────────────────────────────


@dataclass
class BenchmarkResult:
    """Result of a performance benchmark run."""

    name: str
    iterations: int
    total_ms: float
    avg_ms: float
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def run_benchmark(
    name: str,
    fn: object,
    *,
    iterations: int = 100,
) -> BenchmarkResult:
    """Run a synchronous benchmark.

    Args:
        name: Benchmark name.
        fn: Callable to benchmark.
        iterations: Number of iterations.

    Returns:
        BenchmarkResult with timing stats.
    """
    timings: list[float] = []
    for _ in range(iterations):
        t0 = time.monotonic()
        if callable(fn):
            fn()
        elapsed = (time.monotonic() - t0) * 1000
        timings.append(elapsed)

    timings.sort()
    total = sum(timings)

    return BenchmarkResult(
        name=name,
        iterations=iterations,
        total_ms=round(total, 2),
        avg_ms=round(total / iterations, 2),
        min_ms=round(timings[0], 2),
        max_ms=round(timings[-1], 2),
        p50_ms=round(timings[iterations // 2], 2),
        p95_ms=round(timings[int(iterations * 0.95)], 2),
        p99_ms=round(timings[int(iterations * 0.99)], 2),
    )


# ── #47: Grafana dashboard template ───────────────────────────────


def generate_grafana_dashboard() -> dict[str, object]:
    """Generate a Grafana dashboard JSON for InfoMesh metrics.

    Returns:
        Grafana dashboard JSON structure.
    """
    panels: list[dict[str, object]] = [
        {
            "title": "Search Queries / sec",
            "type": "graph",
            "targets": [
                {
                    "expr": ("rate(infomesh_search_total[5m])"),
                }
            ],
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 0},
        },
        {
            "title": "Search Latency (avg)",
            "type": "graph",
            "targets": [
                {
                    "expr": ("infomesh_search_latency_ms_avg"),
                }
            ],
            "gridPos": {"h": 8, "w": 12, "x": 12, "y": 0},
        },
        {
            "title": "Documents Indexed",
            "type": "stat",
            "targets": [
                {"expr": "infomesh_documents_indexed"},
            ],
            "gridPos": {"h": 4, "w": 6, "x": 0, "y": 8},
        },
        {
            "title": "Crawl Rate / min",
            "type": "graph",
            "targets": [
                {
                    "expr": ("rate(infomesh_crawl_total[5m]) * 60"),
                }
            ],
            "gridPos": {"h": 8, "w": 12, "x": 0, "y": 12},
        },
        {
            "title": "P2P Peers Connected",
            "type": "stat",
            "targets": [
                {"expr": "infomesh_p2p_peers"},
            ],
            "gridPos": {"h": 4, "w": 6, "x": 6, "y": 8},
        },
        {
            "title": "Credit Balance",
            "type": "stat",
            "targets": [
                {"expr": "infomesh_credit_balance"},
            ],
            "gridPos": {"h": 4, "w": 6, "x": 12, "y": 8},
        },
    ]

    return {
        "dashboard": {
            "title": "InfoMesh Monitoring",
            "tags": ["infomesh", "search", "p2p"],
            "timezone": "browser",
            "panels": panels,
            "refresh": "30s",
            "time": {"from": "now-1h", "to": "now"},
        },
    }


# ── #48: Alert rules ──────────────────────────────────────────────


def generate_alert_rules() -> list[dict[str, object]]:
    """Generate Prometheus alert rules for InfoMesh.

    Returns:
        List of alert rule definitions.
    """
    return [
        {
            "alert": "HighSearchLatency",
            "expr": "infomesh_search_latency_ms_avg > 2000",
            "for": "5m",
            "labels": {"severity": "warning"},
            "annotations": {
                "summary": "Search latency exceeds 2s",
            },
        },
        {
            "alert": "CrawlRateDropped",
            "expr": ("rate(infomesh_crawl_total[10m]) == 0"),
            "for": "10m",
            "labels": {"severity": "warning"},
            "annotations": {
                "summary": "No crawls in 10 minutes",
            },
        },
        {
            "alert": "LowDiskSpace",
            "expr": "infomesh_disk_free_mb < 500",
            "for": "5m",
            "labels": {"severity": "critical"},
            "annotations": {
                "summary": ("Disk space below 500MB"),
            },
        },
        {
            "alert": "NoPeersConnected",
            "expr": "infomesh_p2p_peers == 0",
            "for": "15m",
            "labels": {"severity": "warning"},
            "annotations": {
                "summary": "No P2P peers connected",
            },
        },
        {
            "alert": "CreditsDepleted",
            "expr": "infomesh_credit_balance < 0",
            "for": "1h",
            "labels": {"severity": "info"},
            "annotations": {
                "summary": "Credit balance is negative",
            },
        },
    ]
