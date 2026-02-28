"""Ed25519 key pair generation, storage, and management.

Keys are stored in ~/.infomesh/keys/ with restrictive permissions (0600).
Used for content attestation, peer identity, and message signing.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from cryptography.exceptions import InvalidSignature

if TYPE_CHECKING:
    from infomesh.p2p.protocol import KeyRevocationRecord

logger = structlog.get_logger()

# Lazy import to avoid hard dependency on cryptography at module level
_Ed25519PrivateKey = None
_Ed25519PublicKey = None


def _ensure_crypto() -> None:
    """Import cryptography types on first use."""
    global _Ed25519PrivateKey, _Ed25519PublicKey  # noqa: PLW0603
    if _Ed25519PrivateKey is None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )

        _Ed25519PrivateKey = Ed25519PrivateKey
        _Ed25519PublicKey = Ed25519PublicKey


class KeyPair:
    """Ed25519 key pair for node identity and signing.

    Usage:
        keys = KeyPair.generate()
        keys.save(keys_dir)

        # Later:
        keys = KeyPair.load(keys_dir)
        sig = keys.sign(data)
        keys.verify(data, sig)
    """

    def __init__(self, private_key: object, public_key: object) -> None:
        self._private_key = private_key
        self._public_key = public_key

    @classmethod
    def generate(cls) -> KeyPair:
        """Generate a new Ed25519 key pair."""
        _ensure_crypto()
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        logger.info(
            "keypair_generated", public_key_hash=cls._key_fingerprint(public_key)
        )
        return cls(private_key, public_key)

    @classmethod
    def load(cls, keys_dir: Path) -> KeyPair:
        """Load key pair from disk.

        Args:
            keys_dir: Directory containing private.pem and public.pem.

        Returns:
            Loaded KeyPair instance.

        Raises:
            FileNotFoundError: If key files don't exist.
        """
        _ensure_crypto()
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        private_path = keys_dir / "private.pem"

        if not private_path.exists():
            msg = f"Private key not found: {private_path}"
            raise FileNotFoundError(msg)

        private_bytes = private_path.read_bytes()
        private_key = load_pem_private_key(private_bytes, password=None)
        public_key = private_key.public_key()

        logger.info("keypair_loaded", keys_dir=str(keys_dir))
        return cls(private_key, public_key)

    def save(self, keys_dir: Path) -> None:
        """Save key pair to disk with restrictive permissions.

        Args:
            keys_dir: Directory to save private.pem and public.pem.
        """
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            NoEncryption,
            PrivateFormat,
            PublicFormat,
        )

        keys_dir.mkdir(parents=True, exist_ok=True)

        private_path = keys_dir / "private.pem"
        public_path = keys_dir / "public.pem"

        # Write private key with restrictive permissions
        private_bytes = self._private_key.private_bytes(  # type: ignore[attr-defined]
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
        private_path.write_bytes(private_bytes)
        os.chmod(private_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600

        # Write public key
        public_bytes = self._public_key.public_bytes(  # type: ignore[attr-defined]
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )
        public_path.write_bytes(public_bytes)
        os.chmod(
            public_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH
        )  # 0644

        logger.info("keypair_saved", keys_dir=str(keys_dir))

    def sign(self, data: bytes) -> bytes:
        """Sign data with the private key.

        Args:
            data: Raw bytes to sign.

        Returns:
            Ed25519 signature (64 bytes).
        """
        return self._private_key.sign(data)  # type: ignore[attr-defined, no-any-return]

    def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify a signature against the public key.

        Args:
            data: Original data.
            signature: Signature to verify.

        Returns:
            True if valid, False otherwise.
        """
        try:
            self._public_key.verify(signature, data)  # type: ignore[attr-defined]
            return True
        except InvalidSignature:
            return False

    def public_key_bytes(self) -> bytes:
        """Get raw public key bytes (32 bytes)."""
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        return self._public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)  # type: ignore[attr-defined, no-any-return]

    @property
    def peer_id(self) -> str:
        """Derive a stable peer ID from the public key (SHA-256 hex, 20 chars)."""
        raw = self.public_key_bytes()
        return hashlib.sha256(raw).hexdigest()[:40]

    @staticmethod
    def _key_fingerprint(public_key: object) -> str:
        """Generate a short fingerprint for logging."""
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)  # type: ignore[attr-defined]
        return hashlib.sha256(raw).hexdigest()[:16]


def ensure_keys(data_dir: Path) -> KeyPair:
    """Load existing keys or generate new ones on first run.

    Args:
        data_dir: InfoMesh data directory (e.g., ~/.infomesh/).

    Returns:
        KeyPair ready for use.
    """
    keys_dir = data_dir / "keys"

    if (keys_dir / "private.pem").exists():
        return KeyPair.load(keys_dir)

    logger.info("first_run_keygen", keys_dir=str(keys_dir))
    pair = KeyPair.generate()
    pair.save(keys_dir)
    return pair


def export_public_key(data_dir: Path) -> str:
    """Export the public key as PEM string.

    Args:
        data_dir: InfoMesh data directory.

    Returns:
        PEM-encoded public key string.
    """
    public_path = data_dir / "keys" / "public.pem"
    if not public_path.exists():
        msg = f"No public key found at {public_path}. Run 'infomesh start' first."
        raise FileNotFoundError(msg)
    return public_path.read_text()


