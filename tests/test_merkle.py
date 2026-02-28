"""Tests for infomesh.trust.merkle — Merkle Tree integrity verification."""

from __future__ import annotations

import hashlib

import pytest

from infomesh.trust.merkle import (
    MerkleProof,
    MerkleTree,
    ProofSide,
    _hash_leaf,
    _hash_pair,
    deserialize_merkle_root,
    deserialize_proof,
    serialize_merkle_root,
    serialize_proof,
)


class MockKeyPair:
    """Lightweight key pair mock for Merkle root signing tests."""

    def __init__(self, peer_id: str = "merkle-peer-001") -> None:
        self._peer_id = peer_id

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def sign(self, data: bytes) -> bytes:
        return hashlib.sha256(data + self._peer_id.encode()).digest() * 2

    def verify(self, data: bytes, signature: bytes) -> bool:
        expected = self.sign(data)
        return signature == expected


def _sample_hashes(n: int) -> list[str]:
    """Generate n deterministic SHA-256 hex hashes."""
    return [hashlib.sha256(f"doc_{i}".encode()).hexdigest() for i in range(n)]


@pytest.fixture
def tree():
    return MerkleTree()


@pytest.fixture
def keypair():
    return MockKeyPair()


# --- Hash functions --------------------------------------------------------


class TestHashFunctions:
    def test_hash_leaf_prefix(self):
        h = _hash_leaf("abc")
        expected = hashlib.sha256(b"leaf:abc").hexdigest()
        assert h == expected

    def test_hash_leaf_deterministic(self):
        assert _hash_leaf("test") == _hash_leaf("test")

    def test_hash_pair(self):
        h = _hash_pair("aaa", "bbb")
        expected = hashlib.sha256(b"aaabbb").hexdigest()
        assert h == expected

    def test_hash_pair_order_matters(self):
        assert _hash_pair("a", "b") != _hash_pair("b", "a")


# --- MerkleTree.build ------------------------------------------------------


class TestBuild:
    def test_empty_raises(self, tree):
        with pytest.raises(ValueError, match="empty"):
            tree.build([])

    def test_single_document(self, tree):
        hashes = _sample_hashes(1)
        root = tree.build(hashes)
        assert root == _hash_leaf(hashes[0])
        assert tree.leaf_count == 1
        assert tree.height == 1

    def test_two_documents(self, tree):
        hashes = _sample_hashes(2)
        root = tree.build(hashes)
        expected = _hash_pair(_hash_leaf(hashes[0]), _hash_leaf(hashes[1]))
        assert root == expected
        assert tree.leaf_count == 2
        assert tree.height == 2

    def test_power_of_two(self, tree):
        hashes = _sample_hashes(4)
        root = tree.build(hashes)
        assert len(root) == 64  # SHA-256 hex
        assert tree.leaf_count == 4
        assert tree.height == 3  # [4 leaves, 2, 1]

    def test_odd_count(self, tree):
        hashes = _sample_hashes(3)
        root = tree.build(hashes)
        assert len(root) == 64
        assert tree.leaf_count == 3

    def test_large_tree(self, tree):
        hashes = _sample_hashes(100)
        root = tree.build(hashes)
        assert len(root) == 64
        assert tree.leaf_count == 100
        assert tree.height >= 7  # log2(100) ~ 7

    def test_root_hash_deterministic(self, tree):
        hashes = _sample_hashes(10)
        root1 = tree.build(hashes)
        tree2 = MerkleTree()
        root2 = tree2.build(hashes)
        assert root1 == root2

    def test_different_documents_different_root(self, tree):
        root1 = tree.build(_sample_hashes(5))
        tree2 = MerkleTree()
        root2 = tree2.build([h + "x" for h in _sample_hashes(5)])
        assert root1 != root2

    def test_built_at_set(self, tree):
        tree.build(_sample_hashes(3))
        assert tree.built_at > 0


# --- MerkleTree.get_proof --------------------------------------------------


class TestGetProof:
    def test_not_built_raises(self, tree):
        with pytest.raises(RuntimeError, match="not built"):
            tree.get_proof(0)

    def test_index_out_of_range(self, tree):
        tree.build(_sample_hashes(3))
        with pytest.raises(IndexError):
            tree.get_proof(3)

    def test_negative_index(self, tree):
        tree.build(_sample_hashes(3))
        with pytest.raises(IndexError):
            tree.get_proof(-1)

    def test_proof_has_correct_leaf(self, tree):
        hashes = _sample_hashes(4)
        tree.build(hashes)
        proof = tree.get_proof(0)
        assert proof.doc_hash == _hash_leaf(hashes[0])
        assert proof.root_hash == tree.root_hash
        assert proof.leaf_index == 0

    def test_proof_path_length(self, tree):
        hashes = _sample_hashes(8)
        tree.build(hashes)
        proof = tree.get_proof(0)
        # 8 leaves → 3 levels above leaves → 3 siblings in path
        assert len(proof.proof_path) == 3

    def test_proof_for_each_leaf(self, tree):
        hashes = _sample_hashes(5)
        tree.build(hashes)
        for i in range(5):
            proof = tree.get_proof(i)
            assert proof.leaf_index == i
            assert proof.root_hash == tree.root_hash


