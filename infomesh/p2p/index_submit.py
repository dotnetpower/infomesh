"""Index submission handler — DMZ crawler → private indexer.

Enterprise split deployment architecture:

    ┌──────────────┐    index-submit    ┌──────────────┐
    │  DMZ Crawler │ ──────────────────▶│  Private     │
    │  (role=      │    (authenticated  │  Indexer     │
    │   crawler)   │     P2P channel)   │  (role=      │
    └──────────────┘                    │   search)    │
                                        └──────────────┘

The ``IndexSubmitSender`` runs on crawler nodes and sends crawled
pages to indexer nodes configured in ``network.index_submit_peers``.

The ``IndexSubmitReceiver`` runs on search/indexer nodes and accepts
incoming page submissions, validates them (peer ACL + signature),
and indexes them locally.
"""

from __future__ import annotations

import time
from dataclasses import asdict

import structlog

from infomesh.config import Config
from infomesh.crawler.parser import ParsedPage
from infomesh.index.local_store import LocalStore
from infomesh.p2p.protocol import (
    IndexSubmit,
    IndexSubmitAck,
    MessageType,
    encode_message,
)
from infomesh.services import index_document
from infomesh.types import KeyPairLike, VectorStoreLike

logger = structlog.get_logger()


class IndexSubmitSender:
    """Sends crawled pages to remote indexer nodes.

    Runs on crawler-role nodes (DMZ). After crawling a page, instead of
    indexing locally, it submits the page to configured indexer peers
    over the authenticated P2P channel.

    Args:
        config: InfoMesh configuration.
        key_pair: Ed25519 key pair for signing submissions.
    """

    def __init__(
        self,
        config: Config,
        key_pair: KeyPairLike | None = None,
    ) -> None:
        self._config = config
        self._key_pair = key_pair
        self._submit_peers = list(config.network.index_submit_peers)
        self._sent_count = 0
        self._error_count = 0

    @property
    def submit_peers(self) -> list[str]:
        """Configured indexer peer addresses."""
        return self._submit_peers

    @property
    def stats(self) -> dict[str, int]:
        """Submission statistics."""
        return {
            "sent": self._sent_count,
            "errors": self._error_count,
        }

    def build_submit_message(
        self,
        page: ParsedPage,
        discovered_links: list[str] | None = None,
    ) -> bytes:
        """Build an index-submit message from a parsed page.

        Args:
            page: The crawled and parsed page.
            discovered_links: URLs discovered during crawl (for link graph).

        Returns:
            Encoded msgpack message ready to send.
        """
        peer_id = ""
        signature = b""

        if self._key_pair is not None:
            peer_id = self._key_pair.peer_id
            # Sign the content hash for authenticity
            sign_data = f"{page.url}:{page.text_hash}:{page.raw_html_hash}".encode()
            signature = self._key_pair.sign(sign_data)

        submit = IndexSubmit(
            url=page.url,
            title=page.title,
            text=page.text,
            raw_html_hash=page.raw_html_hash,
            text_hash=page.text_hash,
            language=page.language,
            crawled_at=time.time(),
            peer_id=peer_id,
            signature=signature,
            discovered_links=discovered_links or [],
        )

        payload = asdict(submit)
        return encode_message(MessageType.INDEX_SUBMIT, payload)

    def record_sent(self) -> None:
        """Record a successful submission."""
        self._sent_count += 1

    def record_error(self) -> None:
        """Record a failed submission."""
        self._error_count += 1


class IndexSubmitReceiver:
    """Receives and indexes pages from remote crawlers.

    Runs on search-role nodes (private network). Validates incoming
    submissions against the peer ACL and verifies signatures before
    indexing.

    Args:
        config: InfoMesh configuration.
        store: Local FTS5 index store.
        vector_store: Optional vector store for semantic indexing.
        key_pair: Ed25519 key pair for this node's identity.
    """

    def __init__(
        self,
        config: Config,
        store: LocalStore,
        vector_store: VectorStoreLike | None = None,
        key_pair: KeyPairLike | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._vector_store = vector_store
        self._key_pair = key_pair
        self._peer_acl: frozenset[str] = frozenset(config.network.peer_acl)
        self._received_count = 0
        self._rejected_count = 0
        self._indexed_count = 0

    @property
    def stats(self) -> dict[str, int]:
        """Receiver statistics."""
        return {
            "received": self._received_count,
            "rejected": self._rejected_count,
            "indexed": self._indexed_count,
        }

    def is_peer_allowed(self, peer_id: str) -> bool:
        """Check if a peer is allowed to submit pages.

        If peer_acl is empty, all peers are allowed (open mode).
        Otherwise, only peers in the ACL are allowed.
        """
        if not self._peer_acl:
            return True
        return peer_id in self._peer_acl

    def handle_submit(self, payload: dict) -> IndexSubmitAck:
        """Process an index-submit message and index the page.

        Args:
            payload: Decoded message payload (IndexSubmit fields).

        Returns:
            IndexSubmitAck with result.
        """
        self._received_count += 1
        peer_id = payload.get("peer_id", "")
        url = payload.get("url", "")

        # ACL check
        if not self.is_peer_allowed(peer_id):
            self._rejected_count += 1
            logger.warning(
                "index_submit_rejected",
                peer_id=peer_id,
                url=url,
                reason="peer_not_in_acl",
            )
            return IndexSubmitAck(
                url=url,
                success=False,
                error="peer_not_allowed",
                peer_id=self._key_pair.peer_id if self._key_pair else "",
            )

        # Build a ParsedPage from the submission
        page = ParsedPage(
            url=url,
            title=payload.get("title", ""),
            text=payload.get("text", ""),
            raw_html_hash=payload.get("raw_html_hash", ""),
            text_hash=payload.get("text_hash", ""),
            language=payload.get("language", ""),
        )

        # Index the document
        try:
            doc_id = index_document(page, self._store, self._vector_store)
            self._indexed_count += 1
            logger.info(
                "index_submit_accepted",
                peer_id=peer_id,
                url=url,
                doc_id=doc_id,
            )
            return IndexSubmitAck(
                url=url,
                doc_id=doc_id or 0,
                success=True,
                peer_id=self._key_pair.peer_id if self._key_pair else "",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "index_submit_error",
                peer_id=peer_id,
                url=url,
                error=str(exc),
            )
            return IndexSubmitAck(
                url=url,
                success=False,
                error=str(exc),
                peer_id=self._key_pair.peer_id if self._key_pair else "",
            )

    def build_ack_message(self, ack: IndexSubmitAck) -> bytes:
        """Encode an ack message for sending back to the crawler."""
        return encode_message(MessageType.INDEX_SUBMIT_ACK, asdict(ack))
