def test_manifest_has_required_fields(manifest):
    assert manifest["id"] == "claude-resume"
    assert manifest["name"]
    assert manifest["version"]
    assert manifest["min_herdr_version"]
    assert manifest["platforms"] == ["linux", "macos"]


def test_manifest_declares_expected_event_hooks(manifest):
    events_by_hook = {event["on"]: event["command"] for event in manifest["events"]}
    assert set(events_by_hook) == {"pane.agent_detected", "pane.closed"}


def test_manifest_declares_status_action(manifest):
    action_ids = {action["id"] for action in manifest["actions"]}
    assert "status" in action_ids


def test_agent_detected_hook_script_exists(manifest):
    from pathlib import Path

    root = Path(__file__).parent.parent
    events_by_hook = {event["on"]: event["command"] for event in manifest["events"]}
    script_path = root / events_by_hook["pane.agent_detected"][-1]
    assert script_path.is_file(), f"{script_path} referenced by manifest does not exist"
