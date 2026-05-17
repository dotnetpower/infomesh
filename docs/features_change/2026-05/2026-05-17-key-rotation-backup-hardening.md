# Key Rotation Backup Hardening

Date: 2026-05-17

## Summary

Key rotation backups now use nanosecond-resolution directory names with a collision fallback so rapid consecutive rotations cannot reuse the same backup directory.

## Changes

- Replaced second-level `backup-<timestamp>` naming with `backup-<timestamp_ns>`.
- Added a bounded suffix fallback if a backup directory already exists.
- Tightened the regression test to rotate twice immediately without sleeping.

## Verification

- `uv run pytest tests/test_key_rotation.py -q --tb=short`
