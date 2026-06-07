import threading

import mpdbackend as backend


class FakeCover:
    def __init__(self):
        self.current = "blank.jpg"
        self.generate_calls = 0

    def generate(self, _path):
        self.generate_calls += 1

    def cover_name(self):
        return ""


def test_handle_track_is_thread_safe_for_same_signature(monkeypatch, tmp_path):
    monkeypatch.setenv("MPDBACKEND_COVER_DIR", str(tmp_path / "covers"))
    monkeypatch.setattr(backend, "build_full_path", lambda rel, root: f"/music/{rel}")

    mpd = backend.MPD()
    worker = backend.Worker(mpd)
    worker.cover = FakeCover()

    song = {"file": "Artist/track.mp3", "title": "T"}
    status = {"songid": "1", "state": "play"}
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def run_once():
        barrier.wait()
        results.append(worker.handle_track(song, status))

    threads = [threading.Thread(target=run_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count(True) == 1
    assert results.count(False) == 1
    assert worker.cover.generate_calls == 1
