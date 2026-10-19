"""Tests for mpdbackend provider channel parsing."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import Mock

import pytest
from music_assistant_models.enums import ContentType, PlaybackState
from music_assistant_models.errors import MediaNotFoundError, ProviderUnavailableError

from mpdbackend.provider import COVER_NAME_RE, COVER_PATH_RE, MPDBackendRadioProvider


@pytest.fixture
def provider() -> MPDBackendRadioProvider:
    """Return a provider instance without full Music Assistant bootstrapping."""
    config = Mock()
    config.instance_id = "mpdbackend_test"
    config.get_value.side_effect = lambda key, default=None: {
        "backend_url": "http://default.example:4533",
        "log_level": "GLOBAL",
    }.get(key, default)
    manifest = Mock()
    manifest.domain = "mpdbackend"
    return MPDBackendRadioProvider(
        mass=Mock(),
        manifest=manifest,
        config=config,
    )


def test_parse_channels_payload_skips_invalid_entries(provider: MPDBackendRadioProvider) -> None:
    """Invalid channel entries should be ignored."""
    payload: dict[str, Any] = {
        "0": {
            "name": "Valid",
            "description": "OK",
            "stream_url": "https://example.com/a.mp3",
            "content_type": "mp3",
            "backend_url": "http://host:4533/",
        },
        "1": "not-a-dict",
        "2": {"name": "", "stream_url": "https://example.com/b.mp3"},
        "3": {"name": "No URL", "stream_url": ""},
    }

    channels = provider._parse_channels_payload(payload)

    assert list(channels) == ["0"]
    assert channels["0"]["name"] == "Valid"
    assert channels["0"]["backend_url"] == "http://host:4533"
    assert channels["0"]["content_type"] == ContentType.MP3


def test_parse_content_type_defaults_to_mp3(provider: MPDBackendRadioProvider) -> None:
    """Unknown content type strings should fall back to MP3."""
    assert provider._parse_content_type("mp3") == ContentType.MP3
    assert provider._parse_content_type("unknown-format") == ContentType.MP3


def test_build_track_key_prefers_songid(provider: MPDBackendRadioProvider) -> None:
    """Track keys should prefer MPD song ids over artist/title."""
    assert provider._build_track_key({"songid": "42", "title": "A", "artist": "B"}) == "id:42"
    assert provider._build_track_key({"title": "Song", "artist": "Artist"}) == "at:artist:song"


@pytest.mark.parametrize(
    ("cover_name", "expected"),
    [
        ("cover_abcdef0123456789abcdef01.jpg", True),
        ("cover_../etc/passwd.jpg", False),
        ("station.jpg", False),
    ],
)
def test_cover_name_regex(cover_name: str, expected: bool) -> None:
    """Cover filenames must match the provider-side safety pattern."""
    assert bool(COVER_NAME_RE.match(cover_name)) is expected


def test_channel_backend_url_uses_channels_json(provider: MPDBackendRadioProvider) -> None:
    """Per-channel backend_url must override the provider default."""
    provider._channels = {
        "0": {
            "name": "A",
            "description": "A",
            "stream_url": "http://stream/a",
            "content_type": ContentType.MP3,
            "backend_url": "http://edeka.example:4533",
        },
        "1": {
            "name": "B",
            "description": "B",
            "stream_url": "http://stream/b",
            "content_type": ContentType.MP3,
            "backend_url": "http://home.example:4534",
        },
    }

    assert (
        provider._channel_backend_url("0", provider._channels)
        == "http://edeka.example:4533"
    )
    assert (
        provider._channel_backend_url("1", provider._channels)
        == "http://home.example:4534"
    )


def test_cover_fetch_url_uses_channel_backend(provider: MPDBackendRadioProvider) -> None:
    """Album covers must be loaded from the channel's mpdbackend, not the MA default."""
    provider._channels = {
        "1": {
            "name": "Home",
            "description": "Home",
            "stream_url": "http://stream/b",
            "content_type": ContentType.MP3,
            "backend_url": "http://home.example:4534",
        },
    }
    cover_name = "cover_abcdef0123456789abcdef01.jpg"

    assert provider._cover_image_path("1", cover_name) == f"cover:1:{cover_name}"
    assert COVER_PATH_RE.match(provider._cover_image_path("1", cover_name))
    assert (
        provider._cover_fetch_url("1", cover_name)
        == f"http://home.example:4534/cover?name={cover_name}"
    )


