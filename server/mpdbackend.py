#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import sys
import time
import logging
import threading
import os
import subprocess
import hashlib

from io import BytesIO
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qsl, urlparse

from PIL import Image
from mpd import MPDClient
from paho.mqtt import client as mqtt_client


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATA_DIR = os.path.join(BASE_DIR, "data")
DEFAULT_ENV_FILE = os.path.join(BASE_DIR, "mpdbackend.env")


def load_env_file() -> None:
    """Load mpdbackend.env into os.environ (does not override existing variables)."""
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
            if key and key not in os.environ:
                os.environ[key] = value.strip()


load_env_file()


# =========================
# CONFIG
# =========================

MQTT_BROKER = os.getenv("MPDBACKEND_MQTT_BROKER", "")
MQTT_PORT = int(os.getenv("MPDBACKEND_MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MPDBACKEND_MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MPDBACKEND_MQTT_PASSWORD", "")

TOPIC_STATE = os.getenv("MPDBACKEND_MQTT_TOPIC_STATE", "mpdbackend/state")
TOPIC_COVER = os.getenv("MPDBACKEND_MQTT_TOPIC_COVER", "mpdbackend/cover")

MUSIC_ROOT = os.getenv("MPDBACKEND_MUSIC_ROOT", "/home/musik")
COVER_DIR = os.getenv("MPDBACKEND_COVER_DIR", os.path.join(DEFAULT_DATA_DIR, "covers"))
STATION_LOGO_DIR = os.getenv(
    "MPDBACKEND_STATION_LOGO_DIR", os.path.join(DEFAULT_DATA_DIR, "logos")
)

MPD_SOCKET = os.getenv("MPDBACKEND_MPD_SOCKET", "/run/mpd/socket")
PUBLIC_BASE_URL = os.getenv("MPDBACKEND_PUBLIC_BASE_URL", "")

HTTP_HOST = "0.0.0.0"
HTTP_PORT = int(os.getenv("MPDBACKEND_HTTP_PORT", "4533"))

logger = logging.getLogger("mpdbackend")

COVER_NAME_RE = re.compile(r"^cover_[0-9a-f]{16,64}\.jpg$")
CHANNEL_ID_RE = re.compile(r"^[0-9a-zA-Z_-]{1,32}$")
STATION_LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
FOLDER_COVER_NAMES = ("cover.jpg", "folder.jpg", "Folder.jpg", "cover.png", "folder.png")

DEFAULT_CHANNELS_FILE = os.path.join(BASE_DIR, "channels.json")


class ChannelRegistry:
    """Thread-safe radio channel registry loaded from channels.json."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._channels_file = os.getenv("MPDBACKEND_CHANNELS_FILE", DEFAULT_CHANNELS_FILE).strip()
        self._mtime: float | None = None
        self._channels = self._read_channels()

    def _read_channels(self) -> dict:
        """Load channels from the configured JSON file."""
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
        """Reload channels when the configured file changed on disk."""
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
        """Return the current channel registry."""
        with self._lock:
            self._maybe_reload()
            return self._channels


CHANNEL_REGISTRY = ChannelRegistry()


# =========================
# HELPERS
# =========================

def build_full_path(rel_path):
    return os.path.join(MUSIC_ROOT, rel_path)


def channel_logo_basename(channel_id: str) -> str:
    """Return the on-disk basename for a channel logo file."""
    return f"channel_{channel_id}"


def resolve_station_logo_path(channel_id: str) -> tuple[str, str] | None:
    """Return (file path, content type) for a station logo by channel id."""
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


def logo_content_type(path: str) -> str:
    """Return HTTP content type for a logo file."""
    ext = os.path.splitext(path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")


# =========================
# COVER
# =========================

class CoverService:

    def __init__(self):
        self.cover_dir = COVER_DIR
        self.current = "blank.jpg"
        os.makedirs(self.cover_dir, exist_ok=True)

    def _ffmpeg_extract_map(self, path: str, map_selector: str) -> bytes | None:
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
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
            if result.returncode != 0 or not result.stdout:
                return None
            return result.stdout
        except Exception:
            return None

    def ffmpeg_extract(self, path):
        """Extract embedded cover art via ffmpeg (video stream or ID3 APIC)."""
        for map_selector in ("0:v:0", "0:p:0"):
            raw = self._ffmpeg_extract_map(path, map_selector)
            if raw:
                return raw
        return self._folder_cover(path)

    def _folder_cover(self, audio_file: str) -> bytes | None:
        """Return folder-level cover art bytes when present beside the audio file."""
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

    def process(self, raw):
        try:
            img = Image.open(BytesIO(raw))
            img = img.convert("RGB")
            img.thumbnail((512, 512))

            buf = BytesIO()
            img.save(buf, format="JPEG", quality=85)
            return buf.getvalue()
        except Exception:
            return None

    def cache_name(self, audio_file):
        stat = os.stat(audio_file)
        key = f"{audio_file}:{stat.st_size}:{stat.st_mtime_ns}"
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return f"cover_{digest}.jpg"

    def generate(self, audio_file):
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
            with open(tmp, "wb") as f:
                f.write(img)
            os.replace(tmp, path)

    def cover_name(self):
        return self.current if self.current != "blank.jpg" else ""

    def path(self):
        return os.path.join(self.cover_dir, self.current)


# =========================
# MPD
# =========================

class MPD:

    def __init__(self):
        self.client = None
        self.lock = threading.Lock()

    def connect(self):
        try:
            self.client = MPDClient()
            self.client.timeout = 5
            self.client.connect(MPD_SOCKET)
            return True
        except Exception:
            self.client = None
            return False

    def safe(self, cmd):
        with self.lock:
            try:
                if not self.client and not self.connect():
                    return {}
                return getattr(self.client, cmd)()
            except Exception:
                self.client = None
                return {}

# =========================
# WORKER
# =========================

class Worker(threading.Thread):

    def __init__(self, mpd):
        super().__init__(daemon=True)
        self.mpd = mpd
        self.mqtt = None

        self.last_song = {}
        self.last_status = {}

        self.cover = CoverService()
        self.stop_flag = False

        self.last_signature = None
        self.state_cache = None

        self.lock = threading.Lock()

        self.current_hash = ""

    # -------------------------
    # duration fix (IMPORTANT)
    # -------------------------
    def resolve_duration(self, song, status):
        return float(song.get("time") or status.get("duration") or 0)

    # -------------------------
    # snapshot
    # -------------------------
    def snapshot(self):
        song = self.mpd.safe("currentsong")
        status = self.mpd.safe("status")

        with self.lock:
            self.last_song = song
            self.last_status = status
        
        return song, status

    # -------------------------
    # track change
    # -------------------------
    def handle_track(self, song, status):
        sig = (song.get("file"), status.get("songid"))

        if sig == self.last_signature:
            return False

        self.last_signature = sig

        file = song.get("file")
        if file:
            self.cover.generate(build_full_path(file))

        return True

    # -------------------------
    # publish
    # -------------------------
    def publish(self, song, status):

        changed = self.handle_track(song, status)

        payload = {
            "state": status.get("state"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "album": song.get("album"),
            "elapsed": float(status.get("elapsed") or 0),
            "duration": self.resolve_duration(song, status),
            "media_image_url": self.cover.cover_name(),
        }

        new_hash  = hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        with self.lock:
            self.current_hash = new_hash

        if payload != self.state_cache:
            self.state_cache = payload
            self.mqtt.publish(TOPIC_STATE, json.dumps(payload), retain=True)
            
    # -------------------------
    # publish
    # -------------------------
    def update_state(self):
        song = self.mpd.safe("currentsong") or {}
        status = self.mpd.safe("status") or {}

        with self.lock:
            self.last_song = song
            self.last_status = status

        return song, status
    
    # -------------------------
    # LOOP (idle = correct approach)
    # -------------------------
    def run(self):
        if not self.mpd.connect():
            time.sleep(2)
        
        # Initial load erzwingen
        song = self.mpd.safe("currentsong") or {}
        status = self.mpd.safe("status") or {}

        self.handle_track(song, status) # FORCE FIRST COVER
        self.update_state()
        self.publish(song, status)

        while not self.stop_flag:
            try:
                if not self.mpd.client and not self.mpd.connect():
                    time.sleep(2)
                    continue
                self.mpd.client.idle()

                song, status = self.update_state()
                self.publish(song, status)

            except Exception:
                self.mpd.client = None
                time.sleep(1)


# =========================
# MQTT
# =========================

def validate_config() -> None:
    """Abort startup when required settings from mpdbackend.env are missing."""
    env_path = os.getenv("MPDBACKEND_ENV_FILE", DEFAULT_ENV_FILE)
    missing = [
        name
        for name, value in (
            ("MPDBACKEND_MQTT_BROKER", MQTT_BROKER),
            ("MPDBACKEND_MQTT_USERNAME", MQTT_USERNAME),
            ("MPDBACKEND_MQTT_PASSWORD", MQTT_PASSWORD),
        )
        if not value
    ]
    if missing:
        logger.error(
            "Missing required settings in %s: %s",
            env_path,
            ", ".join(missing),
        )
        sys.exit(1)


def create_mqtt(worker):
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    return client


# =========================
# HTTP
# =========================

class HTTPAPI:

    def __init__(self, worker, channel_registry: ChannelRegistry):
        self.worker = worker
        self.channel_registry = channel_registry

    def start(self):

        api = self  # wichtig: closure für Handler

        class Handler(BaseHTTPRequestHandler):

            def do_GET(self):
                path = urlparse(self.path).path

                if path == "/hash":
                    api.handle_changed(self)
                    return

                if path == "/nowplaying":
                    api.handle_nowplaying(self)
                    return

                if path == "/cover":
                    api.handle_cover(self)
                    return

                if path == "/stationlogo":
                    api.handle_stationlogo(self)
                    return

                if path == "/channels":
                    api.handle_channels(self)
                    return

                if path == "/health":
                    api.handle_health(self)
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, fmt, *args):
                # optional: weniger Spam im log
                return

        server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)

        # worker in server injizieren
        server.worker = self.worker

        threading.Thread(target=server.serve_forever, daemon=True).start()

    def handle_nowplaying(self, req):
        song = self.worker.last_song or {}
        status = self.worker.last_status or {}

        data = {
            "state": status.get("state"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "album": song.get("album"),
            "songid": status.get("songid"),
            "duration": self.worker.resolve_duration(song, status),
            "elapsed": float(status.get("elapsed") or 0),
            "cover_name": self.worker.cover.cover_name(),
            "media_image_url": self.worker.cover.cover_name(),
        }

        raw = json.dumps(data).encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_channels(self, req):
        raw = json.dumps(self.channel_registry.get()).encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_cover(self, req):
        parsed = urlparse(req.path)
        query = dict(parse_qsl(parsed.query))
        name = query.get("name", "")

        if not name or not COVER_NAME_RE.match(name):
            req.send_response(404)
            req.end_headers()
            return

        path = os.path.join(self.worker.cover.cover_dir, name)
        cover_root = os.path.abspath(self.worker.cover.cover_dir)
        if not os.path.abspath(path).startswith(cover_root + os.sep):
            req.send_response(404)
            req.end_headers()
            return

        if not os.path.exists(path):
            req.send_response(404)
            req.end_headers()
            return

        with open(path, "rb") as f:
            data = f.read()

        req.send_response(200)
        req.send_header("Content-Type", "image/jpeg")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(data)))
        req.end_headers()
        req.wfile.write(data)

    def handle_stationlogo(self, req):
        parsed = urlparse(req.path)
        query = dict(parse_qsl(parsed.query))
        channel_id = query.get("channel", "").strip()

        resolved = resolve_station_logo_path(channel_id)
        if not resolved:
            req.send_response(404)
            req.end_headers()
            return

        path, content_type = resolved
        with open(path, "rb") as f:
            data = f.read()

        req.send_response(200)
        req.send_header("Content-Type", content_type)
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(data)))
        req.end_headers()
        req.wfile.write(data)

    def handle_changed(self, req):
        raw = (self.worker.current_hash or "").encode("utf-8")
        
        req.send_response(200)
        req.send_header("Content-Type", "text/plain; charset=utf-8")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_health(self, req):
        mpd_connected = self.worker.mpd.client is not None
        mqtt_connected = bool(self.worker.mqtt and self.worker.mqtt.is_connected())
        healthy = mpd_connected and mqtt_connected
        data = {
            "status": "ok" if healthy else "degraded",
            "mpd": "connected" if mpd_connected else "disconnected",
            "mqtt": "connected" if mqtt_connected else "disconnected",
        }
        raw = json.dumps(data).encode("utf-8")

        req.send_response(200 if healthy else 503)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

# =========================
# MAIN
# =========================

def main():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("mpd.base").setLevel(logging.WARNING)
    validate_config()

    mpd = MPD()
    worker = Worker(mpd)

    mqtt = create_mqtt(worker)
    worker.mqtt = mqtt

    worker.start()
    HTTPAPI(worker, CHANNEL_REGISTRY).start()

    while True:
        time.sleep(10)


if __name__ == "__main__":
    main()
