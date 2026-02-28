"""Tests for infomesh.trust.audit — random audit system."""

from __future__ import annotations

import time

import pytest

from infomesh.trust.audit import (
    AUDIT_NODES_PER_CHECK,
    AuditResult,
    AuditScheduler,
    AuditVerdict,
    perform_audit_check,
)


@pytest.fixture
def scheduler():
    return AuditScheduler()


# --- AuditScheduler --------------------------------------------------------


class TestAuditScheduler:
    def test_should_schedule_initially(self, scheduler):
        assert scheduler.should_schedule() is True

    def test_should_not_schedule_too_soon(self, scheduler):
        scheduler.create_audit(
            "target-1",
            "https://example.com",
            "abc",
            "def",
            ["a1", "a2", "a3", "a4"],
        )
        assert scheduler.should_schedule() is False

    def test_should_schedule_after_interval(self, scheduler):
        now = time.time()
        scheduler.create_audit(
            "target-1",
            "https://example.com",
            "abc",
            "def",
            ["a1", "a2", "a3", "a4"],
            now=now,
        )
        assert scheduler.should_schedule(now=now + 3601) is True

    def test_create_audit_success(self, scheduler):
        req = scheduler.create_audit(
            "target-1",
            "https://example.com",
            "text_hash",
            "raw_hash",
            ["a1", "a2", "a3", "a4", "a5"],
        )
        assert req is not None
        assert len(req.auditor_peer_ids) == AUDIT_NODES_PER_CHECK
        assert "target-1" not in req.auditor_peer_ids  # Target excluded

    def test_create_audit_insufficient_auditors(self, scheduler):
        req = scheduler.create_audit(
            "target-1",
            "https://example.com",
            "abc",
            "def",
            ["a1"],  # Only 1, need 3
        )
        assert req is None

    def test_target_excluded_from_auditors(self, scheduler):
        req = scheduler.create_audit(
            "target-1",
            "https://example.com",
            "abc",
            "def",
            ["target-1", "a1", "a2", "a3"],
        )
        assert req is not None
        assert "target-1" not in req.auditor_peer_ids

    def test_submit_results_majority_pass(self, scheduler):
        req = scheduler.create_audit(
            "target-1",
            "https://example.com",
            "abc",
            "def",
            ["a1", "a2", "a3", "a4"],
        )
        # Submit 3 passing results
        for i, auditor in enumerate(req.auditor_peer_ids):
            result = AuditResult(
                audit_id=req.audit_id,
                auditor_peer_id=auditor,
                target_peer_id="target-1",
                url="https://example.com",
                actual_text_hash="abc",
                actual_raw_hash="def",
                verdict=AuditVerdict.PASS,
                detail="ok",
                completed_at=time.time(),
            )
            summary = scheduler.submit_result(result)
            if i < AUDIT_NODES_PER_CHECK - 1:
                assert summary is None  # Still waiting
            else:
                assert summary is not None
                assert summary.final_verdict == AuditVerdict.PASS

    def test_submit_results_majority_fail(self, scheduler):
        req = scheduler.create_audit(
            "target-1",
            "https://example.com",
            "abc",
            "def",
            ["a1", "a2", "a3", "a4"],
        )
        verdicts = [AuditVerdict.FAIL, AuditVerdict.FAIL, AuditVerdict.PASS]
        summary = None
        for auditor, verdict in zip(req.auditor_peer_ids, verdicts, strict=False):
            result = AuditResult(
                audit_id=req.audit_id,
                auditor_peer_id=auditor,
                target_peer_id="target-1",
                url="https://example.com",
                actual_text_hash="xyz" if verdict == AuditVerdict.FAIL else "abc",
                actual_raw_hash="def",
                verdict=verdict,
                detail="test",
                completed_at=time.time(),
            )
            summary = scheduler.submit_result(result)

        assert summary is not None
        assert summary.final_verdict == AuditVerdict.FAIL
        assert summary.fail_count == 2

    def test_pending_count(self, scheduler):
        assert scheduler.pending_count == 0
        scheduler.create_audit(
            "t1",
            "https://a.com",
            "h1",
            "h2",
            ["a1", "a2", "a3", "a4"],
        )
        assert scheduler.pending_count == 1


# --- perform_audit_check ---------------------------------------------------


class TestPerformAuditCheck:
    def test_matching_content_passes(self):
        import hashlib

        text = "Hello world test content"
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        raw = b"<html>Hello world test content</html>"
        raw_hash = hashlib.sha256(raw).hexdigest()

        result = perform_audit_check(
            url="https://example.com",
            expected_text_hash=text_hash,
            expected_raw_hash=raw_hash,
            actual_raw_body=raw,
            actual_text=text,
        )
        assert result.verdict == AuditVerdict.PASS

    def test_mismatching_text_fails(self):
        result = perform_audit_check(
            url="https://example.com",
            expected_text_hash="expected_hash",
            expected_raw_hash="expected_raw",
            actual_text="different content",
        )
        assert result.verdict == AuditVerdict.FAIL
        assert "text_hash_mismatch" in result.detail

    def test_no_content_is_error(self):
        result = perform_audit_check(
            url="https://example.com",
            expected_text_hash="abc",
            expected_raw_hash="def",
        )
        assert result.verdict == AuditVerdict.ERROR

    def test_text_only_check(self):
        import hashlib

        text = "Test content"
        text_hash = hashlib.sha256(text.encode()).hexdigest()

        result = perform_audit_check(
            url="https://example.com",
            expected_text_hash=text_hash,
            expected_raw_hash="irrelevant",
            actual_text=text,
        )
        assert result.verdict == AuditVerdict.PASS
