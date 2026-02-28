"""Tests for adaptive auto-recrawl scheduler."""

from __future__ import annotations

import hashlib
import time

import httpx
import pytest

from infomesh.crawler.recrawl import (
    INTERVAL_HIGH,
    INTERVAL_LOW,
    INTERVAL_MEDIUM,
    INTERVAL_STATIC,
    STALE_THRESHOLD,
    RecrawlCandidate,
    compute_recrawl_interval,
    recrawl_url,
    select_candidates,
    update_change_frequency,
)

# ── compute_recrawl_interval ────────────────────────────────────────────


class TestComputeRecrawlInterval:
    def test_zero_change_gives_static(self) -> None:
        assert compute_recrawl_interval(0.0) == INTERVAL_STATIC

    def test_negative_gives_static(self) -> None:
        assert compute_recrawl_interval(-0.1) == INTERVAL_STATIC

    def test_low_change_gives_low(self) -> None:
        assert compute_recrawl_interval(0.05) == INTERVAL_LOW

    def test_boundary_0_10_gives_medium(self) -> None:
        assert compute_recrawl_interval(0.10) == INTERVAL_MEDIUM

    def test_mid_change_gives_medium(self) -> None:
        assert compute_recrawl_interval(0.30) == INTERVAL_MEDIUM

    def test_boundary_0_50_gives_medium(self) -> None:
        assert compute_recrawl_interval(0.50) == INTERVAL_MEDIUM

    def test_high_change_gives_high(self) -> None:
        assert compute_recrawl_interval(0.80) == INTERVAL_HIGH

    def test_max_change_gives_high(self) -> None:
        assert compute_recrawl_interval(1.0) == INTERVAL_HIGH


# ── update_change_frequency ─────────────────────────────────────────────


class TestUpdateChangeFrequency:
    def test_from_zero_with_change(self) -> None:
        result = update_change_frequency(0.0, True)
        assert result == pytest.approx(0.3)  # alpha * 1.0

    def test_from_zero_no_change(self) -> None:
        result = update_change_frequency(0.0, False)
        assert result == 0.0

    def test_from_one_with_change(self) -> None:
        result = update_change_frequency(1.0, True)
        assert result == pytest.approx(1.0)

    def test_from_one_no_change(self) -> None:
        result = update_change_frequency(1.0, False)
        assert result == pytest.approx(0.7)

    def test_custom_alpha(self) -> None:
        result = update_change_frequency(0.5, True, alpha=0.5)
        assert result == pytest.approx(0.75)

    def test_converges_to_one(self) -> None:
        freq = 0.0
        for _ in range(50):
            freq = update_change_frequency(freq, True)
        assert freq > 0.99

    def test_converges_to_zero(self) -> None:
        freq = 1.0
        for _ in range(50):
            freq = update_change_frequency(freq, False)
        assert freq < 0.01


# ── RecrawlCandidate ────────────────────────────────────────────────────


def _make_candidate(
    *,
    doc_id: int = 1,
    url: str = "https://example.com",
    recrawl_interval: int = INTERVAL_MEDIUM,
    crawled_at: float | None = None,
    last_recrawl_at: float | None = None,
    stale_count: int = 0,
    change_frequency: float = 0.0,
) -> RecrawlCandidate:
    now = time.time()
    return RecrawlCandidate(
        doc_id=doc_id,
        url=url,
        text_hash="abc123",
        etag=None,
        last_modified=None,
        recrawl_interval=recrawl_interval,
        stale_count=stale_count,
        change_frequency=change_frequency,
        crawled_at=crawled_at or now,
        last_recrawl_at=last_recrawl_at,
    )


# ── select_candidates ──────────────────────────────────────────────────


class TestSelectCandidates:
    def test_empty_list(self) -> None:
        assert select_candidates([]) == []

    def test_not_overdue(self) -> None:
        now = time.time()
        doc = _make_candidate(crawled_at=now, recrawl_interval=86400)
        result = select_candidates([doc], now=now)
        assert result == []

    def test_overdue_selected(self) -> None:
        now = time.time()
        doc = _make_candidate(
            crawled_at=now - 100_000,
            recrawl_interval=86400,
        )
        result = select_candidates([doc], now=now)
        assert len(result) == 1
        assert result[0].url == "https://example.com"

    def test_max_batch_respected(self) -> None:
        now = time.time()
        docs = [
            _make_candidate(
                doc_id=i,
                url=f"https://example.com/{i}",
                crawled_at=now - 200_000,
                recrawl_interval=86400,
            )
            for i in range(10)
        ]
        result = select_candidates(docs, now=now, max_batch=3)
        assert len(result) == 3

    def test_most_overdue_first(self) -> None:
        now = time.time()
        old = _make_candidate(
            doc_id=1,
            url="https://old.com",
            crawled_at=now - 500_000,
            recrawl_interval=86400,
        )
        recent = _make_candidate(
            doc_id=2,
            url="https://recent.com",
            crawled_at=now - 100_000,
            recrawl_interval=86400,
        )
        result = select_candidates([recent, old], now=now)
        assert result[0].url == "https://old.com"

    def test_last_recrawl_at_used_when_set(self) -> None:
        now = time.time()
        doc = _make_candidate(
            crawled_at=now - 500_000,
            last_recrawl_at=now - 10,  # recent recrawl
            recrawl_interval=86400,
        )
        result = select_candidates([doc], now=now)
        assert result == []  # not overdue since last recrawl


