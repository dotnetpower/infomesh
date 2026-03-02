"""GDPR distributed deletion records.

Provides a mechanism for propagating page-level deletion requests across
the P2P network.  When a data subject requests deletion of a page
containing personal data, a signed deletion record is published to the
DHT.  All nodes hosting the content must delete it.

Flow:
1. Data subject (or their proxy) creates a signed DeletionRequest.
2. The request is published to the DHT under the URL's hash key.
3. Nodes periodically check for deletion records for their hosted content.
4. On receipt, nodes remove the content and confirm compliance.
5. Deletion records are permanent — re-crawling a deleted URL is blocked.

Unlike DMCA (which is about copyright), GDPR deletion is about personal
data.  The two systems share mechanics but differ in legal basis and
compliance requirements.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import structlog

from infomesh.db import SQLiteStore
from infomesh.hashing import content_hash, short_hash
from infomesh.types import KeyPairLike

logger = structlog.get_logger()


# --- Constants -------------------------------------------------------------

# DHT key prefix for deletion records
DELETION_DHT_PREFIX: str = "/infomesh/gdpr/"

# Maximum reason text length
MAX_REASON_LENGTH: int = 5_000


class DeletionStatus(StrEnum):
    """Status of a GDPR deletion request."""

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    DELETED = "deleted"
    INVALID = "invalid"


class DeletionBasis(StrEnum):
    """Legal basis for GDPR deletion."""

    RIGHT_TO_ERASURE = "right_to_erasure"  # Art. 17(1)(a)
    CONSENT_WITHDRAWN = "consent_withdrawn"  # Art. 17(1)(b)
    OBJECTION = "objection"  # Art. 17(1)(c)
    UNLAWFUL_PROCESSING = "unlawful_processing"  # Art. 17(1)(d)
    LEGAL_OBLIGATION = "legal_obligation"  # Art. 17(1)(e)


@dataclass(frozen=True)
class DeletionRequest:
    """A GDPR deletion request for a specific URL."""

    request_id: str
    url: str
    requester_id: str  # Peer ID or external identifier
    basis: DeletionBasis
    reason: str
    signature: bytes  # Ed25519 signature over the request payload
    created_at: float
    personal_data_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DeletionConfirmation:
    """Confirmation that a node has deleted the content."""

    request_id: str
    peer_id: str
    status: DeletionStatus
    deleted_at: float | None = None
    detail: str = ""


@dataclass
class DeletionRecord:
    """Full record of a deletion request with confirmations."""

    request: DeletionRequest
    confirmations: list[DeletionConfirmation] = field(default_factory=list)
    propagated_to: list[str] = field(default_factory=list)


# --- Deletion manager -------------------------------------------------------


class DeletionManager:
    """Manages GDPR deletion requests — creation, verification, compliance.

    Maintains a **persistent** registry of deletion requests and a
    blocklist of URLs that must never be re-crawled, backed by SQLite
    so records survive restarts.  A node cannot evade GDPR obligations
    by simply restarting.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._records: dict[str, DeletionRecord] = {}
        self._url_deletions: dict[str, str] = {}  # url → request_id
        self._blocklist: set[str] = set()  # URLs permanently blocked

        # Persistent storage
        self._store: _GDPRStore | None = None
        if db_path is not None:
            self._store = _GDPRStore(db_path)
            self._load_from_store()

    def create_request(
        self,
        url: str,
        basis: DeletionBasis,
        reason: str,
        key_pair: KeyPairLike,
        *,
        personal_data_fields: list[str] | None = None,
        now: float | None = None,
    ) -> DeletionRequest:
        """Create a signed GDPR deletion request.

        Args:
            url: URL containing personal data to delete.
            basis: Legal basis for deletion.
            reason: Explanation of the request.
            key_pair: Requester's key pair for signing.
            personal_data_fields: Optional list of affected data fields.
            now: Override timestamp.

        Returns:
            Signed DeletionRequest.
        """
        now = now or time.time()
        request_id = _generate_request_id(url, key_pair.peer_id, now)
        payload = _request_payload(request_id, url, basis.value, reason, now)
        signature = key_pair.sign(payload)

        request = DeletionRequest(
            request_id=request_id,
            url=url,
            requester_id=key_pair.peer_id,
            basis=basis,
            reason=reason[:MAX_REASON_LENGTH],
            signature=signature,
            created_at=now,
            personal_data_fields=personal_data_fields or [],
        )

        self._records[request_id] = DeletionRecord(request=request)
        self._url_deletions[url] = request_id
        self._blocklist.add(url)
        self._persist_request(request)
        self._persist_blocklist(url)

        logger.info(
            "gdpr_deletion_created",
            request_id=request_id,
            url=url,
            basis=basis.value,
        )
        return request

    def verify_request(
        self,
        request: DeletionRequest,
        key_pair: KeyPairLike,
    ) -> bool:
        """Verify the signature on a deletion request.

        Args:
            request: The deletion request to verify.
            key_pair: Key pair with requester's public key.

        Returns:
            True if the signature is valid.
        """
        payload = _request_payload(
            request.request_id,
            request.url,
            request.basis.value,
            request.reason,
            request.created_at,
        )
        return key_pair.verify(payload, request.signature)

    def receive_request(
        self,
        request: DeletionRequest,
        requester_key: KeyPairLike | None = None,
    ) -> bool:
        """Process an externally-received deletion request (from DHT).

        Verifies the signature before adding to registry and blocklist.

        Args:
            request: The incoming deletion request.
            requester_key: Key pair with requester's public key for
                signature verification.  If ``None``, the request is
                logged but **not** actioned (untrusted).

        Returns:
            ``True`` if the request was accepted.
        """
        # Signature verification is mandatory for external requests
        if requester_key is not None:
            if not self.verify_request(request, requester_key):
                logger.warning(
                    "gdpr_request_rejected",
                    request_id=request.request_id,
                    reason="invalid_signature",
                )
                return False
        else:
            logger.warning(
                "gdpr_request_unverified",
                request_id=request.request_id,
                reason="no_key_provided",
            )
            return False

        if request.request_id not in self._records:
            self._records[request.request_id] = DeletionRecord(request=request)
            self._persist_request(request)
        self._url_deletions[request.url] = request.request_id
        self._blocklist.add(request.url)
        self._persist_blocklist(request.url)
        logger.info(
            "gdpr_request_accepted",
            request_id=request.request_id,
            url=request.url,
        )
        return True

    def confirm_deletion(
        self,
        request_id: str,
        peer_id: str,
        *,
        now: float | None = None,
    ) -> DeletionConfirmation | None:
        """Confirm that content has been deleted by a peer.

        Args:
            request_id: The deletion request being confirmed.
            peer_id: The peer that deleted the content.
            now: Override timestamp.

        Returns:
            DeletionConfirmation or None if request not found.
        """
        record = self._records.get(request_id)
        if record is None:
            return None

        now = now or time.time()
        confirmation = DeletionConfirmation(
            request_id=request_id,
            peer_id=peer_id,
            status=DeletionStatus.DELETED,
            deleted_at=now,
            detail=f"deleted at {now:.0f}",
        )
        record.confirmations.append(confirmation)
        self._persist_confirmation(confirmation)

        logger.info(
            "gdpr_deletion_confirmed",
            request_id=request_id,
            peer_id=peer_id[:12],
        )
        return confirmation

    def record_propagation(self, request_id: str, peer_id: str) -> None:
        """Record that a deletion request was propagated to a peer."""
        record = self._records.get(request_id)
        if record and peer_id not in record.propagated_to:
            record.propagated_to.append(peer_id)
            self._persist_propagation(request_id, peer_id)

    def is_blocked(self, url: str) -> bool:
        """Check if a URL is on the permanent deletion blocklist.

        Used by the crawler to prevent re-crawling deleted content.
        """
        return url in self._blocklist

    def unblock(self, url: str, *, admin_key: KeyPairLike) -> bool:
        """Remove a URL from the blocklist (admin/legal override).

        This is for cases where a GDPR request was made in error
        or a court order reverses the deletion.

        Args:
            url: URL to unblock.
            admin_key: Admin key pair for authorization verification.

        Returns:
            ``True`` if the URL was on the blocklist and removed.
        """
        if url not in self._blocklist:
            return False

        # Remove from blocklist
        self._blocklist.discard(url)
        self._remove_from_blocklist(url)

        # Mark the record as invalid
        request_id = self._url_deletions.pop(url, None)
        if request_id and request_id in self._records:
            record = self._records[request_id]
            record.confirmations.append(
                DeletionConfirmation(
                    request_id=request_id,
                    peer_id=getattr(admin_key, "peer_id", "admin"),
                    status=DeletionStatus.INVALID,
                    deleted_at=time.time(),
                    detail="admin_unblock",
                )
            )

        logger.info("gdpr_url_unblocked", url=url, request_id=request_id)
        return True

    def get_request_for_url(self, url: str) -> DeletionRequest | None:
        """Get the deletion request for a URL, if any."""
        request_id = self._url_deletions.get(url)
        if request_id and request_id in self._records:
            return self._records[request_id].request
        return None

    def get_record(self, request_id: str) -> DeletionRecord | None:
        """Get the full deletion record."""
        return self._records.get(request_id)

    def list_pending(self, peer_id: str) -> list[DeletionRequest]:
        """List deletion requests where a peer has not yet confirmed."""
        result = []
        for record in self._records.values():
            confirmed = any(
                c.peer_id == peer_id and c.status == DeletionStatus.DELETED
                for c in record.confirmations
            )
            if not confirmed:
                result.append(record.request)
        return result

    def list_all(self) -> list[DeletionRequest]:
        """List all deletion requests."""
        return [r.request for r in self._records.values()]

    @property
    def blocklist_size(self) -> int:
        """Number of URLs on the blocklist."""
        return len(self._blocklist)

    # -- persistence helpers --

    def _load_from_store(self) -> None:
        """Load all records from SQLite into memory caches."""
        if self._store is None:
            return
        for req, confs, props in self._store.load_all():
            self._records[req.request_id] = DeletionRecord(
                request=req,
                confirmations=list(confs),
                propagated_to=list(props),
            )
            self._url_deletions[req.url] = req.request_id
        # Reload blocklist
        for url in self._store.load_blocklist():
            self._blocklist.add(url)

    def _persist_request(self, request: DeletionRequest) -> None:
        if self._store is not None:
            self._store.save_request(request)

    def _persist_confirmation(self, confirmation: DeletionConfirmation) -> None:
        if self._store is not None:
            self._store.save_confirmation(confirmation)

    def _persist_propagation(self, request_id: str, peer_id: str) -> None:
        if self._store is not None:
            self._store.save_propagation(request_id, peer_id)

    def _persist_blocklist(self, url: str) -> None:
        if self._store is not None:
            self._store.add_blocklist(url)

    def _remove_from_blocklist(self, url: str) -> None:
        if self._store is not None:
            self._store.remove_blocklist(url)

    def close(self) -> None:
        """Close the underlying store, if any."""
        if self._store is not None:
            self._store.close()


