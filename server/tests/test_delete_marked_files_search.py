import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "delete_marked_files.py"
_spec = importlib.util.spec_from_file_location("delete_marked_files", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


def test_search_root_uses_artist_folder(tmp_path):
    music_root = tmp_path / "music"
    artist = music_root / "Artist"
    artist.mkdir(parents=True)

    search_root = _mod.search_root_for_marked(music_root, "Artist/Album1/Song.mp3")

    assert search_root == artist.resolve()


def test_find_targets_under_parent_finds_sibling_albums(tmp_path):
    music_root = tmp_path / "music"
    album1 = music_root / "Artist" / "Album1" / "Song.mp3"
    album2 = music_root / "Artist" / "Album2" / "Song.mp3"
    other = music_root / "Other" / "Song.mp3"
    album1.parent.mkdir(parents=True)
    album2.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    album1.write_bytes(b"1")
    album2.write_bytes(b"2")
    other.write_bytes(b"3")

    targets, search_root, error = _mod.find_targets_under_parent(
        music_root, "Artist/Album1/Song.mp3"
    )

    assert error is None
    assert search_root == (music_root / "Artist").resolve()
    assert set(targets) == {album1.resolve(), album2.resolve()}


def test_find_targets_under_parent_finds_nested_copies_in_album(tmp_path):
    music_root = tmp_path / "music"
    main = music_root / "Artist" / "Album1" / "Song.mp3"
    nested = music_root / "Artist" / "Album1" / "disc2" / "Song.mp3"
    nested.parent.mkdir(parents=True)
    main.parent.mkdir(parents=True, exist_ok=True)
    main.write_bytes(b"1")
    nested.write_bytes(b"2")

    targets, search_root, error = _mod.find_targets_under_parent(
        music_root, "Artist/Album1/Song.mp3"
    )

    assert error is None
    assert search_root == (music_root / "Artist").resolve()
    assert set(targets) == {main.resolve(), nested.resolve()}


def test_delete_marked_removes_sibling_albums_not_other_artist(tmp_path):
    music_root = tmp_path / "music"
    album1 = music_root / "Artist" / "Album1" / "Song.mp3"
    album2 = music_root / "Artist" / "Album2" / "Song.mp3"
    other = music_root / "Other" / "Song.mp3"
    album1.parent.mkdir(parents=True)
    album2.parent.mkdir(parents=True)
    other.parent.mkdir(parents=True)
    album1.write_bytes(b"1")
    album2.write_bytes(b"2")
    other.write_bytes(b"3")

    deleted, skipped, missing, errors = _mod.delete_marked(
        music_root,
        ["Artist/Album1/Song.mp3"],
        dry_run=False,
        cover_dir=None,
    )

    assert deleted == 2
    assert skipped == 0
    assert missing == 0
    assert errors == []
    assert not album1.exists()
    assert not album2.exists()
    assert other.exists()


def test_delete_marked_missing_when_artist_has_no_match(tmp_path):
    music_root = tmp_path / "music"
    artist = music_root / "Artist" / "Album1"
    artist.mkdir(parents=True)

    deleted, skipped, missing, errors = _mod.delete_marked(
        music_root,
        ["Artist/Album1/Missing.mp3"],
        dry_run=False,
        cover_dir=None,
    )

    assert deleted == 0
    assert missing == 1
    assert errors == []
