#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""MQTT publishing and control for mpdbackend."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from enum import StrEnum

from paho.mqtt import client as mqtt_client

from mpdbackend import build_mpd_status_data, format_playback_time

logger = logging.getLogger("mpdbackend.mqtt")

DEFAULT_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mpdbackend.env")

MQTT_BROKER = os.getenv("MPDBACKEND_MQTT_BROKER", "")
MQTT_PORT = int(os.getenv("MPDBACKEND_MQTT_PORT", "1883"))
MQTT_USERNAME = os.getenv("MPDBACKEND_MQTT_USERNAME", "")
MQTT_PASSWORD = os.getenv("MPDBACKEND_MQTT_PASSWORD", "")

TOPIC_STATE = os.getenv("MPDBACKEND_MQTT_TOPIC_STATE", "mpdbackend/state")
TOPIC_COVER = os.getenv("MPDBACKEND_MQTT_TOPIC_COVER", "mpdbackend/cover")
TOPIC_CURRENT = os.getenv("MPDBACKEND_MQTT_TOPIC_CURRENT", "mpdbackend/current")
TOPIC_PLAYLISTS = os.getenv(
    "MPDBACKEND_MQTT_TOPIC_PLAYLISTS", "mpdbackend/playlists"
).strip("/")
TOPIC_ELAPSED = os.getenv("MPDBACKEND_MQTT_TOPIC_ELAPSED", "mpdbackend/elapsed")
TOPIC_STATUS = os.getenv("MPDBACKEND_MQTT_TOPIC_STATUS", "mpdbackend/status").strip("/")
TOPIC_CMD_VOLUME = os.getenv(
    "MPDBACKEND_MQTT_TOPIC_CMD_VOLUME", "mpdbackend/cmd/volume"
).strip("/")
TOPIC_CMD_PLAYER = os.getenv(
    "MPDBACKEND_MQTT_TOPIC_CMD_PLAYER", "mpdbackend/cmd/player"
).strip("/")
TOPIC_CMD_PLAYLIST = os.getenv(
    "MPDBACKEND_MQTT_TOPIC_CMD_PLAYLIST", "mpdbackend/cmd/playlist"
).strip("/")
TOPIC_CONNECTED = os.getenv(
    "MPDBACKEND_MQTT_TOPIC_CONNECTED", "mpdbackend/connected"
).strip("/")

_elapsed_interval_raw = float(os.getenv("MPDBACKEND_MQTT_ELAPSED_INTERVAL", "1"))
ELAPSED_INTERVAL = _elapsed_interval_raw if _elapsed_interval_raw > 0 else 1.0


class PlayerCommand(StrEnum):
    """MQTT-Befehle auf mpdbackend/cmd/player."""

    PLAY = "play"
    STOP = "stop"
    NEXT = "next"
    BACK = "back"
    LOADPLAYLIST = "loadplaylist"

    @classmethod
    def parse(cls, raw: str) -> PlayerCommand | None:
        """Parst play|stop|next|back aus Plain-Text-Payload."""
        normalized = raw.strip().lower()
        try:
            command = cls(normalized)
        except ValueError:
            return None
        if command is cls.LOADPLAYLIST:
            return None
        return command


def parse_mqtt_player_command(payload: bytes) -> PlayerCommand | None:
    """Liest Befehl aus Plain-Text auf mpdbackend/cmd/player."""
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    return PlayerCommand.parse(text)


def parse_mqtt_volume_command(payload: bytes) -> int | None:
    """Liest Lautstärke 0–100 aus Plain-Text auf mpdbackend/cmd/volume."""
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        return max(0, min(100, int(float(text))))
    except (TypeError, ValueError):
        return None


def parse_mqtt_playlist_name(payload: bytes) -> str:
    """Liest Playlist-Namen aus Plain-Text auf mpdbackend/cmd/playlist."""
    return payload.decode("utf-8", errors="replace").strip()


class ElapsedPublisherThread(threading.Thread):
    """Publiziert elapsed sekündlich (MPD-Sync + Interpolation)."""

    def __init__(self, publisher: "MqttPublisher") -> None:
        super().__init__(daemon=True, name="mpdbackend-elapsed")
        self.publisher = publisher

    def run(self) -> None:
        worker = self.publisher.worker
        tick = 0
        next_at = time.monotonic()
        while not worker.stop_flag:
            tick += 1
            if tick % 5 == 0:
                worker.try_resync_elapsed()
            elapsed_status = worker.build_elapsed_status()
            self.publisher.publish_elapsed(elapsed_status, force=True)
            next_at += ELAPSED_INTERVAL
            delay = next_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)


