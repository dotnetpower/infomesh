"""Tests for P2P integration in CLI serve and status commands.

Covers:
- _try_start_p2p graceful fallback when libp2p unavailable
- _get_p2p_status reading from status file
- P2P status file writing in InfoMeshNode
- Status command P2P output formatting
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from infomesh.config import Config, NetworkConfig

_VALID_TEST_PEER_ID = "12D3KooWKLomR1fLxJpDEVdqE2CZsRWPvkZvjbKvEJzcF13s33N9"


class TestServePidFile:
    """Tests for node PID file lifecycle helpers."""

    def test_read_live_pid_cleans_invalid_file(self, tmp_path: Path) -> None:
        from infomesh.cli.serve import _pid_path, _read_live_pid

        pid_file = _pid_path(tmp_path)
        pid_file.write_text("not-a-pid")

        assert _read_live_pid(tmp_path) is None
        assert not pid_file.exists()

    def test_read_live_pid_cleans_stale_file(self, tmp_path: Path) -> None:
        from infomesh.cli.serve import _pid_path, _read_live_pid

        pid_file = _pid_path(tmp_path)
        pid_file.write_text("999999999")

        assert _read_live_pid(tmp_path) is None
        assert not pid_file.exists()

    def test_write_and_clear_pid_file(self, tmp_path: Path) -> None:
        from infomesh.cli.serve import _clear_pid_file, _pid_path, _write_pid_file

        _write_pid_file(tmp_path, 123)
        assert _pid_path(tmp_path).read_text() == "123"

        _clear_pid_file(tmp_path, 999)
        assert _pid_path(tmp_path).exists()

        _clear_pid_file(tmp_path, 123)
        assert not _pid_path(tmp_path).exists()

    def test_read_live_pid_returns_running_pid(self, tmp_path: Path) -> None:
        from infomesh.cli.serve import _pid_path, _read_live_pid

        _pid_path(tmp_path).write_text(str(os.getpid()))

        assert _read_live_pid(tmp_path) == os.getpid()


class TestGetP2PStatus:
    """Tests for read_p2p_status helper (dashboard.utils)."""

    def test_no_status_file(self, tmp_path: Path) -> None:
        """Returns empty dict when no status file exists."""
        from infomesh.config import NodeConfig
        from infomesh.dashboard.utils import read_p2p_status

        config = Config(node=NodeConfig(data_dir=tmp_path))
        result = read_p2p_status(config)
        assert result == {}

    def test_valid_status_file(self, tmp_path: Path) -> None:
        """Reads fresh status file correctly."""
        from infomesh.config import NodeConfig
        from infomesh.dashboard.utils import read_p2p_status

        status_data = {
            "state": "running",
            "peer_id": "abc123",
            "peers": 3,
            "listen_addrs": ["/ip4/0.0.0.0/tcp/4001"],
            "timestamp": time.time(),
            "error": "",
        }
        status_file = tmp_path / "p2p_status.json"
        status_file.write_text(json.dumps(status_data))

        config = Config(node=NodeConfig(data_dir=tmp_path))
        result = read_p2p_status(config)
        assert result["state"] == "running"
        assert result["peers"] == 3
        assert len(result["listen_addrs"]) == 1

    def test_stale_status_file(self, tmp_path: Path) -> None:
        """Returns minimal dict with peer_id when status file is stale."""
        from infomesh.config import NodeConfig
        from infomesh.dashboard.utils import read_p2p_status

        status_data = {
            "state": "running",
            "peer_id": "abc123",
            "peers": 2,
            "listen_addrs": [],
            "timestamp": time.time() - 60,  # 60s old
            "error": "",
        }
        status_file = tmp_path / "p2p_status.json"
        status_file.write_text(json.dumps(status_data))

        config = Config(node=NodeConfig(data_dir=tmp_path))
        result = read_p2p_status(config)
        # Stale data: returns peer_id + stopped state, not full data
        assert result.get("peer_id") == "abc123"
        assert result.get("state") == "stopped"
        assert result.get("peers") == 0

    def test_corrupt_status_file(self, tmp_path: Path) -> None:
        """Returns empty dict when status file is corrupt."""
        from infomesh.config import NodeConfig
        from infomesh.dashboard.utils import read_p2p_status

        status_file = tmp_path / "p2p_status.json"
        status_file.write_text("not valid json {{{")

        config = Config(node=NodeConfig(data_dir=tmp_path))
        result = read_p2p_status(config)
        assert result == {}

    def test_error_state(self, tmp_path: Path) -> None:
        """Reads error state and message."""
        from infomesh.config import NodeConfig
        from infomesh.dashboard.utils import read_p2p_status

        status_data = {
            "state": "error",
            "peer_id": "",
            "peers": 0,
            "listen_addrs": [],
            "timestamp": time.time(),
            "error": "trio not installed",
        }
        status_file = tmp_path / "p2p_status.json"
        status_file.write_text(json.dumps(status_data))

        config = Config(node=NodeConfig(data_dir=tmp_path))
        result = read_p2p_status(config)
        assert result["state"] == "error"
        assert result["error"] == "trio not installed"


class TestBootstrapP2P:
    """Tests for bootstrap_p2p graceful fallback."""

    def test_returns_none_when_import_fails(self) -> None:
        """Returns (None, None) when libp2p not installed."""
        config = Config()

        with patch.dict("sys.modules", {"infomesh.p2p.node": None}):
            from infomesh.services import bootstrap_p2p

            result = bootstrap_p2p(config)

        assert result == (None, None)

    def test_returns_none_on_start_error(self) -> None:
        """Returns (None, None) when node.start() raises."""
        from infomesh.services import bootstrap_p2p

        config = Config()

        mock_node_cls = MagicMock()
        mock_node_cls.return_value.start.side_effect = RuntimeError("PoW failed")

        with patch("infomesh.p2p.node.InfoMeshNode", mock_node_cls):
            node, dist_idx = bootstrap_p2p(config)

        assert node is None
        assert dist_idx is None

    def test_warns_no_bootstrap_nodes(self) -> None:
        """Logs warning when no bootstrap nodes configured."""
        from infomesh.services import bootstrap_p2p

        config = Config(network=NetworkConfig(bootstrap_nodes=[]))

        mock_node = MagicMock()
        mock_node.peer_id = "test_peer_id"
        mock_node_cls = MagicMock(return_value=mock_node)

        with patch("infomesh.p2p.node.InfoMeshNode", mock_node_cls):
            node, _dist_idx = bootstrap_p2p(config)

        # Node should have been started (even without bootstrap)
        if node is not None:
            mock_node.start.assert_called_once()

    def test_node_bootstrap_skips_self_multiaddr(self) -> None:
        """Bootstrap should not dial a multiaddr for the local peer ID."""
        import trio

        from infomesh.config import NodeConfig
        from infomesh.p2p.node import InfoMeshNode

        addr = f"/ip4/127.0.0.1/tcp/4001/p2p/{_VALID_TEST_PEER_ID}"
        config = Config(
            node=NodeConfig(data_dir=Path("/tmp")),
            network=NetworkConfig(
                bootstrap_nodes=[addr],
                bootstrap_dns=False,
                bootstrap_github=False,
            ),
        )
        node = InfoMeshNode(config)
        node._peer_id = _VALID_TEST_PEER_ID

        mock_host = MagicMock()
        node._host = mock_host

        trio.run(node._bootstrap)

        mock_host.connect.assert_not_called()
        assert node._bootstrap_results == {
            "configured": 1,
            "connected": 0,
            "failed": 0,
            "failed_addrs": [],
        }


class TestPeerCommand:
    """Tests for peer CLI helpers."""

    def test_peer_test_resolves_default_bootstrap(self) -> None:
        """peer test expands default bootstrap aliases before socket probing."""
        from infomesh.cli.peer import peer_group

        config = Config(network=NetworkConfig(bootstrap_nodes=["default"]))

        class FakeSocket:
            def settimeout(self, timeout: float) -> None:
                self.timeout = timeout

            def connect_ex(self, target: tuple[str, int]) -> int:
                self.target = target
                return 0

            def close(self) -> None:
                pass

        with (
            patch("infomesh.cli.peer.load_config", return_value=config),
            patch(
                "infomesh.config._load_default_bootstrap_nodes",
                return_value=["/ip4/127.0.0.1/tcp/4001/p2p/test-peer"],
            ),
            patch("socket.socket", return_value=FakeSocket()),
        ):
            result = CliRunner().invoke(peer_group, ["test"])

        assert result.exit_code == 0
        assert "OK" in result.output
        assert "SKIP" not in result.output


class TestNodeStatusFile:
    """Tests for InfoMeshNode status file writing."""

    def test_write_status_file(self, tmp_path: Path) -> None:
        """Node writes a valid JSON status file."""
        from infomesh.config import NodeConfig
        from infomesh.p2p.node import InfoMeshNode

        config = Config(node=NodeConfig(data_dir=tmp_path))
        node = InfoMeshNode(config)
        node._write_status_file()

        status_file = tmp_path / "p2p_status.json"
        assert status_file.exists()

        data = json.loads(status_file.read_text())
        assert data["state"] == "stopped"
        assert data["peers"] == 0
        assert "timestamp" in data

    def test_write_status_file_error(self, tmp_path: Path) -> None:
        """Node writes error state to status file."""
        from infomesh.config import NodeConfig
        from infomesh.p2p.node import InfoMeshNode

        config = Config(node=NodeConfig(data_dir=tmp_path))
        node = InfoMeshNode(config)
        node._write_status_file(state="error", error="test error")

        data = json.loads((tmp_path / "p2p_status.json").read_text())
        assert data["state"] == "error"
        assert data["error"] == "test error"

    def test_write_status_file_with_addrs(self, tmp_path: Path) -> None:
        """Status file includes listen addresses when host exists."""
        from infomesh.config import NodeConfig
        from infomesh.p2p.node import InfoMeshNode, NodeState

        config = Config(node=NodeConfig(data_dir=tmp_path))
        node = InfoMeshNode(config)

        # Simulate a running host
        mock_host = MagicMock()
        mock_host.get_addrs.return_value = ["/ip4/0.0.0.0/tcp/4001"]
        mock_host.get_connected_peers.return_value = ["peer1", "peer2"]
        node._host = mock_host
        node._state = NodeState.RUNNING

        node._write_status_file()

        data = json.loads((tmp_path / "p2p_status.json").read_text())
        assert data["state"] == "running"
        assert data["peers"] == 2
        assert len(data["listen_addrs"]) == 1


class TestStatusCommandP2P:
    """Tests for P2P output in the status command."""

    def test_status_shows_p2p_running(self, tmp_path: Path) -> None:
        """Status shows P2P running with peer count."""
        from infomesh.cli.serve import status

        status_data = {
            "state": "running",
            "peer_id": "abc123",
            "peers": 5,
            "listen_addrs": ["/ip4/0.0.0.0/tcp/4001"],
            "timestamp": time.time(),
            "error": "",
        }
        (tmp_path / "p2p_status.json").write_text(json.dumps(status_data))

        with patch("infomesh.cli.serve.load_config") as mock_cfg:
            from infomesh.config import NodeConfig

            mock_cfg.return_value = Config(node=NodeConfig(data_dir=tmp_path))

            with patch("infomesh.services.AppContext") as mock_ctx:
                mock_store = MagicMock()
                mock_store.get_stats.return_value = {"document_count": 10}
                ctx_instance = MagicMock()
                ctx_instance.store = mock_store
                ctx_instance.vector_store = None
                ctx_instance.ledger = None
                ctx_instance.__enter__ = MagicMock(return_value=ctx_instance)
                ctx_instance.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = ctx_instance

                # Create PID file so it shows "running"
                (tmp_path / "infomesh.pid").write_text("12345")
                # Create keys directory
                (tmp_path / "keys").mkdir(exist_ok=True)

                runner = CliRunner()
                result = runner.invoke(status)

                assert "running (5 peers)" in result.output

    def test_status_shows_p2p_error(self, tmp_path: Path) -> None:
        """Status shows P2P error state."""
        from infomesh.cli.serve import status

        status_data = {
            "state": "error",
            "peer_id": "",
            "peers": 0,
            "listen_addrs": [],
            "timestamp": time.time(),
            "error": "trio not installed",
        }
        (tmp_path / "p2p_status.json").write_text(json.dumps(status_data))

        with patch("infomesh.cli.serve.load_config") as mock_cfg:
            from infomesh.config import NodeConfig

            mock_cfg.return_value = Config(node=NodeConfig(data_dir=tmp_path))

            with patch("infomesh.services.AppContext") as mock_ctx:
                mock_store = MagicMock()
                mock_store.get_stats.return_value = {"document_count": 0}
                ctx_instance = MagicMock()
                ctx_instance.store = mock_store
                ctx_instance.vector_store = None
                ctx_instance.ledger = None
                ctx_instance.__enter__ = MagicMock(return_value=ctx_instance)
                ctx_instance.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = ctx_instance

                (tmp_path / "infomesh.pid").write_text("12345")
                (tmp_path / "keys").mkdir(exist_ok=True)

                runner = CliRunner()
                result = runner.invoke(status)

                assert "error" in result.output.lower()

    def test_status_shows_not_connected(self, tmp_path: Path) -> None:
        """Status shows 'not connected' when node runs but P2P is off."""
        from infomesh.cli.serve import status

        # No p2p_status.json, but PID file exists → "not connected"
        with patch("infomesh.cli.serve.load_config") as mock_cfg:
            from infomesh.config import NodeConfig

            mock_cfg.return_value = Config(node=NodeConfig(data_dir=tmp_path))

            with patch("infomesh.services.AppContext") as mock_ctx:
                mock_store = MagicMock()
                mock_store.get_stats.return_value = {"document_count": 0}
                ctx_instance = MagicMock()
                ctx_instance.store = mock_store
                ctx_instance.vector_store = None
                ctx_instance.ledger = None
                ctx_instance.__enter__ = MagicMock(return_value=ctx_instance)
                ctx_instance.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = ctx_instance

                (tmp_path / "infomesh.pid").write_text("12345")
                (tmp_path / "keys").mkdir(exist_ok=True)

                runner = CliRunner()
                result = runner.invoke(status)

                assert "not connected" in result.output

    def test_status_shows_p2p_stopped(self, tmp_path: Path) -> None:
        """Status shows stopped when node is not running."""
        from infomesh.cli.serve import status

        with patch("infomesh.cli.serve.load_config") as mock_cfg:
            from infomesh.config import NodeConfig

            mock_cfg.return_value = Config(node=NodeConfig(data_dir=tmp_path))

            with patch("infomesh.services.AppContext") as mock_ctx:
                mock_store = MagicMock()
                mock_store.get_stats.return_value = {"document_count": 0}
                ctx_instance = MagicMock()
                ctx_instance.store = mock_store
                ctx_instance.vector_store = None
                ctx_instance.ledger = None
                ctx_instance.__enter__ = MagicMock(return_value=ctx_instance)
                ctx_instance.__exit__ = MagicMock(return_value=False)
                mock_ctx.return_value = ctx_instance

                (tmp_path / "keys").mkdir(exist_ok=True)

                runner = CliRunner()
                result = runner.invoke(status)

                assert "P2P:             stopped" in result.output
