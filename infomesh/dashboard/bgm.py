"""Background music player for the dashboard.

Plays MP3/audio files using system CLI players (ffplay, mpv).
Runs as a subprocess so it doesn't block the TUI event loop.

Usage::

    player = BGMPlayer()
    player.play("assets/bgm/chill.mp3", volume=50)  # loop at 50%
    player.play_sfx("assets/bgm/coin.mp3")           # one-shot effect
    player.stop()                                     # stop BGM
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Supported players in order of preference.
_PLAYERS: list[tuple[str, list[str]]] = [
    # ffplay: no video, quiet, loop forever
    ("ffplay", ["-nodisp", "-autoexit", "-loglevel", "quiet", "-loop", "0"]),
    # mpv: no video, loop
    ("mpv", ["--no-video", "--really-quiet", "--loop"]),
]


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


class BGMPlayer:
    """Background music player using system audio tools.

    Attributes:
        is_playing: Whether music is currently playing.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._current_file: Path | None = None
        self._sfx_procs: list[subprocess.Popen[bytes]] = []
        player = _find_player()
        self._player_cmd = player[0] if player else None
        self._player_args = player[1] if player else []
        self._volume: int = 100

    @property
    def is_playing(self) -> bool:
        """Check if a track is currently playing."""
        if self._proc is None:
            return False
        return self._proc.poll() is None

    @property
    def available(self) -> bool:
        """Whether an audio player is available on this system."""
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
        if self._player_cmd is None:
            logger.warning(
                "bgm_no_player",
                hint="Install ffplay (ffmpeg) or mpv for BGM support",
            )
            return False

        path = Path(path)
        if not path.exists():
            logger.warning("bgm_file_not_found", path=str(path))
            return False

        # Stop any current playback
        self.stop()

        self._volume = max(0, min(100, volume))

        # Build command
        cmd = [self._player_cmd, *self._player_args]

        # Remove loop args if not looping
        if not loop:
            if self._player_cmd == "ffplay":
                cmd = [c for c in cmd if c not in ("-loop", "0")]
            elif self._player_cmd == "mpv":
                cmd = [c for c in cmd if c != "--loop"]

        # Add volume control
        cmd.extend(_build_volume_args(self._player_cmd, self._volume))
        cmd.append(str(path))

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            self._current_file = path
            logger.info(
                "bgm_started",
                file=path.name,
                player=self._player_cmd,
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
        if self._player_cmd is None:
            return False

        path = Path(path)
        if not path.exists():
            logger.warning("sfx_file_not_found", path=str(path))
            return False

        # Reap finished SFX processes
        self._sfx_procs = [p for p in self._sfx_procs if p.poll() is None]

        # Build one-shot command (no loop)
        cmd: list[str] = [self._player_cmd]
        if self._player_cmd == "ffplay":
            cmd.extend(["-nodisp", "-autoexit", "-loglevel", "quiet"])
        elif self._player_cmd == "mpv":
            cmd.extend(["--no-video", "--really-quiet"])

        vol = max(0, min(100, volume))
        cmd.extend(_build_volume_args(self._player_cmd, vol))
        cmd.append(str(path))

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            self._sfx_procs.append(proc)
            logger.info("sfx_started", file=path.name)
            return True
        except OSError as exc:
            logger.error("sfx_start_failed", error=str(exc))
            return False

    def stop(self) -> None:
        """Stop BGM and all SFX playback."""
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
