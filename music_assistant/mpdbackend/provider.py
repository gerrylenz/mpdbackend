from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from music_assistant_models.enums import ContentType, ImageType, MediaType, PlaybackState, StreamType
from music_assistant_models.errors import (
    InvalidCommand,
    MediaNotFoundError,
    UnplayableMediaError,
)
from music_assistant_models.media_items import (
    AudioFormat,
    BrowseFolder,
    ItemMapping,
    MediaItemImage,
    MediaItemType,
    Radio,
    SearchResults,
)
from music_assistant_models.streamdetails import StreamDetails, StreamMetadata
from music_assistant.models.music_provider import MusicProvider

from . import parsers
from .parsers import station_logo_url
from .constants import (
    CHANNELS_RELOAD_INTERVAL,
    CONF_BACKEND_URL,
    CONTENT_TYPE_FROM_STRING,
    STREAM_METADATA_UPDATE_INTERVAL,
    PLAYLIST_RESUME_DELAY,
    STREAM_RESUME_COOLDOWN,
    STREAM_RESUME_DELAY,
    STREAM_SYNC_INTERVAL,
    RadioMpdChannel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

COVER_NAME_RE = re.compile(r"^cover_[0-9a-f]{16,64}\.jpg$")
COVER_PATH_RE = re.compile(
    r"^cover:([0-9a-zA-Z_-]{1,32}):(cover_[0-9a-f]{16,64}\.jpg)$"
)
STATION_LOGO_PREFIX = "stationlogo:"


class MPDBackendRadioProvider(MusicProvider):
    """Expose mpdbackend.py radio channels to Music Assistant."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize provider state."""
        super().__init__(*args, **kwargs)
        self._last_track_keys: dict[str, str] = {}
        self._playback_sessions: dict[str, int] = {}
        self._last_seconds_streamed: dict[str, int] = {}
        self._channels: dict[str, RadioMpdChannel] = {}
        self._channels_loaded_at = 0.0
        self._last_playlists: dict[str, str] = {}
        self._last_resume_attempt: dict[str, float] = {}
        self._resume_pending: set[str] = set()
        self._channel_queue_ids: dict[str, set[str]] = {}

    async def loaded_in_mass(self) -> None:
        """Load radio channels from mpdbackend after the provider is loaded."""
        await self._load_channels()
        self.mass.create_task(
            self._stream_sync_loop(),
            task_id=f"{self.instance_id}_stream_sync",
        )

    async def unload(self, is_removed: bool = False) -> None:
        """Called when the provider is unloaded."""

    @property
    def is_streaming_provider(self) -> bool:
        """Return True because the stream lives outside MA."""
        return True

    @property
    def default_backend_url(self) -> str:
        """Default metadata backend URL without trailing slash."""
        return str(self.config.get_value(CONF_BACKEND_URL)).rstrip("/")

    async def _load_channels(self) -> None:
        """Fetch the radio channel list from the configured mpdbackend."""
        url = f"{self.default_backend_url}/channels"
        try:
            async with self.mass.http_session.get(url, ssl=False) as response:
                if response.status != 200:
                    self.logger.warning(
                        "mpdbackend returned HTTP %s for %s", response.status, url
                    )
                    return
                data = await response.json()
                if not isinstance(data, dict):
                    self.logger.warning("mpdbackend /channels returned invalid payload")
                    return
                self._channels = self._parse_channels_payload(data)
                self._channels_loaded_at = time.time()
                self.logger.info("Loaded %s mpdbackend radio channel(s)", len(self._channels))
        except Exception as err:
            self.logger.warning("Could not fetch mpdbackend channels from %s: %s", url, err)

    async def _get_channels(self, *, force_reload: bool = False) -> dict[str, RadioMpdChannel]:
        """Return cached channels, reloading from mpdbackend when needed."""
        cache_expired = (
            self._channels_loaded_at == 0.0
            or time.time() - self._channels_loaded_at >= CHANNELS_RELOAD_INTERVAL
        )
        if force_reload or not self._channels or cache_expired:
            await self._load_channels()
        return self._channels

    def _parse_channels_payload(self, data: dict[str, Any]) -> dict[str, RadioMpdChannel]:
        """Convert a /channels JSON object into typed channel metadata."""
        channels: dict[str, RadioMpdChannel] = {}
        for channel_id, raw in data.items():
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "").strip()
            stream_url = str(raw.get("stream_url") or "").strip()
            if not name or not stream_url:
                continue
            channel: RadioMpdChannel = {
                "name": name,
                "description": str(raw.get("description") or name),
                "stream_url": stream_url,
                "content_type": self._parse_content_type(raw.get("content_type", "mp3")),
            }
            if backend_url := raw.get("backend_url"):
                channel["backend_url"] = str(backend_url).rstrip("/")
            if logo_mtime := raw.get("logo_mtime"):
                try:
                    channel["logo_mtime"] = int(logo_mtime)
                except (TypeError, ValueError):
                    pass
            channels[str(channel_id)] = channel
        return channels

    @staticmethod
    def _parse_content_type(value: str | ContentType) -> ContentType:
        """Map backend content type strings to MA ContentType values."""
        if isinstance(value, ContentType):
            return value
        return CONTENT_TYPE_FROM_STRING.get(str(value).lower(), ContentType.MP3)

    def _channel_backend_url(
        self, channel_id: str, channels: dict[str, RadioMpdChannel]
    ) -> str:
        """Return the metadata backend URL for a channel."""
        channel_info = channels[channel_id]
        return str(channel_info.get("backend_url") or self.default_backend_url).rstrip("/")

    @staticmethod
    def _channel_name(channel_id: str, channels: dict[str, RadioMpdChannel]) -> str:
        """Return the display name for a channel."""
        return channels[channel_id]["name"]

    async def browse(self, path: str) -> Sequence[MediaItemType | ItemMapping | BrowseFolder]:
        """Browse this provider's radio channels."""
        channels = await self._get_channels()
        return [self._parse_radio(channel_id, channels) for channel_id in channels]

    def _parse_radio(self, channel_id: str, channels: dict[str, RadioMpdChannel]) -> Radio:
        """Create a Radio object from cached channel information."""
        return parsers.parse_radio(
            channel_id,
            channels[channel_id],
            self.instance_id,
            self.domain,
            self._channel_backend_url(channel_id, channels),
        )

    def _station_logo_url(self, channel_id: str, channels: dict[str, RadioMpdChannel]) -> str:
        """Return the HTTP URL for a channel station logo."""
        backend_url = self._channel_backend_url(channel_id, channels)
        return station_logo_url(
            backend_url,
            channel_id,
            channels[channel_id].get("logo_mtime"),
        )

    async def search(
        self,
        search_query: str,
        media_types: list[MediaType],
        limit: int = 5,
    ) -> SearchResults:
        """Search configured MPD radio channels by name or description."""
        results = SearchResults()
        if MediaType.RADIO not in media_types:
            return results
        search_query_lower = search_query.lower().strip()
        if not search_query_lower:
            return results
        radios: list[Radio] = []
        channels = await self._get_channels()
        for channel_id, channel_info in channels.items():
            haystack = f"{channel_info['name']} {channel_info['description']}".lower()
            if search_query_lower in haystack:
                radios.append(self._parse_radio(channel_id, channels))
                if len(radios) >= limit:
                    break
        results.radio = radios
        return results

    @staticmethod
    def _cover_image_path(channel_id: str, cover_name: str) -> str:
        """Provider-local image path: routes resolve_image to the channel backend."""
        return f"cover:{channel_id}:{cover_name}"

    def _cover_fetch_url(self, channel_id: str, cover_name: str) -> str:
        """Absolute /cover URL on the mpdbackend that owns this channel."""
        backend = self._channel_backend_url_for_id(channel_id)
        return f"{backend}/cover?name={quote(cover_name)}"

    async def resolve_image(self, path: str) -> bytes:
        """Fetch cover or station logo bytes from mpdbackend."""
        if not path:
            raise FileNotFoundError("Invalid image path")

        if path.startswith(STATION_LOGO_PREFIX):
            channel_id = path.removeprefix(STATION_LOGO_PREFIX)
            channels = await self._get_channels()
            return await self._fetch_station_logo(channel_id, channels)

        if path.startswith(("http://", "https://")) and "/stationlogo" in path:
            return await self._fetch_image(path, "station logo")

        cover_match = COVER_PATH_RE.match(path)
        if cover_match:
            channel_id, cover_name = cover_match.groups()
            return await self._fetch_image(
                self._cover_fetch_url(channel_id, cover_name),
                "cover",
            )

        if COVER_NAME_RE.match(path):
            self.logger.warning(
                "Cover path %s without channel id; using default backend %s",
                path,
                self.default_backend_url,
            )
            url = f"{self.default_backend_url}/cover?name={quote(path)}"
            return await self._fetch_image(url, "cover")

        raise FileNotFoundError(f"Invalid image path: {path}")

    async def _fetch_station_logo(
        self, channel_id: str, channels: dict[str, RadioMpdChannel]
    ) -> bytes:
        """Fetch station logo from the channel's mpdbackend instance."""
        if channel_id not in channels:
            raise FileNotFoundError(f"Unknown radio channel: {channel_id}")
        return await self._fetch_image(self._station_logo_url(channel_id, channels), "station logo")

    async def _fetch_image(self, url: str, label: str) -> bytes:
        """Fetch image bytes from mpdbackend."""
        try:
            async with self.mass.http_session.get(url, ssl=False) as response:
                if response.status != 200:
                    raise FileNotFoundError(
                        f"mpdbackend {label} not found: {url} (HTTP {response.status})"
                    )
                data = await response.read()
                if not data:
                    raise FileNotFoundError(f"mpdbackend {label} empty: {url}")
                return data
        except FileNotFoundError:
            raise
        except Exception as err:
            raise FileNotFoundError(f"Could not fetch {label} from {url}: {err}") from err

    async def get_radio(self, prov_radio_id: str) -> Radio:
        """Get full radio details by id."""
        channels = await self._get_channels()
        if prov_radio_id not in channels:
            raise MediaNotFoundError("Station not found")
        return self._parse_radio(prov_radio_id, channels)

    async def get_library_radios(self):
        """Retrieve all configured library radio channels."""
        channels = await self._get_channels()
        for channel_id in channels:
            yield self._parse_radio(channel_id, channels)

    def _bump_playback_session(self, channel_id: str) -> int:
        """Return a new playback session id for a channel stream (stop/start)."""
        session = self._playback_sessions.get(channel_id, 0) + 1
        self._playback_sessions[channel_id] = session
        return session

    def _append_playback_session(self, image_url: str | None, session: int | None) -> str | None:
        """Append a cache-buster so Chromecast sees metadata as changed after restart."""
        if not image_url or session is None:
            return image_url
        return self._stream_url_with_session(image_url, session)

    @staticmethod
    def _stream_url_with_session(stream_url: str, session: int) -> str:
        """Append a playback-session query param so MA/ffmpeg opens a fresh stream."""
        separator = "&" if "?" in stream_url else "?"
        return f"{stream_url}{separator}_ps={session}"

    def _trigger_players_for_queue(self, queue_id: str, *, force_update: bool = False) -> None:
        """Notify all players currently using the given queue."""
        for player in self.mass.players.all_players(
            return_unavailable=False,
            return_disabled=False,
        ):
            active_queue = self.mass.players.get_active_queue(player)
            if active_queue is None or active_queue.queue_id != queue_id:
                continue
            self.mass.players.trigger_player_update(
                player.player_id,
                force_update=force_update,
            )

    def _stream_restarted(self, channel_id: str, seconds_streamed: int) -> bool:
        """Return True when MA restarted the stream (elapsed time reset)."""
        last_seconds = self._last_seconds_streamed.get(channel_id, -1)
        self._last_seconds_streamed[channel_id] = seconds_streamed
        return seconds_streamed < last_seconds

    async def _push_stream_metadata_to_active_queues(
        self,
        nowplaying: dict[str, Any],
        *,
        force_update: bool = False,
        playback_session: int | None = None,
    ) -> None:
        """Push current metadata and cover into active MA queue items."""
        channels = await self._get_channels()
        for queue in self.mass.player_queues.all():
            current_item = queue.current_item
            streamdetails = current_item.streamdetails if current_item else None

            if not streamdetails:
                continue

            if streamdetails.provider != self.instance_id:
                continue

            if streamdetails.item_id not in channels:
                continue

            stream_metadata = await self._stream_metadata_from_nowplaying(
                nowplaying,
                streamdetails.item_id,
                channels,
                playback_session=playback_session,
            )
            streamdetails.stream_metadata = stream_metadata
            current_item.streamdetails.stream_metadata = stream_metadata
            streamdetails.stream_metadata_last_updated = time.time()

            self.mass.player_queues.signal_update(queue.queue_id)
            self._trigger_players_for_queue(queue.queue_id, force_update=force_update)

    def _channel_backend_url_for_id(self, channel_id: str) -> str:
        """Return backend URL for a channel using cached channel data."""
        if channel_id in self._channels:
            return self._channel_backend_url(channel_id, self._channels)
        return self.default_backend_url

    async def _get_nowplaying(self, channel_id: str) -> dict[str, Any]:
        """Fetch now-playing metadata for a channel from mpdbackend.py."""
        url = f"{self._channel_backend_url_for_id(channel_id)}/nowplaying"
        try:
            async with self.mass.http_session.get(url, ssl=False) as response:
                if response.status != 200:
                    self.logger.warning("mpdbackend returned HTTP %s for %s", response.status, url)
                    return {}
                data = await response.json()
                return data if isinstance(data, dict) else {}
        except Exception as err:
            self.logger.warning("Could not fetch mpdbackend metadata for %s: %s", channel_id, err)
            return {}

    async def _warm_cover_cache(self, channel_id: str, cover_name: str) -> None:
        """Pre-fetch cover via imageproxy so players receive a cached JPEG URL."""
        image_path = self._cover_image_path(channel_id, cover_name)
        try:
            await self.mass.metadata.get_thumbnail(
                image_path,
                provider=self.instance_id,
                size=512,
                image_format="JPEG",
            )
            self.logger.debug("Cover cache warm %s", image_path)
        except Exception as err:
            self.logger.warning("Cover cache warm failed for %s: %s", image_path, err)

    def _cover_name_from_nowplaying(self, nowplaying: dict[str, Any]) -> str:
        """Return provider-local cover cache name from nowplaying payload."""
        cover_name = str(nowplaying.get("cover_name") or "").strip()
        if cover_name and COVER_NAME_RE.match(cover_name):
            self.logger.debug("_cover_name_from_nowplaying cover_name: %s", cover_name)
            return cover_name
        return ""

    def _image_from_nowplaying(
        self, nowplaying: dict[str, Any], channel_id: str
    ) -> MediaItemImage | None:
        """Build MediaItemImage that routes through resolve_image / imageproxy."""
        cover_name = self._cover_name_from_nowplaying(nowplaying)
        if not cover_name:
            return None
        return MediaItemImage(
            type=ImageType.THUMB,
            path=self._cover_image_path(channel_id, cover_name),
            provider=self.instance_id,
            remotely_accessible=False,
        )

    async def _stream_metadata_from_nowplaying(
        self,
        nowplaying: dict[str, Any],
        item_id: str,
        channels: dict[str, RadioMpdChannel],
        *,
        playback_session: int | None = None,
    ) -> StreamMetadata:
        """Create live stream metadata from mpdbackend metadata."""
        title = str(nowplaying.get("title") or self._channel_name(item_id, channels))
        artist = str(nowplaying.get("artist") or "")
        album = str(nowplaying.get("album") or "")

        image_url: str | None = None
        if image := self._image_from_nowplaying(nowplaying, item_id):
            image_url = self.mass.metadata.get_image_url(
                image, size=512, image_format="JPEG"
            )
        elif playback_session is not None:
            station_logo = MediaItemImage(
                type=ImageType.THUMB,
                path=self._station_logo_url(item_id, channels),
                provider=self.instance_id,
                remotely_accessible=False,
            )
            image_url = self.mass.metadata.get_image_url(
                station_logo, size=512, image_format="JPEG"
            )
        if image_url:
            image_url = self._append_playback_session(image_url, playback_session)

        return StreamMetadata(
            title=title,
            artist=artist or None,
            album=album or None,
            image_url=image_url,
            uri=f"{self.instance_id}://radio/{item_id}_{nowplaying.get('songid')}",
        )

    async def _sync_playback_start(self, channel_id: str, nowplaying: dict[str, Any]) -> None:
        """Push fresh metadata to players right after a stream (re)start."""
        cover_name = self._cover_name_from_nowplaying(nowplaying)
        if cover_name:
            await self._warm_cover_cache(channel_id, cover_name)
        playback_session = self._playback_sessions.get(channel_id)
        await self._push_stream_metadata_to_active_queues(
            nowplaying,
            force_update=True,
            playback_session=playback_session,
        )

    async def _on_track_change(self, channel_id: str, nowplaying: dict) -> None:
        self.logger.info(
            "TRACK CHANGE [%s] → %s - %s",
            channel_id,
            nowplaying.get("artist"),
            nowplaying.get("title"),
        )

        cover_name = self._cover_name_from_nowplaying(nowplaying)
        if cover_name:
            await self._warm_cover_cache(channel_id, cover_name)
        else:
            self.logger.info(
                "TRACK CHANGE [%s] no cover in nowplaying (cover_name=%r)",
                channel_id,
                nowplaying.get("cover_name"),
            )

    def _build_track_key(self, nowplaying: dict) -> str:
        songid = nowplaying.get("songid")

        if songid:
            return f"id:{songid}"

        artist = (nowplaying.get("artist") or "").strip().lower()
        title = (nowplaying.get("title") or "").strip().lower()

        return f"at:{artist}:{title}"

    @staticmethod
    def _active_playlist_name(nowplaying: dict[str, Any]) -> str:
        """MPD-Playlist-Name aus /nowplaying (falls gesetzt)."""
        return str(nowplaying.get("playlist") or "").strip()

    def _playlist_changed(self, channel_id: str, nowplaying: dict[str, Any]) -> bool:
        """True wenn sich die aktive MPD-Playlist seit dem letzten Poll geändert hat."""
        current = self._active_playlist_name(nowplaying)
        previous = self._last_playlists.get(channel_id, "")
        self._last_playlists[channel_id] = current
        return bool(current) and current != previous

    def _track_channel_queue(self, channel_id: str, queue_id: str) -> None:
        """Remember queue ids per channel for inactive-queue recovery."""
        self._channel_queue_ids.setdefault(channel_id, set()).add(queue_id)

    def _channel_queue_matches(self, channel_id: str, queue: Any) -> bool:
        """Return True when queue current item belongs to channel_id."""
        current_item = queue.current_item
        streamdetails = current_item.streamdetails if current_item else None
        if not streamdetails:
            return False
        return (
            streamdetails.provider == self.instance_id
            and streamdetails.item_id == channel_id
        )

    def _players_want_channel_playback(self, channel_id: str) -> bool:
        """True when a player is still in PLAYING state for this channel."""
        for player in self.mass.players.all_players(
            return_unavailable=False,
            return_disabled=False,
        ):
            if player.state.state != PlaybackState.PLAYING:
                continue
            active_queue = self.mass.players.get_active_queue(player)
            if active_queue is None or not self._channel_queue_matches(
                channel_id, active_queue
            ):
                continue
            return True
        return False

    def _should_auto_resume(
        self,
        channel_id: str,
        *,
        stream_restarted: bool = False,
    ) -> bool:
        """Only recover when a player still wants playback but the queue stalled."""
        if not self._players_want_channel_playback(channel_id):
            return False

        for queue in self.mass.player_queues.all():
            if not self._channel_queue_matches(channel_id, queue):
                continue
            self._track_channel_queue(channel_id, queue.queue_id)
            if queue.state == PlaybackState.PLAYING and queue.active:
                return False
            return True

        if stream_restarted:
            return True

        for queue_id in self._channel_queue_ids.get(channel_id, ()):
            queue = self.mass.player_queues.get(queue_id)
            if queue is None:
                continue
            if queue.state != PlaybackState.PLAYING or not queue.active:
                return True
        return False

    def _resume_cooldown_ready(self, channel_id: str) -> bool:
        """Limit auto-resume attempts per channel."""
        last_attempt = self._last_resume_attempt.get(channel_id, 0.0)
        return time.time() - last_attempt >= STREAM_RESUME_COOLDOWN

    def _maybe_schedule_stream_resume(
        self,
        channel_id: str,
        *,
        reason: str,
        delay: float | None = None,
    ) -> None:
        """Schedule resume when MPD plays but MA queues for the channel stalled."""
        if channel_id in self._resume_pending:
            return
        if not self._resume_cooldown_ready(channel_id):
            return
        if not self._should_auto_resume(channel_id):
            return
        self._resume_pending.add(channel_id)
        self._schedule_radio_resume(
            channel_id,
            reason=reason,
            delay=STREAM_RESUME_DELAY if delay is None else delay,
        )

    def _schedule_radio_resume(
        self,
        channel_id: str,
        *,
        reason: str,
        delay: float,
    ) -> None:
        """Wait briefly, then resume MA playback if MPD is still playing."""
        task_id = f"{self.instance_id}_radio_resume_{channel_id}"

        async def _resume_after_delay() -> None:
            try:
                await asyncio.sleep(delay)
                nowplaying = await self._get_nowplaying(channel_id)
                if nowplaying.get("state") != "play":
                    return
                if not self._should_auto_resume(channel_id):
                    return
                if not self._resume_cooldown_ready(channel_id):
                    return
                self._last_resume_attempt[channel_id] = time.time()
                self.logger.info(
                    "Auto-resume triggered for channel %s (%s)",
                    channel_id,
                    reason,
                )
                await self._resume_radio_queues(channel_id)
            finally:
                self._resume_pending.discard(channel_id)

        self.mass.create_task(_resume_after_delay(), task_id=task_id)

    async def _stream_sync_loop(self) -> None:
        """Background poll: recover MA playback after HTTP stream drops."""
        while True:
            try:
                await self._sync_stalled_radio_playback()
            except Exception as err:
                self.logger.warning("Stream sync loop error: %s", err)
            await asyncio.sleep(STREAM_SYNC_INTERVAL)

    async def _sync_stalled_radio_playback(self) -> None:
        """Check all provider queues; resume when MPD plays but MA does not."""
        channels = await self._get_channels()
        checked: set[str] = set()
        for queue in self.mass.player_queues.all():
            current_item = queue.current_item
            streamdetails = current_item.streamdetails if current_item else None
            if not streamdetails:
                continue
            if streamdetails.provider != self.instance_id:
                continue
            channel_id = streamdetails.item_id
            if channel_id not in channels or channel_id in checked:
                continue
            self._track_channel_queue(channel_id, queue.queue_id)
            if queue.state == PlaybackState.PLAYING and queue.active:
                continue
            checked.add(channel_id)
            nowplaying = await self._get_nowplaying(channel_id)
            if nowplaying.get("state") != "play":
                continue
            if not self._should_auto_resume(channel_id):
                continue
            self._maybe_schedule_stream_resume(
                channel_id,
                reason="background-sync",
                delay=0,
            )

    async def _hard_resume_queue(self, channel_id: str, queue_id: str) -> None:
        """Restart radio playback when the MA queue became inactive."""
        self._bump_playback_session(channel_id)
        resume_fn = getattr(self.mass.player_queues, "resume", None)
        if callable(resume_fn):
            try:
                await resume_fn(queue_id)
                self.logger.info(
                    "Hard-resumed inactive queue %s (channel %s)",
                    queue_id,
                    channel_id,
                )
                return
            except Exception as err:
                self.logger.debug(
                    "player_queues.resume failed for %s: %s", queue_id, err
                )

        player = self.mass.players.get_player(queue_id)
        if player is None:
            for candidate in self.mass.players.all_players(
                return_unavailable=False,
                return_disabled=False,
            ):
                active_queue = self.mass.players.get_active_queue(candidate)
                if active_queue and active_queue.queue_id == queue_id:
                    player = candidate
                    break

        if player is None:
            self.logger.warning(
                "No player found to hard-resume channel %s (queue %s)",
                channel_id,
                queue_id,
            )
            return

        play_media = getattr(self.mass.players, "play_media", None)
        if not callable(play_media):
            self.logger.warning(
                "play_media unavailable for hard-resume channel %s",
                channel_id,
            )
            return

        radio = await self.get_radio(channel_id)
        await play_media(
            player.player_id,
            radio,
            media_type=MediaType.RADIO,
        )
        self.logger.info(
            "Hard-resumed channel %s on player %s",
            channel_id,
            player.player_id,
        )

    async def _resume_radio_queues(self, channel_id: str) -> None:
        """Setzt MA-Wiedergabe fort, wenn MPD spielt aber der Player gestoppt ist."""
        for queue in self.mass.player_queues.all():
            current_item = queue.current_item
            streamdetails = current_item.streamdetails if current_item else None
            if not streamdetails:
                continue
            if streamdetails.provider != self.instance_id:
                continue
            if streamdetails.item_id != channel_id:
                continue
            if queue.state == PlaybackState.PLAYING:
                continue

            queue_id = queue.queue_id
            self._track_channel_queue(channel_id, queue_id)
            try:
                if queue.state == PlaybackState.PAUSED:
                    player = self.mass.players.get_player(queue_id)
                    if player:
                        await player.play()
                        self.logger.info(
                            "Auto-resumed paused queue %s (channel %s)",
                            queue_id,
                            channel_id,
                        )
                    continue

                if current_item is None:
                    continue
                await self.mass.player_queues.play_index(
                    queue_id,
                    current_item.queue_item_id,
                )
                self.logger.info(
                    "Auto-resumed idle queue %s (channel %s)",
                    queue_id,
                    channel_id,
                )
            except InvalidCommand as err:
                if "not active" in str(err).lower():
                    try:
                        await self._hard_resume_queue(channel_id, queue_id)
                    except Exception as hard_err:
                        self.logger.warning(
                            "Hard-resume queue %s failed: %s",
                            queue_id,
                            hard_err,
                        )
                else:
                    self.logger.warning(
                        "Auto-resume queue %s failed: %s", queue_id, err
                    )
            except (MediaNotFoundError, UnplayableMediaError) as err:
                self.logger.warning(
                    "Auto-resume queue %s skipped (stream not ready): %s",
                    queue_id,
                    err,
                )
            except Exception as err:
                self.logger.warning(
                    "Auto-resume queue %s failed: %s", queue_id, err
                )

    def _schedule_playlist_resume(self, channel_id: str) -> None:
        """Wartet kurz, bis der MPD-Stream nach Playlist-Wechsel wieder läuft."""
        self._schedule_radio_resume(
            channel_id,
            reason="playlist-change",
            delay=PLAYLIST_RESUME_DELAY,
        )

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Get streamdetails for a radio channel."""
        if media_type != MediaType.RADIO:
            raise UnplayableMediaError(f"Unsupported media type: {media_type}")
        channels = await self._get_channels()
        if item_id not in channels:
            raise MediaNotFoundError(f"Unknown radio channel: {item_id}")

        nowplaying = await self._get_nowplaying(item_id)
        channel_info = channels[item_id]
        playback_session = self._bump_playback_session(item_id)
        stream_url = self._stream_url_with_session(
            channel_info["stream_url"],
            playback_session,
        )
        self._last_track_keys[item_id] = self._build_track_key(nowplaying)
        self._last_playlists[item_id] = self._active_playlist_name(nowplaying)

        stream_details = StreamDetails(
            item_id=item_id,
            provider=self.instance_id,
            audio_format=AudioFormat(
                content_type=channel_info["content_type"],
                channels=2,
            ),
            media_type=MediaType.RADIO,
            stream_type=StreamType.HTTP,
            path=stream_url,
            allow_seek=False,
            can_seek=False,
            duration=0,
            stream_metadata=await self._stream_metadata_from_nowplaying(
                nowplaying, item_id, channels, playback_session=playback_session
            ),
            stream_metadata_update_callback=self._update_stream_metadata,
            stream_metadata_update_interval=STREAM_METADATA_UPDATE_INTERVAL,
        )
        stream_details.stream_metadata_last_updated = None
        self.mass.create_task(
            self._sync_playback_start(item_id, nowplaying),
            task_id=f"{self.instance_id}_playback_start_{item_id}",
        )
        return stream_details

    async def _update_stream_metadata(self, streamdetails: StreamDetails, seconds_streamed: int) -> None:
        channel_id = streamdetails.item_id
        channels = await self._get_channels()
        if channel_id not in channels:
            return

        nowplaying = await self._get_nowplaying(channel_id)
        if not nowplaying:
            return

        track_key = self._build_track_key(nowplaying)
        track_changed = track_key != self._last_track_keys.get(channel_id)
        playlist_changed = self._playlist_changed(channel_id, nowplaying)
        stream_restarted = self._stream_restarted(channel_id, seconds_streamed)
        playback_session: int | None = None

        if stream_restarted:
            playback_session = self._bump_playback_session(channel_id)

        if track_changed:
            self._last_track_keys[channel_id] = track_key
            await self._on_track_change(channel_id, nowplaying)
        elif stream_restarted:
            cover_name = self._cover_name_from_nowplaying(nowplaying)
            if cover_name:
                await self._warm_cover_cache(channel_id, cover_name)

        streamdetails.stream_metadata = await self._stream_metadata_from_nowplaying(
            nowplaying,
            channel_id,
            channels,
            playback_session=playback_session,
        )
        streamdetails.stream_metadata_last_updated = time.time()

        # Bei Playlist-Wechsel kein force_update (verhindert Stream-Reload auf Chromecast)
        metadata_force = (track_changed and not playlist_changed) or stream_restarted
        await self._push_stream_metadata_to_active_queues(
            nowplaying,
            force_update=metadata_force,
            playback_session=playback_session,
        )

        if playlist_changed and nowplaying.get("state") == "play":
            self._schedule_playlist_resume(channel_id)
        elif nowplaying.get("state") == "play" and self._should_auto_resume(
            channel_id,
            stream_restarted=stream_restarted,
        ):
            self._maybe_schedule_stream_resume(channel_id, reason="metadata-poll")

