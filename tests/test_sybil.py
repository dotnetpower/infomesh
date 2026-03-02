"""Tests for Sybil defense: PoW node ID generation + subnet rate limiting."""

from __future__ import annotations

import hashlib
import os
import struct
import time

import pytest

from infomesh.p2p.sybil import (
    ProofOfWork,
    SubnetLimiter,
    SybilValidator,
    _count_leading_zero_bits_fast,
    compute_pow_hash,
    derive_node_id,
    generate_pow,
    verify_pow,
)

# ─── Leading Zero Bits Tests ───────────────────────────────


class TestLeadingZeroBits:
    def test_all_zeros(self) -> None:
        assert _count_leading_zero_bits_fast(b"\x00\x00\x00\x00") >= 32

    def test_first_bit_set(self) -> None:
        assert _count_leading_zero_bits_fast(b"\x80\x00") == 0

    def test_one_zero_byte(self) -> None:
        assert _count_leading_zero_bits_fast(b"\x00\x80") == 8

    def test_three_leading_zeros(self) -> None:
        # 0x10 = 0001_0000 → 3 leading zeros in this byte
        assert _count_leading_zero_bits_fast(b"\x10") == 3

    def test_five_leading_zeros(self) -> None:
        # 0x04 = 0000_0100 → 5 leading zeros
        assert _count_leading_zero_bits_fast(b"\x04") == 5

    def test_twelve_leading_zeros(self) -> None:
        # 0x00 0x0F → 8 + 4 = 12
        assert _count_leading_zero_bits_fast(b"\x00\x0f") == 12

    def test_empty_bytes(self) -> None:
        assert _count_leading_zero_bits_fast(b"") == 0


# ─── PoW Hash Tests ────────────────────────────────────────


class TestPowHash:
    def test_deterministic(self) -> None:
        key = os.urandom(32)
        h1 = compute_pow_hash(key, 42)
        h2 = compute_pow_hash(key, 42)
        assert h1 == h2

    def test_different_nonce_different_hash(self) -> None:
        key = os.urandom(32)
        h1 = compute_pow_hash(key, 0)
        h2 = compute_pow_hash(key, 1)
        assert h1 != h2

    def test_hash_format(self) -> None:
        key = os.urandom(32)
        h = compute_pow_hash(key, 0)
        assert isinstance(h, bytes)
        assert len(h) == 32  # SHA-256

    def test_matches_manual_sha256(self) -> None:
        key = b"A" * 32
        nonce = 123
        expected = hashlib.sha256(key + struct.pack("<Q", nonce)).digest()
        assert compute_pow_hash(key, nonce) == expected


# ─── PoW Generation Tests ──────────────────────────────────


class TestPowGeneration:
    def test_generate_low_difficulty(self) -> None:
        """Low difficulty (8 bits) should complete instantly."""
        key = os.urandom(32)
        pow_result = generate_pow(key, difficulty_bits=8)

        assert isinstance(pow_result, ProofOfWork)
        assert pow_result.difficulty_bits == 8
        assert pow_result.nonce >= 0
        assert pow_result.elapsed_seconds >= 0
        assert len(pow_result.hash_hex) == 64  # 32 bytes as hex

    def test_pow_is_verifiable(self) -> None:
        """Generated PoW must pass verification."""
        key = os.urandom(32)
        pow_result = generate_pow(key, difficulty_bits=8)
        assert verify_pow(key, pow_result.nonce, difficulty_bits=8)

    def test_pow_fails_with_wrong_key(self) -> None:
        """PoW should not verify with a different key."""
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        _pow_result = generate_pow(key1, difficulty_bits=8)
        # May or may not verify — but certainly not reliably
        # The test checks that wrong key doesn't always pass;
        # with 8-bit difficulty, ~1/256 chance of accidental pass
        # Use higher difficulty for strong assertion
        pow_result_high = generate_pow(key1, difficulty_bits=16)
        # With 16 bits, ~1/65536 chance of false positive — virtually impossible
        assert not verify_pow(key2, pow_result_high.nonce, difficulty_bits=16)
        # Also ensure different keys produce different IDs
        id1 = derive_node_id(key1, pow_result_high.nonce)
        id2 = derive_node_id(key2, pow_result_high.nonce)
        assert id1 != id2

    def test_pow_medium_difficulty(self) -> None:
        """Medium difficulty (12 bits) should complete in under 1 second."""
        key = os.urandom(32)
        pow_result = generate_pow(key, difficulty_bits=12)
        assert verify_pow(key, pow_result.nonce, difficulty_bits=12)
        assert pow_result.elapsed_seconds < 10  # generous limit

    def test_generate_respects_max_nonce(self) -> None:
        """Should raise if max_nonce is too low."""
        key = os.urandom(32)
        with pytest.raises(RuntimeError, match="no valid nonce found"):
            generate_pow(key, difficulty_bits=32, max_nonce=100)


