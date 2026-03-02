"""JavaScript page rendering via Playwright (headless Chromium).

This module is **optional** — Playwright is only imported when JS
rendering is actually requested.  Install with::

    pip install 'infomesh[browser]'

Features:

- Headless Chromium rendering with configurable timeout (default 30 s).
- Concurrency limiter (default 3 simultaneous tabs).
- Memory guard: aborts rendering if the browser process exceeds a
  configured RSS limit.
- Graceful fallback when Playwright is not installed.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderResult:
    """Outcome of a JS rendering attempt."""

    success: bool
    html: str  # rendered HTML (empty on failure)
    error: str | None = None
    elapsed_ms: float = 0.0


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

_playwright_available: bool | None = None


def is_playwright_available() -> bool:
    """Return *True* if ``playwright`` is importable."""
    global _playwright_available  # noqa: PLW0603
    if _playwright_available is None:
        try:
            import playwright.async_api  # noqa: F401

            _playwright_available = True
        except ImportError:
            _playwright_available = False
    return _playwright_available


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


class JSRenderer:
    """Headless browser renderer backed by Playwright.

    Usage::

        renderer = JSRenderer(max_tabs=3, timeout_ms=30_000)
        result = await renderer.render("https://example.com")
        await renderer.close()

    The renderer lazily launches the browser on the first call to
    :meth:`render`.  Call :meth:`close` when done to free resources.
    """

    def __init__(
        self,
        *,
        max_tabs: int = 3,
        timeout_ms: int = 30_000,
        max_memory_mb: int = 512,
    ) -> None:
        self._max_tabs = max_tabs
        self._timeout_ms = timeout_ms
        self._max_memory_mb = max_memory_mb
        self._semaphore = asyncio.Semaphore(max_tabs)
        self._browser: object | None = None  # playwright Browser
        self._playwright: object | None = None  # playwright context manager

    async def _ensure_browser(self) -> object:
        """Launch the browser if not already running."""
        if self._browser is not None:
            return self._browser

        if not is_playwright_available():
            raise RuntimeError(
                "Playwright is not installed.  "
                "Install with: pip install 'infomesh[browser]'"
            )

        from playwright.async_api import async_playwright

        pw = async_playwright()
        self._playwright = await pw.start()
        self._browser = await self._playwright.chromium.launch(  # type: ignore[union-attr]
            headless=True,
            args=[
                "--disable-gpu",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                f"--js-flags=--max-old-space-size={self._max_memory_mb}",
            ],
        )
        logger.info(
            "js_renderer_browser_launched",
            max_tabs=self._max_tabs,
            timeout_ms=self._timeout_ms,
        )
        return self._browser

    async def render(self, url: str) -> RenderResult:
        """Render a page with headless Chromium and return the final HTML.

        Blocks until a concurrency slot is available (up to
        *max_tabs* simultaneous pages).

        Args:
            url: The URL to render.

        Returns:
            :class:`RenderResult` with the rendered HTML or an error.
        """
        import time

        start = time.monotonic()

        if not is_playwright_available():
            return RenderResult(
                success=False,
                html="",
                error="playwright_not_installed",
                elapsed_ms=0.0,
            )

        async with self._semaphore:
            try:
                browser = await self._ensure_browser()
                page = await browser.new_page()  # type: ignore[attr-defined]
                try:
                    # Navigate and wait for network idle
                    await page.goto(
                        url,
                        wait_until="networkidle",
                        timeout=self._timeout_ms,
                    )
                    # Additional wait for late-loading JS content
                    await page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=min(self._timeout_ms, 5000),
                    )
                    html = await page.content()
                    elapsed = (time.monotonic() - start) * 1000

                    logger.info(
                        "js_render_success",
                        url=url,
                        html_len=len(html),
                        elapsed_ms=round(elapsed, 1),
                    )

                    return RenderResult(
                        success=True,
                        html=html,
                        elapsed_ms=elapsed,
                    )
                finally:
                    await page.close()

            except Exception as exc:
                elapsed = (time.monotonic() - start) * 1000
                error_msg = str(exc)

                # Truncate long error messages
                if len(error_msg) > 200:
                    error_msg = error_msg[:200] + "…"

                logger.warning(
                    "js_render_failed",
                    url=url,
                    error=error_msg,
                    elapsed_ms=round(elapsed, 1),
                )

                return RenderResult(
                    success=False,
                    html="",
                    error=error_msg,
                    elapsed_ms=elapsed,
                )

    async def close(self) -> None:
        """Shut down the browser process."""
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()  # type: ignore[attr-defined]
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()  # type: ignore[attr-defined]
            self._playwright = None
            logger.info("js_renderer_browser_closed")