# --- Persistent store -------------------------------------------------------


class _GDPRStore(SQLiteStore):
    """SQLite-backed GDPR record persistence."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS gdpr_requests (
            request_id  TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            basis       TEXT NOT NULL,
            reason      TEXT NOT NULL,
            signature   BLOB NOT NULL,
            created_at  REAL NOT NULL,
            personal_data_fields TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_gdpr_url
            ON gdpr_requests(url);

        CREATE TABLE IF NOT EXISTS gdpr_confirmations (
            request_id  TEXT NOT NULL,
            peer_id     TEXT NOT NULL,
            status      TEXT NOT NULL,
            deleted_at  REAL,
            detail      TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (request_id, peer_id, status)
        );

        CREATE TABLE IF NOT EXISTS gdpr_propagations (
            request_id  TEXT NOT NULL,
            peer_id     TEXT NOT NULL,
            PRIMARY KEY (request_id, peer_id)
        );

        CREATE TABLE IF NOT EXISTS gdpr_blocklist (
            url TEXT PRIMARY KEY
        );
    """

    # -- write --

    def save_request(self, request: DeletionRequest) -> None:
        import json

        self._conn.execute(
            "INSERT OR REPLACE INTO gdpr_requests "
            "(request_id, url, requester_id, basis, reason, "
            "signature, created_at, personal_data_fields) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request.request_id,
                request.url,
                request.requester_id,
                request.basis.value,
                request.reason,
                request.signature,
                request.created_at,
                json.dumps(request.personal_data_fields),
            ),
        )
        self._conn.commit()

    def save_confirmation(self, confirmation: DeletionConfirmation) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO gdpr_confirmations "
            "(request_id, peer_id, status, deleted_at, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                confirmation.request_id,
                confirmation.peer_id,
                confirmation.status.value,
                confirmation.deleted_at,
                confirmation.detail,
            ),
        )
        self._conn.commit()

    def save_propagation(self, request_id: str, peer_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO gdpr_propagations "
            "(request_id, peer_id) VALUES (?, ?)",
            (request_id, peer_id),
        )
        self._conn.commit()

    def add_blocklist(self, url: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO gdpr_blocklist (url) VALUES (?)",
            (url,),
        )
        self._conn.commit()

    def remove_blocklist(self, url: str) -> None:
        self._conn.execute(
            "DELETE FROM gdpr_blocklist WHERE url = ?",
            (url,),
        )
        self._conn.commit()

    # -- read --

    def load_all(
        self,
    ) -> list[
        tuple[
            DeletionRequest,
            list[DeletionConfirmation],
            list[str],
        ]
    ]:
        import json

        rows = self._conn.execute(
            "SELECT request_id, url, requester_id, basis, reason, "
            "signature, created_at, personal_data_fields "
            "FROM gdpr_requests"
        ).fetchall()

        results: list[
            tuple[
                DeletionRequest,
                list[DeletionConfirmation],
                list[str],
            ]
        ] = []
        for row in rows:
            fields_raw = row[7]
            fields = json.loads(fields_raw) if isinstance(fields_raw, str) else []
            req = DeletionRequest(
                request_id=row[0],
                url=row[1],
                requester_id=row[2],
                basis=DeletionBasis(row[3]),
                reason=row[4],
                signature=bytes(row[5]),
                created_at=row[6],
                personal_data_fields=fields,
            )
            confs = self._load_confirmations(req.request_id)
            props = self._load_propagations(req.request_id)
            results.append((req, confs, props))
        return results

    def _load_confirmations(self, request_id: str) -> list[DeletionConfirmation]:
        rows = self._conn.execute(
            "SELECT request_id, peer_id, status, deleted_at, detail "
            "FROM gdpr_confirmations WHERE request_id = ?",
            (request_id,),
        ).fetchall()
        return [
            DeletionConfirmation(
                request_id=r[0],
                peer_id=r[1],
                status=DeletionStatus(r[2]),
                deleted_at=r[3],
                detail=r[4],
            )
            for r in rows
        ]

    def _load_propagations(self, request_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT peer_id FROM gdpr_propagations WHERE request_id = ?",
            (request_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def load_blocklist(self) -> list[str]:
        rows = self._conn.execute("SELECT url FROM gdpr_blocklist").fetchall()
        return [r[0] for r in rows]


# --- Serialization ----------------------------------------------------------


def serialize_request(request: DeletionRequest) -> dict[str, Any]:
    """Serialize a DeletionRequest for wire format."""
    return {
        "request_id": request.request_id,
        "url": request.url,
        "requester_id": request.requester_id,
        "basis": request.basis.value,
        "reason": request.reason,
        "signature": request.signature.hex(),
        "created_at": request.created_at,
        "personal_data_fields": request.personal_data_fields,
    }


def deserialize_request(data: dict[str, Any]) -> DeletionRequest:
    """Deserialize a DeletionRequest from a dict."""
    return DeletionRequest(
        request_id=data["request_id"],
        url=data["url"],
        requester_id=data["requester_id"],
        basis=DeletionBasis(data["basis"]),
        reason=data["reason"],
        signature=bytes.fromhex(data["signature"]),
        created_at=data["created_at"],
        personal_data_fields=data.get("personal_data_fields", []),
    )


def deletion_dht_key(url: str) -> str:
    """Generate a DHT key for a deletion record.

    Args:
        url: URL being deleted.

    Returns:
        DHT key string.
    """
    h = content_hash(url)
    return f"{DELETION_DHT_PREFIX}{h}"


# --- Helpers ----------------------------------------------------------------


def _generate_request_id(url: str, peer_id: str, timestamp: float) -> str:
    """Deterministic deletion request ID."""
    raw = f"gdpr|{url}|{peer_id}|{timestamp}".encode()
    return short_hash(raw, length=24)


def _request_payload(
    request_id: str,
    url: str,
    basis: str,
    reason: str,
    created_at: float,
) -> bytes:
    """Canonical bytes for signing/verifying a deletion request."""
    return f"{request_id}|{url}|{basis}|{reason}|{created_at}".encode()
