# P2P Search Reliability Hardening

## Summary

- Added bounded exact-read handling for P2P search stream messages so chunked frames are assembled safely and truncated/oversized payloads are rejected.
- Hardened search request and response parsing against malformed payload fields without crashing peer handlers.
- Added explicit peer query timeout telemetry and raised the peer response budget to 5 seconds for low-resource bootstrap conditions.
- Increased the CLI distributed-search peer wait to 20 seconds to absorb bootstrap restart and republish jitter.
- Optimized local snippet enhancement by skipping full-document passage selection when the existing FTS snippet is already useful, stabilizing search latency validation.
- Clamped peer search request limits, rejected invalid CLI `--limit` values, capped accepted peer result batches, and rejected non-finite remote scores.
- Hardened DHT pointer parsing and distributed result construction so malformed peer IDs, non-numeric scores, blank peer queries, and bad remote document IDs cannot crash or broaden searches.
- Added a one-shot CLI bootstrap retry when the first distributed attempt has no connected peers and returns no local or remote results, covering bootstrap restart jitter after redeploys.
- Skipped bootstrap multiaddrs that point to the local peer ID so bootstrap nodes do not self-dial, create self peer status, or emit secure negotiation noise after restarts.
- Redeployed the final wheel to the bootstrap VM with checksum verification and private temporary blob staging, then deleted the staging storage account.

## Verification

- Empty local index search verified against bootstrap after redeploy:
  - Query: `asyncio`
  - Local results: 0
  - Remote results: 5
  - First result: `Developing with asyncio` from `docs.python.org`
  - Reported latency: 733 ms distributed
- Bootstrap service verified active after deployment.
- Raw TCP reachability to `20.42.12.161:4001` verified.
- Local validation:
  - `uv run pytest tests/test_routing.py tests/test_cli_search.py -q --tb=short` -> 15 passed
  - `uv run pytest tests/test_routing.py tests/test_integrations.py -q --tb=short` -> 43 passed
  - `uv run pytest tests/test_cli_search.py tests/test_routing.py tests/test_integrations.py -q --tb=short` -> 50 passed
  - `uv run pytest tests/test_p2p_serve.py tests/test_cli_search.py tests/test_routing.py tests/test_integrations.py -q --tb=short` -> 67 passed
  - `uv run ruff check infomesh/ tests/`
  - `uv run ruff format --check .`
  - `uv run mypy infomesh/ --ignore-missing-imports`
  - `uv run pytest tests/ --ignore=tests/test_vector.py --ignore=tests/test_libp2p_spike.py -x -q --tb=short` -> 1900 passed
  - `uv build`
