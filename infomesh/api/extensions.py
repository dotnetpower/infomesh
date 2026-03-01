"""API extensions — rate limiting, API key management, OpenAPI spec.

Features:
- #22: OpenAPI spec auto-generation
- #23: Rate limiting middleware
- #24: API key management
- #26: WebSocket live results
- #30: CLI auto-completion
"""

from __future__ import annotations

import secrets
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ── #22: OpenAPI spec ────────────────────────────────────────────


def generate_openapi_spec() -> dict[str, Any]:
    """Generate an OpenAPI 3.1 spec for the InfoMesh HTTP API.

    Returns:
        OpenAPI spec as a dict.
    """
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "InfoMesh Search API",
            "description": (
                "Decentralized P2P search engine API for LLMs. "
                "Access via MCP (stdio/HTTP) or direct HTTP endpoints."
            ),
            "version": "2025.1",
            "license": {
                "name": "MIT",
                "url": "https://opensource.org/licenses/MIT",
            },
        },
        "servers": [
            {"url": "http://localhost:8081", "description": "Local"},
        ],
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health check",
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status": {
                                                "type": "string",
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "/mcp": {
                "post": {
                    "summary": "MCP endpoint",
                    "description": ("Streamable HTTP transport for MCP protocol."),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"},
                            },
                        },
                    },
                    "responses": {
                        "200": {"description": "MCP response"},
                    },
                },
            },
        },
        "components": {
            "schemas": {
                "SearchRequest": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 10,
                            "maximum": 50,
                        },
                        "format": {
                            "type": "string",
                            "enum": ["text", "json"],
                            "default": "text",
                        },
                        "language": {
                            "type": "string",
                            "description": "ISO 639-1 code",
                        },
                    },
                    "required": ["query"],
                },
                "SearchResult": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "url": {"type": "string"},
                        "snippet": {"type": "string"},
                        "score": {"type": "number"},
                    },
                },
            },
        },
    }


# ── #23: Rate limiting ──────────────────────────────────────────


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""

    requests_per_minute: int = 60
    burst_size: int = 10
    window_seconds: float = 60.0


