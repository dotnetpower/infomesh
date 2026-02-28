#!/usr/bin/env python3
"""Example: Hybrid search (FTS5 keyword + vector semantic).

Combines BM25 keyword search with ChromaDB embedding-based
semantic search for higher quality results.

Requires optional dependencies:
    uv add chromadb sentence-transformers

Usage:
    uv run python examples/hybrid_search.py "how to handle errors in async python"
"""

from __future__ import annotations

import sys

from infomesh.config import load_config
from infomesh.index.local_store import LocalStore
from infomesh.search.formatter import format_hybrid_results


def main() -> None:
    query = " ".join(sys.argv[1:]) or "async error handling"
    config = load_config()

    store = LocalStore(
        db_path=config.index.db_path,
        compression_enabled=config.storage.compression_enabled,
        compression_level=config.storage.compression_level,
    )

    try:
        from infomesh.index.vector_store import VectorStore
        from infomesh.search.query import search_hybrid
    except ImportError:
        print("Error: chromadb and/or sentence-transformers not installed.")
        print("Install with: uv add chromadb sentence-transformers")
        store.close()
        return

    vec_store = VectorStore(
        persist_dir=config.node.data_dir / "chroma",
        model_name=config.index.embedding_model,
    )

    result = search_hybrid(store, vec_store, query, limit=5)
    print(format_hybrid_results(result))

    vec_store.close()
    store.close()


if __name__ == "__main__":
    main()
