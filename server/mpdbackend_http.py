#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""HTTP API for mpdbackend."""

from __future__ import annotations

import json
import logging
import os
import threading

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qsl, urlparse

logger = logging.getLogger("mpdbackend.http")

HTTP_HOST = "0.0.0.0"
HTTP_PORT = int(os.getenv("MPDBACKEND_HTTP_PORT", "4533"))


class HTTPAPI:
    """HTTP-Endpunkte für Now-Playing, Cover, Sender und Health."""

    def __init__(self, worker, channel_registry, *, mqtt_enabled: bool) -> None:
        """Speichert Worker, Channel-Registry und MQTT-Status für Health."""
        self.worker = worker
        self.channel_registry = channel_registry
        self.mqtt_enabled = mqtt_enabled

    def start(self) -> None:
        """Startet den Threading-HTTP-Server im Hintergrund."""
        api = self

        class Handler(BaseHTTPRequestHandler):

            def do_GET(self):
                """Leitet GET-Anfragen an die passenden Handler weiter."""
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
                """Unterdrückt Standard-Access-Log-Ausgaben."""
                return

        server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT), Handler)
        server.worker = self.worker

        threading.Thread(target=server.serve_forever, daemon=True).start()
        logger.info("HTTP listening on %s:%s", HTTP_HOST, HTTP_PORT)

    def handle_nowplaying(self, req):
        """GET /nowplaying – aktuelle Track-Metadaten als JSON."""
        song = self.worker.last_song or {}
        status = self.worker.last_status or {}
        elapsed_status = self.worker.build_elapsed_status()

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
        }

        raw = json.dumps(data).encode("utf-8")

        req.send_response(200)
        req.send_header("Content-Type", "application/json")
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
