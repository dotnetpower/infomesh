#!/usr/bin/env python3
"""BGM Diagnostic Script — Monitors ffplay/mpv process lifecycle.

Run this script to diagnose why BGM playback stops unexpectedly.
It simulates BGM playback outside the Textual TUI and monitors the
ffplay/mpv subprocess continuously, logging every state change.

Usage::

    # Basic: play BGM and monitor for 5 minutes (default)
    uv run python scripts/diag_bgm.py

    # Extended: monitor for 30 minutes
    uv run python scripts/diag_bgm.py --duration 1800

    # Test idle-timer behavior (simulates crawl idleness)
    uv run python scripts/diag_bgm.py --test-idle

    # Check for orphaned processes only (no playback)
    uv run python scripts/diag_bgm.py --check-orphans

    # Test with custom volume
    uv run python scripts/diag_bgm.py --volume 30
"""

from __future__ import annotations

import argparse
import contextlib
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root to sys.path so we can import infomesh
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def _log(msg: str) -> None:
    """Print timestamped log message."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}] {msg}", flush=True)


def _check_audio_system() -> dict[str, str | bool]:
    """Check audio subsystem availability."""
    info: dict[str, str | bool] = {
        "os": platform.system(),
        "arch": platform.machine(),
    }

    # Check PulseAudio
    info["pulseaudio"] = shutil.which("pactl") is not None
    if info["pulseaudio"]:
        try:
            r = subprocess.run(
                ["pactl", "info"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            info["pulseaudio_running"] = r.returncode == 0
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    if "Server Name:" in line:
                        info["audio_server"] = line.split(":", 1)[1].strip()
        except Exception:
            info["pulseaudio_running"] = False

    # Check PipeWire
    info["pipewire"] = shutil.which("pw-cli") is not None
    if info["pipewire"]:
        try:
            r = subprocess.run(
                ["pw-cli", "info", "0"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            info["pipewire_running"] = r.returncode == 0
        except Exception:
            info["pipewire_running"] = False

    # Check ALSA
    info["aplay"] = shutil.which("aplay") is not None

    return info


def _check_players() -> dict[str, str | None]:
    """Check available audio players."""
    players: dict[str, str | None] = {}
    for cmd in ("ffplay", "mpv", "aplay", "ffmpeg"):
        path = shutil.which(cmd)
        players[cmd] = path
        if path:
            try:
                if cmd == "ffplay":
                    r = subprocess.run(
                        [cmd, "-version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    ver_line = r.stdout.splitlines()[0] if r.stdout else "unknown"
                    players[f"{cmd}_version"] = ver_line
                elif cmd == "mpv":
                    r = subprocess.run(
                        [cmd, "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    ver_line = r.stdout.splitlines()[0] if r.stdout else "unknown"
                    players[f"{cmd}_version"] = ver_line
            except Exception:
                pass
    return players


def _find_orphans() -> list[dict[str, str | int]]:
    """Find orphaned BGM processes."""
    orphans: list[dict[str, str | int]] = []
    for player in ("ffplay", "mpv"):
        # Check both old pattern (assets/bgm) and new pattern (.infomesh/bgm)
        for pattern in (f"{player}.*assets/bgm", f"{player}.*\\.infomesh/bgm"):
            try:
                r = subprocess.run(
                    ["pgrep", "-af", pattern],
                    capture_output=True,
                    text=True,
                )
                for line in r.stdout.strip().splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:  # noqa: PLR2004
                        pid = int(parts[0])
                        if pid != os.getpid():
                            orphans.append(
                                {
                                    "pid": pid,
                                    "player": player,
                                    "cmdline": parts[1],
                                }
                            )
            except (FileNotFoundError, ValueError):
                pass
    return orphans


def _get_proc_info(pid: int) -> dict[str, str | int | float]:
    """Get detailed info about a process."""
    info: dict[str, str | int | float] = {"pid": pid}
    try:
        # /proc/<pid>/status has memory, state info
        status_path = Path(f"/proc/{pid}/status")
        if status_path.exists():
            content = status_path.read_text()
            for line in content.splitlines():
                if line.startswith("VmRSS:"):
                    info["rss_kb"] = int(line.split(":")[1].strip().split()[0])
                elif line.startswith("State:"):
                    info["state"] = line.split(":")[1].strip()
                elif line.startswith("Threads:"):
                    info["threads"] = int(line.split(":")[1].strip())

        # /proc/<pid>/fd count
        fd_dir = Path(f"/proc/{pid}/fd")
        if fd_dir.exists():
            with contextlib.suppress(PermissionError):
                info["fd_count"] = len(list(fd_dir.iterdir()))

    except (OSError, ValueError):
        pass
    return info


def _monitor_playback(
    bgm_file: Path,
    duration_secs: int,
    volume: int,
    poll_interval: float = 2.0,
) -> None:
    """Start BGM and monitor the subprocess continuously."""
    from infomesh.dashboard.bgm import BGMPlayer

    player = BGMPlayer()
    if not player.available:
        _log("ERROR: No audio player found (ffplay/mpv)")
        return

    _log(f"Starting BGM: {bgm_file.name} (volume={volume}%)")
    started = player.play(bgm_file, volume=volume)
    if not started:
        _log("ERROR: BGMPlayer.play() returned False — playback failed")
        return

    proc = player._proc  # Access internal proc for diagnostics
    if proc is None:
        _log("ERROR: _proc is None after play() returned True")
        return

    pid = proc.pid
    _log(f"BGM started — PID={pid}, player={player._player_cmd}")

    start_time = time.monotonic()
    check_count = 0
    last_status = "playing"

    try:
        while time.monotonic() - start_time < duration_secs:
            time.sleep(poll_interval)
            check_count += 1
            elapsed = time.monotonic() - start_time

            poll_result = proc.poll()
            is_alive = poll_result is None

            if is_alive:
                # Gather process metrics
                proc_info = _get_proc_info(pid)
                rss = proc_info.get("rss_kb", "?")
                fds = proc_info.get("fd_count", "?")
                state = proc_info.get("state", "?")

                if check_count % 15 == 0:  # Every ~30 seconds
                    _log(
                        f"[CHECK #{check_count}] "
                        f"elapsed={elapsed:.0f}s | "
                        f"PID={pid} ALIVE | "
                        f"RSS={rss}KB | "
                        f"FDs={fds} | "
                        f"state={state}"
                    )
                last_status = "playing"
            else:
                if last_status == "playing":
                    _log(
                        f"⚠️  BGM STOPPED at {elapsed:.1f}s! "
                        f"PID={pid} exited with code={poll_result}"
                    )
                    # Check if process was killed by signal
                    if poll_result is not None and poll_result < 0:
                        sig_name = "unknown"
                        with contextlib.suppress(ValueError, AttributeError):
                            sig_name = signal.Signals(-poll_result).name
                        _log(
                            f"   Process killed by signal: {-poll_result} ({sig_name})"
                        )

                    # Check for OOM
                    try:
                        dmesg = subprocess.run(
                            ["dmesg", "--time-format", "reltime"],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        oom_lines = [
                            ln
                            for ln in dmesg.stdout.splitlines()
                            if "oom" in ln.lower() or "killed process" in ln.lower()
                        ]
                        if oom_lines:
                            _log(f"   dmesg OOM entries: {oom_lines[-3:]}")
                    except Exception:
                        pass

                    last_status = "dead"
                    _log("   Attempting to restart BGM...")

                    # Try restarting
                    if player.play(bgm_file, volume=volume):
                        proc = player._proc
                        if proc is not None:
                            pid = proc.pid
                            _log(f"   ✅ BGM restarted — new PID={pid}")
                            last_status = "playing"
                        else:
                            _log("   ❌ play() OK but _proc is None")
                    else:
                        _log("   ❌ Restart failed — aborting monitor")
                        break

    except KeyboardInterrupt:
        _log("Interrupted by user (Ctrl+C)")
    finally:
        elapsed = time.monotonic() - start_time
        _log(
            f"Monitor finished — ran for {elapsed:.0f}s, {check_count} checks performed"
        )
        player.stop()
        _log("BGM stopped (cleanup)")


def _test_idle_timer(bgm_file: Path, volume: int) -> None:
    """Simulate the crawl-idle BGM auto-stop behavior.

    This mimics what DashboardApp._check_crawl_idle_bgm() does:
    - Start BGM
    - After 10 seconds, stop it (simulating idle detection)
    - After 5 more seconds, restart it (simulating crawl resume)
    - Repeat 3 times
    """
    from infomesh.dashboard.bgm import BGMPlayer

    player = BGMPlayer()
    if not player.available:
        _log("ERROR: No audio player found")
        return

    IDLE_THRESHOLD = 10.0  # matches _CRAWL_IDLE_THRESHOLD_SECS
    RESUME_DELAY = 5.0

    _log("=== Idle Timer Simulation ===")
    _log(
        f"Simulating: play → idle-stop({IDLE_THRESHOLD}s) → "
        f"resume({RESUME_DELAY}s) × 3 cycles"
    )

    try:
        for cycle in range(1, 4):
            _log(f"\n--- Cycle {cycle}/3 ---")

            # Start BGM
            _log("Starting BGM...")
            if not player.play(bgm_file, volume=volume):
                _log("ERROR: play() failed")
                return
            _log(f"BGM playing — PID={player._proc.pid if player._proc else '?'}")

            # Wait (simulate active crawl)
            _log(f"Simulating active crawl for {IDLE_THRESHOLD}s...")
            time.sleep(IDLE_THRESHOLD)

            # Check still alive
            alive = player.is_playing
            _log(f"After {IDLE_THRESHOLD}s: is_playing={alive}")
            if not alive and player._proc:
                _log(f"   ⚠️  Process died! returncode={player._proc.poll()}")

            # Idle-stop
            _log("Idle detected — stopping BGM...")
            player.stop()
            _log(f"BGM stopped. is_playing={player.is_playing}")

            # Wait (simulate idle period)
            _log(f"Idle period ({RESUME_DELAY}s)...")
            time.sleep(RESUME_DELAY)

            # Resume
            _log("Crawl resumed — restarting BGM...")

        _log("\n=== All 3 cycles completed successfully ===")

    except KeyboardInterrupt:
        _log("Interrupted by user")
    finally:
        player.stop()
        _log("Cleanup done")


def _test_orphan_pattern() -> None:
    """Verify kill_orphaned_bgm pattern correctness."""
    from infomesh.dashboard.bgm import _BGM_CACHE_DIR

    _log("=== Orphan Pattern Check ===")

    # The actual BGM path
    bgm_path = _BGM_CACHE_DIR / "infomesh-bg-fade.mp3"
    _log(f"Actual BGM path: {bgm_path}")
    _log(f"BGM cache dir: {_BGM_CACHE_DIR}")

    # The pattern used in kill_orphaned_bgm
    old_pattern = "ffplay.*assets/bgm"
    correct_pattern = "ffplay.*\\.infomesh/bgm"

    _log(f"Old pgrep pattern: '{old_pattern}'")
    _log(f"Correct pattern:   '{correct_pattern}'")

    # Check if pattern would match the actual command line
    import re

    test_cmdline = (
        f"ffplay -nodisp -autoexit -loglevel quiet -loop 0 -volume 50 {bgm_path}"
    )
    old_match = bool(re.search(old_pattern, test_cmdline))
    new_match = bool(re.search(correct_pattern, test_cmdline))

    _log(f"Test cmdline: {test_cmdline}")
    _log(f"Old pattern matches: {old_match} {'✅' if old_match else '❌ BUG!'}")
    _log(f"New pattern matches: {new_match} {'✅' if new_match else '❌'}")

    # Check current orphans
    orphans = _find_orphans()
    if orphans:
        _log(f"\nFound {len(orphans)} orphaned BGM process(es):")
        for o in orphans:
            _log(f"  PID={o['pid']} cmd={o['cmdline']}")
    else:
        _log("\nNo orphaned BGM processes found.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="InfoMesh BGM Diagnostic Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="Monitor duration in seconds (default: 300 = 5 min)",
    )
    parser.add_argument(
        "--volume",
        type=int,
        default=30,
        help="Playback volume 0-100 (default: 30)",
    )
    parser.add_argument(
        "--test-idle",
        action="store_true",
        help="Test idle-timer stop/resume behavior",
    )
    parser.add_argument(
        "--check-orphans",
        action="store_true",
        help="Only check for orphaned BGM processes (no playback)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Status check interval in seconds (default: 2.0)",
    )
    args = parser.parse_args()

    _log("=" * 60)
    _log("InfoMesh BGM Diagnostic Tool")
    _log("=" * 60)

    # System info
    _log("\n--- Audio System ---")
    audio_info = _check_audio_system()
    for k, v in audio_info.items():
        _log(f"  {k}: {v}")

    _log("\n--- Audio Players ---")
    players = _check_players()
    for k, v in players.items():
        _log(f"  {k}: {v}")

    # Check orphans
    if args.check_orphans:
        _log("")
        _test_orphan_pattern()
        return

    # Find BGM file
    from infomesh.dashboard.bgm import ensure_bgm_assets

    _log("\n--- BGM Assets ---")
    bgm_dir = ensure_bgm_assets()
    _log(f"BGM dir: {bgm_dir}")

    bgm_file = bgm_dir / "infomesh-bg-fade.mp3"
    if not bgm_file.exists():
        _log(f"ERROR: BGM file not found: {bgm_file}")
        _log("Run the dashboard once first to download BGM assets.")
        return

    file_size = bgm_file.stat().st_size
    _log(f"BGM file: {bgm_file.name} ({file_size / 1024:.0f} KB)")

    # Run requested test
    if args.test_idle:
        _log("")
        _test_idle_timer(bgm_file, args.volume)
    else:
        _log("")
        _test_orphan_pattern()
        _log("")
        _log(f"Starting continuous monitor for {args.duration}s...")
        _log("Press Ctrl+C to stop early.\n")
        _monitor_playback(
            bgm_file,
            args.duration,
            args.volume,
            args.poll_interval,
        )


if __name__ == "__main__":
    main()
