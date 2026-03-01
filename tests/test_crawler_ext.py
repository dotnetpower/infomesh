"""Tests for infomesh.crawler sub-modules.

Covers: pdf, rss, structured, diff, lang_detect, content_extract.
"""

from __future__ import annotations

from infomesh.crawler.content_extract import extract_code_blocks, extract_tables
from infomesh.crawler.diff import ContentDiff, compute_diff
from infomesh.crawler.lang_detect import detect_language
from infomesh.crawler.pdf import extract_pdf_text
from infomesh.crawler.rss import FeedResult, parse_feed_xml
from infomesh.crawler.structured import extract_structured_data


class TestPdfExtraction:
    def test_returns_none_for_empty(self) -> None:
        result = extract_pdf_text(b"")
        assert result is None or result == ""

    def test_returns_none_for_invalid(self) -> None:
        result = extract_pdf_text(b"not a pdf")
        assert result is None or result == ""


class TestRSSParsing:
    def test_parse_valid_rss(self) -> None:
        rss_xml = """<?xml version="1.0"?>
        <rss version="2.0">
          <channel>
            <title>Test Feed</title>
            <link>https://example.com</link>
            <item>
              <title>Article 1</title>
              <link>https://example.com/1</link>
              <description>First article content.</description>
            </item>
            <item>
              <title>Article 2</title>
              <link>https://example.com/2</link>
              <description>Second article content.</description>
            </item>
          </channel>
        </rss>"""
        feed = parse_feed_xml(rss_xml, "https://example.com/feed")
        assert isinstance(feed, FeedResult)
        assert feed.title == "Test Feed"
        assert len(feed.items) == 2

    def test_parse_empty(self) -> None:
        feed = parse_feed_xml("", "https://example.com/feed")
        assert len(feed.items) == 0


class TestStructuredData:
    def test_jsonld_extraction(self) -> None:
        html = """<html>
        <head><script type="application/ld+json">
        {"@type": "Article", "name": "Test Article"}
        </script></head>
        <body>Content</body></html>"""
        data = extract_structured_data(html)
        assert data is not None
        assert len(data.json_ld) > 0

    def test_no_structured_data(self) -> None:
        html = "<html><body>Plain HTML</body></html>"
        data = extract_structured_data(html)
        assert data is not None
        assert len(data.json_ld) == 0


class TestContentExtract:
    def test_extract_code_blocks(self) -> None:
        html = """<pre><code class="python">
def hello():
    print("world")
</code></pre>"""
        blocks = extract_code_blocks(html)
        assert len(blocks) >= 1
        assert "hello" in blocks[0].code

    def test_no_code_blocks(self) -> None:
        html = "<p>No code here.</p>"
        blocks = extract_code_blocks(html)
        assert blocks == []

    def test_extract_tables(self) -> None:
        html = """<table>
        <tr><th>Name</th><th>Value</th></tr>
        <tr><td>A</td><td>1</td></tr>
        <tr><td>B</td><td>2</td></tr>
        </table>"""
        tables = extract_tables(html)
        assert len(tables) >= 1
        assert len(tables[0].rows) >= 2


class TestContentDiffer:
    def test_same_content(self) -> None:
        result = compute_diff("Hello world", "Hello world", url="http://example.com")
        assert isinstance(result, ContentDiff)
        assert result.has_changed is False

    def test_content_changed(self) -> None:
        result = compute_diff(
            "Version 1 content", "Version 2 content", url="http://example.com"
        )
        assert result.has_changed is True
        assert result.change_ratio > 0.0


class TestLanguageDetection:
    def test_english(self) -> None:
        result = detect_language(
            "This is a sample text written in English "
            "with multiple sentences for detection."
        )
        assert result.language == "en"
        assert result.confidence > 0.0

    def test_korean(self) -> None:
        result = detect_language("이것은 한국어로 작성된 텍스트입니다.")
        assert result.language == "ko"

    def test_empty(self) -> None:
        result = detect_language("")
        assert result.language == "en"
        assert result.confidence == 0.0
