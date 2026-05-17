"""Performance benchmarking utilities.

Feature #45: Automated performance measurement and reporting.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field


@dataclass
class BenchmarkResult:
    """Result of a benchmark run."""

    name: str
    iterations: int
    total_ms: float
    avg_ms: float
    median_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    ops_per_sec: float

    def __str__(self) -> str:
        return (
            f"{self.name}: avg={self.avg_ms:.1f}ms "
            f"p95={self.p95_ms:.1f}ms "
            f"({self.ops_per_sec:.0f} ops/s, {self.iterations} iters)"
        )


@dataclass
class BenchmarkSuite:
    """Collection of benchmark results."""

    results: list[BenchmarkResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0

    def add(self, result: BenchmarkResult) -> None:
        self.results.append(result)

    def report(self) -> str:
        lines = ["InfoMesh Performance Benchmark", "=" * 40]
        for r in self.results:
            lines.append(str(r))
        return "\n".join(lines)


def benchmark(
    func: object,
    *args: object,
    iterations: int = 100,
    name: str = "",
    **kwargs: object,
) -> BenchmarkResult:
    """Benchmark a callable function."""
    timings: list[float] = []

    for _ in range(iterations):
        start = time.perf_counter()
        func(*args, **kwargs)  # type: ignore[operator]
        elapsed = (time.perf_counter() - start) * 1000
        timings.append(elapsed)

    if not timings:
        return BenchmarkResult(
            name=name or str(func),
            iterations=0,
            total_ms=0.0,
            avg_ms=0.0,
            median_ms=0.0,
            p95_ms=0.0,
            p99_ms=0.0,
            min_ms=0.0,
            max_ms=0.0,
            ops_per_sec=0.0,
        )

    timings.sort()
    total = sum(timings)
    n = len(timings)

    return BenchmarkResult(
        name=name or str(func),
        iterations=iterations,
        total_ms=round(total, 2),
        avg_ms=round(statistics.mean(timings), 2),
        median_ms=round(statistics.median(timings), 2),
        p95_ms=round(timings[min(int(n * 0.95), n - 1)], 2),
        p99_ms=round(timings[min(int(n * 0.99), n - 1)], 2),
        min_ms=round(timings[0], 2),
        max_ms=round(timings[-1], 2),
        ops_per_sec=round(1000 * n / total, 1) if total > 0 else 0.0,
    )
