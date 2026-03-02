"""Random audit system for content integrity verification.

~1 audit per hour per node.  3 audit nodes independently re-crawl a
random URL and compare ``content_hash`` against the original attestation.
Mismatch → trust penalty.  3× consecutive failures → network isolation.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog

from infomesh.hashing import content_hash, short_hash

if TYPE_CHECKING:
    from infomesh.trust.merkle import MerkleProof

logger = structlog.get_logger()

# --- Constants -------------------------------------------------------------

# Target audits per hour per node
AUDITS_PER_HOUR: float = 1.0

# Number of independent audit nodes per audit
AUDIT_NODES_PER_CHECK: int = 3

# Majority required to declare mismatch (≥ 2 of 3 must agree)
AUDIT_MAJORITY: int = 2

# Probation period for new nodes (higher audit frequency)
NEW_NODE_PROBATION_HOURS: float = 24.0

# Audit frequency multiplier for nodes on probation
PROBATION_AUDIT_MULTIPLIER: float = 3.0

# How old a node must be (in hours) before it can be an auditor
MIN_AUDITOR_AGE_HOURS: float = 24.0

# Maximum time allowed for an audit re-crawl (seconds)
AUDIT_TIMEOUT_SECONDS: float = 30.0


class AuditVerdict(StrEnum):
    """Outcome of a single audit."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"  # Couldn't complete (network error, etc.)
    INCONCLUSIVE = "inconclusive"  # Content changed legitimately


@dataclass(frozen=True)
class AuditRequest:
    """Request to audit a specific URL owned by a target peer."""

    audit_id: str
    target_peer_id: str
    url: str
    expected_text_hash: str
    expected_raw_hash: str
    requested_at: float
    auditor_peer_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AuditResult:
    """Result from a single auditor's re-crawl.

    The auditor **must** submit the hashes it obtained from its own
    independent re-crawl (``actual_text_hash``, ``actual_raw_hash``).
    Other auditors cross-compare these hashes to detect dishonest
    auditors that lie about pass/fail verdicts.
    """

    audit_id: str
    auditor_peer_id: str
    target_peer_id: str
    url: str
    actual_text_hash: str | None
    actual_raw_hash: str | None
    verdict: AuditVerdict
    detail: str
    completed_at: float
    # Signature over the canonical audit evidence (Ed25519).
    # Proves the auditor actually produced this result.
    auditor_signature: bytes = b""


@dataclass(frozen=True)
class AuditSummary:
    """Aggregated audit result from all auditors."""

    audit_id: str
    target_peer_id: str
    url: str
    results: list[AuditResult]
    final_verdict: AuditVerdict
    pass_count: int
    fail_count: int
    error_count: int
    # Auditors whose evidence hashes diverged from the majority.
    suspicious_auditors: list[str] = field(default_factory=list)


# --- Audit scheduler -------------------------------------------------------


