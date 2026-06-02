#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""HTTP API for mpdbackend."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import secrets
import threading

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qsl, urlparse

logger = logging.getLogger("mpdbackend.http")

HTTP_HOST = "0.0.0.0"
HTTP_PORT = int(os.getenv("MPDBACKEND_HTTP_PORT", "4533"))
WEB_DIR = os.getenv(
    "MPDBACKEND_WEB_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"),
)
WEB_PASSWORD = os.getenv("MPDBACKEND_WEB_PASSWORD", "").strip()
WEB_AUTH_REALM = "mpdbackend Web Player"

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/style.css": "style.css",
    "/app.js": "app.js",
}

WEB_PROTECTED_GET = frozenset(STATIC_FILES)


def web_auth_enabled() -> bool:
    """True wenn MPDBACKEND_WEB_PASSWORD gesetzt ist."""
    return bool(WEB_PASSWORD)


def path_requires_web_auth(path: str) -> bool:
    """Nur statische Web-Player-Dateien; HTTP-API bleibt ohne Auth."""
    return path in WEB_PROTECTED_GET


def parse_basic_auth(header: str) -> tuple[str, str] | None:
    """Authorization: Basic … → (username, password) oder None."""
    if not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, sep, password = decoded.partition(":")
    if not sep:
        return None
    return username, password


def web_auth_valid(req) -> bool:
    """Prüft HTTP-Basic-Auth: nur Passwort (Benutzername wird ignoriert)."""
    if not web_auth_enabled():
        return True
    parsed = parse_basic_auth(req.headers.get("Authorization", ""))
    if parsed is None:
        return False
    _username, password = parsed
    return secrets.compare_digest(password, WEB_PASSWORD)


def send_web_auth_required(req) -> None:
    """401 mit WWW-Authenticate für Browser-Login."""
    req.send_response(401)
    req.send_header("WWW-Authenticate", f'Basic realm="{WEB_AUTH_REALM}"')
    req.send_header("Cache-Control", "no-store")
    req.send_header("Content-Length", "0")
    req.end_headers()


