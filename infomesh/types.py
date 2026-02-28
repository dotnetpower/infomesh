"""Shared Protocol types for InfoMesh.

Defines structural interfaces (PEP 544 Protocols) used across the
codebase to avoid ``object`` type annotations and ``# type: ignore``
suppressions.  Modules should depend on these Protocols — never on
concrete implementations — when all they need is a structural contract.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

# ── Key pair protocol ───────────────────────────────────────────────


@runtime_checkable
class KeyPairLike(Protocol):
    """Structural interface for Ed25519-style key pairs.

    Any object exposing ``peer_id``, ``sign``, and ``verify`` satisfies
    this protocol — including :class:`infomesh.p2p.keys.KeyPair` and
    test mocks.
    """

    @property
    def peer_id(self) -> str: ...  # noqa: D102

    def sign(self, data: bytes) -> bytes:
        """Sign *data* with the private key and return the signature."""
        ...

    def verify(self, data: bytes, signature: bytes) -> bool:
        """Return ``True`` if *signature* is valid for *data*."""
        ...

    def public_key_bytes(self) -> bytes:
        """Return raw 32-byte public key."""
        ...


# ── Vector store protocol ──────────────────────────────────────────


@runtime_checkable
class VectorStoreLike(Protocol):
    """Structural interface for vector/embedding stores.

    Decouples callers from the concrete
    :class:`~infomesh.index.vector_store.VectorStore`
    so ``chromadb`` need not be installed for type-checking
    to pass.
    """

    def add_document(
        self,
        doc_id: int,
        url: str,
        title: str,
        text: str,
        language: str | None = None,
    ) -> None:
        """Add a document to the vector index."""
        ...

    def search(self, query: str, limit: int = 10) -> list[Any]:
        """Return the *limit* most similar results for *query*."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Return index statistics."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ── Authority function protocol ────────────────────────────────────


class AuthorityFn(Protocol):
    """Callable that returns a domain authority score for a URL."""

    def __call__(self, url: str) -> float: ...  # noqa: D102
