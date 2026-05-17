"""Background music player for the dashboard.

Plays MP3/audio files using system CLI players (mpv, ffplay).
Runs as a subprocess so it doesn't block the TUI event loop.

Usage::

    player = BGMPlayer()
    player.play("assets/bgm/chill.mp3", volume=50)  # loop at 50%
    player.play_sfx("assets/bgm/coin.mp3")           # one-shot effect
    player.stop()                                     # stop BGM
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

import structlog

logger = structlog.get_logger()

# Supported players in order of preference.
_PLAYERS: list[tuple[str, list[str]]] = [
    # mpv has smoother loop handling and explicit audio buffering.
    (
        "mpv",
        [
            "--no-video",
            "--really-quiet",
            "--no-terminal",
            "--loop-file=inf",
            "--gapless-audio=yes",
            "--audio-buffer=2.0",
        ],
    ),
    # ffplay: no video, quiet, loop forever
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", "-loop", "0"]),
]

# BGM cache directory (~/.infomesh/bgm/ — downloaded on first launch)
_BGM_CACHE_DIR = Path.home() / ".infomesh" / "bgm"

# GitHub raw URLs for BGM assets
_BGM_REPO_BASE = (
    "https://raw.githubusercontent.com/dotnetpower/infomesh/main/infomesh/assets/bgm"
)

_BGM_FILES: list[str] = [
    "infomesh-bg-fade.mp3",
    "coin-street-fighter.mp3",
]

_MPV_INSTALL_TIMEOUT_SECONDS = 180


def _sudo_prefix() -> list[str] | None:
    """Return a non-interactive privilege prefix for system package managers."""
    if os.name != "posix":
        return None
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    if shutil.which("sudo"):
        return ["sudo", "-n"]
    return None


def _candidate_mpv_install_commands() -> list[list[str]]:
    """Build supported non-interactive mpv install commands for this host."""
    commands: list[list[str]] = []
    privilege_prefix = _sudo_prefix()

    if privilege_prefix is not None:
        if shutil.which("apt-get"):
            commands.append([*privilege_prefix, "apt-get", "install", "-y", "mpv"])
        if shutil.which("dnf"):
            commands.append([*privilege_prefix, "dnf", "install", "-y", "mpv"])
        if shutil.which("yum"):
            commands.append([*privilege_prefix, "yum", "install", "-y", "mpv"])
        if shutil.which("pacman"):
            commands.append([*privilege_prefix, "pacman", "-S", "--noconfirm", "mpv"])
        if shutil.which("apk"):
            commands.append([*privilege_prefix, "apk", "add", "mpv"])
        if shutil.which("zypper"):
            commands.append(
                [*privilege_prefix, "zypper", "--non-interactive", "install", "mpv"]
            )

    if shutil.which("brew"):
        commands.append(["brew", "install", "mpv"])

    return commands


def _install_command_manager(command: list[str]) -> str:
    """Return the package manager name from a static install command."""
    if command[:2] == ["sudo", "-n"]:
        return command[2]
    return command[0]


def _install_mpv_best_effort(
    *, timeout_seconds: int = _MPV_INSTALL_TIMEOUT_SECONDS
) -> bool:
    """Try to install mpv without interactive prompts.

    Returns ``True`` only when ``mpv`` is available after the attempt. Failures are
    logged and otherwise non-fatal so BGM can still fall back to ``ffplay``.
    """
    if shutil.which("mpv"):
        return True

    commands = _candidate_mpv_install_commands()
    if not commands:
        logger.info("bgm_mpv_install_unavailable")
        return False

    for command in commands:
        manager = _install_command_manager(command)
        logger.info("bgm_mpv_install_start", manager=manager)
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("bgm_mpv_install_timeout", manager=manager)
            continue
        except OSError as exc:
            logger.warning(
                "bgm_mpv_install_failed",
                manager=manager,
                error=str(exc),
            )
            continue

        if result.returncode == 0 and shutil.which("mpv"):
            logger.info("bgm_mpv_install_succeeded", manager=manager)
            return True
        logger.warning(
            "bgm_mpv_install_failed",
            manager=manager,
            returncode=result.returncode,
        )

    return shutil.which("mpv") is not None


def ensure_bgm_assets() -> Path:
    """Download BGM assets from GitHub if not already cached.

    Returns the path to the BGM cache directory.
    Never raises — all errors are silently logged so BGM
    failures never affect dashboard startup.
    """
    try:
        _BGM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.debug("bgm_cache_dir_failed")
        return _BGM_CACHE_DIR

    failed_downloads: list[str] = []
    for filename in _BGM_FILES:
        local_path = _BGM_CACHE_DIR / filename
        if local_path.exists():
            continue
        url = f"{_BGM_REPO_BASE}/{filename}"
        try:
            logger.info("bgm_downloading", file=filename)
            urlretrieve(url, local_path)  # noqa: S310
            logger.info("bgm_downloaded", file=filename)
        except Exception as exc:  # noqa: BLE001
            failed_downloads.append(filename)
            logger.warning("bgm_download_failed", file=filename, error=str(exc))
            # Remove partial download
            with contextlib.suppress(OSError):
                local_path.unlink(missing_ok=True)

    if failed_downloads and not any(
        (_BGM_CACHE_DIR / filename).exists() for filename in _BGM_FILES
    ):
        logger.warning("bgm_assets_unavailable", files=failed_downloads)

    return _BGM_CACHE_DIR


def _build_volume_args(player_cmd: str, volume: int) -> list[str]:
    """Build volume CLI arguments for a given player.

    Args:
        player_cmd: Player command name ("ffplay" or "mpv").
        volume: Volume percentage 0-100.
    """
    if player_cmd == "ffplay":
        return ["-volume", str(volume)]
    if player_cmd == "mpv":
        return [f"--volume={volume}"]
    return []


def _find_player() -> tuple[str, list[str]] | None:
    """Find the first available audio player on the system."""
    for cmd, args in _PLAYERS:
        if shutil.which(cmd):
            return cmd, args
    return None


def kill_orphaned_bgm() -> None:
    """Kill any orphaned BGM player processes from previous runs.

    Uses ``pgrep`` to find ffplay/mpv processes whose command line
    contains the infomesh BGM cache directory, then terminates them.
    This prevents duplicate BGM playback across dashboard restarts.
    """
    # Match both the cached path (~/.infomesh/bgm/) and the legacy
    # in-tree path (assets/bgm/) so orphans from either location
    # are cleaned up.
    patterns = ["assets/bgm", r"\.infomesh/bgm"]
    for player in ("ffplay", "mpv"):
        for pat in patterns:
            try:
                result = subprocess.run(
                    ["pgrep", "-f", f"{player}.*{pat}"],
                    capture_output=True,
                    text=True,
                )
                for line in result.stdout.strip().splitlines():
                    pid = int(line.strip())
                    if pid == os.getpid():
                        continue
                    try:
                        os.kill(pid, signal.SIGTERM)
                        logger.info("bgm_orphan_killed", pid=pid, player=player)
                    except (ProcessLookupError, PermissionError):
                        pass
            except (FileNotFoundError, ValueError):
                pass


def _restore_best_effort_priority() -> None:
    """Try to keep audio playback at normal scheduling priority."""
    if sys.platform == "win32":
        return
    try:
        current = os.nice(0)
        if current > 0:
            os.nice(-current)
    except OSError:
        pass


def _popen_kwargs() -> dict[str, Any]:
    """Build subprocess kwargs shared by BGM and SFX players."""
    kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform != "win32":
        kwargs["start_new_session"] = True
        kwargs["preexec_fn"] = _restore_best_effort_priority
    return kwargs


class BGMPlayer:
    """Background music player using system audio tools.

    Attributes:
        is_playing: Whether music is currently playing.
    """

    # Maximum consecutive auto-restarts before giving up.
    _MAX_AUTO_RESTARTS: int = 5

    def __init__(self, *, auto_install_mpv: bool = False) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._current_file: Path | None = None
        self._sfx_procs: list[subprocess.Popen[bytes]] = []
        self._auto_install_mpv = auto_install_mpv
        self._mpv_install_attempted = False
        player = _find_player()
        self._player_cmd = player[0] if player else None
        self._player_args = player[1] if player else []
        self._volume: int = 100
        self._auto_restart_count: int = 0
        self._intentionally_stopped: bool = False

    @property
    def is_playing(self) -> bool:
        """Check if a track is currently playing."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def check_and_restart(self) -> bool:
        """Check if BGM crashed and auto-restart if needed.

        Call this periodically (e.g. every few seconds) to detect
        unexpected ffplay/mpv process exits and restart playback.

        Returns:
            True if BGM was restarted, False otherwise.
        """
        if self._intentionally_stopped:
            return False
        if self._current_file is None:
            return False
        if self.is_playing:
            # Reset counter when confirmed alive.
            self._auto_restart_count = 0
            return False
        if self._auto_restart_count >= self._MAX_AUTO_RESTARTS:
            logger.warning(
                "bgm_auto_restart_limit",
                count=self._auto_restart_count,
            )
            return False

        # Process died unexpectedly — attempt restart.
        exit_code = self._proc.poll() if self._proc is not None else None
        logger.info(
            "bgm_auto_restarting",
            file=self._current_file.name,
            exit_code=exit_code,
            attempt=self._auto_restart_count + 1,
        )
        saved_file = self._current_file
        self._auto_restart_count += 1
        # Don't call self.stop() — it sets _intentionally_stopped.
        self._proc = None
        return self.play(saved_file, volume=self._volume)

    @property
    def available(self) -> bool:
        """Whether an audio player is available on this system."""
        return self._player_cmd is not None

    def _refresh_player(self) -> None:
        """Refresh the selected player after an installation attempt."""
        player = _find_player()
        self._player_cmd = player[0] if player else None
        self._player_args = player[1] if player else []

    def _ensure_player_available(self) -> bool:
        """Ensure an audio player is available, installing mpv on first use."""
        if self._player_cmd != "mpv" and shutil.which("mpv"):
            self._refresh_player()
        elif (
            self._auto_install_mpv
            and not self._mpv_install_attempted
            and not shutil.which("mpv")
        ):
            self._mpv_install_attempted = True
            _install_mpv_best_effort()
            self._refresh_player()
        elif self._player_cmd is None:
            self._refresh_player()
        return self._player_cmd is not None

    def play(
        self,
        path: str | Path,
        *,
        loop: bool = True,
        volume: int = 100,
    ) -> bool:
        """Start playing an audio file as background music.

        Args:
            path: Path to the audio file (MP3, WAV, OGG, etc.).
            loop: Whether to loop the track. Default True.
            volume: Playback volume 0-100. Default 100.

        Returns:
            True if playback started successfully.
        """
        if not self._ensure_player_available():
            logger.warning(
                "bgm_no_player",
                hint="Install ffplay (ffmpeg) or mpv for BGM support",
            )
            return False
        player_cmd = self._player_cmd
        if player_cmd is None:
            return False

        path = Path(path).resolve()
        if not path.exists():
            logger.warning("bgm_file_not_found", path=str(path))
            return False

        # Stop any current playback (this instance)
        self.stop()

        # Reset intentional stop flag — we're starting fresh.
        self._intentionally_stopped = False
        self._auto_restart_count = 0

        # Kill orphaned BGM processes from previous runs
        kill_orphaned_bgm()

        self._volume = max(0, min(100, volume))

        # Build command
        cmd = [player_cmd, *self._player_args]

        # Remove loop args if not looping
        if not loop:
            if player_cmd == "ffplay":
                cmd = [c for c in cmd if c not in ("-loop", "0")]
            elif player_cmd == "mpv":
                cmd = [c for c in cmd if not c.startswith("--loop")]

        # Add volume control
        cmd.extend(_build_volume_args(player_cmd, self._volume))
        cmd.append(str(path))

        try:
            self._proc = subprocess.Popen(cmd, **_popen_kwargs())
            self._current_file = path
            logger.info(
                "bgm_started",
                file=path.name,
                player=player_cmd,
                volume=self._volume,
            )
            return True
        except OSError as exc:
            logger.error("bgm_start_failed", error=str(exc))
            return False

    def play_sfx(self, path: str | Path, *, volume: int = 100) -> bool:
        """Play a one-shot sound effect on top of current BGM.

        Args:
            path: Path to the audio file.
            volume: Playback volume 0-100. Default 100.

        Returns:
            True if SFX playback started successfully.
        """
        if not self._ensure_player_available():
            return False
        player_cmd = self._player_cmd
        if player_cmd is None:
            return False

        path = Path(path)
        if not path.exists():
            logger.warning("sfx_file_not_found", path=str(path))
            return False

        # Reap finished SFX processes
        self._sfx_procs = [p for p in self._sfx_procs if p.poll() is None]

        # Build one-shot command (no loop)
        cmd: list[str] = [player_cmd]
        if player_cmd == "ffplay":
            cmd.extend(["-nodisp", "-autoexit", "-loglevel", "quiet"])
        elif player_cmd == "mpv":
            cmd.extend(["--no-video", "--really-quiet"])

        vol = max(0, min(100, volume))
        cmd.extend(_build_volume_args(player_cmd, vol))
        cmd.append(str(path))

        try:
            proc = subprocess.Popen(cmd, **_popen_kwargs())
            self._sfx_procs.append(proc)
            logger.info("sfx_started", file=path.name)
            return True
        except OSError as exc:
            logger.error("sfx_start_failed", error=str(exc))
            return False

    def stop(self) -> None:
        """Stop BGM and all SFX playback."""
        self._intentionally_stopped = True
        try:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                logger.info("bgm_stopped")
        except OSError:
            pass
        self._proc = None
        self._current_file = None

        # Kill any running SFX processes
        for proc in self._sfx_procs:
            try:
                if proc.poll() is None:
                    proc.terminate()
            except OSError:
                pass
        self._sfx_procs.clear()

    def toggle(self, path: str | Path, *, volume: int | None = None) -> bool:
        """Toggle playback: stop if playing, start if stopped.

        Args:
            path: Path to the audio file.
            volume: Volume override. Uses last set volume if None.

        Returns:
            True if now playing, False if stopped.
        """
        if self.is_playing:
            self.stop()
            return False
        vol = volume if volume is not None else self._volume
        return self.play(path, volume=vol)
