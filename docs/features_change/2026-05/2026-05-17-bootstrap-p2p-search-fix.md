# Bootstrap P2P Search Fix

## Summary

- Redeployed the bootstrap VM with the current distributed-index republish code.
- Added a CLI distributed-search connection wait so one-shot searches give P2P bootstrap time to connect.
- Added connected-peer fallback routing when DHT keyword pointers are not visible from the querying node yet.
- Fixed P2P search stream decoding to use the length-prefixed wire format produced by `encode_message()`.
- Closed a trio memory-channel send clone after each peer query task so routed searches terminate cleanly.

## Verification

- Bootstrap VM local index: 8077 documents.
- Bootstrap DHT republish verified with nonzero `keys_published` and `puts_performed`.
- Empty local index search verified against bootstrap:
  - Query: `asyncio`
  - Local results: 0
  - Remote results: 5
  - First result: `Developing with asyncio` from `docs.python.org`.
- Local validation:
  - `uv run ruff check infomesh/ tests/`
  - `uv run ruff format --check .`
  - `uv run mypy infomesh/ --ignore-missing-imports`
  - `uv run pytest tests/ --ignore=tests/test_vector.py --ignore=tests/test_libp2p_spike.py -x -q --tb=short` -> 1896 passed
  - `uv build`