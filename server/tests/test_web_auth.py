import base64

import mpdbackend_http as http


def test_web_auth_disabled_without_password(monkeypatch):
    monkeypatch.setattr(http, "WEB_PASSWORD", "")
    assert http.web_auth_enabled() is False
    assert http.path_requires_web_auth("/") is True
    assert http.path_requires_web_auth("/nowplaying") is False


def test_web_auth_validates_password_only(monkeypatch):
    monkeypatch.setattr(http, "WEB_PASSWORD", "secret")

    class Req:
        headers = {
            "Authorization": "Basic "
            + base64.b64encode(b":secret").decode("ascii")
        }

    assert http.web_auth_valid(Req()) is True

    class BadReq:
        headers = {
            "Authorization": "Basic "
            + base64.b64encode(b":wrong").decode("ascii")
        }

    assert http.web_auth_valid(BadReq()) is False


def test_only_static_files_protected(monkeypatch):
    monkeypatch.setattr(http, "WEB_PASSWORD", "x")
    assert http.path_requires_web_auth("/app.js") is True
    assert http.path_requires_web_auth("/playlists") is False
    assert http.path_requires_web_auth("/cmd/player") is False