class AuditScheduler:
    """Decides which URLs to audit and which peers should audit them.

    Maintains a schedule of pending and completed audits.
    """

    def __init__(self) -> None:
        self._pending: dict[str, AuditRequest] = {}
        self._completed: list[AuditSummary] = []
        self._results: dict[str, list[AuditResult]] = {}
        self._last_schedule_time: float = 0.0

    def should_schedule(self, *, now: float | None = None) -> bool:
        """Check if it's time to schedule a new audit.

        Target: AUDITS_PER_HOUR audits per hour.

        Args:
            now: Current timestamp.

        Returns:
            True if an audit should be scheduled now.
        """
        now = now or time.time()
        interval = 3600.0 / AUDITS_PER_HOUR
        return (now - self._last_schedule_time) >= interval

    def create_audit(
        self,
        target_peer_id: str,
        url: str,
        expected_text_hash: str,
        expected_raw_hash: str,
        available_auditors: list[str],
        *,
        now: float | None = None,
    ) -> AuditRequest | None:
        """Create an audit request selecting random auditors.

        Args:
            target_peer_id: Peer that originally crawled the URL.
            url: URL to re-crawl and verify.
            expected_text_hash: SHA-256 of the original extracted text.
            expected_raw_hash: SHA-256 of the original raw response.
            available_auditors: List of eligible auditor peer IDs
                (must exclude the target peer).
            now: Override timestamp.

        Returns:
            AuditRequest if enough auditors are available, else None.
        """
        # Filter out the target peer from potential auditors
        eligible = [p for p in available_auditors if p != target_peer_id]
        if len(eligible) < AUDIT_NODES_PER_CHECK:
            logger.warning(
                "audit_insufficient_auditors",
                target=target_peer_id,
                available=len(eligible),
                required=AUDIT_NODES_PER_CHECK,
            )
            return None

        now = now or time.time()
        selected = random.sample(eligible, AUDIT_NODES_PER_CHECK)
        audit_id = _generate_audit_id(target_peer_id, url, now)

        req = AuditRequest(
            audit_id=audit_id,
            target_peer_id=target_peer_id,
            url=url,
            expected_text_hash=expected_text_hash,
            expected_raw_hash=expected_raw_hash,
            requested_at=now,
            auditor_peer_ids=selected,
        )

        self._pending[audit_id] = req
        self._last_schedule_time = now

        logger.info(
            "audit_created",
            audit_id=audit_id,
            target=target_peer_id[:12],
            url=url,
            auditors=[p[:12] for p in selected],
        )
        return req

    def submit_result(self, result: AuditResult) -> AuditSummary | None:
        """Submit an individual auditor's result.

        When all AUDIT_NODES_PER_CHECK results are in, the audit is
        finalized and an AuditSummary is returned.

        Args:
            result: Result from a single auditor.

        Returns:
            AuditSummary if all auditors have reported, else None.
        """
        audit_id = result.audit_id
        if audit_id not in self._pending:
            logger.warning("audit_unknown", audit_id=audit_id)
            return None

        # Collect results
        self._results.setdefault(audit_id, []).append(result)

        if len(self._results[audit_id]) < AUDIT_NODES_PER_CHECK:
            return None  # Still waiting for more auditors

        # All results in — compute final verdict
        results = self._results.pop(audit_id)
        req = self._pending.pop(audit_id)

        pass_count = sum(1 for r in results if r.verdict == AuditVerdict.PASS)
        fail_count = sum(1 for r in results if r.verdict == AuditVerdict.FAIL)
        error_count = sum(1 for r in results if r.verdict == AuditVerdict.ERROR)

        # Cross-validate auditor evidence: detect dishonest auditors
        # whose hashes diverge from the majority.
        suspicious_auditors = _cross_validate_auditor_hashes(results)

        if fail_count >= AUDIT_MAJORITY:
            final = AuditVerdict.FAIL
        elif pass_count >= AUDIT_MAJORITY:
            final = AuditVerdict.PASS
        elif error_count >= AUDIT_MAJORITY:
            final = AuditVerdict.ERROR
        else:
            final = AuditVerdict.INCONCLUSIVE

        summary = AuditSummary(
            audit_id=audit_id,
            target_peer_id=req.target_peer_id,
            url=req.url,
            results=results,
            final_verdict=final,
            pass_count=pass_count,
            fail_count=fail_count,
            error_count=error_count,
            suspicious_auditors=suspicious_auditors,
        )

        self._completed.append(summary)
        logger.info(
            "audit_completed",
            audit_id=audit_id,
            target=req.target_peer_id[:12],
            verdict=final.value,
            pass_count=pass_count,
            fail_count=fail_count,
            suspicious_auditors=suspicious_auditors,
        )
        return summary

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    @property
    def completed_audits(self) -> list[AuditSummary]:
        return list(self._completed)


# --- Auditor logic ---------------------------------------------------------


def perform_audit_check(
    url: str,
    expected_text_hash: str,
    expected_raw_hash: str,
    *,
    actual_raw_body: bytes | None = None,
    actual_text: str | None = None,
    auditor_peer_id: str = "",
    audit_id: str = "",
    target_peer_id: str = "",
) -> AuditResult:
    """Perform the actual content verification for an audit.

    Called by auditor nodes after re-crawling the URL.

    Args:
        url: URL that was re-crawled.
        expected_text_hash: Hash from the original attestation.
        expected_raw_hash: Hash from the original attestation.
        actual_raw_body: Re-crawled raw body (if available).
        actual_text: Re-crawled extracted text (if available).
        auditor_peer_id: This auditor's peer ID.
        audit_id: Audit request ID.
        target_peer_id: Peer being audited.

    Returns:
        AuditResult with verdict.
    """
    now = time.time()

    if actual_text is None and actual_raw_body is None:
        return AuditResult(
            audit_id=audit_id,
            auditor_peer_id=auditor_peer_id,
            target_peer_id=target_peer_id,
            url=url,
            actual_text_hash=None,
            actual_raw_hash=None,
            verdict=AuditVerdict.ERROR,
            detail="no content available for verification",
            completed_at=now,
        )

    actual_text_hash = content_hash(actual_text) if actual_text else None
    actual_raw_hash = content_hash(actual_raw_body) if actual_raw_body else None

    # Check text hash (primary check)
    text_match = actual_text_hash == expected_text_hash if actual_text_hash else True
    raw_match = actual_raw_hash == expected_raw_hash if actual_raw_hash else True

    if text_match and raw_match:
        verdict = AuditVerdict.PASS
        detail = "content matches attestation"
    else:
        mismatches = []
        if not text_match:
            mismatches.append("text_hash_mismatch")
        if not raw_match:
            mismatches.append("raw_hash_mismatch")
        verdict = AuditVerdict.FAIL
        detail = "; ".join(mismatches)

    return AuditResult(
        audit_id=audit_id,
        auditor_peer_id=auditor_peer_id,
        target_peer_id=target_peer_id,
        url=url,
        actual_text_hash=actual_text_hash,
        actual_raw_hash=actual_raw_hash,
        verdict=verdict,
        detail=detail,
        completed_at=now,
    )


# --- Helpers ---------------------------------------------------------------


