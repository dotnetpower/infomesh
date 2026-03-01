"""Tests for infomesh.security_ext — extended security features."""

from __future__ import annotations

import tempfile
from pathlib import Path

from infomesh.security_ext import (
    AuditLog,
    IPFilter,
    Role,
    TLSConfig,
    check_role,
    sign_webhook_payload,
    verify_webhook_signature,
)


class TestIPFilter:
    def test_allow(self) -> None:
        filt = IPFilter(allowlist={"192.168.1.100"})
        assert filt.is_allowed("192.168.1.100")

    def test_deny_via_allowlist(self) -> None:
        filt = IPFilter(allowlist={"192.168.1.100"})
        assert not filt.is_allowed("10.0.0.1")

    def test_deny_via_blocklist(self) -> None:
        filt = IPFilter(blocklist={"10.0.0.1"})
        assert not filt.is_allowed("10.0.0.1")

    def test_default_allow(self) -> None:
        filt = IPFilter()
        # No rules = allow all
        assert filt.is_allowed("8.8.8.8")

    def test_add_and_check(self) -> None:
        filt = IPFilter()
        filt.add_block("1.2.3.4")
        assert not filt.is_allowed("1.2.3.4")


class TestRBACRole:
    def test_check_admin(self) -> None:
        assert check_role("search", Role.ADMIN) is True
        assert check_role("crawl_url", Role.ADMIN) is True

    def test_check_reader(self) -> None:
        assert check_role("search", Role.READER) is True
        assert check_role("crawl_url", Role.READER) is False

    def test_check_crawler(self) -> None:
        assert check_role("crawl_url", Role.CRAWLER) is True


class TestWebhookHMAC:
    def test_sign_and_verify(self) -> None:
        payload = {"event": "crawl_complete", "url": "https://example.com"}
        secret = "test_webhook_secret"
        sig = sign_webhook_payload(payload, secret)
        assert verify_webhook_signature(payload, sig, secret)

    def test_wrong_secret(self) -> None:
        payload = {"event": "test"}
        sig = sign_webhook_payload(payload, "key1")
        assert not verify_webhook_signature(payload, sig, "key2")


class TestAuditLog:
    def test_log_and_query(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            AuditLog(db_path=Path(tmp) / "audit.db") as audit,
        ):
            audit.log("search", arguments={"query": "python"})
            audit.log("crawl_url", arguments={"url": "https://example.com"})
            entries = audit.query(tool_name="search")
            assert len(entries) == 1

    def test_query_by_action(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            AuditLog(db_path=Path(tmp) / "audit.db") as audit,
        ):
            audit.log("search")
            audit.log("search")
            audit.log("crawl_url")
            entries = audit.query(tool_name="search")
            assert len(entries) == 2


class TestTLSConfig:
    def test_validate_missing_files(self) -> None:
        cfg = TLSConfig(
            enabled=True,
            cert_file="/nonexistent/cert.pem",
            key_file="/nonexistent/key.pem",
        )
        errors = cfg.validate()
        assert len(errors) > 0  # Should report missing files
