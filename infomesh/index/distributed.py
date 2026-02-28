"""Distributed inverted index — publish/query keyword→peer pointers via DHT.

When a node indexes a document locally, it extracts keywords and publishes
each keyword's hash to the DHT, pointing back to itself:

    hash(keyword) → [{peer_id, doc_id, url, score, title}, ...]

When searching, a node queries the DHT for each keyword in the query to
find which peers have relevant documents, then fetches results from those
peers.

This module provides the bridge between the local ``LocalStore`` (FTS5)
and the distributed DHT-based index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import structlog

from infomesh.p2p.protocol import PeerPointer

logger = structlog.get_logger()

# Minimum keyword length to index
MIN_KEYWORD_LENGTH = 2

# Maximum keywords to extract per document
MAX_KEYWORDS_PER_DOC = 50

# Common English stop words to skip
_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "is",
        "it",
        "be",
        "as",
        "do",
        "by",
        "he",
        "we",
        "so",
        "if",
        "no",
        "up",
        "my",
        "me",
        "am",
        "us",
        "are",
        "was",
        "has",
        "had",
        "not",
        "all",
        "can",
        "her",
        "his",
        "its",
        "our",
        "you",
        "who",
        "how",
        "did",
        "get",
        "may",
        "new",
        "now",
        "old",
        "see",
        "way",
        "from",
        "with",
        "this",
        "that",
        "have",
        "will",
        "been",
        "each",
        "make",
        "like",
        "than",
        "them",
        "then",
        "into",
        "over",
        "such",
        "when",
        "very",
        "what",
        "just",
        "also",
        "more",
        "some",
        "only",
        "come",
        "could",
        "would",
        "about",
        "which",
        "their",
        "there",
        "these",
        "those",
        "other",
        "after",
        "being",
        "where",
        "does",
    }
)

# Word tokenizer
_WORD_RE = re.compile(r"\b[a-zA-Z0-9]+\b")


def extract_keywords(
    text: str, *, max_keywords: int = MAX_KEYWORDS_PER_DOC
) -> list[str]:
    """Extract indexable keywords from document text.

    Uses TF-based ranking to pick the most significant terms.

    Args:
        text: Document text.
        max_keywords: Maximum keywords to extract.

    Returns:
        List of keywords sorted by frequency (descending).
    """
    words = _WORD_RE.findall(text.lower())
    word_freq: dict[str, int] = {}

    for word in words:
        if len(word) < MIN_KEYWORD_LENGTH:
            continue
        if word in _STOP_WORDS:
            continue
        word_freq[word] = word_freq.get(word, 0) + 1

    # Sort by frequency descending
    ranked = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in ranked[:max_keywords]]


@dataclass
class DistributedIndexStats:
    """Statistics for the distributed index."""

    documents_published: int = 0
    keywords_published: int = 0
    queries_performed: int = 0
    pointers_found: int = 0


class DistributedIndex:
    """Manages the distributed inverted index over DHT.

    Publishes local document keywords to the DHT so other peers can
    discover documents hosted on this node.

    Args:
        dht: InfoMeshDHT instance.
        local_peer_id: This node's peer ID.
    """

    def __init__(self, dht: object, local_peer_id: str) -> None:
        self._dht = dht
        self._peer_id = local_peer_id
        self._stats = DistributedIndexStats()

    @property
    def stats(self) -> DistributedIndexStats:
        """Current distributed index statistics."""
        return self._stats

    async def publish_document(
        self,
        doc_id: int,
        url: str,
        title: str,
        text: str,
        score: float = 1.0,
    ) -> int:
        """Publish a document's keywords to the DHT.

        Extracts keywords from the document text and publishes
        each one as a pointer back to this node.

        Args:
            doc_id: Local document ID.
            url: Document URL.
            title: Document title.
            text: Document text.
            score: Relevance score for this document.

        Returns:
            Number of keywords successfully published.
        """
        keywords = extract_keywords(text)
        if not keywords:
            return 0

        published = 0
        pointer = PeerPointer(
            peer_id=self._peer_id,
            doc_id=doc_id,
            url=url,
            score=score,
            title=title,
        )
        pointer_dict = {
            "peer_id": pointer.peer_id,
            "doc_id": pointer.doc_id,
            "url": pointer.url,
            "score": pointer.score,
            "title": pointer.title,
        }

        for kw in keywords:
            ok = await self._dht.publish_keyword(kw, [pointer_dict])  # type: ignore[attr-defined]
            if ok:
                published += 1

        self._stats.documents_published += 1
        self._stats.keywords_published += published

        logger.debug(
            "distributed_index_published",
            url=url,
            keywords_total=len(keywords),
            keywords_published=published,
        )
        return published

    async def query(self, keywords: list[str]) -> list[PeerPointer]:
        """Query the distributed index for documents matching keywords.

        For each keyword, queries the DHT and collects peer pointers.
        Deduplicates by (peer_id, doc_id) and ranks by aggregate score.

        Args:
            keywords: Search keywords.

        Returns:
            Ranked list of PeerPointer instances.
        """
        self._stats.queries_performed += 1

        pointer_scores: dict[tuple[str, int], dict] = {}

        for kw in keywords:
            pointers = await self._dht.query_keyword(kw)  # type: ignore[attr-defined]
            for ptr in pointers:
                key = (ptr.get("peer_id", ""), ptr.get("doc_id", 0))
                if key in pointer_scores:
                    pointer_scores[key]["score"] += ptr.get("score", 0.5)
                else:
                    pointer_scores[key] = dict(ptr)

        # Sort by aggregate score
        ranked = sorted(
            pointer_scores.values(), key=lambda p: p.get("score", 0), reverse=True
        )

        self._stats.pointers_found += len(ranked)

        return [
            PeerPointer(
                peer_id=p.get("peer_id", ""),
                doc_id=p.get("doc_id", 0),
                url=p.get("url", ""),
                score=p.get("score", 0.0),
                title=p.get("title", ""),
            )
            for p in ranked
        ]

    async def publish_batch(
        self,
        documents: list[dict],
    ) -> int:
        """Publish multiple documents to the distributed index.

        Each document dict should have: doc_id, url, title, text, score.

        Args:
            documents: List of document dicts.

        Returns:
            Total keywords published across all documents.
        """
        total = 0
        for doc in documents:
            count = await self.publish_document(
                doc_id=doc["doc_id"],
                url=doc["url"],
                title=doc.get("title", ""),
                text=doc.get("text", ""),
                score=doc.get("score", 1.0),
            )
            total += count
        return total
