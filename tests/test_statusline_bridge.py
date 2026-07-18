import json
import subprocess
import sys
from pathlib import Path

from statusline_bridge import extract_rate_limit_snapshot, original_command_for, write_snapshot

BRIDGE_SCRIPT = Path(__file__).parent.parent / "scripts" / "statusline_bridge.py"


def test_extract_rate_limit_snapshot_present():
    payload = {
        "session_id": "sess-1",
        "rate_limits": {"five_hour": {"used_percentage": 100.0, "resets_at": 1780000000}},
    }
    assert extract_rate_limit_snapshot(payload) == {
        "session_id": "sess-1",
        "used_percentage": 100.0,
        "resets_at": 1780000000,
    }


def test_extract_rate_limit_snapshot_absent_when_no_session_id():
    payload = {"rate_limits": {"five_hour": {"used_percentage": 100.0, "resets_at": 1780000000}}}
    assert extract_rate_limit_snapshot(payload) is None


def test_extract_rate_limit_snapshot_absent_when_no_five_hour():
    payload = {"session_id": "sess-1", "rate_limits": {}}
    assert extract_rate_limit_snapshot(payload) is None


def test_write_snapshot_creates_file(tmp_path):
    snapshot = {"session_id": "sess-1", "used_percentage": 50.0, "resets_at": 123}
    write_snapshot(tmp_path, snapshot)
    written = json.loads((tmp_path / "statusline" / "sess-1.json").read_text())
    assert written == snapshot


def test_original_command_for_missing_file_returns_none(tmp_path):
    assert original_command_for("/some/settings.json", tmp_path / "originals.json") is None


def test_original_command_for_found(tmp_path):
    originals_file = tmp_path / "originals.json"
    originals_file.write_text(json.dumps({"/some/settings.json": "echo hi"}))
    assert original_command_for("/some/settings.json", originals_file) == "echo hi"


def test_main_passthrough_and_snapshot_end_to_end(tmp_path):
    state_dir = tmp_path / "state"
    settings_path = tmp_path / "settings.json"
    originals_file = state_dir / "statusline" / "originals.json"
    originals_file.parent.mkdir(parents=True)
    originals_file.write_text(json.dumps({str(settings_path): "cat"}))

    stdin_payload = json.dumps(
        {
            "session_id": "sess-1",
            "rate_limits": {"five_hour": {"used_percentage": 100.0, "resets_at": 1780000000}},
        }
    )

    result = subprocess.run(
        [sys.executable, str(BRIDGE_SCRIPT), "--settings", str(settings_path)],
        input=stdin_payload,
        capture_output=True,
        text=True,
        env={"HERDR_PLUGIN_STATE_DIR": str(state_dir), "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0
    assert result.stdout == stdin_payload
    snapshot = json.loads((state_dir / "statusline" / "sess-1.json").read_text())
    assert snapshot["session_id"] == "sess-1"
    assert snapshot["resets_at"] == 1780000000
