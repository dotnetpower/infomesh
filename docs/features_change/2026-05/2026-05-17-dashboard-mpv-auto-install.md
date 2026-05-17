# Dashboard mpv auto-install

## Summary

Dashboard BGM now attempts a non-interactive best-effort `mpv` install on first playback when `mpv` is missing before selecting an audio player. If installation is unavailable or fails, the existing `ffplay` fallback and no-player disable path remain unchanged.

## User impact

- Fresh systems can get smoother gapless BGM without manual `mpv` installation when root, passwordless sudo, or Homebrew installation is available.
- The automatic attempt is controlled by `[dashboard] bgm_auto_install_mpv`, which defaults to `true`.
- Install attempts never prompt for a password; unsupported systems continue safely with fallback behavior.

## Validation

- `uv run pytest tests/test_dashboard.py -q` — 69 passed
- `uv run ruff check infomesh/ tests/` — passed
- `uv run ruff format --check .` — passed
- `uv run mypy infomesh/ --ignore-missing-imports` — passed
- `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short` — 1844 passed
- `uv build` — passed
