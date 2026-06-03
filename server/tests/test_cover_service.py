from io import BytesIO
from pathlib import Path

from PIL import Image

import mpdbackend_cover as cover_mod


def _tiny_jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(40, 80, 120)).save(buf, format="JPEG")
    return buf.getvalue()


def test_cover_dir_from_env(monkeypatch, tmp_path):
    cache = tmp_path / "covers"
    monkeypatch.setenv("MPDBACKEND_COVER_DIR", str(cache))
    assert cover_mod.cover_dir_from_env() == str(cache)


def test_folder_cover_and_cache(tmp_path):
    album = tmp_path / "Artist" / "Album"
    album.mkdir(parents=True)
    track = album / "01 - Song.flac"
    track.write_bytes(b"\x00")
    (album / "cover.jpg").write_bytes(_tiny_jpeg())

    service = cover_mod.CoverService(cover_dir=str(tmp_path / "cache"))
    service.generate(str(track))

    assert service.cover_name().startswith("cover_")
    assert (Path(service.cover_dir) / service.cover_name()).is_file()


def test_generate_missing_file_sets_blank(tmp_path):
    service = cover_mod.CoverService(cover_dir=str(tmp_path / "cache"))
    service.generate(str(tmp_path / "missing.flac"))
    assert service.cover_name() == ""
