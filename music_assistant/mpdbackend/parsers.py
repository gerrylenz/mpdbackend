from music_assistant_models.enums import ImageType
from music_assistant_models.media_items import (
    MediaItemImage,
    MediaItemMetadata,
    ProviderMapping,
    Radio,
)

from .constants import RadioMpdChannel


def parse_radio(
    channel_id: str,
    channel_info: RadioMpdChannel,
    instance_id: str,
    provider_domain: str,
    backend_url: str,
) -> Radio:
    """
    Create a Radio object from channel information.

    :param channel_id: MPD radio channel id.
    :param channel_info: Channel metadata from mpdbackend.
    :param instance_id: The provider instance id.
    :param provider_domain: The provider domain string.
    :param backend_url: mpdbackend base URL for this channel.
    """
    channel_name = channel_info["name"]

    radio = Radio(
        provider=instance_id,
        item_id=channel_id,
        name=channel_name,
        metadata=MediaItemMetadata(description=channel_info["description"]),
        provider_mappings={
            ProviderMapping(
                provider_domain=provider_domain,
                provider_instance=instance_id,
                item_id=channel_id,
                available=True,
            )
        },
    )

    logo_url = f"{backend_url.rstrip('/')}/stationlogo?channel={channel_id}"
    radio.metadata.add_image(
        MediaItemImage(
            provider=instance_id,
            type=ImageType.THUMB,
            path=logo_url,
            remotely_accessible=False,
        )
    )

    return radio
