"""Persistent storage for analytics, webhooks, sessions, history, presets.

Features:
- #31: Persistent analytics (survives restart)
- #32: Persistent webhook registry
- #33: Persistent session storage (with TTL)
- #34: Search history (opt-in, privacy-preserving)
- #35: User filter presets
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


class PersistentStore:
    """SQLite-based persistent storage for MCP server state.

    Stores analytics, webhooks, sessions, search history,
    and user presets. All data is local-only.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = str(db_path) if db_path else ":memory:"
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS analytics (
                key TEXT PRIMARY KEY,
                value REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS webhooks (
                url TEXT PRIMARY KEY,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                last_query TEXT NOT NULL DEFAULT '',
                last_results TEXT NOT NULL DEFAULT '',
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS search_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                result_count INTEGER NOT NULL DEFAULT 0,
                latency_ms REAL NOT NULL DEFAULT 0,
                searched_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS presets (
                name TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
        """)
        # Initialize analytics counters if not present
        for key in (
            "total_searches",
            "total_crawls",
            "total_fetches",
            "latency_sum",
        ):
            self._conn.execute(
                "INSERT OR IGNORE INTO analytics (key, value) VALUES (?, 0)",
                (key,),
            )
        self._conn.commit()

    # ── #31: Persistent analytics ──────────────────────────────────

    def record_search(self, latency_ms: float) -> None:
        """Record a search event."""
        self._conn.execute(
            "UPDATE analytics SET value = value + 1 WHERE key = 'total_searches'",
        )
        self._conn.execute(
            "UPDATE analytics SET value = value + ? WHERE key = 'latency_sum'",
            (latency_ms,),
        )
        self._conn.commit()

    def record_crawl(self) -> None:
        """Record a crawl event."""
        self._conn.execute(
            "UPDATE analytics SET value = value + 1 WHERE key = 'total_crawls'",
        )
        self._conn.commit()

    def record_fetch(self) -> None:
        """Record a fetch event."""
        self._conn.execute(
            "UPDATE analytics SET value = value + 1 WHERE key = 'total_fetches'",
        )
        self._conn.commit()

    def get_analytics(self) -> dict[str, object]:
        """Get all analytics data."""
        cur = self._conn.execute(
            "SELECT key, value FROM analytics",
        )
        data: dict[str, float] = {}
        for row in cur:
            data[row["key"]] = row["value"]

        total = data.get("total_searches", 0)
        latency = data.get("latency_sum", 0)
        avg = latency / total if total > 0 else 0.0

        return {
            "total_searches": int(total),
            "total_crawls": int(data.get("total_crawls", 0)),
            "total_fetches": int(data.get("total_fetches", 0)),
            "avg_latency_ms": round(avg, 1),
        }

    # ── #32: Persistent webhook registry ───────────────────────────

    def register_webhook(self, url: str) -> None:
        """Register a webhook URL."""
        self._conn.execute(
            "INSERT OR REPLACE INTO webhooks (url, created_at) VALUES (?, ?)",
            (url, time.time()),
        )
        self._conn.commit()

    def unregister_webhook(self, url: str) -> bool:
        """Unregister a webhook URL."""
        cur = self._conn.execute(
            "DELETE FROM webhooks WHERE url = ?",
            (url,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_webhooks(self) -> list[str]:
        """Get all registered webhook URLs."""
        cur = self._conn.execute(
            "SELECT url FROM webhooks ORDER BY created_at",
        )
        return [row["url"] for row in cur]

    # ── #33: Persistent session storage ────────────────────────────

    def save_session(
        self,
        session_id: str,
        last_query: str,
        last_results: str,
    ) -> None:
        """Save or update a session."""
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(session_id, last_query, last_results, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (session_id, last_query, last_results[:2000], time.time()),
        )
        self._conn.commit()

    def get_session(
        self,
        session_id: str,
    ) -> dict[str, object] | None:
        """Get session data by ID."""
        cur = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def expire_sessions(self, ttl_seconds: float = 3600) -> int:
        """Remove sessions older than TTL."""
        cutoff = time.time() - ttl_seconds
        cur = self._conn.execute(
            "DELETE FROM sessions WHERE updated_at < ?",
            (cutoff,),
        )
        self._conn.commit()
        return cur.rowcount

    # ── #34: Search history ────────────────────────────────────────

    def add_history(
        self,
        query: str,
        result_count: int = 0,
        latency_ms: float = 0,
    ) -> None:
        """Add a search query to history."""
        self._conn.execute(
            "INSERT INTO search_history "
            "(query, result_count, latency_ms, searched_at) "
            "VALUES (?, ?, ?, ?)",
            (query, result_count, latency_ms, time.time()),
        )
        self._conn.commit()

    def get_history(
        self,
        *,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        """Get recent search history."""
        cur = self._conn.execute(
            "SELECT query, result_count, latency_ms, searched_at "
            "FROM search_history "
            "ORDER BY searched_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur]

    def clear_history(self) -> int:
        """Clear all search history."""
        cur = self._conn.execute("DELETE FROM search_history")
        self._conn.commit()
        return cur.rowcount

    # ── #35: User presets ──────────────────────────────────────────

    def save_preset(
        self,
        name: str,
        config: dict[str, object],
    ) -> None:
        """Save a named filter preset."""
        self._conn.execute(
            "INSERT OR REPLACE INTO presets "
            "(name, config_json, created_at) VALUES (?, ?, ?)",
            (name, json.dumps(config), time.time()),
        )
        self._conn.commit()

    def get_preset(self, name: str) -> dict[str, object] | None:
        """Get a preset by name."""
        cur = self._conn.execute(
            "SELECT config_json FROM presets WHERE name = ?",
            (name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        result: dict[str, object] = json.loads(row["config_json"])
        return result

    def list_presets(self) -> list[str]:
        """List all preset names."""
        cur = self._conn.execute(
            "SELECT name FROM presets ORDER BY created_at",
        )
        return [row["name"] for row in cur]

    def delete_preset(self, name: str) -> bool:
        """Delete a preset."""
        cur = self._conn.execute(
            "DELETE FROM presets WHERE name = ?",
            (name,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── Lifecycle ──────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> PersistentStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
