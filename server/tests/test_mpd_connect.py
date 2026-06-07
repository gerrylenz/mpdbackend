import logging

import mpdbackend as backend


def test_mpd_connect_failure_is_logged(monkeypatch, caplog):
    monkeypatch.setenv("MPDBACKEND_MPD_SOCKET", "/nonexistent/mpd.sock")

    class BrokenClient:
        timeout = 5

        def connect(self, _socket):
            raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(backend, "MPDClient", BrokenClient)

    mpd = backend.MPD()
    with caplog.at_level(logging.WARNING, logger="mpdbackend"):
        ok = mpd.connect()

    assert ok is False
    assert any("MPD connect failed" in record.message for record in caplog.records)
