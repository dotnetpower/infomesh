"""Tests for infomesh.index.starter — starter index download & import."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from infomesh.index.starter import (
    SNAPSHOT_ASSET_NAME,
    StarterAssetInfo,
    _read_cache,
    _write_cache,
    download_starter_snapshot,
    find_starter_asset,
    needs_starter,
)

# ── needs_starter ───────────────────────────────────────────────────


class TestNeedsStarter:
    def test_empty_index(self) -> None:
        assert needs_starter(0) is True

    def test_tiny_index(self) -> None:
        assert needs_starter(5) is True

    def test_threshold(self) -> None:
        assert needs_starter(9) is True
        assert needs_starter(10) is False

    def test_large_index(self) -> None:
        assert needs_starter(50000) is False


# ── Cache helpers ───────────────────────────────────────────────────


class TestCache:
    def test_write_and_read(self, tmp_path: Path) -> None:
        info = StarterAssetInfo(
            download_url="https://example.com/snap",
            size_bytes=1024,
            release_tag="v0.1.0",
            created_at="2025-01-01T00:00:00Z",
        )
        _write_cache(tmp_path, info)
        result = _read_cache(tmp_path)
        assert result is not None
        assert result.download_url == info.download_url
        assert result.size_bytes == info.size_bytes
        assert result.release_tag == info.release_tag

    def test_expired_cache(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "starter_meta_cache.json"
        cache_file.write_text(
            json.dumps(
                {
                    "ts": time.time() - 7200,  # 2 hours ago (TTL is 1 hour)
                    "url": "https://example.com/snap",
                    "size": 1024,
                    "tag": "v0.1.0",
                }
            ),
            encoding="utf-8",
        )
        assert _read_cache(tmp_path) is None

    def test_missing_cache(self, tmp_path: Path) -> None:
        assert _read_cache(tmp_path) is None

    def test_corrupted_cache(self, tmp_path: Path) -> None:
        cache_file = tmp_path / "starter_meta_cache.json"
        cache_file.write_text("not json!", encoding="utf-8")
        assert _read_cache(tmp_path) is None


# ── StarterAssetInfo ────────────────────────────────────────────────


class TestStarterAssetInfo:
    def test_size_mb(self) -> None:
        info = StarterAssetInfo(
            download_url="https://example.com",
            size_bytes=10 * 1024 * 1024,
            release_tag="v1.0",
            created_at="",
        )
        assert info.size_mb == pytest.approx(10.0)


# ── find_starter_asset ──────────────────────────────────────────────


class TestFindStarterAsset:
    @pytest.mark.asyncio
    async def test_finds_asset(self) -> None:
        mock_releases = [
            {
                "tag_name": "v0.1.5",
                "assets": [
                    {
                        "name": "starter.infomesh-snapshot",
                        "browser_download_url": "https://gh.com/dl/snap",
                        "size": 5000000,
                        "created_at": "2025-06-01T00:00:00Z",
                    },
                    {
                        "name": "other-file.tar.gz",
                        "browser_download_url": "https://gh.com/dl/other",
                        "size": 100,
                    },
                ],
            },
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_releases
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("infomesh.index.starter.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await find_starter_asset()

        assert result is not None
        assert result.download_url == "https://gh.com/dl/snap"
        assert result.size_bytes == 5000000
        assert result.release_tag == "v0.1.5"

    @pytest.mark.asyncio
    async def test_no_snapshot_asset(self) -> None:
        mock_releases = [
            {
                "tag_name": "v0.1.5",
                "assets": [
                    {
                        "name": "source.tar.gz",
                        "browser_download_url": "https://gh.com/dl/src",
                        "size": 100,
                    },
                ],
            },
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_releases
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("infomesh.index.starter.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await find_starter_asset()

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_releases(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = []
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("infomesh.index.starter.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await find_starter_asset()

        assert result is None

    @pytest.mark.asyncio
    async def test_api_failure_returns_none(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("infomesh.index.starter.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            result = await find_starter_asset()

        assert result is None

    @pytest.mark.asyncio
    async def test_uses_cache(self, tmp_path: Path) -> None:
        info = StarterAssetInfo(
            download_url="https://cached.com/snap",
            size_bytes=2048,
            release_tag="v0.2.0",
            created_at="2025-06-01T00:00:00Z",
        )
        _write_cache(tmp_path, info)

        # Should not make any HTTP calls — returns from cache
        result = await find_starter_asset(cache_dir=tmp_path)
        assert result is not None
        assert result.download_url == "https://cached.com/snap"


# ── download_starter_snapshot ───────────────────────────────────────


class TestDownloadStarterSnapshot:
    @pytest.mark.asyncio
    async def test_download_success(self, tmp_path: Path) -> None:
        fake_content = b"fake-snapshot-data-12345"

        # Mock find_starter_asset
        asset = StarterAssetInfo(
            download_url="https://gh.com/dl/snap",
            size_bytes=len(fake_content),
            release_tag="v0.1.0",
            created_at="",
        )

        # Mock httpx streaming
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = MagicMock(
            return_value=_async_iter([fake_content]),
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "infomesh.index.starter.find_starter_asset",
                return_value=asset,
            ),
            patch("infomesh.index.starter.httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.Timeout = MagicMock()

            result = await download_starter_snapshot(tmp_path)

        assert result is not None
        assert result.name == SNAPSHOT_ASSET_NAME
        assert result.read_bytes() == fake_content

    @pytest.mark.asyncio
    async def test_no_asset_returns_none(self, tmp_path: Path) -> None:
        with patch(
            "infomesh.index.starter.find_starter_asset",
            return_value=None,
        ):
            result = await download_starter_snapshot(tmp_path)

        assert result is None

    @pytest.mark.asyncio
    async def test_already_downloaded(self, tmp_path: Path) -> None:
        content = b"existing-data"
        dest = tmp_path / SNAPSHOT_ASSET_NAME
        dest.write_bytes(content)

        asset = StarterAssetInfo(
            download_url="https://gh.com/dl/snap",
            size_bytes=len(content),
            release_tag="v0.1.0",
            created_at="",
        )

        with patch(
            "infomesh.index.starter.find_starter_asset",
            return_value=asset,
        ):
            result = await download_starter_snapshot(tmp_path)

        assert result == dest

    @pytest.mark.asyncio
    async def test_progress_callback(self, tmp_path: Path) -> None:
        chunk1 = b"aaaa"
        chunk2 = b"bbbb"

        asset = StarterAssetInfo(
            download_url="https://gh.com/dl/snap",
            size_bytes=8,
            release_tag="v0.1.0",
            created_at="",
        )

        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = MagicMock(
            return_value=_async_iter([chunk1, chunk2]),
        )
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        progress_calls: list[tuple[int, int]] = []

        def on_progress(downloaded: int, total: int) -> None:
            progress_calls.append((downloaded, total))

        with (
            patch(
                "infomesh.index.starter.find_starter_asset",
                return_value=asset,
            ),
            patch("infomesh.index.starter.httpx") as mock_httpx,
        ):
            mock_httpx.AsyncClient.return_value = mock_client
            mock_httpx.Timeout = MagicMock()

            await download_starter_snapshot(
                tmp_path,
                progress_callback=on_progress,
            )

        assert len(progress_calls) == 2
        assert progress_calls[0] == (4, 8)
        assert progress_calls[1] == (8, 8)


# ── CLI integration (smoke test) ────────────────────────────────────


class TestCLIStarterFlag:
    def test_starter_info_no_asset(self) -> None:
        """--starter --info with no asset should exit cleanly."""
        from click.testing import CliRunner

        from infomesh.cli.index import index_import

        runner = CliRunner()
        with patch(
            "infomesh.cli.index.load_config",
        ) as mock_cfg:
            mock_cfg.return_value = _make_mock_config()
            with patch(
                "infomesh.index.starter.find_starter_asset",
                return_value=None,
            ):
                result = runner.invoke(index_import, ["--starter", "--info"])

        assert result.exit_code == 0
        assert "No starter snapshot" in result.output

    def test_starter_info_shows_metadata(self) -> None:
        """--starter --info should display asset metadata."""
        from click.testing import CliRunner

        from infomesh.cli.index import index_import

        runner = CliRunner()
        asset = StarterAssetInfo(
            download_url="https://gh.com/dl/snap",
            size_bytes=5 * 1024 * 1024,
            release_tag="v0.1.5",
            created_at="2025-06-01T00:00:00Z",
        )

        with patch(
            "infomesh.cli.index.load_config",
        ) as mock_cfg:
            mock_cfg.return_value = _make_mock_config()
            with patch(
                "infomesh.index.starter.find_starter_asset",
                return_value=asset,
            ):
                result = runner.invoke(index_import, ["--starter", "--info"])

        assert result.exit_code == 0
        assert "v0.1.5" in result.output
        assert "5.0 MB" in result.output

    def test_no_input_path_without_starter(self) -> None:
        """Missing INPUT_PATH without --starter should error."""
        from click.testing import CliRunner

        from infomesh.cli.index import index_import

        runner = CliRunner()
        result = runner.invoke(index_import, [])
        assert result.exit_code == 1
        assert "Missing argument" in result.output


# ── Helpers ─────────────────────────────────────────────────────────


async def _async_iter(items: list[bytes]) -> None:
    """Create an async iterator from a list (for mocking aiter_bytes)."""
    for item in items:
        yield item  # type: ignore[misc]


def _make_mock_config() -> MagicMock:
    """Create a mock Config with data_dir pointing to /tmp."""
    from pathlib import Path

    config = MagicMock()
    config.node.data_dir = Path("/tmp/infomesh-test-starter")
    config.index.db_path = Path("/tmp/infomesh-test-starter/index.db")
    config.storage.compression_enabled = True
    config.storage.compression_level = 3
    return config
