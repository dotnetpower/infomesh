"""Tests for enterprise split deployment — NodeRole, IndexSubmit, conditional init."""

from __future__ import annotations

from dataclasses import replace as dc_replace
from pathlib import Path
from unittest.mock import MagicMock

from infomesh.config import Config, NodeRole, load_config
from infomesh.crawler.parser import ParsedPage
from infomesh.p2p.protocol import (
    PROTOCOL_INDEX_SUBMIT,
    IndexSubmitAck,
    MessageType,
    decode_message,
    encode_message,
)

# ─── NodeRole ──────────────────────────────────────────────


class TestNodeRole:
    """Test NodeRole constants and validation."""

    def test_role_values(self) -> None:
        assert NodeRole.FULL == "full"
        assert NodeRole.CRAWLER == "crawler"
        assert NodeRole.SEARCH == "search"

    def test_all_contains_three(self) -> None:
        assert len(NodeRole.ALL) == 3
        assert NodeRole.FULL in NodeRole.ALL
        assert NodeRole.CRAWLER in NodeRole.ALL
        assert NodeRole.SEARCH in NodeRole.ALL

    def test_default_role_is_full(self) -> None:
        config = Config()
        assert config.node.role == NodeRole.FULL


class TestNodeRoleConfig:
    """Test role configuration via TOML and env vars."""

    def test_role_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text('[node]\nrole = "crawler"\n')
        config = load_config(config_file)
        assert config.node.role == "crawler"

    def test_role_search_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text('[node]\nrole = "search"\n')
        config = load_config(config_file)
        assert config.node.role == "search"

    def test_listen_address_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text('[node]\nlisten_address = "192.168.1.10"\n')
        config = load_config(config_file)
        assert config.node.listen_address == "192.168.1.10"

    def test_index_submit_peers_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            "[network]\n"
            "index_submit_peers = ["
            '"/ip4/10.0.0.1/tcp/4001", '
            '"/ip4/10.0.0.2/tcp/4001"'
            "]\n"
        )
        config = load_config(config_file)
        assert len(config.network.index_submit_peers) == 2
        assert "/ip4/10.0.0.1/tcp/4001" in config.network.index_submit_peers

    def test_peer_acl_from_toml(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text('[network]\npeer_acl = ["peer-id-1", "peer-id-2"]\n')
        config = load_config(config_file)
        assert len(config.network.peer_acl) == 2

    def test_role_replace_frozen(self) -> None:
        """Frozen dataclass role can be overridden via replace."""
        config = Config()
        new_config = dc_replace(
            config, node=dc_replace(config.node, role=NodeRole.CRAWLER)
        )
        assert new_config.node.role == NodeRole.CRAWLER
        assert config.node.role == NodeRole.FULL  # original unchanged


# ─── IndexSubmit protocol messages ─────────────────────────


class TestIndexSubmitProtocol:
    """Test INDEX_SUBMIT / INDEX_SUBMIT_ACK message encoding."""

    def test_protocol_id(self) -> None:
        assert PROTOCOL_INDEX_SUBMIT == "/infomesh/index-submit/1.0.0"

    def test_message_type_values(self) -> None:
        assert MessageType.INDEX_SUBMIT == 80
        assert MessageType.INDEX_SUBMIT_ACK == 81

    def test_index_submit_roundtrip(self) -> None:
        payload = {
            "url": "https://example.com",
            "title": "Example",
            "text": "Hello world",
            "raw_html_hash": "abc123",
            "text_hash": "def456",
            "language": "en",
            "crawled_at": 1234567890.0,
            "peer_id": "test-peer",
            "signature": b"sig-bytes",
            "discovered_links": ["https://example.com/page2"],
        }
        encoded = encode_message(MessageType.INDEX_SUBMIT, payload)
        msg_type, decoded = decode_message(encoded)
        assert msg_type == MessageType.INDEX_SUBMIT
        assert decoded["url"] == "https://example.com"
        assert decoded["title"] == "Example"
        assert decoded["peer_id"] == "test-peer"

    def test_index_submit_ack_roundtrip(self) -> None:
        payload = {
            "url": "https://example.com",
            "doc_id": 42,
            "success": True,
            "error": None,
            "peer_id": "indexer-peer",
        }
        encoded = encode_message(MessageType.INDEX_SUBMIT_ACK, payload)
        msg_type, decoded = decode_message(encoded)
        assert msg_type == MessageType.INDEX_SUBMIT_ACK
        assert decoded["doc_id"] == 42
        assert decoded["success"] is True


# ─── IndexSubmitSender ─────────────────────────────────────


class TestIndexSubmitSender:
    """Test IndexSubmitSender (runs on DMZ crawler nodes)."""

    def _make_sender(
        self,
        submit_peers: list[str] | None = None,
        key_pair: object | None = None,
    ):
        from infomesh.p2p.index_submit import IndexSubmitSender

        config = Config()
        peers = submit_peers or ["/ip4/10.0.0.1/tcp/4001"]
        config = dc_replace(
            config,
            network=dc_replace(config.network, index_submit_peers=peers),
        )
        return IndexSubmitSender(config, key_pair)

    def _make_page(self) -> ParsedPage:
        return ParsedPage(
            url="https://example.com",
            title="Example Page",
            text="This is the content.",
            raw_html_hash="raw123",
            text_hash="txt456",
            language="en",
        )

    def test_build_submit_message(self) -> None:
        sender = self._make_sender()
        page = self._make_page()
        msg = sender.build_submit_message(page)
        assert isinstance(msg, bytes)
        msg_type, decoded = decode_message(msg)
        assert msg_type == MessageType.INDEX_SUBMIT
        assert decoded["url"] == "https://example.com"
        assert decoded["title"] == "Example Page"
        assert decoded["text"] == "This is the content."

    def test_build_submit_message_with_discovered_links(self) -> None:
        sender = self._make_sender()
        page = self._make_page()
        links = ["https://example.com/a", "https://example.com/b"]
        msg = sender.build_submit_message(page, discovered_links=links)
        _, decoded = decode_message(msg)
        assert decoded["discovered_links"] == links

    def test_build_submit_message_with_key_pair(self) -> None:
        mock_kp = MagicMock()
        mock_kp.peer_id = "my-peer-id"
        mock_kp.sign.return_value = b"fake-sig"
        sender = self._make_sender(key_pair=mock_kp)
        page = self._make_page()
        msg = sender.build_submit_message(page)
        _, decoded = decode_message(msg)
        assert decoded["peer_id"] == "my-peer-id"
        assert decoded["signature"] == b"fake-sig"
        mock_kp.sign.assert_called_once()

    def test_stats_tracking(self) -> None:
        sender = self._make_sender()
        assert sender.stats == {"sent": 0, "errors": 0}
        sender.record_sent()
        sender.record_sent()
        sender.record_error()
        assert sender.stats == {"sent": 2, "errors": 1}

    def test_submit_peers(self) -> None:
        peers = ["/ip4/10.0.0.1/tcp/4001", "/ip4/10.0.0.2/tcp/4001"]
        sender = self._make_sender(submit_peers=peers)
        assert sender.submit_peers == peers


# ─── IndexSubmitReceiver ───────────────────────────────────


class TestIndexSubmitReceiver:
    """Test IndexSubmitReceiver (runs on private search nodes)."""

    def _make_receiver(
        self,
        tmp_path: Path,
        peer_acl: list[str] | None = None,
    ):
        from infomesh.p2p.index_submit import IndexSubmitReceiver

        config = Config()
        if peer_acl is not None:
            config = dc_replace(
                config,
                network=dc_replace(config.network, peer_acl=peer_acl),
            )
        store = MagicMock()
        store.add_document.return_value = 42
        mock_kp = MagicMock()
        mock_kp.peer_id = "indexer-peer"
        return IndexSubmitReceiver(config, store, vector_store=None, key_pair=mock_kp)

    def test_open_mode_allows_any_peer(self, tmp_path: Path) -> None:
        """Empty ACL = open mode, all peers allowed."""
        receiver = self._make_receiver(tmp_path, peer_acl=[])
        assert receiver.is_peer_allowed("any-peer") is True

    def test_acl_allows_listed_peer(self, tmp_path: Path) -> None:
        receiver = self._make_receiver(tmp_path, peer_acl=["peer-a", "peer-b"])
        assert receiver.is_peer_allowed("peer-a") is True
        assert receiver.is_peer_allowed("peer-b") is True

    def test_acl_rejects_unlisted_peer(self, tmp_path: Path) -> None:
        receiver = self._make_receiver(tmp_path, peer_acl=["peer-a"])
        assert receiver.is_peer_allowed("peer-c") is False

    def test_handle_submit_success(self, tmp_path: Path) -> None:
        receiver = self._make_receiver(tmp_path)
        payload = {
            "url": "https://example.com",
            "title": "Example",
            "text": "Content here",
            "raw_html_hash": "raw123",
            "text_hash": "txt456",
            "language": "en",
            "peer_id": "crawler-1",
            "signature": b"sig",
        }
        ack = receiver.handle_submit(payload)
        assert ack.success is True
        assert ack.url == "https://example.com"
        assert receiver.stats["received"] == 1
        assert receiver.stats["indexed"] == 1
        assert receiver.stats["rejected"] == 0

    def test_handle_submit_rejected_by_acl(self, tmp_path: Path) -> None:
        receiver = self._make_receiver(tmp_path, peer_acl=["allowed-peer"])
        payload = {
            "url": "https://example.com",
            "title": "Example",
            "text": "Content",
            "peer_id": "bad-peer",
        }
        ack = receiver.handle_submit(payload)
        assert ack.success is False
        assert ack.error == "peer_not_allowed"
        assert receiver.stats["rejected"] == 1
        assert receiver.stats["indexed"] == 0

    def test_build_ack_message(self, tmp_path: Path) -> None:
        receiver = self._make_receiver(tmp_path)
        ack = IndexSubmitAck(
            url="https://example.com",
            doc_id=42,
            success=True,
            peer_id="indexer-peer",
        )
        msg = receiver.build_ack_message(ack)
        msg_type, decoded = decode_message(msg)
        assert msg_type == MessageType.INDEX_SUBMIT_ACK
        assert decoded["doc_id"] == 42
        assert decoded["success"] is True


# ─── AppContext conditional init ───────────────────────────


class TestAppContextRoles:
    """Test that AppContext initializes different components per role."""

    def test_full_role_has_all_components(self, tmp_data_dir: Path) -> None:
        config = Config()
        config = dc_replace(
            config,
            node=dc_replace(config.node, data_dir=tmp_data_dir, role=NodeRole.FULL),
            index=dc_replace(config.index, db_path=tmp_data_dir / "test.db"),
        )
        from infomesh.services import AppContext

        ctx = AppContext(config)
        try:
            assert ctx.worker is not None
            assert ctx.scheduler is not None
            assert ctx.dedup is not None
            assert ctx.robots is not None
            assert ctx.link_graph is not None
            assert ctx.store is not None
            assert ctx.index_submit_sender is None  # no submit peers
            assert ctx.index_submit_receiver is None  # not search role
        finally:
            ctx.close()

    def test_crawler_role_no_search_components(self, tmp_data_dir: Path) -> None:
        config = Config()
        config = dc_replace(
            config,
            node=dc_replace(config.node, data_dir=tmp_data_dir, role=NodeRole.CRAWLER),
            index=dc_replace(config.index, db_path=tmp_data_dir / "test.db"),
        )
        from infomesh.services import AppContext

        ctx = AppContext(config)
        try:
            # Crawler has crawl components
            assert ctx.worker is not None
            assert ctx.scheduler is not None
            assert ctx.dedup is not None
            assert ctx.robots is not None
            # Crawler does NOT have search components
            assert ctx.link_graph is None
            assert ctx.ledger is None
            assert ctx.vector_store is None
        finally:
            ctx.close()

    def test_search_role_no_crawler_components(self, tmp_data_dir: Path) -> None:
        config = Config()
        config = dc_replace(
            config,
            node=dc_replace(config.node, data_dir=tmp_data_dir, role=NodeRole.SEARCH),
            index=dc_replace(config.index, db_path=tmp_data_dir / "test.db"),
        )
        from infomesh.services import AppContext

        ctx = AppContext(config)
        try:
            # Search has search components
            assert ctx.link_graph is not None
            assert ctx.store is not None
            # Search does NOT have crawler components
            assert ctx.worker is None
            assert ctx.scheduler is None
            assert ctx.dedup is None
            assert ctx.robots is None
        finally:
            ctx.close()

    def test_crawler_role_with_submit_peers(self, tmp_data_dir: Path) -> None:
        config = Config()
        config = dc_replace(
            config,
            node=dc_replace(config.node, data_dir=tmp_data_dir, role=NodeRole.CRAWLER),
            index=dc_replace(config.index, db_path=tmp_data_dir / "test.db"),
            network=dc_replace(
                config.network,
                index_submit_peers=["/ip4/10.0.0.1/tcp/4001"],
            ),
        )
        from infomesh.services import AppContext

        ctx = AppContext(config)
        try:
            assert ctx.index_submit_sender is not None
            assert ctx.index_submit_receiver is None
        finally:
            ctx.close()

    def test_search_role_has_receiver(self, tmp_data_dir: Path) -> None:
        config = Config()
        config = dc_replace(
            config,
            node=dc_replace(config.node, data_dir=tmp_data_dir, role=NodeRole.SEARCH),
            index=dc_replace(config.index, db_path=tmp_data_dir / "test.db"),
        )
        from infomesh.services import AppContext

        ctx = AppContext(config)
        try:
            assert ctx.index_submit_receiver is not None
            assert ctx.index_submit_sender is None
        finally:
            ctx.close()

    def test_close_handles_none_components(self, tmp_data_dir: Path) -> None:
        """close() should not raise when components are None (crawler role)."""
        config = Config()
        config = dc_replace(
            config,
            node=dc_replace(config.node, data_dir=tmp_data_dir, role=NodeRole.CRAWLER),
            index=dc_replace(config.index, db_path=tmp_data_dir / "test.db"),
        )
        from infomesh.services import AppContext

        ctx = AppContext(config)
        ctx.close()  # Should not raise
