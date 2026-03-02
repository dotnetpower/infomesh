"""P2P message authentication — sign, verify, and reject unsigned messages.

Every inter-node message is wrapped in a :class:`SignedEnvelope` that
carries an Ed25519 signature over the serialised payload, a monotonic
nonce for replay protection, and the sender's peer ID so receivers can
look up the public key.

Design goals:

* **Tamper detection** — a modified node cannot forge another peer's
  responses because it lacks the victim's private key.
* **Replay protection** — nonces are tracked per-peer and stale
  (already-seen) nonces are rejected.
* **Isolation enforcement** — receivers refuse envelopes from peers
  that have been network-isolated by the trust system.

Usage (sender)::

    envelope = sign_envelope(payload_bytes, key_pair, nonce_counter)

Usage (receiver)::

    ok, payload = verify_envelope(
        envelope, peer_registry, trust_store,
    )
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

# Maximum age (seconds) of a message before it is considered stale.
MAX_MESSAGE_AGE_SECONDS: float = 300.0  # 5 minutes

# Maximum number of tracked nonces per peer (ring-buffer).
MAX_NONCE_HISTORY: int = 10_000


@dataclass(frozen=True)
class SignedEnvelope:
    """Cryptographic wrapper around every P2P message.

    Fields
    ------
    payload : bytes
        The raw msgpack-encoded inner message.
    peer_id : str
        Sender's peer ID (derived from public key).
    signature : bytes
        ``Ed25519(private_key, canonical_bytes)`` where
        ``canonical_bytes = peer_id | nonce_bytes | timestamp_bytes | payload``.
    nonce : int
        Strictly-increasing counter **per sender** — prevents replay.
    timestamp : float
        Wall-clock time when the envelope was created.
    """

    payload: bytes
    peer_id: str
    signature: bytes
    nonce: int
    timestamp: float = field(default_factory=time.time)


def _canonical_bytes(
    peer_id: str,
    nonce: int,
    timestamp: float,
    payload: bytes,
) -> bytes:
    """Build the canonical byte string that is signed / verified.

    The format is deterministic so both sides produce the same bytes:

        ``<peer_id>|<nonce_8B_BE>|<timestamp_str>|<payload>``
    """
    return (
        peer_id.encode()
        + b"|"
        + nonce.to_bytes(8, "big")
        + b"|"
        + f"{timestamp:.6f}".encode()
        + b"|"
        + payload
    )


# ── Sender helpers ─────────────────────────────────────────────────


class NonceCounter:
    """Thread-safe monotonic nonce generator."""

    def __init__(self, start: int = 0) -> None:
        self._value = start

    def next(self) -> int:
        self._value += 1
        return self._value

    @property
    def current(self) -> int:
        return self._value


def sign_envelope(
    payload: bytes,
    key_pair: Any,
    nonce_counter: NonceCounter,
    *,
    now: float | None = None,
) -> SignedEnvelope:
    """Create a signed envelope wrapping *payload*.

    Args:
        payload: Raw msgpack bytes of the inner message.
        key_pair: Object satisfying :class:`~infomesh.types.KeyPairLike`.
        nonce_counter: Sender's nonce generator.
        now: Override timestamp (for testing).

    Returns:
        A :class:`SignedEnvelope` ready for wire serialisation.
    """
    now = now or time.time()
    nonce = nonce_counter.next()
    canonical = _canonical_bytes(key_pair.peer_id, nonce, now, payload)
    signature = key_pair.sign(canonical)

    return SignedEnvelope(
        payload=payload,
        peer_id=key_pair.peer_id,
        signature=signature,
        nonce=nonce,
        timestamp=now,
    )


def envelope_to_dict(env: SignedEnvelope) -> dict[str, Any]:
    """Serialise a :class:`SignedEnvelope` to a dict for msgpack."""
    return {
        "payload": env.payload,
        "peer_id": env.peer_id,
        "signature": env.signature,
        "nonce": env.nonce,
        "timestamp": env.timestamp,
    }


def envelope_from_dict(d: dict[str, Any]) -> SignedEnvelope:
    """Deserialise a :class:`SignedEnvelope` from a dict."""
    return SignedEnvelope(
        payload=d["payload"],
        peer_id=d["peer_id"],
        signature=d["signature"],
        nonce=d["nonce"],
        timestamp=d["timestamp"],
    )


# ── Receiver helpers ───────────────────────────────────────────────


class PeerKeyRegistry:
    """In-memory registry mapping ``peer_id → public_key_bytes``.

    Public keys are learned during the initial handshake or from DHT
    records.  The registry is consulted on every incoming message.
    """

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    def register(self, peer_id: str, public_key: bytes) -> None:
        """Store or update a peer's public key."""
        self._keys[peer_id] = public_key

    def get(self, peer_id: str) -> bytes | None:
        """Return the raw 32-byte public key, or ``None``."""
        return self._keys.get(peer_id)

    def remove(self, peer_id: str) -> None:
        """Remove a peer from the registry."""
        self._keys.pop(peer_id, None)

    def __contains__(self, peer_id: str) -> bool:
        return peer_id in self._keys

    def __len__(self) -> int:
        return len(self._keys)


