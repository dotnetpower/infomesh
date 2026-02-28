"""Tests for SimHash near-duplicate detection."""

from __future__ import annotations

from infomesh.crawler.simhash import (
    SimHashIndex,
    hamming_distance,
    is_near_duplicate,
    simhash,
)


class TestSimHash:
    """Core SimHash fingerprinting."""

    def test_deterministic(self) -> None:
        """Same text → same fingerprint."""
        text = "The quick brown fox jumps over the lazy dog"
        assert simhash(text) == simhash(text)

    def test_different_texts_different_hash(self) -> None:
        """Different texts → different fingerprints."""
        a = simhash("Python is a great programming language for data science")
        b = simhash("JavaScript runs in the browser and on the server with Node")
        assert a != b

    def test_similar_texts_small_distance(self) -> None:
        """Slightly modified text → small Hamming distance."""
        original = (
            "InfoMesh is a fully decentralized peer to peer search engine "
            "designed exclusively for large language models and AI assistants"
        )
        modified = (
            "InfoMesh is a fully decentralized peer to peer search engine "
            "designed exclusively for large language models and AI chatbots"
        )
        a = simhash(original)
        b = simhash(modified)
        dist = hamming_distance(a, b)
        # Similar texts should have small distance
        assert dist <= 10, f"Expected small distance, got {dist}"

    def test_empty_text_returns_zero(self) -> None:
        """Empty text → fingerprint 0."""
        assert simhash("") == 0

    def test_short_text_works(self) -> None:
        """Text shorter than shingle width still produces a fingerprint."""
        fp = simhash("hello")
        assert isinstance(fp, int)
        assert fp != 0

    def test_fingerprint_is_64_bit(self) -> None:
        """Fingerprint fits in 64 bits."""
        fp = simhash("A reasonably long document about programming and software")
        assert 0 <= fp < (1 << 64)

    def test_unicode_text(self) -> None:
        """Unicode text produces valid fingerprints."""
        fp = simhash("한국어 테스트 문자열로 SimHash를 생성합니다")
        assert isinstance(fp, int)


class TestHammingDistance:
    """Hamming distance computation."""

    def test_identical_zero_distance(self) -> None:
        assert hamming_distance(0, 0) == 0
        assert hamming_distance(0xFFFF, 0xFFFF) == 0

    def test_one_bit_diff(self) -> None:
        assert hamming_distance(0b1000, 0b0000) == 1

    def test_all_bits_different(self) -> None:
        # For 64-bit values
        a = 0x0000000000000000
        b = 0xFFFFFFFFFFFFFFFF
        assert hamming_distance(a, b) == 64

    def test_known_distance(self) -> None:
        # 0b1010 vs 0b0110 → bits 0 and 2 differ → distance 2
        assert hamming_distance(0b1010, 0b0110) == 2


class TestIsNearDuplicate:
    """Near-duplicate predicate."""

    def test_identical_is_near_dup(self) -> None:
        fp = simhash("Test document content for deduplication")
        assert is_near_duplicate(fp, fp)

    def test_very_different_not_near_dup(self) -> None:
        a = simhash("Python is a programming language created by Guido van Rossum")
        b = simhash("The weather in Tokyo is sunny and warm in the summer months")
        assert not is_near_duplicate(a, b)

    def test_custom_threshold(self) -> None:
        assert is_near_duplicate(0b1000, 0b0000, threshold=1)
        assert not is_near_duplicate(0b1010, 0b0000, threshold=0)


class TestSimHashIndex:
    """In-memory SimHash index."""

    def test_add_and_find(self) -> None:
        idx = SimHashIndex()
        fp = simhash("Document about machine learning and neural networks")
        idx.add(1, fp)
        assert idx.find_near_duplicates(fp) == [1]

    def test_find_near_duplicates_by_distance(self) -> None:
        idx = SimHashIndex()
        fp1 = 0b1111
        fp2 = 0b1110  # distance 1 from fp1
        idx.add(1, fp1)
        matches = idx.find_near_duplicates(fp2, threshold=1)
        assert 1 in matches

    def test_no_false_positive_beyond_threshold(self) -> None:
        idx = SimHashIndex()
        idx.add(1, 0b0000)
        # distance 4 > default threshold 3
        matches = idx.find_near_duplicates(0b1111, threshold=3)
        assert matches == []

    def test_remove(self) -> None:
        idx = SimHashIndex()
        fp = 100
        idx.add(1, fp)
        idx.remove(1, fp)
        assert idx.find_near_duplicates(fp) == []

    def test_size(self) -> None:
        idx = SimHashIndex()
        assert idx.size == 0
        idx.add(1, 100)
        idx.add(2, 200)
        assert idx.size == 2

    def test_get_stats(self) -> None:
        idx = SimHashIndex()
        idx.add(1, 100)
        idx.add(2, 100)  # Same fingerprint, different doc
        idx.add(3, 200)
        stats = idx.get_stats()
        assert stats["unique_fingerprints"] == 2
        assert stats["total_documents"] == 3

    def test_multiple_docs_same_fingerprint(self) -> None:
        idx = SimHashIndex()
        idx.add(1, 42)
        idx.add(2, 42)
        matches = idx.find_near_duplicates(42)
        assert sorted(matches) == [1, 2]
