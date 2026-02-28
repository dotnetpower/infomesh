"""Content attestation chain for crawled documents.

On crawl, compute:
    SHA-256(raw_response) + SHA-256(extracted_text)
    → sign with peer private key
    → publish to DHT

This creates a tamper-evident chain: anyone can re-crawl the URL
and verify the hash matches the original attestation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from infomesh.hashing import content_hash
from infomesh.types import KeyPairLike

logger = structlog.get_logger()


@dataclass(frozen=True)
class ContentAttestation:
    """Signed attestation of crawled content integrity."""

    url: str
    raw_hash: str  # SHA-256 of raw HTTP response body
    text_hash: str  # SHA-256 of extracted text
    peer_id: str  # Peer that crawled the content
    signature: bytes  # Ed25519 signature over the attestation payload
    crawled_at: float  # When the content was crawled
    content_length: int  # Byte length of extracted text


@dataclass(frozen=True)
class VerificationResult:
    """Result of verifying an attestation against content."""

    url: str
    raw_match: bool
    text_match: bool
    signature_valid: bool
    verified: bool  # All checks passed
    detail: str


def _attestation_payload(
    url: str, raw_hash: str, text_hash: str, crawled_at: float
) -> bytes:
    """Canonical byte payload for signing/verifying.

    Format: ``url|raw_hash|text_hash|crawled_at``
    """
    return f"{url}|{raw_hash}|{text_hash}|{crawled_at}".encode()


def create_attestation(
    url: str,
    raw_body: bytes,
    extracted_text: str,
    key_pair: KeyPairLike,
    *,
    crawled_at: float | None = None,
) -> ContentAttestation:
    """Create a signed content attestation.

    Args:
        url: The crawled URL.
        raw_body: Raw HTTP response body bytes.
        extracted_text: Extracted text content.
        key_pair: KeyPair for signing and peer identity.
        crawled_at: Override crawl timestamp.

    Returns:
        Signed ContentAttestation.
    """
    raw_h = content_hash(raw_body)
    text_h = content_hash(extracted_text)
    ts = crawled_at or time.time()

    payload = _attestation_payload(url, raw_h, text_h, ts)
    signature = key_pair.sign(payload)

    att = ContentAttestation(
        url=url,
        raw_hash=raw_h,
        text_hash=text_h,
        peer_id=key_pair.peer_id,
        signature=signature,
        crawled_at=ts,
        content_length=len(extracted_text.encode("utf-8")),
    )

    logger.info(
        "attestation_created",
        url=url,
        raw_hash=raw_h[:16],
        text_hash=text_h[:16],
        peer_id=att.peer_id[:12],
    )
    return att


def verify_attestation(
    attestation: ContentAttestation,
    key_pair: KeyPairLike,
    *,
    raw_body: bytes | None = None,
    extracted_text: str | None = None,
) -> VerificationResult:
    """Verify a content attestation.

    Checks:
    1. Signature validity (always checked).
    2. Raw hash match (if raw_body provided).
    3. Text hash match (if extracted_text provided).

    Args:
        attestation: The attestation to verify.
        key_pair: KeyPair with public key of the attesting peer.
        raw_body: Optional raw HTTP body to check against.
        extracted_text: Optional extracted text to check against.

    Returns:
        VerificationResult with detailed check results.
    """
    payload = _attestation_payload(
        attestation.url,
        attestation.raw_hash,
        attestation.text_hash,
        attestation.crawled_at,
    )
    sig_valid = key_pair.verify(payload, attestation.signature)

    raw_match = True
    if raw_body is not None:
        raw_match = content_hash(raw_body) == attestation.raw_hash

    text_match = True
    if extracted_text is not None:
        text_match = content_hash(extracted_text) == attestation.text_hash

    verified = sig_valid and raw_match and text_match

    details = []
    if not sig_valid:
        details.append("signature_invalid")
    if not raw_match:
        details.append("raw_hash_mismatch")
    if not text_match:
        details.append("text_hash_mismatch")

    result = VerificationResult(
        url=attestation.url,
        raw_match=raw_match,
        text_match=text_match,
        signature_valid=sig_valid,
        verified=verified,
        detail="; ".join(details) if details else "ok",
    )

    logger.info(
        "attestation_verified",
        url=attestation.url,
        verified=verified,
        detail=result.detail,
    )
    return result


def serialize_attestation(att: ContentAttestation) -> dict:
    """Serialize an attestation to a dict suitable for msgpack/JSON.

    Args:
        att: ContentAttestation to serialize.

    Returns:
        Dictionary with all fields (signature as hex).
    """
    return {
        "url": att.url,
        "raw_hash": att.raw_hash,
        "text_hash": att.text_hash,
        "peer_id": att.peer_id,
        "signature": att.signature.hex(),
        "crawled_at": att.crawled_at,
        "content_length": att.content_length,
    }


def deserialize_attestation(data: dict) -> ContentAttestation:
    """Deserialize an attestation from a dict.

    Args:
        data: Dictionary (e.g. from msgpack/JSON).

    Returns:
        ContentAttestation instance.
    """
    return ContentAttestation(
        url=data["url"],
        raw_hash=data["raw_hash"],
        text_hash=data["text_hash"],
        peer_id=data["peer_id"],
        signature=bytes.fromhex(data["signature"]),
        crawled_at=data["crawled_at"],
        content_length=data["content_length"],
    )


# ── Merkle root attestation (Layer 3) ─────────────────────────────


def _merkle_root_payload(
    root_hash: str, doc_count: int, built_at: float, peer_id: str
) -> bytes:
    """Canonical payload for Merkle root signing/verification."""
    return f"{root_hash}|{doc_count}|{built_at}|{peer_id}".encode()


def verify_merkle_root(
    root: object,  # MerkleRoot from infomesh.trust.merkle
    key_pair: KeyPairLike,
) -> bool:
    """Verify the digital signature on a :class:`MerkleRoot`.

    Args:
        root: :class:`~infomesh.trust.merkle.MerkleRoot` to verify.
        key_pair: KeyPair whose public key should match the signer.

    Returns:
        ``True`` if the signature is valid.
    """
    payload = _merkle_root_payload(
        root.root_hash,  # type: ignore[attr-defined]
        root.document_count,  # type: ignore[attr-defined]
        root.built_at,  # type: ignore[attr-defined]
        root.peer_id,  # type: ignore[attr-defined]
    )
    try:
        valid: bool = key_pair.verify(payload, root.signature)  # type: ignore[attr-defined]
    except Exception:
        logger.warning(
            "merkle_root_verify_failed",
            root_hash=root.root_hash[:16],  # type: ignore[attr-defined]
        )
        return False

    logger.info(
        "merkle_root_verified",
        root_hash=root.root_hash[:16],  # type: ignore[attr-defined]
        peer_id=root.peer_id[:12],  # type: ignore[attr-defined]
        valid=valid,
    )
    return valid
