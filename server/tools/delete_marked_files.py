#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Löscht Dateien aus der mpdbackend-Markierliste (GET /markfordelete).

Konfiguration nur in DEFAULT_* unten (optional per CLI überschreiben).

Beispiel:
  python3 delete_marked_files.py --dry-run
  python3 delete_marked_files.py --mpd-update --cover-dir /path/to/data/covers
  python3 delete_marked_files.py --password geheim
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# --- Konfiguration (hier anpassen) ---
DEFAULT_MPDBACKEND_URL = "http://127.0.0.1:4533"
DEFAULT_MPDBACKEND_CHANNEL = "0"
DEFAULT_MPDBACKEND_MUSIC_ROOT = "/home/musik/alben"
DEFAULT_COVER_DIR = ""


def log(message: str) -> None:
    """Ausgabe auf der Konsole (sofort sichtbar)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {message}", flush=True)


def build_request_url(
    base_url: str,
    path: str,
    *,
    channel: str = "",
    password: str = "",
) -> str:
    """Baut URL inkl. optionaler ?channel= und ?password=."""
    params: dict[str, str] = {}
    if channel.strip():
        params["channel"] = channel.strip()
    if password.strip():
        params["password"] = password.strip()
    query = f"?{urlencode(params)}" if params else ""
    return f"{base_url.rstrip('/')}{path}{query}"


def fetch_marked_files(
    base_url: str, channel: str = "", *, password: str = ""
) -> dict:
    """Holt JSON von GET /markfordelete."""
    url = build_request_url(
        base_url, "/markfordelete", channel=channel, password=password
    )
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


def cover_cache_filename(audio_file: Path) -> str:
    """Cover-Cache-Dateiname (gleiche Logik wie mpdbackend_cover.cover_cache_filename)."""
    stat = audio_file.stat()
    key = f"{audio_file}:{stat.st_size}:{stat.st_mtime_ns}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:24]
    return f"cover_{digest}.jpg"


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


def remove_cover_cache(cover_dir: Path, audio_file: Path, *, dry_run: bool) -> None:
    """Entfernt gecachtes Cover für eine Audiodatei, falls vorhanden."""
    try:
        cache_name = cover_cache_filename(audio_file)
    except OSError as err:
        log(f"Cover-Cache übersprungen (stat): {err}")
        return

    cache_path = cover_dir / cache_name
    if not cache_path.is_file():
        return

    if dry_run:
        log(f"würde Cover-Cache löschen: {cache_path}")
        return

    try:
        cache_path.unlink()
        log(f"Cover-Cache gelöscht: {cache_name}")
    except OSError as err:
        log(f"FEHLER beim Löschen des Cover-Cache: {err}")


def delete_marked(
    music_root: Path,
    rel_paths: list[str],
    *,
    dry_run: bool,
    cover_dir: Path | None,
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

        if cover_dir is not None:
            remove_cover_cache(cover_dir, target, dry_run=dry_run)

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


def run_mpd_update(*, dry_run: bool) -> None:
    """Aktualisiert die MPD-Datenbank via mpc update."""
    if dry_run:
        log("[dry-run] würde MPD-Datenbank aktualisieren (mpc update)")
        return

    log("Starte MPD-Datenbank-Update (mpc update) …")
    try:
        result = subprocess.run(
            ["mpc", "update"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        log("FEHLER: mpc nicht gefunden (Paket mpc oder MPD-Client installieren)")
        return

    if result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        log(f"FEHLER: mpc update (Exit {result.returncode}): {stderr or 'unbekannt'}")
        return

    output = (result.stdout or "").strip()
    if output:
        log(f"mpc update: {output}")
    else:
        log("mpc update abgeschlossen")


def clear_marked_files_on_server(
    base_url: str,
    channel: str = "",
    *,
    dry_run: bool,
    password: str = "",
) -> None:
    """Leert mark_for_delete.cfg auf dem mpdbackend-Server."""
    if dry_run:
        log("[dry-run] würde Markierliste auf dem Server leeren (POST /markfordelete/clear)")
        return

    url = build_request_url(
        base_url, "/markfordelete/clear", channel=channel, password=password
    )
    log(f"Leere Markierliste auf dem Server: {url}")
    request = Request(
        url,
        data=b"",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        log(f"Server-Antwort: HTTP {response.status}")
        raw = response.read()
    data = json.loads(raw.decode("utf-8"))
    if not data.get("ok"):
        raise ValueError(data.get("error") or "clear failed")
    log(f"Markierdatei geleert: {data.get('path', '')}")


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
        "--password",
        default="",
        help="web password for ?password= (required when MPDBACKEND_WEB_PASSWORD is set)",
    )
    parser.add_argument(
        "--cover-dir",
        default=DEFAULT_COVER_DIR,
        help="cover cache directory to clean up (optional)",
    )
    parser.add_argument(
        "--mpd-update",
        action="store_true",
        help="run mpc update after successful deletes",
    )
    parser.add_argument(
        "--keep-list-on-error",
        action="store_true",
        help="do not clear mark list on the server when delete errors occurred",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only print what would be deleted",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    log("=== delete_marked_files ===")
    log(f"mpdbackend: {args.url}")
    log(f"Kanal: {args.channel or '(keiner)'}")
    log(f"Musik-Wurzel: {args.music_root}")
    if args.password:
        log("Passwort: (gesetzt)")
    if args.dry_run:
        log("Modus: Dry-Run (es wird nichts gelöscht)")
    else:
        log("Modus: Löschen")

    music_root = Path(args.music_root)
    log("Prüfe Musik-Wurzel …")
    if not music_root.is_dir():
        log(f"FEHLER: Verzeichnis nicht gefunden: {music_root}")
        return 2
    log(f"Musik-Wurzel OK: {music_root.resolve()}")

    cover_dir: Path | None = None
    if args.cover_dir.strip():
        cover_dir = Path(args.cover_dir)
        if not cover_dir.is_dir():
            log(f"FEHLER: Cover-Verzeichnis nicht gefunden: {cover_dir}")
            return 2
        log(f"Cover-Cache: {cover_dir.resolve()}")

    try:
        payload = fetch_marked_files(
            args.url, args.channel, password=args.password
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as err:
        log(f"FEHLER beim Abruf: {err}")
        return 1

    rel_paths = [str(item) for item in payload.get("files", [])]
    log(f"Server-Datei (Referenz): {payload.get('path', '')}")

    if not rel_paths:
        log("Keine Einträge zum Löschen")
        log("--- Markierdatei auf dem Server leeren ---")
        try:
            clear_marked_files_on_server(
                args.url,
                args.channel,
                dry_run=args.dry_run,
                password=args.password,
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as err:
            log(f"FEHLER beim Leeren der Markierdatei: {err}")
            return 1
        log("Fertig")
        return 0

    log("--- Verarbeitung starten ---")
    deleted, skipped, errors = delete_marked(
        music_root,
        rel_paths,
        dry_run=args.dry_run,
        cover_dir=cover_dir,
    )

    log("--- Zusammenfassung ---")
    log(f"Verarbeitet: {len(rel_paths)}")
    log(f"Gelöscht bzw. dry-run: {deleted}")
    log(f"Übersprungen: {skipped}")
    log(f"Fehler: {len(errors)}")

    if errors:
        log("Programm beendet mit Fehlern")
        if args.keep_list_on_error:
            log("Markierliste bleibt erhalten (--keep-list-on-error)")
        return 1

    if args.mpd_update:
        log("--- MPD-Datenbank aktualisieren ---")
        run_mpd_update(dry_run=args.dry_run)

    log("--- Markierdatei auf dem Server leeren ---")
    try:
        clear_marked_files_on_server(
            args.url,
            args.channel,
            dry_run=args.dry_run,
            password=args.password,
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as err:
        log(f"FEHLER beim Leeren der Markierdatei: {err}")
        return 1

    log("Fertig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
