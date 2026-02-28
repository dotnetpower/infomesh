"""Tests for security hardening — URL validation, input sanitization, etc."""

from __future__ import annotations

import pytest

from infomesh.security import SSRFError, validate_url

# ── URL Validator ───────────────────────────────────────────────────


class TestValidateUrl:
    """Test SSRF protection URL validator."""

    def test_valid_http_url(self) -> None:
        assert validate_url("http://example.com") == "http://example.com"

    def test_valid_https_url(self) -> None:
        assert (
            validate_url("https://example.com/path?q=1")
            == "https://example.com/path?q=1"
        )

    def test_rejects_empty(self) -> None:
        with pytest.raises(SSRFError):
            validate_url("")

    def test_rejects_none(self) -> None:
        with pytest.raises(SSRFError):
            validate_url(None)  # type: ignore[arg-type]

    def test_rejects_ftp_scheme(self) -> None:
        with pytest.raises(SSRFError, match="Scheme"):
            validate_url("ftp://example.com")

    def test_rejects_file_scheme(self) -> None:
        with pytest.raises(SSRFError, match="Scheme"):
            validate_url("file:///etc/passwd")

    def test_rejects_javascript_scheme(self) -> None:
        with pytest.raises(SSRFError, match="Scheme"):
            validate_url("javascript:alert(1)")

    def test_rejects_no_hostname(self) -> None:
        with pytest.raises(SSRFError, match="hostname"):
            validate_url("http://")

    def test_rejects_localhost(self) -> None:
        with pytest.raises(SSRFError, match="blocked pattern"):
            validate_url("http://localhost/admin")

    def test_rejects_127_0_0_1(self) -> None:
        with pytest.raises(SSRFError, match="private"):
            validate_url("http://127.0.0.1/admin")

    def test_rejects_10_network(self) -> None:
        with pytest.raises(SSRFError, match="private"):
            validate_url("http://10.0.0.1/secret")

    def test_rejects_172_16_network(self) -> None:
        with pytest.raises(SSRFError, match="private"):
            validate_url("http://172.16.0.1/internal")

    def test_rejects_192_168_network(self) -> None:
        with pytest.raises(SSRFError, match="private"):
            validate_url("http://192.168.1.1/router")

    def test_rejects_169_254_metadata(self) -> None:
        with pytest.raises(SSRFError, match="private|blocked"):
            validate_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_ipv6_loopback(self) -> None:
        with pytest.raises(SSRFError, match="private"):
            validate_url("http://[::1]/admin")

    def test_rejects_long_url(self) -> None:
        url = "https://example.com/" + "a" * 5000
        with pytest.raises(SSRFError, match="maximum length"):
            validate_url(url)

    def test_rejects_metadata_google(self) -> None:
        with pytest.raises(SSRFError, match="blocked"):
            validate_url("http://metadata.google.internal/")

    def test_allows_normal_domain(self) -> None:
        assert (
            validate_url("https://docs.python.org/3/library/")
            == "https://docs.python.org/3/library/"
        )


# ── FTS5 Query Sanitizer ───────────────────────────────────────────


class TestFtsSanitizer:
    """Test hardened FTS5 query sanitizer."""

    def test_strips_boolean_operators(self) -> None:
        from infomesh.search.query import _sanitize_fts_query

        result = _sanitize_fts_query("hello AND world OR NOT foo NEAR bar")
        # Boolean operators should be removed
        assert "AND" not in result.split()
        assert "OR" not in result.split()
        assert "NOT" not in result.split()
        assert "NEAR" not in result.split()

    def test_strips_special_chars(self) -> None:
        from infomesh.search.query import _sanitize_fts_query

        result = _sanitize_fts_query(
            'test "quoted" (grouped) {braced} *wild* ^column:value'
        )
        assert '"' not in result
        assert "(" not in result
        assert ")" not in result
        assert "{" not in result
        assert "*" not in result
        assert "^" not in result
        assert ":" not in result

    def test_preserves_normal_query(self) -> None:
        from infomesh.search.query import _sanitize_fts_query

        assert _sanitize_fts_query("python web scraping") == "python web scraping"

    def test_caps_query_length(self) -> None:
        from infomesh.search.query import _sanitize_fts_query

        long_query = "word " * 300  # ~1500 chars
        result = _sanitize_fts_query(long_query)
        assert len(result) <= 1000


# ── Decompression Bomb Protection ──────────────────────────────────


class TestDecompressionBomb:
    """Test zstd decompression size limits."""

    def test_decompress_with_default_limit(self) -> None:
        from infomesh.compression.zstd import Compressor

        c = Compressor()
        data = b"x" * 1000
        compressed = c.compress(data)
        assert c.decompress(compressed) == data

    def test_decompress_with_custom_limit(self) -> None:
        from infomesh.compression.zstd import Compressor

        c = Compressor()
        data = b"x" * 1000
        compressed = c.compress(data)
        assert c.decompress(compressed, max_output_size=2000) == data

    def test_decompress_rejects_exceeding_limit(self) -> None:
        import zstandard

        from infomesh.compression.zstd import Compressor

        c = Compressor()
        data = b"x" * 10000
        compressed = c.compress(data)
        with pytest.raises(zstandard.ZstdError):
            c.decompress(compressed, max_output_size=100)

    def test_decompress_text_with_limit(self) -> None:
        from infomesh.compression.zstd import Compressor

        c = Compressor()
        text = "hello world"
        compressed = c.compress_text(text)
        assert c.decompress_text(compressed, max_output_size=1000) == text


