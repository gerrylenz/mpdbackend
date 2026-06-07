"""Tests for mpdbackend channel registry reload."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_mpdbackend_module():
    module_path = Path(__file__).resolve().parents[1] / "mpdbackend.py"
    spec = importlib.util.spec_from_file_location("mpdbackend_service", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_channel_registry_reloads_changed_file(tmp_path, monkeypatch) -> None:
    """ChannelRegistry should pick up updates to the channels JSON file."""
    module = _load_mpdbackend_module()
    channels_file = tmp_path / "channels.json"
    channels_file.write_text(
        json.dumps({"0": {"name": "A", "stream_url": "http://a", "content_type": "mp3"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MPDBACKEND_CHANNELS_FILE", str(channels_file))

    registry = module.ChannelRegistry()
    assert registry.get()["0"]["name"] == "A"

    channels_file.write_text(
        json.dumps({"0": {"name": "B", "stream_url": "http://b", "content_type": "mp3"}}),
        encoding="utf-8",
    )
    assert registry.get()["0"]["name"] == "B"


def test_channel_registry_clears_on_empty_file(tmp_path, monkeypatch) -> None:
    module = _load_mpdbackend_module()
    channels_file = tmp_path / "channels.json"
    channels_file.write_text(
        json.dumps({"0": {"name": "A", "stream_url": "http://a", "content_type": "mp3"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MPDBACKEND_CHANNELS_FILE", str(channels_file))

    registry = module.ChannelRegistry()
    assert registry.get()["0"]["name"] == "A"

    channels_file.write_text("{}", encoding="utf-8")
    assert registry.get() == {}


def test_channel_registry_keeps_stale_on_parse_error(tmp_path, monkeypatch) -> None:
    module = _load_mpdbackend_module()
    channels_file = tmp_path / "channels.json"
    channels_file.write_text(
        json.dumps({"0": {"name": "A", "stream_url": "http://a", "content_type": "mp3"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MPDBACKEND_CHANNELS_FILE", str(channels_file))

    registry = module.ChannelRegistry()
    assert registry.get()["0"]["name"] == "A"

    channels_file.write_text("{ invalid json", encoding="utf-8")
    assert registry.get()["0"]["name"] == "A"
