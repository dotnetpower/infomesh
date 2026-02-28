#!/usr/bin/env python3
"""Example: Basic local search using InfoMesh.

Demonstrates how to use the LocalStore directly to search
your local index without running the full node.

Usage:
    uv run python examples/basic_search.py "python asyncio"
"""

from __future__ import annotations

import sys

from infomesh.config import load_config
from infomesh.index.local_store import LocalStore
from infomesh.search.formatter import format_fts_results
from infomesh.search.query import search_local


def main() -> None:
    query = " ".join(sys.argv[1:]) or "python"
    config = load_config()

    # Open the local SQLite FTS5 index
    store = LocalStore(
        db_path=config.index.db_path,
        compression_enabled=config.storage.compression_enabled,
        compression_level=config.storage.compression_level,
    )

    # Search with BM25 ranking
    result = search_local(store, query, limit=5)
    print(format_fts_results(result))

    store.close()


if __name__ == "__main__":
    main()