# ── recrawl_url ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRecrawlUrl:
    async def test_304_not_modified(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(304))
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com",
            etag='"abc"',
            last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
            old_text_hash="deadbeef",
            stale_count=0,
            client=client,
        )
        assert outcome.status == "not_modified"
        assert outcome.stale_count == 0
        assert outcome.new_etag == '"abc"'

    async def test_404_increments_stale(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com/gone",
            etag=None,
            last_modified=None,
            old_text_hash="deadbeef",
            stale_count=1,
            client=client,
        )
        assert outcome.status == "error"  # stale_count=2 < STALE_THRESHOLD
        assert outcome.stale_count == 2

    async def test_404_reaches_stale_threshold(self) -> None:
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com/deleted",
            etag=None,
            last_modified=None,
            old_text_hash="deadbeef",
            stale_count=STALE_THRESHOLD - 1,
            client=client,
        )
        assert outcome.status == "deleted"
        assert outcome.stale_count == STALE_THRESHOLD

    async def test_200_content_unchanged(self) -> None:
        content = "Hello World"
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=content)
        )
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com",
            etag=None,
            last_modified=None,
            old_text_hash=content_hash,
            stale_count=0,
            client=client,
        )
        assert outcome.status == "not_modified"
        assert outcome.new_text_hash == content_hash

    async def test_200_content_changed(self) -> None:
        new_content = "Updated Content"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=new_content,
                headers={"etag": '"new_etag"', "last-modified": "Tue, 02 Jan 2024"},
            )
        )
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com",
            etag='"old_etag"',
            last_modified=None,
            old_text_hash="old_hash",
            stale_count=0,
            client=client,
        )
        assert outcome.status == "updated"
        assert outcome.new_etag == '"new_etag"'
        assert outcome.new_last_modified == "Tue, 02 Jan 2024"
        assert outcome.new_text_hash is not None
        assert outcome.stale_count == 0

    async def test_network_error(self) -> None:
        def raise_error(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(raise_error)
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com",
            etag=None,
            last_modified=None,
            old_text_hash="abc",
            stale_count=0,
            client=client,
        )
        assert outcome.status == "error"
        assert outcome.stale_count == 1
        assert outcome.elapsed_ms > 0

    async def test_custom_extract_fn(self) -> None:
        html_content = "<html><body>Full HTML</body></html>"
        extracted = "Full HTML"
        old_hash = hashlib.sha256(extracted.encode()).hexdigest()

        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=html_content)
        )
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com",
            etag=None,
            last_modified=None,
            old_text_hash=old_hash,
            stale_count=0,
            client=client,
            extract_fn=lambda html, url: extracted,
        )
        assert outcome.status == "not_modified"

    async def test_extract_fn_returns_none(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html></html>")
        )
        client = httpx.AsyncClient(transport=transport)
        outcome = await recrawl_url(
            "https://example.com",
            etag=None,
            last_modified=None,
            old_text_hash="abc",
            stale_count=0,
            client=client,
            extract_fn=lambda html, url: None,
        )
        assert outcome.status == "error"

    async def test_conditional_headers_sent(self) -> None:
        """Verify ETag and If-Modified-Since headers are set."""
        captured_headers: dict[str, str] = {}

        def capture(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(304)

        transport = httpx.MockTransport(capture)
        client = httpx.AsyncClient(transport=transport)
        await recrawl_url(
            "https://example.com",
            etag='"v42"',
            last_modified="Wed, 01 Jan 2025 12:00:00 GMT",
            old_text_hash="xxx",
            stale_count=0,
            client=client,
        )
        assert captured_headers.get("if-none-match") == '"v42"'
        assert (
            captured_headers.get("if-modified-since") == "Wed, 01 Jan 2025 12:00:00 GMT"
        )
