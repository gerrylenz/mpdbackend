#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cover art extraction and caching for mpdbackend."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
from io import BytesIO

from PIL import Image

logger = logging.getLogger("mpdbackend.cover")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data")

COVER_NAME_RE = re.compile(r"^cover_[0-9a-f]{16,64}\.jpg$")
FOLDER_COVER_NAMES = ("cover.jpg", "folder.jpg", "Folder.jpg", "cover.png", "folder.png")


def cover_dir_from_env() -> str:
    """Cover-Cache-Verzeichnis (zur Laufzeit, nach load_env_file)."""
    return os.getenv(
        "MPDBACKEND_COVER_DIR", os.path.join(DEFAULT_DATA_DIR, "covers")
    )


class CoverService:
    """Extrahiert, verarbeitet und cached Album-Cover als JPEG."""

    def __init__(self, cover_dir: str | None = None) -> None:
        """Setzt Cover-Verzeichnis und initialen Platzhalter."""
        self.cover_dir = cover_dir or cover_dir_from_env()
        self.current = "blank.jpg"
        os.makedirs(self.cover_dir, exist_ok=True)

    def _ffmpeg_run(self, cmd: list[str], timeout: float = 8) -> bytes | None:
        """Führt ffmpeg aus und liefert stdout oder None."""
        try:
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
            if result.returncode != 0 or not result.stdout:
                return None
            return result.stdout
        except Exception:
            return None

    def _ffmpeg_extract_map(self, path: str, map_selector: str) -> bytes | None:
        """Extrahiert ein Bild per ffmpeg mit gegebenem Stream-Map."""
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            path,
            "-map",
            map_selector,
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "pipe:1",
        ]
        return self._ffmpeg_run(cmd)

    def _ffmpeg_extract_first_video(self, path: str) -> bytes | None:
        """Extrahiert das erste Video-/Attached-Pic-Frame ohne explizites -map."""
        cmd = [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            path,
            "-an",
            "-frames:v",
            "1",
            "-f",
            "image2pipe",
            "pipe:1",
        ]
        return self._ffmpeg_run(cmd)

    def ffmpeg_extract(self, path: str) -> bytes | None:
        """Extrahiert Cover aus Tags/Video oder Ordner-Cover-Datei."""
        if not os.path.isfile(path):
            return self._folder_cover(path)

        raw = self._ffmpeg_extract_first_video(path)
        if raw:
            return raw

        for map_selector in ("0:v:0", "0:v", "0:V:0"):
            raw = self._ffmpeg_extract_map(path, map_selector)
            if raw:
                return raw
        return self._folder_cover(path)

    def _folder_cover(self, audio_file: str) -> bytes | None:
        """Liest cover.jpg/folder.jpg aus dem Album-Ordner."""
        folder = os.path.dirname(audio_file)
        for name in FOLDER_COVER_NAMES:
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "rb") as handle:
                        return handle.read()
                except OSError:
                    return None
        return None

    def process(self, raw: bytes) -> bytes | None:
        """Skaliert Rohbild auf max. 512px und encodiert als JPEG."""
        try:
            img = Image.open(BytesIO(raw))
            img = img.convert("RGB")
            img.thumbnail((512, 512))

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception:
            return None

    def cache_name(self, audio_file: str) -> str:
        """Erzeugt stabilen Cache-Dateinamen aus Pfad und Datei-Metadaten."""
        stat = os.stat(audio_file)
        key = f"{audio_file}:{stat.st_size}:{stat.st_mtime_ns}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return f"cover_{digest}.jpg"

    def generate(self, audio_file: str) -> None:
        """Extrahiert Cover und schreibt es in den Cover-Cache."""
        if not os.path.isfile(audio_file):
            logger.warning("Cover: audio file not found: %s", audio_file)
            self.current = "blank.jpg"
            return

        raw = self.ffmpeg_extract(audio_file)
        img = self.process(raw) if raw else None
        if not img:
            folder_raw = self._folder_cover(audio_file)
            img = self.process(folder_raw) if folder_raw else None
        if not img:
            logger.debug("Cover: no image for %s", audio_file)
            self.current = "blank.jpg"
            return

        try:
            self.current = self.cache_name(audio_file)
        except OSError as err:
            logger.warning("Cover: stat failed for %s: %s", audio_file, err)
            self.current = "blank.jpg"
            return

        path = os.path.join(self.cover_dir, self.current)

        if not os.path.exists(path):
            tmp = path + ".tmp"
            try:
                with open(tmp, "wb") as handle:
                    handle.write(img)
                os.replace(tmp, path)
                logger.info("Cover cached: %s", self.current)
            except OSError as err:
                logger.warning("Cover: write failed %s: %s", path, err)
                self.current = "blank.jpg"

    def cover_name(self) -> str:
        """Liefert aktuellen Cache-Dateinamen oder leer bei blank."""
        return self.current if self.current != "blank.jpg" else ""

    def path(self) -> str:
        """Liefert absoluten Pfad zum aktuellen Cover im Cache."""
        return os.path.join(self.cover_dir, self.current)
