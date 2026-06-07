#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import signal
import sys
import time
import logging
import threading
import os
import hashlib
from urllib.parse import quote

from mpd import MPDClient

from env_util import env_bool, load_env_file as _load_env_file
from marked_file import append_marked_line, clear_marked_file, read_marked_lines
from paths import build_full_path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_ENV_FILE = os.path.join(BASE_DIR, "mpdbackend.env")


def load_env_file() -> None:
    """Lädt mpdbackend.env (siehe env_util.load_env_file)."""
    _load_env_file(DEFAULT_ENV_FILE)


def get_mqtt_enabled() -> bool:
    return env_bool("MPDBACKEND_MQTT_ENABLED", False)


def get_music_root() -> str:
    return os.getenv("MPDBACKEND_MUSIC_ROOT", "/home/musik")


def get_marked_for_delete() -> str:
    return os.getenv(
        "MPDBACKEND_MARKED_FOR_DELETE",
        os.path.join(DEFAULT_DATA_DIR, "mark_for_delete.cfg"),
    )


def get_station_logo_dir() -> str:
    return os.getenv(
        "MPDBACKEND_STATION_LOGO_DIR", os.path.join(DEFAULT_DATA_DIR, "logos")
    )


def get_mpd_socket() -> str:
    return os.getenv("MPDBACKEND_MPD_SOCKET", "/run/mpd/socket")


def get_playlist_dir() -> str:
    return os.getenv("MPDBACKEND_PLAYLIST_DIR", "").strip()


def get_public_base_url() -> str:
    return os.getenv("MPDBACKEND_PUBLIC_BASE_URL", "")


load_env_file()

from mpdbackend_cover import COVER_NAME_RE, CoverService  # noqa: E402


logger = logging.getLogger("mpdbackend")

CHANNEL_ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,32}$")
STATION_LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

DEFAULT_CHANNELS_FILE = os.path.join(BASE_DIR, "channels.json")

class ChannelRegistry:
    """Thread-sichere Senderliste aus channels.json."""

    def __init__(self) -> None:
        """Initialisiert Registry und lädt channels.json."""
        self._lock = threading.Lock()
        self._channels_file = os.getenv("MPDBACKEND_CHANNELS_FILE", DEFAULT_CHANNELS_FILE).strip()
        self._mtime: float | None = None
        self._channels = self._read_channels() or {}

    def _read_channels(self) -> dict | None:
        """Liest die Sender aus der konfigurierten JSON-Datei."""
        if not self._channels_file or not os.path.isfile(self._channels_file):
            logger.warning(
                "Channels file not found: %s (copy example/channels.json.example to channels.json)",
                self._channels_file or DEFAULT_CHANNELS_FILE,
            )
            self._mtime = None
            return {}
        try:
            with open(self._channels_file, encoding="utf-8") as handle:
                data = json.load(handle)
            if not isinstance(data, dict):
                logger.warning(
                    "Channels file is not a JSON object: %s", self._channels_file
                )
                return None
            self._mtime = os.path.getmtime(self._channels_file)
            logger.info(
                "Loaded %s radio channel(s) from %s", len(data), self._channels_file
            )
            return data
        except Exception as err:
            logger.warning("Failed to load channels from %s: %s", self._channels_file, err)
        return None

    def _maybe_reload(self) -> None:
        """Lädt channels.json neu, wenn die Datei geändert wurde."""
        if not self._channels_file or not os.path.isfile(self._channels_file):
            return
        mtime = os.path.getmtime(self._channels_file)
        if self._mtime is not None and mtime <= self._mtime:
            return
        channels = self._read_channels()
        if channels is None:
            return
        self._channels = channels
        logger.info(
            "Reloaded %s radio channel(s) from %s", len(channels), self._channels_file
        )

    def get(self) -> dict:
        """Gibt eine Kopie der aktuellen Senderliste zurück."""
        with self._lock:
            self._maybe_reload()
            return dict(self._channels)


CHANNEL_REGISTRY = ChannelRegistry()


# =========================
# HELPERS
# =========================

def save_current_track_file(song: dict, output_path: str | None = None) -> str:
    """Hängt den MPD-Dateipfad des aktuellen Titels an eine Textdatei an."""
    track_file = (song.get("file") or "").strip()
    if not track_file:
        raise ValueError("no current track file")

    target = (output_path or get_marked_for_delete()).strip()
    if not target:
        raise ValueError("output path not configured")

    append_marked_line(target, track_file)
    return track_file


