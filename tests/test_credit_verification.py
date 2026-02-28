"""Tests for infomesh.credits.verification — P2P credit verification."""

from __future__ import annotations

import pytest

from infomesh.credits.ledger import ActionType, CreditLedger
from infomesh.credits.verification import CreditProofBuilder
from infomesh.p2p.keys import KeyPair

# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def key_pair() -> KeyPair:
    """Generate a fresh Ed25519 key pair for tests."""
    return KeyPair.generate()


@pytest.fixture
def another_key_pair() -> KeyPair:
    """A different key pair (simulates another peer)."""
    return KeyPair.generate()


@pytest.fixture
def ledger():
    """In-memory credit ledger."""
    lg = CreditLedger()
    yield lg
    lg.close()


@pytest.fixture
def signed_ledger(ledger: CreditLedger, key_pair: KeyPair) -> CreditLedger:
    """Ledger with several signed entries."""
    ledger.record_action(
        ActionType.CRAWL, quantity=5.0, note="page1", key_pair=key_pair
    )
    ledger.record_action(
        ActionType.QUERY_PROCESS, quantity=3.0, note="q1", key_pair=key_pair
    )
    ledger.record_action(
        ActionType.NETWORK_UPTIME, quantity=2.0, note="up", key_pair=key_pair
    )
    ledger.record_action(
        ActionType.CRAWL, quantity=1.0, note="page2", key_pair=key_pair
    )
    return ledger


# ─── Entry signing tests ──────────────────────────────────


