# Month-Long Runtime Stability Hardening

## Summary

Added guards for month-long node operation by preventing duplicate node starts and making the resource governor react to the InfoMesh process RSS, not only system-wide memory pressure.

## Changes

- `infomesh start` now checks the node PID file and refuses to launch a duplicate live node for the same data directory.
- `_serve` now performs the same live PID guard when invoked directly.
- PID file writes are atomic, stale/invalid PID files are cleaned, and shutdown removes the PID file only when it still belongs to the current process.
- `ResourceGovernor` now samples this process's resident memory in MiB and escalates degradation when it approaches or exceeds the configured memory soft limit.
- Added regression tests for PID file lifecycle and process-RSS-based governor degradation.

## Validation

- `uv run ruff check infomesh/ tests/` — passed
- `uv run ruff format --check .` — passed
- `uv run mypy infomesh/ --ignore-missing-imports` — passed
- `uv run pytest tests/test_p2p_serve.py tests/test_resources.py tests/test_crawler.py tests/test_local_api.py tests/test_dashboard.py tests/test_infrastructure.py -q --tb=short` — 233 passed
- `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short` — 1857 passed
- `uv build` — passed
