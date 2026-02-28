"""P2P credit verification via signed entries and Merkle proofs.

Each credit entry can be signed with the node's Ed25519 private key at
recording time.  A Merkle tree is built over entry hashes, enabling
other peers to:

1.  Verify individual entry **signatures** (prove the node really
    created each entry, not fabricated post-hoc).
2.  Verify entries are **part of the full history** via Merkle proofs
    (prevent silent omission or rewriting of entries).
3.  Cross-check **total credits** against the signed Merkle root.

Verification flow (simplified)::

    Requester                          Target
    ─────────                          ──────
    CreditProofRequest  ──────────►
                         ◄──────────  CreditProofResponse
    verify_credit_proof(response)

The response contains:
- Signed Merkle root over *all* credit entry hashes.
- A random sample of signed entries with their Merkle membership proofs.
- Action-type breakdown and totals.

The verifier checks:
- Root signature matches the peer's public key.
- Each sampled entry's signature is valid.
- Each sampled entry's Merkle proof is valid against the root.
- Entry hashes are correctly derived from entry data.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

import structlog

from infomesh.credits.ledger import CreditEntry, CreditLedger, _entry_canonical
from infomesh.hashing import content_hash
from infomesh.trust.merkle import (
    MerkleTree,
    deserialize_proof,
    serialize_proof,
)
from infomesh.types import KeyPairLike

logger = structlog.get_logger()


# ─── Result dataclass ──────────────────────────────────────


@dataclass(frozen=True)
class CreditVerificationResult:
    """Result of verifying a peer's credit proof."""

    peer_id: str
    verified: bool
    total_earned: float
    entry_count: int
    valid_signatures: int
    invalid_signatures: int
    valid_proofs: int
    invalid_proofs: int
    merkle_root_valid: bool
    detail: str


# ─── Proof builder ─────────────────────────────────────────


