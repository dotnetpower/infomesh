#!/usr/bin/env python3
"""Example: Fetch and display a web page's full text.

Uses the service layer to fetch content — returns cached
version if available, otherwise crawls live.

Usage:
    uv run python examples/fetch_page.py https://example.com
"""

from __future__ import annotations

import asyncio
import sys

from infomesh.config import load_config
from infomesh.services import AppContext, fetch_page_async


async def fetch(url: str) -> None:
    config = load_config()
    ctx = AppContext(config)

    max_size = config.index.max_doc_size_kb * 1024
    cache_ttl = config.storage.cache_ttl_days * 86400

    result = await fetch_page_async(
        url,
        store=ctx.store,
        worker=ctx.worker,
        vector_store=ctx.vector_store,
        max_size_bytes=max_size,
        cache_ttl_seconds=cache_ttl,
    )

    if result.success:
        print(f"Title: {result.title}")
        print(f"URL:   {result.url}")
        print(f"Cache: {'yes' if result.is_cached else 'no (live crawl)'}")
        print(f"Size:  {len(result.text)} chars")
        print("─" * 60)
        print(result.text[:2000])
        if len(result.text) > 2000:
            print(f"\n... ({len(result.text) - 2000} more chars)")
    else:
        print(f"Failed: {result.error}")

    await ctx.close_async()


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"
    asyncio.run(fetch(url))


if __name__ == "__main__":
    main()
