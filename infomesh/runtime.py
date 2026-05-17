"""Runtime process coordination and health snapshots."""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from contextlib import AbstractContextManager
from pathlib import Path
from types import TracebackType
from typing import Any

import structlog

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None  # type: ignore[assignment]

logger = structlog.get_logger()

PID_FILE_NAME = "infomesh.pid"
STARTUP_LOCK_FILE_NAME = "infomesh.start.lock"
RUNTIME_STATUS_FILE_NAME = "runtime_status.json"
RUNTIME_STATUS_MAX_AGE_SECONDS = 30.0


def pid_path(data_dir: Path) -> Path:
    """Return the node PID file path."""
    return data_dir / PID_FILE_NAME


def startup_lock_path(data_dir: Path) -> Path:
    """Return the startup lock file path."""
    return data_dir / STARTUP_LOCK_FILE_NAME


def runtime_status_path(data_dir: Path) -> Path:
    """Return the runtime status snapshot path."""
    return data_dir / RUNTIME_STATUS_FILE_NAME


def is_process_running(pid: int) -> bool:
    """Return True if ``pid`` appears to be alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_cmdline(pid: int) -> str | None:
    """Best-effort process command line inspection."""
    if pid == os.getpid():
        return " ".join([sys.executable, *sys.argv])
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    if not proc_cmdline.exists():
        return None
    try:
        raw = proc_cmdline.read_bytes()
    except OSError:
        return None
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()


def is_infomesh_process(pid: int) -> bool:
    """Return True if ``pid`` is a running InfoMesh process."""
    if not is_process_running(pid):
        return False
    if pid == os.getpid():
        return True
    cmdline = _process_cmdline(pid)
    if cmdline is None:
        return True
    return "infomesh" in cmdline


def read_live_pid(data_dir: Path) -> int | None:
    """Return the live InfoMesh PID, cleaning invalid or stale PID files."""
    path = pid_path(data_dir)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if is_infomesh_process(pid):
        return pid
    path.unlink(missing_ok=True)
    return None


def write_pid_file(data_dir: Path, pid: int) -> None:
    """Atomically write the node PID file."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = pid_path(data_dir)
    tmp_path = path.with_suffix(".pid.tmp")
    tmp_path.write_text(str(pid), encoding="utf-8")
    tmp_path.replace(path)


def clear_pid_file(data_dir: Path, pid: int) -> None:
    """Remove the PID file only if it still belongs to ``pid``."""
    path = pid_path(data_dir)
    try:
        current_pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        return
    if current_pid == pid:
        path.unlink(missing_ok=True)


def wait_for_process_exit(
    pid: int,
    *,
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.05,
) -> bool:
    """Wait until ``pid`` exits, returning False on timeout."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_process_running(pid):
            return True
        time.sleep(poll_interval_seconds)
    return not is_process_running(pid)


class StartupLock(AbstractContextManager["StartupLock"]):
    """Cross-process lock used to serialize node startup."""

    def __init__(
        self,
        data_dir: Path,
        *,
        timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self._data_dir = data_dir
        self._timeout_seconds = timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._handle: Any | None = None
        self.acquired = False

    def acquire(self) -> bool:
        """Acquire the lock, waiting up to the configured timeout."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._handle = startup_lock_path(self._data_dir).open("a+", encoding="utf-8")
        if fcntl is None:
            self.acquired = True
            return True

        deadline = time.monotonic() + self._timeout_seconds
        while True:
            try:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle.seek(0)
                self._handle.truncate()
                self._handle.write(str(os.getpid()))
                self._handle.flush()
                self.acquired = True
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self.release()
                    return False
                time.sleep(self._poll_interval_seconds)

    def release(self) -> None:
        """Release the startup lock."""
        if self._handle is None:
            return
        if fcntl is not None and self.acquired:
            with contextlib_suppress_os_error():
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None
        self.acquired = False

    def __enter__(self) -> StartupLock:
        if not self.acquire():
            raise RuntimeError("another InfoMesh startup is already in progress")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self.release()
        return None


class contextlib_suppress_os_error(AbstractContextManager[None]):
    """Small local context manager to avoid importing contextlib in hot paths."""

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def build_runtime_status(
    *,
    pid: int,
    role: str,
    started_at: float,
    no_crawl: bool,
    governor_state: Any,
) -> dict[str, Any]:
    """Build a JSON-serializable runtime status snapshot."""
    now = time.time()
    degrade_level = getattr(governor_state.degrade_level, "name", "UNKNOWN")
    return {
        "status": "running",
        "pid": pid,
        "role": role,
        "no_crawl": no_crawl,
        "started_at": round(started_at, 3),
        "updated_at": round(now, 3),
        "uptime_seconds": round(now - started_at, 1),
        "degrade_level": degrade_level,
        "cpu_percent": round(float(governor_state.cpu_percent), 1),
        "memory_percent": round(float(governor_state.memory_percent), 1),
        "process_memory_mb": round(float(governor_state.process_memory_mb), 1),
        "process_memory_limit_mb": int(
            getattr(governor_state, "process_memory_limit_mb", 0)
        ),
        "process_memory_ratio": round(
            float(getattr(governor_state, "process_memory_ratio", 0.0)),
            3,
        ),
        "throttle_factor": round(float(governor_state.throttle_factor), 3),
        "checks_performed": int(governor_state.checks_performed),
    }


def write_runtime_status(data_dir: Path, status: dict[str, Any]) -> None:
    """Atomically write a runtime status snapshot."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = runtime_status_path(data_dir)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(status, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def mark_runtime_stopped(data_dir: Path, pid: int) -> None:
    """Mark the runtime status as stopped if the status belongs to ``pid``."""
    status = read_runtime_status(data_dir, max_age_seconds=None)
    if status and status.get("pid") not in (pid, None):
        return
    write_runtime_status(
        data_dir,
        {
            "status": "stopped",
            "pid": pid,
            "updated_at": round(time.time(), 3),
        },
    )


def read_runtime_status(
    data_dir: Path,
    *,
    max_age_seconds: float | None = RUNTIME_STATUS_MAX_AGE_SECONDS,
) -> dict[str, Any]:
    """Read the runtime status snapshot, marking stale data as stopped."""
    path = runtime_status_path(data_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return {}
    if not isinstance(data, dict):
        path.unlink(missing_ok=True)
        return {}
    if max_age_seconds is None:
        return data
    updated_at = data.get("updated_at", 0.0)
    try:
        age = time.time() - float(updated_at)
    except (TypeError, ValueError):
        age = max_age_seconds + 1.0
    if age > max_age_seconds:
        return {
            "status": "stopped",
            "pid": data.get("pid"),
            "stale": True,
            "age_seconds": round(age, 1),
        }
    return data


def request_graceful_stop(pid: int, *, timeout_seconds: float = 5.0) -> bool:
    """Send SIGTERM and wait for process exit."""
    os.kill(pid, signal.SIGTERM)
    return wait_for_process_exit(pid, timeout_seconds=timeout_seconds)
