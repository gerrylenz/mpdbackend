import mpdbackend as backend


def test_find_playlist_in_available_case_insensitive():
    available = ["Pop.m3u", "Rock.m3u"]
    assert backend.find_playlist_in_available("pop.m3u", available) == "Pop.m3u"
    assert backend.find_playlist_in_available("POP", available) == "Pop.m3u"


def test_resolve_active_playlist_from_lastloadedplaylist():
    status = {"lastloadedplaylist": "Rock"}
    available = ["Pop.m3u", "Rock.m3u"]
    assert (
        backend.resolve_active_playlist_name(status, "", available)
        == "Rock.m3u"
    )


def test_resolve_active_playlist_from_loaded_playlist():
    status = {}
    available = ["Jazz.m3u"]
    assert (
        backend.resolve_active_playlist_name(status, "Jazz", available)
        == "Jazz.m3u"
    )


def test_infer_active_playlist_from_m3u_file(tmp_path, monkeypatch):
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir()
    (playlist_dir / "Work.m3u").write_text(
        "artist/title.flac\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MPDBACKEND_PLAYLIST_DIR", str(playlist_dir))

    class FakeMpd:
        def _playlist_directory(self):
            return str(playlist_dir)

        def normalize_playlist_name(self, name):
            return backend.MPD.normalize_playlist_name(name)

        def safe(self, *_args, **_kwargs):
            return []

    mpd = FakeMpd()
    available = ["Work.m3u"]
    assert (
        backend.infer_active_playlist_name(mpd, "artist/title.flac", available)
        == "Work.m3u"
    )


def test_resolve_active_playlist_infers_from_current_file(tmp_path, monkeypatch):
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir()
    (playlist_dir / "Chill.m3u").write_text(
        "music/song.mp3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MPDBACKEND_PLAYLIST_DIR", str(playlist_dir))

    class FakeMpd:
        def _playlist_directory(self):
            return str(playlist_dir)

        def normalize_playlist_name(self, name):
            return backend.MPD.normalize_playlist_name(name)

        def safe(self, *_args, **_kwargs):
            return []

    mpd = FakeMpd()
    available = ["Chill.m3u", "Other.m3u"]
    assert (
        backend.resolve_active_playlist_name(
            {},
            "",
            available,
            mpd=mpd,
            current_file="music/song.mp3",
        )
        == "Chill.m3u"
    )


def test_resolve_active_playlist_ignores_stale_loaded_name(tmp_path, monkeypatch):
    playlist_dir = tmp_path / "playlists"
    playlist_dir.mkdir()
    (playlist_dir / "Chill.m3u").write_text(
        "music/song.mp3\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MPDBACKEND_PLAYLIST_DIR", str(playlist_dir))

    class FakeMpd:
        def _playlist_directory(self):
            return str(playlist_dir)

        def normalize_playlist_name(self, name):
            return backend.MPD.normalize_playlist_name(name)

        def safe(self, *_args, **_kwargs):
            return []

    mpd = FakeMpd()
    available = ["Chill.m3u"]
    assert (
        backend.resolve_active_playlist_name(
            {},
            "Deleted.m3u",
            available,
            mpd=mpd,
            current_file="music/song.mp3",
        )
        == "Chill.m3u"
    )


def test_infer_active_playlist_from_listplaylist_strings():
    class FakeMpd:
        def _playlist_directory(self):
            return ""

        def normalize_playlist_name(self, name):
            return backend.MPD.normalize_playlist_name(name)

        def safe(self, command, name, default=None):
            if command == "listplaylist" and name == "Work":
                return ["artist/title.flac", "other/track.mp3"]
            return default

    mpd = FakeMpd()
    available = ["Work.m3u"]
    assert (
        backend.infer_active_playlist_name(mpd, "artist/title.flac", available)
        == "Work.m3u"
    )


def test_playlist_entry_file_accepts_dict_and_string():
    assert backend._playlist_entry_file({"file": "a.mp3"}) == "a.mp3"
    assert backend._playlist_entry_file("b.mp3") == "b.mp3"
    assert backend._playlist_entry_file(None) == ""
    assert backend._media_paths_match("music/song.mp3", "music/song.mp3")
    assert backend._media_paths_match("./music/song.mp3", "music/song.mp3")
    assert backend._media_paths_match("song.mp3", "music/artist/song.mp3")
    assert not backend._media_paths_match("other.mp3", "music/song.mp3")
