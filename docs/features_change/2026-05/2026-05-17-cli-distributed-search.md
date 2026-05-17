# CLI Distributed Search

Date: 2026-05-17

## Summary

`infomesh search` now attempts local + peer distributed search by default when P2P is available. The command reuses the existing P2P bootstrap and `search_distributed()` path used by MCP, then falls back to local search if P2P is unavailable.

## User Impact

- `infomesh search "query"` can surface peer/bootstrap results.
- `infomesh search --local "query"` remains local-only for offline use.
- `infomesh search --local-only "query"` is accepted as an alias for `--local`.
- `--vector` remains an explicit local semantic/hybrid search mode.

## Verification

Focused CLI tests cover distributed default search, P2P fallback to local search, and `--local-only` avoiding P2P bootstrap.
