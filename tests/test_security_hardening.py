"""Tests for security hardening — message auth, audit evidence, persistence.

Covers:
- infomesh.p2p.message_auth (SignedEnvelope, sign/verify, replay, isolation)
- infomesh.trust.audit (auditor cross-validation, canonical bytes)
- infomesh.trust.scoring (is_isolated)
- infomesh.trust.dmca (SQLite persistence)
- infomesh.trust.gdpr (SQLite persistence)
- infomesh.p2p.protocol (SIGNED_ENVELOPE message type)
"""

from __future__ import annotations

import hashlib
import time

import pytest

from infomesh.p2p.message_auth import (
    MAX_MESSAGE_AGE_SECONDS,
    NonceCounter,
    NonceTracker,
    PeerKeyRegistry,
    SignedEnvelope,
    VerificationError,
    envelope_from_dict,
    envelope_to_dict,
    sign_envelope,
    verify_envelope,
)
from infomesh.p2p.protocol import MessageType
from infomesh.trust.audit import (
    AuditResult,
    AuditVerdict,
    _cross_validate_auditor_hashes,
    audit_result_canonical,
)
from infomesh.trust.dmca import (
    TakedownManager,
    TakedownStatus,
)
from infomesh.trust.gdpr import (
    DeletionBasis,
    DeletionManager,
    DeletionStatus,
)
from infomesh.trust.scoring import TrustStore

# ---- Mock helpers -----------------------------------------------------------


class MockKeyPair:
    """Deterministic mock key pair using SHA-256."""

    def __init__(self, peer_id: str = "test-peer-001") -> None:
        self._peer_id = peer_id

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def public_key_bytes(self) -> bytes:
        return hashlib.sha256(self._peer_id.encode()).digest()

    def sign(self, data: bytes) -> bytes:
        return hashlib.sha256(data + self._peer_id.encode()).digest() * 2

    def verify(self, data: bytes, signature: bytes) -> bool:
        return signature == self.sign(data)


def _make_verify_raw(kp: MockKeyPair):  # noqa: ANN202
    """Build a raw verify function matching the mock key pair."""

    def _verify_raw(pub_bytes: bytes, data: bytes, signature: bytes) -> bool:
        return signature == hashlib.sha256(data + kp.peer_id.encode()).digest() * 2

    return _verify_raw


# ═══════════════════════════════════════════════════════════════════
# 1. message_auth — SignedEnvelope, sign/verify, replay, isolation
# ═══════════════════════════════════════════════════════════════════


class TestNonceCounter:
    def test_monotonic(self) -> None:
        nc = NonceCounter()
        vals = [nc.next() for _ in range(5)]
        assert vals == [1, 2, 3, 4, 5]

    def test_current(self) -> None:
        nc = NonceCounter()
        nc.next()
        nc.next()
        assert nc.current == 2


class TestPeerKeyRegistry:
    def test_register_and_get(self) -> None:
        reg = PeerKeyRegistry()
        reg.register("p1", b"key1")
        assert reg.get("p1") == b"key1"

    def test_unknown_peer(self) -> None:
        reg = PeerKeyRegistry()
        assert reg.get("unknown") is None

    def test_remove(self) -> None:
        reg = PeerKeyRegistry()
        reg.register("p1", b"key1")
        reg.remove("p1")
        assert reg.get("p1") is None


class TestNonceTracker:
    def test_fresh_nonce(self) -> None:
        nt = NonceTracker()
        assert nt.check_and_record("p1", 1) is True

    def test_replay_rejected(self) -> None:
        nt = NonceTracker()
        nt.check_and_record("p1", 5)
        assert nt.check_and_record("p1", 3) is False

    def test_equal_nonce_rejected(self) -> None:
        nt = NonceTracker()
        nt.check_and_record("p1", 5)
        assert nt.check_and_record("p1", 5) is False

    def test_different_peers_independent(self) -> None:
        nt = NonceTracker()
        nt.check_and_record("p1", 10)
        assert nt.check_and_record("p2", 1) is True


