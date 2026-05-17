# CLI resource and BGM hardening

## Summary

InfoMesh now separates long-running crawl/MCP worker priority from the
interactive dashboard/BGM process more carefully.

## Changes

- Apply configured CPU nice and Linux `ionice` priority early in `_serve` worker
  processes.
- Keep `AppContext` from lowering the caller process priority unless a heavy
  command explicitly opts in.
- Fix crawl-loop governor overload handling to use public governor state instead
  of missing private attributes.
- Launch BGM/SFX subprocesses in an isolated session and attempt best-effort
  normal-priority playback.

## Validation

- Added focused tests for governor state access, I/O priority application,
  crawl-loop backpressure, and BGM subprocess isolation.