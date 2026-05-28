"""Tests for mpdbackend radio parsers."""

from __future__ import annotations

from music_assistant_models.enums import ContentType, ImageType

from mpdbackend.constants import RadioMpdChannel
from mpdbackend.parsers import parse_radio


def test_parse_radio_builds_station_with_logo_url() -> None:
    """parse_radio should expose channel metadata and a station logo image."""
    channel: RadioMpdChannel = {
        "name": "EDEKA - Pos",
        "description": "Store radio",
        "stream_url": "https://example.com/stream.mp3",
        "content_type": ContentType.MP3,
    }

    radio = parse_radio(
        channel_id="0",
        channel_info=channel,
        instance_id="mpdbackend_test",
        provider_domain="mpdbackend",
        backend_url="http://backend:4533",
    )

    assert radio.item_id == "0"
    assert radio.name == "EDEKA - Pos"
    assert radio.metadata.description == "Store radio"
    assert len(radio.metadata.images) == 1
    image = radio.metadata.images[0]
    assert image.type == ImageType.THUMB
    assert image.path == "http://backend:4533/stationlogo?channel=0"


def test_parse_radio_logo_url_includes_mtime_cache_buster() -> None:
    """logo_mtime should be appended so Music Assistant refetches updated logos."""
    channel: RadioMpdChannel = {
        "name": "EDEKA - Pos",
        "description": "Store radio",
        "stream_url": "https://example.com/stream.mp3",
        "content_type": ContentType.MP3,
        "logo_mtime": 1716900000,
    }

    radio = parse_radio(
        channel_id="0",
        channel_info=channel,
        instance_id="mpdbackend_test",
        provider_domain="mpdbackend",
        backend_url="http://backend:4533",
    )

    assert radio.metadata.images[0].path == (
        "http://backend:4533/stationlogo?channel=0&v=1716900000"
    )
