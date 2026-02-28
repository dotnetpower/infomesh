"""Sybil attack defense — PoW node ID generation + subnet rate limiting.

PoW Node ID:
  Nodes must prove computational work to join the network.
  The hash of (public_key_bytes + nonce) must have N leading zero bits.
  Default difficulty: 20 bits (~30 sec on avg CPU).

Subnet Rate Limiting:
  Max K nodes per /24 subnet in any DHT routing bucket.
  Prevents Sybil attacks from a single network location.
"""

from __future__ import annotations

import hashlib
import ipaddress
import struct
import time
from collections import defaultdict
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger()

# Default: 20 leading zero bits → ~2^20 = 1M hashes → ~30s on avg CPU
DEFAULT_DIFFICULTY_BITS = 20

# Max nodes per /24 subnet per DHT routing bucket
DEFAULT_MAX_PER_SUBNET = 3


@dataclass(frozen=True)
class ProofOfWork:
    """Proof of work for node ID generation.

    Attributes:
        nonce: The nonce that satisfies the difficulty requirement.
        difficulty_bits: Number of leading zero bits required.
        hash_hex: The resulting hash (hex string).
        elapsed_seconds: Time taken to compute.
    """

    nonce: int
    difficulty_bits: int
    hash_hex: str
    elapsed_seconds: float


def _count_leading_zero_bits_fast(hash_bytes: bytes) -> int:
    """Count leading zero bits efficiently."""
    count = 0
    for byte in hash_bytes:
        if byte == 0:
            count += 8
        else:
            # Number of leading zeros = 7 - floor(log2(byte))
            count += 7 - byte.bit_length() + 1
            break
    return count


def compute_pow_hash(public_key_bytes: bytes, nonce: int) -> bytes:
    """Compute SHA-256(public_key_bytes || nonce_as_8_bytes_le).

    Args:
        public_key_bytes: Raw Ed25519 public key (32 bytes).
        nonce: Integer nonce to try.

    Returns:
        SHA-256 hash bytes (32 bytes).
    """
    return hashlib.sha256(public_key_bytes + struct.pack("<Q", nonce)).digest()


def generate_pow(
    public_key_bytes: bytes,
    difficulty_bits: int = DEFAULT_DIFFICULTY_BITS,
    *,
    max_nonce: int = 2**48,
    progress_interval: int = 1_000_000,
) -> ProofOfWork:
    """Generate a proof-of-work for a node's public key.

    Finds a nonce such that SHA-256(public_key || nonce) has at least
    `difficulty_bits` leading zero bits.

    Args:
        public_key_bytes: Raw Ed25519 public key (32 bytes).
        difficulty_bits: Required leading zero bits (default: 20).
        max_nonce: Maximum nonce to try before giving up.
        progress_interval: Log progress every N hashes.

    Returns:
        ProofOfWork with the valid nonce and metadata.

    Raises:
        RuntimeError: If max_nonce reached without finding valid hash.
    """
    start = time.monotonic()
    nonce = 0

    while nonce < max_nonce:
        hash_bytes = compute_pow_hash(public_key_bytes, nonce)
        leading_zeros = _count_leading_zero_bits_fast(hash_bytes)

        if leading_zeros >= difficulty_bits:
            elapsed = time.monotonic() - start
            hash_hex = hash_bytes.hex()
            logger.info(
                "pow_found",
                nonce=nonce,
                difficulty=difficulty_bits,
                leading_zeros=leading_zeros,
                elapsed_seconds=round(elapsed, 2),
                hash_rate=round(nonce / elapsed) if elapsed > 0 else 0,
            )
            return ProofOfWork(
                nonce=nonce,
                difficulty_bits=difficulty_bits,
                hash_hex=hash_hex,
                elapsed_seconds=elapsed,
            )

        if nonce > 0 and nonce % progress_interval == 0:
            elapsed = time.monotonic() - start
            logger.debug(
                "pow_progress",
                nonces_tried=nonce,
                elapsed_seconds=round(elapsed, 2),
                hash_rate=round(nonce / elapsed) if elapsed > 0 else 0,
            )

        nonce += 1

    msg = f"PoW failed: no valid nonce found in {max_nonce} attempts"
    raise RuntimeError(msg)


