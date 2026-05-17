# Documentation synchronization hardening

## Summary

Active documentation was synchronized with the current package version, MCP tool surface, dashboard/BGM behavior, test policy, and CI/CD validation commands.

## User impact

- Getting started docs now show the current `0.1.13` startup example and consolidated MCP tool names.
- Dashboard docs now describe 6 TUI tabs, current version examples, and BGM playback via `mpv`/`ffplay` without obsolete `aplay` references.
- Publishing and tech-stack docs now use current locked `uv` commands and current package artifact examples.
- README and CONTRIBUTING now use the supported test command and current project/test statistics.
- EN/KO docs and Copilot instructions were kept in sync for the changed behavior.

## Validation

- Stale active-doc scan for old versions, obsolete BGM players, legacy MCP examples, and old test commands — clean except historical feature-change evidence.
- `uv run ruff check infomesh/ tests/` — passed
- `uv run ruff format --check .` — passed
- `uv run mypy infomesh/ --ignore-missing-imports` — passed
- `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short` — 1844 passed
- `uv build` — passed