class TestSignAndVerifyEnvelope:
    def test_roundtrip(self) -> None:
        kp = MockKeyPair("signer-001")
        nc = NonceCounter()
        reg = PeerKeyRegistry()
        reg.register(kp.peer_id, kp.public_key_bytes())
        nt = NonceTracker()

        envelope = sign_envelope(b"hello", kp, nc)
        assert envelope.payload == b"hello"
        assert envelope.peer_id == kp.peer_id

        # Override _verify_raw for mock
        import infomesh.p2p.message_auth as ma

        orig = ma._verify_raw
        ma._verify_raw = _make_verify_raw(kp)
        try:
            verify_envelope(envelope, reg, nt)
        finally:
            ma._verify_raw = orig

    def test_stale_timestamp_rejected(self) -> None:
        kp = MockKeyPair("signer-002")
        nc = NonceCounter()
        reg = PeerKeyRegistry()
        reg.register(kp.peer_id, kp.public_key_bytes())
        nt = NonceTracker()

        old_time = time.time() - MAX_MESSAGE_AGE_SECONDS - 100
        env = SignedEnvelope(
            payload=b"old",
            peer_id=kp.peer_id,
            signature=kp.sign(b"dummy"),
            nonce=nc.next(),
            timestamp=old_time,
        )
        with pytest.raises(VerificationError, match="too old"):
            verify_envelope(env, reg, nt)

    def test_unknown_peer_rejected(self) -> None:
        env = SignedEnvelope(
            payload=b"x",
            peer_id="stranger",
            signature=b"x",
            nonce=1,
            timestamp=time.time(),
        )
        reg = PeerKeyRegistry()
        nt = NonceTracker()
        with pytest.raises(VerificationError, match="unknown public key"):
            verify_envelope(env, reg, nt)

    def test_isolated_peer_rejected(self) -> None:
        env = SignedEnvelope(
            payload=b"x",
            peer_id="bad-peer",
            signature=b"x",
            nonce=1,
            timestamp=time.time(),
        )
        reg = PeerKeyRegistry()
        reg.register("bad-peer", b"key")
        nt = NonceTracker()

        def _is_isolated(pid: str) -> bool:
            return pid == "bad-peer"

        with pytest.raises(VerificationError, match="isolated"):
            verify_envelope(env, reg, nt, is_isolated_fn=_is_isolated)

    def test_replay_nonce_rejected(self) -> None:
        kp = MockKeyPair("signer-003")
        nc = NonceCounter()
        reg = PeerKeyRegistry()
        reg.register(kp.peer_id, kp.public_key_bytes())
        nt = NonceTracker()

        import infomesh.p2p.message_auth as ma

        orig = ma._verify_raw
        ma._verify_raw = _make_verify_raw(kp)
        try:
            env1 = sign_envelope(b"msg1", kp, nc)
            verify_envelope(env1, reg, nt)

            # Replay env1 (same nonce)
            with pytest.raises(VerificationError, match="replay"):
                verify_envelope(env1, reg, nt)
        finally:
            ma._verify_raw = orig


class TestEnvelopeSerialization:
    def test_roundtrip(self) -> None:
        env = SignedEnvelope(
            payload=b"data",
            peer_id="p1",
            signature=b"sig",
            nonce=42,
            timestamp=1000.0,
        )
        d = envelope_to_dict(env)
        restored = envelope_from_dict(d)
        assert restored.payload == env.payload
        assert restored.peer_id == env.peer_id
        assert restored.nonce == env.nonce
        assert restored.timestamp == env.timestamp
        assert restored.signature == env.signature


# ═══════════════════════════════════════════════════════════════════
# 2. protocol — SIGNED_ENVELOPE message type
# ═══════════════════════════════════════════════════════════════════


class TestSignedEnvelopeProtocol:
    def test_message_type_exists(self) -> None:
        assert MessageType.SIGNED_ENVELOPE == 100

    def test_encode_decode(self) -> None:
        from infomesh.p2p.protocol import (
            decode_signed_envelope,
            encode_signed_envelope,
        )

        env_dict = {
            "payload": b"hello",
            "peer_id": "p1",
            "signature": b"sig",
            "nonce": 1,
            "timestamp": 1000.0,
        }
        raw = encode_signed_envelope(env_dict)
        decoded = decode_signed_envelope(raw)
        assert decoded is not None
        assert decoded["peer_id"] == "p1"


