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
