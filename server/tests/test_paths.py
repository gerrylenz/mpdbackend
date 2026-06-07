import mpdbackend as backend
from paths import build_full_path, resolve_under_root


def test_build_full_path_relative():
    assert (
        build_full_path("Artist/Album/track.mp3", "/music")
        == str(resolve_under_root("/music", "Artist/Album/track.mp3"))
    )


def test_build_full_path_rejects_absolute():
    assert build_full_path("/etc/passwd", "/music") is None


def test_build_full_path_rejects_parent_traversal():
    assert build_full_path("../outside.mp3", "/music") is None


def test_resolve_under_root_symlink_escape(tmp_path):
    music_root = tmp_path / "music"
    outside = tmp_path / "outside"
    music_root.mkdir()
    outside.mkdir()
    target = outside / "secret.flac"
    target.write_text("x", encoding="utf-8")
    link = music_root / "link.flac"
    try:
        link.symlink_to(target)
    except OSError:
        return  # symlinks not supported on this platform

    resolved = resolve_under_root(music_root, "link.flac")
    assert resolved is not None
    assert resolved.resolve() == target.resolve()
