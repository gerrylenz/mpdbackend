#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""HTTP API for mpdbackend."""

from __future__ import annotations

import hashlib
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
WEB_AUTH_COOKIE = "mpdbackend_web"
WEB_AUTH_QUERY = "password"
WEB_AUTH_COOKIE_MAX_AGE = 30 * 24 * 3600

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


def web_auth_token() -> str:
    """Cookie-Wert aus dem konfigurierten Passwort (nicht das Klartext-Passwort)."""
    return hashlib.sha256(WEB_PASSWORD.encode("utf-8")).hexdigest()


def parse_cookies(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for part in header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def web_auth_cookie_valid(req) -> bool:
    token = parse_cookies(req.headers.get("Cookie", "")).get(WEB_AUTH_COOKIE, "")
    if not token:
        return False
    return secrets.compare_digest(token, web_auth_token())


def password_from_query(req) -> str:
    query = dict(parse_qsl(urlparse(req.path).query))
    return query.get(WEB_AUTH_QUERY, "")


def query_has_password(req) -> bool:
    return WEB_AUTH_QUERY in dict(parse_qsl(urlparse(req.path).query))


def build_web_auth_cookie() -> str:
    return (
        f"{WEB_AUTH_COOKIE}={web_auth_token()}; Path=/; HttpOnly; "
        f"SameSite=Lax; Max-Age={WEB_AUTH_COOKIE_MAX_AGE}"
    )


def evaluate_web_static_access(req) -> tuple[bool, bool, str | None]:
    """
    Zugriff auf statische Web-Player-Dateien prüfen.

    Returns:
        (granted, set_cookie, redirect_path)
    """
    if not web_auth_enabled():
        return True, False, None

    if web_auth_cookie_valid(req):
        return True, False, None

    query_password = password_from_query(req)
    if query_password and secrets.compare_digest(query_password, WEB_PASSWORD):
        path = urlparse(req.path).path
        if query_has_password(req) and path in ("/", "/index.html"):
            return True, True, "/" if path == "/" else "/index.html"
        return True, True, None

    return False, False, None


def send_web_access_denied(req) -> None:
    """403 wenn Web-Player ohne gültiges URL-Passwort aufgerufen wird."""
    body = (
        "<!DOCTYPE html><html lang=\"de\"><head><meta charset=\"utf-8\">"
        "<title>403 Forbidden</title></head><body>"
        "<h1>403 Forbidden</h1>"
        f"<p>Web-Player: Passwort als URL-Parameter, z.&nbsp;B. "
        f"<code>/?{WEB_AUTH_QUERY}=…</code></p>"
        "</body></html>"
    ).encode("utf-8")
    req.send_response(403)
    req.send_header("Content-Type", "text/html; charset=utf-8")
    req.send_header("Cache-Control", "no-store")
    req.send_header("Content-Length", str(len(body)))
    req.end_headers()
    req.wfile.write(body)


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

                if path in STATIC_FILES:
                    granted, set_cookie, redirect = evaluate_web_static_access(self)
                    if not granted:
                        send_web_access_denied(self)
                        return
                    if redirect:
                        self.send_response(302)
                        self.send_header("Location", redirect)
                        if set_cookie:
                            self.send_header("Set-Cookie", build_web_auth_cookie())
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        return
                    api.handle_static(self, STATIC_FILES[path], set_auth_cookie=set_cookie)
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
                    "Web UI at http://%s:%s/?%s=… (password in URL)",
                    HTTP_HOST,
                    HTTP_PORT,
                    WEB_AUTH_QUERY,
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

    def handle_static(self, req, filename: str, *, set_auth_cookie: bool = False) -> None:
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
        if set_auth_cookie:
            req.send_header("Set-Cookie", build_web_auth_cookie())
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