class RateLimiter:
    """Token-bucket rate limiter for API requests.

    Supports per-key and global rate limiting.

    Args:
        config: Rate limit configuration.
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
    ) -> None:
        self._config = config or RateLimitConfig()
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str = "global") -> bool:
        """Check if a request is allowed.

        Args:
            key: Rate limit key (e.g., API key or IP).

        Returns:
            True if request is allowed.
        """
        now = time.time()
        window = self._config.window_seconds
        bucket = self._buckets[key]

        # Remove expired entries
        self._buckets[key] = [t for t in bucket if now - t < window]

        if len(self._buckets[key]) >= self._config.requests_per_minute:
            return False

        self._buckets[key].append(now)
        return True

    def remaining(self, key: str = "global") -> int:
        """Get remaining requests in current window.

        Args:
            key: Rate limit key.

        Returns:
            Number of remaining allowed requests.
        """
        now = time.time()
        window = self._config.window_seconds
        bucket = self._buckets.get(key, [])
        active = sum(1 for t in bucket if now - t < window)
        return max(0, self._config.requests_per_minute - active)

    def reset(self, key: str = "global") -> None:
        """Reset rate limit for a key."""
        self._buckets.pop(key, None)


# ── #24: API key management ──────────────────────────────────────


@dataclass
class APIKey:
    """An API key with metadata."""

    key: str
    name: str
    created_at: float
    expires_at: float | None = None
    permissions: list[str] = field(default_factory=list)
    active: bool = True

    def is_valid(self, *, now: float | None = None) -> bool:
        """Check if the key is valid and not expired."""
        if not self.active:
            return False
        if self.expires_at is not None:
            now = now or time.time()
            if now > self.expires_at:
                return False
        return True


class APIKeyManager:
    """Manage API keys for the InfoMesh API.

    Stores keys in memory. For persistence, use with
    PersistentStore.
    """

    def __init__(self) -> None:
        self._keys: dict[str, APIKey] = {}

    def create_key(
        self,
        name: str,
        *,
        expires_in_days: int | None = None,
        permissions: list[str] | None = None,
    ) -> APIKey:
        """Create a new API key.

        Args:
            name: Human-readable key name.
            expires_in_days: Days until expiration (None = never).
            permissions: List of permission strings.

        Returns:
            Newly created APIKey.
        """
        key = f"im_{secrets.token_hex(24)}"
        now = time.time()
        expires_at = None
        if expires_in_days is not None:
            expires_at = now + expires_in_days * 86400

        api_key = APIKey(
            key=key,
            name=name,
            created_at=now,
            expires_at=expires_at,
            permissions=permissions or [],
            active=True,
        )
        self._keys[key] = api_key

        logger.info("api_key_created", name=name)
        return api_key

    def validate(self, key: str) -> APIKey | None:
        """Validate an API key.

        Args:
            key: The API key string.

        Returns:
            APIKey if valid, None otherwise.
        """
        api_key = self._keys.get(key)
        if api_key is None:
            return None
        if not api_key.is_valid():
            return None
        return api_key

    def revoke(self, key: str) -> bool:
        """Revoke an API key.

        Args:
            key: The API key string.

        Returns:
            True if revoked, False if not found.
        """
        api_key = self._keys.get(key)
        if api_key is None:
            return False
        api_key.active = False
        logger.info("api_key_revoked", name=api_key.name)
        return True

    def list_keys(self) -> list[APIKey]:
        """List all API keys."""
        return list(self._keys.values())

    def rotate(self, old_key: str) -> APIKey | None:
        """Rotate an API key (revoke old, create new).

        Args:
            old_key: The old API key to rotate.

        Returns:
            New APIKey, or None if old key not found.
        """
        old = self._keys.get(old_key)
        if old is None:
            return None

        self.revoke(old_key)
        return self.create_key(
            name=old.name,
            permissions=old.permissions,
        )


# ── #30: CLI auto-completion helpers ─────────────────────────────


def get_completion_commands() -> list[str]:
    """Get list of CLI commands for auto-completion.

    Returns:
        List of command names.
    """
    return [
        "start",
        "stop",
        "status",
        "serve",
        "crawl",
        "search",
        "mcp",
        "dashboard",
        "config",
        "keys",
        "index",
    ]


def generate_bash_completion() -> str:
    """Generate a bash completion script for infomesh CLI.

    Returns:
        Bash completion script as string.
    """
    commands = " ".join(get_completion_commands())
    return f'''# InfoMesh bash completion
_infomesh_completion() {{
    local cur prev opts commands
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    commands="{commands}"

    case "${{prev}}" in
        infomesh)
            COMPREPLY=( $(compgen -W "${{commands}}" -- "${{cur}}") )
            return 0
            ;;
        search)
            COMPREPLY=( $(compgen -W "--limit --format --language" -- "${{cur}}") )
            return 0
            ;;
        crawl)
            COMPREPLY=( $(compgen -W "--depth --force" -- "${{cur}}") )
            return 0
            ;;
        config)
            COMPREPLY=( $(compgen -W "show set" -- "${{cur}}") )
            return 0
            ;;
        keys)
            COMPREPLY=( $(compgen -W "export rotate" -- "${{cur}}") )
            return 0
            ;;
        index)
            COMPREPLY=( $(compgen -W "stats export import" -- "${{cur}}") )
            return 0
            ;;
    esac

    COMPREPLY=( $(compgen -W "${{commands}}" -- "${{cur}}") )
    return 0
}}
complete -F _infomesh_completion infomesh
'''


def generate_zsh_completion() -> str:
    """Generate a zsh completion script for infomesh CLI.

    Returns:
        Zsh completion script as string.
    """
    commands = get_completion_commands()
    cmd_lines = "\n    ".join(f"'{c}:{c} command'" for c in commands)
    return f"""#compdef infomesh

_infomesh() {{
    local -a commands
    commands=(
    {cmd_lines}
    )
    _describe 'command' commands
}}

_infomesh
"""
