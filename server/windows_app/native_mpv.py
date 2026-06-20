#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native HTTP-Stream-Wiedergabe über mpv (geringe Latenz, kein Browser-Puffer)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from windows_util import app_install_dir


def mpv_search_dirs() -> list[Path]:
    base = app_install_dir()
    return [base, base / "mpv"]


def resolve_mpv_binary(configured: str = "") -> str:
    explicit = (configured or "").strip()
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"mpv nicht gefunden: {explicit}")
        return str(path.resolve())

    for directory in mpv_search_dirs():
        for name in ("mpv.exe", "mpv"):
            candidate = directory / name
            if candidate.is_file():
                return str(candidate.resolve())

    found = shutil.which("mpv") or shutil.which("mpv.exe")
    if found:
        return found

    raise FileNotFoundError(
        "mpv nicht gefunden. mpv.exe neben MPD-Player.exe legen oder von "
        "https://mpv.io/installation/ installieren."
    )


def build_mpv_command(url: str, *, binary: str) -> list[str]:
    """mpv mit minimaler Pufferung für Live-HTTP-Streams."""
    stream_url = (url or "").strip()
    if not stream_url:
        raise ValueError("stream URL is required")

    return [
        binary,
        "--no-video",
        "--force-window=no",
        "--really-quiet",
        "--no-terminal",
        "--cache=no",
        "--demuxer-readahead-secs=0",
        "--stream-buffer-size=4096",
        "--untimed",
        stream_url,
    ]


def popen_mpv(cmd: list[str]) -> subprocess.Popen:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, **kwargs)


def terminate_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class NativeMpvPlayer:
    def __init__(self, mpv_bin: str = "") -> None:
        self._mpv_bin = mpv_bin
        self._proc: subprocess.Popen | None = None
        self._url = ""

    @property
    def playing(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @property
    def url(self) -> str:
        return self._url

    def start(self, url: str) -> None:
        stream_url = (url or "").strip()
        if not stream_url:
            raise ValueError("stream URL is required")

        if self.playing and self._url == stream_url:
            return

        self.stop()
        binary = resolve_mpv_binary(self._mpv_bin)
        cmd = build_mpv_command(stream_url, binary=binary)
        self._proc = popen_mpv(cmd)
        self._url = stream_url

    def stop(self) -> None:
        terminate_process(self._proc)
        self._proc = None
        self._url = ""
