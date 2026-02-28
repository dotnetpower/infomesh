"""Merkle Tree for index-wide integrity verification.

Provides:
- Build a Merkle tree from document content hashes.
- Generate membership proofs (O(log N) hashes).
- Verify proofs without full index download.
- Periodic root hash for DHT publication.

Layer 1: SHA-256(document)       — per-document integrity  (attestation.py)
Layer 2: Ed25519 signature       — origin proof             (attestation.py)
Layer 3: Merkle tree root        — index-wide integrity     (THIS MODULE)
Layer 4: DHT root publication    — time-based snapshots     (integration)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import StrEnum

import structlog

from infomesh.types import KeyPairLike

logger = structlog.get_logger()


class ProofSide(StrEnum):
    """Side of a sibling hash in a membership proof."""

    LEFT = "L"
    RIGHT = "R"


@dataclass(frozen=True)
class MerkleProof:
    """Membership proof for a single document in the tree.

    Contains the sequence of sibling hashes from the leaf to the root.
    Each step is ``(sibling_hash, side)`` where *side* indicates
    whether the sibling is on the left or right.
    """

    doc_hash: str
    proof_path: tuple[tuple[str, str], ...]  # ((hash, side), ...)
    root_hash: str
    leaf_index: int


@dataclass(frozen=True)
class MerkleRoot:
    """Signed Merkle root for DHT publication."""

    root_hash: str
    document_count: int
    built_at: float
    peer_id: str
    signature: bytes = b""


def _hash_pair(left: str, right: str) -> str:
    """Compute SHA-256 of two concatenated hex hashes."""
    combined = (left + right).encode("ascii")
    return hashlib.sha256(combined).hexdigest()


def _hash_leaf(data: str) -> str:
    """Hash a leaf node (prefixed to prevent second-preimage attacks)."""
    prefixed = ("leaf:" + data).encode("utf-8")
    return hashlib.sha256(prefixed).hexdigest()


class MerkleTree:
    """Merkle Tree over document text hashes.

    Each leaf corresponds to a document's ``text_hash``.
    Internal nodes are ``SHA-256(left_child || right_child)``.

    The tree is built bottom-up.  If the number of leaves is odd,
    the last leaf is duplicated to make pairs.

    Usage::

        tree = MerkleTree()
        tree.build(["hash_a", "hash_b", "hash_c"])
        root = tree.root_hash
        proof = tree.get_proof(0)  # proof for hash_a
        assert MerkleTree.verify_proof(proof)
    """

    def __init__(self) -> None:
        self._leaves: list[str] = []
        self._levels: list[list[str]] = []  # levels[0] = leaves, levels[-1] = [root]
        self._root_hash: str = ""
        self._built_at: float = 0.0

    # ── Properties ──────────────────────────────────────────────────

    @property
    def root_hash(self) -> str:
        """Root hash of the tree. Empty string if not built."""
        return self._root_hash

    @property
    def leaf_count(self) -> int:
        return len(self._leaves)

    @property
    def built_at(self) -> float:
        return self._built_at

    @property
    def height(self) -> int:
        """Number of levels (0 if empty)."""
        return len(self._levels)

    # ── Build ───────────────────────────────────────────────────────

    def build(self, document_hashes: list[str]) -> str:
        """Build the Merkle tree from document content hashes.

        Args:
            document_hashes: List of hex SHA-256 hashes (one per document).

        Returns:
            Root hash of the tree.

        Raises:
            ValueError: If the hash list is empty.
        """
        if not document_hashes:
            raise ValueError("Cannot build Merkle tree from empty hash list")

        self._built_at = time.time()

        # Hash leaves with prefix
        self._leaves = [_hash_leaf(h) for h in document_hashes]

        # Build levels bottom-up
        self._levels = [list(self._leaves)]

        current = list(self._leaves)
        while len(current) > 1:
            # Duplicate last element if odd
            if len(current) % 2 == 1:
                current.append(current[-1])

            next_level: list[str] = []
            for i in range(0, len(current), 2):
                next_level.append(_hash_pair(current[i], current[i + 1]))
            self._levels.append(next_level)
            current = next_level

        self._root_hash = current[0]

        logger.info(
            "merkle_tree_built",
            leaves=len(document_hashes),
            height=self.height,
            root=self._root_hash[:16],
        )
        return self._root_hash

    # ── Proof generation ────────────────────────────────────────────

    def get_proof(self, leaf_index: int) -> MerkleProof:
        """Generate a membership proof for a document at *leaf_index*.

        Args:
            leaf_index: 0-based index into the original hash list.

        Returns:
            :class:`MerkleProof` with the sibling path.

        Raises:
            IndexError: If *leaf_index* is out of range.
            RuntimeError: If the tree has not been built.
        """
        if not self._levels:
            raise RuntimeError("Merkle tree not built yet")
        if leaf_index < 0 or leaf_index >= len(self._leaves):
            raise IndexError(
                f"leaf_index {leaf_index} out of range [0, {len(self._leaves)})"
            )

        proof_path: list[tuple[str, str]] = []
        idx = leaf_index

        for level in self._levels[:-1]:  # skip root level
            # Determine sibling
            if idx % 2 == 0:
                # Current is left child → sibling is right
                sibling_idx = idx + 1
                if sibling_idx < len(level):
                    proof_path.append((level[sibling_idx], ProofSide.RIGHT))
                else:
                    proof_path.append((level[idx], ProofSide.RIGHT))  # duplicated
            else:
                # Current is right child → sibling is left
                proof_path.append((level[idx - 1], ProofSide.LEFT))

            idx //= 2

        return MerkleProof(
            doc_hash=self._leaves[leaf_index],
            proof_path=tuple(proof_path),
            root_hash=self._root_hash,
            leaf_index=leaf_index,
        )

    # ── Proof verification (static) ────────────────────────────────

    @staticmethod
    def verify_proof(proof: MerkleProof) -> bool:
        """Verify a Merkle membership proof.

        Recomputes the root hash from the leaf and proof path,
        then checks if it matches the claimed root.

        Args:
            proof: :class:`MerkleProof` to verify.

        Returns:
            ``True`` if the proof is valid.
        """
        current = proof.doc_hash

        for sibling_hash, side in proof.proof_path:
            if side == ProofSide.LEFT:
                current = _hash_pair(sibling_hash, current)
            else:
                current = _hash_pair(current, sibling_hash)

        return current == proof.root_hash

    @staticmethod
    def verify_document(
        document_hash: str,
        proof: MerkleProof,
    ) -> bool:
        """Verify that a specific document hash is in the tree.

        First checks that the leaf matches the document hash,
        then verifies the proof path.

        Args:
            document_hash: Raw document text_hash (before leaf hashing).
            proof: Merkle proof for this document.

        Returns:
            ``True`` if the document is verifiably in the tree.
        """
        expected_leaf = _hash_leaf(document_hash)
        if expected_leaf != proof.doc_hash:
            return False
        return MerkleTree.verify_proof(proof)

    # ── Serialization ───────────────────────────────────────────────

    def create_root_record(
        self,
        peer_id: str,
        key_pair: KeyPairLike | None = None,
    ) -> MerkleRoot:
        """Create a :class:`MerkleRoot` record for DHT publication.

        Args:
            peer_id: This node's peer ID.
            key_pair: Optional KeyPair for signing.

        Returns:
            :class:`MerkleRoot` (unsigned if no key_pair).
        """
        signature = b""
        if key_pair is not None:
            payload = (
                f"{self._root_hash}|{self.leaf_count}|{self._built_at}|{peer_id}"
            ).encode()
            signature = key_pair.sign(payload)  # type: ignore[union-attr]

        return MerkleRoot(
            root_hash=self._root_hash,
            document_count=self.leaf_count,
            built_at=self._built_at,
            peer_id=peer_id,
            signature=signature,
        )


def serialize_merkle_root(root: MerkleRoot) -> dict:
    """Serialize a MerkleRoot to a dict for msgpack/JSON."""
    return {
        "root_hash": root.root_hash,
        "document_count": root.document_count,
        "built_at": root.built_at,
        "peer_id": root.peer_id,
        "signature": root.signature.hex(),
    }


def deserialize_merkle_root(data: dict) -> MerkleRoot:
    """Deserialize a MerkleRoot from a dict."""
    return MerkleRoot(
        root_hash=data["root_hash"],
        document_count=data["document_count"],
        built_at=data["built_at"],
        peer_id=data["peer_id"],
        signature=bytes.fromhex(data.get("signature", "")),
    )


def serialize_proof(proof: MerkleProof) -> dict:
    """Serialize a MerkleProof to a dict."""
    return {
        "doc_hash": proof.doc_hash,
        "proof_path": [(h, s) for h, s in proof.proof_path],
        "root_hash": proof.root_hash,
        "leaf_index": proof.leaf_index,
    }


def deserialize_proof(data: dict) -> MerkleProof:
    """Deserialize a MerkleProof from a dict."""
    return MerkleProof(
        doc_hash=data["doc_hash"],
        proof_path=tuple((h, s) for h, s in data["proof_path"]),
        root_hash=data["root_hash"],
        leaf_index=data["leaf_index"],
    )
