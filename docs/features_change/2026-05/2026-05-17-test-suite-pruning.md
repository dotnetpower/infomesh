# Test Suite Pruning

## Summary

Reduced test file sprawl by deleting redundant catch-all, duplicate, uncollected, and spike test files while preserving essential regression coverage in existing domain-focused test files.

## Removed Files

- `tests/test_new_features.py` — catch-all feature tests consolidated into domain tests.
- `tests/test_improvements.py` — catch-all improvement tests consolidated into domain tests.
- `tests/test_admin_api_integration.py` — duplicate admin API endpoint coverage consolidated into `tests/test_local_api.py`.
- `tests/integration_dashboard.py` — uncollected manual dashboard runner superseded by focused dashboard tests.
- `tests/test_libp2p_spike.py` — ignored libp2p spike tests removed from active test policy.

## Preserved Coverage

- Admin readiness, API key middleware, security headers, analytics state.
- LocalStore filters, pagination, and suggestions.
- Image alt extraction and crawl intelligence helpers.
- Search summary cache and multilingual keyword translation.
- Security ops API key rotation and audit logging.
- Benchmark and diagnostics smoke coverage.
- JSON search formatter fields.

## Verification

- Focused consolidated test run: `171 passed`.
- Full supported suite: `1841 passed` with `uv run pytest tests/ --ignore=tests/test_vector.py -x -q --tb=short`.
- `tests/test_libp2p_spike.py` no longer exists, so active CI and troubleshooting commands only ignore `tests/test_vector.py`.