def test_playlist_changed_detects_mpd_playlist_switch(
    provider: MPDBackendRadioProvider,
) -> None:
    """Playlist changes on MPD should be detected between metadata polls."""
    provider._last_playlists["0"] = "Pop.m3u"
    assert provider._playlist_changed("0", {"playlist": "Rock.m3u"}) is True
    assert provider._last_playlists["0"] == "Rock.m3u"
    assert provider._playlist_changed("0", {"playlist": "Rock.m3u"}) is False


def test_stream_url_with_session_appends_query_param(
    provider: MPDBackendRadioProvider,
) -> None:
    """Stream URLs should get a playback-session cache buster."""
    assert (
        provider._stream_url_with_session("http://stream/a", 3)
        == "http://stream/a?_ps=3"
    )
    assert (
        provider._stream_url_with_session("http://stream/a?x=1", 4)
        == "http://stream/a?x=1&_ps=4"
    )


def test_direct_image_url_from_nowplaying(provider: MPDBackendRadioProvider) -> None:
    """Chromecast should prefer the public cover URL from mpdbackend."""
    assert provider._direct_image_url_from_nowplaying(
        {"media_image_url": "https://host.example/cover?name=x.jpg"}
    ) == "https://host.example/cover?name=x.jpg"
    assert provider._direct_image_url_from_nowplaying({"media_image_url": ""}) is None


def test_image_url_from_nowplaying_prefers_media_image_url(
    provider: MPDBackendRadioProvider,
) -> None:
    """Public media_image_url must bypass the MA image proxy."""
    provider._channels = {
        "0": {
            "name": "A",
            "description": "A",
            "stream_url": "http://stream/a",
            "content_type": ContentType.MP3,
            "backend_url": "http://host:4533",
        },
    }
    nowplaying = {
        "media_image_url": "https://host.example/cover?name=cover_abcd.jpg",
        "cover_name": "cover_abcd0123456789abcdef01.jpg",
    }

    assert (
        provider._image_url_from_nowplaying(nowplaying, "0", provider._channels)
        == "https://host.example/cover?name=cover_abcd.jpg"
    )


def test_image_url_from_nowplaying_falls_back_to_station_logo(
    provider: MPDBackendRadioProvider,
) -> None:
    """Without cover, station logo should still be used."""
    provider._channels = {
        "0": {
            "name": "A",
            "description": "A",
            "stream_url": "http://stream/a",
            "content_type": ContentType.MP3,
            "backend_url": "http://host:4533",
            "logo_mtime": 123,
        },
    }
    provider.mass.metadata.get_image_url.return_value = "http://ma/imageproxy/logo"

    url = provider._image_url_from_nowplaying(
        {"title": "Song"},
        "0",
        provider._channels,
    )

    assert url == "http://ma/imageproxy/logo"
    provider.mass.metadata.get_image_url.assert_called_once()


def test_player_playback_state_reads_playback_state_and_attr(
    provider: MPDBackendRadioProvider,
) -> None:
    """Player state must use playback_state without touching player.state."""
    new_player = Mock()
    new_player.playback_state = PlaybackState.PLAYING
    assert provider._player_playback_state(new_player) == PlaybackState.PLAYING

    attr_player = Mock(spec=[])
    attr_player._attr_playback_state = PlaybackState.PAUSED
    assert provider._player_playback_state(attr_player) == PlaybackState.PAUSED

    broken_state_player = Mock()
    broken_state_player.playback_state = None
    type(broken_state_player).state = property(
        lambda _self: (_ for _ in ()).throw(AttributeError("no state"))
    )
    broken_state_player._attr_playback_state = PlaybackState.IDLE
    assert provider._player_playback_state(broken_state_player) == PlaybackState.IDLE


