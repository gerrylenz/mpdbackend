"""Setup flow so Music Assistant asks for the mpdbackend URL."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from music_assistant.models.setup_flow import SetupFlowError

from .constants import CONF_BACKEND_URL, DEFAULT_BACKEND_URL
from .provider import backend_url_config_entries

if TYPE_CHECKING:
    from music_assistant.models.setup_flow import SetupSession


def _normalize_submitted_url(raw: object) -> str | None:
    """Return a usable http(s) URL, or None when the submission cannot be used."""
    if not isinstance(raw, str):
        return None
    url = raw.strip().rstrip("/")
    if not url or url.lower() == "none":
        return None
    if not url.startswith(("http://", "https://")):
        return None
    return url


async def run_setup(session: SetupSession) -> None:
    """Ask for the mpdbackend URL when the provider is added or reconfigured."""
    errors: dict[str, str | SetupFlowError] | None = None
    setup_data = dict(session.context.setup_data)
    existing_values = dict(session.context.values)
    template = backend_url_config_entries()[0]
    while True:
        current = (
            setup_data.get(CONF_BACKEND_URL)
            or existing_values.get(CONF_BACKEND_URL)
            or DEFAULT_BACKEND_URL
        )
        submitted = await session.form(
            [replace(template, value=current)],
            step_id="backend",
            errors=errors,
            last_step=True,
        )
        setup_data.update(submitted)
        url = _normalize_submitted_url(setup_data.get(CONF_BACKEND_URL))
        if url is None:
            errors = {
                CONF_BACKEND_URL: (
                    "Enter the full mpdbackend address, for example "
                    "http://edeka.ex-store.de:4533"
                )
            }
            continue
        setup_data[CONF_BACKEND_URL] = url
        try:
            await session.finish(setup_data)
            return
        except SetupFlowError as err:
            errors = {"base": err}