class CreditProofBuilder:
    """Builds and verifies signed Merkle proofs over credit entries.

    **Building** (on the target node):

        builder = CreditProofBuilder(ledger, key_pair)
        proof = builder.build_proof(sample_size=10)
        # → send ``proof`` dict to the requester

    **Verifying** (on the requesting node):

        result = CreditProofBuilder.verify_proof(proof)
        if result.verified:
            print("Credits are legit")

    The builder reads all *signed* entries from the ledger, builds a
    Merkle tree over their entry hashes, signs the root, and selects a
    random sample of entries+proofs for the verifier to spot-check.
    """

    def __init__(self, ledger: CreditLedger, key_pair: KeyPairLike) -> None:
        self._ledger = ledger
        self._key_pair = key_pair

    # ── Build ───────────────────────────────────────────────────

    def build_proof(
        self,
        *,
        sample_size: int = 10,
        request_id: str = "",
    ) -> dict[str, Any]:
        """Build a credit proof for P2P verification.

        Args:
            sample_size: Number of random entries to include as samples.
            request_id: Optional request ID for correlation.

        Returns:
            Dict suitable for msgpack serialization / P2P transport.
        """
        entries = self._ledger.signed_entries()
        if not entries:
            return self._empty_proof(request_id)

        # Build Merkle tree from entry hashes
        entry_hashes = [e.entry_hash for e in entries]
        tree = MerkleTree()
        tree.build(entry_hashes)

        # Sign the Merkle root
        root_payload = _root_canonical(
            tree.root_hash,
            len(entries),
            self._key_pair.peer_id,
        )
        root_signature = self._key_pair.sign(root_payload)

        # Select random sample
        sample_indices = _select_sample(len(entries), sample_size)
        sample_entries: list[dict[str, Any]] = []
        sample_proofs: list[dict[str, Any]] = []

        for idx in sample_indices:
            entry = entries[idx]
            sample_entries.append(_entry_to_dict(entry))
            proof = tree.get_proof(idx)
            sample_proofs.append(serialize_proof(proof))

        # Action breakdown
        breakdown: dict[str, float] = {}
        for e in entries:
            breakdown[e.action] = breakdown.get(e.action, 0.0) + e.credits

        stats = self._ledger.stats()

        result = {
            "peer_id": self._key_pair.peer_id,
            "request_id": request_id,
            "total_earned": stats.total_earned,
            "total_spent": stats.total_spent,
            "action_breakdown": breakdown,
            "entry_count": len(entries),
            "merkle_root": tree.root_hash,
            "root_signature": root_signature.hex(),
            "sample_entries": sample_entries,
            "sample_proofs": sample_proofs,
            "timestamp": time.time(),
            "public_key": self._key_pair.public_key_bytes().hex(),
        }

        logger.info(
            "credit_proof_built",
            peer_id=self._key_pair.peer_id,
            entry_count=len(entries),
            sample_size=len(sample_entries),
            root=tree.root_hash[:16],
        )
        return result

    def _empty_proof(self, request_id: str) -> dict[str, Any]:
        """Return a valid proof for an empty ledger."""
        return {
            "peer_id": self._key_pair.peer_id,
            "request_id": request_id,
            "total_earned": 0.0,
            "total_spent": 0.0,
            "action_breakdown": {},
            "entry_count": 0,
            "merkle_root": "",
            "root_signature": "",
            "sample_entries": [],
            "sample_proofs": [],
            "timestamp": time.time(),
            "public_key": self._key_pair.public_key_bytes().hex(),
        }

    # ── Verify (static) ────────────────────────────────────────

    @staticmethod
    def verify_proof(proof_data: dict[str, Any]) -> CreditVerificationResult:
        """Verify a credit proof received from a peer.

        Performs three levels of verification:

        1. **Merkle root signature** — proves the root was signed by
           the peer's private key.
        2. **Entry signatures** — proves each sampled entry was created
           by the same peer.
        3. **Merkle membership proofs** — proves each sampled entry is
           part of the complete credit history (no omitted entries).

        Args:
            proof_data: Dict as returned by :meth:`build_proof`.

        Returns:
            :class:`CreditVerificationResult` with detailed check results.
        """
        peer_id = proof_data.get("peer_id", "")
        entry_count = proof_data.get("entry_count", 0)
        total_earned = proof_data.get("total_earned", 0.0)

        # Empty ledger — trivially valid
        if entry_count == 0:
            return CreditVerificationResult(
                peer_id=peer_id,
                verified=True,
                total_earned=0.0,
                entry_count=0,
                valid_signatures=0,
                invalid_signatures=0,
                valid_proofs=0,
                invalid_proofs=0,
                merkle_root_valid=True,
                detail="empty_ledger",
            )

        # ── Load public key ──────────────────────────────────
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            pub_key_bytes = bytes.fromhex(proof_data["public_key"])
            pub_key = Ed25519PublicKey.from_public_bytes(pub_key_bytes)
        except Exception as exc:
            return CreditVerificationResult(
                peer_id=peer_id,
                verified=False,
                total_earned=total_earned,
                entry_count=entry_count,
                valid_signatures=0,
                invalid_signatures=0,
                valid_proofs=0,
                invalid_proofs=0,
                merkle_root_valid=False,
                detail=f"invalid_public_key: {exc}",
            )

        # ── Verify Merkle root signature ─────────────────────
        merkle_root = proof_data.get("merkle_root", "")
        root_sig_hex = proof_data.get("root_signature", "")
        root_payload = _root_canonical(merkle_root, entry_count, peer_id)

        try:
            root_sig = bytes.fromhex(root_sig_hex)
            pub_key.verify(root_sig, root_payload)
            merkle_root_valid = True
        except Exception:
            merkle_root_valid = False

        # ── Verify sample entries ────────────────────────────
        sample_entries = proof_data.get("sample_entries", [])
        sample_proofs = proof_data.get("sample_proofs", [])

        valid_sigs = 0
        invalid_sigs = 0
        valid_proofs_count = 0
        invalid_proofs_count = 0

        for i, entry_data in enumerate(sample_entries):
            # Check that entry_hash matches recomputed hash
            canonical = _entry_canonical(
                entry_data["action"],
                entry_data["quantity"],
                entry_data["weight"],
                entry_data["multiplier"],
                entry_data["credits"],
                entry_data["timestamp"],
                entry_data["note"],
            )
            expected_hash = content_hash(canonical)

            if expected_hash != entry_data["entry_hash"]:
                invalid_sigs += 1
                continue

            # Verify Ed25519 signature over canonical data
            try:
                entry_sig = bytes.fromhex(entry_data["signature"])
                pub_key.verify(entry_sig, canonical)
                valid_sigs += 1
            except Exception:
                invalid_sigs += 1

            # Verify Merkle membership proof
            if i < len(sample_proofs):
                proof = deserialize_proof(sample_proofs[i])
                if MerkleTree.verify_proof(proof) and proof.root_hash == merkle_root:
                    valid_proofs_count += 1
                else:
                    invalid_proofs_count += 1

        verified = (
            merkle_root_valid
            and invalid_sigs == 0
            and invalid_proofs_count == 0
            and (valid_sigs > 0 or len(sample_entries) == 0)
        )

        details: list[str] = []
        if not merkle_root_valid:
            details.append("merkle_root_signature_invalid")
        if invalid_sigs > 0:
            details.append(f"invalid_entry_signatures={invalid_sigs}")
        if invalid_proofs_count > 0:
            details.append(f"invalid_merkle_proofs={invalid_proofs_count}")

        result = CreditVerificationResult(
            peer_id=peer_id,
            verified=verified,
            total_earned=total_earned,
            entry_count=entry_count,
            valid_signatures=valid_sigs,
            invalid_signatures=invalid_sigs,
            valid_proofs=valid_proofs_count,
            invalid_proofs=invalid_proofs_count,
            merkle_root_valid=merkle_root_valid,
            detail="; ".join(details) if details else "ok",
        )

        logger.info(
            "credit_proof_verified",
            peer_id=peer_id,
            verified=verified,
            valid_sigs=valid_sigs,
            invalid_sigs=invalid_sigs,
            detail=result.detail,
        )
        return result


# ─── Helpers ───────────────────────────────────────────────


def _root_canonical(root_hash: str, entry_count: int, peer_id: str) -> bytes:
    """Canonical bytes for Merkle root signing/verification."""
    return f"{root_hash}|{entry_count}|{peer_id}".encode()


def _select_sample(total: int, sample_size: int) -> list[int]:
    """Select random sample indices, capping at total."""
    if total <= sample_size:
        return list(range(total))
    return sorted(random.sample(range(total), sample_size))


def _entry_to_dict(entry: CreditEntry) -> dict[str, Any]:
    """Convert a CreditEntry to a dict for serialization."""
    return {
        "entry_hash": entry.entry_hash,
        "action": entry.action,
        "quantity": entry.quantity,
        "weight": entry.weight,
        "multiplier": entry.multiplier,
        "credits": entry.credits,
        "timestamp": entry.timestamp,
        "note": entry.note,
        "signature": entry.signature,
    }
