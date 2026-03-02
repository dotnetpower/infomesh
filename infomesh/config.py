"""Configuration management for InfoMesh.

Loads settings from ~/.infomesh/config.toml with environment variable overrides.
"""

from __future__ import annotations

import json
import os
import sysconfig
import tomllib
from dataclasses import dataclass, field
from dataclasses import fields as dataclass_fields
from dataclasses import replace as dc_replace
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Default paths
DEFAULT_DATA_DIR = Path.home() / ".infomesh"
DEFAULT_CONFIG_PATH = DEFAULT_DATA_DIR / "config.toml"


class NodeRole:
    """Node role determines which components are active.

    - ``full``: All components (crawler + indexer + search). Default.
    - ``crawler``: Crawl-only node (DMZ). Crawls pages and submits
      results to indexer nodes over the internal channel.
    - ``search``: Search/index-only node (private network). Receives
      crawl results from crawler nodes, indexes them, and serves
      search queries.
    """

    FULL = "full"
    CRAWLER = "crawler"
    SEARCH = "search"

    ALL = frozenset({FULL, CRAWLER, SEARCH})


@dataclass(frozen=True)
class NodeConfig:
    """Node identity and network settings."""

    data_dir: Path = DEFAULT_DATA_DIR
    listen_port: int = 4001
    listen_address: str = "0.0.0.0"
    role: str = NodeRole.FULL
    log_level: str = "info"
    github_email: str = ""


@dataclass(frozen=True)
class CrawlConfig:
    """Crawler behavior settings."""

    max_concurrent: int = 5
    politeness_delay: float = 1.0  # seconds per domain
    max_depth: int = 0  # 0 = unlimited (controlled by rate limits & dedup)
    urls_per_hour: int = 60
    pending_per_domain: int = 10
    user_agent: str = "InfoMesh/0.1 (+https://github.com/dotnetpower/infomesh)"
    respect_robots: bool = True


@dataclass(frozen=True)
class NetworkConfig:
    """P2P network constraints."""

    upload_limit_mbps: float = 5.0
    download_limit_mbps: float = 10.0
    replication_factor: int = 3
    bootstrap_nodes: list[str] = field(default_factory=list)
    index_submit_peers: list[str] = field(default_factory=list)
    peer_acl: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IndexConfig:
    """Search index settings."""

    db_path: Path = field(default_factory=lambda: DEFAULT_DATA_DIR / "index.db")
    fts_tokenizer: str = "unicode61"
    max_doc_size_kb: int = 100
    vector_search: bool = False
    embedding_model: str = "all-MiniLM-L6-v2"


@dataclass(frozen=True)
class LLMConfig:
    """Local LLM summarization settings."""

    enabled: bool = False
    runtime: str = "ollama"
    model: str = "qwen2.5:3b"
    off_peak_start: str = "23:00"
    off_peak_end: str = "07:00"
    timezone: str = "auto"


@dataclass(frozen=True)
class StorageConfig:
    """Storage and compression settings."""

    compression_enabled: bool = True
    compression_level: int = 3
    max_cache_size_mb: int = 500
    max_index_size_gb: int = 50
    cache_ttl_days: int = 7


@dataclass(frozen=True)
class ResourceConfig:
    """Resource governor settings."""

    profile: str = "balanced"
    cpu_cores_limit: int = 2
    cpu_nice: int = 10
    memory_limit_mb: int = 2048
    disk_io_priority: str = "low"


@dataclass(frozen=True)
class DashboardConfig:
    """Dashboard UI settings."""

    bgm_auto_start: bool = False
    bgm_volume: int = 50
    refresh_interval: float = 0.5
    theme: str = "catppuccin-mocha"


@dataclass(frozen=True)
class McpConfig:
    """MCP server settings."""

    default_format: str = "text"
    max_response_chars: int = 0  # 0 = no limit
    show_attribution: bool = True
    show_copyright: bool = True
    debug: bool = False


