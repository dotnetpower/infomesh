# Runtime Resource Bounds

## Summary

Bounded several long-running runtime paths that could otherwise accumulate state or subprocess handles during sustained crawling, dashboard use, or admin API polling.

## Changes

- Crawl workers now always release scheduler domain pending counts, including DHT lock rejection and unexpected exception paths.
- Scheduler domain tracking prunes stale domains before reaching the hard cap and cleanup calls no longer create new domain state.
- Admin API rate-limit buckets now use bounded deques and ignore non-local clients before allocating bucket state.
- SLO latency measurements now use bounded deques instead of list slicing.
- Dashboard SFX subprocess handles are reaped during BGM health checks and capped during bursty SFX playback.

## Validation

- `uv run pytest tests/test_crawler.py tests/test_local_api.py tests/test_dashboard.py tests/test_infrastructure.py -q --tb=short` — 174 passed
- `uv run ruff check infomesh/ tests/` — passed
- `uv run ruff format --check .` — passed
- `uv run mypy infomesh/ --ignore-missing-imports` — passed
- `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short` — 1851 passed
- `uv build` — passed
