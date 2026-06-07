from env_util import env_bool, load_env_file, parse_env_file


def test_env_bool_truthy():
    import os

    os.environ["TEST_FLAG"] = "yes"
    assert env_bool("TEST_FLAG") is True
    os.environ["TEST_FLAG"] = "off"
    assert env_bool("TEST_FLAG") is False


def test_parse_env_file(tmp_path):
    env = tmp_path / "test.env"
    env.write_text("# comment\nFOO=bar\nBAZ=qux\n", encoding="utf-8")

    values = parse_env_file(env)

    assert values == {"FOO": "bar", "BAZ": "qux"}


def test_load_env_file_respects_existing(monkeypatch, tmp_path):
    env = tmp_path / "mpdbackend.env"
    env.write_text("MPDBACKEND_HTTP_PORT=4533\n", encoding="utf-8")
    monkeypatch.setenv("MPDBACKEND_HTTP_PORT", "4534")
    monkeypatch.delenv("MPDBACKEND_ENV_FILE", raising=False)

    load_env_file(str(env))

    import os

    assert os.getenv("MPDBACKEND_HTTP_PORT") == "4534"