def rotate_keys(data_dir: Path) -> tuple[KeyPair, KeyPair, KeyRevocationRecord]:
    """Rotate key pair: generate new keys, sign a revocation record.

    The old keys are backed up and a ``KeyRevocationRecord`` is created,
    signed by both old and new keys to prove continuity of identity.

    Args:
        data_dir: InfoMesh data directory (e.g., ``~/.infomesh/``).

    Returns:
        Tuple of (old_keys, new_keys, revocation_record).

    Raises:
        FileNotFoundError: If no existing keys to rotate.
    """
    import time as _time

    from infomesh.p2p.protocol import KeyRevocationRecord

    keys_dir = data_dir / "keys"
    private_path = keys_dir / "private.pem"
    if not private_path.exists():
        msg = "No existing key pair to rotate. Run 'infomesh start' first."
        raise FileNotFoundError(msg)

    # Load old keys
    old_keys = KeyPair.load(keys_dir)

    # Backup old keys
    backup_dir = keys_dir / f"backup-{int(_time.time())}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    import shutil

    shutil.copy2(keys_dir / "private.pem", backup_dir / "private.pem")
    shutil.copy2(keys_dir / "public.pem", backup_dir / "public.pem")
    logger.info("keys_backed_up", backup_dir=str(backup_dir))

    # Generate new keys
    new_keys = KeyPair.generate()

    # Build the revocation payload (deterministic bytes for signing)
    import msgpack

    ts = _time.time()
    payload = msgpack.packb(
        {
            "old_peer_id": old_keys.peer_id,
            "new_peer_id": new_keys.peer_id,
            "old_public_key": old_keys.public_key_bytes(),
            "new_public_key": new_keys.public_key_bytes(),
            "reason": "rotation",
            "timestamp": ts,
        },
        use_bin_type=True,
    )

    # Sign with both keys
    old_sig = old_keys.sign(payload)
    new_sig = new_keys.sign(payload)

    record = KeyRevocationRecord(
        old_peer_id=old_keys.peer_id,
        new_peer_id=new_keys.peer_id,
        old_public_key=old_keys.public_key_bytes(),
        new_public_key=new_keys.public_key_bytes(),
        reason="rotation",
        timestamp=ts,
        old_key_signature=old_sig,
        new_key_signature=new_sig,
    )

    # Save new keys (overwrites old files)
    new_keys.save(keys_dir)

    # Save revocation record
    revocation_path = keys_dir / "revocations"
    revocation_path.mkdir(parents=True, exist_ok=True)
    record_file = revocation_path / f"{old_keys.peer_id[:16]}.bin"
    record_file.write_bytes(
        msgpack.packb(
            {
                "old_peer_id": record.old_peer_id,
                "new_peer_id": record.new_peer_id,
                "old_public_key": record.old_public_key,
                "new_public_key": record.new_public_key,
                "reason": record.reason,
                "timestamp": record.timestamp,
                "old_key_signature": record.old_key_signature,
                "new_key_signature": record.new_key_signature,
            },
            use_bin_type=True,
        )
    )

    logger.info(
        "keys_rotated",
        old_peer_id=old_keys.peer_id,
        new_peer_id=new_keys.peer_id,
        revocation_saved=str(record_file),
    )

    return old_keys, new_keys, record


def verify_revocation(record: KeyRevocationRecord) -> bool:
    """Verify that a revocation record is properly signed by both keys.

    Args:
        record: The ``KeyRevocationRecord`` to verify.

    Returns:
        ``True`` if both signatures are valid.
    """
    _ensure_crypto()
    import msgpack
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    # Reconstruct the signed payload
    payload = msgpack.packb(
        {
            "old_peer_id": record.old_peer_id,
            "new_peer_id": record.new_peer_id,
            "old_public_key": record.old_public_key,
            "new_public_key": record.new_public_key,
            "reason": record.reason,
            "timestamp": record.timestamp,
        },
        use_bin_type=True,
    )

    try:
        old_pub = Ed25519PublicKey.from_public_bytes(record.old_public_key)
        old_pub.verify(record.old_key_signature, payload)
    except Exception:
        logger.warning("revocation_old_sig_invalid", old_peer_id=record.old_peer_id)
        return False

    try:
        new_pub = Ed25519PublicKey.from_public_bytes(record.new_public_key)
        new_pub.verify(record.new_key_signature, payload)
    except Exception:
        logger.warning("revocation_new_sig_invalid", new_peer_id=record.new_peer_id)
        return False

    return True


def load_revocations(data_dir: Path) -> list[KeyRevocationRecord]:
    """Load all saved revocation records from disk.

    Args:
        data_dir: InfoMesh data directory.

    Returns:
        List of ``KeyRevocationRecord`` instances.
    """
    import msgpack

    from infomesh.p2p.protocol import KeyRevocationRecord

    revocations_dir = data_dir / "keys" / "revocations"
    if not revocations_dir.exists():
        return []

    records: list[KeyRevocationRecord] = []
    for path in sorted(revocations_dir.glob("*.bin")):
        try:
            raw = msgpack.unpackb(path.read_bytes(), raw=False)
            records.append(KeyRevocationRecord(**raw))
        except Exception:
            logger.warning("revocation_load_failed", path=str(path))
    return records
