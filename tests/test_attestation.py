"""Tests for infomesh.trust.attestation — content attestation chain."""

from __future__ import annotations

import pytest

from infomesh.trust.attestation import (
    content_hash,
    create_attestation,
    deserialize_attestation,
    serialize_attestation,
    verify_attestation,
)

# Use a simple mock KeyPair for testing without cryptography dependency
_MOCK_SIGNATURE = b"\x00" * 64


class MockKeyPair:
    """Lightweight key pair mock for attestation tests."""

    def __init__(self, peer_id: str = "mock-peer-001") -> None:
        self._peer_id = peer_id

    @property
    def peer_id(self) -> str:
        return self._peer_id

    def sign(self, data: bytes) -> bytes:
        # Deterministic mock signature keyed by peer_id
        import hashlib

        return hashlib.sha256(data + self._peer_id.encode()).digest() * 2  # 64 bytes

    def verify(self, data: bytes, signature: bytes) -> bool:
        expected = self.sign(data)
        return signature == expected


@pytest.fixture
def keypair():
    return MockKeyPair()


@pytest.fixture
def sample_attestation(keypair):
    return create_attestation(
        url="https://example.com/page",
        raw_body=b"<html>Hello World</html>",
        extracted_text="Hello World",
        key_pair=keypair,
        crawled_at=1700000000.0,
    )


# --- content_hash ----------------------------------------------------------


class TestContentHash:
    def test_bytes_input(self):
        h = content_hash(b"hello")
        assert len(h) == 64  # SHA-256 hex

    def test_string_input(self):
        h = content_hash("hello")
        assert h == content_hash(b"hello")

    def test_different_content_different_hash(self):
        assert content_hash("a") != content_hash("b")

    def test_deterministic(self):
        assert content_hash("test") == content_hash("test")


# --- create_attestation ----------------------------------------------------


class TestCreateAttestation:
    def test_creates_valid_attestation(self, sample_attestation, keypair):
        att = sample_attestation
        assert att.url == "https://example.com/page"
        assert att.peer_id == keypair.peer_id
        assert len(att.raw_hash) == 64
        assert len(att.text_hash) == 64
        assert att.crawled_at == 1700000000.0
        assert len(att.signature) == 64

    def test_hashes_are_correct(self, sample_attestation):
        att = sample_attestation
        assert att.raw_hash == content_hash(b"<html>Hello World</html>")
        assert att.text_hash == content_hash("Hello World")

    def test_content_length(self, sample_attestation):
        assert sample_attestation.content_length == len(b"Hello World")


# --- verify_attestation ----------------------------------------------------


class TestVerifyAttestation:
    def test_valid_attestation_passes(self, sample_attestation, keypair):
        result = verify_attestation(
            sample_attestation,
            keypair,
            raw_body=b"<html>Hello World</html>",
            extracted_text="Hello World",
        )
        assert result.verified is True
        assert result.signature_valid is True
        assert result.raw_match is True
        assert result.text_match is True
        assert result.detail == "ok"

    def test_signature_only_check(self, sample_attestation, keypair):
        result = verify_attestation(sample_attestation, keypair)
        assert result.verified is True
        assert result.signature_valid is True

    def test_wrong_content_fails(self, sample_attestation, keypair):
        result = verify_attestation(
            sample_attestation,
            keypair,
            raw_body=b"<html>TAMPERED</html>",
            extracted_text="Hello World",
        )
        assert result.verified is False
        assert result.raw_match is False
        assert "raw_hash_mismatch" in result.detail

    def test_wrong_text_fails(self, sample_attestation, keypair):
        result = verify_attestation(
            sample_attestation,
            keypair,
            extracted_text="Tampered text",
        )
        assert result.verified is False
        assert result.text_match is False

    def test_wrong_key_fails(self, sample_attestation):
        wrong_key = MockKeyPair(peer_id="wrong-peer")
        result = verify_attestation(sample_attestation, wrong_key)
        assert result.verified is False
        assert result.signature_valid is False


# --- serialization ---------------------------------------------------------


class TestSerialization:
    def test_roundtrip(self, sample_attestation):
        data = serialize_attestation(sample_attestation)
        restored = deserialize_attestation(data)
        assert restored.url == sample_attestation.url
        assert restored.raw_hash == sample_attestation.raw_hash
        assert restored.text_hash == sample_attestation.text_hash
        assert restored.peer_id == sample_attestation.peer_id
        assert restored.signature == sample_attestation.signature
        assert restored.crawled_at == sample_attestation.crawled_at

    def test_serialized_signature_is_hex(self, sample_attestation):
        data = serialize_attestation(sample_attestation)
        assert isinstance(data["signature"], str)
        # Should be valid hex
        bytes.fromhex(data["signature"])
