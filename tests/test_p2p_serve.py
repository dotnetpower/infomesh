"""Tests for P2P integration in CLI serve and status commands.

Covers:
- _try_start_p2p graceful fallback when libp2p unavailable
- _get_p2p_status reading from status file
- P2P status file writing in InfoMeshNode
- Status command P2P output formatting
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from infomesh.config import Config, NetworkConfig


class TestGetP2PStatus:
    """Tests for _get_p2p_status helper."""

    def test_no_status_file(self, tmp_path: Path) -> None:
        """Returns stopped when no status file exists."""
        from infomesh.cli.serve import _get_p2p_status
        from infomesh.config import NodeConfig

        config = Config(node=NodeConfig(data_dir=tmp_path))
        result = _get_p2p_status(config)
        assert result["state"] == "stopped"
        assert result["peers"] == 0

    def test_valid_status_file(self, tmp_path: Path) -> None:
        """Reads fresh status file correctly."""
        from infomesh.cli.serve import _get_p2p_status
        from infomesh.config import NodeConfig

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
        result = _get_p2p_status(config)
        assert result["state"] == "running"
        assert result["peers"] == 3
        assert len(result["listen_addrs"]) == 1

    def test_stale_status_file(self, tmp_path: Path) -> None:
        """Returns stopped when status file is stale (>30s old)."""
        from infomesh.cli.serve import _get_p2p_status
        from infomesh.config import NodeConfig

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
        result = _get_p2p_status(config)
        assert result["state"] == "stopped"

    def test_corrupt_status_file(self, tmp_path: Path) -> None:
        """Returns stopped when status file is corrupt."""
        from infomesh.cli.serve import _get_p2p_status
        from infomesh.config import NodeConfig

        status_file = tmp_path / "p2p_status.json"
        status_file.write_text("not valid json {{{")

        config = Config(node=NodeConfig(data_dir=tmp_path))
        result = _get_p2p_status(config)
        assert result["state"] == "stopped"

    def test_error_state(self, tmp_path: Path) -> None:
        """Reads error state and message."""
        from infomesh.cli.serve import _get_p2p_status
        from infomesh.config import NodeConfig

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
        result = _get_p2p_status(config)
        assert result["state"] == "error"
        assert result["error"] == "trio not installed"


class TestTryStartP2P:
    """Tests for _try_start_p2p graceful fallback."""

    def test_returns_none_when_import_fails(self) -> None:
        """Returns None and logs warning when libp2p not installed."""
        mock_logger = MagicMock()
        config = Config()

        with (
            patch.dict("sys.modules", {"infomesh.p2p.node": None}),
            patch("infomesh.cli.serve._try_start_p2p") as mock_fn,
        ):
            mock_fn.return_value = None
            result = mock_fn(config, mock_logger)

        assert result is None

    def test_returns_none_on_start_error(self) -> None:
        """Returns None when node.start() raises."""
        from infomesh.cli.serve import _try_start_p2p

        mock_logger = MagicMock()
        config = Config()

        mock_node_cls = MagicMock()
        mock_node_cls.return_value.start.side_effect = RuntimeError("PoW failed")

        with patch("infomesh.p2p.node.InfoMeshNode", mock_node_cls):
            result = _try_start_p2p(config, mock_logger)

        assert result is None
        # Should have logged a warning
        mock_logger.warning.assert_called()

    def test_warns_no_bootstrap_nodes(self) -> None:
        """Logs warning when no bootstrap nodes configured."""
        from infomesh.cli.serve import _try_start_p2p

        mock_logger = MagicMock()
        config = Config(network=NetworkConfig(bootstrap_nodes=[]))

        mock_node = MagicMock()
        mock_node.peer_id = "test_peer_id"
        mock_node_cls = MagicMock(return_value=mock_node)

        with patch("infomesh.p2p.node.InfoMeshNode", mock_node_cls):
            result = _try_start_p2p(config, mock_logger)

        if result is not None:
            # Should have warned about no bootstrap nodes
            warning_calls = [
                c
                for c in mock_logger.warning.call_args_list
                if "p2p_no_bootstrap" in str(c)
            ]
            assert len(warning_calls) >= 1


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
