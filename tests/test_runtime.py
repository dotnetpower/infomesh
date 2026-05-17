"""Tests for runtime process coordination helpers."""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from infomesh.runtime import (
    StartupLock,
    build_runtime_status,
    clear_pid_file,
    is_infomesh_process,
    mark_runtime_stopped,
    pid_path,
    read_live_pid,
    read_runtime_status,
    request_graceful_stop,
    runtime_status_path,
    startup_lock_path,
    wait_for_process_exit,
    write_pid_file,
    write_runtime_status,
)


class TestRuntimePidHelpers:
    def test_paths_are_data_dir_scoped(self, tmp_path: Path) -> None:
        assert pid_path(tmp_path) == tmp_path / "infomesh.pid"
        assert startup_lock_path(tmp_path) == tmp_path / "infomesh.start.lock"
        assert runtime_status_path(tmp_path) == tmp_path / "runtime_status.json"

    def test_current_process_counts_as_infomesh_process(self) -> None:
        assert is_infomesh_process(os.getpid()) is True

    def test_read_live_pid_returns_current_process(self, tmp_path: Path) -> None:
        write_pid_file(tmp_path, os.getpid())

        assert read_live_pid(tmp_path) == os.getpid()

    def test_read_live_pid_cleans_invalid_file(self, tmp_path: Path) -> None:
        pid_path(tmp_path).write_text("not-a-pid", encoding="utf-8")

        assert read_live_pid(tmp_path) is None
        assert not pid_path(tmp_path).exists()

    def test_read_live_pid_cleans_unrelated_running_process(
        self,
        tmp_path: Path,
    ) -> None:
        write_pid_file(tmp_path, 12345)

        with (
            patch("infomesh.runtime.is_process_running", return_value=True),
            patch("infomesh.runtime._process_cmdline", return_value="python other.py"),
        ):
            assert read_live_pid(tmp_path) is None

        assert not pid_path(tmp_path).exists()

    def test_clear_pid_file_only_removes_matching_pid(self, tmp_path: Path) -> None:
        write_pid_file(tmp_path, 123)

        clear_pid_file(tmp_path, 456)
        assert pid_path(tmp_path).exists()

        clear_pid_file(tmp_path, 123)
        assert not pid_path(tmp_path).exists()


class TestStartupLock:
    def test_second_lock_waits_out_while_first_is_held(self, tmp_path: Path) -> None:
        first = StartupLock(tmp_path)
        second = StartupLock(
            tmp_path,
            timeout_seconds=0.01,
            poll_interval_seconds=0.001,
        )

        assert first.acquire() is True
        try:
            assert second.acquire() is False
        finally:
            first.release()
            second.release()

    def test_lock_can_be_reacquired_after_release(self, tmp_path: Path) -> None:
        lock = StartupLock(tmp_path)

        assert lock.acquire() is True
        lock.release()
        assert lock.acquire() is True
        lock.release()


class TestRuntimeStatus:
    def test_build_runtime_status_contains_resource_fields(self) -> None:
        state = SimpleNamespace(
            degrade_level=SimpleNamespace(name="WARNING"),
            cpu_percent=61.2,
            memory_percent=71.7,
            process_memory_mb=512.4,
            process_memory_limit_mb=2048,
            process_memory_ratio=0.25,
            throttle_factor=0.5,
            checks_performed=3,
        )

        status = build_runtime_status(
            pid=123,
            role="full",
            started_at=time.time() - 5,
            no_crawl=False,
            governor_state=state,
        )

        assert status["status"] == "running"
        assert status["degrade_level"] == "WARNING"
        assert status["process_memory_limit_mb"] == 2048
        assert status["process_memory_ratio"] == 0.25
        assert status["checks_performed"] == 3

    def test_write_and_read_runtime_status(self, tmp_path: Path) -> None:
        write_runtime_status(
            tmp_path,
            {"status": "running", "pid": 123, "updated_at": time.time()},
        )

        assert read_runtime_status(tmp_path)["status"] == "running"

    def test_read_runtime_status_marks_stale(self, tmp_path: Path) -> None:
        write_runtime_status(
            tmp_path,
            {"status": "running", "pid": 123, "updated_at": time.time() - 60},
        )

        status = read_runtime_status(tmp_path, max_age_seconds=1)

        assert status["status"] == "stopped"
        assert status["stale"] is True
        assert status["pid"] == 123

    def test_read_runtime_status_cleans_corrupt_file(self, tmp_path: Path) -> None:
        runtime_status_path(tmp_path).write_text("not json", encoding="utf-8")

        assert read_runtime_status(tmp_path) == {}
        assert not runtime_status_path(tmp_path).exists()

    def test_mark_runtime_stopped_keeps_other_owner_status(
        self, tmp_path: Path
    ) -> None:
        write_runtime_status(
            tmp_path,
            {"status": "running", "pid": 111, "updated_at": time.time()},
        )

        mark_runtime_stopped(tmp_path, 222)

        assert read_runtime_status(tmp_path)["status"] == "running"

    def test_mark_runtime_stopped_updates_matching_owner(self, tmp_path: Path) -> None:
        write_runtime_status(
            tmp_path,
            {"status": "running", "pid": 111, "updated_at": time.time()},
        )

        mark_runtime_stopped(tmp_path, 111)

        status = read_runtime_status(tmp_path)
        assert status["status"] == "stopped"
        assert status["pid"] == 111


class TestRuntimeStop:
    def test_wait_for_process_exit_returns_true_for_missing_process(self) -> None:
        assert wait_for_process_exit(999999999, timeout_seconds=0.01) is True

    def test_request_graceful_stop_sends_sigterm_and_waits(self) -> None:
        with (
            patch("infomesh.runtime.os.kill") as mock_kill,
            patch("infomesh.runtime.wait_for_process_exit", return_value=True),
        ):
            assert request_graceful_stop(123, timeout_seconds=1.0) is True

        mock_kill.assert_called_once_with(123, 15)
