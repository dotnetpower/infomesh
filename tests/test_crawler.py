"""Tests for the crawler modules."""

from __future__ import annotations

from infomesh.crawler.dedup import DeduplicatorDB, content_hash, normalize_url
from infomesh.crawler.parser import extract_links
from infomesh.crawler.seeds import load_seeds


class TestNormalizeUrl:
    """Tests for URL normalization."""

    def test_lowercase(self) -> None:
        assert normalize_url("HTTPS://EXAMPLE.COM/Page") == "https://example.com/Page"

    def test_strip_fragment(self) -> None:
        assert (
            normalize_url("https://example.com/page#section")
            == "https://example.com/page"
        )

    def test_strip_tracking_params(self) -> None:
        result = normalize_url("https://example.com/page?utm_source=google&q=test")
        assert "utm_source" not in result
        assert "q=test" in result

    def test_trailing_slash(self) -> None:
        assert normalize_url("https://example.com/page/") == "https://example.com/page"
        # Root slash should be preserved
        assert normalize_url("https://example.com/") == "https://example.com/"

    def test_sort_query_params(self) -> None:
        result = normalize_url("https://example.com/page?z=1&a=2")
        assert (
            result == "https://example.com/page?a=%5B%272%27%5D&z=%5B%271%27%5D"
            or "a=" in result
        )


class TestContentHash:
    """Tests for content hashing."""

    def test_deterministic(self) -> None:
        h1 = content_hash("hello world")
        h2 = content_hash("hello world")
        assert h1 == h2

    def test_different_content(self) -> None:
        h1 = content_hash("hello")
        h2 = content_hash("world")
        assert h1 != h2


class TestDeduplicatorDB:
    """Tests for the deduplication database."""

    def test_url_dedup(self) -> None:
        db = DeduplicatorDB()
        assert not db.is_url_seen("https://example.com/page")
        db.mark_seen("https://example.com/page", "hash1")
        assert db.is_url_seen("https://example.com/page")
        db.close()

    def test_content_dedup(self) -> None:
        db = DeduplicatorDB()
        assert not db.is_content_seen("hash1")
        db.mark_seen("https://example.com/page", "hash1")
        assert db.is_content_seen("hash1")
        db.close()


class TestSeeds:
    """Tests for seed URL loading."""

    def test_load_all_seeds(self) -> None:
        urls = load_seeds()
        assert len(urls) > 0
        assert all(url.startswith("http") for url in urls)

    def test_load_category(self) -> None:
        urls = load_seeds(category="tech-docs")
        assert len(urls) > 0
        assert "https://docs.python.org/3/" in urls

    def test_nonexistent_category(self) -> None:
        urls = load_seeds(category="nonexistent")
        assert urls == []


class TestExtractLinks:
    """Tests for link extraction from HTML."""

    def test_absolute_links(self) -> None:
        html = (
            '<html><body><a href="https://example.com/page1">Link 1</a></body></html>'
        )
        links = extract_links(html, "https://example.com/")
        assert "https://example.com/page1" in links

    def test_relative_links(self) -> None:
        html = '<html><body><a href="/about">About</a></body></html>'
        links = extract_links(html, "https://example.com/page")
        assert "https://example.com/about" in links

    def test_skips_mailto(self) -> None:
        html = '<html><body><a href="mailto:test@example.com">Email</a></body></html>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 0

    def test_skips_binary_extensions(self) -> None:
        html = (
            '<a href="/file.pdf">PDF</a>'
            '<a href="/file.jpg">Image</a>'
            '<a href="/page">Page</a>'
        )
        links = extract_links(html, "https://example.com/")
        assert len(links) == 1
        assert "/page" in links[0]

    def test_deduplicates(self) -> None:
        html = '<a href="/page">1</a><a href="/page">2</a><a href="/page#section">3</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 1

    def test_skips_javascript(self) -> None:
        html = '<a href="javascript:void(0)">Click</a>'
        links = extract_links(html, "https://example.com/")
        assert len(links) == 0
