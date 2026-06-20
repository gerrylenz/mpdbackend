#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pywebview-Bridge: native Stream-Wiedergabe für die Web-UI."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mpd_player import PlayerApp


class WebPlayerApi:
    """Von app.js über window.pywebview.api aufgerufen."""

    def __init__(self, app: PlayerApp) -> None:
        self._app = app

    def is_native_player(self) -> bool:
        return True

    def start_stream(self, url: str) -> dict[str, Any]:
        try:
            self._app.native_start_stream(str(url or ""))
        except (FileNotFoundError, ValueError, OSError) as err:
            return {"ok": False, "error": str(err)}
        return {"ok": True}

    def stop_stream(self) -> dict[str, Any]:
        self._app.native_stop_stream()
        return {"ok": True}

    def stream_playing(self) -> bool:
        return self._app.native_stream_playing()
