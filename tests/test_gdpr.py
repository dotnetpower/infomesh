"""Tests for GDPR distributed deletion."""

from __future__ import annotations

import hashlib

import pytest

from infomesh.trust.gdpr import (
    DELETION_DHT_PREFIX,
    DeletionBasis,
    DeletionManager,
    DeletionStatus,
    deletion_dht_key,
    deserialize_request,
    serialize_request,
)


class MockKeyPair:
    """Mock key pair for GDPR tests."""

    def __init__(self, peer_id: str = "gdpr-peer-001") -> None:
        self._peer_id = peer_id

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def sign(self, data: bytes) -> bytes:
        return hashlib.sha256(data + self._peer_id.encode()).digest() * 2

    def verify(self, data: bytes, signature: bytes) -> bool:
        return signature == self.sign(data)


@pytest.fixture()
def kp() -> MockKeyPair:
    return MockKeyPair()


@pytest.fixture()
def manager() -> DeletionManager:
    return DeletionManager()


class TestCreateRequest:
    def test_basic(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        req = manager.create_request(
            url="http://example.com/personal",
            basis=DeletionBasis.RIGHT_TO_ERASURE,
            reason="Contains personal data",
            key_pair=kp,
            now=1000.0,
        )
        assert req.url == "http://example.com/personal"
        assert req.basis == DeletionBasis.RIGHT_TO_ERASURE
        assert req.requester_id == kp.peer_id
        assert manager.is_blocked(req.url)

    def test_personal_data_fields(
        self, manager: DeletionManager, kp: MockKeyPair
    ) -> None:
        req = manager.create_request(
            url="http://x.com/p",
            basis=DeletionBasis.CONSENT_WITHDRAWN,
            reason="consent withdrawn",
            key_pair=kp,
            personal_data_fields=["email", "phone"],
        )
        assert req.personal_data_fields == ["email", "phone"]

    def test_all_bases(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        for basis in DeletionBasis:
            req = manager.create_request(
                url=f"http://x.com/{basis.value}",
                basis=basis,
                reason="test",
                key_pair=kp,
            )
            assert req.basis == basis


class TestVerifyRequest:
    def test_valid_signature(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        req = manager.create_request(
            "http://a.com", DeletionBasis.RIGHT_TO_ERASURE, "r", kp
        )
        assert manager.verify_request(req, kp) is True

    def test_invalid_signature(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        req = manager.create_request(
            "http://a.com", DeletionBasis.RIGHT_TO_ERASURE, "r", kp
        )
        other = MockKeyPair("other-peer")
        assert manager.verify_request(req, other) is False


class TestReceiveRequest:
    def test_external_request(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        other_manager = DeletionManager()
        req = other_manager.create_request(
            "http://personal.com/page",
            DeletionBasis.OBJECTION,
            "objection",
            kp,
        )
        # Simulate receiving from DHT — must provide requester key
        result = manager.receive_request(req, requester_key=kp)
        assert result is True
        assert manager.is_blocked("http://personal.com/page")
        assert manager.get_request_for_url("http://personal.com/page") == req

    def test_external_request_rejected_without_key(
        self, manager: DeletionManager, kp: MockKeyPair
    ) -> None:
        other_manager = DeletionManager()
        req = other_manager.create_request(
            "http://personal.com/page2",
            DeletionBasis.OBJECTION,
            "objection",
            kp,
        )
        # Without key, receive_request should reject
        result = manager.receive_request(req)
        assert result is False
        assert not manager.is_blocked("http://personal.com/page2")

    def test_external_request_rejected_with_wrong_key(
        self, manager: DeletionManager, kp: MockKeyPair
    ) -> None:
        other_manager = DeletionManager()
        req = other_manager.create_request(
            "http://personal.com/page3",
            DeletionBasis.OBJECTION,
            "objection",
            kp,
        )
        wrong_kp = MockKeyPair("wrong-peer")
        result = manager.receive_request(req, requester_key=wrong_kp)
        assert result is False
        assert not manager.is_blocked("http://personal.com/page3")


class TestConfirmDeletion:
    def test_confirm(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        req = manager.create_request(
            "http://a.com", DeletionBasis.RIGHT_TO_ERASURE, "r", kp
        )
        conf = manager.confirm_deletion(req.request_id, "node-1", now=2000.0)
        assert conf is not None
        assert conf.status == DeletionStatus.DELETED
        assert conf.deleted_at == 2000.0

    def test_confirm_unknown(self, manager: DeletionManager) -> None:
        assert manager.confirm_deletion("nonexistent", "node-1") is None


class TestBlocklist:
    def test_blocked_after_create(
        self, manager: DeletionManager, kp: MockKeyPair
    ) -> None:
        manager.create_request(
            "http://blocked.com", DeletionBasis.UNLAWFUL_PROCESSING, "r", kp
        )
        assert manager.is_blocked("http://blocked.com") is True
        assert manager.is_blocked("http://notblocked.com") is False

    def test_blocklist_size(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        for i in range(5):
            manager.create_request(
                f"http://x.com/{i}", DeletionBasis.RIGHT_TO_ERASURE, "r", kp
            )
        assert manager.blocklist_size == 5


class TestListMethods:
    def test_list_pending(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        r1 = manager.create_request(
            "http://a.com", DeletionBasis.RIGHT_TO_ERASURE, "r1", kp
        )
        _r2 = manager.create_request(
            "http://b.com", DeletionBasis.RIGHT_TO_ERASURE, "r2", kp
        )
        manager.confirm_deletion(r1.request_id, "node-1")
        pending = manager.list_pending("node-1")
        assert len(pending) == 1
        assert pending[0].url == "http://b.com"

    def test_list_all(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        manager.create_request("http://a.com", DeletionBasis.RIGHT_TO_ERASURE, "r", kp)
        manager.create_request(
            "http://b.com", DeletionBasis.CONSENT_WITHDRAWN, "r2", kp
        )
        assert len(manager.list_all()) == 2


class TestPropagation:
    def test_record_propagation(
        self, manager: DeletionManager, kp: MockKeyPair
    ) -> None:
        req = manager.create_request(
            "http://a.com", DeletionBasis.RIGHT_TO_ERASURE, "r", kp
        )
        manager.record_propagation(req.request_id, "peer-A")
        manager.record_propagation(req.request_id, "peer-B")
        manager.record_propagation(req.request_id, "peer-A")  # dup
        record = manager.get_record(req.request_id)
        assert record is not None
        assert record.propagated_to == ["peer-A", "peer-B"]


class TestSerialization:
    def test_roundtrip(self, manager: DeletionManager, kp: MockKeyPair) -> None:
        req = manager.create_request(
            "http://a.com",
            DeletionBasis.LEGAL_OBLIGATION,
            "legal requirement",
            kp,
            personal_data_fields=["name", "address"],
            now=5000.0,
        )
        data = serialize_request(req)
        restored = deserialize_request(data)
        assert restored.request_id == req.request_id
        assert restored.url == req.url
        assert restored.basis == DeletionBasis.LEGAL_OBLIGATION
        assert restored.signature == req.signature
        assert restored.personal_data_fields == ["name", "address"]


class TestDhtKey:
    def test_key_format(self) -> None:
        key = deletion_dht_key("http://example.com")
        assert key.startswith(DELETION_DHT_PREFIX)

    def test_deterministic(self) -> None:
        assert deletion_dht_key("http://x.com") == deletion_dht_key("http://x.com")

    def test_different_urls(self) -> None:
        assert deletion_dht_key("http://a.com") != deletion_dht_key("http://b.com")
