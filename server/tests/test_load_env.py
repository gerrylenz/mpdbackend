import os

import mpdbackend as backend


def test_load_env_does_not_override_systemd_port(tmp_path, monkeypatch):
    env_file = tmp_path / "mpdbackend.env"
    env_file.write_text("MPDBACKEND_HTTP_PORT=4533\n", encoding="utf-8")
    monkeypatch.setenv("MPDBACKEND_HTTP_PORT", "4534")
    monkeypatch.delenv("MPDBACKEND_ENV_FILE", raising=False)
    monkeypatch.setattr(backend, "DEFAULT_ENV_FILE", str(env_file))

    backend.load_env_file()

    assert os.getenv("MPDBACKEND_HTTP_PORT") == "4534"


def test_load_env_explicit_file_overrides(tmp_path, monkeypatch):
    env_file = tmp_path / "mpdbackend_1.env"
    env_file.write_text("MPDBACKEND_HTTP_PORT=4534\n", encoding="utf-8")
    monkeypatch.setenv("MPDBACKEND_HTTP_PORT", "4533")
    monkeypatch.setenv("MPDBACKEND_ENV_FILE", str(env_file))

    backend.load_env_file()

    assert os.getenv("MPDBACKEND_HTTP_PORT") == "4534"