# ─── PoW Verification Tests ────────────────────────────────


class TestPowVerification:
    def test_valid_pow(self) -> None:
        key = os.urandom(32)
        pow_result = generate_pow(key, difficulty_bits=8)
        assert verify_pow(key, pow_result.nonce, difficulty_bits=8) is True

    def test_invalid_nonce(self) -> None:
        key = os.urandom(32)
        # Nonce 0 with difficulty 32 is extremely unlikely to be valid
        assert verify_pow(key, 0, difficulty_bits=32) is False

    def test_higher_difficulty_still_valid(self) -> None:
        """A PoW for 16 bits should also validate at 8 bits."""
        key = os.urandom(32)
        pow_result = generate_pow(key, difficulty_bits=16)
        # Valid at lower difficulty
        assert verify_pow(key, pow_result.nonce, difficulty_bits=8)
        # Valid at exact difficulty
        assert verify_pow(key, pow_result.nonce, difficulty_bits=16)


# ─── Node ID Derivation Tests ──────────────────────────────


class TestNodeIDDerivation:
    def test_id_length(self) -> None:
        key = os.urandom(32)
        node_id = derive_node_id(key, 42)
        assert len(node_id) == 40  # 160 bits in hex

    def test_id_deterministic(self) -> None:
        key = os.urandom(32)
        id1 = derive_node_id(key, 42)
        id2 = derive_node_id(key, 42)
        assert id1 == id2

    def test_id_tied_to_pow(self) -> None:
        """Different nonces produce different IDs."""
        key = os.urandom(32)
        id1 = derive_node_id(key, 0)
        id2 = derive_node_id(key, 1)
        assert id1 != id2

    def test_id_tied_to_key(self) -> None:
        """Different keys produce different IDs."""
        key1 = os.urandom(32)
        key2 = os.urandom(32)
        id1 = derive_node_id(key1, 42)
        id2 = derive_node_id(key2, 42)
        assert id1 != id2


# ─── Subnet Limiter Tests ──────────────────────────────────


