"""P2P message protocol definitions.

All inter-node messages are serialized with msgpack. Each message has a
``type`` field identifying the operation and a ``payload`` carrying the
request/response data.

Protocol IDs (for libp2p stream multiplexing):
  /infomesh/search/1.0.0       — distributed search queries
  /infomesh/index/1.0.0        — inverted-index publish/query
  /infomesh/crawl/1.0.0        — crawl coordination (assign, lock)
  /infomesh/replicate/1.0.0    — document replication
  /infomesh/ping/1.0.0         — health check / keep-alive
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import IntEnum
from typing import Any

import msgpack

from infomesh.hashing import content_hash

# ─── Protocol IDs ──────────────────────────────────────────

PROTOCOL_SEARCH = "/infomesh/search/1.0.0"
PROTOCOL_INDEX = "/infomesh/index/1.0.0"
PROTOCOL_CRAWL = "/infomesh/crawl/1.0.0"
PROTOCOL_REPLICATE = "/infomesh/replicate/1.0.0"
PROTOCOL_PING = "/infomesh/ping/1.0.0"
PROTOCOL_CREDIT = "/infomesh/credit/1.0.0"
PROTOCOL_CREDIT_SYNC = "/infomesh/credit-sync/1.0.0"
PROTOCOL_INDEX_SUBMIT = "/infomesh/index-submit/1.0.0"
PROTOCOL_PEX = "/infomesh/pex/1.0.0"

# ─── Message Types ─────────────────────────────────────────


class MessageType(IntEnum):
    """Wire-format message type identifiers (compact for msgpack)."""

    # Ping / Pong
    PING = 0
    PONG = 1

    # Search
    SEARCH_REQUEST = 10
    SEARCH_RESPONSE = 11

    # Inverted-Index
    INDEX_PUBLISH = 20
    INDEX_PUBLISH_ACK = 21
    INDEX_QUERY = 22
    INDEX_QUERY_RESPONSE = 23

    # Crawl coordination
    CRAWL_ASSIGN = 30
    CRAWL_ASSIGN_ACK = 31
    CRAWL_LOCK = 32
    CRAWL_LOCK_ACK = 33
    CRAWL_UNLOCK = 34

    # Replication
    REPLICATE_REQUEST = 40
    REPLICATE_RESPONSE = 41

    # Attestation
    ATTESTATION_PUBLISH = 50
    ATTESTATION_PUBLISH_ACK = 51

    # Key rotation / revocation
    KEY_REVOCATION = 60
    KEY_REVOCATION_ACK = 61

    # Credit verification
    CREDIT_PROOF_REQUEST = 70
    CREDIT_PROOF_RESPONSE = 71

    # Index submit (DMZ crawler → private indexer)
    INDEX_SUBMIT = 80
    INDEX_SUBMIT_ACK = 81

    # Peer Exchange (PEX)
    PEX_REQUEST = 90
    PEX_RESPONSE = 91

    # Credit sync (cross-node credit aggregation)
    CREDIT_SYNC_ANNOUNCE = 72
    CREDIT_SYNC_EXCHANGE = 73

    # Signed envelope (wraps any message for authentication)
    SIGNED_ENVELOPE = 100

    # Error
    ERROR = 99


# ─── Dataclass messages ───────────────────────────────────


@dataclass(frozen=True)
class PeerPointer:
    """A pointer to a document hosted by a peer.

    Stored in the DHT inverted index as:
        hash(keyword) → list[PeerPointer]
    """

    peer_id: str
    doc_id: int
    url: str
    score: float
    title: str = ""


@dataclass(frozen=True)
class SearchRequest:
    """Distributed search query sent to peer nodes."""

    query: str
    keywords: list[str]
    limit: int = 10
    request_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class SearchResult:
    """A single search result from a peer."""

    url: str
    title: str
    snippet: str
    score: float
    peer_id: str = ""
    doc_id: int = 0


@dataclass(frozen=True)
class SearchResponse:
    """Response to a search query from a peer."""

    request_id: str
    results: list[dict[str, Any]]  # list of SearchResult as dicts
    peer_id: str = ""
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class IndexPublish:
    """Publish keyword → peer pointer mapping to DHT."""

    keyword: str
    pointers: list[dict[str, Any]]  # list of PeerPointer as dicts
    peer_id: str = ""
    timestamp: float = field(default_factory=time.time)
    signature: bytes = b""


@dataclass(frozen=True)
class CrawlLock:
    """Acquire a crawl lock for a URL on the DHT.

    Prevents multiple nodes from crawling the same URL simultaneously.
    Lock expires after ``ttl_seconds`` (default 300 = 5 minutes).
    """

    url: str
    url_hash: str = ""
    peer_id: str = ""
    timestamp: float = field(default_factory=time.time)
    ttl_seconds: int = 300


@dataclass(frozen=True)
class CrawlAssignment:
    """URL assigned to a node for crawling (via DHT hash proximity)."""

    url: str
    depth: int = 0
    priority: float = 1.0
    assigner_peer_id: str = ""


@dataclass(frozen=True)
class ReplicateRequest:
    """Request to replicate a document to this node."""

    doc_id: int
    url: str
    title: str
    text: str
    text_hash: str
    language: str = ""
    source_peer_id: str = ""
    replica_index: int = 0  # 0, 1, 2 for N=3


@dataclass(frozen=True)
class Attestation:
    """Content attestation record — proves a peer crawled and hashed content."""

    url: str
    raw_hash: str  # SHA-256 of raw HTTP response
    text_hash: str  # SHA-256 of extracted text
    peer_id: str
    timestamp: float = field(default_factory=time.time)
    signature: bytes = b""


@dataclass(frozen=True)
class CreditProofRequest:
    """Request credit proof from a peer for P2P verification."""

    requester_peer_id: str
    request_id: str = ""
    sample_size: int = 10
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CreditProofResponse:
    """Signed credit proof sent in response to a verification request.

    Contains a signed Merkle root over all credit entries,
    plus a random sample of signed entries with their Merkle proofs
    for spot-check verification.
    """

    peer_id: str
    request_id: str
    total_earned: float
    total_spent: float
    action_breakdown: dict[str, Any]  # action_type -> total_credits
    entry_count: int
    merkle_root: str
    root_signature: str  # hex-encoded Ed25519 signature
    sample_entries: list[dict[str, Any]]  # list of SignedCreditEntry as dicts
    sample_proofs: list[dict[str, Any]]  # list of MerkleProof as dicts
    timestamp: float = field(default_factory=time.time)
    public_key: str = ""  # hex-encoded 32-byte raw public key


@dataclass(frozen=True)
class CreditSyncAnnounce:
    """Announce owner identity hash to a newly-connected peer.

    If the remote peer has the same ``owner_email_hash``, they
    will respond with their own announce and initiate a credit
    summary exchange.
    """

    peer_id: str
    owner_email_hash: str  # SHA-256 of normalized email
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True)
class CreditSyncExchange:
    """Exchange credit summary with a same-owner peer.

    Contains a signed snapshot of local credit stats so the
    remote node can compute aggregated totals.
    """

    peer_id: str
    owner_email_hash: str
    total_earned: float
    total_spent: float
    contribution_score: float
    entry_count: int
    tier: str
    timestamp: float = field(default_factory=time.time)
    signature: str = ""


@dataclass(frozen=True)
class KeyRevocationRecord:
    """Signed record announcing that an old key has been revoked.

    Published to the DHT so peers stop trusting the old key.
    The record must be signed by **both** the old key (proving ownership)
    and the new key (binding the successor identity).
    """

    old_peer_id: str  # peer_id derived from old public key
    new_peer_id: str  # peer_id derived from new public key
    old_public_key: bytes  # raw 32-byte old public key
    new_public_key: bytes  # raw 32-byte new public key
    reason: str = "rotation"  # "rotation" | "compromise"
    timestamp: float = field(default_factory=time.time)
    old_key_signature: bytes = b""  # signed by old private key
    new_key_signature: bytes = b""  # signed by new private key


@dataclass(frozen=True)
class IndexSubmit:
    """Submit a crawled page from a DMZ crawler to a private indexer.

    Used in enterprise split deployments where crawlers run in the DMZ
    and index/search nodes run on the private network. The crawler
    sends crawled content over the authenticated P2P channel.
    """

    url: str
    title: str
    text: str
    raw_html_hash: str  # SHA-256 of raw HTML
    text_hash: str  # SHA-256 of extracted text
    language: str = ""
    crawled_at: float = field(default_factory=time.time)
    peer_id: str = ""  # crawler's peer ID
    signature: bytes = b""  # Ed25519 signature
    discovered_links: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IndexSubmitAck:
    """Acknowledgement from indexer that a submitted page was indexed."""

    url: str
    doc_id: int = 0  # 0 = duplicate / not indexed
    success: bool = True
    error: str = ""
    peer_id: str = ""  # indexer's peer ID


# ─── Serialization ─────────────────────────────────────────

# Maximum message size (10 MB)
MAX_MESSAGE_SIZE = 10 * 1024 * 1024

# Length-prefix format: 4 bytes big-endian
_LENGTH_PREFIX_BYTES = 4


def encode_message(msg_type: MessageType, payload: dict[str, Any]) -> bytes:
    """Encode a message as length-prefixed msgpack.

    Wire format: [4-byte length][msgpack({type: int, payload: dict})]

    Args:
        msg_type: Message type identifier.
        payload: Message payload as a dict.

    Returns:
        Length-prefixed msgpack bytes.
    """
    raw = msgpack.packb({"type": int(msg_type), "payload": payload}, use_bin_type=True)
    if len(raw) > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {len(raw)} > {MAX_MESSAGE_SIZE}")
    length = len(raw).to_bytes(_LENGTH_PREFIX_BYTES, byteorder="big")
    return bytes(length + raw)


# Safe msgpack deserialization limits for untrusted data.
_SAFE_UNPACK: dict[str, int] = {
    "max_map_len": 2**16,
    "max_array_len": 2**16,
    "max_str_len": 2**20,
    "max_bin_len": 2**20,
}


def safe_unpackb(data: bytes) -> Any:
    """Deserialize msgpack with size limits to prevent OOM attacks."""
    return msgpack.unpackb(data, raw=False, **_SAFE_UNPACK)


def decode_message(data: bytes) -> tuple[MessageType, dict[str, Any]]:
    """Decode a length-prefixed msgpack message.

    Args:
        data: Raw bytes (with or without length prefix).

    Returns:
        Tuple of (MessageType, payload dict).

    Raises:
        ValueError: If message is malformed or exceeds size limit.
    """
    if len(data) < _LENGTH_PREFIX_BYTES:
        raise ValueError(f"Message too short: {len(data)} bytes")

    # Reject oversized messages before any deserialization
    if len(data) > MAX_MESSAGE_SIZE + _LENGTH_PREFIX_BYTES:
        raise ValueError(f"Message exceeds max size: {len(data)} bytes")

    # Check if data starts with a valid length prefix
    length = int.from_bytes(data[:_LENGTH_PREFIX_BYTES], byteorder="big")

    if length <= 0 or length > MAX_MESSAGE_SIZE:
        # Maybe it's raw msgpack without length prefix
        unpacked = safe_unpackb(data)
    else:
        raw = data[_LENGTH_PREFIX_BYTES : _LENGTH_PREFIX_BYTES + length]
        unpacked = safe_unpackb(raw)

    if "type" not in unpacked or "payload" not in unpacked:
        raise ValueError("Message missing 'type' or 'payload' field")

    return MessageType(unpacked["type"]), unpacked["payload"]


def dataclass_to_payload(obj: object) -> dict[str, Any]:
    """Convert a dataclass instance to a msgpack-compatible dict.

    Handles bytes fields by keeping them as bytes (msgpack supports bin).
    """
    return asdict(obj)  # type: ignore[call-overload, no-any-return]


def url_to_dht_key(url: str) -> str:
    """Hash a URL to a DHT key for crawl ownership.

    Returns:
        DHT key string: /infomesh/url/<sha256_hex>
    """
    h = content_hash(url)
    return f"/infomesh/url/{h}"


def keyword_to_dht_key(keyword: str) -> str:
    """Hash a keyword to a DHT key for inverted-index lookup.

    Returns:
        DHT key string: /infomesh/kw/<sha256_hex>
    """
    h = content_hash(keyword.lower())
    return f"/infomesh/kw/{h}"


# ─── Signed envelope wire helpers ──────────────────────────────────


def encode_signed_envelope(envelope_dict: dict[str, Any]) -> bytes:
    """Encode a :class:`SignedEnvelope` (as dict) into wire format.

    The signed envelope wraps the inner message for authentication.
    """
    return encode_message(MessageType.SIGNED_ENVELOPE, envelope_dict)


def decode_signed_envelope(
    data: bytes,
) -> dict[str, Any] | None:
    """Decode wire bytes and return the envelope dict if it is a
    ``SIGNED_ENVELOPE``, otherwise ``None``.
    """
    msg_type, payload = decode_message(data)
    if msg_type == MessageType.SIGNED_ENVELOPE:
        return payload
    return None
