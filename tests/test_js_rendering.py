"""Tests for JavaScript detection and rendering modules."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from infomesh.crawler.js_detect import (
    JSDetectionResult,
    _body_text_length,
    _text_to_html_ratio,
    detect_js_requirement,
)
from infomesh.crawler.js_render import JSRenderer, RenderResult, is_playwright_available

# ── JS Detection Tests ───────────────────────────────────────────────────


class TestDetectJSRequirement:
    """Test detect_js_requirement() with various HTML patterns."""

    def test_static_html_not_js_required(self) -> None:
        """A normal static HTML page should NOT be flagged as JS-required."""
        html = """
        <html><head><title>Hello</title></head>
        <body>
            <h1>Welcome to our site</h1>
            <p>This is a regular static page with plenty of content.
            It has paragraphs and text that can be extracted by
            trafilatura without any JavaScript rendering needed.
            The text-to-HTML ratio is reasonable and the body is not empty.</p>
            <p>More content here to ensure a good ratio of text to markup.</p>
        </body></html>
        """
        result = detect_js_requirement(html)
        assert not result.js_required
        assert result.confidence < 0.5
        assert isinstance(result.signals, list)

    def test_react_spa_detected(self) -> None:
        """React SPA with empty #root div should be flagged."""
        html = """
        <html><head><title>React App</title></head>
        <body>
            <div id="root"></div>
            <script src="/static/js/bundle.js"></script>
        </body></html>
        """
        result = detect_js_requirement(html)
        assert result.js_required
        assert result.confidence >= 0.5
        assert any("SPA root" in s for s in result.signals)

    def test_nextjs_detected(self) -> None:
        """Next.js pages with __next div and __NEXT_DATA__ should be flagged."""
        html = """
        <html><head><title>Next App</title></head>
        <body>
            <div id="__next"></div>
            <script id="__NEXT_DATA__" type="application/json">
            {"props":{}}
            </script>
        </body></html>
        """
        result = detect_js_requirement(html)
        assert result.js_required
        assert result.confidence >= 0.5

    def test_noscript_fallback_detected(self) -> None:
        """Pages with 'enable JavaScript' in noscript should be flagged."""
        html = """
        <html><head><title>JS App</title></head>
        <body>
            <noscript>You need to enable JavaScript to run this app.</noscript>
            <div id="app"></div>
        </body></html>
        """
        result = detect_js_requirement(html)
        assert result.js_required
        assert any("Noscript" in s for s in result.signals)

    def test_vue_app_detected(self) -> None:
        """Vue.js with #app mount point should contribute to score."""
        html = """
        <html><head><title>Vue</title></head>
        <body><div id="app"></div>
        <script src="/js/app.js"></script></body></html>
        """
        result = detect_js_requirement(html)
        # Vue app with empty body should be detected
        assert result.js_required
        assert result.confidence >= 0.5

    def test_nuxt_detected(self) -> None:
        """Nuxt.js with __nuxt div and __NUXT__ data should be flagged."""
        html = """
        <html><head><title>Nuxt</title></head>
        <body>
            <div id="__nuxt"></div>
            <script>window.__NUXT__={}</script>
        </body></html>
        """
        result = detect_js_requirement(html)
        assert result.js_required

    def test_empty_body_high_confidence(self) -> None:
        """Completely empty body should get high score."""
        html = "<html><head><title>X</title></head><body></body></html>"
        result = detect_js_requirement(html)
        # Empty body + low ratio should push confidence high
        assert result.confidence >= 0.3

    def test_confidence_capped_at_1(self) -> None:
        """Multiple signals should not exceed confidence of 1.0."""
        html = """
        <html><head><title>All Signals</title></head>
        <body>
            <noscript>Please enable JavaScript to use this app.</noscript>
            <div id="root"></div>
            <script id="__NEXT_DATA__">{"p":{}}</script>
        </body></html>
        """
        result = detect_js_requirement(html)
        assert result.confidence <= 1.0

    def test_result_dataclass(self) -> None:
        """JSDetectionResult should be a frozen dataclass."""
        result = JSDetectionResult(
            js_required=True,
            confidence=0.75,
            signals=["test signal"],
        )
        assert result.js_required
        assert result.confidence == 0.75
        assert result.signals == ["test signal"]