# ═══════════════════════════════════════════════════════════════════
# 3. audit — cross-validation + canonical bytes
# ═══════════════════════════════════════════════════════════════════


class TestAuditCrossValidation:
    def test_honest_majority(self) -> None:
        now = time.time()
        results = [
            AuditResult(
                audit_id="a1",
                url="http://example.com",
                auditor_peer_id="p1",
                target_peer_id="target",
                actual_text_hash="aaa",
                actual_raw_hash="bbb",
                verdict=AuditVerdict.PASS,
                detail="ok",
                completed_at=now,
            ),
            AuditResult(
                audit_id="a1",
                url="http://example.com",
                auditor_peer_id="p2",
                target_peer_id="target",
                actual_text_hash="aaa",
                actual_raw_hash="bbb",
                verdict=AuditVerdict.PASS,
                detail="ok",
                completed_at=now,
            ),
            AuditResult(
                audit_id="a1",
                url="http://example.com",
                auditor_peer_id="p3",
                target_peer_id="target",
                actual_text_hash="DIFFERENT",
                actual_raw_hash="bbb",
                verdict=AuditVerdict.FAIL,
                detail="mismatch",
                completed_at=now,
            ),
        ]
        suspicious = _cross_validate_auditor_hashes(results)
        assert "p3" in suspicious
        assert "p1" not in suspicious
        assert "p2" not in suspicious

    def test_all_agree(self) -> None:
        now = time.time()
        results = [
            AuditResult(
                audit_id="a1",
                url="http://example.com",
                auditor_peer_id=f"p{i}",
                target_peer_id="target",
                actual_text_hash="aaa",
                actual_raw_hash="bbb",
                verdict=AuditVerdict.PASS,
                detail="ok",
                completed_at=now,
            )
            for i in range(3)
        ]
        assert _cross_validate_auditor_hashes(results) == []

    def test_error_results_excluded(self) -> None:
        now = time.time()
        results = [
            AuditResult(
                audit_id="a1",
                url="http://example.com",
                auditor_peer_id="p1",
                target_peer_id="target",
                actual_text_hash="",
                actual_raw_hash="",
                verdict=AuditVerdict.ERROR,
                detail="err",
                completed_at=now,
            ),
        ]
        assert _cross_validate_auditor_hashes(results) == []


class TestAuditResultCanonical:
    def test_deterministic(self) -> None:
        now = time.time()
        r = AuditResult(
            audit_id="a1",
            url="http://example.com",
            auditor_peer_id="p1",
            target_peer_id="t1",
            actual_text_hash="aaa",
            actual_raw_hash="bbb",
            verdict=AuditVerdict.PASS,
            detail="ok",
            completed_at=now,
        )
        b1 = audit_result_canonical(r)
        b2 = audit_result_canonical(r)
        assert b1 == b2
        assert b"a1|p1|t1|" in b1


class TestAuditResultSignature:
    def test_signature_field(self) -> None:
        r = AuditResult(
            audit_id="a1",
            url="http://example.com",
            auditor_peer_id="p1",
            target_peer_id="t1",
            actual_text_hash="aaa",
            actual_raw_hash="bbb",
            verdict=AuditVerdict.PASS,
            detail="ok",
            completed_at=time.time(),
            auditor_signature=b"sig123",
        )
        assert r.auditor_signature == b"sig123"


# ═══════════════════════════════════════════════════════════════════
# 4. scoring — is_isolated
# ═══════════════════════════════════════════════════════════════════


class TestTrustStoreIsolated:
    def test_unknown_peer_not_isolated(self) -> None:
        with TrustStore() as ts:
            assert ts.is_isolated("unknown") is False

    def test_isolated_peer(self) -> None:
        with TrustStore() as ts:
            ts.isolate_peer("bad-peer")
            assert ts.is_isolated("bad-peer") is True

    def test_non_isolated_peer(self) -> None:
        with TrustStore() as ts:
            ts.record_audit(
                "good-peer",
                passed=True,
            )
            assert ts.is_isolated("good-peer") is False


