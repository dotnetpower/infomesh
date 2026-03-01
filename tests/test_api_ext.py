"""Tests for infomesh.api.extensions — OpenAPI, rate limiting, API keys."""

from __future__ import annotations

from infomesh.api.extensions import (
    APIKeyManager,
    RateLimitConfig,
    RateLimiter,
    generate_bash_completion,
    generate_openapi_spec,
    generate_zsh_completion,
)


class TestOpenAPISpec:
    def test_spec_structure(self) -> None:
        spec = generate_openapi_spec()
        assert spec["openapi"] == "3.1.0"
        assert "paths" in spec
        assert "info" in spec
        assert spec["info"]["title"] == "InfoMesh Search API"

    def test_paths_present(self) -> None:
        spec = generate_openapi_spec()
        paths = spec["paths"]
        assert "/search" in paths or len(paths) > 0


class TestRateLimiter:
    def test_allow(self) -> None:
        config = RateLimitConfig(requests_per_minute=60)
        rl = RateLimiter(config)
        assert rl.check("client1") is True

    def test_deny_after_limit(self) -> None:
        config = RateLimitConfig(requests_per_minute=2)
        rl = RateLimiter(config)
        assert rl.check("client1") is True
        assert rl.check("client1") is True
        assert rl.check("client1") is False

    def test_different_clients(self) -> None:
        config = RateLimitConfig(requests_per_minute=1)
        rl = RateLimiter(config)
        assert rl.check("client1") is True
        assert rl.check("client2") is True  # separate bucket


class TestAPIKeyManager:
    def test_create_and_validate(self) -> None:
        mgr = APIKeyManager()
        api_key = mgr.create_key("test_user")
        assert mgr.validate(api_key.key) is not None

    def test_invalid_key(self) -> None:
        mgr = APIKeyManager()
        assert mgr.validate("invalid_key_12345") is None

    def test_revoke(self) -> None:
        mgr = APIKeyManager()
        api_key = mgr.create_key("user1")
        mgr.revoke(api_key.key)
        assert mgr.validate(api_key.key) is None

    def test_list_keys(self) -> None:
        mgr = APIKeyManager()
        mgr.create_key("user1")
        mgr.create_key("user2")
        keys = mgr.list_keys()
        assert len(keys) == 2


class TestCompletions:
    def test_bash_completion(self) -> None:
        script = generate_bash_completion()
        assert isinstance(script, str)
        assert "infomesh" in script.lower() or "complete" in script.lower()

    def test_zsh_completion(self) -> None:
        script = generate_zsh_completion()
        assert isinstance(script, str)
        assert "infomesh" in script.lower() or "compdef" in script.lower()
