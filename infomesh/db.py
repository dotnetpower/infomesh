"""SQLite store base class — shared DB lifecycle and WAL setup.

All SQLite-backed stores in InfoMesh (CreditLedger, FarmingDetector,
LLMReputationTracker, TrustStore, DeduplicatorDB, etc.) share the same
init / WAL / close / context-manager boilerplate.  This module extracts
that into a reusable base class so subclasses only provide a schema and
business logic.

Usage::

    class MyStore(SQLiteStore):
        _SCHEMA = '''
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
        '''

        def add(self, name: str) -> None:
            self._conn.execute("INSERT INTO items (name) VALUES (?)", (name,))
            self._conn.commit()

        with MyStore("/tmp/my.db") as store:
            store.add("hello")
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import structlog

logger = structlog.get_logger()


class SQLiteStore:
    """Base class for SQLite-backed stores.

    Provides:
    - Connection setup with WAL journal mode
    - Schema initialization via class-level ``_SCHEMA``
    - Context-manager support (``with`` statement)
    - Clean ``close()`` without fragile ``__del__``

    Subclasses **must** define ``_SCHEMA`` as a class attribute containing
    the ``CREATE TABLE`` / ``CREATE INDEX`` SQL.
    """

    _SCHEMA: str = ""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        check_same_thread: bool = False,
        row_factory: type | None = None,
        extra_pragmas: list[str] | None = None,
    ) -> None:
        path = str(db_path) if db_path else ":memory:"
        self._db_path = path
        self._conn = sqlite3.connect(path, check_same_thread=check_same_thread)

        if row_factory is not None:
            self._conn.row_factory = row_factory  # type: ignore[assignment]

        # Standard pragmas
        self._conn.execute("PRAGMA journal_mode=WAL")
        for pragma in extra_pragmas or []:
            self._conn.execute(pragma)

        # Initialize schema
        if self._SCHEMA:
            self._conn.executescript(self._SCHEMA)
            self._conn.commit()

        logger.info(
            f"{type(self).__name__.lower()}_opened",
            db=path,
        )

    # ── Context manager ────────────────────────────────────────────

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── Lifecycle ──────────────────────────────────────────────────

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
