import mpdbackend as backend
from marked_file import append_marked_line, clear_marked_file, read_marked_lines


def test_append_marked_line_skips_duplicate_anywhere(tmp_path):
    cfg = tmp_path / "mark_for_delete.cfg"
    cfg.write_text("Artist/A/1.flac\nArtist/B/2.flac\n", encoding="utf-8")

    added = append_marked_line(str(cfg), "Artist/A/1.flac")

    assert added is False
    assert read_marked_lines(str(cfg)) == ["Artist/A/1.flac", "Artist/B/2.flac"]


def test_save_current_track_file_skips_non_consecutive_duplicate(tmp_path):
    cfg = tmp_path / "mark_for_delete.cfg"
    cfg.write_text("Artist/A/1.flac\nArtist/B/2.flac\n", encoding="utf-8")
    song = {"file": "Artist/A/1.flac"}

    written = backend.save_current_track_file(song, str(cfg))

    assert written == "Artist/A/1.flac"
    assert cfg.read_text(encoding="utf-8") == "Artist/A/1.flac\nArtist/B/2.flac\n"


def test_marked_file_lock_removed_after_read(tmp_path):
    cfg = tmp_path / "mark_for_delete.cfg"
    lock = tmp_path / "mark_for_delete.cfg.lock"
    cfg.write_text("Artist/A/1.flac\n", encoding="utf-8")

    assert read_marked_lines(str(cfg)) == ["Artist/A/1.flac"]
    assert not lock.exists()


def test_marked_file_lock_removed_after_append(tmp_path):
    cfg = tmp_path / "mark_for_delete.cfg"
    lock = tmp_path / "mark_for_delete.cfg.lock"

    append_marked_line(str(cfg), "Artist/A/1.flac")

    assert not lock.exists()


def test_marked_file_lock_removed_after_clear(tmp_path):
    cfg = tmp_path / "mark_for_delete.cfg"
    lock = tmp_path / "mark_for_delete.cfg.lock"
    cfg.write_text("Artist/A/1.flac\n", encoding="utf-8")

    clear_marked_file(str(cfg))

    assert cfg.read_text(encoding="utf-8") == ""
    assert not lock.exists()
