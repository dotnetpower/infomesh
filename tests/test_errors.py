"""Tests for infomesh.errors — structured error codes."""

from __future__ import annotations

from infomesh.errors import (
    ERRORS,
    ErrorCategory,
    InfoMeshError,
    format_error,
    get_error,
)


class TestErrorCategory:
    def test_enum_values(self) -> None:
        assert ErrorCategory.AUTH == "AUTH"
        assert ErrorCategory.SEARCH == "SEARCH"
        assert ErrorCategory.CRAWL == "CRAWL"

    def test_all_unique(self) -> None:
        values = [e.value for e in ErrorCategory]
        assert len(values) == len(set(values))


class TestInfoMeshError:
    def test_basic(self) -> None:
        err = InfoMeshError(
            code="TEST_001",
            category=ErrorCategory.SEARCH,
            message="Query must not be empty",
            resolution="Provide a non-empty query",
        )
        assert err.code == "TEST_001"
        assert "empty" in err.message.lower()

    def test_to_dict(self) -> None:
        err = InfoMeshError(
            code="TEST_002",
            category=ErrorCategory.CRAWL,
            message="SSRF blocked",
            resolution="Use valid URL",
        )
        d = err.to_dict()
        assert "error" in d
        err_d = d["error"]
        assert isinstance(err_d, dict)
        assert err_d["code"] == "TEST_002"

    def test_format(self) -> None:
        err = InfoMeshError(
            code="TEST_003",
            category=ErrorCategory.AUTH,
            message="Invalid key",
            resolution="Fix the key",
        )
        s = err.format()
        assert "TEST_003" in s
        assert "Invalid key" in s


class TestErrorCatalog:
    def test_catalog_not_empty(self) -> None:
        assert len(ERRORS) >= 15

    def test_get_error(self) -> None:
        e = get_error("E001")
        assert e is not None
        assert e.category == ErrorCategory.AUTH

    def test_get_unknown(self) -> None:
        assert get_error("E999") is None

    def test_format_error(self) -> None:
        s = format_error("E003")
        assert "non-empty" in s.lower() or "query" in s.lower()

    def test_format_unknown(self) -> None:
        s = format_error("E999")
        assert "Unknown" in s