class TestEntrySigning:
    """Verify that record_action stores hash+signature when key_pair is given."""

    def test_signed_entry_has_hash(self, ledger: CreditLedger, key_pair: KeyPair):
        ledger.record_action(ActionType.CRAWL, quantity=1.0, key_pair=key_pair)
        entries = ledger.recent_entries(limit=1)
        assert len(entries) == 1
        assert entries[0].entry_hash != ""
        assert len(entries[0].entry_hash) == 64  # SHA-256 hex

    def test_signed_entry_has_signature(self, ledger: CreditLedger, key_pair: KeyPair):
        ledger.record_action(ActionType.CRAWL, quantity=1.0, key_pair=key_pair)
        entries = ledger.recent_entries(limit=1)
        assert entries[0].signature != ""
        assert len(entries[0].signature) == 128  # Ed25519 sig hex

    def test_unsigned_entry_has_hash_but_no_sig(self, ledger: CreditLedger):
        ledger.record_action(ActionType.CRAWL, quantity=1.0)
        entries = ledger.recent_entries(limit=1)
        assert entries[0].entry_hash != ""
        assert entries[0].signature == ""

    def test_signed_entries_excludes_unsigned(
        self,
        ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        # One unsigned, two signed
        ledger.record_action(ActionType.CRAWL, quantity=1.0)
        ledger.record_action(ActionType.CRAWL, quantity=2.0, key_pair=key_pair)
        ledger.record_action(ActionType.CRAWL, quantity=3.0, key_pair=key_pair)

        signed = ledger.signed_entries()
        assert len(signed) == 2
        assert all(e.signature != "" for e in signed)


# ─── Proof building tests ─────────────────────────────────


class TestProofBuilding:
    """Test CreditProofBuilder.build_proof()."""

    def test_empty_ledger_proof(self, ledger: CreditLedger, key_pair: KeyPair):
        builder = CreditProofBuilder(ledger, key_pair)
        proof = builder.build_proof()

        assert proof["peer_id"] == key_pair.peer_id
        assert proof["entry_count"] == 0
        assert proof["total_earned"] == 0.0
        assert proof["sample_entries"] == []

    def test_proof_has_merkle_root(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof()

        assert proof["entry_count"] == 4
        assert proof["merkle_root"] != ""
        assert len(proof["merkle_root"]) == 64
        assert proof["root_signature"] != ""

    def test_proof_sample_size(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        builder = CreditProofBuilder(signed_ledger, key_pair)

        # Request all entries as sample
        proof = builder.build_proof(sample_size=100)
        assert len(proof["sample_entries"]) == 4
        assert len(proof["sample_proofs"]) == 4

        # Request smaller sample
        proof2 = builder.build_proof(sample_size=2)
        assert len(proof2["sample_entries"]) == 2

    def test_proof_contains_action_breakdown(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof()

        bd = proof["action_breakdown"]
        assert "crawl" in bd
        assert "query_process" in bd
        assert bd["crawl"] == 6.0  # 5.0 + 1.0 weights × qty × 1.0
        assert bd["query_process"] == 1.5  # 0.5 weight × 3.0 qty

    def test_proof_includes_public_key(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof()

        assert proof["public_key"] == key_pair.public_key_bytes().hex()

    def test_proof_request_id_forwarded(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(request_id="req-42")
        assert proof["request_id"] == "req-42"


# ─── Proof verification tests ─────────────────────────────


class TestProofVerification:
    """Test CreditProofBuilder.verify_proof()."""

    def test_valid_proof_passes(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        result = CreditProofBuilder.verify_proof(proof)

        assert result.verified is True
        assert result.merkle_root_valid is True
        assert result.valid_signatures == 4
        assert result.invalid_signatures == 0
        assert result.valid_proofs == 4
        assert result.invalid_proofs == 0
        assert result.detail == "ok"

    def test_empty_proof_passes(self, ledger: CreditLedger, key_pair: KeyPair):
        builder = CreditProofBuilder(ledger, key_pair)
        proof = builder.build_proof()

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is True
        assert result.detail == "empty_ledger"

    def test_tampered_entry_detected(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        """Modifying an entry's credits should fail signature verification."""
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        # Tamper: inflate first entry's credits
        proof["sample_entries"][0]["credits"] = 9999.0

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is False
        assert result.invalid_signatures > 0

    def test_tampered_entry_hash_detected(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        """Modifying the entry_hash should fail verification."""
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        # Tamper: change entry hash
        proof["sample_entries"][0]["entry_hash"] = "a" * 64

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is False

    def test_forged_signature_detected(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
        another_key_pair: KeyPair,
    ):
        """Entry signed with a different key should fail verification."""
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        # Replace one entry's signature with one from another key
        entry = proof["sample_entries"][0]
        from infomesh.credits.ledger import _entry_canonical

        canonical = _entry_canonical(
            entry["action"],
            entry["quantity"],
            entry["weight"],
            entry["multiplier"],
            entry["credits"],
            entry["timestamp"],
            entry["note"],
        )
        forged_sig = another_key_pair.sign(canonical)
        proof["sample_entries"][0]["signature"] = forged_sig.hex()

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is False
        assert result.invalid_signatures > 0

    def test_tampered_merkle_root_detected(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        """Changing the Merkle root should fail root signature check."""
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        # Tamper merkle root
        proof["merkle_root"] = "b" * 64

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is False
        assert result.merkle_root_valid is False

    def test_tampered_root_signature_detected(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        """Invalid root signature should fail."""
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        proof["root_signature"] = "cc" * 64

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is False
        assert result.merkle_root_valid is False

    def test_invalid_public_key_fails(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
    ):
        """Garbage public key should return a useful error."""
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        proof["public_key"] = "deadbeef"

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is False
        assert "invalid_public_key" in result.detail

    def test_wrong_peer_public_key_fails(
        self,
        signed_ledger: CreditLedger,
        key_pair: KeyPair,
        another_key_pair: KeyPair,
    ):
        """Using another peer's public key should fail all checks."""
        builder = CreditProofBuilder(signed_ledger, key_pair)
        proof = builder.build_proof(sample_size=100)

        # Replace public key with another peer's
        proof["public_key"] = another_key_pair.public_key_bytes().hex()

        result = CreditProofBuilder.verify_proof(proof)
        assert result.verified is False


# ─── Protocol message tests ───────────────────────────────


class TestProtocolMessages:
    """Verify credit verification protocol message types exist."""

    def test_credit_message_types_exist(self):
        from infomesh.p2p.protocol import MessageType

        assert MessageType.CREDIT_PROOF_REQUEST == 70
        assert MessageType.CREDIT_PROOF_RESPONSE == 71

    def test_credit_protocol_id_exists(self):
        from infomesh.p2p.protocol import PROTOCOL_CREDIT

        assert PROTOCOL_CREDIT == "/infomesh/credit/1.0.0"

    def test_credit_proof_request_dataclass(self):
        from infomesh.p2p.protocol import CreditProofRequest

        req = CreditProofRequest(
            requester_peer_id="peer_abc",
            request_id="req-1",
            sample_size=5,
        )
        assert req.requester_peer_id == "peer_abc"
        assert req.sample_size == 5

    def test_credit_proof_response_dataclass(self):
        from infomesh.p2p.protocol import CreditProofResponse

        resp = CreditProofResponse(
            peer_id="peer_xyz",
            request_id="req-1",
            total_earned=42.0,
            total_spent=5.0,
            action_breakdown={"crawl": 30.0},
            entry_count=100,
            merkle_root="a" * 64,
            root_signature="b" * 128,
            sample_entries=[],
            sample_proofs=[],
            public_key="c" * 64,
        )
        assert resp.total_earned == 42.0
        assert resp.entry_count == 100


# ─── Schema migration test ────────────────────────────────


class TestLedgerMigration:
    """Verify that existing databases get the new columns."""

    def test_migration_adds_columns(self):
        """Ledger opened twice should not fail (migration is idempotent)."""
        lg1 = CreditLedger()
        lg1.record_action(ActionType.CRAWL, quantity=1.0)
        lg1.close()

        # Re-create (simulates reopen)
        lg2 = CreditLedger()
        lg2.record_action(ActionType.CRAWL, quantity=2.0)
        entries = lg2.recent_entries(limit=10)
        # Both entries should have the hash/signature columns
        assert all(hasattr(e, "entry_hash") for e in entries)
        lg2.close()


# ─── End-to-end round-trip ────────────────────────────────


class TestRoundTrip:
    """Full build → serialize → verify round-trip."""

    def test_many_entries_round_trip(self, key_pair: KeyPair):
        """Verify proof with many entries (tests Merkle tree scaling)."""
        ledger = CreditLedger()
        try:
            for i in range(50):
                ledger.record_action(
                    ActionType.CRAWL,
                    quantity=1.0,
                    note=f"page_{i}",
                    key_pair=key_pair,
                )

            builder = CreditProofBuilder(ledger, key_pair)
            proof = builder.build_proof(sample_size=10)

            assert proof["entry_count"] == 50
            assert len(proof["sample_entries"]) == 10

            result = CreditProofBuilder.verify_proof(proof)
            assert result.verified is True
            assert result.valid_signatures == 10
            assert result.valid_proofs == 10
        finally:
            ledger.close()

    def test_mixed_actions_round_trip(self, key_pair: KeyPair):
        """Verify proof with all action types."""
        ledger = CreditLedger()
        try:
            for action in ActionType:
                ledger.record_action(action, quantity=1.0, key_pair=key_pair)

            builder = CreditProofBuilder(ledger, key_pair)
            proof = builder.build_proof(sample_size=100)

            result = CreditProofBuilder.verify_proof(proof)
            assert result.verified is True
            assert result.entry_count == len(ActionType)
            assert len(proof["action_breakdown"]) == len(ActionType)
        finally:
            ledger.close()
