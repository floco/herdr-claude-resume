import os
from pathlib import Path

from on_agent_detected import maybe_spawn_watcher, parse_event, should_watch
from state import is_watcher_running, write_watcher_pidfile


def test_parse_event_valid_json():
    assert parse_event('{"event": "pane.agent_detected", "data": {"agent": "claude"}}') == {
        "event": "pane.agent_detected",
        "data": {"agent": "claude"},
    }


def test_parse_event_invalid_json_returns_empty_dict():
    assert parse_event("not json") == {}


def test_should_watch_true_for_claude():
    # HERDR_PLUGIN_EVENT_JSON is the full EventEnvelope shape herdr actually
    # sends: {"event": ..., "data": {...}} -- the agent field is nested
    # under "data", not top-level. A flat {"agent": "claude"} fixture here
    # previously hid a real bug where should_watch checked the wrong key.
    assert should_watch({"event": "pane.agent_detected", "data": {"agent": "claude", "pane_id": "w1:p1"}}) is True


def test_should_watch_false_for_other_agent():
    assert should_watch({"event": "pane.agent_detected", "data": {"agent": "codex"}}) is False


def test_should_watch_false_for_missing_agent():
    assert should_watch({"event": "pane.agent_detected", "data": {}}) is False


def test_should_watch_false_for_missing_data():
    assert should_watch({"event": "pane.agent_detected"}) is False


def test_maybe_spawn_watcher_spawns_when_not_running(tmp_path):
    calls = []

    class FakeProcess:
        # Use the test process's own real, alive pid -- is_watcher_running
        # does a real os.kill(pid, 0) liveness check, so an arbitrary made-up
        # pid number would make this assertion flaky/false depending on
        # whatever happens to hold that pid on the test machine.
        pid = os.getpid()

    def fake_popen(argv, **kwargs):
        calls.append(argv)
        return FakeProcess()

    spawned = maybe_spawn_watcher(
        "w1:p1", "/repo", tmp_path, watcher_script=Path("/fake/watcher.py"), popen_fn=fake_popen
    )

    assert spawned is True
    assert len(calls) == 1
    assert calls[0] == ["python3", "/fake/watcher.py", "--pane-id", "w1:p1", "--cwd", "/repo"]
    assert is_watcher_running(tmp_path, "w1:p1") is True


def test_maybe_spawn_watcher_skips_when_already_running(tmp_path):
    write_watcher_pidfile(tmp_path, "w1:p1", os.getpid())

    def fail_popen(argv, **kwargs):
        raise AssertionError("popen should not have been called")

    spawned = maybe_spawn_watcher(
        "w1:p1", "/repo", tmp_path, watcher_script=Path("/fake/watcher.py"), popen_fn=fail_popen
    )

    assert spawned is False


def test_maybe_spawn_watcher_real_detached_process(tmp_path):
    stub_script = tmp_path / "stub_watcher.py"
    stub_script.write_text("import time\ntime.sleep(0.5)\n")

    spawned = maybe_spawn_watcher("w1:p1", "/repo", tmp_path, watcher_script=stub_script)

    assert spawned is True
    assert is_watcher_running(tmp_path, "w1:p1") is True

    pidfile = tmp_path / "watchers" / "w1_p1.pid"
    pid = int(pidfile.read_text().splitlines()[0])
    # Reap the child explicitly: the test process is its parent (Popen was
    # not double-forked), so an unreaped exited child becomes a zombie that
    # os.kill(pid, 0) still reports as "alive" -- waitpid blocks until it
    # actually exits and then reaps it, which is what makes the following
    # assertion deterministic instead of a timing guess.
    os.waitpid(pid, 0)

    assert is_watcher_running(tmp_path, "w1:p1") is False