# ═══════════════════════════════════════════════════════════════════
# 5. DMCA persistence
# ═══════════════════════════════════════════════════════════════════


class TestDMCAPersistence:
    def test_survives_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = str(tmp_path / "dmca.db")
        kp = MockKeyPair("dmca-req")

        # Session 1: create notice
        mgr1 = TakedownManager(db_path=db)
        notice = mgr1.create_notice(
            url="http://evil.com/stolen",
            reason="Copyright",
            key_pair=kp,
            now=1000.0,
        )
        mgr1.acknowledge(notice.notice_id, "node-A")
        mgr1.record_propagation(notice.notice_id, "node-B")
        mgr1.close()

        # Session 2: reload and verify
        mgr2 = TakedownManager(db_path=db)
        assert mgr2.is_taken_down("http://evil.com/stolen")
        rec = mgr2.get_record(notice.notice_id)
        assert rec is not None
        assert len(rec.acknowledgments) == 1
        assert rec.acknowledgments[0].peer_id == "node-A"
        assert "node-B" in rec.propagated_to
        mgr2.close()

    def test_without_db_still_works(self) -> None:
        mgr = TakedownManager()
        kp = MockKeyPair("dmca-req2")
        mgr.create_notice(
            url="http://example.com/x",
            reason="r",
            key_pair=kp,
            now=1000.0,
        )
        assert mgr.is_taken_down("http://example.com/x")
        mgr.close()

    def test_compliance_persisted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = str(tmp_path / "dmca2.db")
        kp = MockKeyPair("dmca-req3")

        mgr = TakedownManager(db_path=db)
        notice = mgr.create_notice(
            url="http://copy.com/a",
            reason="Copyrighted",
            key_pair=kp,
            now=1000.0,
        )
        mgr.mark_complied(notice.notice_id, "node-C", now=2000.0)
        mgr.close()

        mgr2 = TakedownManager(db_path=db)
        status = mgr2.check_compliance(notice.notice_id, "node-C")
        assert status == TakedownStatus.COMPLIED
        mgr2.close()


# ═══════════════════════════════════════════════════════════════════
# 6. GDPR persistence
# ═══════════════════════════════════════════════════════════════════


class TestGDPRPersistence:
    def test_survives_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = str(tmp_path / "gdpr.db")
        kp = MockKeyPair("gdpr-req")

        # Session 1
        mgr1 = DeletionManager(db_path=db)
        req = mgr1.create_request(
            url="http://example.com/personal",
            basis=DeletionBasis.RIGHT_TO_ERASURE,
            reason="Personal data",
            key_pair=kp,
            now=1000.0,
        )
        mgr1.confirm_deletion(req.request_id, "node-X", now=2000.0)
        mgr1.record_propagation(req.request_id, "node-Y")
        mgr1.close()

        # Session 2
        mgr2 = DeletionManager(db_path=db)
        assert mgr2.is_blocked("http://example.com/personal")
        rec = mgr2.get_record(req.request_id)
        assert rec is not None
        assert len(rec.confirmations) == 1
        assert rec.confirmations[0].status == DeletionStatus.DELETED
        assert "node-Y" in rec.propagated_to
        mgr2.close()

    def test_blocklist_persisted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = str(tmp_path / "gdpr2.db")
        kp = MockKeyPair("gdpr-req2")

        mgr = DeletionManager(db_path=db)
        mgr.create_request(
            url="http://blocked.com/data",
            basis=DeletionBasis.CONSENT_WITHDRAWN,
            reason="Consent withdrawn",
            key_pair=kp,
            now=1000.0,
        )
        mgr.close()

        mgr2 = DeletionManager(db_path=db)
        assert mgr2.is_blocked("http://blocked.com/data")
        assert mgr2.blocklist_size >= 1
        mgr2.close()

    def test_without_db_still_works(self) -> None:
        mgr = DeletionManager()
        kp = MockKeyPair("gdpr-req3")
        mgr.create_request(
            url="http://example.com/y",
            basis=DeletionBasis.OBJECTION,
            reason="r",
            key_pair=kp,
            now=1000.0,
        )
        assert mgr.is_blocked("http://example.com/y")
        mgr.close()

    def test_unblock_persisted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = str(tmp_path / "gdpr3.db")
        kp = MockKeyPair("gdpr-req4")
        admin = MockKeyPair("admin")

        mgr = DeletionManager(db_path=db)
        mgr.create_request(
            url="http://unblock.com/data",
            basis=DeletionBasis.RIGHT_TO_ERASURE,
            reason="Personal data",
            key_pair=kp,
            now=1000.0,
        )
        assert mgr.is_blocked("http://unblock.com/data")
        mgr.unblock("http://unblock.com/data", admin_key=admin)
        assert not mgr.is_blocked("http://unblock.com/data")
        mgr.close()

        mgr2 = DeletionManager(db_path=db)
        assert not mgr2.is_blocked("http://unblock.com/data")
        mgr2.close()


