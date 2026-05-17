"""Local admin API — FastAPI-based status and configuration endpoints.

Provides HTTP endpoints for monitoring and managing a local InfoMesh node.
Not exposed to the public network; binds to localhost only.

Endpoints:
    GET  /health              — Liveness probe
    GET  /readiness           — Readiness probe (DB accessible)
    GET  /status              — Node status (uptime, index size, peer count)
    GET  /config              — Current configuration (redacted secrets)
    GET  /index/stats         — Index statistics (document count, size)
    GET  /credits/balance     — Local credit balance
    GET  /network/peers       — Connected peers summary
    GET  /analytics           — Search analytics (counts, latency)
    POST /config/reload       — Reload configuration from disk
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from infomesh.config import Config, load_config
from infomesh.runtime import read_runtime_status

logger = structlog.get_logger()

_LOCAL_ADMIN_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})

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
    """State container for the admin API."""

    config: Config
    config_path: Path | None = None
    start_time: float = field(default_factory=time.time)
    total_searches: int = 0
    total_crawls: int = 0
    total_fetches: int = 0
    avg_latency_ms: float = 0.0
    _latency_sum: float = 0.0

    def record_search(self, latency_ms: float) -> None:
        self.total_searches += 1
        self._latency_sum += latency_ms
        self.avg_latency_ms = self._latency_sum / self.total_searches

    def record_crawl(self) -> None:
        self.total_crawls += 1

    def record_fetch(self) -> None:
        self.total_fetches += 1


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
        if client_host not in _LOCAL_ADMIN_HOSTS:
            logger.warning("admin_api_blocked", client=client_host)
            return JSONResponse(
                status_code=403,
                content={"detail": "Admin API is only accessible from localhost"},
            )

        # Optional API key check (constant-time comparison)
        api_key = os.environ.get("INFOMESH_API_KEY")
        if api_key is not None:
            import hmac

            provided = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(provided, api_key):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid API key"},
                )

        return await call_next(request)

    # #37: Content Security Policy header
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"
        )
        return response

    # #36: Rate limiting (10 requests/second per client)
    _rate_buckets: dict[str, deque[float]] = {}
    _rate_last_cleanup = [time.time()]
    _RATE_MAX_CLIENTS = 10000
    _RATE_LIMIT_PER_SECOND = 10

    def _cleanup_rate_buckets(now: float, *, force: bool = False) -> None:
        if not force and now - _rate_last_cleanup[0] <= 10.0:
            return
        cutoff = now - 10.0
        stale = [k for k, v in _rate_buckets.items() if not v or v[-1] < cutoff]
        for key in stale:
            del _rate_buckets[key]
        while len(_rate_buckets) > _RATE_MAX_CLIENTS:
            _rate_buckets.pop(next(iter(_rate_buckets)))
        _rate_last_cleanup[0] = now

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        client = request.client.host if request.client else "unknown"
        if client not in _LOCAL_ADMIN_HOSTS:
            return await call_next(request)

        now = time.time()
        if client not in _rate_buckets and len(_rate_buckets) >= _RATE_MAX_CLIENTS:
            _cleanup_rate_buckets(now, force=True)
        bucket = _rate_buckets.setdefault(
            client,
            deque(maxlen=_RATE_LIMIT_PER_SECOND),
        )
        while bucket and now - bucket[0] >= 1.0:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMIT_PER_SECOND:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
            )
        bucket.append(now)
        _cleanup_rate_buckets(now, force=len(_rate_buckets) > _RATE_MAX_CLIENTS)
        return await call_next(request)

    # ── Health (#10: Detailed health check) ──────────────────

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        """Liveness probe with optional detailed checks."""
        st: AdminState = request.app.state.admin
        detail = request.query_params.get("detail", "")
        if not detail:
            return {"status": "ok"}

        import shutil

        checks: dict[str, str] = {"status": "ok"}
        # DB check
        db_path = st.config.index.db_path
        checks["db"] = "ok" if db_path.exists() else "missing"
        # Disk check
        try:
            _, _, free = shutil.disk_usage(
                st.config.node.data_dir if st.config.node.data_dir.exists() else "/"
            )
            checks["disk_free_gb"] = str(round(free / (1024**3), 1))
            if free < 1024**3:
                checks["status"] = "degraded"
                checks["disk"] = "low"
            else:
                checks["disk"] = "ok"
        except OSError:
            checks["disk"] = "unknown"
        # Memory check
        try:
            import psutil

            mem = psutil.virtual_memory()
            checks["memory_pct"] = str(round(mem.percent, 1))
            if mem.percent > 95:
                checks["status"] = "degraded"
                checks["memory"] = "critical"
            else:
                checks["memory"] = "ok"
        except ImportError:
            checks["memory"] = "unknown"
        # Uptime
        checks["uptime_s"] = str(round(time.time() - st.start_time, 0))
        runtime = read_runtime_status(st.config.node.data_dir)
        if runtime:
            checks["runtime"] = str(runtime.get("status", "unknown"))
            checks["runtime_degrade_level"] = str(
                runtime.get("degrade_level", "unknown")
            )
            checks["runtime_process_memory_mb"] = str(
                runtime.get("process_memory_mb", "unknown")
            )
        return checks

    # ── #7: Search Console API ─────────────────────────────

    @app.get("/search")
    async def search_api(
        request: Request,
        q: str = "",
        limit: int = 5,
    ) -> dict[str, Any]:
        """Search endpoint for the web dashboard console."""
        st: AdminState = request.app.state.admin
        if not q:
            return {"results": [], "error": "query required"}

        try:
            from infomesh.index.local_store import LocalStore
            from infomesh.search.query import search_local

            _store = LocalStore(
                db_path=st.config.index.db_path,
                compression_enabled=st.config.storage.compression_enabled,
                compression_level=st.config.storage.compression_level,
            )
            try:
                result = search_local(_store, q, limit=min(limit, 20))
                return {
                    "query": q,
                    "total": result.total,
                    "elapsed_ms": round(result.elapsed_ms, 1),
                    "results": [
                        {
                            "url": r.url,
                            "title": r.title,
                            "snippet": r.snippet[:300],
                            "score": round(r.combined_score, 4),
                        }
                        for r in result.results
                    ],
                }
            finally:
                _store.close()
        except Exception as exc:
            logger.warning("search_api_error", error=str(exc))
            return {"results": [], "error": str(exc)[:200]}

    @app.get("/readiness")
    async def readiness(request: Request) -> JSONResponse:
        """Readiness probe — checks DB accessibility."""
        st: AdminState = request.app.state.admin
        db_path = st.config.index.db_path
        if db_path.exists():
            return JSONResponse(content={"status": "ready", "db": "accessible"})
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "db": "missing"},
        )

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
            "runtime": read_runtime_status(st.config.node.data_dir),
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
    async def network_peers(request: Request) -> dict[str, Any]:
        """Connected peers summary (reads live P2P status file)."""
        st: AdminState = request.app.state.admin
        status_path = st.config.node.data_dir / "p2p_status.json"
        try:
            if status_path.exists():
                import json as _json

                data = _json.loads(status_path.read_text())
                age = time.time() - float(data.get("timestamp", 0))
                if age < 30:
                    return {
                        "total_peers": data.get("connected_peers", 0),
                        "connected": data.get("connected_peers", 0),
                        "peer_id": data.get("peer_id", ""),
                        "uptime_seconds": data.get("uptime", 0),
                        "dht_mode": data.get("dht_mode", "unknown"),
                    }
        except (OSError, ValueError):
            pass
        return {
            "total_peers": 0,
            "connected": 0,
            "note": "P2P metrics available when node is networked",
        }

    # ── Analytics ───────────────────────────────────────────

    @app.get("/analytics")
    async def analytics_endpoint(request: Request) -> dict[str, Any]:
        """Search analytics — usage counts and avg latency."""
        st: AdminState = request.app.state.admin
        return {
            "total_searches": st.total_searches,
            "total_crawls": st.total_crawls,
            "total_fetches": st.total_fetches,
            "avg_latency_ms": round(st.avg_latency_ms, 1),
            "uptime_seconds": round(time.time() - st.start_time, 1),
        }

    # ── #25: MCP Tool Usage Statistics ──────────────────────

    @app.get("/analytics/tools")
    async def tool_stats(request: Request) -> dict[str, Any]:
        """MCP tool usage breakdown."""
        st: AdminState = request.app.state.admin
        total = st.total_searches + st.total_crawls + st.total_fetches
        return {
            "tool_usage": {
                "web_search": st.total_searches,
                "crawl_url": st.total_crawls,
                "fetch_page": st.total_fetches,
                "total": total,
            },
            "search_fetch_rate": (
                round(st.total_fetches / st.total_searches * 100, 1)
                if st.total_searches > 0
                else 0.0
            ),
        }

    # ── #15: Index Compression Stats ────────────────────────

    @app.get("/index/compression")
    async def index_compression(request: Request) -> dict[str, Any]:
        """Index compression statistics."""
        st: AdminState = request.app.state.admin
        stats = _get_index_stats(st.config)
        doc_count = stats.get("document_count", 0)
        db_mb = stats.get("db_size_mb", 0.0)
        avg_kb = (
            round(float(db_mb) * 1024 / int(doc_count), 2)
            if isinstance(doc_count, int) and doc_count > 0
            else 0.0
        )
        return {
            "documents": doc_count,
            "db_size_mb": db_mb,
            "avg_doc_kb": avg_kb,
            "compression_enabled": st.config.storage.compression_enabled,
            "compression_level": st.config.storage.compression_level,
        }

    # ── Metrics (Prometheus) ────────────────────────────────

    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> JSONResponse:
        """Prometheus-compatible metrics endpoint."""
        from infomesh.observability.metrics import MetricsCollector

        mc = MetricsCollector()
        st: AdminState = request.app.state.admin
        mc.inc("searches_total", float(st.total_searches))
        mc.inc("crawls_total", float(st.total_crawls))
        mc.inc("fetches_total", float(st.total_fetches))
        mc.set_gauge("avg_latency_ms", st.avg_latency_ms)
        mc.set_gauge(
            "uptime_seconds",
            round(time.time() - st.start_time, 1),
        )
        runtime = read_runtime_status(st.config.node.data_dir)
        process_memory = runtime.get("process_memory_mb")
        if isinstance(process_memory, int | float):
            mc.set_gauge("process_memory_mb", float(process_memory))
        text = mc.format_prometheus()
        return JSONResponse(
            content={"metrics": text},
            media_type="application/json",
        )

    # ── OpenAPI spec ────────────────────────────────────────

    @app.get("/openapi-spec")
    async def openapi_spec() -> dict[str, Any]:
        """Custom OpenAPI 3.1 specification for InfoMesh API."""
        from infomesh.api.extensions import generate_openapi_spec

        return generate_openapi_spec()

    # ── Web Dashboard ───────────────────────────────────────

    @app.get("/dashboard", response_class=HTMLResponse)
    async def dashboard_page() -> HTMLResponse:
        """Serve the web analytics dashboard (HTML)."""
        return HTMLResponse(content=_DASHBOARD_HTML)

    return app


# ── Helper functions ────────────────────────────────────────────


def _get_index_stats(config: Config) -> dict[str, Any]:
    """Read index statistics from the local store database."""
    db_path = config.index.db_path
    if not db_path.exists():
        return {"document_count": 0, "db_size_mb": 0.0}

    try:
        from infomesh.index.local_store import LocalStore

        store = LocalStore(
            db_path,
            compression_enabled=config.storage.compression_enabled,
            compression_level=config.storage.compression_level,
        )
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


# ── Inline dashboard HTML ───────────────────────────────────────────

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>InfoMesh Dashboard</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;
    --dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;
    --orange:#d29922}
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,
    Arial,sans-serif;background:var(--bg);color:var(--text);padding:24px}
  h1{font-size:1.5rem;margin-bottom:8px;color:var(--accent)}
  .tabs{display:flex;gap:0;border-bottom:1px solid var(--border);
    margin-bottom:16px}
  .tab{padding:8px 16px;cursor:pointer;color:var(--dim);border-bottom:
    2px solid transparent;font-size:.9rem;transition:all .15s}
  .tab:hover{color:var(--text)}
  .tab.active{color:var(--accent);border-bottom-color:var(--accent)}
  .page{display:none}
  .page.active{display:block}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));
    gap:16px;margin-bottom:24px}
  .card{background:var(--card);border:1px solid var(--border);
    border-radius:8px;padding:20px}
  .card h2{font-size:.85rem;color:var(--dim);text-transform:uppercase;
    letter-spacing:.5px;margin-bottom:12px}
  .metric{font-size:2rem;font-weight:600}
  .metric small{font-size:.75rem;color:var(--dim);margin-left:4px}
  .sub{font-size:.85rem;color:var(--dim);margin-top:4px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:50%;
    margin-right:6px}
  .dot.ok{background:var(--green)}.dot.err{background:var(--red)}
  .dot.warn{background:var(--orange)}
  #refresh-bar{text-align:right;color:var(--dim);font-size:.8rem;
    margin-bottom:8px}
  table{width:100%;border-collapse:collapse;margin-top:8px}
  td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);
    font-size:.85rem}
  th{color:var(--dim);font-weight:500}
  .bar{height:6px;border-radius:3px;background:var(--border);margin-top:4px}
  .bar-fill{height:100%;border-radius:3px;background:var(--accent);
    transition:width .3s}
  .section{margin-top:16px}
  .section h3{font-size:.9rem;color:var(--dim);margin-bottom:8px}
</style>
</head>
<body>
<h1>InfoMesh Node Dashboard</h1>
<div id="refresh-bar">Loading...</div>
<div class="tabs">
  <div class="tab active" data-page="overview">Overview</div>
  <div class="tab" data-page="search">Search</div>
  <div class="tab" data-page="crawl">Crawl</div>
  <div class="tab" data-page="network">Network</div>
  <div class="tab" data-page="credits">Credits</div>
</div>

<div class="page active" id="pg-overview">
  <div class="grid" id="overview-cards"></div>
</div>

<div class="page" id="pg-search">
  <div class="grid" id="search-cards"></div>
  <div class="card section" id="search-detail"></div>
</div>

<div class="page" id="pg-crawl">
  <div class="grid" id="crawl-cards"></div>
  <div class="card section" id="crawl-detail"></div>
</div>

<div class="page" id="pg-network">
  <div class="grid" id="net-cards"></div>
  <div class="card section" id="net-detail"></div>
</div>

<div class="page" id="pg-credits">
  <div class="grid" id="cred-cards"></div>
  <div class="card section" id="cred-detail"></div>
</div>

<script>
// Tab navigation
document.querySelectorAll('.tab').forEach(t=>{
  t.addEventListener('click',()=>{
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.page').forEach(x=>x.classList.remove('active'));
    t.classList.add('active');
    document.getElementById('pg-'+t.dataset.page).classList.add('active');
  });
});

async function fetchJSON(u){const r=await fetch(u);return r.ok?r.json():null}
function fmt(n,d=1){return typeof n==='number'?n.toFixed(d):n||'\\u2014'}
function fmtB(mb){return mb>=1024?(mb/1024).toFixed(1)+' GB':mb.toFixed(1)+' MB'}

async function refresh(){
  const[st,idx,cred,net,an]=await Promise.all([
    fetchJSON('/status'),fetchJSON('/index/stats'),
    fetchJSON('/credits/balance'),fetchJSON('/network/peers'),
    fetchJSON('/analytics')]);

  // Overview
  document.getElementById('overview-cards').innerHTML=`
  <div class="card"><h2>Node Status</h2>
    <div class="metric"><span class="dot ok"></span>${
      st?.status||'unknown'}</div>
    <div class="sub">Uptime: ${st?.uptime_human||'\\u2014'}</div></div>
  <div class="card"><h2>Index</h2>
    <div class="metric">${fmt(idx?.document_count,0)}<small>docs</small></div>
    <div class="sub">DB: ${fmtB(idx?.db_size_mb||0)}</div></div>
  <div class="card"><h2>Peers</h2>
    <div class="metric">${fmt(net?.connected||net?.total_peers||0,0)}</div>
    <div class="sub">DHT: ${net?.dht_mode||'\\u2014'}</div></div>
  <div class="card"><h2>Credits</h2>
    <div class="metric">${fmt(cred?.balance)}</div>
    <div class="sub">Earned: ${fmt(cred?.total_earned)}</div></div>
  <div class="card"><h2>Searches</h2>
    <div class="metric">${fmt(an?.total_searches,0)}</div>
    <div class="sub">Latency: ${fmt(an?.avg_latency_ms)}ms</div></div>
  <div class="card"><h2>Crawls</h2>
    <div class="metric">${fmt(an?.total_crawls,0)}</div>
    <div class="sub">Fetches: ${fmt(an?.total_fetches,0)}</div></div>`;

  // Search Analytics
  const latMs=an?.avg_latency_ms||0;
  const latBar=Math.min(latMs/1000*100,100);
  const latColor=latMs<200?'var(--green)':latMs<500?'var(--orange)':'var(--red)';
  document.getElementById('search-cards').innerHTML=`
  <div class="card"><h2>Total Searches</h2>
    <div class="metric">${fmt(an?.total_searches,0)}</div>
    <div class="sub">Since node started</div></div>
  <div class="card"><h2>Avg Latency</h2>
    <div class="metric" style="color:${latColor}">${fmt(latMs)}
      <small>ms</small></div>
    <div class="bar"><div class="bar-fill" style="width:${latBar}%;
      background:${latColor}"></div></div>
    <div class="sub">${latMs<200?'Excellent':latMs<500?'Good':'Slow'}
    </div></div>
  <div class="card"><h2>Fetches</h2>
    <div class="metric">${fmt(an?.total_fetches,0)}</div>
    <div class="sub">Pages fetched after search</div></div>`;
  const sr=an?.total_searches||0;const fr=an?.total_fetches||0;
  const ctr=sr>0?((fr/sr)*100).toFixed(1):'0.0';
  document.getElementById('search-detail').innerHTML=`
    <h3>Search Quality Signals</h3>
    <table><tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Search \\u2192 Fetch Rate</td>
      <td>${ctr}%</td></tr>
    <tr><td>Avg Latency</td><td>${fmt(latMs)} ms</td></tr>
    <tr><td>Uptime</td><td>${st?.uptime_human||'\\u2014'}</td></tr>
    </table>`;

  // Crawl Status
  const docs=idx?.document_count||0;
  const dbMb=idx?.db_size_mb||0;
  const avgKb=docs>0?((dbMb*1024)/docs).toFixed(1):'0.0';
  document.getElementById('crawl-cards').innerHTML=`
  <div class="card"><h2>Total Crawled</h2>
    <div class="metric">${fmt(an?.total_crawls,0)}</div>
    <div class="sub">Pages crawled</div></div>
  <div class="card"><h2>Indexed Documents</h2>
    <div class="metric">${fmt(docs,0)}</div>
    <div class="sub">${fmtB(dbMb)} on disk</div></div>
  <div class="card"><h2>Avg Doc Size</h2>
    <div class="metric">${avgKb}<small>KB</small></div>
    <div class="sub">Per document</div></div>`;
  document.getElementById('crawl-detail').innerHTML=`
    <h3>Index Details</h3>
    <table><tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Documents</td><td>${fmt(docs,0)}</td></tr>
    <tr><td>Database Size</td><td>${fmtB(dbMb)}</td></tr>
    <tr><td>Avg Size/Doc</td><td>${avgKb} KB</td></tr>
    <tr><td>Total Crawl Actions</td><td>${fmt(an?.total_crawls,0)}</td></tr>
    </table>`;

  // Network
  const peers=net?.connected||net?.total_peers||0;
  const peerId=net?.peer_id||'\\u2014';
  document.getElementById('net-cards').innerHTML=`
  <div class="card"><h2>Connected Peers</h2>
    <div class="metric"><span class="dot ${peers>0?'ok':'warn'}"></span>${
      fmt(peers,0)}</div>
    <div class="sub">${peers>0?'Network active':'No peers'}</div></div>
  <div class="card"><h2>DHT Mode</h2>
    <div class="metric" style="font-size:1.4rem">${
      net?.dht_mode||'\\u2014'}</div>
    <div class="sub">Kademlia routing</div></div>`;
  document.getElementById('net-detail').innerHTML=`
    <h3>Peer Info</h3>
    <table><tr><th>Field</th><th>Value</th></tr>
    <tr><td>Peer ID</td><td style="font-family:monospace;font-size:.8rem">${
      peerId}</td></tr>
    <tr><td>Connected Peers</td><td>${fmt(peers,0)}</td></tr>
    <tr><td>Uptime</td><td>${fmt(net?.uptime_seconds,0)}s</td></tr>
    </table>`;

  // Credits
  const bal=cred?.balance||0;const earn=cred?.total_earned||0;
  const spent=cred?.total_spent||0;
  const tier=bal>=1000?'High':bal>=100?'Medium':'Starter';
  document.getElementById('cred-cards').innerHTML=`
  <div class="card"><h2>Balance</h2>
    <div class="metric">${fmt(bal)}</div>
    <div class="sub">Tier: ${tier}</div></div>
  <div class="card"><h2>Total Earned</h2>
    <div class="metric" style="color:var(--green)">${fmt(earn)}</div>
    <div class="sub">From contributions</div></div>
  <div class="card"><h2>Total Spent</h2>
    <div class="metric" style="color:var(--orange)">${fmt(spent)}</div>
    <div class="sub">On searches</div></div>`;
  document.getElementById('cred-detail').innerHTML=`
    <h3>Credit Breakdown</h3>
    <table><tr><th>Metric</th><th>Value</th></tr>
    <tr><td>Balance</td><td>${fmt(bal)}</td></tr>
    <tr><td>Total Earned</td><td>${fmt(earn)}</td></tr>
    <tr><td>Total Spent</td><td>${fmt(spent)}</td></tr>
    <tr><td>Contribution Tier</td><td>${tier}</td></tr>
    </table>`;

  document.getElementById('refresh-bar').textContent=
    'Last refreshed: '+new Date().toLocaleTimeString()+' (every 5s)';
}
refresh();setInterval(refresh,5000);
</script>
</body>
</html>
"""
