from pathlib import Path

from local_config import default_client_secret_path, load_config, resolve_username, save_config


def test_load_config_returns_empty_dict_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_config() == {}


def test_save_and_load_config_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_config({"infomentor_username": "alice"})
    assert load_config() == {"infomentor_username": "alice"}
    # Updates merge rather than overwrite unrelated keys.
    save_config({"other_key": "value"})
    assert load_config() == {"infomentor_username": "alice", "other_key": "value"}


def test_resolve_username_prefers_cli_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_config({"infomentor_username": "from-config"})
    assert resolve_username("from-cli") == "from-cli"


def test_resolve_username_falls_back_to_saved_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    save_config({"infomentor_username": "from-config"})
    assert resolve_username(None) == "from-config"


def test_resolve_username_returns_none_when_nothing_saved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_username(None) is None


def test_default_client_secret_path_prefers_cli_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    explicit = tmp_path / "somewhere" / "creds.json"
    assert default_client_secret_path(explicit) == explicit


def test_default_client_secret_path_uses_local_file_when_present(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "client_secret.json").write_text("{}", encoding="utf-8")
    assert default_client_secret_path(None) == Path("client_secret.json")


def test_default_client_secret_path_none_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert default_client_secret_path(None) is None
