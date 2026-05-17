# Bounded Input Hardening

Date: 2026-05-17

## Summary

- Added bounded Common Crawl WET input reads for local files, remote downloads, and gzip expansion.
- Added strict TCP port validation before port checks and firewall auto-open command generation.
- Documented the valid `network.listen_port` range in English and Korean getting-started docs.

## Verification

- `uv run pytest tests/test_commoncrawl.py tests/test_port_check.py -q --tb=short`
- `uv run ruff check infomesh/index/commoncrawl.py infomesh/resources/port_check.py tests/test_commoncrawl.py tests/test_port_check.py`
- `uv run ruff format --check infomesh/index/commoncrawl.py infomesh/resources/port_check.py tests/test_commoncrawl.py tests/test_port_check.py`
- `uv run mypy infomesh/index/commoncrawl.py infomesh/resources/port_check.py --ignore-missing-imports`
