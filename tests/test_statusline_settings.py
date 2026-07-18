import json

import pytest

from statusline_settings import (
    find_statusline_settings_path,
    is_already_wrapped,
    wrap_statusline,
)


def _write_settings(path, statusline_command=None, extra=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(extra or {})
    if statusline_command is not None:
        data["statusLine"] = {"type": "command", "command": statusline_command}
    path.write_text(json.dumps(data))


def test_project_settings_take_precedence_over_user_settings(tmp_path):
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    _write_settings(cwd / ".claude" / "settings.json", statusline_command="project-cmd")
    _write_settings(home / ".claude" / "settings.json", statusline_command="user-cmd")

    result = find_statusline_settings_path(cwd, home=home)
    assert result == cwd / ".claude" / "settings.json"


def test_falls_back_to_user_settings(tmp_path):
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    cwd.mkdir()
    _write_settings(home / ".claude" / "settings.json", statusline_command="user-cmd")

    result = find_statusline_settings_path(cwd, home=home)
    assert result == home / ".claude" / "settings.json"


def test_returns_none_when_neither_has_statusline(tmp_path):
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    cwd.mkdir()
    home.mkdir()

    assert find_statusline_settings_path(cwd, home=home) is None


def test_malformed_project_settings_falls_through_to_user_settings(tmp_path):
    cwd = tmp_path / "project"
    home = tmp_path / "home"
    settings_path = cwd / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not valid json")
    _write_settings(home / ".claude" / "settings.json", statusline_command="user-cmd")

    result = find_statusline_settings_path(cwd, home=home)
    assert result == home / ".claude" / "settings.json"


def test_is_already_wrapped_false_before_wrapping(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, statusline_command="original-cmd")
    assert is_already_wrapped(settings_path) is False


def test_wrap_statusline_raises_when_no_statusline(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path)
    with pytest.raises(ValueError):
        wrap_statusline(settings_path, tmp_path / "bridge.py", tmp_path / "originals.json")


def test_wrap_statusline_records_original_and_rewrites_command(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, statusline_command="original-cmd", extra={"otherKey": "kept"})
    originals_file = tmp_path / "originals.json"
    bridge_script = tmp_path / "statusline_bridge.py"

    changed = wrap_statusline(settings_path, bridge_script, originals_file)

    assert changed is True
    new_data = json.loads(settings_path.read_text())
    assert "statusline_bridge.py" in new_data["statusLine"]["command"]
    assert new_data["otherKey"] == "kept"
    originals = json.loads(originals_file.read_text())
    assert originals[str(settings_path)] == "original-cmd"
    assert is_already_wrapped(settings_path) is True


def test_wrap_statusline_is_idempotent(tmp_path):
    settings_path = tmp_path / "settings.json"
    _write_settings(settings_path, statusline_command="original-cmd")
    originals_file = tmp_path / "originals.json"
    bridge_script = tmp_path / "statusline_bridge.py"

    wrap_statusline(settings_path, bridge_script, originals_file)
    command_after_first_wrap = json.loads(settings_path.read_text())["statusLine"]["command"]

    changed_second_time = wrap_statusline(settings_path, bridge_script, originals_file)

    assert changed_second_time is False
    command_after_second_wrap = json.loads(settings_path.read_text())["statusLine"]["command"]
    assert command_after_first_wrap == command_after_second_wrap
