"""Tests for DMCA takedown propagation."""

from __future__ import annotations

import hashlib

import pytest

from infomesh.trust.dmca import (
    COMPLIANCE_DEADLINE_HOURS,
    TAKEDOWN_DHT_PREFIX,
    TakedownManager,
    TakedownStatus,
    deserialize_notice,
    serialize_notice,
    takedown_dht_key,
)


class MockKeyPair:
    """Mock key pair for DMCA tests."""

    def __init__(self, peer_id: str = "dmca-peer-001") -> None:
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
def manager() -> TakedownManager:
    return TakedownManager()


class TestCreateNotice:
    def test_basic(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        notice = manager.create_notice(
            url="http://example.com/page",
            reason="Copyright infringement",
            key_pair=kp,
            now=1000.0,
        )
        assert notice.url == "http://example.com/page"
        assert notice.requester_id == kp.peer_id
        assert notice.reason == "Copyright infringement"
        assert notice.deadline == 1000.0 + COMPLIANCE_DEADLINE_HOURS * 3600

    def test_contact_info(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        notice = manager.create_notice(
            url="http://x.com/p",
            reason="test",
            key_pair=kp,
            contact_info="legal@example.com",
        )
        assert notice.contact_info == "legal@example.com"

    def test_duplicate_url_overwrites(
        self, manager: TakedownManager, kp: MockKeyPair
    ) -> None:
        n1 = manager.create_notice("http://x.com", "r1", kp, now=100.0)
        n2 = manager.create_notice("http://x.com", "r2", kp, now=200.0)
        assert n1.notice_id != n2.notice_id
        # Latest notice is accessible by URL
        assert manager.get_notice_for_url("http://x.com") == n2


class TestVerifyNotice:
    def test_valid_signature(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        notice = manager.create_notice("http://a.com", "reason", kp)
        assert manager.verify_notice(notice, kp) is True

    def test_invalid_signature(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        notice = manager.create_notice("http://a.com", "reason", kp)
        different_kp = MockKeyPair("other-peer")
        assert manager.verify_notice(notice, different_kp) is False


class TestAcknowledgeAndComply:
    def test_acknowledge(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        notice = manager.create_notice("http://a.com", "r", kp)
        ack = manager.acknowledge(notice.notice_id, "node-1")
        assert ack is not None
        assert ack.status == TakedownStatus.ACKNOWLEDGED

    def test_acknowledge_unknown(self, manager: TakedownManager) -> None:
        assert manager.acknowledge("nonexistent", "node-1") is None

    def test_mark_complied(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        notice = manager.create_notice("http://a.com", "r", kp)
        ack = manager.mark_complied(notice.notice_id, "node-1")
        assert ack is not None
        assert ack.status == TakedownStatus.COMPLIED
        assert ack.complied_at is not None

    def test_compliance_check_complied(
        self, manager: TakedownManager, kp: MockKeyPair
    ) -> None:
        notice = manager.create_notice("http://a.com", "r", kp)
        manager.mark_complied(notice.notice_id, "node-1")
        status = manager.check_compliance(notice.notice_id, "node-1")
        assert status == TakedownStatus.COMPLIED

    def test_compliance_check_pending(
        self, manager: TakedownManager, kp: MockKeyPair
    ) -> None:
        notice = manager.create_notice("http://a.com", "r", kp, now=1000.0)
        status = manager.check_compliance(notice.notice_id, "node-1", now=1000.0)
        assert status == TakedownStatus.PENDING

    def test_compliance_check_expired(
        self, manager: TakedownManager, kp: MockKeyPair
    ) -> None:
        notice = manager.create_notice("http://a.com", "r", kp, now=1000.0)
        # Far past deadline
        status = manager.check_compliance(
            notice.notice_id, "node-1", now=1000.0 + 48 * 3600
        )
        assert status == TakedownStatus.EXPIRED


class TestIsTakenDown:
    def test_taken_down(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        manager.create_notice("http://a.com", "dmca", kp)
        assert manager.is_taken_down("http://a.com") is True
        assert manager.is_taken_down("http://other.com") is False


class TestListMethods:
    def test_list_active(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        manager.create_notice("http://a.com", "r1", kp)
        manager.create_notice("http://b.com", "r2", kp)
        assert len(manager.list_active()) == 2

    def test_list_non_compliant(
        self, manager: TakedownManager, kp: MockKeyPair
    ) -> None:
        n1 = manager.create_notice("http://a.com", "r1", kp)
        _n2 = manager.create_notice("http://b.com", "r2", kp)
        manager.mark_complied(n1.notice_id, "node-1")
        non_compliant = manager.list_non_compliant("node-1")
        assert len(non_compliant) == 1
        assert non_compliant[0].url == "http://b.com"


class TestPropagation:
    def test_record_propagation(
        self, manager: TakedownManager, kp: MockKeyPair
    ) -> None:
        notice = manager.create_notice("http://a.com", "r", kp)
        manager.record_propagation(notice.notice_id, "peer-A")
        manager.record_propagation(notice.notice_id, "peer-B")
        manager.record_propagation(notice.notice_id, "peer-A")  # duplicate
        record = manager.get_record(notice.notice_id)
        assert record is not None
        assert record.propagated_to == ["peer-A", "peer-B"]


class TestSerialization:
    def test_roundtrip(self, manager: TakedownManager, kp: MockKeyPair) -> None:
        notice = manager.create_notice("http://a.com", "DMCA reason", kp, now=5000.0)
        data = serialize_notice(notice)
        restored = deserialize_notice(data)
        assert restored.notice_id == notice.notice_id
        assert restored.url == notice.url
        assert restored.reason == notice.reason
        assert restored.signature == notice.signature
        assert restored.deadline == notice.deadline


class TestDhtKey:
    def test_key_format(self) -> None:
        key = takedown_dht_key("http://example.com")
        assert key.startswith(TAKEDOWN_DHT_PREFIX)
        assert len(key) > len(TAKEDOWN_DHT_PREFIX) + 10

    def test_deterministic(self) -> None:
        assert takedown_dht_key("http://x.com") == takedown_dht_key("http://x.com")

    def test_different_urls(self) -> None:
        assert takedown_dht_key("http://a.com") != takedown_dht_key("http://b.com")