class TestSubnetLimiter:
    def test_can_add_initially(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=3)
        assert limiter.can_add("192.168.1.100", bucket_id=0)

    def test_add_succeeds(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=3)
        assert limiter.add("192.168.1.100", "peer-a", bucket_id=0)
        assert limiter.total_nodes() == 1

    def test_subnet_limit_enforced(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=2)
        assert limiter.add("192.168.1.10", "peer-a", bucket_id=0)
        assert limiter.add("192.168.1.20", "peer-b", bucket_id=0)
        # Third from same /24 should be rejected
        assert not limiter.can_add("192.168.1.30", bucket_id=0)
        assert not limiter.add("192.168.1.30", "peer-c", bucket_id=0)

    def test_different_subnets_ok(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=1)
        assert limiter.add("192.168.1.10", "peer-a", bucket_id=0)
        assert limiter.add("192.168.2.10", "peer-b", bucket_id=0)
        assert limiter.add("10.0.0.10", "peer-c", bucket_id=0)
        assert limiter.total_nodes() == 3

    def test_different_buckets_independent(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=1)
        assert limiter.add("192.168.1.10", "peer-a", bucket_id=0)
        # Same subnet, different bucket → allowed
        assert limiter.add("192.168.1.20", "peer-b", bucket_id=1)

    def test_remove(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=1)
        limiter.add("192.168.1.10", "peer-a", bucket_id=0)
        assert not limiter.can_add("192.168.1.20", bucket_id=0)

        limiter.remove("192.168.1.10", "peer-a", bucket_id=0)
        assert limiter.can_add("192.168.1.20", bucket_id=0)

    def test_ipv6_subnet(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=2)
        assert limiter.add("2001:db8::1", "peer-a", bucket_id=0)
        assert limiter.add("2001:db8::2", "peer-b", bucket_id=0)
        # Same /48 → rejected
        assert not limiter.can_add("2001:db8::3", bucket_id=0)

    def test_get_subnet_counts(self) -> None:
        limiter = SubnetLimiter(max_per_subnet=5)
        limiter.add("192.168.1.10", "peer-a", bucket_id=0)
        limiter.add("192.168.1.20", "peer-b", bucket_id=0)
        limiter.add("10.0.0.1", "peer-c", bucket_id=0)

        counts = limiter.get_subnet_counts(bucket_id=0)
        assert counts["192.168.1.0/24"] == 2
        assert counts["10.0.0.0/24"] == 1


# ─── Sybil Validator Integration Tests ─────────────────────


class TestSybilValidator:
    def test_valid_peer_accepted(self) -> None:
        """A peer with valid PoW and unique subnet should be accepted."""
        from infomesh.p2p.keys import KeyPair

        keys = KeyPair.generate()
        pubkey = keys.public_key_bytes()

        # Generate PoW
        pow_result = generate_pow(pubkey, difficulty_bits=8)
        node_id = derive_node_id(pubkey, pow_result.nonce)

        validator = SybilValidator(difficulty_bits=8, max_per_subnet=3)
        ok, reason = validator.validate_peer(
            public_key_bytes=pubkey,
            pow_nonce=pow_result.nonce,
            ip="192.168.1.100",
            peer_id=node_id,
            bucket_id=0,
        )

        assert ok is True
        assert reason == "ok"

    def test_invalid_pow_rejected(self) -> None:
        """A peer with invalid (fake) PoW should be rejected."""
        from infomesh.p2p.keys import KeyPair

        keys = KeyPair.generate()
        pubkey = keys.public_key_bytes()
        fake_id = derive_node_id(pubkey, 0)

        validator = SybilValidator(difficulty_bits=20, max_per_subnet=3)
        ok, reason = validator.validate_peer(
            public_key_bytes=pubkey,
            pow_nonce=0,  # Almost certainly invalid at difficulty 20
            ip="192.168.1.100",
            peer_id=fake_id,
            bucket_id=0,
        )

        assert ok is False
        assert reason == "invalid_pow"

    def test_wrong_node_id_rejected(self) -> None:
        """A peer claiming a different node ID than PoW hash should be rejected."""
        from infomesh.p2p.keys import KeyPair

        keys = KeyPair.generate()
        pubkey = keys.public_key_bytes()
        pow_result = generate_pow(pubkey, difficulty_bits=8)

        validator = SybilValidator(difficulty_bits=8, max_per_subnet=3)
        ok, reason = validator.validate_peer(
            public_key_bytes=pubkey,
            pow_nonce=pow_result.nonce,
            ip="192.168.1.100",
            peer_id="fake_node_id_that_doesnt_match_pow",
            bucket_id=0,
        )

        assert ok is False
        assert reason == "node_id_mismatch"

    def test_subnet_overflow_rejected(self) -> None:
        """Too many peers from same /24 subnet should be rejected."""
        validator = SybilValidator(difficulty_bits=8, max_per_subnet=2)

        # Add 2 valid peers from same subnet
        for i in range(2):
            from infomesh.p2p.keys import KeyPair

            keys = KeyPair.generate()
            pubkey = keys.public_key_bytes()
            pow_result = generate_pow(pubkey, difficulty_bits=8)
            node_id = derive_node_id(pubkey, pow_result.nonce)

            ok, reason = validator.validate_peer(
                public_key_bytes=pubkey,
                pow_nonce=pow_result.nonce,
                ip=f"10.0.0.{10 + i}",
                peer_id=node_id,
                bucket_id=0,
            )
            assert ok is True

        # Third peer from same /24 → rejected
        keys = KeyPair.generate()
        pubkey = keys.public_key_bytes()
        pow_result = generate_pow(pubkey, difficulty_bits=8)
        node_id = derive_node_id(pubkey, pow_result.nonce)

        ok, reason = validator.validate_peer(
            public_key_bytes=pubkey,
            pow_nonce=pow_result.nonce,
            ip="10.0.0.99",
            peer_id=node_id,
            bucket_id=0,
        )
        assert ok is False
        assert reason == "subnet_limit_exceeded"

    def test_pow_timing_reference(self) -> None:
        """Measure PoW generation time at different difficulties for reference."""
        from infomesh.p2p.keys import KeyPair

        keys = KeyPair.generate()
        pubkey = keys.public_key_bytes()

        results: list[tuple[int, float, int]] = []
        for bits in [8, 12, 16]:
            start = time.monotonic()
            pow_result = generate_pow(pubkey, difficulty_bits=bits)
            elapsed = time.monotonic() - start
            results.append((bits, elapsed, pow_result.nonce))

        print("\n  PoW timing reference:")
        for bits, elapsed, nonce in results:
            print(f"    {bits} bits: {elapsed:.3f}s (nonce={nonce})")

        # All should complete in reasonable time
        for bits, elapsed, _ in results:
            assert elapsed < 30, f"{bits}-bit PoW took {elapsed:.1f}s (too slow)"