# --- MerkleTree.verify_proof -----------------------------------------------


class TestVerifyProof:
    def test_valid_proof(self, tree):
        hashes = _sample_hashes(4)
        tree.build(hashes)
        proof = tree.get_proof(0)
        assert MerkleTree.verify_proof(proof) is True

    def test_all_leaves_verify(self, tree):
        hashes = _sample_hashes(16)
        tree.build(hashes)
        for i in range(16):
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(proof) is True

    def test_tampered_doc_hash_fails(self, tree):
        hashes = _sample_hashes(4)
        tree.build(hashes)
        proof = tree.get_proof(0)
        tampered = MerkleProof(
            doc_hash="deadbeef" * 8,  # 64 hex chars
            proof_path=proof.proof_path,
            root_hash=proof.root_hash,
            leaf_index=proof.leaf_index,
        )
        assert MerkleTree.verify_proof(tampered) is False

    def test_tampered_root_hash_fails(self, tree):
        hashes = _sample_hashes(4)
        tree.build(hashes)
        proof = tree.get_proof(0)
        tampered = MerkleProof(
            doc_hash=proof.doc_hash,
            proof_path=proof.proof_path,
            root_hash="0" * 64,
            leaf_index=proof.leaf_index,
        )
        assert MerkleTree.verify_proof(tampered) is False

    def test_tampered_proof_path_fails(self, tree):
        hashes = _sample_hashes(4)
        tree.build(hashes)
        proof = tree.get_proof(0)
        bad_path = (("bad" * 21 + "b", ProofSide.RIGHT),) + proof.proof_path[1:]
        tampered = MerkleProof(
            doc_hash=proof.doc_hash,
            proof_path=bad_path,
            root_hash=proof.root_hash,
            leaf_index=proof.leaf_index,
        )
        assert MerkleTree.verify_proof(tampered) is False

    def test_odd_leaf_count(self, tree):
        """Odd leaf count (with duplication) still produces valid proofs."""
        hashes = _sample_hashes(7)
        tree.build(hashes)
        for i in range(7):
            proof = tree.get_proof(i)
            assert MerkleTree.verify_proof(proof) is True

    def test_single_leaf_proof(self, tree):
        hashes = _sample_hashes(1)
        tree.build(hashes)
        proof = tree.get_proof(0)
        assert MerkleTree.verify_proof(proof) is True
        assert len(proof.proof_path) == 0


# --- MerkleTree.verify_document --------------------------------------------


class TestVerifyDocument:
    def test_valid_document(self, tree):
        hashes = _sample_hashes(4)
        tree.build(hashes)
        proof = tree.get_proof(2)
        assert MerkleTree.verify_document(hashes[2], proof) is True

    def test_wrong_document_hash(self, tree):
        hashes = _sample_hashes(4)
        tree.build(hashes)
        proof = tree.get_proof(2)
        assert MerkleTree.verify_document("wrong_hash", proof) is False

    def test_all_documents_verify(self, tree):
        hashes = _sample_hashes(10)
        tree.build(hashes)
        for i in range(10):
            proof = tree.get_proof(i)
            assert MerkleTree.verify_document(hashes[i], proof) is True


# --- create_root_record ----------------------------------------------------


class TestCreateRootRecord:
    def test_unsigned(self, tree):
        tree.build(_sample_hashes(3))
        root = tree.create_root_record("peer-1")
        assert root.root_hash == tree.root_hash
        assert root.document_count == 3
        assert root.peer_id == "peer-1"
        assert root.signature == b""

    def test_signed(self, tree, keypair):
        tree.build(_sample_hashes(5))
        root = tree.create_root_record(keypair.peer_id, keypair)
        assert root.signature != b""
        assert root.document_count == 5

    def test_signed_root_verifiable(self, tree, keypair):
        tree.build(_sample_hashes(5))
        root = tree.create_root_record(keypair.peer_id, keypair)

        # Manually verify signature
        payload = (
            f"{root.root_hash}|{root.document_count}"
            f"|{root.built_at}|{root.peer_id}"
        ).encode()
        assert keypair.verify(payload, root.signature) is True


# --- Serialization ---------------------------------------------------------


