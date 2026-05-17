# Second-Pass Runtime Resilience Hardening

## Summary

Added a second pass of runtime reliability hardening focused on duplicate-start prevention, PID reuse safety, graceful stop behavior, runtime heartbeat observability, and long-lived task cleanup.

## Hardening Items

1. Added `infomesh.runtime` as the shared home for runtime process coordination.
2. Centralized node PID file path handling.
3. Centralized startup lock file path handling.
4. Centralized runtime status file path handling.
5. Added live process probing for PID files.
6. Added InfoMesh process identity validation to reduce PID reuse false positives.
7. Clean invalid PID files automatically.
8. Clean stale PID files automatically.
9. Preserve PID files that belong to another live InfoMesh process.
10. Added atomic PID file writes.
11. Added owner-aware PID file cleanup.
12. Added a cross-process startup lock for racing starts.
13. Made `infomesh start` hold the startup lock through launch.
14. Made the parent `infomesh start` write the child PID immediately after launch.
15. Made direct `_serve` invocation use the same startup lock and PID validation.
16. Made `infomesh stop` send SIGTERM and wait for graceful process exit.
17. Made `infomesh stop` leave the PID file in place when the process does not exit within timeout.
18. Added runtime heartbeat snapshot generation from `_serve`.
19. Added runtime status atomic writes.
20. Added stale runtime status detection.
21. Added corrupt runtime status cleanup.
22. Added stopped-state runtime status marking on shutdown.
23. Exposed runtime heartbeat fields through admin `/status`.
24. Exposed runtime heartbeat fields through detailed `/health?detail=1`.
25. Exposed process memory through admin `/metrics`.
26. Extended `ResourceGovernor` state with process memory limit and ratio.
27. Awaited the MCP HTTP app task after cancellation during shutdown.
28. Made dashboard `stop_all` use graceful shutdown and owner-aware PID cleanup.
29. Made `infomesh update` restart wait for the old node to exit before launching the replacement process.
30. Added regression tests for runtime PID helpers, startup locks, runtime status, graceful stop, admin status exposure, and process-RSS governor state.

## Documentation

- Updated `docs/en/05-getting-started.md`.
- Updated `docs/ko/05-getting-started.md`.
- Updated `.github/copilot-instructions.md`.

## Validation

- `uv run ruff check infomesh/ tests/` — passed
- `uv run ruff format --check .` — passed
- `uv run mypy infomesh/ --ignore-missing-imports` — passed
- `uv run pytest tests/test_runtime.py tests/test_p2p_serve.py tests/test_resources.py tests/test_local_api.py -q --tb=short` — 102 passed
- `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short` — 1875 passed
- `uv build` — passed
