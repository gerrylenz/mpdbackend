#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lädt ein offizielles Windows-Build von mpv (aus dem mpv-Quellcode kompiliert)
und entpackt mpv.exe inkl. DLLs für den MPD-Player.

Quelle: shinchiro/mpv-winbuild-cmake (empfohlen auf mpv.io/installation)
Lizenz: GPL — Quellcode: https://github.com/mpv-player/mpv

Beispiel:
  python bundle_mpv.py
  python bundle_mpv.py --dest dist\\mpv
  python bundle_mpv.py --skip-if-present
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

GITHUB_API = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
USER_AGENT = "mpdbackend-bundle-mpv"
DEFAULT_ASSET_PREFIX = "mpv-x86_64-"


def find_7z_exe() -> str | None:
    candidates = [
        shutil.which("7z"),
        shutil.which("7z.exe"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def extract_7z(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        import py7zr  # type: ignore[import-untyped]
    except ImportError:
        py7zr = None  # type: ignore[assignment]

    if py7zr is not None:
        with py7zr.SevenZipFile(archive, mode="r") as archive_file:
            archive_file.extractall(path=dest)
        return

    seven_zip = find_7z_exe()
    if seven_zip is None:
        raise RuntimeError(
            "7z-Entpacken fehlgeschlagen. Bitte 7-Zip installieren oder "
            "'pip install py7zr' ausführen."
        )

    result = subprocess.run(
        [seven_zip, "x", "-y", f"-o{dest}", str(archive)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "7z extract failed")


def fetch_latest_asset_url(prefix: str = DEFAULT_ASSET_PREFIX) -> tuple[str, str]:
    request = urllib.request.Request(
        GITHUB_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)

    for asset in payload.get("assets", []):
        name = str(asset.get("name", ""))
        if name.startswith(prefix) and name.endswith(".7z") and "dev" not in name:
            url = str(asset.get("browser_download_url", "")).strip()
            if url:
                return name, url

    raise RuntimeError(f"Kein mpv-Asset mit Präfix {prefix!r} in der letzten Release gefunden.")


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        data = response.read()
    dest.write_bytes(data)


def locate_mpv_dir(extracted_root: Path) -> Path:
    direct = extracted_root / "mpv.exe"
    if direct.is_file():
        return extracted_root

    for candidate in extracted_root.rglob("mpv.exe"):
        return candidate.parent

    raise FileNotFoundError("mpv.exe im Archiv nicht gefunden.")


def install_mpv_tree(source_dir: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for item in source_dir.iterdir():
        target = dest_dir / item.name
        if item.is_file():
            shutil.copy2(item, target)
            copied += 1

    if copied == 0:
        raise RuntimeError(f"Keine Dateien zum Kopieren in {source_dir}")

    mpv_exe = dest_dir / "mpv.exe"
    if not mpv_exe.is_file():
        raise FileNotFoundError(f"mpv.exe fehlt nach dem Kopieren: {mpv_exe}")

    return mpv_exe


def write_notice(dest_dir: Path, asset_name: str, download_url: str) -> None:
    notice = dest_dir / "MPV_SOURCE.txt"
    notice.write_text(
        "\n".join(
            [
                "mpv (GPL) — gebündelt für MPD-Player",
                f"Binary-Paket: {asset_name}",
                f"Download: {download_url}",
                "Quellcode: https://github.com/mpv-player/mpv",
                "Build-Skripte: https://github.com/shinchiro/mpv-winbuild-cmake",
                "",
                "Gemäß GPL können Sie den Quellcode von mpv unter der obigen URL beziehen.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def bundle_mpv(dest_dir: Path, *, arch: str = "x86_64", skip_if_present: bool) -> Path:
    mpv_exe = dest_dir / "mpv.exe"
    if skip_if_present and mpv_exe.is_file():
        print(f"mpv bereits vorhanden: {mpv_exe}")
        return mpv_exe

    prefix = f"mpv-{arch}-"
    asset_name, download_url = fetch_latest_asset_url(prefix)
    print(f"Lade {asset_name} …")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        archive = tmp_path / asset_name
        extract_dir = tmp_path / "extract"

        download_file(download_url, archive)
        extract_7z(archive, extract_dir)
        source_dir = locate_mpv_dir(extract_dir)
        mpv_path = install_mpv_tree(source_dir, dest_dir)
        write_notice(dest_dir, asset_name, download_url)

    print(f"mpv installiert: {mpv_path}")
    return mpv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="mpv-Windows-Build für MPD-Player bündeln")
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).resolve().parent / "dist" / "mpv",
        help="Zielordner für mpv.exe und DLLs (Standard: dist/mpv)",
    )
    parser.add_argument(
        "--arch",
        choices=("x86_64", "x86_64-v3", "i686", "aarch64"),
        default="x86_64",
        help="CPU-Architektur des mpv-Builds",
    )
    parser.add_argument(
        "--skip-if-present",
        action="store_true",
        help="Nicht erneut herunterladen, wenn mpv.exe schon existiert",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        bundle_mpv(args.dest.resolve(), arch=args.arch, skip_if_present=args.skip_if_present)
    except Exception as err:
        print(f"Fehler: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
