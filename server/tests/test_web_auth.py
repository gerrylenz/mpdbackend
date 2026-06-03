import mpdbackend
import mpdbackend_http as http


def test_web_auth_disabled_without_password(monkeypatch):
    monkeypatch.delenv("MPDBACKEND_WEB_PASSWORD", raising=False)

    class Req:
        path = "/"
        headers = {}

    assert http.web_auth_enabled() is False
    assert http.web_control_granted(Req()) is True


def test_web_auth_disabled_for_false_like_values(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_WEB_PASSWORD", "false")
    assert http.read_web_password() == ""
    assert http.web_auth_enabled() is False


def test_guest_no_control_without_url_password(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_WEB_PASSWORD", "secret")

    class Req:
        path = "/"
        headers = {}

    assert http.web_control_granted(Req()) is False


def test_full_control_with_url_password(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_WEB_PASSWORD", "secret")

    class Req:
        path = "/?password=secret"
        headers = {}

    assert http.web_control_granted(Req()) is True


def test_cookie_does_not_grant_control(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_WEB_PASSWORD", "secret")

    class Req:
        path = "/cmd/player"
        headers = {"Cookie": "mpdbackend_web=anything"}

    assert http.web_control_granted(Req()) is False


def test_web_control_denied_for_wrong_password(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_WEB_PASSWORD", "secret")

    class Req:
        path = "/?password=wrong"
        headers = {}

    assert http.web_control_granted(Req()) is False


def test_only_static_paths_listed():
    assert http.path_requires_web_auth("/app.js") is True
    assert http.path_requires_web_auth("/playlists") is False


def test_load_env_file_overrides_existing_password(tmp_path, monkeypatch):
    env_file = tmp_path / "mpdbackend.env"
    env_file.write_text("MPDBACKEND_WEB_PASSWORD=\n", encoding="utf-8")
    monkeypatch.setenv("MPDBACKEND_WEB_PASSWORD", "geheim")
    monkeypatch.setenv("MPDBACKEND_ENV_FILE", str(env_file))

    mpdbackend.load_env_file()

    assert http.read_web_password() == ""