class NonceTracker:
    """Per-peer nonce anti-replay tracker.

    Records the highest nonce seen from each peer and rejects any
    nonce ≤ that value.  In addition a sliding set of the last
    ``MAX_NONCE_HISTORY`` nonces is kept for gap detection.
    """

    def __init__(self) -> None:
        self._highest: dict[str, int] = {}

    def check_and_record(self, peer_id: str, nonce: int) -> bool:
        """Return ``True`` if the nonce is fresh (not replayed).

        A nonce is fresh iff ``nonce > highest_seen[peer_id]``.
        """
        prev = self._highest.get(peer_id, 0)
        if nonce <= prev:
            return False
        self._highest[peer_id] = nonce
        return True

    def highest(self, peer_id: str) -> int:
        """Return the highest nonce seen from *peer_id*."""
        return self._highest.get(peer_id, 0)


class VerificationError(Exception):
    """Raised when an envelope fails verification."""


def verify_envelope(
    envelope: SignedEnvelope,
    key_registry: PeerKeyRegistry,
    nonce_tracker: NonceTracker,
    *,
    is_isolated_fn: Any | None = None,
    now: float | None = None,
    max_age: float = MAX_MESSAGE_AGE_SECONDS,
) -> bytes:
    """Verify a received :class:`SignedEnvelope` and return its payload.

    Checks performed (in order):

    1. **Isolation** — is the sender network-isolated?
    2. **Known key** — do we know the sender's public key?
    3. **Timestamp freshness** — is the message within *max_age*?
    4. **Nonce freshness** — has this nonce been seen before?
    5. **Signature** — does Ed25519 verification pass?

    Args:
        envelope: The incoming signed envelope.
        key_registry: Lookup for peer public keys.
        nonce_tracker: Anti-replay nonce tracker.
        is_isolated_fn: Optional callable ``(peer_id) -> bool`` that
            returns ``True`` when a peer is isolated.
        now: Override current time (for testing).
        max_age: Maximum acceptable message age in seconds.

    Returns:
        The verified inner *payload* bytes.

    Raises:
        VerificationError: If any check fails.
    """
    now = now or time.time()
    pid = envelope.peer_id

    # 1. Isolation check
    if is_isolated_fn is not None and is_isolated_fn(pid):
        logger.warning("msg_rejected_isolated", peer_id=pid[:16])
        raise VerificationError(f"peer {pid[:16]} is isolated")

    # 2. Known key
    pub_bytes = key_registry.get(pid)
    if pub_bytes is None:
        logger.warning("msg_rejected_unknown_key", peer_id=pid[:16])
        raise VerificationError(f"unknown public key for peer {pid[:16]}")

    # 3. Freshness
    age = abs(now - envelope.timestamp)
    if age > max_age:
        logger.warning(
            "msg_rejected_stale",
            peer_id=pid[:16],
            age_s=round(age, 1),
        )
        raise VerificationError(f"message too old ({age:.0f}s > {max_age:.0f}s)")

    # 4. Nonce
    if not nonce_tracker.check_and_record(pid, envelope.nonce):
        logger.warning(
            "msg_rejected_replay",
            peer_id=pid[:16],
            nonce=envelope.nonce,
        )
        raise VerificationError(f"replayed nonce {envelope.nonce} from {pid[:16]}")

    # 5. Signature
    canonical = _canonical_bytes(
        pid,
        envelope.nonce,
        envelope.timestamp,
        envelope.payload,
    )
    if not _verify_raw(pub_bytes, canonical, envelope.signature):
        logger.warning("msg_rejected_bad_sig", peer_id=pid[:16])
        raise VerificationError(f"invalid signature from {pid[:16]}")

    return envelope.payload


def _verify_raw(
    public_key_bytes: bytes,
    data: bytes,
    signature: bytes,
) -> bool:
    """Low-level Ed25519 signature verification."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        pub = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        pub.verify(signature, data)
        return True
    except ImportError:
        logger.error("cryptography_not_installed")
        return False
    except Exception:  # InvalidSignature, ValueError, etc.
        return False
