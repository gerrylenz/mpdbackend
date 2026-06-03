#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import time
import logging
import threading
import os
import hashlib
from urllib.parse import quote

from mpd import MPDClient

from mpdbackend_cover import COVER_NAME_RE, CoverService


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_ENV_FILE = os.path.join(BASE_DIR, "mpdbackend.env")


def load_env_file() -> None:
    """Lädt mpdbackend.env in os.environ (bestehende Variablen bleiben unverändert)."""
    env_path = os.getenv("MPDBACKEND_ENV_FILE", DEFAULT_ENV_FILE)
    if not os.path.isfile(env_path):
        return

    with open(env_path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key:
                os.environ[key] = value.strip()


def env_bool(name: str, default: bool = False) -> bool:
    """Wandelt eine Umgebungsvariable in einen bool-Wert um (true/1/yes/on)."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


load_env_file()


# =========================
# CONFIG
# =========================

MQTT_ENABLED = env_bool("MPDBACKEND_MQTT_ENABLED", True)

MUSIC_ROOT = os.getenv("MPDBACKEND_MUSIC_ROOT", "/home/musik")
MARKED_FOR_DELETE = os.getenv(
    "MPDBACKEND_MARKED_FOR_DELETE",
    os.path.join(DEFAULT_DATA_DIR, "mark_for_delete.cfg"),
)
STATION_LOGO_DIR = os.getenv(
    "MPDBACKEND_STATION_LOGO_DIR", os.path.join(DEFAULT_DATA_DIR, "logos")
)

MPD_SOCKET = os.getenv("MPDBACKEND_MPD_SOCKET", "/run/mpd/socket")
PLAYLIST_DIR = os.getenv("MPDBACKEND_PLAYLIST_DIR", "").strip()
PUBLIC_BASE_URL = os.getenv("MPDBACKEND_PUBLIC_BASE_URL", "")

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
        self._channels = self._read_channels()

    def _read_channels(self) -> dict:
        """Liest die Sender aus der konfigurierten JSON-Datei."""
        if not self._channels_file or not os.path.isfile(self._channels_file):
            logger.warning(
                "Channels file not found: %s (copy channels.json.example to channels.json)",
                self._channels_file or DEFAULT_CHANNELS_FILE,
            )
            self._mtime = None
            return {}
        try:
            with open(self._channels_file, encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._mtime = os.path.getmtime(self._channels_file)
                logger.info("Loaded %s radio channel(s) from %s", len(data), self._channels_file)
                return data
        except Exception as err:
            logger.warning("Failed to load channels from %s: %s", self._channels_file, err)
        self._mtime = None
        return {}

    def _maybe_reload(self) -> None:
        """Lädt channels.json neu, wenn die Datei geändert wurde."""
        if not self._channels_file or not os.path.isfile(self._channels_file):
            return
        mtime = os.path.getmtime(self._channels_file)
        if self._mtime is not None and mtime <= self._mtime:
            return
        channels = self._read_channels()
        if channels:
            self._channels = channels
            logger.info("Reloaded %s radio channel(s) from %s", len(channels), self._channels_file)

    def get(self) -> dict:
        """Gibt die aktuelle Senderliste zurück."""
        with self._lock:
            self._maybe_reload()
            return self._channels


CHANNEL_REGISTRY = ChannelRegistry()


# =========================
# HELPERS
# =========================

def build_full_path(rel_path):
    """Baut den absoluten Pfad zur Audiodatei unter MUSIC_ROOT."""
    return os.path.join(MUSIC_ROOT, rel_path)


def save_current_track_file(song: dict, output_path: str | None = None) -> str:
    """Hängt den MPD-Dateipfad des aktuellen Titels an eine Textdatei an."""
    track_file = (song.get("file") or "").strip()
    if not track_file:
        raise ValueError("no current track file")

    target = (output_path or MARKED_FOR_DELETE).strip()
    if not target:
        raise ValueError("output path not configured")

    target_abs = os.path.abspath(target)
    parent = os.path.dirname(target_abs)
    if parent:
        os.makedirs(parent, exist_ok=True)

    with open(target_abs, "a", encoding="utf-8") as handle:
        handle.write(track_file)
        handle.write("\n")

    return track_file


def channel_logo_basename(channel_id: str) -> str:
    """Liefert den Dateinamen-Basis für ein Senderlogo."""
    return f"channel_{channel_id}"


def resolve_station_logo_path(channel_id: str) -> tuple[str, str] | None:
    """Sucht Senderlogo-Datei und liefert (Pfad, Content-Type)."""
    if not channel_id or not CHANNEL_ID_RE.match(channel_id):
        return None

    os.makedirs(STATION_LOGO_DIR, exist_ok=True)
    basename = channel_logo_basename(channel_id)

    exact = os.path.join(STATION_LOGO_DIR, basename)
    if os.path.isfile(exact):
        return exact, logo_content_type(exact)

    for ext in STATION_LOGO_EXTENSIONS:
        candidate = os.path.join(STATION_LOGO_DIR, f"{basename}{ext}")
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
    base = (PUBLIC_BASE_URL or "").strip().rstrip("/")
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


def resolve_active_playlist_name(
    status: dict, loaded_playlist: str, available: list[str]
) -> str:
    """Aktive Playlist: zuerst per MQTT gesetzt, sonst MPD lastloadedplaylist."""
    if loaded_playlist:
        return display_playlist_filename(loaded_playlist)

    mpd_raw = parse_status_lastloadedplaylist(status)
    if not mpd_raw:
        return ""

    display = display_playlist_filename(mpd_raw)
    if display in available:
        return display

    base = MPD.normalize_playlist_name(display)
    for item in available:
        if MPD.normalize_playlist_name(item) == base:
            return item
    return display


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
        client.connect(MPD_SOCKET)
        return client

    def connect(self) -> bool:
        """Stellt Idle- und Befehls-Verbindung zum MPD-Socket her."""
        try:
            self.client = self._new_client()
            self.command_client = self._new_client()
            return True
        except Exception:
            self.client = None
            self.command_client = None
            return False

    def connect_idle(self) -> bool:
        """Verbindet nur die Idle-Verbindung (Worker-Events)."""
        try:
            self.client = self._new_client()
            return True
        except Exception:
            self.client = None
            return False

    def connect_command(self) -> bool:
        """Verbindet nur die Befehls-Verbindung (MQTT, elapsed-resync)."""
        try:
            self.command_client = self._new_client()
            return True
        except Exception:
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
            client.clear()
            client.load(mpd_name)
            client.play()

        ok = self.run_command(_load_play)
        if ok:
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
        if PLAYLIST_DIR and os.path.isdir(PLAYLIST_DIR):
            return PLAYLIST_DIR

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
                status, loaded_playlist, available
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

        if sig == self.last_signature:
            return False

        self.last_signature = sig

        file = song.get("file")
        if file:
            self.cover.generate(build_full_path(file))

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
            self.mqtt_publisher.publish(song, status, payload, MUSIC_ROOT)

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
                self.mpd.idle()

                song, status = self.update_state()
                self.publish(song, status)

            except Exception:
                self.mpd.client = None
                time.sleep(1)


# =========================
# MAIN
# =========================

def main():
    """Startet MPD-Worker, optional MQTT und HTTP-API."""
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("mpd.base").setLevel(logging.WARNING)

    mpd = MPD()
    worker = Worker(mpd)

    if MQTT_ENABLED:
        from mpdbackend_mqtt import MqttPublisher

        env_path = os.getenv("MPDBACKEND_ENV_FILE", DEFAULT_ENV_FILE)
        publisher = MqttPublisher(worker)
        publisher.start(env_path)
        worker.mqtt_publisher = publisher
    else:
        logger.info("MQTT disabled (MPDBACKEND_MQTT_ENABLED=false)")

    worker.start()

    from mpdbackend_http import HTTPAPI

    HTTPAPI(worker, CHANNEL_REGISTRY, mqtt_enabled=MQTT_ENABLED).start()

    while True:
        time.sleep(10)


if __name__ == "__main__":
    main()
