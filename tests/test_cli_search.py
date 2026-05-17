"""Tests for the search CLI command."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from infomesh.config import Config, IndexConfig, NodeConfig, StorageConfig
from infomesh.index.local_store import LocalStore

search_cli = importlib.import_module("infomesh.cli.search")


class _MockP2PNode:
    def __init__(self) -> None:
        self.stopped = False

    async def search_network(
        self,
        query: str,
        keywords: list[str],
        limit: int,
    ) -> list[dict[str, object]]:
        return [
            {
                "url": "https://peer.example/page",
                "title": "Peer Result",
                "snippet": "result from a peer node",
                "score": 3.0,
                "peer_id": "peer-1",
                "doc_id": 42,
            }
        ]

    def stop(self) -> None:
        self.stopped = True


class _DelayedPeerNode:
    def __init__(self) -> None:
        self.calls = 0

    def get_connected_peers(self) -> list[str]:
        self.calls += 1
        return ["peer-1"] if self.calls >= 3 else []


class _RecoveringP2PNode(_MockP2PNode):
    def __init__(self, *, connected: bool) -> None:
        super().__init__()
        self._connected = connected

    def get_connected_peers(self) -> list[str]:
        return ["peer-1"] if self._connected else []

    async def search_network(
        self,
        query: str,
        keywords: list[str],
        limit: int,
    ) -> list[dict[str, object]]:
        if not self._connected:
            return []
        return await super().search_network(query, keywords, limit)


def _make_config(tmp_path: Path) -> Config:
    return Config(
        node=NodeConfig(data_dir=tmp_path),
        index=IndexConfig(db_path=tmp_path / "index.db"),
        storage=StorageConfig(compression_enabled=False),
    )


def _add_local_document(config: Config) -> None:
    store = LocalStore(
        db_path=config.index.db_path,
        compression_enabled=config.storage.compression_enabled,
        compression_level=config.storage.compression_level,
    )
    try:
        store.add_document(
            url="https://local.example/page",
            title="Local Result",
            text="python asyncio local document",
            raw_html_hash="raw-local",
            text_hash="text-local",
        )
    finally:
        store.close()


def test_cli_search_uses_distributed_by_default(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    _add_local_document(config)
    node = _MockP2PNode()

    monkeypatch.setattr(search_cli, "load_config", lambda: config)
    monkeypatch.setattr(
        "infomesh.services.bootstrap_p2p",
        lambda config: (node, object()),
    )

    result = CliRunner().invoke(search_cli.search, ["python"])

    assert result.exit_code == 0
    assert "distributed" in result.output
    assert "Remote: 1" in result.output
    assert "Peer Result" in result.output
    assert node.stopped is True


def test_cli_search_falls_back_to_local_when_p2p_unavailable(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    _add_local_document(config)

    monkeypatch.setattr(search_cli, "load_config", lambda: config)
    monkeypatch.setattr(
        "infomesh.services.bootstrap_p2p",
        lambda config: (None, None),
    )

    result = CliRunner().invoke(search_cli.search, ["python"])

    assert result.exit_code == 0
    assert "P2P unavailable" in result.output
    assert "Local Result" in result.output


def test_cli_search_retries_empty_disconnected_bootstrap(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    first_node = _RecoveringP2PNode(connected=False)
    second_node = _RecoveringP2PNode(connected=True)
    attempts = [(first_node, object()), (second_node, object())]
    wait_results = [False, True]

    monkeypatch.setattr(search_cli, "load_config", lambda: config)
    monkeypatch.setattr(
        search_cli,
        "_wait_for_connected_peer",
        lambda node: wait_results.pop(0),
    )
    monkeypatch.setattr(
        "infomesh.services.bootstrap_p2p",
        lambda config: attempts.pop(0),
    )

    result = CliRunner().invoke(search_cli.search, ["python"])

    assert result.exit_code == 0
    assert "Remote: 1" in result.output
    assert "Peer Result" in result.output
    assert first_node.stopped is True
    assert second_node.stopped is True
    assert attempts == []
    assert wait_results == []


def test_cli_search_local_only_alias_skips_p2p(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    config = _make_config(tmp_path)
    _add_local_document(config)

    def fail_bootstrap(config: Config) -> tuple[None, None]:
        raise AssertionError("bootstrap should not run for --local-only")

    monkeypatch.setattr(search_cli, "load_config", lambda: config)
    monkeypatch.setattr("infomesh.services.bootstrap_p2p", fail_bootstrap)

    result = CliRunner().invoke(search_cli.search, ["--local-only", "python"])

    assert result.exit_code == 0
    assert "Local Result" in result.output
    assert "Remote:" not in result.output


def test_cli_search_rejects_invalid_limit() -> None:
    result = CliRunner().invoke(search_cli.search, ["--limit", "0", "python"])

    assert result.exit_code != 0
    assert "Invalid value for '--limit'" in result.output


def test_wait_for_connected_peer_allows_background_bootstrap(
    monkeypatch: Any,
) -> None:
    node = _DelayedPeerNode()
    sleeps: list[float] = []

    monkeypatch.setattr(search_cli.time, "sleep", sleeps.append)

    search_cli._wait_for_connected_peer(node, timeout_seconds=1.0)

    assert node.calls == 3
    assert sleeps == [0.1, 0.1]


def test_wait_for_connected_peer_reports_timeout(monkeypatch: Any) -> None:
    node = _RecoveringP2PNode(connected=False)

    monkeypatch.setattr(search_cli.time, "sleep", lambda delay: None)

    connected = search_cli._wait_for_connected_peer(node, timeout_seconds=0.0)

    assert connected is False
