"""Tests for mpdbackend provider channel parsing."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest
from music_assistant_models.enums import ContentType, PlaybackState

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


def test_channel_has_stalled_queues(provider: MPDBackendRadioProvider) -> None:
    """Stalled queues should be detected for auto-resume."""
    streamdetails = Mock()
    streamdetails.provider = "mpdbackend_test"
    streamdetails.item_id = "0"
    current_item = Mock(streamdetails=streamdetails)
    playing_queue = Mock(
        current_item=current_item,
        state=PlaybackState.PLAYING,
    )
    idle_queue = Mock(
        current_item=current_item,
        state=PlaybackState.IDLE,
    )
    provider.mass.player_queues.all.return_value = [playing_queue, idle_queue]

    assert provider._channel_has_stalled_queues("0") is True
    assert provider._channel_has_stalled_queues("1") is False
