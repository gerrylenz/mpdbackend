import mpdbackend as backend


def test_public_cover_url_with_base(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_PUBLIC_BASE_URL", "https://mpd.example.com:4533")
    url = backend.public_cover_url("cover_0123456789abcdef.jpg")
    assert url == "https://mpd.example.com:4533/cover?name=cover_0123456789abcdef.jpg"


def test_public_cover_url_without_base(monkeypatch):
    monkeypatch.delenv("MPDBACKEND_PUBLIC_BASE_URL", raising=False)
    assert backend.public_cover_url("cover_0123456789abcdef.jpg") is None


def test_public_cover_url_invalid_name(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_PUBLIC_BASE_URL", "https://mpd.example.com")
    assert backend.public_cover_url("../evil.jpg") is None


def test_get_public_base_url_reads_env_at_runtime(monkeypatch):
    monkeypatch.setenv("MPDBACKEND_PUBLIC_BASE_URL", "https://runtime.example")
    assert backend.get_public_base_url() == "https://runtime.example"
