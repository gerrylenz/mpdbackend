#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""HTTP API for mpdbackend."""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import secrets
import threading

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger("mpdbackend.http")

HTTP_HOST = "0.0.0.0"


def http_port() -> int:
    """HTTP-Port aus der Umgebung (nach load_env_file / systemd)."""
    return int(os.getenv("MPDBACKEND_HTTP_PORT", "4533"))
WEB_DIR = os.getenv(
    "MPDBACKEND_WEB_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"),
)
WEB_AUTH_QUERY = "password"

_WEB_PASSWORD_DISABLED = frozenset({"0", "false", "no", "off"})


def read_web_password() -> str:
    """Aktuelles Web-Player-Passwort aus der Umgebung (leer = kein Schutz)."""
    raw = os.getenv("MPDBACKEND_WEB_PASSWORD", "").strip()
    if raw.lower() in _WEB_PASSWORD_DISABLED:
        return ""
    return raw

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/style.css": "style.css",
    "/app.js": "app.js",
}

WEB_PROTECTED_GET = frozenset(STATIC_FILES)


def web_auth_enabled() -> bool:
    """True wenn MPDBACKEND_WEB_PASSWORD gesetzt ist."""
    return bool(read_web_password())


def path_requires_web_auth(path: str) -> bool:
    """Nur statische Web-Player-Dateien; HTTP-API bleibt ohne Auth."""
    return path in WEB_PROTECTED_GET


def password_from_query(req) -> str:
    query = dict(parse_qsl(urlparse(req.path).query))
    return query.get(WEB_AUTH_QUERY, "")


def web_control_granted(req) -> bool:
    """Vollzugriff nur bei gültigem ?password= in der Request-URL."""
    if not web_auth_enabled():
        return True

    query_password = password_from_query(req)
    configured = read_web_password()
    return bool(
        query_password and secrets.compare_digest(query_password, configured)
    )