class TestSerializeMerkleRoot:
    def test_round_trip(self, tree, keypair):
        tree.build(_sample_hashes(4))
        root = tree.create_root_record(keypair.peer_id, keypair)
        data = serialize_merkle_root(root)
        restored = deserialize_merkle_root(data)
        assert restored.root_hash == root.root_hash
        assert restored.document_count == root.document_count
        assert restored.peer_id == root.peer_id
        assert restored.signature == root.signature

    def test_unsigned_round_trip(self, tree):
        tree.build(_sample_hashes(2))
        root = tree.create_root_record("peer-x")
        data = serialize_merkle_root(root)
        restored = deserialize_merkle_root(data)
        assert restored.root_hash == root.root_hash
        assert restored.signature == b""


class TestSerializeProof:
    def test_round_trip(self, tree):
        hashes = _sample_hashes(8)
        tree.build(hashes)
        proof = tree.get_proof(3)
        data = serialize_proof(proof)
        restored = deserialize_proof(data)
        assert restored.doc_hash == proof.doc_hash
        assert restored.root_hash == proof.root_hash
        assert restored.leaf_index == proof.leaf_index
        assert restored.proof_path == proof.proof_path

    def test_single_leaf_round_trip(self, tree):
        hashes = _sample_hashes(1)
        tree.build(hashes)
        proof = tree.get_proof(0)
        data = serialize_proof(proof)
        restored = deserialize_proof(data)
        assert MerkleTree.verify_proof(restored) is True


# --- verify_merkle_root (attestation integration) -------------------------


class TestVerifyMerkleRoot:
    def test_valid_root(self, tree, keypair):
        from infomesh.trust.attestation import verify_merkle_root

        tree.build(_sample_hashes(4))
        root = tree.create_root_record(keypair.peer_id, keypair)
        assert verify_merkle_root(root, keypair) is True

    def test_wrong_key(self, tree, keypair):
        from infomesh.trust.attestation import verify_merkle_root

        tree.build(_sample_hashes(4))
        root = tree.create_root_record(keypair.peer_id, keypair)
        other_kp = MockKeyPair("other-peer")
        assert verify_merkle_root(root, other_kp) is False

    def test_unsigned_root(self, tree, keypair):
        from infomesh.trust.attestation import verify_merkle_root

        tree.build(_sample_hashes(4))
        root = tree.create_root_record("peer-x")  # unsigned
        # Unsigned → empty signature → verify should fail
        assert verify_merkle_root(root, keypair) is False


# --- perform_merkle_audit (audit integration) ------------------------------


class TestPerformMerkleAudit:
    def test_valid_proof_passes(self, tree):
        from infomesh.trust.audit import AuditVerdict, perform_merkle_audit

        hashes = _sample_hashes(8)
        root_hash = tree.build(hashes)
        proof = tree.get_proof(3)
        result = perform_merkle_audit(
            document_hash=hashes[3],
            proof=proof,
            expected_root_hash=root_hash,
            auditor_peer_id="auditor-1",
            target_peer_id="target-1",
            url="https://example.com/doc3",
        )
        assert result.verdict == AuditVerdict.PASS
        assert "valid" in result.detail

    def test_wrong_root_fails(self, tree):
        from infomesh.trust.audit import AuditVerdict, perform_merkle_audit

        hashes = _sample_hashes(8)
        tree.build(hashes)
        proof = tree.get_proof(3)
        result = perform_merkle_audit(
            document_hash=hashes[3],
            proof=proof,
            expected_root_hash="0" * 64,
            auditor_peer_id="auditor-1",
            target_peer_id="target-1",
        )
        assert result.verdict == AuditVerdict.FAIL
        assert "root_mismatch" in result.detail

    def test_invalid_proof_fails(self, tree):
        from infomesh.trust.audit import AuditVerdict, perform_merkle_audit

        hashes = _sample_hashes(8)
        root_hash = tree.build(hashes)
        proof = tree.get_proof(3)
        result = perform_merkle_audit(
            document_hash="wrong_document_hash",
            proof=proof,
            expected_root_hash=root_hash,
            auditor_peer_id="auditor-1",
            target_peer_id="target-1",
        )
        assert result.verdict == AuditVerdict.FAIL
        assert "invalid" in result.detail

    def test_audit_id_and_peers_preserved(self, tree):
        from infomesh.trust.audit import perform_merkle_audit

        hashes = _sample_hashes(4)
        root_hash = tree.build(hashes)
        proof = tree.get_proof(0)
        result = perform_merkle_audit(
            document_hash=hashes[0],
            proof=proof,
            expected_root_hash=root_hash,
            auditor_peer_id="aud-X",
            audit_id="audit-123",
            target_peer_id="tgt-Y",
            url="https://example.com",
        )
        assert result.audit_id == "audit-123"
        assert result.auditor_peer_id == "aud-X"
        assert result.target_peer_id == "tgt-Y"
        assert result.url == "https://example.com"
