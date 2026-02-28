"""zstd compression with level-tunable support and dictionary mode."""

from __future__ import annotations

import structlog
import zstandard as zstd

logger = structlog.get_logger()

# Default compression levels by use case
LEVEL_REALTIME = 3  # Fast: real-time crawl data
LEVEL_SNAPSHOT = 12  # Balanced: index snapshots
LEVEL_ARCHIVE = 19  # High: Common Crawl import / archival

# Default maximum decompressed output size (100 MB)
DEFAULT_MAX_OUTPUT_SIZE = 100 * 1024 * 1024


class Compressor:
    """zstd compressor with optional dictionary support.

    Usage:
        comp = Compressor(level=3)
        compressed = comp.compress(data)
        original = comp.decompress(compressed)
    """

    def __init__(
        self,
        level: int = LEVEL_REALTIME,
        *,
        dict_data: bytes | None = None,
    ) -> None:
        self._level = level

        if dict_data:
            self._dict: zstd.ZstdCompressionDict | None = zstd.ZstdCompressionDict(
                dict_data
            )
            self._compressor = zstd.ZstdCompressor(level=level, dict_data=self._dict)
            self._decompressor = zstd.ZstdDecompressor(dict_data=self._dict)
        else:
            self._dict = None
            self._compressor = zstd.ZstdCompressor(level=level)
            self._decompressor = zstd.ZstdDecompressor()

    def compress(self, data: bytes) -> bytes:
        """Compress data using zstd.

        Args:
            data: Raw bytes to compress.

        Returns:
            Compressed bytes.
        """
        return self._compressor.compress(data)

    def decompress(
        self, data: bytes, *, max_output_size: int = DEFAULT_MAX_OUTPUT_SIZE
    ) -> bytes:
        """Decompress zstd-compressed data.

        Args:
            data: Compressed bytes.
            max_output_size: Maximum allowed decompressed size in bytes.
                Prevents decompression bombs. Defaults to 100 MB.

        Returns:
            Original bytes.

        Raises:
            zstd.ZstdError: If decompressed output exceeds ``max_output_size``.
        """
        result = self._decompressor.decompress(data, max_output_size=max_output_size)
        if len(result) > max_output_size:
            raise zstd.ZstdError(
                f"decompressed {len(result)} bytes exceeds limit of {max_output_size}"
            )
        return result

    def compress_text(self, text: str) -> bytes:
        """Compress a text string (UTF-8 encoded).

        Args:
            text: String to compress.

        Returns:
            Compressed bytes.
        """
        return self.compress(text.encode("utf-8"))

    def decompress_text(
        self, data: bytes, *, max_output_size: int = DEFAULT_MAX_OUTPUT_SIZE
    ) -> str:
        """Decompress to a text string.

        Args:
            data: Compressed bytes.
            max_output_size: Maximum allowed decompressed size in bytes.

        Returns:
            Original string.
        """
        return self.decompress(data, max_output_size=max_output_size).decode("utf-8")

    @property
    def level(self) -> int:
        """Current compression level."""
        return self._level


def train_dictionary(samples: list[bytes], *, dict_size: int = 112_640) -> bytes:
    """Train a zstd compression dictionary from sample data.

    Args:
        samples: List of sample data to train on.
        dict_size: Target dictionary size in bytes (default: 110KB).

    Returns:
        Dictionary data bytes.
    """
    return zstd.train_dictionary(dict_size, samples).as_bytes()  # type: ignore[arg-type]
