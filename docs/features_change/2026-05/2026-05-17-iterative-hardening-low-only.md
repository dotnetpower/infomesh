# Iterative hardening to low-only residuals

## Summary

Repeated hardening review found lifecycle, credit-tier, and bounded-input issues.
The non-low findings were fixed and the remaining review notes are low-severity
polish only.

## Changes

- Closed the long-running `_serve` `AppContext` with async context management so
  stores, HTTP clients, ledgers, and vector resources are released on shutdown.
- Moved `AppContext(..., apply_os_priority=True)` priority application before
  heavy initialization.
- Fixed MCP credit tier reporting to use ledger contribution tier instead of
  current balance.
- Added snapshot import guards for oversized files and truncated headers.
- Made BGM asset download failures visible at warning level.
- Snapshot feed monitor dictionaries before iteration.
- Clarified EN/KO resource profile and off-peak credit documentation.

## Validation

- Added focused regression coverage for MCP tier calculation, snapshot import
  guards, credit-sync stale boundaries, and prior resource/BGM hardening paths.
