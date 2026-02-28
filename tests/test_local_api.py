"""Tests for infomesh.api.local_api — FastAPI local admin API."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from infomesh.api.local_api import (
    _format_duration,
    _redact_paths,
    create_admin_app,
)
from infomesh.config import Config

# ── Fixtures ────────────────────────────────────────────────


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    """Create a minimal config pointing at tmp_path."""
    from infomesh.config import IndexConfig, NodeConfig

    return Config(
        node=NodeConfig(data_dir=tmp_path),
        index=IndexConfig(db_path=tmp_path / "index.db"),
    )


@pytest.fixture()
def client(config: Config) -> TestClient:
    """Create a test client for the admin API."""
    app = create_admin_app(config=config)
    return TestClient(app)


# ── Health endpoint ─────────────────────────────────────────


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ── Status endpoint ─────────────────────────────────────────


class TestStatus:
    def test_status_returns_running(self, client: TestClient) -> None:
        resp = client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert "uptime_seconds" in data
        assert "version" in data

    def test_status_has_index_info(self, client: TestClient) -> None:
        resp = client.get("/status")
        data = resp.json()
        assert "index" in data

    def test_status_uptime_increases(self, client: TestClient) -> None:
        r1 = client.get("/status").json()
        time.sleep(0.05)
        r2 = client.get("/status").json()
        assert r2["uptime_seconds"] >= r1["uptime_seconds"]


# ── Config endpoint ─────────────────────────────────────────


class TestConfig:
    def test_get_config(self, client: TestClient) -> None:
        resp = client.get("/config")
        assert resp.status_code == 200
        data = resp.json()
        # Should contain top-level sections
        assert "node" in data
        assert "crawl" in data
        assert "index" in data

    def test_config_paths_are_strings(self, client: TestClient) -> None:
        resp = client.get("/config")
        data = resp.json()
        # All paths should be serialized as strings
        assert isinstance(data["node"]["data_dir"], str)

    def test_config_reload(self, client: TestClient, config: Config) -> None:
        resp = client.post("/config/reload")
        assert resp.status_code == 200
        assert resp.json()["status"] == "reloaded"


# ── Index stats endpoint ───────────────────────────────────


class TestIndexStats:
    def test_empty_index(self, client: TestClient) -> None:
        resp = client.get("/index/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["document_count"] == 0

    def test_index_with_docs(self, client: TestClient, config: Config) -> None:
        """Create a real index, add a doc, and check stats."""
        from infomesh.index.local_store import LocalStore

        store = LocalStore(db_path=config.index.db_path)
        store.add_document(
            url="https://example.com/test",
            title="Test",
            text="Hello world",
            raw_html_hash="abc123",
            text_hash="def456",
        )
        store.close()

        resp = client.get("/index/stats")
        data = resp.json()
        assert data["document_count"] == 1
        assert data["db_size_mb"] > 0


# ── Credits endpoint ────────────────────────────────────────


class TestCredits:
    def test_no_ledger(self, client: TestClient) -> None:
        resp = client.get("/credits/balance")
        assert resp.status_code == 200
        data = resp.json()
        assert data["balance"] == 0.0

    def test_with_ledger(self, client: TestClient, config: Config) -> None:
        """Create a proper ledger DB and check credits read."""
        from infomesh.credits.ledger import ActionType, CreditLedger

        ledger_path = config.node.data_dir / "credits.db"
        ledger = CreditLedger(ledger_path)
        # Earn 10 crawl credits (weight 1.0 × qty 10)
        ledger.record_action(ActionType.CRAWL, 10.0)
        # Earn 5 query credits (weight 0.5 × qty 10 = 5.0)
        ledger.record_action(ActionType.QUERY_PROCESS, 10.0)
        # Spend 2.0 credits
        ledger.spend(2.0, reason="search")
        ledger.close()

        resp = client.get("/credits/balance")
        data = resp.json()
        assert data["total_earned"] == 15.0
        assert data["total_spent"] == 2.0
        assert data["balance"] == 13.0


# ── Network endpoint ───────────────────────────────────────


class TestNetwork:
    def test_peers_default(self, client: TestClient) -> None:
        resp = client.get("/network/peers")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_peers" in data


# ── Helper functions ────────────────────────────────────────


class TestHelpers:
    def test_format_duration_seconds(self) -> None:
        assert _format_duration(30) == "30s"

    def test_format_duration_minutes(self) -> None:
        assert _format_duration(150) == "2m 30s"

    def test_format_duration_hours(self) -> None:
        result = _format_duration(3700)
        assert "1h" in result

    def test_redact_paths(self) -> None:
        d: dict = {"key": Path("/foo/bar"), "nested": {"p": Path("/baz")}}
        _redact_paths(d)
        assert d["key"] == "/foo/bar"
        assert d["nested"]["p"] == "/baz"