class TestTextToHTMLRatio:
    """Test the text-to-HTML ratio helper."""

    def test_empty_html(self) -> None:
        assert _text_to_html_ratio("") == 0.0

    def test_pure_text(self) -> None:
        """Plain text should have ratio close to 1.0."""
        ratio = _text_to_html_ratio("hello world")
        assert ratio > 0.9

    def test_heavy_markup(self) -> None:
        """Lots of tags with little text should give low ratio."""
        html = "<div>" * 100 + "hi" + "</div>" * 100
        ratio = _text_to_html_ratio(html)
        assert ratio < 0.05

    def test_scripts_excluded(self) -> None:
        """Script content should not count as visible text."""
        html = (
            "<html><body>"
            "<p>Hello</p>"
            "<script>var x = 'lots of js code here';</script>"
            "</body></html>"
        )
        ratio = _text_to_html_ratio(html)
        # Script is stripped, so ratio is just "Hello" vs total HTML
        assert 0 < ratio < 0.5


class TestBodyTextLength:
    """Test the body text length helper."""

    def test_no_body(self) -> None:
        assert _body_text_length("<html><head></head></html>") == 0

    def test_body_with_text(self) -> None:
        html = "<html><body><p>Hello World</p></body></html>"
        length = _body_text_length(html)
        assert length == len("Hello World")

    def test_body_with_scripts(self) -> None:
        """Scripts should be stripped from body text measurement."""
        html = "<html><body><script>var x=1;</script><p>Text</p></body></html>"
        length = _body_text_length(html)
        assert length == len("Text")

    def test_empty_body(self) -> None:
        html = "<html><body></body></html>"
        assert _body_text_length(html) == 0


# ── JS Render Tests ──────────────────────────────────────────────────────


class TestRenderResult:
    """Test the RenderResult dataclass."""

    def test_success_result(self) -> None:
        r = RenderResult(success=True, html="<html>rendered</html>")
        assert r.success
        assert "rendered" in r.html
        assert r.error is None

    def test_failure_result(self) -> None:
        r = RenderResult(
            success=False,
            html="",
            error="timeout",
            elapsed_ms=5000.0,
        )
        assert not r.success
        assert r.error == "timeout"


class TestIsPlaywrightAvailable:
    """Test playwright availability check."""

    def test_returns_bool(self) -> None:
        """Should return a boolean (likely False in test env)."""
        result = is_playwright_available()
        assert isinstance(result, bool)


class TestJSRenderer:
    """Test JSRenderer with mocked Playwright."""

    def test_render_no_playwright(self) -> None:
        """Without Playwright, render should return failure."""
        renderer = JSRenderer()
        with patch(
            "infomesh.crawler.js_render.is_playwright_available",
            return_value=False,
        ):
            result = asyncio.run(renderer.render("https://example.com"))
        assert not result.success
        assert result.error == "playwright_not_installed"

    def test_init_defaults(self) -> None:
        """Default values should match the spec."""
        renderer = JSRenderer()
        assert renderer._max_tabs == 3
        assert renderer._timeout_ms == 30_000
        assert renderer._max_memory_mb == 512

    def test_custom_config(self) -> None:
        """Custom values should be stored."""
        renderer = JSRenderer(
            max_tabs=5,
            timeout_ms=15_000,
            max_memory_mb=256,
        )
        assert renderer._max_tabs == 5
        assert renderer._timeout_ms == 15_000
        assert renderer._max_memory_mb == 256


# ── Worker Integration Tests ─────────────────────────────────────────────


