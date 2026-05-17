# CI/CD workflow hardening

## Summary

CI/CD workflows were tightened so type regressions and stale lockfile state are caught before release, and manual PyPI publishing has the permissions and retry behavior it needs.

## User impact

- CI now treats mypy failures as blocking instead of advisory.
- CI and auto-release install dependencies with `uv sync --dev --locked`.
- Auto-release runs format and mypy checks before publishing.
- Auto-release updates `uv.lock` after version bumps and commits it with the version files.
- Manual trusted publishing grants `contents: read` for checkout and uses `skip-existing` for safe retry after partial publish failures.

## Validation

- Workflow YAML parse: `ruby -e 'require "yaml"; ARGV.each { |path| YAML.load_file(path) }' .github/workflows/*.yml` — passed
- Locked dependency sync: `uv sync --dev --locked` — passed
- `uv run ruff check infomesh/ tests/` — passed
- `uv run ruff format --check .` — passed
- `uv run mypy infomesh/ --ignore-missing-imports` — passed
- `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short` — 1844 passed
- `uv build` — passed

`actionlint` was not available in the local environment and Go was not installed
to run it ephemerally.
