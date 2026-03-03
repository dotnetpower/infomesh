#!/usr/bin/env python3
"""Dashboard Long-Running Stability Monitor.

Launches the dashboard in headless mode (Textual pilot) and monitors
resource usage over time: memory, FDs, CPU, thread count.
Detects leaks by comparing initial vs final resource consumption.

Usage::

    # Monitor for 5 minutes (default)
    uv run python scripts/diag_stability.py

    # Monitor for 1 hour
    uv run python scripts/diag_stability.py --duration 3600

    # Sample every 10 seconds (less overhead)
    uv run python scripts/diag_stability.py --interval 10

    # Show per-sample output (verbose)
    uv run python scripts/diag_stability.py --verbose
"""

from __future__ import annotations

import argparse
import gc
import os
import sys
import time
import tracemalloc
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}", flush=True)


def _get_fd_count() -> int:
    """Get open file descriptor count for current process."""
    try:
        return len(list(Path(f"/proc/{os.getpid()}/fd").iterdir()))
    except (OSError, PermissionError):
        return -1


def _get_thread_count() -> int:
    """Get thread count from /proc."""
    try:
        status = Path(f"/proc/{os.getpid()}/status").read_text()
        for line in status.splitlines():
            if line.startswith("Threads:"):
                return int(line.split(":")[1].strip())
    except (OSError, ValueError):
        pass
    return -1


def _get_memory_mb() -> float:
    """Get RSS memory in MB."""
    try:
        status = Path(f"/proc/{os.getpid()}/status").read_text()
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                kb = int(line.split(":")[1].strip().split()[0])
                return kb / 1024.0
    except (OSError, ValueError):
        pass
    return 0.0


def run_stability_monitor(
    duration_secs: int,
    interval_secs: float,
    verbose: bool,
) -> None:
    """Run the stability monitor."""
    # Start memory tracking
    tracemalloc.start()

    _log("=" * 60)
    _log("Dashboard Stability Monitor")
    _log(f"Duration: {duration_secs}s | Interval: {interval_secs}s")
    _log("=" * 60)

    # Import dashboard components
    from infomesh.config import load_config
    from infomesh.dashboard.data_cache import DashboardDataCache

    config = load_config()
    cache = DashboardDataCache(config, ttl=0.5)

    # Baseline measurements
    gc.collect()
    baseline_mem = _get_memory_mb()
    baseline_fds = _get_fd_count()
    baseline_threads = _get_thread_count()
    baseline_snapshot = tracemalloc.take_snapshot()

    _log("\n--- Baseline ---")
    _log(f"  Memory: {baseline_mem:.1f} MB")
    _log(f"  FDs: {baseline_fds}")
    _log(f"  Threads: {baseline_threads}")

    # Sample history for trend analysis
    samples: list[dict[str, float | int]] = []

    start_time = time.monotonic()
    sample_count = 0

    _log("\n--- Monitoring (simulating dashboard refresh cycles) ---")

    try:
        while time.monotonic() - start_time < duration_secs:
            time.sleep(interval_secs)
            sample_count += 1
            elapsed = time.monotonic() - start_time

            # Simulate what the dashboard does every refresh
            try:
                _ = cache.get_stats()
            except Exception as e:
                _log(f"  ⚠️  Cache error: {e}")

            # Collect metrics
            mem_mb = _get_memory_mb()
            fd_count = _get_fd_count()
            thread_count = _get_thread_count()

            sample = {
                "elapsed": elapsed,
                "mem_mb": mem_mb,
                "fds": fd_count,
                "threads": thread_count,
                "mem_delta": mem_mb - baseline_mem,
                "fd_delta": fd_count - baseline_fds,
            }
            samples.append(sample)

            if verbose or sample_count % max(1, int(30 / interval_secs)) == 0:
                _log(
                    f"  [{elapsed:6.0f}s] "
                    f"mem={mem_mb:.1f}MB "
                    f"(Δ{sample['mem_delta']:+.1f}) | "
                    f"fds={fd_count} "
                    f"(Δ{sample['fd_delta']:+d}) | "
                    f"threads={thread_count}"
                )

            # Alert on significant changes
            if sample["mem_delta"] > 50:
                _log(f"  ⚠️  Memory grew by {sample['mem_delta']:.0f} MB!")
            if sample["fd_delta"] > 20:
                _log(f"  ⚠️  FD count grew by {sample['fd_delta']}!")

    except KeyboardInterrupt:
        _log("\nInterrupted by user")

    # Final measurements
    gc.collect()
    final_mem = _get_memory_mb()
    final_fds = _get_fd_count()
    final_threads = _get_thread_count()
    final_snapshot = tracemalloc.take_snapshot()

    cache.close()

    # Analysis
    _log(f"\n{'=' * 60}")
    _log(f"RESULTS — {sample_count} samples over {time.monotonic() - start_time:.0f}s")
    _log(f"{'=' * 60}")

    _log("\n--- Resource Changes ---")
    _log(
        f"  Memory:  {baseline_mem:.1f} → {final_mem:.1f} MB "
        f"(Δ{final_mem - baseline_mem:+.1f} MB)"
    )
    _log(f"  FDs:     {baseline_fds} → {final_fds} (Δ{final_fds - baseline_fds:+d})")
    _log(
        f"  Threads: {baseline_threads} → {final_threads} "
        f"(Δ{final_threads - baseline_threads:+d})"
    )

    if samples:
        mem_values = [s["mem_mb"] for s in samples]
        fd_values = [s["fds"] for s in samples]
        _log("\n--- Memory Stats ---")
        _log(f"  Min: {min(mem_values):.1f} MB")
        _log(f"  Max: {max(mem_values):.1f} MB")
        _log(f"  Avg: {sum(mem_values) / len(mem_values):.1f} MB")

        _log("\n--- FD Stats ---")
        _log(f"  Min: {min(fd_values)}")
        _log(f"  Max: {max(fd_values)}")

    # Top memory allocations
    _log("\n--- Top 10 Memory Allocations (tracemalloc) ---")
    top_stats = final_snapshot.compare_to(baseline_snapshot, "lineno")
    for i, stat in enumerate(top_stats[:10]):
        _log(f"  {i + 1}. {stat}")

    # Verdict
    _log("\n--- Verdict ---")
    mem_growth = final_mem - baseline_mem
    fd_growth = final_fds - baseline_fds

    issues = []
    if mem_growth > 20:
        issues.append(f"Memory leak: +{mem_growth:.0f} MB")
    if fd_growth > 10:
        issues.append(f"FD leak: +{fd_growth} descriptors")
    if final_threads - baseline_threads > 5:
        issues.append(f"Thread leak: +{final_threads - baseline_threads} threads")

    if issues:
        _log("  ⚠️  ISSUES DETECTED:")
        for issue in issues:
            _log(f"     - {issue}")
    else:
        _log("  ✅ No significant resource leaks detected")

    tracemalloc.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dashboard Stability Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Monitor duration in seconds (default: 300)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Sample interval in seconds (default: 5.0)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print every sample (default: every ~30s)",
    )
    args = parser.parse_args()

    run_stability_monitor(args.duration, args.interval, args.verbose)


if __name__ == "__main__":
    main()