def load_marked_for_delete_entries(output_path: str | None = None) -> list[str]:
    """Liest mark_for_delete.cfg: eine MPD-Dateipfad-Zeile pro Eintrag."""
    target = (output_path or get_marked_for_delete()).strip()
    if not target:
        return []
    return read_marked_lines(target)


def clear_marked_for_delete_file(output_path: str | None = None) -> str:
    """Leert mark_for_delete.cfg; liefert den absoluten Pfad."""
    target = (output_path or get_marked_for_delete()).strip()
    if not target:
        raise ValueError("output path not configured")
    return clear_marked_file(target)


def channel_logo_basename(channel_id: str) -> str:
    """Liefert den Dateinamen-Basis für ein Senderlogo."""
    return f"channel_{channel_id}"


def resolve_station_logo_path(channel_id: str) -> tuple[str, str] | None:
    """Sucht Senderlogo-Datei und liefert (Pfad, Content-Type)."""
    if not channel_id or not CHANNEL_ID_RE.match(channel_id):
        return None

    os.makedirs(get_station_logo_dir(), exist_ok=True)
    basename = channel_logo_basename(channel_id)
    logo_dir = get_station_logo_dir()

    exact = os.path.join(logo_dir, basename)
    if os.path.isfile(exact):
        return exact, logo_content_type(exact)

    for ext in STATION_LOGO_EXTENSIONS:
        candidate = os.path.join(logo_dir, f"{basename}{ext}")
        if os.path.isfile(candidate):
            return candidate, logo_content_type(candidate)

    return None


def station_logo_mtime(channel_id: str) -> int | None:
    """Liefert Logo-mtime für Cache-Busting in der Channel-API."""
    resolved = resolve_station_logo_path(channel_id)
    if not resolved:
        return None
    return int(os.path.getmtime(resolved[0]))


def enrich_channels_payload(channels: dict) -> dict:
    """Ergänzt Sender-Daten um logo_mtime pro Kanal."""
    enriched: dict = {}
    for channel_id, channel_data in channels.items():
        if not isinstance(channel_data, dict):
            enriched[channel_id] = channel_data
            continue
        entry = dict(channel_data)
        if mtime := station_logo_mtime(str(channel_id)):
            entry["logo_mtime"] = mtime
        enriched[channel_id] = entry
    return enriched


def logo_content_type(path: str) -> str:
    """Ermittelt den HTTP Content-Type für eine Logo-Datei."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


# =========================
# MPD STATUS HELPERS
# =========================

def parse_status_volume(status: dict) -> int | None:
    """Liest Lautstärke 0–100 aus MPD-Status; None wenn nicht vorhanden."""
    if "volume" not in status:
        return None
    raw = str(status["volume"]).split(":")[0].strip()
    try:
        return max(0, min(100, int(float(raw))))
    except (TypeError, ValueError):
        return None


def parse_status_lastloadedplaylist(status: dict) -> str:
    """Liest lastloadedplaylist aus MPD-Status (leer wenn nicht gesetzt)."""
    raw = status.get("lastloadedplaylist")
    if not raw:
        return ""
    return str(raw).strip()


def parse_status_elapsed(status: dict) -> float | None:
    """Liest elapsed aus MPD-Status; None wenn das Feld fehlt."""
    if "elapsed" not in status:
        return None
    try:
        return max(0.0, float(status["elapsed"]))
    except (TypeError, ValueError):
        return None


def format_playback_time(seconds: float) -> str:
    """Formatiert Sekunden als M:SS oder H:MM:SS (z. B. 0:00, 3:45, 1:05:30)."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def public_cover_url(cover_name: str) -> str | None:
    """Öffentliche Cover-URL für Media Session / CarPlay (braucht MPDBACKEND_PUBLIC_BASE_URL)."""
    name = (cover_name or "").strip()
    if not name or not COVER_NAME_RE.match(name):
        return None
    path = f"/cover?name={quote(name)}"
    base = (get_public_base_url() or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}{path}"


def display_playlist_filename(name: str) -> str:
    """Liefert Playlist-Dateinamen mit .m3u für HA/MQTT (leer wenn kein Name)."""
    name = (name or "").strip()
    if not name:
        return ""
    if name.endswith(".m3u"):
        return name
    return f"{name}.m3u"


def find_playlist_in_available(name: str, available: list[str]) -> str:
    """Findet den exakten Eintrag aus available (Groß/Kleinschreibung, mit/ohne .m3u)."""
    if not name or not available:
        return ""
    if name in available:
        return name
    target = _normalize_playlist_label(name).lower()
    if not target:
        return ""
    for item in available:
        if _normalize_playlist_label(item).lower() == target:
            return item
    return ""