def verify_pow(
    public_key_bytes: bytes,
    nonce: int,
    difficulty_bits: int = DEFAULT_DIFFICULTY_BITS,
) -> bool:
    """Verify a proof-of-work.

    Args:
        public_key_bytes: Raw Ed25519 public key (32 bytes).
        nonce: The claimed nonce.
        difficulty_bits: Required leading zero bits.

    Returns:
        True if the PoW is valid.
    """
    hash_bytes = compute_pow_hash(public_key_bytes, nonce)
    leading_zeros = _count_leading_zero_bits_fast(hash_bytes)
    return leading_zeros >= difficulty_bits


def derive_node_id(public_key_bytes: bytes, nonce: int) -> str:
    """Derive a node ID from the PoW hash.

    The node ID is the first 40 hex chars of SHA-256(pubkey || nonce).
    This ties the identity to both the public key and the proof of work.

    Args:
        public_key_bytes: Raw public key bytes.
        nonce: The valid PoW nonce.

    Returns:
        40-character hex node ID (160 bits — matches Kademlia).
    """
    hash_bytes = compute_pow_hash(public_key_bytes, nonce)
    return hash_bytes.hex()[:40]


# ─── Subnet Rate Limiting ──────────────────────────────────


@dataclass
class SubnetLimiter:
    """Limits the number of nodes per /24 subnet in a DHT routing bucket.

    Prevents Sybil attacks from a single network location by ensuring
    geographic/network diversity in the routing table.

    Usage:
        limiter = SubnetLimiter(max_per_subnet=3)
        if limiter.can_add("192.168.1.100", bucket_id=5):
            limiter.add("192.168.1.100", "peer-id-abc", bucket_id=5)
    """

    max_per_subnet: int = DEFAULT_MAX_PER_SUBNET
    # bucket_id → subnet_str → set of peer_ids
    _buckets: dict[int, dict[str, set[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set))
    )

    def _get_subnet(self, ip: str) -> str:
        """Extract /24 subnet from an IP address.

        Args:
            ip: IPv4 or IPv6 address string.

        Returns:
            Subnet string (e.g., "192.168.1.0/24").
        """
        addr = ipaddress.ip_address(ip)
        network: ipaddress.IPv4Network | ipaddress.IPv6Network
        if isinstance(addr, ipaddress.IPv4Address):
            network = ipaddress.IPv4Network(f"{ip}/24", strict=False)
        else:
            # For IPv6, use /48 subnet
            network = ipaddress.IPv6Network(f"{ip}/48", strict=False)
        return str(network)

    def can_add(self, ip: str, bucket_id: int) -> bool:
        """Check if a node from this IP can be added to the bucket.

        Args:
            ip: IP address of the new node.
            bucket_id: DHT routing bucket index.

        Returns:
            True if the subnet limit is not reached.
        """
        subnet = self._get_subnet(ip)
        current = self._buckets[bucket_id][subnet]
        return len(current) < self.max_per_subnet

    def add(self, ip: str, peer_id: str, bucket_id: int) -> bool:
        """Register a node in the subnet limiter.

        Args:
            ip: IP address of the node.
            peer_id: Peer ID of the node.
            bucket_id: DHT routing bucket index.

        Returns:
            True if added, False if subnet limit reached.
        """
        subnet = self._get_subnet(ip)
        current = self._buckets[bucket_id][subnet]

        if len(current) >= self.max_per_subnet:
            logger.warning(
                "subnet_limit_reached",
                subnet=subnet,
                bucket_id=bucket_id,
                max_per_subnet=self.max_per_subnet,
                rejected_peer=peer_id,
            )
            return False

        current.add(peer_id)
        logger.debug(
            "peer_added_to_bucket",
            subnet=subnet,
            bucket_id=bucket_id,
            peer_id=peer_id,
            subnet_count=len(current),
        )
        return True

    def remove(self, ip: str, peer_id: str, bucket_id: int) -> None:
        """Remove a node from the subnet limiter.

        Args:
            ip: IP address of the node.
            peer_id: Peer ID of the node.
            bucket_id: DHT routing bucket index.
        """
        subnet = self._get_subnet(ip)
        self._buckets[bucket_id][subnet].discard(peer_id)
        # Clean up empty entries to prevent unbounded dict growth
        if not self._buckets[bucket_id][subnet]:
            del self._buckets[bucket_id][subnet]
        if not self._buckets[bucket_id]:
            del self._buckets[bucket_id]

    def get_subnet_counts(self, bucket_id: int) -> dict[str, int]:
        """Get current node counts per subnet in a bucket.

        Args:
            bucket_id: DHT routing bucket index.

        Returns:
            Mapping of subnet → count.
        """
        return {
            subnet: len(peers)
            for subnet, peers in self._buckets[bucket_id].items()
            if peers
        }

    def total_nodes(self) -> int:
        """Get total number of tracked nodes across all buckets."""
        return sum(
            len(peers) for bucket in self._buckets.values() for peers in bucket.values()
        )


