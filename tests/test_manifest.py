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


def test_event_hook_commands_point_to_existing_scripts(manifest):
    from pathlib import Path

    root = Path(__file__).parent.parent
    events_by_hook = {event["on"]: event["command"] for event in manifest["events"]}
    for command in events_by_hook.values():
        script_path = root / command[-1]
        assert script_path.is_file(), f"{script_path} referenced by manifest does not exist"


def test_status_action_command_points_to_existing_script(manifest):
    from pathlib import Path

    root = Path(__file__).parent.parent
    action = next(a for a in manifest["actions"] if a["id"] == "status")
    script_path = root / action["command"][-1]
    assert script_path.is_file()
