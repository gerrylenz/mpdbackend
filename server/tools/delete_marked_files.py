#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Löscht Dateien aus der mpdbackend-Markierliste (GET /markfordelete).

Pro Eintrag: Pfad ermitteln → eine Ebene über dem Album-Ordner (Künstler-Ebene)
→ dort rekursiv nach dem Dateinamen suchen und alle Treffer löschen.

Konfiguration in delete_marked_files.env neben diesem Skript (Vorlage:
delete_marked_files.env.example). CLI-Argumente überschreiben die Datei.

Beispiel:
  python3 delete_marked_files.py --dry-run
  python3 delete_marked_files.py --config /etc/delete_marked_files.env
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR.parent))

from env_util import parse_env_file  # noqa: E402
from paths import resolve_under_root  # noqa: E402

SCRIPT_DIR = _SCRIPT_DIR
DEFAULT_CONFIG_NAME = "delete_marked_files.env"

_BUILTIN_DEFAULTS = {
    "URL": "http://127.0.0.1:4533",
    "CHANNEL": "0",
    "MUSIC_ROOT": "/home/musik/alben",
    "COVER_DIR": "",
    "PASSWORD": "",
    "MPD_UPDATE": "false",
    "KEEP_LIST_ON_ERROR": "false",
    "DRY_RUN": "false",
}

_MPDBACKEND_ALIASES = {
    "MPDBACKEND_PUBLIC_BASE_URL": "URL",
    "MPDBACKEND_MUSIC_ROOT": "MUSIC_ROOT",
    "MPDBACKEND_COVER_DIR": "COVER_DIR",
    "MPDBACKEND_WEB_PASSWORD": "PASSWORD",
}


