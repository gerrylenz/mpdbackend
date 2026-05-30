#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cover art extraction and caching for mpdbackend."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from io import BytesIO

from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data")

COVER_DIR = os.getenv("MPDBACKEND_COVER_DIR", os.path.join(DEFAULT_DATA_DIR, "covers"))
COVER_NAME_RE = re.compile(r"^cover_[0-9a-f]{16,64}\.jpg$")
FOLDER_COVER_NAMES = ("cover.jpg", "folder.jpg", "Folder.jpg", "cover.png", "folder.png")


class CoverService:
    """Extrahiert, verarbeitet und cached Album-Cover als JPEG."""

    def __init__(self, cover_dir: str | None = None) -> None:
        """Setzt Cover-Verzeichnis und initialen Platzhalter."""
        self.cover_dir = cover_dir or COVER_DIR
        self.current = "blank.jpg"
        os.makedirs(self.cover_dir, exist_ok=True)

    def _ffmpeg_extract_map(self, path: str, map_selector: str) -> bytes | None:
        """Extrahiert ein Bild per ffmpeg mit gegebenem Stream-Map."""
        cmd = [
            "ffmpeg",
            "-loglevel", "error",
            "-i", path,
            "-map", map_selector,
            "-frames:v", "1",
            "-f", "image2pipe",
            "pipe:1",
        ]

        try:
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3
            )
            if result.returncode != 0 or not result.stdout:
                return None
            return result.stdout
        except Exception:
            return None

    def ffmpeg_extract(self, path: str) -> bytes | None:
        """Extrahiert Cover aus Tags/Video oder Ordner-Cover-Datei."""
        for map_selector in ("0:v:0", "0:p:0"):
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
        raw = self.ffmpeg_extract(audio_file)
        if not raw:
            self.current = "blank.jpg"
            return

        img = self.process(raw)
        if not img:
            self.current = "blank.jpg"
            return

        self.current = self.cache_name(audio_file)
        path = os.path.join(self.cover_dir, self.current)

        if not os.path.exists(path):
            tmp = path + ".tmp"
            with open(tmp, "wb") as handle:
                handle.write(img)
            os.replace(tmp, path)

    def cover_name(self) -> str:
        """Liefert aktuellen Cache-Dateinamen oder leer bei blank."""
        return self.current if self.current != "blank.jpg" else ""

    def path(self) -> str:
        """Liefert absoluten Pfad zum aktuellen Cover im Cache."""
        return os.path.join(self.cover_dir, self.current)
