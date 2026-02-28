#!/usr/bin/env python3
"""Example: Crawl a URL and search it immediately.

Demonstrates the full crawl → index → search pipeline
using InfoMesh as a Python library.

Usage:
    uv run python examples/crawl_and_search.py https://docs.python.org/3/library/asyncio.html
"""

from __future__ import annotations

import asyncio
import sys

from infomesh.config import load_config
from infomesh.search.formatter import format_fts_results
from infomesh.search.query import search_local
from infomesh.services import AppContext, index_document


async def crawl_and_search(url: str, query: str | None = None) -> None:
    config = load_config()
    ctx = AppContext(config)

    # 1. Crawl the URL
    print(f"Crawling: {url}")
    result = await ctx.worker.crawl_url(url, depth=0)

    if not result.success or not result.page:
        print(f"Crawl failed: {result.error}")
        await ctx.close_async()
        return

    print(f"  Title: {result.page.title}")
    print(f"  Text length: {len(result.page.text)} chars")
    print(f"  Links discovered: {len(result.discovered_links)}")
    print(f"  Elapsed: {result.elapsed_ms:.0f}ms")

    # 2. Index into SQLite FTS5
    doc_id = index_document(result.page, ctx.store, ctx.vector_store)
    if doc_id:
        print(f"  Indexed as doc #{doc_id}")
    else:
        print("  Already indexed (duplicate)")

    # 3. Search the index
    search_query = query or result.page.title.split()[0]
    print(f"\nSearching for: '{search_query}'")
    search_result = search_local(ctx.store, search_query, limit=3)
    print(format_fts_results(search_result))

    await ctx.close_async()


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://docs.python.org/3/library/asyncio.html"
    query = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(crawl_and_search(url, query))


if __name__ == "__main__":
    main()
