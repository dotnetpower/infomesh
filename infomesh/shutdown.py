"""Graceful shutdown handler for InfoMesh node.

Feature #9: Registers SIGTERM/SIGINT handlers for clean shutdown.
Ensures database connections, HTTP clients, and P2P peers are closed
properly when the process is terminated.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any

import structlog

logger = structlog.get_logger()


class GracefulShutdown:
    """Manage graceful shutdown of the InfoMesh node.

    Usage::

        shutdown = GracefulShutdown()
        shutdown.register(app_context)
        # ... run main loop ...
        # On SIGTERM/SIGINT, cleanup runs automatically.
    """

    def __init__(self) -> None:
        self._shutting_down = False
        self._context: Any | None = None
        self._callbacks: list[object] = []

    def register(self, context: Any) -> None:
        """Register AppContext and signal handlers."""
        self._context = context

        import contextlib

        loop = None
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()

        if loop is not None:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._handle_signal)
        else:
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, self._sync_handler)

    def add_callback(self, callback: object) -> None:
        """Add a cleanup callback to run on shutdown."""
        self._callbacks.append(callback)

    def _try_set_shutting_down(self) -> bool:
        """Atomically check and set the shutdown flag.

        Returns True if this call transitioned to shutting-down state.
        Prevents double-cleanup on rapid repeated signals.
        """
        if self._shutting_down:
            return False
        self._shutting_down = True
        return True

    def _handle_signal(self) -> None:
        """Async signal handler — schedules cleanup."""
        # Atomic check-and-set to prevent double cleanup on rapid signals
        if not self._try_set_shutting_down():
            return
        logger.info("shutdown_signal_received")
        asyncio.ensure_future(self.cleanup())

    def _sync_handler(self, signum: int, frame: Any) -> None:
        """Synchronous signal handler fallback."""
        if not self._try_set_shutting_down():
            return
        logger.info("shutdown_signal_received", signal=signum)
        if self._context is not None:
            self._context.close()
        raise SystemExit(0)

    async def cleanup(self) -> None:
        """Run all cleanup tasks."""
        logger.info("graceful_shutdown_starting")

        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb()
                elif callable(cb):
                    cb()
            except Exception:  # noqa: BLE001
                logger.warning("shutdown_callback_error", exc_info=True)

        if self._context is not None:
            try:
                if hasattr(self._context, "close_async"):
                    await self._context.close_async()
                else:
                    self._context.close()
            except Exception:  # noqa: BLE001
                logger.warning("shutdown_context_error", exc_info=True)

        logger.info("graceful_shutdown_complete")

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down
