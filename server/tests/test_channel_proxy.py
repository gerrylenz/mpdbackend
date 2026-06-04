"""Tests for multi-channel metadata proxy in mpdbackend_http."""

from unittest.mock import MagicMock

import mpdbackend_http as http


class _FakeRegistry:
    def __init__(self, channels: dict) -> None:
        self._channels = channels

    def get(self) -> dict:
        return self._channels


def _api(channels: dict) -> http.HTTPAPI:
    worker = MagicMock()
    return http.HTTPAPI(worker, _FakeRegistry(channels), mqtt_enabled=False)


def test_channel_backend_url_from_registry():
    api = _api(
        {
            "1": {
                "name": "Home",
                "backend_url": "http://home.example:4533",
            }
        }
    )
    assert api._channel_backend_url("1") == "http://home.example:4533"
    assert api._channel_backend_url("9") is None


def test_proxy_needed_when_backend_differs_from_host(monkeypatch):
    monkeypatch.delenv("MPDBACKEND_PUBLIC_BASE_URL", raising=False)
    api = _api({})
    req = MagicMock()
    req.headers.get.return_value = "edeka.example:4534"

    assert api._proxy_needed("http://home.example:4533", req) is True
    assert api._proxy_needed("http://edeka.example:4534", req) is False


def test_proxy_needed_honors_public_base_url(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_PUBLIC_BASE_URL", "http://edeka.example:4533")
    api = _api({})
    req = MagicMock()
    req.headers.get.return_value = "edeka.example:4534"

    assert api._proxy_needed("http://edeka.example:4533", req) is False


def test_append_auth_to_path_adds_password(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_WEB_PASSWORD", "secret")
    api = _api({})
    req = MagicMock()
    req.path = "/playlists?channel=1&password=secret"

    assert api._append_auth_to_path(req, "/playlists") == "/playlists?password=secret"


def test_append_auth_to_path_without_password():
    api = _api({})
    req = MagicMock()
    req.path = "/playlists?channel=1"

    assert api._append_auth_to_path(req, "/playlists") == "/playlists"


def test_channel_id_from_request():
    api = _api({})
    req = MagicMock()
    req.path = "/cmd/playlist?channel=2&password=secret"

    assert api._channel_id_from_request(req) == "2"