class HTTPAPI:
    """HTTP-Endpunkte für Now-Playing, Cover, Sender, Web-UI und Steuerung."""

    def __init__(self, worker, channel_registry, *, mqtt_enabled: bool) -> None:
        """Speichert Worker, Channel-Registry und MQTT-Status für Health."""
        self.worker = worker
        self.channel_registry = channel_registry
        self.mqtt_enabled = mqtt_enabled
        self.loaded_playlist = ""

    def _resolve_loaded_playlist(self) -> str:
        """Aktive Playlist aus HTTP- oder MQTT-Kontext."""
        publisher = self.worker.mqtt_publisher
        if publisher and publisher.loaded_playlist:
            return publisher.loaded_playlist
        return self.loaded_playlist

    def _build_playlist_state(self) -> dict:
        """Verfügbare und aktive MPD-Playlist für HTTP-Responses."""
        from mpdbackend import resolve_active_playlist_name

        status = self.worker.last_status or {}
        available = self.worker.mpd.available_playlists()
        active = resolve_active_playlist_name(
            status, self._resolve_loaded_playlist(), available
        )
        return {"playlists": available, "active": active}

    def start(self) -> None:
        """Startet den Threading-HTTP-Server im Hintergrund."""
        api = self

        class Handler(BaseHTTPRequestHandler):

            def do_GET(self):
                """Leitet GET-Anfragen an die passenden Handler weiter."""
                path = urlparse(self.path).path

                if path_requires_web_auth(path) and not web_auth_valid(self):
                    send_web_auth_required(self)
                    return

                if path in STATIC_FILES:
                    api.handle_static(self, STATIC_FILES[path])
                    return

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

                if path == "/playlists":
                    api.handle_playlists(self)
                    return

                if path == "/health":
                    api.handle_health(self)
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                """Steuerbefehle an MPD (Analog zu MQTT cmd-Topics)."""
                path = urlparse(self.path).path

                if path == "/cmd/player":
                    api.handle_cmd_player(self)
                    return

                if path == "/cmd/volume":
                    api.handle_cmd_volume(self)
                    return

                if path == "/cmd/playlist":
                    api.handle_cmd_playlist(self)
                    return

                if path == "/cmd/savefile":
                    api.handle_cmd_savefile(self)
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, fmt, *args):
                """Unterdrückt Standard-Access-Log-Ausgaben."""
                return

        server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
        server.worker = self.worker

        threading.Thread(target=server.serve_forever, daemon=True).start()
        if os.path.isdir(WEB_DIR):
            if web_auth_enabled():
                logger.info(
                    "Web UI at http://%s:%s/ (password protected)",
                    HTTP_HOST,
                    HTTP_PORT,
                )
            else:
                logger.info("Web UI at http://%s:%s/", HTTP_HOST, HTTP_PORT)
        logger.info("HTTP listening on %s:%s", HTTP_HOST, HTTP_PORT)

    @staticmethod
    def _read_body(req) -> bytes:
        length = int(req.headers.get("Content-Length", 0))
        if length <= 0:
            return b""
        return req.rfile.read(length)

    @staticmethod
    def _send_json(req, status: int, data: dict) -> None:
        raw = json.dumps(data).encode("utf-8")
        req.send_response(status)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_static(self, req, filename: str) -> None:
        """Liefert Dateien aus dem web/-Verzeichnis aus."""
        safe_name = os.path.basename(filename)
        path = os.path.join(WEB_DIR, safe_name)
        web_root = os.path.abspath(WEB_DIR)

        if not os.path.abspath(path).startswith(web_root + os.sep):
            req.send_response(404)
            req.end_headers()
            return

        if not os.path.isfile(path):
            req.send_response(404)
            req.end_headers()
            return

        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as handle:
            data = handle.read()

        req.send_response(200)
        req.send_header("Content-Type", content_type)
        if safe_name == "index.html":
            req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(data)))
        req.end_headers()
        req.wfile.write(data)

    def handle_nowplaying(self, req):
        """GET /nowplaying – aktuelle Track-Metadaten als JSON."""
        from mpdbackend import parse_status_volume, resolve_active_playlist_name

        song = self.worker.last_song or {}
        status = self.worker.last_status or {}
        elapsed_status = self.worker.build_elapsed_status()
        available = self.worker.mpd.available_playlists()
        active_playlist = resolve_active_playlist_name(
            status, self._resolve_loaded_playlist(), available
        )

        data = {
            "state": status.get("state"),
            "title": song.get("title"),
            "artist": song.get("artist"),
            "album": song.get("album"),
            "songid": status.get("songid"),
            "duration": self.worker.resolve_duration(song, status),
            "elapsed": float(elapsed_status.get("elapsed") or 0),
            "cover_name": self.worker.cover.cover_name(),
            "media_image_url": self.worker.cover.cover_name(),
            "playlist": active_playlist,
            "file": song.get("file") or "",
        }
        volume = parse_status_volume(status)
        if volume is not None:
            data["volume"] = volume

        song_pos = status.get("song")
        if song_pos is not None:
            data["pos"] = int(song_pos) + 1
        playlist_length = status.get("playlistlength")
        if playlist_length is not None:
            data["playlist_length"] = int(playlist_length)

        raw = json.dumps(data).encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_playlists(self, req):
        """GET /playlists – verfügbare MPD-Playlists und aktive Playlist."""
        raw = json.dumps(self._build_playlist_state()).encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_channels(self, req):
        """GET /channels – Senderliste aus channels.json."""
        from mpdbackend import enrich_channels_payload

        raw = json.dumps(enrich_channels_payload(self.channel_registry.get())).encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_cover(self, req):
        """GET /cover?name=… – gecachtes Cover-JPEG ausliefern."""
        from mpdbackend_cover import COVER_NAME_RE

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
        """GET /stationlogo?channel=… – Senderlogo-Bild ausliefern."""
        from mpdbackend import resolve_station_logo_path

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
        """GET /hash – MD5-Hash des aktuellen Now-Playing-Payloads."""
        raw = (self.worker.current_hash or "").encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "text/plain; charset=utf-8")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_health(self, req):
        """GET /health – Verbindungsstatus MPD und optional MQTT."""
        mpd_connected = self.worker.mpd.client is not None
        publisher = self.worker.mqtt_publisher

        if publisher:
            mqtt_connected = publisher.is_connected()
            mqtt_status = "connected" if mqtt_connected else "disconnected"
            healthy = mpd_connected and mqtt_connected
        else:
            mqtt_status = "disabled"
            healthy = mpd_connected

        data = {
            "status": "ok" if healthy else "degraded",
            "mpd": "connected" if mpd_connected else "disconnected",
            "mqtt": mqtt_status,
            "mqtt_enabled": self.mqtt_enabled,
        }
        raw = json.dumps(data).encode("utf-8")

        req.send_response(200 if healthy else 503)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_cmd_player(self, req) -> None:
        """POST /cmd/player – play|stop|next|back (Plain-Text)."""
        from mpdbackend_mqtt import parse_mqtt_player_command

        command = parse_mqtt_player_command(self._read_body(req))
        if command is None:
            self._send_json(req, 400, {"ok": False, "error": "invalid command"})
            return

        if not self.worker.mpd.execute_player_action(command.value):
            self._send_json(req, 503, {"ok": False, "error": "mpd command failed"})
            return

        self._send_json(req, 200, {"ok": True, "command": command.value})

    def handle_cmd_volume(self, req) -> None:
        """POST /cmd/volume – Lautstärke 0–100 (Plain-Text)."""
        from mpdbackend_mqtt import parse_mqtt_volume_command

        volume = parse_mqtt_volume_command(self._read_body(req))
        if volume is None:
            self._send_json(req, 400, {"ok": False, "error": "invalid volume"})
            return

        if not self.worker.mpd.set_volume(volume):
            self._send_json(req, 503, {"ok": False, "error": "mpd setvol failed"})
            return

        song, status = self.worker.update_state()
        self.worker.publish(song, status)
        self._send_json(req, 200, {"ok": True, "volume": volume})

    def handle_cmd_playlist(self, req) -> None:
        """POST /cmd/playlist – Playlist laden und abspielen (Plain-Text)."""
        from mpdbackend_mqtt import parse_mqtt_playlist_name

        playlist = parse_mqtt_playlist_name(self._read_body(req))
        if not playlist:
            self._send_json(req, 400, {"ok": False, "error": "empty playlist name"})
            return

        if not self.worker.mpd.load_and_play_playlist(playlist):
            self._send_json(req, 503, {"ok": False, "error": "load playlist failed"})
            return

        self.loaded_playlist = playlist
        publisher = self.worker.mqtt_publisher
        if publisher:
            publisher.set_loaded_playlist(playlist)
            publisher.state_cache = None

        song, status = self.worker.update_state()
        self.worker.publish(song, status)
        self._send_json(req, 200, {"ok": True, "playlist": playlist})

    def handle_cmd_savefile(self, req) -> None:
        """POST /cmd/savefile – aktuellen MPD-Dateipfad an Textdatei anhängen."""
        from mpdbackend import MARKED_FOR_DELETE, save_current_track_file

        song = self.worker.last_song or {}
        try:
            track_file = save_current_track_file(song)
        except ValueError as err:
            if str(err) == "no current track file":
                self._send_json(req, 400, {"ok": False, "error": str(err)})
                return
            self._send_json(req, 503, {"ok": False, "error": str(err)})
            return
        except OSError as err:
            logger.warning("Failed to write current file: %s", err)
            self._send_json(req, 503, {"ok": False, "error": "write failed"})
            return

        self._send_json(
            req,
            200,
            {
                "ok": True,
                "file": track_file,
                "path": os.path.abspath(MARKED_FOR_DELETE),
            },
        )