# ─── PoW Cache Tests ──────────────────────────────────────


class TestPoWCache:
    """Test PoW result caching for fast restarts."""

    def test_cache_roundtrip(self, tmp_path: object) -> None:
        """Save and load PoW cache should return same nonce."""
        from pathlib import Path

        from infomesh.p2p.node import InfoMeshNode

        cache_path = Path(str(tmp_path)) / "pow_cache.bin"
        pub_key = os.urandom(32)

        # Generate a real PoW for this key
        pow_result = generate_pow(pub_key, difficulty_bits=8)

        InfoMeshNode._save_cached_pow(
            cache_path, pub_key, pow_result.nonce, difficulty=8
        )
        loaded = InfoMeshNode._load_cached_pow(cache_path, pub_key)
        assert loaded == pow_result.nonce

    def test_cache_rejects_wrong_key(self, tmp_path: object) -> None:
        """Cache must reject if the public key changed."""
        from pathlib import Path

        from infomesh.p2p.node import InfoMeshNode

        cache_path = Path(str(tmp_path)) / "pow_cache.bin"
        pub_key_1 = os.urandom(32)
        pub_key_2 = os.urandom(32)

        pow_result = generate_pow(pub_key_1, difficulty_bits=8)
        InfoMeshNode._save_cached_pow(
            cache_path, pub_key_1, pow_result.nonce, difficulty=8
        )

        loaded = InfoMeshNode._load_cached_pow(cache_path, pub_key_2)
        assert loaded is None

    def test_cache_missing_file(self, tmp_path: object) -> None:
        """Cache returns None if file doesn't exist."""
        from pathlib import Path

        from infomesh.p2p.node import InfoMeshNode

        cache_path = Path(str(tmp_path)) / "nonexistent.bin"
        loaded = InfoMeshNode._load_cached_pow(cache_path, os.urandom(32))
        assert loaded is None

    def test_cache_corrupt_file(self, tmp_path: object) -> None:
        """Cache returns None for corrupted data."""
        from pathlib import Path

        from infomesh.p2p.node import InfoMeshNode

        cache_path = Path(str(tmp_path)) / "pow_cache.bin"
        cache_path.write_bytes(b"corrupt")
        loaded = InfoMeshNode._load_cached_pow(cache_path, os.urandom(32))
        assert loaded is None
