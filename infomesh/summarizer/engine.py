"""Local LLM summarization engine — backend abstraction.

Supports multiple LLM runtimes (ollama, llama.cpp) via a unified interface.
The summarizer is optional and only activates when ``LLMConfig.enabled = True``.

Recommended models (in order):
1. Qwen 2.5 (3B/7B) — multilingual, Apache 2.0
2. Llama 3.x (3B/8B) — general-purpose
3. Gemma 3 (4B/12B) — size-to-quality
4. Phi-4 (3.8B/14B) — reasoning/summarization
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from enum import StrEnum

import httpx
import structlog

from infomesh.hashing import content_hash

logger = structlog.get_logger()


# --- Types -----------------------------------------------------------------


class LLMRuntime(StrEnum):
    """Supported LLM runtime backends."""

    OLLAMA = "ollama"
    LLAMA_CPP = "llama.cpp"


@dataclass(frozen=True)
class SummaryResult:
    """Output of summarization."""

    url: str
    summary: str
    model: str
    runtime: LLMRuntime
    content_hash: str  # SHA-256 of source text
    elapsed_ms: float
    token_count: int | None  # Estimated tokens in summary (if available)


@dataclass(frozen=True)
class ModelInfo:
    """Information about a loaded LLM model."""

    name: str
    runtime: LLMRuntime
    parameter_count: str | None  # e.g. "3B", "7B"
    quantization: str | None  # e.g. "Q4_K_M"
    available: bool


# --- Abstract backend -------------------------------------------------------


class LLMBackend(abc.ABC):
    """Abstract base class for LLM runtime backends."""

    @abc.abstractmethod
    async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
        """Generate text from a prompt.

        Args:
            prompt: Input prompt text.
            max_tokens: Maximum tokens to generate.

        Returns:
            Generated text.
        """

    @abc.abstractmethod
    async def is_available(self) -> bool:
        """Check if the backend is running and a model is loaded."""

    @abc.abstractmethod
    async def model_info(self) -> ModelInfo:
        """Return information about the current model."""

    # ── Shared HTTP client lifecycle ────────────────────────

    def _init_client_slot(self) -> None:
        """Initialize the ``_client`` slot — call in subclass ``__init__``."""
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a shared :class:`httpx.AsyncClient`, creating one lazily."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=120.0)
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None


# --- Ollama backend ---------------------------------------------------------


class OllamaBackend(LLMBackend):
    """Ollama REST API backend.

    Requires ollama to be running locally (default: http://localhost:11434).
    """

    def __init__(
        self,
        model: str = "qwen2.5:3b",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._init_client_slot()

    async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
        client = await self._get_client()
        resp = await client.post(
            f"{self._base_url}/api/generate",
            json={
                "model": self._model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": max_tokens,
                    "temperature": 0.3,
                },
            },
        )
        resp.raise_for_status()
        return str(resp.json()["response"])

    async def is_available(self) -> bool:
        import httpx

        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base_url}/api/tags")
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            return any(m["name"].startswith(self._model.split(":")[0]) for m in models)
        except (httpx.HTTPError, OSError):
            return False

    async def model_info(self) -> ModelInfo:
        try:
            client = await self._get_client()
            resp = await client.post(
                f"{self._base_url}/api/show",
                json={"name": self._model},
            )
            if resp.status_code == 200:
                info = resp.json()
                details = info.get("details", {})
                return ModelInfo(
                    name=self._model,
                    runtime=LLMRuntime.OLLAMA,
                    parameter_count=details.get("parameter_size"),
                    quantization=details.get("quantization_level"),
                    available=True,
                )
        except (httpx.HTTPError, OSError):
            pass

        return ModelInfo(
            name=self._model,
            runtime=LLMRuntime.OLLAMA,
            parameter_count=None,
            quantization=None,
            available=False,
        )


# --- llama.cpp backend (HTTP server mode) -----------------------------------


class LlamaCppBackend(LLMBackend):
    """llama.cpp HTTP server backend.

    Requires llama-server running (default: http://localhost:8080).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        model_name: str = "llama-cpp-model",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_name = model_name
        self._init_client_slot()

    async def generate(self, prompt: str, *, max_tokens: int = 512) -> str:
        client = await self._get_client()
        resp = await client.post(
            f"{self._base_url}/completion",
            json={
                "prompt": prompt,
                "n_predict": max_tokens,
                "temperature": 0.3,
                "stop": ["\n\n---", "###"],
            },
        )
        resp.raise_for_status()
        return str(resp.json()["content"])

    async def is_available(self) -> bool:
        try:
            client = await self._get_client()
            resp = await client.get(f"{self._base_url}/health")
            return resp.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    async def model_info(self) -> ModelInfo:
        available = await self.is_available()
        return ModelInfo(
            name=self._model_name,
            runtime=LLMRuntime.LLAMA_CPP,
            parameter_count=None,
            quantization=None,
            available=available,
        )


# --- Summarization engine ---------------------------------------------------

# Prompt template for summarization
_SUMMARIZE_PROMPT = """\
You are a precise summarization assistant. Summarize the following web page content.
Keep the summary concise (3-5 sentences), factual, and information-dense.
Do not add opinions or information not present in the text.
Label this output as AI-generated.

URL: {url}
Title: {title}

Content:
{text}

Summary:"""


def create_backend(
    runtime: str,
    model: str = "qwen2.5:3b",
    *,
    base_url: str | None = None,
) -> LLMBackend:
    """Factory to create an LLM backend by runtime name.

    Args:
        runtime: ``"ollama"`` or ``"llama.cpp"``.
        model: Model identifier.
        base_url: Override the default server URL.

    Returns:
        Configured LLMBackend instance.

    Raises:
        ValueError: If the runtime is not supported.
    """
    match runtime:
        case "ollama":
            kw: dict[str, str] = {}
            if base_url is not None:
                kw["base_url"] = base_url
            return OllamaBackend(model=model, **kw)
        case "llama.cpp" | "llama_cpp" | "llamacpp":
            kw = {}
            if base_url is not None:
                kw["base_url"] = base_url
            return LlamaCppBackend(model_name=model, **kw)
        case _:
            msg = f"Unsupported LLM runtime: {runtime!r}. Use 'ollama' or 'llama.cpp'."
            raise ValueError(msg)


class SummarizationEngine:
    """High-level summarization engine using a pluggable LLM backend.

    Usage::

        engine = SummarizationEngine(backend)
        result = await engine.summarize(url, title, text)
    """

    def __init__(self, backend: LLMBackend) -> None:
        self._backend = backend

    async def summarize(
        self,
        url: str,
        title: str,
        text: str,
        *,
        max_tokens: int = 512,
        max_input_chars: int = 8000,
    ) -> SummaryResult:
        """Summarize document text using the LLM backend.

        Args:
            url: Source URL.
            title: Document title.
            text: Extracted text content.
            max_tokens: Maximum summary tokens.
            max_input_chars: Truncate input text at this length.

        Returns:
            SummaryResult with the generated summary.
        """
        truncated = text[:max_input_chars]
        prompt = _SUMMARIZE_PROMPT.format(url=url, title=title, text=truncated)

        start = time.monotonic()
        summary = await self._backend.generate(prompt, max_tokens=max_tokens)
        elapsed_ms = (time.monotonic() - start) * 1000

        c_hash = content_hash(text)

        info = await self._backend.model_info()

        result = SummaryResult(
            url=url,
            summary=summary.strip(),
            model=info.name,
            runtime=info.runtime,
            content_hash=c_hash,
            elapsed_ms=round(elapsed_ms, 1),
            token_count=_estimate_tokens(summary),
        )

        logger.info(
            "summarization_complete",
            url=url,
            model=info.name,
            elapsed_ms=round(elapsed_ms, 1),
            summary_length=len(result.summary),
        )
        return result

    async def is_available(self) -> bool:
        """Check if the LLM backend is available."""
        return await self._backend.is_available()


def _estimate_tokens(text: str) -> int:
    """Rough token count estimate (~4 chars per token for English)."""
    return max(1, len(text) // 4)