def send_web_control_denied(req) -> None:
    """403 JSON wenn MPD-Steuerung ohne gültiges Web-Passwort aufgerufen wird."""
    HTTPAPI._send_json(
        req,
        403,
        {
            "ok": False,
            "error": "control requires password",
            "hint": f"Open /?{WEB_AUTH_QUERY}=… for full access",
        },
    )


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

    def _channel_backend_url(self, channel_id: str) -> str | None:
        """backend_url aus channels.json für eine Kanal-ID."""
        if not channel_id:
            return None
        channels = self.channel_registry.get()
        entry = channels.get(channel_id)
        if not isinstance(entry, dict):
            return None
        raw = str(entry.get("backend_url") or "").strip()
        return raw.rstrip("/") if raw else None

    def _local_backend_bases(self, req) -> set[str]:
        """URLs, die auf diesen mpdbackend-Prozess zeigen (kein Proxy nötig)."""
        public_base = os.getenv("MPDBACKEND_PUBLIC_BASE_URL", "").strip()

        host = (req.headers.get("Host") or "").strip()
        bases: set[str] = set()
        if host:
            bases.add(f"http://{host}".rstrip("/"))
            bases.add(f"https://{host}".rstrip("/"))
        port = http_port()
        if host and ":" not in host:
            bases.add(f"http://{host}:{port}".rstrip("/"))
            bases.add(f"https://{host}:{port}".rstrip("/"))
        if public_base:
            bases.add(public_base.rstrip("/"))
        return bases

    def _proxy_needed(self, backend_url: str, req) -> bool:
        """True wenn Metadaten von einem anderen mpdbackend geholt werden müssen."""
        return backend_url.rstrip("/") not in self._local_backend_bases(req)

    def _append_auth_to_path(self, req, path: str) -> str:
        """Übernimmt ?password= aus der Client-Anfrage für geschützte Backend-Proxies."""
        pwd = password_from_query(req)
        if not pwd:
            return path
        parsed = urlparse(path)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query[WEB_AUTH_QUERY] = pwd
        return f"{parsed.path}?{urlencode(query)}"

    def _fetch_backend(
        self, backend_url: str, path: str, timeout: float = 5
    ) -> tuple[int, bytes]:
        """HTTP-GET auf ein anderes mpdbackend (z. B. Now-Playing eines anderen MPD)."""
        url = f"{backend_url.rstrip('/')}{path}"
        request = Request(url, headers={"Accept": "*/*"})
        try:
            with urlopen(request, timeout=timeout) as resp:
                return resp.status, resp.read()
        except HTTPError as err:
            return err.code, err.read()

    def _channel_id_from_request(self, req) -> str:
        """Kanal-ID aus ?channel= (Web-Player / Multi-MPD-Routing)."""
        query = dict(parse_qsl(urlparse(req.path).query))
        return query.get("channel", "").strip()

    def _try_proxy_cmd_post(self, req, cmd_path: str) -> bool:
        """POST /cmd/… an backend_url des Kanals weiterleiten; True wenn proxied."""
        channel_id = self._channel_id_from_request(req)
        backend = self._channel_backend_url(channel_id)
        if not backend or not self._proxy_needed(backend, req):
            return False

        body = self._read_body(req)
        proxy_path = self._append_auth_to_path(req, cmd_path)
        url = f"{backend.rstrip('/')}{proxy_path}"
        try:
            request = Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
            with urlopen(request, timeout=10) as resp:
                status = resp.status
                response_body = resp.read()
        except HTTPError as err:
            status = err.code
            response_body = err.read()
        except (URLError, TimeoutError, OSError) as err:
            logger.warning("Backend proxy POST %s failed: %s", url, err)
            self._send_json(req, 502, {"ok": False, "error": "backend unreachable"})
            return True

        req.send_response(status)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(response_body)))
        req.end_headers()
        req.wfile.write(response_body)
        return True

    def _proxy_backend_get(self, req, backend_url: str, path: str, content_type: str) -> bool:
        """Leitet GET an backend_url weiter; True bei Erfolg."""
        path = self._append_auth_to_path(req, path)
        try:
            status, body = self._fetch_backend(backend_url, path)
        except (URLError, TimeoutError, OSError) as err:
            logger.warning("Backend proxy %s%s failed: %s", backend_url, path, err)
            self._send_json(req, 502, {"ok": False, "error": "backend unreachable"})
            return True

        if status in (401, 403):
            req.send_response(status)
            req.send_header("Content-Type", "application/json")
            req.send_header("Cache-Control", "no-store")
            req.send_header("Content-Length", str(len(body)))
            req.end_headers()
            req.wfile.write(body)
            return True

        if status != 200:
            logger.warning(
                "Backend proxy %s%s returned HTTP %s", backend_url, path, status
            )
            req.send_response(502)
            req.end_headers()
            return True

        req.send_response(200)
        req.send_header("Content-Type", content_type)
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(body)))
        req.end_headers()
        req.wfile.write(body)
        return True

    def start(self) -> None:
        """Startet den Threading-HTTP-Server im Hintergrund."""
        from mpdbackend import load_env_file

        load_env_file()
        api = self

        class Handler(BaseHTTPRequestHandler):

            def do_GET(self):
                """Leitet GET-Anfragen an die passenden Handler weiter."""
                path = urlparse(self.path).path

                if path == "/web/session":
                    api.handle_web_session(self)
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
                    if web_auth_enabled() and not web_control_granted(self):
                        send_web_control_denied(self)
                        return
                    api.handle_playlists(self)
                    return

                if path == "/health":
                    api.handle_health(self)
                    return

                if path == "/markfordelete":
                    api.handle_markfordelete(self)
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                """Steuerbefehle an MPD (Analog zu MQTT cmd-Topics)."""
                path = urlparse(self.path).path

                if path.startswith("/cmd/"):
                    if web_auth_enabled() and not web_control_granted(self):
                        send_web_control_denied(self)
                        return

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

                if path == "/markfordelete/clear":
                    api.handle_markfordelete_clear(self)
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, fmt, *args):
                """Unterdrückt Standard-Access-Log-Ausgaben."""
                return

        port = http_port()
        server = ThreadingHTTPServer((HTTP_HOST, port), Handler)
        server.worker = self.worker

        threading.Thread(target=server.serve_forever, daemon=True).start()
        if os.path.isdir(WEB_DIR):
            if web_auth_enabled():
                logger.info(
                    "Web UI at http://%s:%s/ (guest: stream+channels; full: /?%s=…)",
                    HTTP_HOST,
                    port,
                    WEB_AUTH_QUERY,
                )
            else:
                logger.info("Web UI at http://%s:%s/", HTTP_HOST, port)
        logger.info("HTTP listening on %s:%s", HTTP_HOST, port)

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
        from mpdbackend import (
            parse_status_volume,
            public_cover_url,
            resolve_active_playlist_name,
        )

        parsed = urlparse(req.path)
        query = dict(parse_qsl(parsed.query))
        channel_id = query.get("channel", "").strip()
        backend = self._channel_backend_url(channel_id)
        if backend and self._proxy_needed(backend, req):
            self._proxy_backend_get(req, backend, "/nowplaying", "application/json")
            return

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
            "media_image_url": public_cover_url(self.worker.cover.cover_name()),
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
        parsed = urlparse(req.path)
        query = dict(parse_qsl(parsed.query))
        channel_id = query.get("channel", "").strip()
        backend = self._channel_backend_url(channel_id)
        if backend and self._proxy_needed(backend, req):
            self._proxy_backend_get(req, backend, "/playlists", "application/json")
            return

        raw = json.dumps(self._build_playlist_state()).encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "application/json")
        req.send_header("Cache-Control", "no-store")
        req.send_header("Content-Length", str(len(raw)))
        req.end_headers()
        req.wfile.write(raw)

    def handle_markfordelete(self, req):
        """GET /markfordelete – Inhalt von mark_for_delete.cfg als JSON."""
        from mpdbackend import MARKED_FOR_DELETE, load_marked_for_delete_entries

        parsed = urlparse(req.path)
        query = dict(parse_qsl(parsed.query))
        channel_id = query.get("channel", "").strip()
        backend = self._channel_backend_url(channel_id)
        if backend and self._proxy_needed(backend, req):
            proxy_path = "/markfordelete"
            self._proxy_backend_get(req, backend, proxy_path, "application/json")
            return

        payload = {
            "path": os.path.abspath(MARKED_FOR_DELETE),
            "files": load_marked_for_delete_entries(),
        }
        self._send_json(req, 200, payload)

    def handle_markfordelete_clear(self, req) -> None:
        """POST /markfordelete/clear – mark_for_delete.cfg auf diesem Server leeren."""
        from mpdbackend import clear_marked_for_delete_file

        channel_id = self._channel_id_from_request(req)
        backend = self._channel_backend_url(channel_id)
        if backend and self._proxy_needed(backend, req):
            self._try_proxy_cmd_post(req, "/markfordelete/clear")
            return

        try:
            path = clear_marked_for_delete_file()
        except ValueError as err:
            self._send_json(req, 503, {"ok": False, "error": str(err)})
            return
        except OSError as err:
            logger.warning("Failed to clear mark_for_delete file: %s", err)
            self._send_json(req, 503, {"ok": False, "error": "clear failed"})
            return

        self._send_json(req, 200, {"ok": True, "path": path})

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
        channel_id = query.get("channel", "").strip()
        backend = self._channel_backend_url(channel_id)
        if (
            name
            and COVER_NAME_RE.match(name)
            and backend
            and self._proxy_needed(backend, req)
        ):
            proxy_path = f"/cover?{urlencode({'name': name})}"
            self._proxy_backend_get(req, backend, proxy_path, "image/jpeg")
            return

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
        backend = self._channel_backend_url(channel_id)
        if backend and self._proxy_needed(backend, req):
            proxy_path = self._append_auth_to_path(
                req, f"/stationlogo?channel={quote(channel_id)}"
            )
            try:
                status, body = self._fetch_backend(backend, proxy_path)
                if status == 200:
                    content_type = "image/png"
                    req.send_response(200)
                    req.send_header("Content-Type", content_type)
                    req.send_header("Cache-Control", "no-store")
                    req.send_header("Content-Length", str(len(body)))
                    req.end_headers()
                    req.wfile.write(body)
                    return
            except (HTTPError, URLError, TimeoutError, OSError) as err:
                logger.debug(
                    "Station logo proxy %s%s failed, trying local: %s",
                    backend,
                    proxy_path,
                    err,
                )

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

    def handle_web_session(self, req) -> None:
        """GET /web/session – Gast vs. Vollzugriff für den Web-Player."""
        auth_required = web_auth_enabled()
        control = web_control_granted(req)
        self._send_json(
            req,
            200,
            {
                "auth_required": auth_required,
                "control_granted": control,
                "login_query": WEB_AUTH_QUERY,
            },
        )

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
            "web_auth_required": web_auth_enabled(),
            "web_control_granted": web_control_granted(req),
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

        if self._try_proxy_cmd_post(req, "/cmd/player"):
            return

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

        if self._try_proxy_cmd_post(req, "/cmd/volume"):
            return

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

        if self._try_proxy_cmd_post(req, "/cmd/playlist"):
            return

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

        if self._try_proxy_cmd_post(req, "/cmd/savefile"):
            return

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
