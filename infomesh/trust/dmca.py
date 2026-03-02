"""DMCA takedown propagation mechanism.

Handles DMCA takedown requests via signed DHT records.  When a valid
takedown is received, the node must remove the content within 24 hours
and propagate the notice to peers hosting replicas.

Flow:
1. A takedown notice is created and signed by the requester.
2. The notice is published to the DHT under the URL's hash key.
3. Nodes periodically check for takedown notices for their hosted content.
4. On receipt, nodes remove the content from their local index and
   propagate to replica holders.

The system uses cryptographic signatures to prevent spoofed takedowns.
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

# Hours within which a node must comply with a takedown
COMPLIANCE_DEADLINE_HOURS: float = 24.0

# DHT key prefix for takedown records
TAKEDOWN_DHT_PREFIX: str = "/infomesh/takedown/"

# Maximum takedown notice text length
MAX_NOTICE_LENGTH: int = 10_000


class TakedownStatus(StrEnum):
    """Status of a takedown notice."""

    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    COMPLIED = "complied"
    EXPIRED = "expired"
    INVALID = "invalid"


@dataclass(frozen=True)
class TakedownNotice:
    """A DMCA takedown notice for a specific URL."""

    notice_id: str
    url: str
    requester_id: str  # Peer ID of the requester
    reason: str  # Takedown reason/description
    signature: bytes  # Signature over the notice payload
    created_at: float
    deadline: float  # Compliance deadline timestamp
    contact_info: str = ""  # Optional contact information


@dataclass(frozen=True)
class TakedownAck:
    """Acknowledgment of a takedown notice by a hosting node."""

    notice_id: str
    peer_id: str
    status: TakedownStatus
    complied_at: float | None = None
    detail: str = ""


@dataclass
class TakedownRecord:
    """Full record of a takedown including acknowledgments."""

    notice: TakedownNotice
    acknowledgments: list[TakedownAck] = field(default_factory=list)
    propagated_to: list[str] = field(default_factory=list)


# --- Takedown manager -------------------------------------------------------


class TakedownManager:
    """Manages DMCA takedown notices — creation, verification, compliance.

    Maintains a **persistent** registry of active takedowns backed by
    SQLite so records survive restarts.  A node cannot evade DMCA
    obligations by simply restarting.
    """

    # Rate limit: max notices per requester per hour
    MAX_NOTICES_PER_HOUR: int = 10

    def __init__(
        self,
        db_path: str | None = None,
    ) -> None:
        # In-memory caches loaded from / synced to SQLite
        self._records: dict[str, TakedownRecord] = {}
        self._url_takedowns: dict[str, str] = {}  # url → notice_id
        # Rate limiting: requester_id → list of creation timestamps
        self._rate_window: dict[str, list[float]] = {}

        # Persistent storage
        self._store: _TakedownStore | None = None
        if db_path is not None:
            self._store = _TakedownStore(db_path)
            self._load_from_store()

    def _check_rate_limit(self, requester_id: str, now: float) -> bool:
        """Return ``True`` if the requester is within rate limits."""
        window = self._rate_window.setdefault(requester_id, [])
        cutoff = now - 3600
        # Purge old entries
        self._rate_window[requester_id] = [t for t in window if t > cutoff]
        return len(self._rate_window[requester_id]) < self.MAX_NOTICES_PER_HOUR

    def create_notice(
        self,
        url: str,
        reason: str,
        key_pair: KeyPairLike,
        *,
        contact_info: str = "",
        now: float | None = None,
    ) -> TakedownNotice:
        """Create a signed takedown notice for a URL.

        Args:
            url: URL to take down.
            reason: Description/grounds for takedown.
            key_pair: Requester's key pair for signing.
            contact_info: Optional contact information.
            now: Override timestamp.

        Returns:
            Signed TakedownNotice.
        """
        now = now or time.time()

        # Rate limiting per requester
        if not self._check_rate_limit(key_pair.peer_id, now):
            raise ValueError(
                f"Rate limit exceeded: max {self.MAX_NOTICES_PER_HOUR} "
                f"takedown notices per hour"
            )

        deadline = now + COMPLIANCE_DEADLINE_HOURS * 3600

        notice_id = _generate_notice_id(url, key_pair.peer_id, now)
        payload = _notice_payload(notice_id, url, reason, now)
        signature = key_pair.sign(payload)

        notice = TakedownNotice(
            notice_id=notice_id,
            url=url,
            requester_id=key_pair.peer_id,
            reason=reason[:MAX_NOTICE_LENGTH],
            signature=signature,
            created_at=now,
            deadline=deadline,
            contact_info=contact_info,
        )

        self._records[notice_id] = TakedownRecord(notice=notice)
        self._url_takedowns[url] = notice_id
        self._rate_window.setdefault(key_pair.peer_id, []).append(now)
        self._persist_notice(notice)

        logger.info(
            "takedown_created",
            notice_id=notice_id,
            url=url,
            requester=key_pair.peer_id[:12],
        )
        return notice

    def verify_notice(
        self,
        notice: TakedownNotice,
        key_pair: KeyPairLike,
    ) -> bool:
        """Verify the signature on a takedown notice.

        Args:
            notice: The notice to verify.
            key_pair: Key pair with the requester's public key.

        Returns:
            True if the signature is valid.
        """
        payload = _notice_payload(
            notice.notice_id, notice.url, notice.reason, notice.created_at
        )
        return key_pair.verify(payload, notice.signature)

    def acknowledge(
        self,
        notice_id: str,
        peer_id: str,
        *,
        status: TakedownStatus = TakedownStatus.ACKNOWLEDGED,
        now: float | None = None,
    ) -> TakedownAck | None:
        """Record acknowledgment of a takedown notice.

        Args:
            notice_id: The notice being acknowledged.
            peer_id: The peer acknowledging.
            status: Current compliance status.
            now: Override timestamp.

        Returns:
            TakedownAck or None if notice not found.
        """
        record = self._records.get(notice_id)
        if record is None:
            logger.warning("takedown_unknown_notice", notice_id=notice_id)
            return None

        now = now or time.time()
        ack = TakedownAck(
            notice_id=notice_id,
            peer_id=peer_id,
            status=status,
            complied_at=now if status == TakedownStatus.COMPLIED else None,
            detail=f"acknowledged at {now:.0f}",
        )
        record.acknowledgments.append(ack)
        self._persist_ack(ack)

        logger.info(
            "takedown_acknowledged",
            notice_id=notice_id,
            peer_id=peer_id[:12],
            status=status.value,
        )
        return ack

    def mark_complied(
        self,
        notice_id: str,
        peer_id: str,
        *,
        now: float | None = None,
    ) -> TakedownAck | None:
        """Mark a takedown as complied by a peer.

        Args:
            notice_id: The notice being complied with.
            peer_id: The complying peer.
            now: Override timestamp.

        Returns:
            TakedownAck or None if notice not found.
        """
        return self.acknowledge(
            notice_id, peer_id, status=TakedownStatus.COMPLIED, now=now
        )

    def record_propagation(self, notice_id: str, peer_id: str) -> None:
        """Record that a takedown was propagated to a peer."""
        record = self._records.get(notice_id)
        if record and peer_id not in record.propagated_to:
            record.propagated_to.append(peer_id)
            self._persist_propagation(notice_id, peer_id)

    def is_taken_down(self, url: str) -> bool:
        """Check if a URL has an active takedown notice."""
        return url in self._url_takedowns

    def get_notice_for_url(self, url: str) -> TakedownNotice | None:
        """Get the takedown notice for a URL, if any."""
        notice_id = self._url_takedowns.get(url)
        if notice_id and notice_id in self._records:
            return self._records[notice_id].notice
        return None

    def get_record(self, notice_id: str) -> TakedownRecord | None:
        """Get the full takedown record."""
        return self._records.get(notice_id)

    def check_compliance(
        self,
        notice_id: str,
        peer_id: str,
        *,
        now: float | None = None,
    ) -> TakedownStatus:
        """Check whether a peer has complied with a takedown.

        Args:
            notice_id: The takedown notice ID.
            peer_id: The peer to check.
            now: Override timestamp.

        Returns:
            TakedownStatus indicating compliance state.
        """
        record = self._records.get(notice_id)
        if record is None:
            return TakedownStatus.INVALID

        now = now or time.time()

        # Check if the peer has acknowledged
        for ack in record.acknowledgments:
            if ack.peer_id == peer_id:
                if ack.status == TakedownStatus.COMPLIED:
                    return TakedownStatus.COMPLIED
                return ack.status

        # Not acknowledged — check if deadline passed
        if now > record.notice.deadline:
            return TakedownStatus.EXPIRED

        return TakedownStatus.PENDING

    def list_active(self) -> list[TakedownNotice]:
        """List all active takedown notices."""
        return [r.notice for r in self._records.values()]

    def list_non_compliant(
        self,
        peer_id: str,
        *,
        now: float | None = None,
    ) -> list[TakedownNotice]:
        """List takedowns where a peer has not yet complied."""
        now = now or time.time()
        result = []
        for record in self._records.values():
            status = TakedownStatus.PENDING
            for ack in record.acknowledgments:
                if ack.peer_id == peer_id:
                    status = ack.status
                    break
            if status not in (TakedownStatus.COMPLIED, TakedownStatus.INVALID):
                result.append(record.notice)
        return result

    def _load_from_store(self) -> None:
        """Load all notices and acks from SQLite into memory caches."""
        if self._store is None:
            return
        for notice, acks, propagated in self._store.load_all():
            self._records[notice.notice_id] = TakedownRecord(
                notice=notice,
                acknowledgments=list(acks),
                propagated_to=list(propagated),
            )
            self._url_takedowns[notice.url] = notice.notice_id

    def _persist_notice(self, notice: TakedownNotice) -> None:
        """Write a new notice to persistent store."""
        if self._store is None:
            return
        self._store.save_notice(notice)

    def _persist_ack(self, ack: TakedownAck) -> None:
        """Write an acknowledgment to persistent store."""
        if self._store is None:
            return
        self._store.save_ack(ack)

    def _persist_propagation(self, notice_id: str, peer_id: str) -> None:
        """Write a propagation record to persistent store."""
        if self._store is None:
            return
        self._store.save_propagation(notice_id, peer_id)

    def close(self) -> None:
        """Close the underlying store, if any."""
        if self._store is not None:
            self._store.close()


# --- Persistent store -------------------------------------------------------


class _TakedownStore(SQLiteStore):
    """SQLite-backed DMCA record persistence."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS dmca_notices (
            notice_id   TEXT PRIMARY KEY,
            url         TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            reason      TEXT NOT NULL,
            signature   BLOB NOT NULL,
            created_at  REAL NOT NULL,
            deadline    REAL NOT NULL,
            contact_info TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_dmca_url
            ON dmca_notices(url);

        CREATE TABLE IF NOT EXISTS dmca_acks (
            notice_id   TEXT NOT NULL,
            peer_id     TEXT NOT NULL,
            status      TEXT NOT NULL,
            complied_at REAL,
            detail      TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (notice_id, peer_id, status)
        );

        CREATE TABLE IF NOT EXISTS dmca_propagations (
            notice_id   TEXT NOT NULL,
            peer_id     TEXT NOT NULL,
            PRIMARY KEY (notice_id, peer_id)
        );
    """

    # -- write helpers --

    def save_notice(self, notice: TakedownNotice) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO dmca_notices "
            "(notice_id, url, requester_id, reason, signature, "
            "created_at, deadline, contact_info) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                notice.notice_id,
                notice.url,
                notice.requester_id,
                notice.reason,
                notice.signature,
                notice.created_at,
                notice.deadline,
                notice.contact_info,
            ),
        )
        self._conn.commit()

    def save_ack(self, ack: TakedownAck) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO dmca_acks "
            "(notice_id, peer_id, status, complied_at, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                ack.notice_id,
                ack.peer_id,
                ack.status.value,
                ack.complied_at,
                ack.detail,
            ),
        )
        self._conn.commit()

    def save_propagation(self, notice_id: str, peer_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO dmca_propagations "
            "(notice_id, peer_id) VALUES (?, ?)",
            (notice_id, peer_id),
        )
        self._conn.commit()

    # -- read helpers --

    def load_all(
        self,
    ) -> list[tuple[TakedownNotice, list[TakedownAck], list[str]]]:
        """Return all notices with their acks and propagation lists."""
        rows = self._conn.execute(
            "SELECT notice_id, url, requester_id, reason, "
            "signature, created_at, deadline, contact_info "
            "FROM dmca_notices"
        ).fetchall()

        results: list[tuple[TakedownNotice, list[TakedownAck], list[str]]] = []
        for row in rows:
            notice = TakedownNotice(
                notice_id=row[0],
                url=row[1],
                requester_id=row[2],
                reason=row[3],
                signature=bytes(row[4]),
                created_at=row[5],
                deadline=row[6],
                contact_info=row[7],
            )
            acks = self._load_acks(notice.notice_id)
            props = self._load_propagations(notice.notice_id)
            results.append((notice, acks, props))
        return results

    def _load_acks(self, notice_id: str) -> list[TakedownAck]:
        rows = self._conn.execute(
            "SELECT notice_id, peer_id, status, complied_at, detail "
            "FROM dmca_acks WHERE notice_id = ?",
            (notice_id,),
        ).fetchall()
        return [
            TakedownAck(
                notice_id=r[0],
                peer_id=r[1],
                status=TakedownStatus(r[2]),
                complied_at=r[3],
                detail=r[4],
            )
            for r in rows
        ]

    def _load_propagations(self, notice_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT peer_id FROM dmca_propagations WHERE notice_id = ?",
            (notice_id,),
        ).fetchall()
        return [r[0] for r in rows]


# --- Serialization ----------------------------------------------------------


def serialize_notice(notice: TakedownNotice) -> dict[str, Any]:
    """Serialize a TakedownNotice for wire format."""
    return {
        "notice_id": notice.notice_id,
        "url": notice.url,
        "requester_id": notice.requester_id,
        "reason": notice.reason,
        "signature": notice.signature.hex(),
        "created_at": notice.created_at,
        "deadline": notice.deadline,
        "contact_info": notice.contact_info,
    }


def deserialize_notice(data: dict[str, Any]) -> TakedownNotice:
    """Deserialize a TakedownNotice from a dict."""
    return TakedownNotice(
        notice_id=data["notice_id"],
        url=data["url"],
        requester_id=data["requester_id"],
        reason=data["reason"],
        signature=bytes.fromhex(data["signature"]),
        created_at=data["created_at"],
        deadline=data["deadline"],
        contact_info=data.get("contact_info", ""),
    )


# --- Helpers ----------------------------------------------------------------


def takedown_dht_key(url: str) -> str:
    """Generate a DHT key for a takedown notice.

    Args:
        url: URL being taken down.

    Returns:
        DHT key string.
    """
    h = content_hash(url)
    return f"{TAKEDOWN_DHT_PREFIX}{h}"


def _generate_notice_id(url: str, peer_id: str, timestamp: float) -> str:
    """Deterministic notice ID."""
    raw = f"takedown|{url}|{peer_id}|{timestamp}".encode()
    return short_hash(raw, length=24)


def _notice_payload(notice_id: str, url: str, reason: str, created_at: float) -> bytes:
    """Canonical bytes for signing/verifying a takedown notice."""
    return f"{notice_id}|{url}|{reason}|{created_at}".encode()
