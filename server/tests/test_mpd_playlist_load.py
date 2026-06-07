"""Tests for MPD playlist loading."""

from __future__ import annotations

from unittest.mock import MagicMock

import mpdbackend as backend


def test_load_and_play_playlist_uses_command_list(monkeypatch):
    mpd = backend.MPD()
    client = MagicMock()
    calls: list[str] = []

    client.command_list_ok_begin.side_effect = lambda: calls.append("begin")
    client.clear.side_effect = lambda: calls.append("clear")
    client.load.side_effect = lambda name: calls.append(f"load:{name}")
    client.play.side_effect = lambda: calls.append("play")
    client.command_list_end.side_effect = lambda: calls.append("end")

    def fake_run(action):
        action(client)
        return True

    monkeypatch.setattr(mpd, "run_command", fake_run)

    assert mpd.load_and_play_playlist("Pop.m3u") is True
    assert calls == ["begin", "clear", "load:Pop", "play", "end"]
