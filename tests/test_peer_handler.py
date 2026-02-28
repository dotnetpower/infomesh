"""Tests for peer LLM summarization request handling."""

from __future__ import annotations

import asyncio
import time

import pytest

from infomesh.summarizer.engine import (
    LLMBackend,
    LLMRuntime,
    ModelInfo,
    SummarizationEngine,
)
from infomesh.summarizer.peer_handler import (
    MAX_TEXT_LENGTH,
    PeerSummarizationHandler,
    RejectReason,
    RequestStatus,
    SummarizeRequest,
    SummarizeResponse,
    deserialize_request,
    deserialize_response,
    serialize_request,
    serialize_response,
)


class MockBackend(LLMBackend):
    """Mock LLM backend for testing."""

    def __init__(self, response: str = "Test summary.", delay: float = 0.0) -> None:
        self._response = response
        self._delay = delay

    async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return self._response

    async def is_available(self) -> bool:
        return True

    async def model_info(self) -> ModelInfo:
        return ModelInfo(
            name="mock-model",
            runtime=LLMRuntime.OLLAMA,
            parameter_count="3B",
            quantization="Q4",
            available=True,
        )


def _make_request(
    requester: str = "peer-1",
    url: str = "http://example.com",
    text: str = "Some text here.",
) -> SummarizeRequest:
    return SummarizeRequest(
        request_id="req-001",
        requester_peer_id=requester,
        url=url,
        title="Test Page",
        text=text,
        timestamp=time.time(),
    )


@pytest.fixture()
def handler() -> PeerSummarizationHandler:
    backend = MockBackend()
    engine = SummarizationEngine(backend)
    return PeerSummarizationHandler(engine)


class TestHandleRequest:
    @pytest.mark.asyncio()
    async def test_successful_request(self, handler: PeerSummarizationHandler) -> None:
        req = _make_request()
        resp = await handler.handle_request(req, requester_trust=0.8)
        assert resp.status == RequestStatus.COMPLETED
        assert resp.summary is not None
        assert "summary" in resp.summary.lower() or len(resp.summary) > 0
        assert resp.content_hash is not None
        assert handler.total_served == 1

    @pytest.mark.asyncio()
    async def test_untrusted_peer_rejected(
        self, handler: PeerSummarizationHandler
    ) -> None:
        req = _make_request()
        resp = await handler.handle_request(req, requester_trust=0.1)
        assert resp.status == RequestStatus.REJECTED
        assert resp.reject_reason == RejectReason.UNTRUSTED_PEER
        assert handler.total_rejected == 1

    @pytest.mark.asyncio()
    async def test_text_too_long_rejected(
        self, handler: PeerSummarizationHandler
    ) -> None:
        req = _make_request(text="x" * (MAX_TEXT_LENGTH + 1))
        resp = await handler.handle_request(req, requester_trust=0.8)
        assert resp.status == RequestStatus.REJECTED
        assert resp.reject_reason == RejectReason.TEXT_TOO_LONG

    @pytest.mark.asyncio()
    async def test_active_count_tracking(
        self, handler: PeerSummarizationHandler
    ) -> None:
        req = _make_request()
        assert handler.active_count == 0
        resp = await handler.handle_request(req, requester_trust=0.8)
        assert handler.active_count == 0  # Completed, back to 0
        assert resp.status == RequestStatus.COMPLETED


class TestCooldown:
    @pytest.mark.asyncio()
    async def test_cooldown_rejected(self) -> None:
        backend = MockBackend()
        engine = SummarizationEngine(backend)
        handler = PeerSummarizationHandler(engine)

        req1 = SummarizeRequest(
            request_id="req-1",
            requester_peer_id="peer-1",
            url="http://a.com",
            title="T",
            text="Content",
            timestamp=time.time(),
        )
        resp1 = await handler.handle_request(req1, requester_trust=0.8)
        assert resp1.status == RequestStatus.COMPLETED

        # Immediate second request should be rate limited
        req2 = SummarizeRequest(
            request_id="req-2",
            requester_peer_id="peer-1",
            url="http://b.com",
            title="T2",
            text="More content",
            timestamp=time.time(),
        )
        resp2 = await handler.handle_request(req2, requester_trust=0.8)
        assert resp2.status == RequestStatus.REJECTED
        assert resp2.reject_reason == RejectReason.COOLDOWN


class TestSerialization:
    def test_request_roundtrip(self) -> None:
        req = _make_request()
        data = serialize_request(req)
        restored = deserialize_request(data)
        assert restored.request_id == req.request_id
        assert restored.url == req.url
        assert restored.text == req.text

    def test_response_roundtrip(self) -> None:
        resp = SummarizeResponse(
            request_id="req-1",
            status=RequestStatus.COMPLETED,
            summary="Test summary",
            content_hash="abc123",
            model="mock",
            elapsed_ms=100.0,
            detail="ok",
        )
        data = serialize_response(resp)
        restored = deserialize_response(data)
        assert restored.request_id == resp.request_id
        assert restored.status == RequestStatus.COMPLETED
        assert restored.summary == "Test summary"

    def test_rejected_response_roundtrip(self) -> None:
        resp = SummarizeResponse(
            request_id="req-2",
            status=RequestStatus.REJECTED,
            reject_reason=RejectReason.UNTRUSTED_PEER,
            detail="trust too low",
        )
        data = serialize_response(resp)
        restored = deserialize_response(data)
        assert restored.reject_reason == RejectReason.UNTRUSTED_PEER
