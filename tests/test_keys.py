"""Tests for Ed25519 key management."""

from __future__ import annotations

from pathlib import Path

import pytest

from infomesh.p2p.keys import KeyPair, ensure_keys, export_public_key


def test_generate_keypair() -> None:
    """Should generate a valid key pair."""
    pair = KeyPair.generate()
    assert pair.peer_id
    assert len(pair.peer_id) == 40  # SHA-256 hex prefix
    assert len(pair.public_key_bytes()) == 32  # Ed25519 public key


def test_save_and_load(tmp_path: Path) -> None:
    """Should save and reload keys correctly."""
    keys_dir = tmp_path / "keys"
    pair = KeyPair.generate()
    pair.save(keys_dir)

    # Files should exist
    assert (keys_dir / "private.pem").exists()
    assert (keys_dir / "public.pem").exists()

    # Permissions on private key (owner read/write only)
    import stat

    mode = (keys_dir / "private.pem").stat().st_mode
    assert mode & stat.S_IRWXG == 0  # no group access
    assert mode & stat.S_IRWXO == 0  # no other access

    # Reload
    loaded = KeyPair.load(keys_dir)
    assert loaded.peer_id == pair.peer_id


def test_sign_and_verify() -> None:
    """Should sign and verify data correctly."""
    pair = KeyPair.generate()
    data = b"Hello, InfoMesh!"
    sig = pair.sign(data)

    assert pair.verify(data, sig) is True
    assert pair.verify(b"tampered data", sig) is False


def test_ensure_keys_first_run(tmp_path: Path) -> None:
    """ensure_keys should generate keys on first run."""
    pair = ensure_keys(tmp_path)
    assert pair.peer_id
    assert (tmp_path / "keys" / "private.pem").exists()


def test_ensure_keys_existing(tmp_path: Path) -> None:
    """ensure_keys should load existing keys."""
    pair1 = ensure_keys(tmp_path)
    pair2 = ensure_keys(tmp_path)
    assert pair1.peer_id == pair2.peer_id


def test_export_public_key(tmp_path: Path) -> None:
    """Should export public key as PEM."""
    ensure_keys(tmp_path)
    pem = export_public_key(tmp_path)
    assert pem.startswith("-----BEGIN PUBLIC KEY-----")


def test_export_no_key(tmp_path: Path) -> None:
    """Should raise FileNotFoundError if no key exists."""
    with pytest.raises(FileNotFoundError):
        export_public_key(tmp_path)
