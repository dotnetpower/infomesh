"""Tests for key rotation and revocation protocol."""

from __future__ import annotations

from pathlib import Path

import pytest

from infomesh.p2p.keys import (
    KeyPair,
    load_revocations,
    rotate_keys,
    verify_revocation,
)
from infomesh.p2p.protocol import KeyRevocationRecord


@pytest.fixture
def keys_dir(tmp_data_dir: Path) -> Path:
    """Create a keys directory with an initial key pair."""
    pair = KeyPair.generate()
    pair.save(tmp_data_dir / "keys")
    return tmp_data_dir


class TestRotateKeys:
    """Tests for ``rotate_keys``."""

    def test_rotate_produces_new_keys(self, keys_dir: Path) -> None:
        old_keys = KeyPair.load(keys_dir / "keys")
        old_peer_id = old_keys.peer_id

        _, new_keys, _ = rotate_keys(keys_dir)

        assert new_keys.peer_id != old_peer_id

    def test_rotate_returns_old_keys(self, keys_dir: Path) -> None:
        expected_id = KeyPair.load(keys_dir / "keys").peer_id
        old_keys, _, _ = rotate_keys(keys_dir)
        assert old_keys.peer_id == expected_id

    def test_rotate_saves_new_keys_to_disk(self, keys_dir: Path) -> None:
        _, new_keys, _ = rotate_keys(keys_dir)
        loaded = KeyPair.load(keys_dir / "keys")
        assert loaded.peer_id == new_keys.peer_id

    def test_rotate_backs_up_old_keys(self, keys_dir: Path) -> None:
        rotate_keys(keys_dir)
        backups = list((keys_dir / "keys").glob("backup-*"))
        assert len(backups) == 1
        assert (backups[0] / "private.pem").exists()
        assert (backups[0] / "public.pem").exists()

    def test_rotate_creates_revocation_record(self, keys_dir: Path) -> None:
        old_keys, new_keys, record = rotate_keys(keys_dir)
        assert isinstance(record, KeyRevocationRecord)
        assert record.old_peer_id == old_keys.peer_id
        assert record.new_peer_id == new_keys.peer_id
        assert record.reason == "rotation"
        assert record.old_public_key == old_keys.public_key_bytes()
        assert record.new_public_key == new_keys.public_key_bytes()

    def test_rotate_no_existing_keys_raises(self, tmp_data_dir: Path) -> None:
        empty_dir = tmp_data_dir / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="No existing key pair"):
            rotate_keys(empty_dir)

    def test_rotate_saves_revocation_bin(self, keys_dir: Path) -> None:
        rotate_keys(keys_dir)
        revocation_files = list((keys_dir / "keys" / "revocations").glob("*.bin"))
        assert len(revocation_files) == 1

    def test_rotate_twice_creates_two_backups(self, keys_dir: Path) -> None:
        import time

        rotate_keys(keys_dir)
        time.sleep(1.1)  # ensure different timestamp
        rotate_keys(keys_dir)

        backups = list((keys_dir / "keys").glob("backup-*"))
        assert len(backups) == 2


class TestVerifyRevocation:
    """Tests for ``verify_revocation``."""

    def test_valid_revocation_passes(self, keys_dir: Path) -> None:
        _, _, record = rotate_keys(keys_dir)
        assert verify_revocation(record) is True

    def test_tampered_old_sig_fails(self, keys_dir: Path) -> None:
        _, _, record = rotate_keys(keys_dir)
        bad = KeyRevocationRecord(
            old_peer_id=record.old_peer_id,
            new_peer_id=record.new_peer_id,
            old_public_key=record.old_public_key,
            new_public_key=record.new_public_key,
            reason=record.reason,
            timestamp=record.timestamp,
            old_key_signature=b"\x00" * 64,  # forged
            new_key_signature=record.new_key_signature,
        )
        assert verify_revocation(bad) is False

    def test_tampered_new_sig_fails(self, keys_dir: Path) -> None:
        _, _, record = rotate_keys(keys_dir)
        bad = KeyRevocationRecord(
            old_peer_id=record.old_peer_id,
            new_peer_id=record.new_peer_id,
            old_public_key=record.old_public_key,
            new_public_key=record.new_public_key,
            reason=record.reason,
            timestamp=record.timestamp,
            old_key_signature=record.old_key_signature,
            new_key_signature=b"\x00" * 64,  # forged
        )
        assert verify_revocation(bad) is False

    def test_tampered_reason_fails(self, keys_dir: Path) -> None:
        _, _, record = rotate_keys(keys_dir)
        bad = KeyRevocationRecord(
            old_peer_id=record.old_peer_id,
            new_peer_id=record.new_peer_id,
            old_public_key=record.old_public_key,
            new_public_key=record.new_public_key,
            reason="compromise",  # changed
            timestamp=record.timestamp,
            old_key_signature=record.old_key_signature,
            new_key_signature=record.new_key_signature,
        )
        assert verify_revocation(bad) is False


class TestLoadRevocations:
    """Tests for ``load_revocations``."""

    def test_no_revocations_dir(self, tmp_data_dir: Path) -> None:
        assert load_revocations(tmp_data_dir) == []

    def test_load_after_rotation(self, keys_dir: Path) -> None:
        _, _, original = rotate_keys(keys_dir)
        records = load_revocations(keys_dir)
        assert len(records) == 1
        assert records[0].old_peer_id == original.old_peer_id
        assert records[0].new_peer_id == original.new_peer_id

    def test_loaded_record_verifies(self, keys_dir: Path) -> None:
        rotate_keys(keys_dir)
        records = load_revocations(keys_dir)
        assert verify_revocation(records[0]) is True

    def test_corrupt_file_skipped(self, keys_dir: Path) -> None:
        rotate_keys(keys_dir)
        # Write a corrupt file
        rev_dir = keys_dir / "keys" / "revocations"
        (rev_dir / "corrupt.bin").write_bytes(b"not-valid-msgpack")
        records = load_revocations(keys_dir)
        # Should still load the valid one
        assert len(records) >= 1
