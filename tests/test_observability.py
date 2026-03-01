"""Tests for infomesh.observability.metrics — metrics collection."""

from __future__ import annotations

from infomesh.observability.metrics import (
    MetricsCollector,
    QuerySpan,
    QueryTrace,
    generate_alert_rules,
    generate_grafana_dashboard,
)


class TestMetricsCollector:
    def test_inc(self) -> None:
        mc = MetricsCollector()
        mc.inc("test_counter", 1.0)
        mc.inc("test_counter", 2.0)
        d = mc.to_dict()
        assert d["counters"]["test_counter"] == 3.0

    def test_set_gauge(self) -> None:
        mc = MetricsCollector()
        mc.set_gauge("cpu_usage", 42.5)
        d = mc.to_dict()
        assert d["gauges"]["cpu_usage"] == 42.5

    def test_observe(self) -> None:
        mc = MetricsCollector()
        mc.observe("latency_ms", 10.0)
        mc.observe("latency_ms", 20.0)
        d = mc.to_dict()
        hist = d["histograms"]
        assert isinstance(hist, dict)
        assert "latency_ms" in hist

    def test_prometheus_format(self) -> None:
        mc = MetricsCollector()
        mc.inc("searches_total", 100.0)
        mc.set_gauge("peers_connected", 5.0)
        text = mc.format_prometheus()
        assert "searches_total" in text
        assert "100" in text
        assert "peers_connected" in text

    def test_to_dict(self) -> None:
        mc = MetricsCollector()
        mc.inc("a", 1.0)
        mc.set_gauge("b", 2.0)
        d = mc.to_dict()
        assert "counters" in d
        assert "gauges" in d
        assert "uptime_seconds" in d


class TestQueryTrace:
    def test_trace_spans(self) -> None:
        trace = QueryTrace(trace_id="t1", query="python")
        trace.add_span(
            QuerySpan(
                span_id="s1",
                peer_id="peer1",
                operation="search_local",
                latency_ms=5.0,
                metadata={"store": "fts5"},
            )
        )
        d = trace.to_dict()
        assert d["trace_id"] == "t1"
        assert len(d["spans"]) == 1


class TestGrafanaDashboard:
    def test_structure(self) -> None:
        result = generate_grafana_dashboard()
        dashboard = result.get("dashboard", result)
        assert "panels" in dashboard or "title" in dashboard


class TestAlertRules:
    def test_rules_present(self) -> None:
        rules = generate_alert_rules()
        assert isinstance(rules, list)
        assert len(rules) > 0
        assert "alert" in rules[0]