def validate_mqtt_config(env_path: str = DEFAULT_ENV_FILE) -> None:
    """Beendet Start, wenn Pflicht-MQTT-Einstellungen fehlen."""
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
            "Missing required MQTT settings in %s: %s",
            env_path,
            ", ".join(missing),
        )
        sys.exit(1)


def dispatch_player_command(
    publisher: "MqttPublisher", command: PlayerCommand, playlist: str
) -> None:
    """Leitet geparste MQTT-Befehle an MPD weiter."""
    mpd = publisher.worker.mpd

    if command is PlayerCommand.LOADPLAYLIST:
        if not playlist:
            logger.warning("loadplaylist on %s: empty playlist name", TOPIC_CMD_PLAYLIST)
            return
        if not mpd.load_and_play_playlist(playlist):
            logger.warning("Failed to load and play playlist: %s", playlist)
            return
        publisher.set_loaded_playlist(playlist)
        return

    if not mpd.execute_player_action(command.value):
        logger.warning("MPD %s failed", command.value)


def handle_player_command(publisher: "MqttPublisher", payload: bytes) -> None:
    """Verarbeitet mpdbackend/cmd/player."""
    command = parse_mqtt_player_command(payload)
    if command is None:
        logger.warning("Invalid player command on %s", TOPIC_CMD_PLAYER)
        return
    logger.info("MQTT command on %s: %s", TOPIC_CMD_PLAYER, command.value)
    dispatch_player_command(publisher, command, "")


def handle_playlist_command(publisher: "MqttPublisher", payload: bytes) -> None:
    """Verarbeitet mpdbackend/cmd/playlist."""
    playlist = parse_mqtt_playlist_name(payload)
    if not playlist:
        logger.warning("Empty playlist name on %s", TOPIC_CMD_PLAYLIST)
        return
    logger.info("MQTT command on %s: %s", TOPIC_CMD_PLAYLIST, playlist)
    dispatch_player_command(publisher, PlayerCommand.LOADPLAYLIST, playlist)


def handle_volume_command(publisher: "MqttPublisher", payload: bytes) -> None:
    """Verarbeitet mpdbackend/cmd/volume."""
    volume = parse_mqtt_volume_command(payload)
    if volume is None:
        logger.warning("Invalid volume command on %s", TOPIC_CMD_VOLUME)
        return
    logger.info("MQTT command on %s: %s", TOPIC_CMD_VOLUME, volume)
    if not publisher.worker.mpd.set_volume(volume):
        logger.warning("MPD setvol failed: %s", volume)
        return
    publisher.publish_status(publisher.worker.mpd.status_dict())


def _on_mqtt_command(_client, userdata, msg) -> None:
    """Verarbeitet Steuerbefehle auf mpdbackend/cmd/…."""
    if msg.topic == TOPIC_CMD_PLAYER:
        handle_player_command(userdata, msg.payload)
        return
    if msg.topic == TOPIC_CMD_PLAYLIST:
        handle_playlist_command(userdata, msg.payload)
        return
    if msg.topic == TOPIC_CMD_VOLUME:
        handle_volume_command(userdata, msg.payload)
        return
    logger.warning(
        "Ignored MQTT message on %s (only %s, %s, %s)",
        msg.topic,
        TOPIC_CMD_PLAYER,
        TOPIC_CMD_PLAYLIST,
        TOPIC_CMD_VOLUME,
    )


def _on_mqtt_connect(
    client: mqtt_client.Client,
    userdata,
    _flags,
    reason_code: mqtt_client.ReasonCode,
    _properties,
) -> None:
    """Meldet Online-Status nach Verbindung."""
    if reason_code.is_failure:
        return
    client.publish(TOPIC_CONNECTED, "online", retain=True, qos=1)


def create_client(publisher: "MqttPublisher") -> mqtt_client.Client:
    """Erstellt MQTT-Client, verbindet und abonniert Steuer-Topics."""
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.user_data_set(publisher)
    client.on_connect = _on_mqtt_connect
    client.on_message = _on_mqtt_command
    client.will_set(TOPIC_CONNECTED, "offline", retain=True, qos=1)
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.subscribe(
        [
            (TOPIC_CMD_PLAYER, 0),
            (TOPIC_CMD_PLAYLIST, 0),
            (TOPIC_CMD_VOLUME, 0),
        ]
    )
    logger.info(
        "MQTT subscribed: %s, %s, %s (qos=0)",
        TOPIC_CMD_PLAYER,
        TOPIC_CMD_PLAYLIST,
        TOPIC_CMD_VOLUME,
    )
    client.loop_start()
    return client


