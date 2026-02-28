"""Tests for zstd compression."""

from __future__ import annotations

from infomesh.compression.zstd import (
    LEVEL_ARCHIVE,
    LEVEL_REALTIME,
    Compressor,
    train_dictionary,
)


class TestCompressor:
    """Tests for zstd compressor."""

    def test_roundtrip(self) -> None:
        comp = Compressor(level=LEVEL_REALTIME)
        original = b"Hello, InfoMesh! " * 100
        compressed = comp.compress(original)
        decompressed = comp.decompress(compressed)
        assert decompressed == original

    def test_compression_ratio(self) -> None:
        comp = Compressor(level=LEVEL_REALTIME)
        original = b"repeated text for compression " * 1000
        compressed = comp.compress(original)
        assert len(compressed) < len(original)

    def test_text_roundtrip(self) -> None:
        comp = Compressor()
        text = (
            "Python asyncio provides infrastructure for writing concurrent code." * 50
        )
        compressed = comp.compress_text(text)
        assert comp.decompress_text(compressed) == text

    def test_different_levels(self) -> None:
        data = b"test data for compression levels " * 500
        c1 = Compressor(level=LEVEL_REALTIME)
        c2 = Compressor(level=LEVEL_ARCHIVE)

        compressed_fast = c1.compress(data)
        compressed_high = c2.compress(data)

        # Higher level should compress better (or equal)
        assert len(compressed_high) <= len(compressed_fast)

        # Both should decompress correctly
        assert c1.decompress(compressed_fast) == data
        assert c2.decompress(compressed_high) == data

    def test_level_property(self) -> None:
        comp = Compressor(level=9)
        assert comp.level == 9


class TestDictionary:
    """Tests for dictionary training."""

    def test_train_and_use(self) -> None:
        # Create similar samples
        samples = [
            f"Document about {topic} with technical content".encode()
            for topic in ["python", "rust", "java", "go", "javascript"] * 20
        ]

        dict_data = train_dictionary(samples, dict_size=4096)
        assert len(dict_data) > 0

        comp = Compressor(level=3, dict_data=dict_data)
        original = b"Document about python with technical content and more details"
        compressed = comp.compress(original)
        assert comp.decompress(compressed) == original
