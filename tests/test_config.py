"""Tests for configuration management."""

from __future__ import annotations

from pathlib import Path

import pytest

from infomesh.config import Config, load_config


def test_default_config() -> None:
    """Default config should have sensible defaults."""
    config = Config()
    assert config.node.listen_port == 4001
    assert config.crawl.max_concurrent == 5
    assert config.crawl.politeness_delay == 1.0
    assert config.crawl.respect_robots is True
    assert config.network.replication_factor == 3
    assert config.storage.compression_enabled is True
    assert config.llm.enabled is False


def test_load_config_no_file(tmp_path: Path) -> None:
    """Loading config without a file should return defaults."""
    config = load_config(tmp_path / "nonexistent.toml")
    assert config.crawl.max_concurrent == 5
    assert config.node.listen_port == 4001


def test_load_config_from_toml(tmp_path: Path) -> None:
    """Loading config from a TOML file should override defaults."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[node]
listen_port = 5001

[crawl]
max_concurrent = 10
politeness_delay = 2.0

[storage]
compression_level = 9
""")
    config = load_config(config_file)
    assert config.node.listen_port == 5001
    assert config.crawl.max_concurrent == 10
    assert config.crawl.politeness_delay == 2.0
    assert config.storage.compression_level == 9
    # Unset values remain default
    assert config.crawl.respect_robots is True


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables should override TOML values."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[crawl]\nmax_concurrent = 10\n")

    monkeypatch.setenv("INFOMESH_CRAWL_MAX_CONCURRENT", "20")
    config = load_config(config_file)
    assert config.crawl.max_concurrent == 20


# ─── DashboardConfig ─────────────────────────────────────────


def test_dashboard_config_defaults() -> None:
    """DashboardConfig should have sensible defaults."""
    config = Config()
    assert config.dashboard.bgm_auto_start is True
    assert config.dashboard.bgm_volume == 50
    assert config.dashboard.refresh_interval == 0.5
    assert config.dashboard.theme == "catppuccin-mocha"


def test_dashboard_config_from_toml(tmp_path: Path) -> None:
    """Dashboard section should load from TOML."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[dashboard]
bgm_auto_start = false
bgm_volume = 30
refresh_interval = 1.0
theme = "dracula"
""")
    config = load_config(config_file)
    assert config.dashboard.bgm_auto_start is False
    assert config.dashboard.bgm_volume == 30
    assert config.dashboard.refresh_interval == 1.0
    assert config.dashboard.theme == "dracula"


def test_dashboard_volume_clamped(tmp_path: Path) -> None:
    """bgm_volume should be clamped to 0-100."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[dashboard]\nbgm_volume = 200\n")
    config = load_config(config_file)
    assert config.dashboard.bgm_volume == 100


def test_dashboard_refresh_interval_clamped(tmp_path: Path) -> None:
    """refresh_interval should be clamped to 0.2-5.0."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[dashboard]\nrefresh_interval = 0.01\n")
    config = load_config(config_file)
    assert config.dashboard.refresh_interval == 0.2


# ─── save_config ─────────────────────────────────────────────


def test_save_config_roundtrip(tmp_path: Path) -> None:
    """save_config → load_config should preserve non-default values."""
    from infomesh.config import (
        CrawlConfig,
        DashboardConfig,
        NodeConfig,
        save_config,
    )

    original = Config(
        node=NodeConfig(data_dir=tmp_path / ".infomesh", listen_port=9999),
        crawl=CrawlConfig(max_concurrent=20),
        dashboard=DashboardConfig(bgm_volume=75, theme="nord"),
    )
    config_file = tmp_path / "config.toml"
    save_config(original, config_file)

    loaded = load_config(config_file)
    assert loaded.node.listen_port == 9999
    assert loaded.crawl.max_concurrent == 20
    assert loaded.dashboard.bgm_volume == 75
    assert loaded.dashboard.theme == "nord"
    # Defaults should remain unchanged
    assert loaded.crawl.politeness_delay == 1.0
    assert loaded.dashboard.bgm_auto_start is True


def test_save_config_only_non_defaults(tmp_path: Path) -> None:
    """save_config should only write keys that differ from defaults."""
    from infomesh.config import DashboardConfig, save_config

    config = Config(
        dashboard=DashboardConfig(bgm_volume=80),
    )
    config_file = tmp_path / "config.toml"
    save_config(config, config_file)

    content = config_file.read_text()
    assert "bgm_volume = 80" in content
    # Default values should NOT appear in the file
    assert "bgm_auto_start" not in content
    assert "refresh_interval" not in content
