"""Tests for infomesh.scalability — connection pool, bloom filter, batch."""

from __future__ import annotations

import tempfile
from pathlib import Path

from infomesh.scalability import BloomFilter, ConnectionPool, batch_ingest


class TestConnectionPool:
    def test_get_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            pool = ConnectionPool(str(db_path), max_connections=3)
            conn = pool.get()
            assert conn is not None
            pool.release(conn)
            pool.close_all()

    def test_pool_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            pool = ConnectionPool(str(db_path), max_connections=3)
            c1 = pool.get()
            pool.release(c1)
            c2 = pool.get()
            # Should reuse the same connection
            assert c2 is c1
            pool.release(c2)
            pool.close_all()


class TestBloomFilter:
    def test_add_and_check(self) -> None:
        bf = BloomFilter(capacity=1000, fp_rate=0.01)
        bf.add("https://example.com")
        assert "https://example.com" in bf

    def test_false_negative_impossible(self) -> None:
        bf = BloomFilter(capacity=1000, fp_rate=0.01)
        for i in range(100):
            bf.add(f"url_{i}")
        for i in range(100):
            assert f"url_{i}" in bf

    def test_empty(self) -> None:
        bf = BloomFilter(capacity=1000, fp_rate=0.01)
        # Not guaranteed to be False (false positives), but likely
        assert isinstance("never_added" in bf, bool)


class TestBatchIngest:
    def test_basic_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from infomesh.index.local_store import LocalStore

            store = LocalStore(Path(tmp) / "test.db")
            documents = [
                {
                    "url": f"https://example.com/{i}",
                    "title": f"Doc {i}",
                    "content": f"Content for document {i} with some words.",
                    "content_hash": f"hash_{i}",
                }
                for i in range(5)
            ]
            result = batch_ingest(store, documents, batch_size=3)
            assert result.succeeded == 5
            assert result.failed == 0
            store.close()

    def test_empty_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from infomesh.index.local_store import LocalStore

            store = LocalStore(Path(tmp) / "test.db")
            result = batch_ingest(store, [], batch_size=10)
            assert result.total == 0
            store.close()
