from typing import NotRequired, TypedDict

from music_assistant_models.enums import ContentType

CONF_BACKEND_URL = "backend_url"

STREAM_METADATA_UPDATE_INTERVAL = 5
CHANNELS_RELOAD_INTERVAL = 60
# Warten bis MPD-HTTP-Stream nach Playlist-Wechsel wieder stabil ist
PLAYLIST_RESUME_DELAY = 2.5

CONTENT_TYPE_FROM_STRING: dict[str, ContentType] = {
    "mp3": ContentType.MP3,
    "aac": ContentType.AAC,
    "ogg": ContentType.OGG,
    "flac": ContentType.FLAC,
    "unknown": ContentType.UNKNOWN,
}


class RadioMpdChannel(TypedDict):
    """Type definition for an MPD radio channel."""

    name: str
    description: str
    stream_url: str
    content_type: ContentType
    backend_url: NotRequired[str]
    logo_mtime: NotRequired[int]
