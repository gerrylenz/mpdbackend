import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "delete_marked_files.py"
_spec = importlib.util.spec_from_file_location("delete_marked_files", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


def test_load_config_from_tool_env(tmp_path):
    cfg = tmp_path / "delete_marked_files.env"
    cfg.write_text(
        "\n".join(
            [
                "URL=http://example:9999",
                "CHANNEL=2",
                "MUSIC_ROOT=/music",
                "COVER_DIR=/covers",
                "PASSWORD=secret",
                "MPD_UPDATE=true",
            ]
        ),
        encoding="utf-8",
    )

    config, path = _mod.load_config(str(cfg))

    assert path == cfg
    assert config["URL"] == "http://example:9999"
    assert config["CHANNEL"] == "2"
    assert config["MUSIC_ROOT"] == "/music"
    assert _mod.config_bool(config["MPD_UPDATE"]) is True


def test_normalize_config_reads_mpdbackend_env_keys(tmp_path):
    cfg = tmp_path / "mpdbackend.env"
    cfg.write_text(
        "\n".join(
            [
                "MPDBACKEND_PUBLIC_BASE_URL=http://nas:4533",
                "MPDBACKEND_MUSIC_ROOT=/home/musik",
                "MPDBACKEND_COVER_DIR=/data/covers",
                "MPDBACKEND_WEB_PASSWORD=geheim",
            ]
        ),
        encoding="utf-8",
    )

    config, _ = _mod.load_config(str(cfg))

    assert config["URL"] == "http://nas:4533"
    assert config["MUSIC_ROOT"] == "/home/musik"
    assert config["COVER_DIR"] == "/data/covers"
    assert config["PASSWORD"] == "geheim"


def test_build_settings_cli_overrides_config(tmp_path, monkeypatch):
    cfg = tmp_path / "delete_marked_files.env"
    cfg.write_text("URL=http://example:4533\nMUSIC_ROOT=/music\n", encoding="utf-8")

    monkeypatch.setattr(_mod.sys, "argv", ["delete_marked_files.py"])
    args = _mod.parse_args()
    args.config = str(cfg)
    args.url = "http://override:4533"
    args.music_root = None
    args.channel = None
    args.password = None
    args.cover_dir = None
    args.mpd_update = False
    args.keep_list_on_error = False
    args.dry_run = False

    settings = _mod.build_settings(args)

    assert settings.url == "http://override:4533"
    assert settings.music_root == "/music"


def test_load_config_missing_explicit_file(tmp_path):
    missing = tmp_path / "missing.env"

    with pytest.raises(FileNotFoundError):
        _mod.load_config(str(missing))
