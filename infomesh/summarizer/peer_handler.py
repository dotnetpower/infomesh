"""Peer-to-peer LLM summarization request handling.

Handles incoming summarization requests from other peers.
A node with local LLM capability can serve requests for other nodes
that lack LLM support, earning LLM_SUMMARIZE_PEER credits.

Protocol:
1. Requesting node sends a SummarizeRequest via /infomesh/llm/1.0.0
2. Serving node checks rate limits, trust, and capacity.
3. Serving node runs local LLM and returns summary.
4. Both sides record credit entries.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import StrEnum

import structlog

from infomesh.summarizer.engine import SummarizationEngine

logger = structlog.get_logger()


# Protocol ID for LLM requests
PROTOCOL_LLM = "/infomesh/llm/1.0.0"

# --- Constants -------------------------------------------------------------

# Maximum pending requests per peer
MAX_PENDING_PER_PEER: int = 5

# Maximum concurrent LLM requests being processed
MAX_CONCURRENT_REQUESTS: int = 3

# Maximum text length accepted for summarization (chars)
MAX_TEXT_LENGTH: int = 16_000

# Request timeout (seconds)
REQUEST_TIMEOUT_SECONDS: float = 120.0

# Cooldown between requests from the same peer (seconds)
PEER_COOLDOWN_SECONDS: float = 10.0


class RequestStatus(StrEnum):
    """Status of a summarization request."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    TIMEOUT = "timeout"


class RejectReason(StrEnum):
    """Why a request was rejected."""

    NO_LLM = "no_llm"
    RATE_LIMITED = "rate_limited"
    CAPACITY_FULL = "capacity_full"
    TEXT_TOO_LONG = "text_too_long"
    UNTRUSTED_PEER = "untrusted_peer"
    COOLDOWN = "cooldown"


@dataclass(frozen=True)
class SummarizeRequest:
    """Incoming summarization request from a peer."""

    request_id: str
    requester_peer_id: str
    url: str
    title: str
    text: str
    max_tokens: int = 512
    timestamp: float = 0.0


@dataclass(frozen=True)
class SummarizeResponse:
    """Response to a summarization request."""

    request_id: str
    status: RequestStatus
    summary: str | None = None
    content_hash: str | None = None
    model: str | None = None
    elapsed_ms: float = 0.0
    reject_reason: RejectReason | None = None
    detail: str = ""


# --- Request handler --------------------------------------------------------


