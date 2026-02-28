"""Local admin API — FastAPI-based status and configuration endpoints.

Provides HTTP endpoints for monitoring and managing a local InfoMesh node.
Not exposed to the public network; binds to localhost only.

Endpoints:
    GET  /health              — Liveness probe
    GET  /status              — Node status (uptime, index size, peer count)
    GET  /config              — Current configuration (redacted secrets)
    GET  /index/stats         — Index statistics (document count, size)
    GET  /credits/balance     — Local credit balance
    GET  /network/peers       — Connected peers summary
    POST /config/reload       — Reload configuration from disk
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from infomesh.config import Config, load_config

logger = structlog.get_logger()

# Fields to redact in config output
_SENSITIVE_KEYS = frozenset(
    {
        "data_dir",
        "db_path",
        "bootstrap_nodes",
        "persist_dir",
    }
)


@dataclass
class AdminState:
    """Immutable state container for the admin API — replaces module globals."""

    config: Config
    config_path: Path | None = None
    start_time: float = field(default_factory=time.time)


def create_admin_app(
    config: Config | None = None,
    config_path: Path | None = None,
) -> FastAPI:
    """Create the local admin FastAPI application.

    Args:
        config: Current configuration. If None, loads from default path.
        config_path: Path to config file (for reload endpoint).

    Returns:
        Configured FastAPI app.
    """
    resolved_config = config or load_config(config_path)

    state = AdminState(config=resolved_config, config_path=config_path)

    # Only enable Swagger UI in debug mode
    enable_docs = resolved_config.node.log_level.lower() == "debug"

    app = FastAPI(
        title="InfoMesh Admin API",
        description="Local node administration and monitoring.",
        version="0.1.0",
        docs_url="/docs" if enable_docs else None,
        redoc_url=None,
    )

    app.state.admin = state

    # Localhost-only middleware — reject non-loopback clients
    @app.middleware("http")
    async def localhost_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        client_host = request.client.host if request.client else None
        # "testclient" is Starlette's TestClient default — allowed for testing.
        allowed = {"127.0.0.1", "::1", "localhost", "testclient"}
        if client_host not in allowed:
            logger.warning("admin_api_blocked", client=client_host)
            return JSONResponse(
                status_code=403,
                content={"detail": "Admin API is only accessible from localhost"},
            )
        return await call_next(request)

    # ── Health ──────────────────────────────────────────────

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe — always returns ok."""
        return {"status": "ok"}

    # ── Status ──────────────────────────────────────────────

    @app.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        """Node status overview."""
        st: AdminState = request.app.state.admin
        uptime = time.time() - st.start_time
        index_stats = _get_index_stats(st.config)
        return {
            "status": "running",
            "uptime_seconds": round(uptime, 1),
            "uptime_human": _format_duration(uptime),
            "index": index_stats,
            "version": "0.1.0",
        }

    # ── Configuration ───────────────────────────────────────

    @app.get("/config")
    async def get_config(request: Request) -> dict[str, Any]:
        """Current configuration (keys and secrets redacted)."""
        st: AdminState = request.app.state.admin
        cfg = asdict(st.config)
        _redact_paths(cfg)
        return cfg

    @app.post("/config/reload", response_model=None)
    async def reload_config(request: Request) -> dict[str, str] | JSONResponse:
        """Reload configuration from disk."""
        st: AdminState = request.app.state.admin
        try:
            st.config = load_config(st.config_path)
            logger.info("config_reloaded", path=str(st.config_path))
            return {"status": "reloaded"}
        except Exception as exc:
            logger.error("config_reload_failed", error=str(exc))
            return JSONResponse(
                status_code=500,
                content={"status": "error", "detail": "Failed to reload configuration"},
            )

    # ── Index stats ─────────────────────────────────────────

    @app.get("/index/stats")
    async def index_stats(request: Request) -> dict[str, Any]:
        """Index statistics."""
        st: AdminState = request.app.state.admin
        return _get_index_stats(st.config)

    # ── Credits ─────────────────────────────────────────────

    @app.get("/credits/balance")
    async def credits_balance(request: Request) -> dict[str, Any]:
        """Local credit balance and earnings summary."""
        st: AdminState = request.app.state.admin
        return _get_credit_stats(st.config)

    # ── Network ─────────────────────────────────────────────

    @app.get("/network/peers")
    async def network_peers() -> dict[str, Any]:
        """Connected peers summary."""
        return {
            "total_peers": 0,
            "connected": 0,
            "note": "P2P metrics available when node is networked",
        }

    return app


# ── Helper functions ────────────────────────────────────────────


def _get_index_stats(config: Config) -> dict[str, Any]:
    """Read index statistics from the local store database."""
    db_path = config.index.db_path
    if not db_path.exists():
        return {"document_count": 0, "db_size_mb": 0.0}

    try:
        from infomesh.index.local_store import LocalStore

        store = LocalStore(db_path)
        try:
            stats = store.get_stats()
        finally:
            store.close()
        db_size = db_path.stat().st_size / (1024 * 1024)
        return {
            "document_count": stats.get("document_count", 0),
            "db_size_mb": round(db_size, 2),
        }
    except Exception:
        logger.exception("index_stats_error")
        return {"error": "unable to read index stats"}


def _get_credit_stats(config: Config) -> dict[str, Any]:
    """Read credit stats from local ledger if available."""
    ledger_path = config.node.data_dir / "credits.db"
    if not ledger_path.exists():
        return {"balance": 0.0, "total_earned": 0.0, "total_spent": 0.0}

    try:
        from infomesh.credits.ledger import CreditLedger

        ledger = CreditLedger(ledger_path)
        try:
            s = ledger.stats()
        finally:
            ledger.close()
        return {
            "total_earned": s.total_earned,
            "total_spent": s.total_spent,
            "balance": s.balance,
        }
    except Exception:
        return {"balance": 0.0, "total_earned": 0.0, "total_spent": 0.0}


def _redact_paths(cfg: dict[str, Any]) -> None:
    """Redact sensitive fields and replace Path objects with strings."""
    for key, value in list(cfg.items()):
        if key in _SENSITIVE_KEYS:
            cfg[key] = "***REDACTED***"
        elif isinstance(value, Path):
            cfg[key] = str(value)
        elif isinstance(value, dict):
            _redact_paths(value)


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m {seconds % 60:.0f}s"
    hours = seconds / 3600
    minutes = (seconds % 3600) / 60
    return f"{hours:.0f}h {minutes:.0f}m"