def test_should_auto_resume_respects_user_stop(provider: MPDBackendRadioProvider) -> None:
    """Inactive idle queues after user stop must not trigger auto-resume."""
    streamdetails = Mock()
    streamdetails.provider = "mpdbackend_test"
    streamdetails.item_id = "0"
    current_item = Mock(streamdetails=streamdetails)
    stopped_queue = Mock(
        current_item=current_item,
        state=PlaybackState.IDLE,
        active=False,
        queue_id="queue0",
    )
    playing_queue = Mock(
        current_item=current_item,
        state=PlaybackState.PLAYING,
        active=True,
        queue_id="queue0",
    )
    idle_active_queue = Mock(
        current_item=current_item,
        state=PlaybackState.IDLE,
        active=True,
        queue_id="queue0",
    )
    provider.mass.player_queues.all.return_value = [stopped_queue]
    provider.mass.players.all_players.return_value = []

    assert provider._should_auto_resume("0") is False
    assert provider._should_auto_resume("1") is False

    provider.mass.player_queues.all.return_value = [idle_active_queue]
    provider.mass.players.all_players.return_value = []
    assert provider._should_auto_resume("0") is False

    playing_player = Mock()
    playing_player.playback_state = PlaybackState.PLAYING
    playing_player.player_id = "player0"
    provider.mass.player_queues.all.return_value = [idle_active_queue]
    provider.mass.players.all_players.return_value = [playing_player]
    provider.mass.players.get_active_queue.return_value = idle_active_queue
    assert provider._should_auto_resume("0") is True

    provider.mass.player_queues.all.return_value = [playing_queue]
    assert provider._should_auto_resume("0") is False

    stopped_queue.active = False
    provider.mass.player_queues.all.return_value = [stopped_queue]
    provider.mass.players.all_players.return_value = [playing_player]
    provider.mass.players.get_active_queue.return_value = stopped_queue
    assert provider._should_auto_resume("0") is True


def test_default_backend_url_ignores_missing_config(provider: MPDBackendRadioProvider) -> None:
    """A missing config value must not become the URL None/channels."""
    provider.config.get_value.side_effect = lambda key, default=None: default

    assert provider.default_backend_url == "http://127.0.0.1:4533"


def test_setup_data_url_overrides_config(provider: MPDBackendRadioProvider) -> None:
    """The address entered in the setup dialog is the one the catalog is loaded from."""
    provider.mass.config.get.return_value = {
        "backend_url": "http://edeka.ex-store.de:4533/"
    }
    provider.mass.config.decrypt_string.side_effect = lambda value: value

    assert provider.default_backend_url == "http://edeka.ex-store.de:4533"


def test_get_channels_waits_after_a_failed_fetch(provider: MPDBackendRadioProvider) -> None:
    """An empty catalog after a failed fetch must not be reloaded on every poll."""
    provider._channels_loaded_at = time.time()
    provider._channels = {}

    async def _unexpected_reload() -> None:
        raise AssertionError("catalog fetch should wait for the reload interval")

    provider._load_channels = _unexpected_reload  # type: ignore[method-assign]

    assert asyncio.run(provider._get_channels()) == {}


def test_channel_id_from_item_accepts_song_suffix() -> None:
    """Now-playing URIs use channel id plus MPD song id and must still resolve."""
    channels = {"0": {}, "home_radio": {}}

    assert MPDBackendRadioProvider._channel_id_from_item("0", channels) == "0"
    assert MPDBackendRadioProvider._channel_id_from_item("0_12656", channels) == "0"
    assert (
        MPDBackendRadioProvider._channel_id_from_item("home_radio_99", channels)
        == "home_radio"
    )
    assert MPDBackendRadioProvider._channel_id_from_item("missing", channels) is None
    assert MPDBackendRadioProvider._channel_id_from_item("9_1", channels) is None


def test_get_radio_resolves_song_suffix(provider: MPDBackendRadioProvider) -> None:
    """A stream-metadata id must return the station, not a missing-item error."""
    provider._channels = {
        "0": {
            "name": "EDEKA",
            "description": "Pos",
            "stream_url": "http://stream/a",
            "content_type": ContentType.MP3,
        }
    }
    provider._channels_loaded_at = time.time()
    provider._channels_load_ok = True

    radio = asyncio.run(provider.get_radio("0_12656"))

    assert radio.item_id == "0"
    assert radio.name == "EDEKA"


def test_get_radio_unknown_channel_is_not_found(provider: MPDBackendRadioProvider) -> None:
    """A loaded catalog with no such channel is a real missing item."""
    provider._channels = {
        "0": {
            "name": "EDEKA",
            "description": "Pos",
            "stream_url": "http://stream/a",
            "content_type": ContentType.MP3,
        }
    }
    provider._channels_loaded_at = time.time()
    provider._channels_load_ok = True

    with pytest.raises(MediaNotFoundError):
        asyncio.run(provider.get_radio("missing"))


def test_get_radio_when_catalog_cannot_be_loaded(
    provider: MPDBackendRadioProvider,
) -> None:
    """A down metadata backend must not be reported as a missing media item."""
    provider.mass.http_session.get.side_effect = OSError("connection refused")

    with pytest.raises(ProviderUnavailableError):
        asyncio.run(provider.get_radio("0"))