def _normalize_playlist_label(name: str) -> str:
    name = (name or "").strip()
    if name.endswith(".m3u"):
        return name[:-4]
    return name


def _current_file_in_playlist_m3u(playlist_path: str, current_file: str) -> bool:
    current_norm = current_file.replace("\\", "/")
    current_base = os.path.basename(current_norm)
    try:
        with open(playlist_path, encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                entry = line.replace("\\", "/")
                if entry.startswith("file://"):
                    entry = entry[7:]
                entry_norm = os.path.normpath(entry).replace("\\", "/")
                if entry_norm == current_norm:
                    return True
                if current_norm.endswith("/" + entry_norm) or entry_norm.endswith(
                    "/" + current_norm
                ):
                    return True
                if current_base and os.path.basename(entry_norm) == current_base:
                    return True
    except OSError:
        return False
    return False


def infer_active_playlist_name(mpd, current_file: str, available: list[str]) -> str:
    """Ermittelt die gespeicherte Playlist anhand des aktuellen MPD-Tracks."""
    current_file = (current_file or "").strip()
    if not current_file or not available:
        return ""

    playlist_dir = mpd._playlist_directory()
    if playlist_dir:
        for name in available:
            path = os.path.join(playlist_dir, name)
            if os.path.isfile(path) and _current_file_in_playlist_m3u(path, current_file):
                return name

    for name in available:
        mpd_name = mpd.normalize_playlist_name(name)
        entries = mpd.safe("listplaylist", mpd_name, default=[]) or []
        for entry in entries:
            entry_file = (entry.get("file") or "").strip()
            if entry_file == current_file:
                return name
            if entry_file.replace("\\", "/") == current_file.replace("\\", "/"):
                return name
    return ""


def resolve_active_playlist_name(
    status: dict,
    loaded_playlist: str,
    available: list[str],
    *,
    mpd=None,
    current_file: str = "",
) -> str:
    """Aktive Playlist: MQTT/HTTP, MPD lastloadedplaylist, sonst Track in .m3u finden."""
    if loaded_playlist:
        display = display_playlist_filename(loaded_playlist)
        match = find_playlist_in_available(display, available)
        if match:
            return match
        if display:
            return display

    mpd_raw = parse_status_lastloadedplaylist(status)
    if mpd_raw:
        display = display_playlist_filename(mpd_raw)
        match = find_playlist_in_available(display, available)
        if match:
            return match
        if display:
            return display

    if mpd and current_file:
        inferred = infer_active_playlist_name(mpd, current_file, available)
        if inferred:
            return inferred

    return ""


def build_mpd_status_data(status: dict) -> dict:
    """Baut Status-Daten aus MPD status() für MQTT mpdbackend/status."""
    payload: dict = {
        "lastloadedplaylist": display_playlist_filename(
            parse_status_lastloadedplaylist(status)
        ),
    }
    volume = parse_status_volume(status)
    if volume is not None:
        payload["volume"] = volume
    return payload


# =========================
# MPD
# =========================

class MPD:
    """MPD-Zugriff über Unix-Socket (idle- und Befehls-Verbindung getrennt)."""

    def __init__(self):
        """Initialisiert Client-Zustand und Playlist-Cache."""
        self.client = None
        self.command_client = None
        self.lock = threading.Lock()
        self._playlists_cache: list[str] | None = None
        self._playlists_dir_mtime: float | None = None

    def _new_client(self) -> MPDClient:
        client = MPDClient()
        client.timeout = 5
        client.connect(get_mpd_socket())
        return client

    def connect(self) -> bool:
        """Stellt Idle- und Befehls-Verbindung zum MPD-Socket her."""
        try:
            self.client = self._new_client()
            self.command_client = self._new_client()
            return True
        except Exception as err:
            logger.warning("MPD connect failed (%s): %s", get_mpd_socket(), err)
            self.client = None
            self.command_client = None
            return False

    def connect_idle(self) -> bool:
        """Verbindet nur die Idle-Verbindung (Worker-Events)."""
        try:
            self.client = self._new_client()
            return True
        except Exception as err:
            logger.warning("MPD idle connect failed (%s): %s", get_mpd_socket(), err)
            self.client = None
            return False

    def connect_command(self) -> bool:
        """Verbindet nur die Befehls-Verbindung (MQTT, elapsed-resync)."""
        try:
            self.command_client = self._new_client()
            return True
        except Exception as err:
            logger.warning(
                "MPD command connect failed (%s): %s", get_mpd_socket(), err
            )
            self.command_client = None
            return False

    def safe(self, cmd, *args, default=None):
        """Führt einen MPD-Befehl aus und liefert bei Fehler den Default."""
        if default is None:
            default = {}
        with self.lock:
            try:
                if not self.client and not self.connect():
                    return default
                return getattr(self.client, cmd)(*args)
            except Exception:
                self.client = None
                return default

    def idle(self):
        """Wartet auf MPD-Events; Lock nur für Connect, nicht während blockierendem idle."""
        with self.lock:
            try:
                if not self.client and not self.connect_idle():
                    return None
                client = self.client
            except Exception:
                self.client = None
                return None
        try:
            return client.idle()
        except Exception:
            self.client = None
            return None

    def run_command(self, action):
        """Führt eine MPD-Aktion auf der separaten Befehls-Verbindung aus."""
        with self.lock:
            try:
                if not self.command_client and not self.connect_command():
                    return False
                action(self.command_client)
                return True
            except Exception as err:
                logger.warning("MPD command failed: %s", err)
                self.command_client = None
                return False

    @staticmethod
    def normalize_playlist_name(name: str) -> str:
        """Normalisiert Playlist-Namen für MPD load (mit oder ohne .m3u)."""
        name = (name or "").strip()
        if name.endswith(".m3u"):
            return name[:-4]
        return name

    def load_and_play_playlist(self, playlist_name: str) -> bool:
        """Lädt eine gespeicherte Playlist in die Queue und startet die Wiedergabe."""
        mpd_name = self.normalize_playlist_name(playlist_name)
        if not mpd_name:
            return False

        def _load_play(client):
            # Atomar: kürzeste Unterbrechung für HTTP/Icecast-Stream (Music Assistant)
            client.command_list_ok_begin()
            client.clear()
            client.load(mpd_name)
            client.play()
            client.command_list_end()

        ok = self.run_command(_load_play)
        if ok:
            self.invalidate_playlists_cache()
            logger.info("Loaded and playing playlist: %s", playlist_name)
        return ok

    def play(self) -> bool:
        """Startet oder setzt die Wiedergabe fort."""
        return self.run_command(lambda client: client.play())

    def stop(self) -> bool:
        """Stoppt die Wiedergabe."""
        return self.run_command(lambda client: client.stop())

    def next_track(self) -> bool:
        """Springt zum nächsten Titel."""
        return self.run_command(lambda client: client.next())

    def previous_track(self) -> bool:
        """Springt zum vorherigen Titel."""
        return self.run_command(lambda client: client.previous())

    def set_volume(self, volume: int) -> bool:
        """Setzt die MPD-Lautstärke (0–100)."""
        level = max(0, min(100, int(volume)))
        return self.run_command(lambda client: client.setvol(level))

    def execute_player_action(self, action: str) -> bool:
        """Führt play|stop|next|back aus."""
        actions = {
            "play": self.play,
            "stop": self.stop,
            "next": self.next_track,
            "back": self.previous_track,
        }
        handler = actions.get(action)
        if handler is None:
            return False
        return handler()

    def _playlist_directory(self) -> str:
        """Ermittelt das MPD-Playlist-Verzeichnis (Env oder MPD config)."""
        playlist_dir = get_playlist_dir()
        if playlist_dir and os.path.isdir(playlist_dir):
            return playlist_dir

        config = self.safe("config", default={})
        if isinstance(config, dict):
            path = (config.get("playlist_directory") or "").strip()
            if path and os.path.isdir(path):
                return path
        return ""

    def _playlists_from_mpd(self) -> list[str]:
        """Fallback: gespeicherte MPD-Playlists mit .m3u-Endung."""
        entries = self.safe("listplaylists", default=[])
        if not isinstance(entries, list):
            return []

        names: list[str] = []
        for entry in entries:
            name = (entry.get("playlist") or "").strip()
            if not name:
                continue
            names.append(name if name.endswith(".m3u") else f"{name}.m3u")
        return sorted(names)

    def available_playlists(self) -> list[str]:
        """Listet alle .m3u-Dateien im Playlist-Verzeichnis auf."""
        playlist_dir = self._playlist_directory()
        if not playlist_dir:
            return self._playlists_from_mpd()

        try:
            dir_mtime = os.path.getmtime(playlist_dir)
        except OSError:
            return []

        if (
            self._playlists_cache is not None
            and self._playlists_dir_mtime == dir_mtime
        ):
            return self._playlists_cache

        playlists: list[str] = []
        try:
            for name in sorted(os.listdir(playlist_dir)):
                if not name.endswith(".m3u"):
                    continue
                path = os.path.join(playlist_dir, name)
                if os.path.isfile(path):
                    playlists.append(name)
        except OSError:
            playlists = []

        self._playlists_dir_mtime = dir_mtime
        self._playlists_cache = playlists
        return playlists

    def invalidate_playlists_cache(self) -> None:
        """Leert den Playlist-Cache (z. B. nach MPD playlist-Event)."""
        self._playlists_cache = None
        self._playlists_dir_mtime = None


# =========================
# WORKER
# =========================

class Worker(threading.Thread):
    """Hintergrund-Thread: MPD idle → Status/Cover/MQTT aktualisieren."""

    def __init__(self, mpd):
        """Initialisiert Worker mit MPD-Client und Cover-Service."""
        super().__init__(daemon=True)
        self.mpd = mpd
        self.mqtt = None
        self.mqtt_publisher = None

        self.last_song = {}
        self.last_status = {}

        self.cover = CoverService()
        self.stop_flag = False

        self.last_signature = None

        self.lock = threading.Lock()

        self.current_hash = ""
        self.elapsed_sync_base = 0.0
        self.elapsed_sync_at = 0.0

    def sync_elapsed_clock(self, status: dict) -> None:
        """Speichert MPD-elapsed als Referenz für die Interpolation."""
        parsed = parse_status_elapsed(status)
        if parsed is None:
            return
        self.elapsed_sync_base = parsed
        self.elapsed_sync_at = time.monotonic()

    def try_resync_elapsed(self) -> None:
        """MPD-elapsed über die Befehls-Verbindung nachziehen."""
        with self.mpd.lock:
            try:
                if not self.mpd.command_client and not self.mpd.connect_command():
                    return
                status = self.mpd.command_client.status() or {}
            except Exception:
                self.mpd.command_client = None
                return
        parsed = parse_status_elapsed(status)
        if parsed is None:
            return
        with self.lock:
            self.elapsed_sync_base = parsed
            self.elapsed_sync_at = time.monotonic()

    def build_elapsed_status(self) -> dict:
        """Berechnet elapsed aus Sync-Punkt + Interpolation."""
        with self.lock:
            state = self.last_status.get("state")
            songid = self.last_status.get("songid")
            if state == "play":
                elapsed = self.elapsed_sync_base + (
                    time.monotonic() - self.elapsed_sync_at
                )
            else:
                elapsed = self.elapsed_sync_base

        return {"state": state, "songid": songid, "elapsed": max(0.0, elapsed)}

    def build_track_state_data(self, song: dict, status: dict) -> dict:
        """Track-Metadaten aus MPD für MQTT state-Topic."""
        duration = self.resolve_duration(song, status)
        duration_sec = int(duration) if duration else 0
        payload = {
            "state": status.get("state"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "album": song.get("album"),
            "duration": format_playback_time(duration_sec),
            "cover_name": self.cover.cover_name(),
            "lastloadedplaylist": display_playlist_filename(
                parse_status_lastloadedplaylist(status)
            ),
        }
        volume = parse_status_volume(status)
        if volume is not None:
            payload["volume"] = volume
        return payload

    def build_playlists_data(self) -> dict:
        """Verfügbare MPD-Playlists für MQTT playlists-Topic."""
        return {"playlists": self.mpd.available_playlists()}

    def build_queue_state_data(
        self, song: dict, status: dict, loaded_playlist: str
    ) -> dict:
        """Queue-Kontext aus MPD für MQTT current-Topic."""
        song_pos = status.get("song")
        available = self.mpd.available_playlists()
        payload = {
            "playlist": resolve_active_playlist_name(
                status,
                loaded_playlist,
                available,
                mpd=self.mpd,
                current_file=song.get("file") or "",
            ),
            "pos": int(song_pos) if song_pos is not None else None,
            "file": song.get("file") or "",
        }
        volume = parse_status_volume(status)
        if volume is not None:
            payload["volume"] = volume
        return payload

    def resolve_duration(self, song, status):
        """Liefert Track-Dauer aus Song- oder Status-Metadaten."""
        return float(song.get("time") or status.get("duration") or 0)

    def snapshot(self):
        """Liest aktuellen Song/Status und speichert sie im Worker."""
        song = self.mpd.safe("currentsong")
        status = self.mpd.safe("status")

        with self.lock:
            self.last_song = song
            self.last_status = status
            self.sync_elapsed_clock(status)

        return song, status

    def handle_track(self, song, status):
        """Bei Trackwechsel Cover neu generieren; True wenn gewechselt."""
        sig = (song.get("file"), status.get("songid"))

        with self.lock:
            if sig == self.last_signature:
                return False
            self.last_signature = sig

        file = song.get("file")
        if file:
            full_path = build_full_path(file, get_music_root())
            if not full_path:
                logger.warning("Invalid track path from MPD: %s", file)
                self.cover.current = "blank.jpg"
            else:
                try:
                    self.cover.generate(full_path)
                except Exception as err:
                    logger.warning("Cover generation failed for %s: %s", file, err)
                    self.cover.current = "blank.jpg"

        return True

    def publish(self, song, status):
        """Aktualisiert Hash und publiziert Status per MQTT (falls aktiv)."""
        self.handle_track(song, status)

        payload = {
            "state": status.get("state"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "album": song.get("album"),
            "elapsed": float(status.get("elapsed") or 0),
            "duration": self.resolve_duration(song, status),
            "cover_name": self.cover.cover_name(),
        }

        new_hash = hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        with self.lock:
            self.current_hash = new_hash

        if self.mqtt_publisher:
            self.mqtt_publisher.publish(song, status, payload, get_music_root())

    def update_state(self):
        """Liest MPD-Status und aktualisiert last_song/last_status."""
        song = self.mpd.safe("currentsong") or {}
        status = self.mpd.safe("status") or {}

        with self.lock:
            self.last_song = song
            self.last_status = status
            self.sync_elapsed_clock(status)

        return song, status

    def run(self):
        """Hauptschleife: MPD idle abwarten und bei Änderung publizieren."""
        if not self.mpd.connect():
            time.sleep(2)

        song = self.mpd.safe("currentsong") or {}
        status = self.mpd.safe("status") or {}

        self.handle_track(song, status)
        self.update_state()
        self.publish(song, status)

        while not self.stop_flag:
            try:
                if not self.mpd.client and not self.mpd.connect_idle():
                    time.sleep(2)
                    continue
                events = self.mpd.idle()
                if events and "playlist" in events:
                    self.mpd.invalidate_playlists_cache()

                song, status = self.update_state()
                self.publish(song, status)

            except Exception:
                self.mpd.client = None
                time.sleep(1)


# =========================
# MAIN
# =========================

_shutdown_done = False


def _shutdown(signum, _frame, *, worker: Worker, http_api, publisher) -> None:
    """Graceful shutdown für systemd SIGTERM/SIGINT."""
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True

    logger.info("Shutting down (signal %s)", signum)
    worker.stop_flag = True

    if publisher is not None:
        publisher.stop()

    if http_api is not None:
        http_api.stop()

    sys.exit(0)


def main():
    """Startet MPD-Worker, optional MQTT und HTTP-API."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("mpd.base").setLevel(logging.WARNING)

    env_path = os.getenv("MPDBACKEND_ENV_FILE", DEFAULT_ENV_FILE)
    if os.path.isfile("/etc/mpdbackend.env") and env_path == DEFAULT_ENV_FILE:
        logger.info(
            "Config: systemd EnvironmentFile=/etc/mpdbackend.env "
            "(values there override %s)",
            DEFAULT_ENV_FILE,
        )

    mpd = MPD()
    worker = Worker(mpd)
    publisher = None

    if get_mqtt_enabled():
        from mpdbackend_mqtt import MqttPublisher

        publisher = MqttPublisher(worker)
        publisher.start(env_path)
        worker.mqtt_publisher = publisher
    else:
        logger.info("MQTT disabled (MPDBACKEND_MQTT_ENABLED=false)")

    worker.start()

    from mpdbackend_http import HTTPAPI

    http_api = HTTPAPI(worker, CHANNEL_REGISTRY, mqtt_enabled=get_mqtt_enabled())
    http_api.start()

    signal.signal(
        signal.SIGTERM,
        lambda signum, frame: _shutdown(
            signum, frame, worker=worker, http_api=http_api, publisher=publisher
        ),
    )
    signal.signal(
        signal.SIGINT,
        lambda signum, frame: _shutdown(
            signum, frame, worker=worker, http_api=http_api, publisher=publisher
        ),
    )

    while not worker.stop_flag:
        time.sleep(1)


if __name__ == "__main__":
    main()