class TestCrawlResultJSFields:
    """Test that CrawlResult includes JS fields."""

    def test_default_values(self) -> None:
        from infomesh.crawler.worker import CrawlResult

        r = CrawlResult(url="https://example.com", success=True)
        assert r.js_required is False
        assert r.js_rendered is False

    def test_js_fields_set(self) -> None:
        from infomesh.crawler.worker import CrawlResult

        r = CrawlResult(
            url="https://example.com",
            success=True,
            js_required=True,
            js_rendered=True,
        )
        assert r.js_required is True
        assert r.js_rendered is True


class TestCrawlConfigJSFields:
    """Test CrawlConfig JS rendering fields."""

    def test_defaults(self) -> None:
        from infomesh.config import CrawlConfig

        cfg = CrawlConfig()
        assert cfg.js_rendering is False
        assert cfg.js_max_tabs == 3
        assert cfg.js_timeout_ms == 30_000
        assert cfg.js_max_memory_mb == 512

    def test_custom_values(self) -> None:
        from infomesh.config import CrawlConfig

        cfg = CrawlConfig(
            js_rendering=True,
            js_max_tabs=5,
            js_timeout_ms=15_000,
        )
        assert cfg.js_rendering is True
        assert cfg.js_max_tabs == 5
        assert cfg.js_timeout_ms == 15_000


class TestLocalStoreJSRequired:
    """Test that LocalStore handles js_required column."""

    def test_add_document_with_js_required(self, tmp_path: object) -> None:
        """Documents can be stored with js_required flag."""
        from pathlib import Path

        from infomesh.index.local_store import LocalStore

        db_path = Path(str(tmp_path)) / "test.db"
        store = LocalStore(db_path=db_path)
        doc_id = store.add_document(
            url="https://example.com/spa",
            title="SPA Page",
            text="Rendered content from JavaScript " * 5,
            raw_html_hash="abc123",
            text_hash="def456",
            js_required=True,
        )
        assert doc_id is not None

        # Verify the column was set
        row = store._conn.execute(
            "SELECT js_required FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        assert row["js_required"] == 1

        store.close()

    def test_add_document_default_not_js(self, tmp_path: object) -> None:
        """Default js_required should be False/0."""
        from pathlib import Path

        from infomesh.index.local_store import LocalStore

        db_path = Path(str(tmp_path)) / "test.db"
        store = LocalStore(db_path=db_path)
        doc_id = store.add_document(
            url="https://example.com/static",
            title="Static Page",
            text="Normal static content here " * 5,
            raw_html_hash="xyz789",
            text_hash="uvw012",
        )
        assert doc_id is not None

        row = store._conn.execute(
            "SELECT js_required FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        assert row["js_required"] == 0

        store.close()

    def test_get_js_required_domains(self, tmp_path: object) -> None:
        """get_js_required_domains returns correct stats."""
        from pathlib import Path

        from infomesh.index.local_store import LocalStore

        db_path = Path(str(tmp_path)) / "test.db"
        store = LocalStore(db_path=db_path)

        # Add JS-required docs
        store.add_document(
            url="https://spa.example.com/page1",
            title="SPA 1",
            text="Content " * 20,
            raw_html_hash="h1",
            text_hash="t1",
            js_required=True,
        )
        store.add_document(
            url="https://spa.example.com/page2",
            title="SPA 2",
            text="Content " * 20,
            raw_html_hash="h2",
            text_hash="t2",
            js_required=True,
        )
        # Add static doc
        store.add_document(
            url="https://static.example.com/page1",
            title="Static",
            text="Static content " * 20,
            raw_html_hash="h3",
            text_hash="t3",
            js_required=False,
        )

        domains = store.get_js_required_domains()
        assert len(domains) == 1
        domain, js_cnt, total = domains[0]
        assert domain == "spa.example.com"
        assert js_cnt == 2
        assert total == 2

        store.close()