# ═══════════════════════════════════════════════════════════════════
# 7. Real Ed25519 verification (no mocking)
# ═══════════════════════════════════════════════════════════════════


class TestRealEd25519Verification:
    """Exercise the actual cryptography library path — no monkey-patching."""

    def test_real_sign_and_verify_roundtrip(self) -> None:
        """Sign with real Ed25519 key, verify with real _verify_raw."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        from infomesh.p2p.message_auth import _verify_raw

        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        pub_bytes = pub.public_bytes_raw()

        data = b"important P2P message payload"
        signature = priv.sign(data)

        # Valid signature must pass
        assert _verify_raw(pub_bytes, data, signature) is True

    def test_real_tampered_data_rejected(self) -> None:
        """Tampered data must fail verification."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        from infomesh.p2p.message_auth import _verify_raw

        priv = Ed25519PrivateKey.generate()
        pub = priv.public_key()
        pub_bytes = pub.public_bytes_raw()

        data = b"original message"
        signature = priv.sign(data)

        # Tampered data must fail
        assert _verify_raw(pub_bytes, b"tampered message", signature) is False

    def test_real_wrong_key_rejected(self) -> None:
        """Signature from a different key must fail."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        from infomesh.p2p.message_auth import _verify_raw

        priv1 = Ed25519PrivateKey.generate()
        priv2 = Ed25519PrivateKey.generate()
        pub2_bytes = priv2.public_key().public_bytes_raw()

        data = b"message signed by key1"
        sig_from_key1 = priv1.sign(data)

        # Wrong public key must fail
        assert _verify_raw(pub2_bytes, data, sig_from_key1) is False

    def test_real_full_envelope_roundtrip(self) -> None:
        """Full SignedEnvelope flow with real Ed25519 keys."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )

        priv = Ed25519PrivateKey.generate()
        pub_bytes = priv.public_key().public_bytes_raw()

        # Build a KeyPair-like object using real crypto
        class RealKeyPair:
            def __init__(self, private_key: Ed25519PrivateKey) -> None:
                self._priv = private_key
                import hashlib

                self._peer_id = hashlib.sha256(
                    self._priv.public_key().public_bytes_raw()
                ).hexdigest()[:40]

            @property
            def peer_id(self) -> str:
                return self._peer_id

            def public_key_bytes(self) -> bytes:
                return self._priv.public_key().public_bytes_raw()

            def sign(self, data: bytes) -> bytes:
                return self._priv.sign(data)

            def verify(self, data: bytes, signature: bytes) -> bool:
                try:
                    self._priv.public_key().verify(signature, data)
                    return True
                except Exception:
                    return False

        kp = RealKeyPair(priv)
        nc = NonceCounter()
        reg = PeerKeyRegistry()
        reg.register(kp.peer_id, pub_bytes)
        nt = NonceTracker()

        envelope = sign_envelope(b"real crypto payload", kp, nc)

        # Must succeed without any mocking
        result = verify_envelope(envelope, reg, nt)
        assert result == b"real crypto payload"
