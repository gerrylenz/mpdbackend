#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Löscht Dateien aus der mpdbackend-Markierliste (GET /markfordelete).

Konfiguration nur in DEFAULT_* unten (optional per CLI überschreiben).

Beispiel:
  python3 delete_marked_files.py --dry-run
  python3 delete_marked_files.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# --- Konfiguration (hier anpassen) ---
DEFAULT_MPDBACKEND_URL = "http://edeka.ex-store.de:4533"
DEFAULT_MPDBACKEND_CHANNEL = "0"
DEFAULT_MPDBACKEND_MUSIC_ROOT = "/home/musik/alben"


def log(message: str) -> None:
    """Ausgabe auf der Konsole (sofort sichtbar)."""
    print(message, flush=True)


def fetch_marked_files(base_url: str, channel: str = "") -> dict:
    """Holt JSON von GET /markfordelete."""
    params = {}
    if channel.strip():
        params["channel"] = channel.strip()
    query = f"?{urlencode(params)}" if params else ""
    url = f"{base_url.rstrip('/')}/markfordelete{query}"
    log(f"Lade Markierliste von: {url}")
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        log(f"Antwort erhalten: HTTP {response.status}")
        raw = response.read()
    log(f"JSON wird ausgewertet ({len(raw)} Bytes) …")
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("invalid JSON payload from /markfordelete")
    files = data.get("files")
    if not isinstance(files, list):
        raise ValueError("missing 'files' array in /markfordelete response")
    log(f"Markierliste enthält {len(files)} Eintrag/Einträge")
    return data


def resolve_under_root(music_root: Path, rel_path: str) -> Path | None:
    """Baut absoluten Pfad; None wenn außerhalb der Wurzel oder ungültig."""
    rel = rel_path.strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        return None
    parts = Path(rel).parts
    if ".." in parts:
        return None

    root = music_root.resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def delete_marked(
    music_root: Path,
    rel_paths: list[str],
    *,
    dry_run: bool,
) -> tuple[int, int, list[str]]:
    """Löscht Dateien unter music_root. Returns (deleted, skipped, errors)."""
    deleted = 0
    skipped = 0
    errors: list[str] = []

    total = len(rel_paths)
    mode = "Dry-Run" if dry_run else "Löschen"

    for index, rel in enumerate(rel_paths, start=1):
        prefix = f"[{index}/{total}]"
        if not isinstance(rel, str) or not rel.strip():
            log(f"{prefix} übersprungen: leerer Eintrag")
            skipped += 1
            continue

        log(f"{prefix} {mode}: {rel}")
        target = resolve_under_root(music_root, rel)
        if target is None:
            msg = f"ungültiger Pfad (außerhalb der Wurzel): {rel!r}"
            log(f"{prefix} FEHLER: {msg}")
            errors.append(msg)
            continue

        log(f"{prefix} Ziel: {target}")
        if not target.is_file():
            msg = f"keine Datei: {target}"
            log(f"{prefix} FEHLER: {msg}")
            errors.append(msg)
            continue

        if dry_run:
            log(f"{prefix} würde gelöscht werden")
            deleted += 1
            continue

        try:
            target.unlink()
            log(f"{prefix} gelöscht")
            deleted += 1
        except OSError as err:
            msg = f"{rel}: {err}"
            log(f"{prefix} FEHLER: {err}")
            errors.append(msg)

    return deleted, skipped, errors


def clear_local_mark_file(cfg_path: str, dry_run: bool) -> None:
    """Leert die lokale mark_for_delete.cfg (optional, nur wenn Datei existiert)."""
    path = Path(cfg_path).resolve()
    log(f"Markierdatei leeren: {path}")
    if not path.is_file():
        log("Übersprungen: Datei existiert nicht")
        return
    if dry_run:
        log("[dry-run] würde Datei leeren")
        return
    path.write_text("", encoding="utf-8")
    log("Markierdatei geleert")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete files listed in mpdbackend /markfordelete JSON."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_MPDBACKEND_URL,
        help=f"mpdbackend base URL (default: {DEFAULT_MPDBACKEND_URL})",
    )
    parser.add_argument(
        "--music-root",
        default=DEFAULT_MPDBACKEND_MUSIC_ROOT,
        help=f"music library root (default: {DEFAULT_MPDBACKEND_MUSIC_ROOT})",
    )
    parser.add_argument(
        "--channel",
        default=DEFAULT_MPDBACKEND_CHANNEL,
        help=f"channel id for ?channel= proxy (default: {DEFAULT_MPDBACKEND_CHANNEL})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only print what would be deleted",
    )
    parser.add_argument(
        "--clear-cfg",
        metavar="PATH",
        default="",
        help="after success, truncate this local mark_for_delete.cfg",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log("=== delete_marked_files ===")
    log(f"mpdbackend: {args.url}")
    log(f"Kanal: {args.channel or '(keiner)'}")
    log(f"Musik-Wurzel: {args.music_root}")
    if args.dry_run:
        log("Modus: Dry-Run (es wird nichts gelöscht)")
    else:
        log("Modus: Löschen")

    music_root = Path(args.music_root)
    log(f"Prüfe Musik-Wurzel …")
    if not music_root.is_dir():
        log(f"FEHLER: Verzeichnis nicht gefunden: {music_root}")
        return 2
    log(f"Musik-Wurzel OK: {music_root.resolve()}")

    try:
        payload = fetch_marked_files(args.url, args.channel)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as err:
        log(f"FEHLER beim Abruf: {err}")
        return 1

    rel_paths = [str(item) for item in payload.get("files", [])]
    log(f"Server-Datei (Referenz): {payload.get('path', '')}")

    if not rel_paths:
        log("Keine Einträge – nichts zu tun")
        return 0

    log("--- Verarbeitung starten ---")
    deleted, skipped, errors = delete_marked(
        music_root, rel_paths, dry_run=args.dry_run
    )

    log("--- Zusammenfassung ---")
    log(f"Verarbeitet: {len(rel_paths)}")
    log(f"Gelöscht bzw. dry-run: {deleted}")
    log(f"Übersprungen: {skipped}")
    log(f"Fehler: {len(errors)}")

    if errors:
        log("Programm beendet mit Fehlern")
        return 1

    if args.clear_cfg:
        log("--- Markierdatei leeren ---")
        clear_local_mark_file(args.clear_cfg, dry_run=args.dry_run)

    log("Fertig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