@dataclass(frozen=True)
class Config:
    """Root configuration container."""

    node: NodeConfig = field(default_factory=NodeConfig)
    crawl: CrawlConfig = field(default_factory=CrawlConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    mcp: McpConfig = field(default_factory=McpConfig)


def _env_override(section: str, key: str) -> str | None:
    """Check for INFOMESH_{SECTION}_{KEY} environment variable."""
    env_key = f"INFOMESH_{section.upper()}_{key.upper()}"
    return os.environ.get(env_key)


def _coerce(value: str, target_type: type) -> object:
    """Coerce a string value to the target type."""
    if target_type is bool:
        return value.lower() in ("true", "1", "yes")
    if target_type is int:
        return int(value)
    if target_type is float:
        return float(value)
    if target_type is Path:
        return Path(value)
    if target_type is list:
        # Env var lists are comma-separated
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


# Configuration value constraints
_VALUE_CONSTRAINTS: dict[str, tuple[float, float]] = {
    "listen_port": (1, 65535),
    "max_concurrent": (1, 100),
    "politeness_delay": (0.1, 60.0),
    "urls_per_hour": (1, 10000),
    "pending_per_domain": (1, 1000),
    "upload_limit_mbps": (0.1, 1000.0),
    "download_limit_mbps": (0.1, 1000.0),
    "replication_factor": (1, 10),
    "max_doc_size_kb": (1, 10240),
    "compression_level": (1, 22),
    "max_cache_size_mb": (10, 100000),
    "max_index_size_gb": (1, 10000),
    "cache_ttl_days": (1, 365),
    "cpu_cores_limit": (1, 256),
    "cpu_nice": (0, 19),
    "memory_limit_mb": (64, 1048576),
    "bgm_volume": (0, 100),
    "refresh_interval": (0.2, 5.0),
    "max_response_chars": (0, 10000000),
}

# Allowed values for string enums
_ALLOWED_VALUES: dict[str, frozenset[str]] = {
    "role": frozenset(NodeRole.ALL),
    "log_level": frozenset({"debug", "info", "warning", "error", "critical"}),
    "runtime": frozenset({"ollama", "llama_cpp", "vllm"}),
    "profile": frozenset({"minimal", "balanced", "contributor", "dedicated"}),
    "disk_io_priority": frozenset({"low", "normal", "high"}),
    "fts_tokenizer": frozenset({"unicode61", "ascii", "porter", "trigram"}),
    "default_format": frozenset({"text", "json"}),
    "theme": frozenset(
        {
            "catppuccin-mocha",
            "textual-dark",
            "textual-light",
            "dracula",
            "tokyo-night",
            "monokai",
            "nord",
            "gruvbox",
            "textual-ansi",
            "solarized-light",
        }
    ),
}


def _validate_value(key: str, value: object) -> object:
    """Validate a config value against known constraints."""
    if key in _VALUE_CONSTRAINTS and isinstance(value, (int, float)):
        lo, hi = _VALUE_CONSTRAINTS[key]
        if not (lo <= value <= hi):
            logger.warning(
                "config_value_out_of_range",
                key=key,
                value=value,
                min=lo,
                max=hi,
            )
            # Clamp to valid range
            return type(value)(max(lo, min(hi, value)))
    if (
        key in _ALLOWED_VALUES
        and isinstance(value, str)
        and value.lower() not in _ALLOWED_VALUES[key]
    ):
        logger.warning(
            "config_invalid_value",
            key=key,
            value=value,
            allowed=sorted(_ALLOWED_VALUES[key]),
        )
        return None  # Will use default
    return value


def _build_section[T](
    cls: type[T], toml_section: dict[str, object], section_name: str
) -> T:
    """Build a dataclass instance from TOML data + env overrides."""
    kwargs: dict[str, object] = {}
    for f in dataclass_fields(cls):  # type: ignore[arg-type]
        # TOML value
        raw = toml_section.get(f.name)
        # env override
        env_val = _env_override(section_name, f.name)
        if env_val is not None:
            raw = _coerce(
                env_val, f.type if isinstance(f.type, type) else type(f.default)
            )
        if raw is not None:
            if f.type is Path or (isinstance(f.default, Path)):
                raw = Path(str(raw))
            # Validate value against constraints
            validated = _validate_value(f.name, raw)
            if validated is not None:
                kwargs[f.name] = validated
    return cls(**kwargs)


def _load_default_bootstrap_nodes() -> list[str]:
    """Load bundled bootstrap nodes from nodes.json.

    The file is shipped with the PyPI package via hatch shared-data
    (installed to ``<prefix>/share/infomesh/bootstrap/nodes.json``).
    When running from a development checkout the repo-relative path
    is also tried.

    Returns:
        List of multiaddr strings, or empty list if the file is
        missing / unreadable.
    """
    # 1) Development checkout: <repo>/bootstrap/nodes.json
    dev_path = Path(__file__).parent.parent / "bootstrap" / "nodes.json"
    # 2) Installed shared-data path
    data_prefix = sysconfig.get_path("data") or ""
    installed_path = (
        Path(data_prefix) / "share" / "infomesh" / "bootstrap" / "nodes.json"
    )

    for candidate in (dev_path, installed_path):
        if candidate.exists():
            try:
                entries = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(entries, list):
                    addrs = [
                        e["addr"]
                        for e in entries
                        if isinstance(e, dict) and "addr" in e
                    ]
                    if addrs:
                        logger.info(
                            "bootstrap_nodes_loaded",
                            source=str(candidate),
                            count=len(addrs),
                        )
                        return addrs
            except (json.JSONDecodeError, OSError, KeyError):
                pass
    return []


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from TOML file with environment variable overrides.

    Priority: env vars > config.toml > defaults.

    Args:
        config_path: Path to config file. Defaults to ~/.infomesh/config.toml.

    Returns:
        Populated Config instance.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    raw: dict[str, object] = {}

    if path.exists():
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        logger.info("config_loaded", path=str(path))
    else:
        logger.info("config_default", path=str(path), reason="file not found")

    node = _build_section(NodeConfig, raw.get("node", {}), "node")  # type: ignore[arg-type]
    crawl = _build_section(CrawlConfig, raw.get("crawl", {}), "crawl")  # type: ignore[arg-type]
    network = _build_section(NetworkConfig, raw.get("network", {}), "network")  # type: ignore[arg-type]
    index_cfg = _build_section(IndexConfig, raw.get("index", {}), "index")  # type: ignore[arg-type]
    llm = _build_section(LLMConfig, raw.get("llm", {}), "llm")  # type: ignore[arg-type]
    storage = _build_section(StorageConfig, raw.get("storage", {}), "storage")  # type: ignore[arg-type]
    resources = _build_section(ResourceConfig, raw.get("resources", {}), "resources")  # type: ignore[arg-type]
    dashboard = _build_section(DashboardConfig, raw.get("dashboard", {}), "dashboard")  # type: ignore[arg-type]
    mcp_cfg = _build_section(McpConfig, raw.get("mcp", {}), "mcp")  # type: ignore[arg-type]

    config = Config(
        node=node,
        crawl=crawl,
        network=network,
        index=index_cfg,
        llm=llm,
        storage=storage,
        resources=resources,
        dashboard=dashboard,
        mcp=mcp_cfg,
    )

    # If no bootstrap nodes configured, load bundled defaults from nodes.json
    if not config.network.bootstrap_nodes:
        default_nodes = _load_default_bootstrap_nodes()
        if default_nodes:
            config = dc_replace(
                config,
                network=dc_replace(config.network, bootstrap_nodes=default_nodes),
            )

    # When data_dir is overridden but index.db_path still points to the
    # original default, re-derive db_path relative to the new data_dir.
    if (
        config.node.data_dir != DEFAULT_DATA_DIR
        and config.index.db_path == DEFAULT_DATA_DIR / "index.db"
    ):
        config = dc_replace(
            config,
            index=dc_replace(
                config.index,
                db_path=config.node.data_dir / "index.db",
            ),
        )

    # Ensure data directory exists (resolve to prevent traversal)
    data_dir = config.node.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    return config


def save_config(config: Config, config_path: Path | None = None) -> None:
    """Save configuration to TOML file.

    Only writes sections/keys that differ from defaults to keep
    the config file clean and readable.

    Args:
        config: Config instance to persist.
        config_path: Path to config file. Defaults to ~/.infomesh/config.toml.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    sections: dict[str, dict[str, object]] = {}
    defaults = Config()

    section_map: list[tuple[str, object, object]] = [
        ("node", config.node, defaults.node),
        ("crawl", config.crawl, defaults.crawl),
        ("network", config.network, defaults.network),
        ("index", config.index, defaults.index),
        ("llm", config.llm, defaults.llm),
        ("storage", config.storage, defaults.storage),
        ("resources", config.resources, defaults.resources),
        ("dashboard", config.dashboard, defaults.dashboard),
        ("mcp", config.mcp, defaults.mcp),
    ]

    for section_name, current_section, default_section in section_map:
        section_dict: dict[str, object] = {}
        for f in type(current_section).__dataclass_fields__.values():  # type: ignore[attr-defined]
            cur_val = getattr(current_section, f.name)
            def_val = getattr(default_section, f.name)
            if cur_val != def_val:
                # Convert Path to string for TOML
                if isinstance(cur_val, Path):
                    section_dict[f.name] = str(cur_val)
                else:
                    section_dict[f.name] = cur_val
        if section_dict:
            sections[section_name] = section_dict

    # Write TOML manually (tomllib is read-only)
    lines: list[str] = [
        "# InfoMesh configuration",
        "# Generated by InfoMesh dashboard settings",
        "",
    ]
    for section_name, section_dict in sections.items():
        lines.append(f"[{section_name}]")
        for key, value in section_dict.items():
            if isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, str):
                escaped = value.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{key} = "{escaped}"')
            elif isinstance(value, (float, int)):
                lines.append(f"{key} = {value}")
            elif isinstance(value, list):
                items = ", ".join(f'"{v}"' for v in value)
                lines.append(f"{key} = [{items}]")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("config_saved", path=str(path))
