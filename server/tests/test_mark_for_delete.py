import mpdbackend as backend


def test_load_marked_for_delete_entries(tmp_path):
    cfg = tmp_path / "mark_for_delete.cfg"
    cfg.write_text("Artist/A/1.flac\n\nArtist/B/2.mp3\n", encoding="utf-8")

    entries = backend.load_marked_for_delete_entries(str(cfg))

    assert entries == ["Artist/A/1.flac", "Artist/B/2.mp3"]


def test_load_marked_for_delete_missing_file(tmp_path):
    assert backend.load_marked_for_delete_entries(str(tmp_path / "missing.cfg")) == []


def test_clear_marked_for_delete_file(tmp_path):
    cfg = tmp_path / "mark_for_delete.cfg"
    cfg.write_text("a.flac\nb.flac\n", encoding="utf-8")

    path = backend.clear_marked_for_delete_file(str(cfg))

    assert path == str(cfg.resolve())
    assert cfg.read_text(encoding="utf-8") == ""
    assert backend.load_marked_for_delete_entries(str(cfg)) == []
