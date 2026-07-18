from datetime import datetime, timezone

import watcher
from socket_client import HerdrRequestError


class FakeSocket:
    def __init__(self, responses=None, subscribe_events=None):
        self.calls = []
        self._responses = responses or {}
        self._subscribe_events = subscribe_events or []

    def request(self, request_id, method, params):
        self.calls.append((method, params))
        value = self._responses.get(method)
        if isinstance(value, Exception):
            raise value
        return value if value is not None else {}

    def subscribe(self, request_id, params):
        for event in self._subscribe_events:
            yield event


def test_pick_resets_at_prefers_statusline_when_limit_hit():
    snapshot = {"used_percentage": 100, "resets_at": 1234567890}
    assert watcher.pick_resets_at(snapshot, banner_epoch=999) == 1234567890


def test_pick_resets_at_falls_back_to_banner_when_statusline_not_maxed():
    snapshot = {"used_percentage": 42, "resets_at": 1234567890}
    assert watcher.pick_resets_at(snapshot, banner_epoch=999) == 999


def test_pick_resets_at_falls_back_to_banner_when_no_snapshot():
    assert watcher.pick_resets_at(None, banner_epoch=999) == 999


def test_pick_resets_at_none_when_nothing_available():
    assert watcher.pick_resets_at(None, None) is None


def test_should_send_resume_true_when_idle():
    assert watcher.should_send_resume(True, "idle") is True


def test_should_send_resume_false_when_already_working():
    assert watcher.should_send_resume(True, "working") is False


def test_should_send_resume_false_when_pane_gone():
    assert watcher.should_send_resume(False, "idle") is False


def test_read_statusline_snapshot_missing_session_id_returns_none(tmp_path):
    assert watcher.read_statusline_snapshot(tmp_path, None) is None


def test_read_statusline_snapshot_missing_file_returns_none(tmp_path):
    assert watcher.read_statusline_snapshot(tmp_path, "sess-1") is None


def test_read_statusline_snapshot_reads_written_file(tmp_path):
    path = tmp_path / "statusline" / "sess-1.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"used_percentage": 100, "resets_at": 42}')
    result = watcher.read_statusline_snapshot(tmp_path, "sess-1")
    assert result == {"used_percentage": 100, "resets_at": 42}


def test_ensure_statusline_bridge_noop_when_no_statusline(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    home = tmp_path / "home"
    state_dir = tmp_path / "state"
    watcher.ensure_statusline_bridge(cwd, state_dir, home=home)
    assert not (state_dir / "statusline").exists()


def test_wait_for_rate_limit_hit_returns_epoch_from_banner(tmp_path):
    now = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    sock = FakeSocket(subscribe_events=[{"data": {"matched_line": "5-hour limit reached - resets 3pm"}}])
    result = watcher.wait_for_rate_limit_hit(sock, "w1:p1", tmp_path, None, "prefix", now_fn=lambda: now)
    assert result == int(now.replace(hour=15, minute=0).timestamp())


def test_wait_for_rate_limit_hit_skips_unrelated_lines(tmp_path):
    now = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    sock = FakeSocket(
        subscribe_events=[
            {"data": {"matched_line": "just some noise"}},
            {"data": {"matched_line": "usage limit reached, resets 4pm"}},
        ]
    )
    result = watcher.wait_for_rate_limit_hit(sock, "w1:p1", tmp_path, None, "prefix", now_fn=lambda: now)
    assert result == int(now.replace(hour=16, minute=0).timestamp())


def test_wait_for_rate_limit_hit_prefers_statusline_snapshot(tmp_path):
    snapshot_path = tmp_path / "statusline" / "sess-1.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text('{"used_percentage": 100, "resets_at": 555}')
    sock = FakeSocket(subscribe_events=[])
    result = watcher.wait_for_rate_limit_hit(sock, "w1:p1", tmp_path, "sess-1", "prefix")
    assert result == 555


def test_sleep_until_returns_true_once_target_reached():
    clock = {"t": 0.0}
    result = watcher.sleep_until(
        100,
        10,
        "w1:p1",
        FakeSocket(),
        "prefix",
        time_fn=lambda: clock["t"],
        sleep_fn=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
    )
    assert result is True
    assert clock["t"] >= 110


def test_sleep_until_returns_false_when_pane_disappears():
    clock = {"t": 0.0}
    sock = FakeSocket(responses={"pane.get": HerdrRequestError("not_found", "gone")})
    result = watcher.sleep_until(
        50,
        5,
        "w1:p1",
        sock,
        "prefix",
        time_fn=lambda: clock["t"],
        sleep_fn=lambda seconds: clock.__setitem__("t", clock["t"] + seconds),
    )
    assert result is False


def test_resume_pane_sends_message_and_notifies_when_idle():
    sock = FakeSocket(responses={"pane.get": {"agent_status": "idle"}})
    watcher.resume_pane(sock, "w1:p1", "continue", True, "prefix")
    methods = [call[0] for call in sock.calls]
    assert "pane.send_text" in methods
    assert "pane.send_keys" in methods
    assert "notification.show" in methods


def test_resume_pane_skips_send_when_already_working():
    sock = FakeSocket(responses={"pane.get": {"agent_status": "working"}})
    watcher.resume_pane(sock, "w1:p1", "continue", True, "prefix")
    methods = [call[0] for call in sock.calls]
    assert "pane.send_text" not in methods
    assert "pane.send_keys" not in methods
    assert "notification.show" in methods


def test_resume_pane_respects_notify_false():
    sock = FakeSocket(responses={"pane.get": {"agent_status": "idle"}})
    watcher.resume_pane(sock, "w1:p1", "continue", False, "prefix")
    methods = [call[0] for call in sock.calls]
    assert "notification.show" not in methods


def test_resume_pane_noop_when_pane_gone():
    sock = FakeSocket(responses={"pane.get": HerdrRequestError("not_found", "gone")})
    watcher.resume_pane(sock, "w1:p1", "continue", True, "prefix")
    assert sock.calls == [("pane.get", {"pane_id": "w1:p1"})]