class MqttPublisher:
    """Publiziert Worker-Daten auf MQTT-Topics."""

    def __init__(self, worker) -> None:
        self.worker = worker
        self.client: mqtt_client.Client | None = None
        self.state_cache = None
        self.current_cache = None
        self.playlists_cache = None
        self.status_cache = None
        self.elapsed_cache: str | None = None
        self.last_cover_hash = ""
        self.loaded_playlist = ""
        self._elapsed_thread: ElapsedPublisherThread | None = None

    def start(self, env_path: str = DEFAULT_ENV_FILE) -> None:
        validate_mqtt_config(env_path)
        self.client = create_client(self)
        self.worker.mqtt = self.client
        self._elapsed_thread = ElapsedPublisherThread(self)
        self._elapsed_thread.start()
        logger.info("MQTT enabled (broker %s:%s)", MQTT_BROKER, MQTT_PORT)
        logger.info(
            "MQTT publish: %s, %s, %s, %s, %s",
            TOPIC_STATE,
            TOPIC_CURRENT,
            TOPIC_PLAYLISTS,
            TOPIC_ELAPSED,
            TOPIC_STATUS,
        )
        logger.info("MQTT availability: %s", TOPIC_CONNECTED)

    def is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected())

    def set_loaded_playlist(self, name: str) -> None:
        """Merkt per MQTT geladene Playlist für current-Topic."""
        self.loaded_playlist = name
        self.current_cache = None

    def publish_status(self, status: dict) -> None:
        """Publiziert mpdbackend/status bei Änderung."""
        if not self.client:
            return

        payload = build_mpd_status_data(status)
        if payload == self.status_cache:
            return

        self.status_cache = payload
        self.client.publish(TOPIC_STATUS, json.dumps(payload), qos=0, retain=True)

    def publish_playlists(self) -> None:
        """Publiziert mpdbackend/playlists bei Änderung."""
        if not self.client:
            return

        payload = self.worker.build_playlists_data()
        if payload == self.playlists_cache:
            return

        self.playlists_cache = payload
        self.client.publish(TOPIC_PLAYLISTS, json.dumps(payload), retain=True)

    def publish_elapsed(self, status: dict, *, force: bool = False) -> None:
        if not self.client:
            return

        elapsed_seconds = max(0.0, float(status.get("elapsed", 0)))
        elapsed_text = format_playback_time(elapsed_seconds)
        if not force and elapsed_text == self.elapsed_cache:
            return

        self.elapsed_cache = elapsed_text
        self.client.publish(TOPIC_ELAPSED, elapsed_text, qos=0, retain=True)

    def publish_current_cover(self, song_file: str | None, music_root: str) -> None:
        if not self.client or not song_file:
            return

        img: bytes | None = None
        cover_path = self.worker.cover.path()
        if self.worker.cover.cover_name() and os.path.isfile(cover_path):
            try:
                with open(cover_path, "rb") as handle:
                    img = handle.read()
            except OSError:
                img = None

        if not img:
            full_path = os.path.join(music_root, song_file)
            if not os.path.isfile(full_path):
                return
            raw = self.worker.cover.ffmpeg_extract(full_path)
            if not raw:
                return
            img = self.worker.cover.process(raw)
            if not img:
                return

        cover_hash = hashlib.md5(img).hexdigest()
        if cover_hash == self.last_cover_hash:
            return

        self.last_cover_hash = cover_hash
        self.client.publish(TOPIC_COVER, img, retain=True, qos=1)

    def publish(
        self, song: dict, status: dict, state_payload: dict, music_root: str
    ) -> None:
        """Publiziert state, current, status und Cover bei Änderungen."""
        if not self.client:
            return

        mqtt_state = self.worker.build_track_state_data(song, status)
        if mqtt_state != self.state_cache:
            self.state_cache = mqtt_state
            self.client.publish(TOPIC_STATE, json.dumps(mqtt_state), retain=True)

        current_payload = self.worker.build_queue_state_data(
            song, status, self.loaded_playlist
        )
        if current_payload != self.current_cache:
            self.current_cache = current_payload
            self.client.publish(TOPIC_CURRENT, json.dumps(current_payload), retain=True)

        self.publish_playlists()
        self.publish_status(status)
        self.publish_current_cover(song.get("file"), music_root)
