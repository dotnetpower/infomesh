# Distributed Index Republish

Date: 2026-05-17

## Summary

Bootstrap and MCP nodes now republish existing local index documents to the distributed DHT at startup, and newly crawled/indexed pages are published immediately when P2P is available.

## Changes

- Added asyncio-safe P2P publish bridge on `InfoMeshNode`.
- Reused the node-owned `DistributedIndex` so publish statistics and DHT writes reflect actual node activity.
- Added local index republish support via public `LocalStore.get_documents_for_publish()`.
- Changed distributed batch publishing to group pointers per keyword.
- Changed DHT keyword publishing to merge existing pointer lists instead of overwriting them.
- Updated `infomesh peer test` to resolve the `default` bootstrap alias before TCP checks.

## Verification

- `uv run pytest tests/test_distributed.py tests/test_dht.py tests/test_services.py tests/test_p2p_serve.py -q --tb=short`
- `uv run ruff check infomesh/services.py infomesh/p2p/node.py infomesh/p2p/dht.py infomesh/index/distributed.py infomesh/index/local_store.py infomesh/crawler/crawl_loop.py infomesh/cli/peer.py infomesh/cli/serve.py infomesh/mcp/server.py infomesh/mcp/handlers.py tests/test_distributed.py tests/test_dht.py tests/test_services.py tests/test_p2p_serve.py`