def log(message: str) -> None:
    """Ausgabe auf der Konsole (sofort sichtbar)."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts} {message}", flush=True)


def parse_env_file_lines(path: Path) -> dict[str, str]:
    """Alias für env_util.parse_env_file."""
    return parse_env_file(path)


def normalize_config(raw: dict[str, str]) -> dict[str, str]:
    """Vereinheitlicht Schlüssel und übernimmt mpdbackend.env-Aliase."""
    config = {key.upper(): value for key, value in raw.items()}

    for source_key, target_key in _MPDBACKEND_ALIASES.items():
        if source_key.upper() in config and not config.get(target_key, "").strip():
            config[target_key] = config[source_key.upper()]

    if not config.get("URL", "").strip():
        port = config.get("MPDBACKEND_HTTP_PORT", "").strip()
        if port:
            config["URL"] = f"http://127.0.0.1:{port}"

    return config


def resolve_config_path(explicit: str | None) -> Path | None:
    """Ermittelt die zu ladende Konfigurationsdatei."""
    if explicit:
        return Path(explicit).expanduser()

    env_path = os.getenv("DELETE_MARKED_CONFIG", "").strip()
    if env_path:
        return Path(env_path).expanduser()

    local = SCRIPT_DIR / DEFAULT_CONFIG_NAME
    if local.is_file():
        return local

    parent_env = SCRIPT_DIR.parent / "mpdbackend.env"
    if parent_env.is_file():
        return parent_env

    return None


def load_config(explicit: str | None) -> tuple[dict[str, str], Path | None]:
    """Lädt Konfiguration; fehlende Datei → eingebaute Defaults."""
    path = resolve_config_path(explicit)
    if path is None:
        return dict(_BUILTIN_DEFAULTS), None
    if not path.is_file():
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {path}")

    merged = dict(_BUILTIN_DEFAULTS)
    merged.update(normalize_config(parse_env_file_lines(path)))
    return merged, path


def config_bool(raw: str) -> bool:
    """Wandelt Konfigurationswerte in bool um."""
    return raw.strip().lower() in ("1", "true", "yes", "on")


def normalize_rel_path(rel_path: str) -> str | None:
    """Normalisiert einen MPD-Relativpfad; None bei ungültigen Werten."""
    rel = rel_path.strip().replace("\\", "/")
    if not rel or rel.startswith("/"):
        return None
    if ".." in Path(rel).parts:
        return None
    return rel


def search_root_for_marked(music_root: Path, norm: str) -> Path | None:
    """Liefert die Suchwurzel eine Ebene über dem unmittelbaren Album-Ordner.

    Artist/Album1/Song.mp3 → Artist/
    Artist/Song.mp3        → Artist/
    Song.mp3               → Musik-Wurzel
    """
    parts = Path(norm).parts
    if len(parts) == 1:
        return music_root.resolve()
    if len(parts) == 2:
        return resolve_under_root(music_root, parts[0])
    parent_rel = Path(*parts[:-2]).as_posix()
    return resolve_under_root(music_root, parent_rel)


def find_targets_under_parent(
    music_root: Path, norm: str
) -> tuple[list[Path], Path | None, str | None]:
    """Sucht rekursiv ab der Künstler-Ebene alle Dateien mit gleichem Namen.

    Returns (targets, search_root, error).
    """
    filename = Path(norm).name
    search_root = search_root_for_marked(music_root, norm)
    if search_root is None:
        return [], None, f"ungültiger Pfad (außerhalb der Wurzel): {norm!r}"
    if not search_root.is_dir():
        return [], search_root, f"Verzeichnis nicht gefunden: {search_root}"

    targets = sorted(
        path.resolve()
        for path in search_root.rglob(filename)
        if path.is_file()
    )
    return targets, search_root, None


@dataclass(frozen=True)
class Settings:
    url: str
    channel: str
    music_root: str
    password: str
    cover_dir: str
    mpd_update: bool
    keep_list_on_error: bool
    dry_run: bool
    config_path: Path | None


def build_settings(args: argparse.Namespace) -> Settings:
    """Kombiniert Konfigurationsdatei und CLI (CLI hat Vorrang)."""
    config, config_path = load_config(args.config)

    def from_config(key: str) -> str:
        return config.get(key, _BUILTIN_DEFAULTS.get(key, "")).strip()

    url = args.url if args.url is not None else from_config("URL")
    channel = args.channel if args.channel is not None else from_config("CHANNEL")
    music_root = (
        args.music_root if args.music_root is not None else from_config("MUSIC_ROOT")
    )
    password = args.password if args.password is not None else from_config("PASSWORD")
    cover_dir = args.cover_dir if args.cover_dir is not None else from_config("COVER_DIR")

    mpd_update = args.mpd_update or config_bool(from_config("MPD_UPDATE"))
    keep_list_on_error = args.keep_list_on_error or config_bool(
        from_config("KEEP_LIST_ON_ERROR")
    )
    dry_run = args.dry_run or config_bool(from_config("DRY_RUN"))

    return Settings(
        url=url,
        channel=channel,
        music_root=music_root,
        password=password,
        cover_dir=cover_dir,
        mpd_update=mpd_update,
        keep_list_on_error=keep_list_on_error,
        dry_run=dry_run,
        config_path=config_path,
    )


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
) -> tuple[int, int, int, list[str]]:
    """Löscht markierte Dateien rekursiv ab dem jeweiligen Elternverzeichnis.

    Returns (deleted, skipped, missing, errors).
    missing zählt Markierungseinträge ohne Treffer; errors enthält blockierende Fehler.
    """
    deleted = 0
    skipped = 0
    missing = 0
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
        norm = normalize_rel_path(rel)
        if norm is None:
            msg = f"ungültiger Pfad (außerhalb der Wurzel): {rel!r}"
            log(f"{prefix} FEHLER: {msg}")
            errors.append(msg)
            continue

        targets, search_root, find_error = find_targets_under_parent(music_root, norm)
        if find_error:
            log(f"{prefix} FEHLER: {find_error}")
            if find_error.startswith("ungültiger Pfad"):
                errors.append(find_error)
            else:
                missing += 1
            continue

        assert search_root is not None
        log(f"{prefix} Suche ab: {search_root} (Dateiname: {Path(norm).name})")

        if not targets:
            msg = f"keine Datei gefunden unter {search_root}"
            log(f"{prefix} FEHLER: {msg}")
            missing += 1
            continue

        for target in targets:
            log(f"{prefix} Ziel: {target}")
            if cover_dir is not None:
                remove_cover_cache(cover_dir, target, dry_run=dry_run)

            if dry_run:
                log(f"{prefix} würde gelöscht werden: {target}")
                deleted += 1
                continue

            try:
                target.unlink()
                log(f"{prefix} gelöscht: {target}")
                deleted += 1
            except OSError as err:
                msg = f"{target}: {err}"
                log(f"{prefix} FEHLER: {err}")
                errors.append(msg)

    return deleted, skipped, missing, errors


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
        "--config",
        default=None,
        help=(
            f"path to config file (default: {DEFAULT_CONFIG_NAME} beside script, "
            "else ../mpdbackend.env; override with DELETE_MARKED_CONFIG)"
        ),
    )
    parser.add_argument(
        "--url",
        default=None,
        help="mpdbackend base URL (overrides config URL)",
    )
    parser.add_argument(
        "--music-root",
        default=None,
        help="music library root (overrides config MUSIC_ROOT)",
    )
    parser.add_argument(
        "--channel",
        default=None,
        help="channel id for ?channel= proxy (overrides config CHANNEL)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="web password for ?password= (overrides config PASSWORD)",
    )
    parser.add_argument(
        "--cover-dir",
        default=None,
        help="cover cache directory (overrides config COVER_DIR)",
    )
    parser.add_argument(
        "--mpd-update",
        action="store_true",
        help="run mpc update after successful deletes (or config MPD_UPDATE=true)",
    )
    parser.add_argument(
        "--keep-list-on-error",
        action="store_true",
        help="do not clear mark list when delete errors occurred",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only print what would be deleted (or config DRY_RUN=true)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        settings = build_settings(args)
    except FileNotFoundError as err:
        log(f"FEHLER: {err}")
        return 2

    log("=== delete_marked_files ===")
    if settings.config_path:
        log(f"Konfiguration: {settings.config_path}")
    else:
        log("Konfiguration: (keine Datei, eingebaute Defaults)")
    log(f"mpdbackend: {settings.url}")
    log(f"Kanal: {settings.channel or '(keiner)'}")
    log(f"Musik-Wurzel: {settings.music_root}")
    if settings.password:
        log("Passwort: (gesetzt)")
    if settings.mpd_update:
        log("MPD-Update: ja")
    if settings.dry_run:
        log("Modus: Dry-Run (es wird nichts gelöscht)")
    else:
        log("Modus: Löschen")

    music_root = Path(settings.music_root)
    log("Prüfe Musik-Wurzel …")
    if not music_root.is_dir():
        log(f"FEHLER: Verzeichnis nicht gefunden: {music_root}")
        return 2
    log(f"Musik-Wurzel OK: {music_root.resolve()}")

    cover_dir: Path | None = None
    if settings.cover_dir.strip():
        cover_dir = Path(settings.cover_dir)
        if not cover_dir.is_dir():
            log(f"FEHLER: Cover-Verzeichnis nicht gefunden: {cover_dir}")
            return 2
        log(f"Cover-Cache: {cover_dir.resolve()}")

    try:
        payload = fetch_marked_files(
            settings.url, settings.channel, password=settings.password
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
                settings.url,
                settings.channel,
                dry_run=settings.dry_run,
                password=settings.password,
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as err:
            log(f"FEHLER beim Leeren der Markierdatei: {err}")
            return 1
        log("Fertig")
        return 0

    log("--- Verarbeitung starten ---")
    deleted, skipped, missing, errors = delete_marked(
        music_root,
        rel_paths,
        dry_run=settings.dry_run,
        cover_dir=cover_dir,
    )

    log("--- Zusammenfassung ---")
    log(f"Verarbeitet: {len(rel_paths)}")
    log(f"Gelöscht bzw. dry-run: {deleted}")
    log(f"Übersprungen: {skipped}")
    log(f"Nicht gefunden: {missing}")
    log(f"Fehler: {len(errors)}")

    if errors:
        log("Programm beendet mit Fehlern")
        if settings.keep_list_on_error:
            log("Markierliste bleibt erhalten (--keep-list-on-error)")
        return 1

    if missing:
        log(
            f"Hinweis: {missing} Datei(en) waren nicht vorhanden; "
            "Markierliste wird trotzdem geleert"
        )

    if settings.mpd_update:
        log("--- MPD-Datenbank aktualisieren ---")
        run_mpd_update(dry_run=settings.dry_run)

    log("--- Markierdatei auf dem Server leeren ---")
    try:
        clear_marked_files_on_server(
            settings.url,
            settings.channel,
            dry_run=settings.dry_run,
            password=settings.password,
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as err:
        log(f"FEHLER beim Leeren der Markierdatei: {err}")
        return 1

    log("Fertig")
    return 0


if __name__ == "__main__":
    sys.exit(main())