# ── Credit Ledger Security ─────────────────────────────────────────


class TestLedgerSecurity:
    """Test credit ledger input validation and atomicity."""

    def test_record_action_rejects_negative_quantity(self) -> None:
        from infomesh.credits.ledger import ActionType, CreditLedger

        ledger = CreditLedger()
        with pytest.raises(ValueError, match="positive"):
            ledger.record_action(ActionType.CRAWL, quantity=-1.0)
        ledger.close()

    def test_record_action_rejects_zero_quantity(self) -> None:
        from infomesh.credits.ledger import ActionType, CreditLedger

        ledger = CreditLedger()
        with pytest.raises(ValueError, match="positive"):
            ledger.record_action(ActionType.CRAWL, quantity=0.0)
        ledger.close()

    def test_spend_rejects_negative_amount(self) -> None:
        from infomesh.credits.ledger import ActionType, CreditLedger

        ledger = CreditLedger()
        ledger.record_action(ActionType.CRAWL, quantity=10.0)
        with pytest.raises(ValueError, match="positive"):
            ledger.spend(-5.0)
        ledger.close()

    def test_spend_rejects_zero_amount(self) -> None:
        from infomesh.credits.ledger import ActionType, CreditLedger

        ledger = CreditLedger()
        ledger.record_action(ActionType.CRAWL, quantity=10.0)
        with pytest.raises(ValueError, match="positive"):
            ledger.spend(0.0)
        ledger.close()

    def test_spend_insufficient_balance_enters_debt(self) -> None:
        """Spend always succeeds — debt is allowed (no credit card, no dollars)."""
        from infomesh.credits.ledger import ActionType, CreditLedger, CreditState

        ledger = CreditLedger()
        ledger.record_action(ActionType.CRAWL, quantity=1.0)
        assert ledger.spend(100.0) is True
        assert ledger.balance() < 0
        assert ledger.credit_state() == CreditState.GRACE
        ledger.close()


# ── SQL Injection Protection ───────────────────────────────────────


class TestSqlInjection:
    """Test tokenizer whitelist in LocalStore."""

    def test_rejects_invalid_tokenizer(self) -> None:
        from infomesh.index.local_store import LocalStore

        with pytest.raises(ValueError, match="Invalid tokenizer"):
            LocalStore(tokenizer="'); DROP TABLE documents;--")

    def test_accepts_valid_tokenizers(self) -> None:
        from infomesh.index.local_store import LocalStore

        for tok in ("unicode61", "ascii", "porter", "trigram"):
            store = LocalStore(tokenizer=tok)
            store.close()


# ── Seeds Path Traversal ──────────────────────────────────────────


class TestSeedsPathTraversal:
    """Test seed category validation."""

    def test_rejects_path_traversal(self) -> None:
        from infomesh.crawler.seeds import load_seeds

        result = load_seeds("../../etc/passwd")
        assert result == []

    def test_rejects_unknown_category(self) -> None:
        from infomesh.crawler.seeds import load_seeds

        result = load_seeds("nonexistent-category")
        assert result == []


# ── Config Validation ──────────────────────────────────────────────


class TestConfigValidation:
    """Test configuration value validation."""

    def test_clamps_out_of_range_values(self) -> None:
        from infomesh.config import _validate_value

        # Port too high
        assert _validate_value("listen_port", 99999) == 65535
        # Port too low
        assert _validate_value("listen_port", 0) == 1
        # Normal value passes through
        assert _validate_value("listen_port", 4001) == 4001

    def test_rejects_invalid_enum(self) -> None:
        from infomesh.config import _validate_value

        result = _validate_value("log_level", "INVALID")
        assert result is None

    def test_accepts_valid_enum(self) -> None:
        from infomesh.config import _validate_value

        assert _validate_value("log_level", "debug") == "debug"


# ── Truncate to Bytes ─────────────────────────────────────────────


class TestTruncateToBytes:
    """Test byte-aware text truncation."""

    def test_no_truncation_needed(self) -> None:
        from infomesh.services import _truncate_to_bytes

        assert _truncate_to_bytes("hello", 100) == "hello"

    def test_truncates_ascii(self) -> None:
        from infomesh.services import _truncate_to_bytes

        result = _truncate_to_bytes("abcdefghij", 5)
        assert len(result.encode("utf-8")) <= 5

    def test_truncates_multibyte_safely(self) -> None:
        from infomesh.services import _truncate_to_bytes

        text = "한글테스트"  # Each Korean char is 3 bytes
        result = _truncate_to_bytes(text, 6)
        # Should get at most 2 Korean chars (6 bytes)
        assert len(result.encode("utf-8")) <= 6
