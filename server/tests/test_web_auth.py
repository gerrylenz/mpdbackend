import mpdbackend_http as http


def test_web_auth_disabled_without_password(monkeypatch):
    monkeypatch.setattr(http, "WEB_PASSWORD", "")

    class Req:
        path = "/"
        headers = {}

    assert http.web_auth_enabled() is False
    assert http.evaluate_web_static_access(Req()) == (True, False, None)


def test_web_auth_via_query_password(monkeypatch):
    monkeypatch.setattr(http, "WEB_PASSWORD", "secret")

    class Req:
        path = "/?password=secret"
        headers = {}

    assert http.evaluate_web_static_access(Req()) == (True, True, "/")


def test_web_auth_via_cookie(monkeypatch):
    monkeypatch.setattr(http, "WEB_PASSWORD", "secret")
    token = http.web_auth_token()

    class Req:
        path = "/"
        headers = {"Cookie": f"{http.WEB_AUTH_COOKIE}={token}"}

    assert http.evaluate_web_static_access(Req()) == (True, False, None)


def test_web_auth_denied(monkeypatch):
    monkeypatch.setattr(http, "WEB_PASSWORD", "secret")

    class Req:
        path = "/?password=wrong"
        headers = {}

    assert http.evaluate_web_static_access(Req()) == (False, False, None)


def test_only_static_paths_listed():
    assert http.path_requires_web_auth("/app.js") is True
    assert http.path_requires_web_auth("/playlists") is False