class PeerSummarizationHandler:
    """Handles incoming LLM summarization requests from peers.

    Manages rate limiting, capacity tracking, and request processing.

    Args:
        engine: Local SummarizationEngine (wraps LLMBackend).
        min_trust_score: Minimum trust score to accept requests from.
    """

    def __init__(
        self,
        engine: SummarizationEngine,
        *,
        min_trust_score: float = 0.3,
    ) -> None:
        self._engine = engine
        self._min_trust = min_trust_score

        # Track active requests
        self._active_count: int = 0
        self._peer_last_request: dict[str, float] = {}
        self._peer_pending: dict[str, int] = {}

        # Stats
        self._total_served: int = 0
        self._total_rejected: int = 0

    async def handle_request(
        self,
        request: SummarizeRequest,
        *,
        requester_trust: float = 0.5,
    ) -> SummarizeResponse:
        """Process a summarization request from a peer.

        Args:
            request: The incoming request.
            requester_trust: Trust score of the requesting peer.

        Returns:
            SummarizeResponse with the result or rejection.
        """
        now = time.time()

        # Pre-checks
        rejection = self._check_rejection(
            request, requester_trust=requester_trust, now=now
        )
        if rejection is not None:
            self._total_rejected += 1
            return rejection

        # Accept and process
        self._active_count += 1
        self._peer_pending[request.requester_peer_id] = (
            self._peer_pending.get(request.requester_peer_id, 0) + 1
        )
        self._peer_last_request[request.requester_peer_id] = now

        try:
            result = await asyncio.wait_for(
                self._engine.summarize(
                    url=request.url,
                    title=request.title,
                    text=request.text,
                    max_tokens=request.max_tokens,
                ),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            self._total_served += 1

            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.COMPLETED,
                summary=result.summary,
                content_hash=result.content_hash,
                model=result.model,
                elapsed_ms=result.elapsed_ms,
                detail="ok",
            )

        except TimeoutError:
            logger.warning(
                "peer_llm_timeout",
                request_id=request.request_id,
                requester=request.requester_peer_id[:12],
            )
            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.TIMEOUT,
                detail="LLM generation timed out",
            )

        except Exception as exc:
            logger.exception(
                "peer_llm_failed",
                request_id=request.request_id,
                requester=request.requester_peer_id[:12],
            )
            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.FAILED,
                detail=str(exc),
            )

        finally:
            self._active_count -= 1
            pending = self._peer_pending.get(request.requester_peer_id, 1)
            self._peer_pending[request.requester_peer_id] = max(0, pending - 1)

    def _check_rejection(
        self,
        request: SummarizeRequest,
        *,
        requester_trust: float,
        now: float,
    ) -> SummarizeResponse | None:
        """Check if a request should be rejected.

        Returns:
            SummarizeResponse with rejection, or None if accepted.
        """
        # Trust check
        if requester_trust < self._min_trust:
            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.REJECTED,
                reject_reason=RejectReason.UNTRUSTED_PEER,
                detail=(
                    f"trust score {requester_trust:.3f}"
                    f" below minimum {self._min_trust:.3f}"
                ),
            )

        # Text length check
        if len(request.text) > MAX_TEXT_LENGTH:
            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.REJECTED,
                reject_reason=RejectReason.TEXT_TOO_LONG,
                detail=(
                    f"text length {len(request.text)} exceeds max {MAX_TEXT_LENGTH}"
                ),
            )

        # Capacity check
        if self._active_count >= MAX_CONCURRENT_REQUESTS:
            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.REJECTED,
                reject_reason=RejectReason.CAPACITY_FULL,
                detail=(
                    f"active requests {self._active_count}/{MAX_CONCURRENT_REQUESTS}"
                ),
            )

        # Per-peer pending check
        pending = self._peer_pending.get(request.requester_peer_id, 0)
        if pending >= MAX_PENDING_PER_PEER:
            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.REJECTED,
                reject_reason=RejectReason.RATE_LIMITED,
                detail=f"peer has {pending} pending requests",
            )

        # Cooldown check
        last = self._peer_last_request.get(request.requester_peer_id, 0)
        if (now - last) < PEER_COOLDOWN_SECONDS:
            return SummarizeResponse(
                request_id=request.request_id,
                status=RequestStatus.REJECTED,
                reject_reason=RejectReason.COOLDOWN,
                detail=(
                    f"cooldown: {PEER_COOLDOWN_SECONDS - (now - last):.1f}s remaining"
                ),
            )

        return None

    @property
    def active_count(self) -> int:
        """Number of currently processing requests."""
        return self._active_count

    @property
    def total_served(self) -> int:
        """Total requests successfully served."""
        return self._total_served

    @property
    def total_rejected(self) -> int:
        """Total requests rejected."""
        return self._total_rejected


def serialize_request(req: SummarizeRequest) -> dict:
    """Serialize a SummarizeRequest to a dict for wire format."""
    return {
        "request_id": req.request_id,
        "requester_peer_id": req.requester_peer_id,
        "url": req.url,
        "title": req.title,
        "text": req.text,
        "max_tokens": req.max_tokens,
        "timestamp": req.timestamp,
    }


def deserialize_request(data: dict) -> SummarizeRequest:
    """Deserialize a SummarizeRequest from a dict."""
    return SummarizeRequest(
        request_id=data["request_id"],
        requester_peer_id=data["requester_peer_id"],
        url=data["url"],
        title=data["title"],
        text=data["text"],
        max_tokens=data.get("max_tokens", 512),
        timestamp=data.get("timestamp", 0.0),
    )


def serialize_response(resp: SummarizeResponse) -> dict:
    """Serialize a SummarizeResponse to a dict for wire format."""
    return {
        "request_id": resp.request_id,
        "status": resp.status.value,
        "summary": resp.summary,
        "content_hash": resp.content_hash,
        "model": resp.model,
        "elapsed_ms": resp.elapsed_ms,
        "reject_reason": resp.reject_reason.value if resp.reject_reason else None,
        "detail": resp.detail,
    }


def deserialize_response(data: dict) -> SummarizeResponse:
    """Deserialize a SummarizeResponse from a dict."""
    return SummarizeResponse(
        request_id=data["request_id"],
        status=RequestStatus(data["status"]),
        summary=data.get("summary"),
        content_hash=data.get("content_hash"),
        model=data.get("model"),
        elapsed_ms=data.get("elapsed_ms", 0.0),
        reject_reason=RejectReason(data["reject_reason"])
        if data.get("reject_reason")
        else None,
        detail=data.get("detail", ""),
    )