# ─── Combined Sybil Validator ──────────────────────────────


@dataclass
class SybilValidator:
    """Combined PoW + subnet validation for new peers.

    Usage:
        validator = SybilValidator(difficulty_bits=20, max_per_subnet=3)

        # When a new peer wants to join:
        ok, reason = validator.validate_peer(
            public_key_bytes=peer_pubkey,
            pow_nonce=peer_nonce,
            ip="1.2.3.4",
            peer_id="abc123...",
            bucket_id=5,
        )
        if ok:
            # Accept peer into routing table
            ...
    """

    difficulty_bits: int = DEFAULT_DIFFICULTY_BITS
    max_per_subnet: int = DEFAULT_MAX_PER_SUBNET
    subnet_limiter: SubnetLimiter = field(init=False)

    def __post_init__(self) -> None:
        self.subnet_limiter = SubnetLimiter(max_per_subnet=self.max_per_subnet)

    def validate_peer(
        self,
        public_key_bytes: bytes,
        pow_nonce: int,
        ip: str,
        peer_id: str,
        bucket_id: int,
    ) -> tuple[bool, str]:
        """Validate a new peer for Sybil resistance.

        Checks:
        1. PoW is valid (hash has enough leading zeros)
        2. Derived node ID matches the claimed peer_id
        3. Subnet limit not exceeded

        Args:
            public_key_bytes: Peer's raw Ed25519 public key.
            pow_nonce: Peer's claimed PoW nonce.
            ip: Peer's IP address.
            peer_id: Peer's claimed node ID.
            bucket_id: Target DHT routing bucket.

        Returns:
            (True, "ok") if valid, (False, reason) if rejected.
        """
        # Check 1: PoW validity
        if not verify_pow(public_key_bytes, pow_nonce, self.difficulty_bits):
            logger.warning(
                "sybil_pow_invalid",
                peer_id=peer_id,
                ip=ip,
                difficulty=self.difficulty_bits,
            )
            return False, "invalid_pow"

        # Check 2: Node ID matches PoW hash
        expected_id = derive_node_id(public_key_bytes, pow_nonce)
        if peer_id != expected_id:
            logger.warning(
                "sybil_id_mismatch",
                peer_id=peer_id,
                expected_id=expected_id,
            )
            return False, "node_id_mismatch"

        # Check 3: Subnet limit
        if not self.subnet_limiter.can_add(ip, bucket_id):
            logger.warning(
                "sybil_subnet_limit",
                peer_id=peer_id,
                ip=ip,
                bucket_id=bucket_id,
            )
            return False, "subnet_limit_exceeded"

        # All checks passed — register and accept
        self.subnet_limiter.add(ip, peer_id, bucket_id)
        logger.info(
            "peer_validated",
            peer_id=peer_id[:16],
            ip=ip,
        )
        return True, "ok"
