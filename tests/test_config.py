import json

from config import DEFAULTS, load_config


def test_load_config_defaults_when_no_file(tmp_path):
    config = load_config(tmp_path)
    assert config == DEFAULTS


def test_load_config_overrides_known_keys(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"resume_message": "keep going"}))
    config = load_config(tmp_path)
    assert config["resume_message"] == "keep going"
    assert config["enabled"] is True
    assert config["resume_buffer_seconds"] == 45


def test_load_config_ignores_unknown_keys(tmp_path):
    (tmp_path / "config.json").write_text(json.dumps({"unknown_key": "value"}))
    config = load_config(tmp_path)
    assert "unknown_key" not in config


def test_load_config_falls_back_to_defaults_on_malformed_json(tmp_path):
    (tmp_path / "config.json").write_text("{not valid json")
    config = load_config(tmp_path)
    assert config == DEFAULTS
