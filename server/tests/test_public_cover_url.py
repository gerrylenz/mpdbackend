import mpdbackend as backend


def test_public_cover_url_with_base(monkeypatch):
    monkeypatch.setattr(backend, "PUBLIC_BASE_URL", "https://mpd.example.com:4533")
    url = backend.public_cover_url("cover_0123456789abcdef.jpg")
    assert url == "https://mpd.example.com:4533/cover?name=cover_0123456789abcdef.jpg"


def test_public_cover_url_without_base(monkeypatch):
    monkeypatch.setattr(backend, "PUBLIC_BASE_URL", "")
    assert backend.public_cover_url("cover_0123456789abcdef.jpg") is None


def test_public_cover_url_invalid_name(monkeypatch):
    monkeypatch.setattr(backend, "PUBLIC_BASE_URL", "https://mpd.example.com")
    assert backend.public_cover_url("../evil.jpg") is None
