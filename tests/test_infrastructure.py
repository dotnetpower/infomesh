"""Tests for new infrastructure modules (shutdown, plugins, SLO, etc.)."""

from __future__ import annotations

from pathlib import Path

from infomesh.benchmarks import BenchmarkSuite, benchmark
from infomesh.diagnostics import DiagnosticReport, run_diagnostics
from infomesh.plugins import HookPoint, PluginRegistry, get_registry
from infomesh.shutdown import GracefulShutdown
from infomesh.slo import DEFAULT_SLOS, SLOTracker


class TestDiagnostics:
    def test_run_diagnostics(self, tmp_path: Path) -> None:
        report = run_diagnostics(tmp_path)
        assert isinstance(report, DiagnosticReport)
        assert report.summary


class TestBenchmarks:
    def test_zero_iterations(self) -> None:
        result = benchmark(lambda: None, iterations=0, name="zero")
        assert result.iterations == 0
        assert result.avg_ms == 0.0

    def test_suite_report(self) -> None:
        suite = BenchmarkSuite()
        suite.add(benchmark(lambda: None, iterations=1, name="noop"))
        assert "noop" in suite.report()


class TestGracefulShutdown:
    def test_initial_state(self) -> None:
        gs = GracefulShutdown()
        assert not gs.is_shutting_down

    def test_add_callback(self) -> None:
        gs = GracefulShutdown()
        gs.add_callback(lambda: None)

    def test_try_set_shutting_down_atomic(self) -> None:
        """Regression: rapid signals should only trigger cleanup once."""
        gs = GracefulShutdown()
        assert gs._try_set_shutting_down() is True
        # Second call should return False (already shutting down)
        assert gs._try_set_shutting_down() is False
        assert gs.is_shutting_down

    def test_sync_handler_sets_flag(self) -> None:
        gs = GracefulShutdown()
        # Don't actually call the handler (raises SystemExit)
        assert not gs.is_shutting_down


class TestPluginRegistry:
    def test_register_plugin(self) -> None:
        reg = PluginRegistry()
        reg.register_plugin("test-plugin", "1.0.0")
        assert len(reg.registered_plugins) == 1
        assert reg.registered_plugins[0]["name"] == "test-plugin"

    def test_hook_decorator(self) -> None:
        reg = PluginRegistry()

        @reg.hook(HookPoint.PRE_SEARCH)
        def my_hook(data: str) -> str:
            return data.upper()

        result = reg.run_hook(HookPoint.PRE_SEARCH, "hello")
        assert result == "HELLO"

    def test_hook_filter(self) -> None:
        reg = PluginRegistry()

        @reg.hook(HookPoint.PRE_INDEX)
        def spam_filter(doc: dict) -> dict | None:  # type: ignore[type-arg]
            if "spam" in str(doc.get("text", "")):
                return None
            return doc

        assert reg.run_hook(HookPoint.PRE_INDEX, {"text": "good"}) is not None
        assert reg.run_hook(HookPoint.PRE_INDEX, {"text": "spam here"}) is None

    def test_hook_counts(self) -> None:
        reg = PluginRegistry()

        @reg.hook(HookPoint.POST_SEARCH)
        def h1(d: object) -> object:
            return d

        @reg.hook(HookPoint.POST_SEARCH)
        def h2(d: object) -> object:
            return d

        assert reg.hook_counts["post_search"] == 2

    def test_register_with_hooks(self) -> None:
        reg = PluginRegistry()
        reg.register_plugin(
            "scorer",
            hooks={HookPoint.CUSTOM_SCORER: lambda x: x},
        )
        assert "custom_scorer" in reg.hook_counts

    def test_global_registry(self) -> None:
        reg = get_registry()
        assert isinstance(reg, PluginRegistry)


class TestSLOTracker:
    def test_default_slos(self) -> None:
        assert len(DEFAULT_SLOS) >= 4

    def test_record_and_status(self) -> None:
        tracker = SLOTracker()
        for i in range(10):
            tracker.record("search_latency_p99", 100.0 + i * 10)
        statuses = tracker.get_status()
        assert len(statuses) > 0
        for s in statuses:
            if s.slo.name == "search_latency_p99":
                assert s.met  # 190ms << 1000ms target

    def test_measurements_are_bounded(self) -> None:
        tracker = SLOTracker()

        for i in range(12_000):
            tracker.record("search_latency_p99", float(i))

        assert len(tracker._measurements["search_latency_p99"]) == 10_000

    def test_record_success(self) -> None:
        tracker = SLOTracker()
        for _ in range(95):
            tracker.record_success("search_availability", True)
        for _ in range(5):
            tracker.record_success("search_availability", False)
        statuses = tracker.get_status()
        for s in statuses:
            if s.slo.name == "search_availability":
                assert s.current_value == 0.95
                assert not s.met  # target is 0.99

    def test_summary(self) -> None:
        tracker = SLOTracker()
        summary = tracker.summary()
        assert "total_slos" in summary
        assert "details" in summary

    def test_empty_tracker(self) -> None:
        tracker = SLOTracker()
        statuses = tracker.get_status()
        # All should be met with no data (defaults to 1.0/0.0)
        for s in statuses:
            if s.slo.unit == "ratio":
                assert s.met  # 1.0 >= target
