"""Tests for infomesh.persistence.store — persistent analytics & state."""

from __future__ import annotations

import tempfile
from pathlib import Path

from infomesh.persistence.store import PersistentStore


class TestPersistentStore:
    def test_create_and_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentStore(Path(tmp) / "test.db")
            store.close()

    def test_record_analytics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentStore(Path(tmp) / "test.db")
            store.record_search(15.0)
            store.record_crawl()
            store.record_fetch()
            data = store.get_analytics()
            assert data["total_searches"] == 1
            assert data["total_crawls"] == 1
            assert data["total_fetches"] == 1
            store.close()

    def test_search_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentStore(Path(tmp) / "test.db")
            store.add_history("python tutorial", result_count=3, latency_ms=12.5)
            store.add_history("rust async", result_count=5, latency_ms=8.0)
            history = store.get_history(limit=10)
            assert len(history) == 2
            assert history[0]["query"] in ("python tutorial", "rust async")
            store.close()

    def test_webhooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentStore(Path(tmp) / "test.db")
            store.register_webhook("https://hooks.example.com/crawl")
            hooks = store.get_webhooks()
            assert len(hooks) == 1
            assert hooks[0] == "https://hooks.example.com/crawl"
            store.close()

    def test_presets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentStore(Path(tmp) / "test.db")
            store.save_preset("default_search", {"limit": 10, "format": "json"})
            preset = store.get_preset("default_search")
            assert preset is not None
            assert preset["limit"] == 10
            store.close()

    def test_preset_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentStore(Path(tmp) / "test.db")
            assert store.get_preset("nonexistent") is None
            store.close()

    def test_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PersistentStore(Path(tmp) / "test.db")
            store.save_session("s1", "python", "result text")
            sess = store.get_session("s1")
            assert sess is not None
            assert sess["last_query"] == "python"
            store.close()
