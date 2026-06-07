import mpdbackend as backend


def test_invalidate_playlists_cache():
    mpd = backend.MPD()
    mpd._playlists_cache = ["Pop.m3u"]
    mpd._playlists_dir_mtime = 123.0

    mpd.invalidate_playlists_cache()

    assert mpd._playlists_cache is None
    assert mpd._playlists_dir_mtime is None