def perform_merkle_audit(
    document_hash: str,
    proof: MerkleProof,
    expected_root_hash: str,
    *,
    auditor_peer_id: str = "",
    audit_id: str = "",
    target_peer_id: str = "",
    url: str = "",
) -> AuditResult:
    """Verify a document's membership in a peer's Merkle tree.

    Instead of re-crawling, this audit mode asks the target peer to
    provide a Merkle proof for a specific document.  The auditor then
    verifies the proof against the expected root hash (published on DHT).

    Args:
        document_hash: The document's ``text_hash`` (pre leaf-hashing).
        proof: :class:`~infomesh.trust.merkle.MerkleProof` from the target peer.
        expected_root_hash: Root hash from the most recent DHT publication.
        auditor_peer_id: This auditor's peer ID.
        audit_id: Audit request ID.
        target_peer_id: Peer being audited.
        url: URL of the document (for logging / record keeping).

    Returns:
        :class:`AuditResult` with verdict.
    """
    from infomesh.trust.merkle import MerkleTree

    now = time.time()

    try:
        # Check 1: proof root matches expected root from DHT
        if proof.root_hash != expected_root_hash:
            return AuditResult(
                audit_id=audit_id,
                auditor_peer_id=auditor_peer_id,
                target_peer_id=target_peer_id,
                url=url,
                actual_text_hash=None,
                actual_raw_hash=None,
                verdict=AuditVerdict.FAIL,
                detail=(
                    "merkle_root_mismatch:"
                    f" proof_root={proof.root_hash[:16]}..."
                    f" expected={expected_root_hash[:16]}..."
                ),
                completed_at=now,
            )

        # Check 2: verify document membership via the proof path
        if not MerkleTree.verify_document(document_hash, proof):
            return AuditResult(
                audit_id=audit_id,
                auditor_peer_id=auditor_peer_id,
                target_peer_id=target_peer_id,
                url=url,
                actual_text_hash=document_hash,
                actual_raw_hash=None,
                verdict=AuditVerdict.FAIL,
                detail="merkle_proof_invalid: document not in tree",
                completed_at=now,
            )

        logger.info(
            "merkle_audit_pass",
            audit_id=audit_id,
            target=target_peer_id[:12] if target_peer_id else "",
            url=url,
        )
        return AuditResult(
            audit_id=audit_id,
            auditor_peer_id=auditor_peer_id,
            target_peer_id=target_peer_id,
            url=url,
            actual_text_hash=document_hash,
            actual_raw_hash=None,
            verdict=AuditVerdict.PASS,
            detail="merkle_proof_valid",
            completed_at=now,
        )

    except Exception as exc:
        logger.warning(
            "merkle_audit_error",
            audit_id=audit_id,
            error=str(exc),
        )
        return AuditResult(
            audit_id=audit_id,
            auditor_peer_id=auditor_peer_id,
            target_peer_id=target_peer_id,
            url=url,
            actual_text_hash=None,
            actual_raw_hash=None,
            verdict=AuditVerdict.ERROR,
            detail=f"merkle_audit_error: {exc}",
            completed_at=now,
        )


# --- Helpers ---------------------------------------------------------------


def _generate_audit_id(peer_id: str, url: str, timestamp: float) -> str:
    """Deterministic audit ID from peer + url + time."""
    raw = f"{peer_id}|{url}|{timestamp}".encode()
    return short_hash(raw, length=24)


# --- Auditor evidence cross-validation ------------------------------------


def _cross_validate_auditor_hashes(
    results: list[AuditResult],
) -> list[str]:
    """Identify auditors whose evidence hashes diverge from the majority.

    For each non-error result, collect the ``actual_text_hash``.
    The hash submitted by the majority is the "consensus".
    Any auditor whose hash differs is flagged as suspicious.

    Returns:
        List of ``auditor_peer_id`` values that diverged.
    """
    hash_votes: dict[str | None, list[str]] = {}
    for r in results:
        if r.verdict == AuditVerdict.ERROR:
            continue
        hash_votes.setdefault(r.actual_text_hash, []).append(
            r.auditor_peer_id,
        )

    if not hash_votes:
        return []

    # Find the consensus hash (most votes)
    consensus_hash = max(hash_votes, key=lambda h: len(hash_votes[h]))

    suspicious: list[str] = []
    for h, peers in hash_votes.items():
        if h != consensus_hash:
            suspicious.extend(peers)
    return suspicious


def audit_result_canonical(result: AuditResult) -> bytes:
    """Produce canonical bytes for signing an :class:`AuditResult`.

    The signed payload covers the audit ID, auditor, target, URL,
    both evidence hashes, the verdict, and the timestamp.  This
    prevents an auditor from changing its verdict after signing.
    """
    parts = [
        result.audit_id,
        result.auditor_peer_id,
        result.target_peer_id,
        result.url,
        result.actual_text_hash or "",
        result.actual_raw_hash or "",
        result.verdict.value,
        f"{result.completed_at:.6f}",
    ]
    return "|".join(parts).encode()
