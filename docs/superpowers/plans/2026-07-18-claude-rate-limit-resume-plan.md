# Claude Code Rate-Limit Auto-Resume Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a herdr plugin that detects when a Claude Code pane hits its 5-hour rate limit and automatically resumes the session (sends a "continue" message) once the window resets.

**Architecture:** A herdr plugin (own git repo, `herdr-plugin.toml` + Python 3 scripts, no build step) hooks `pane.agent_detected` to spawn a detached per-pane watcher. Each watcher prefers exact `resets_at` data from a chained Claude Code `statusLine` script when one already exists, falling back to regex-parsing the rate-limit banner text from pane output via herdr's `pane.output_matched` socket subscription. When the window resets, the watcher sends a configurable resume message into the pane via `pane.send_text`/`pane.send_keys` and shows a notification via `notification.show`.

**Tech Stack:** Python 3 standard library only (no pip dependencies), pytest for tests, herdr's raw Unix-domain-socket JSON-lines protocol (`HERDR_SOCKET_PATH`).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-18-claude-rate-limit-resume-design.md` in this same repo.
- Plugin manifest `id = "claude-resume"`, `min_herdr_version = "0.7.0"`, `platforms = ["linux", "macos"]` (no Windows in v1).
- No third-party Python dependencies — standard library only, matching herdr's own bundled Claude integration script (`python3` is the only external dependency).
- Never create a Claude Code `statusLine` from scratch. Only chain one that already exists. Projects without one always use the screen-scrape fallback.
- Default auto-continue message is `"continue"`, configurable via `HERDR_PLUGIN_CONFIG_DIR/config.json`.
- The `"continuing with usage credits"` banner variant must never arm the resume timer — it means Claude is not actually blocked.
- `statusline_bridge.py` invokes the original statusLine command with
  `subprocess.run(..., shell=True)` deliberately: that command string is the
  user's own pre-existing `statusLine` config, which Claude Code itself
  already executes via a shell, so this is not new attacker-controlled
  input and shell parsing is required for the pipes/quoting users write
  there. Do not "fix" this to `shell=False`/`shlex.split` — it would break
  real-world statusLine commands.
- Every socket request/response field name used below (`pane_id`, `agent_status`, `agent_session.value`, `cwd`, `pane.send_text`/`pane.send_keys`/`notification.show` param shapes, valid `pane.agent_detected`/`pane.closed` manifest event names) was verified against herdr's own Rust source (`src/api/schema/*.rs`) during design — treat them as exact, not illustrative.
- All commands below assume `cd /projects/herdr-claude-resume` first, and a `python3` on `PATH` (3.11+, for the `tomllib` standard library module used in manifest tests).

---

## File Structure

```
herdr-claude-resume/
  herdr-plugin.toml
  config.example.json
  pyproject.toml
  .gitignore
  scripts/
    lib/
      config.py
      reset_time.py
      state.py
      socket_client.py
      statusline_settings.py
    on_agent_detected.py
    on_pane_closed.py
    watcher.py
    statusline_bridge.py
    status.py
  tests/
    conftest.py
    test_manifest.py
    test_config.py
    test_reset_time.py
    test_state.py
    test_socket_client.py
    test_statusline_settings.py
    test_statusline_bridge.py
    test_watcher.py
    test_on_agent_detected.py
    test_on_pane_closed.py
    test_status.py
  README.md
```

---

### Task 1: Plugin scaffold, manifest, and config loader

**Files:**
- Create: `herdr-plugin.toml`
- Create: `config.example.json`
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `scripts/lib/config.py`
- Create: `tests/conftest.py`
- Create: `tests/test_manifest.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `config.DEFAULTS: dict`, `config.load_config(config_dir: pathlib.Path) -> dict` with keys `enabled: bool`, `resume_message: str`, `resume_buffer_seconds: int`, `notify: bool`. Every later task that reads plugin config calls this function.
- Produces: `tests/conftest.py` puts `scripts/` and `scripts/lib/` on `sys.path` and defines a `manifest` pytest fixture returning the parsed `herdr-plugin.toml` as a dict. Every later test file relies on this for imports and manifest inspection.

- [ ] **Step 1: Create the directory skeleton and non-code files**

```bash
mkdir -p scripts/lib tests
```

Create `.gitignore`:

```
__pycache__/
*.pyc
.pytest_cache/
```

Create `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
```

Create `config.example.json`:

```json
{
  "enabled": true,
  "resume_message": "continue",
  "resume_buffer_seconds": 45,
  "notify": true
}
```

- [ ] **Step 2: Write the manifest**

Create `herdr-plugin.toml`:

```toml
id = "claude-resume"
name = "Claude Rate-Limit Auto-Resume"
version = "0.1.0"
min_herdr_version = "0.7.0"
description = "Detects Claude Code's 5-hour rate limit and auto-resumes the session when it resets"
platforms = ["linux", "macos"]

[[events]]
on = "pane.agent_detected"
command = ["python3", "scripts/on_agent_detected.py"]

[[events]]
on = "pane.closed"
command = ["python3", "scripts/on_pane_closed.py"]

[[actions]]
id = "status"
title = "List watched Claude panes"
contexts = ["workspace"]
command = ["python3", "scripts/status.py"]
```

- [ ] **Step 3: Write the failing manifest tests**

Create `tests/conftest.py`:

```python
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))


@pytest.fixture
def manifest():
    manifest_path = ROOT / "herdr-plugin.toml"
    with manifest_path.open("rb") as fh:
        return tomllib.load(fh)
```

Create `tests/test_manifest.py`:

```python
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
```

- [ ] **Step 4: Run the manifest tests to verify they pass**

Run: `python3 -m pytest tests/test_manifest.py -v`
Expected: 3 passed (this is a case where the implementation — the TOML file — was written just before the test, since there's no meaningful "fails first" state for a static config file; the test still guards against future accidental edits).

- [ ] **Step 5: Write the failing config loader test**

Create `tests/test_config.py`:

```python
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
```

- [ ] **Step 6: Run the config tests to verify they fail**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 7: Implement the config loader**

Create `scripts/lib/config.py`:

```python
"""Loads this plugin's user-editable config with sane defaults."""
from __future__ import annotations

import json
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    "resume_message": "continue",
    "resume_buffer_seconds": 45,
    "notify": True,
}


def load_config(config_dir: Path) -> dict:
    config = dict(DEFAULTS)
    path = config_dir / "config.json"
    if path.is_file():
        try:
            overrides = json.loads(path.read_text())
        except json.JSONDecodeError:
            overrides = {}
        for key in DEFAULTS:
            if key in overrides:
                config[key] = overrides[key]
    return config
```

- [ ] **Step 8: Run all tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: 7 passed

- [ ] **Step 9: Commit**

```bash
git add herdr-plugin.toml config.example.json pyproject.toml .gitignore scripts/lib/config.py tests/conftest.py tests/test_manifest.py tests/test_config.py
git commit -m "Scaffold claude-resume plugin with manifest and config loader"
```

---

### Task 2: Rate-limit banner text parser (`reset_time.py`)

**Files:**
- Create: `scripts/lib/reset_time.py`
- Test: `tests/test_reset_time.py`

**Interfaces:**
- Consumes: nothing (pure, standard-library only).
- Produces: `reset_time.parse_reset_epoch(line: str, now: datetime.datetime) -> int | None`. `now` must be timezone-aware. Returns `None` when `line` does not indicate the 5-hour limit was reached. Used by `watcher.py` (Task 7).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reset_time.py`:

```python
from datetime import datetime, timezone

from reset_time import parse_reset_epoch


def _now(hour, minute=0):
    return datetime(2026, 7, 18, hour, minute, tzinfo=timezone.utc)


def test_reached_with_future_time_today():
    now = _now(10)
    result = parse_reset_epoch("5-hour limit reached - resets 3pm", now)
    assert result == int(now.replace(hour=15, minute=0).timestamp())


def test_reached_with_past_time_rolls_to_tomorrow():
    now = _now(16)
    result = parse_reset_epoch("5-hour limit reached - resets 3pm", now)
    expected = now.replace(hour=15, minute=0) + __import__("datetime").timedelta(days=1)
    assert result == int(expected.timestamp())


def test_continuing_with_credits_returns_none():
    now = _now(10)
    line = "5-hour limit resets 3pm - continuing with usage credits"
    assert parse_reset_epoch(line, now) is None


def test_unrelated_line_returns_none():
    assert parse_reset_epoch("hello world", _now(10)) is None


def test_am_time_rolls_to_tomorrow_when_already_past():
    now = _now(10)
    result = parse_reset_epoch("You've hit your session limit · resets 2am", now)
    expected = now.replace(hour=2, minute=0) + __import__("datetime").timedelta(days=1)
    assert result == int(expected.timestamp())


def test_minutes_are_parsed():
    now = _now(10)
    result = parse_reset_epoch("Rate limit hit. Resets at 4:30pm", now)
    assert result == int(now.replace(hour=16, minute=30).timestamp())


def test_no_time_token_uses_five_hour_fallback():
    now = _now(10)
    result = parse_reset_epoch("usage limit reached", now)
    expected = now + __import__("datetime").timedelta(hours=5)
    assert result == int(expected.timestamp())


def test_case_insensitive_matching():
    now = _now(10)
    result = parse_reset_epoch("RATE LIMIT HIT. RESETS AT 4PM", now)
    assert result == int(now.replace(hour=16, minute=0).timestamp())


def test_out_of_range_time_token_uses_fallback():
    now = _now(10)
    result = parse_reset_epoch("usage limit reached, resets 13pm", now)
    expected = now + __import__("datetime").timedelta(hours=5)
    assert result == int(expected.timestamp())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_reset_time.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reset_time'`

- [ ] **Step 3: Implement the parser**

Create `scripts/lib/reset_time.py`:

```python
"""Pure functions for detecting Claude Code's 5-hour rate-limit banner in
terminal output and computing when the window resets.

No I/O, no herdr/socket dependency -- fully unit-testable in isolation.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# Phrases that mean Claude Code is currently blocked by the 5-hour limit.
_BLOCKED_PHRASES = re.compile(
    r"(5-hour limit reached|usage limit reached|session limit|rate limit hit)",
    re.IGNORECASE,
)

# This variant means Claude is NOT blocked -- it's still working, spending
# paid usage credits instead of stopping. Must never arm the resume timer.
_NOT_BLOCKED_PHRASE = re.compile(r"continuing with usage credits", re.IGNORECASE)

# Matches a 12-hour clock time token like "3pm", "4:30pm", "10:15 AM".
_TIME_TOKEN = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b", re.IGNORECASE)

FALLBACK_WAIT = timedelta(hours=5)


def parse_reset_epoch(line: str, now: datetime) -> int | None:
    """Return the Unix epoch (seconds) the 5-hour limit resets at, or None
    if `line` does not indicate the limit was reached.

    `now` must be a timezone-aware datetime representing "the current time"
    in the timezone the reset time token should be interpreted in.
    """
    if _NOT_BLOCKED_PHRASE.search(line):
        return None
    if not _BLOCKED_PHRASES.search(line):
        return None

    match = _TIME_TOKEN.search(line)
    if match is None:
        return int((now + FALLBACK_WAIT).timestamp())

    hour_raw = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3).lower()

    if not (1 <= hour_raw <= 12) or not (0 <= minute <= 59):
        return int((now + FALLBACK_WAIT).timestamp())

    hour = 0 if hour_raw == 12 else hour_raw
    if meridiem == "pm":
        hour += 12

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_reset_time.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/reset_time.py tests/test_reset_time.py
git commit -m "Add rate-limit banner text parser"
```

---

### Task 3: Pidfile helpers (`state.py`)

**Files:**
- Create: `scripts/lib/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `state.watcher_pidfile_path(state_dir, pane_id) -> Path`, `state.is_watcher_running(state_dir, pane_id) -> bool`, `state.write_watcher_pidfile(state_dir, pane_id, pid) -> None`, `state.remove_watcher_pidfile(state_dir, pane_id) -> None`, `state.kill_watcher(state_dir, pane_id) -> bool`, `state.list_watched_pane_ids(state_dir) -> list[str]`. Used by `on_agent_detected.py` (Task 8), `on_pane_closed.py` and `status.py` (Task 9), and `watcher.py` (Task 7).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_state.py`:

```python
import subprocess
import sys
import time

from state import (
    is_watcher_running,
    kill_watcher,
    list_watched_pane_ids,
    remove_watcher_pidfile,
    watcher_pidfile_path,
    write_watcher_pidfile,
)


def test_pidfile_path_sanitizes_pane_id(tmp_path):
    path = watcher_pidfile_path(tmp_path, "w1:p1")
    assert path == tmp_path / "watchers" / "w1_p1.pid"


def test_is_watcher_running_false_when_no_pidfile(tmp_path):
    assert is_watcher_running(tmp_path, "w1:p1") is False


def test_write_then_is_running_true_for_self_pid(tmp_path):
    import os

    write_watcher_pidfile(tmp_path, "w1:p1", os.getpid())
    assert is_watcher_running(tmp_path, "w1:p1") is True


def test_is_watcher_running_false_for_dead_pid(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=5)
    write_watcher_pidfile(tmp_path, "w1:p1", process.pid)
    assert is_watcher_running(tmp_path, "w1:p1") is False


def test_remove_pidfile_is_idempotent(tmp_path):
    remove_watcher_pidfile(tmp_path, "w1:p1")
    write_watcher_pidfile(tmp_path, "w1:p1", 123)
    remove_watcher_pidfile(tmp_path, "w1:p1")
    remove_watcher_pidfile(tmp_path, "w1:p1")
    assert is_watcher_running(tmp_path, "w1:p1") is False


def test_kill_watcher_terminates_and_removes_pidfile(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    write_watcher_pidfile(tmp_path, "w1:p1", process.pid)
    assert kill_watcher(tmp_path, "w1:p1") is True
    process.wait(timeout=5)
    assert process.returncode is not None
    assert not watcher_pidfile_path(tmp_path, "w1:p1").exists()


def test_kill_watcher_false_when_no_pidfile(tmp_path):
    assert kill_watcher(tmp_path, "w1:p1") is False


def test_list_watched_pane_ids_returns_only_alive_ones(tmp_path):
    import os

    dead_process = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_process.wait(timeout=5)

    write_watcher_pidfile(tmp_path, "w1:p1", os.getpid())
    write_watcher_pidfile(tmp_path, "w2:p2", dead_process.pid)

    result = list_watched_pane_ids(tmp_path)
    assert result == ["w1:p1"]


def test_list_watched_pane_ids_empty_when_no_watchers_dir(tmp_path):
    assert list_watched_pane_ids(tmp_path) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'state'`

- [ ] **Step 3: Implement the pidfile helpers**

Create `scripts/lib/state.py`:

```python
"""Pidfile helpers used to dedupe watcher processes per pane and to clean
them up when a pane closes.
"""
from __future__ import annotations

import os
import signal
from pathlib import Path


def watcher_pidfile_path(state_dir: Path, pane_id: str) -> Path:
    safe_pane_id = pane_id.replace("/", "_").replace(":", "_")
    return state_dir / "watchers" / f"{safe_pane_id}.pid"


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pidfile(path: Path) -> int | None:
    try:
        first_line = path.read_text().splitlines()[0]
        return int(first_line)
    except (FileNotFoundError, ValueError, IndexError):
        return None


def is_watcher_running(state_dir: Path, pane_id: str) -> bool:
    pid = _read_pidfile(watcher_pidfile_path(state_dir, pane_id))
    return pid is not None and _pid_is_alive(pid)


def write_watcher_pidfile(state_dir: Path, pane_id: str, pid: int) -> None:
    path = watcher_pidfile_path(state_dir, pane_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n{pane_id}\n")


def remove_watcher_pidfile(state_dir: Path, pane_id: str) -> None:
    watcher_pidfile_path(state_dir, pane_id).unlink(missing_ok=True)


def kill_watcher(state_dir: Path, pane_id: str) -> bool:
    """Best-effort terminate the watcher for `pane_id`. Returns True if a
    live process was found and signaled."""
    path = watcher_pidfile_path(state_dir, pane_id)
    pid = _read_pidfile(path)
    if pid is None:
        return False
    killed = False
    if _pid_is_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            killed = True
        except ProcessLookupError:
            pass
    path.unlink(missing_ok=True)
    return killed


def list_watched_pane_ids(state_dir: Path) -> list[str]:
    """Returns the original pane_id (not the filesystem-sanitized name) for
    every watcher whose process is still alive."""
    watchers_dir = state_dir / "watchers"
    if not watchers_dir.is_dir():
        return []
    pane_ids = []
    for pidfile in sorted(watchers_dir.glob("*.pid")):
        lines = pidfile.read_text().splitlines()
        if len(lines) < 2:
            continue
        try:
            pid = int(lines[0])
        except ValueError:
            continue
        if _pid_is_alive(pid):
            pane_ids.append(lines[1])
    return pane_ids
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_state.py -v`
Expected: 9 passed

Note: `test_kill_watcher_terminates_and_removes_pidfile` sends `SIGTERM` to a
`time.sleep(30)` child and then waits up to 5s for it to exit; this should
take well under a second in practice since `SIGTERM`'s default disposition
kills the interpreter immediately.

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/state.py tests/test_state.py
git commit -m "Add watcher pidfile helpers"
```

---

### Task 4: Raw herdr socket client (`socket_client.py`)

**Files:**
- Create: `scripts/lib/socket_client.py`
- Test: `tests/test_socket_client.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `socket_client.HerdrSocket(socket_path, timeout=5.0)` with methods `.request(request_id, method, params) -> dict` (raises `HerdrRequestError` on an `{"error": ...}` response) and `.subscribe(request_id, params) -> Iterator[dict]` (yields each pushed event dict after the initial ack; raises `HerdrRequestError` if the ack itself is an error), and `.close()`. Also produces `socket_client.HerdrRequestError(code: str, message: str)` with `.code`/`.message` attributes. Used by `watcher.py` (Task 7) and `on_agent_detected.py` (Task 8).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_socket_client.py`:

```python
import json
import socket
import threading

import pytest

from socket_client import HerdrRequestError, HerdrSocket


def _make_server(sock_path):
    # bind()+listen() happen synchronously in the test thread, before the
    # background thread ever starts, so the client can never race ahead of
    # the server being ready to accept(). Racing this (binding inside the
    # background thread instead) causes an intermittent FileNotFoundError
    # on connect(), which aborts the test before thread.join() runs and
    # leaks a thread parked forever in accept() -- since threads are
    # non-daemon by default, that hangs the whole test process at exit.
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(sock_path)
    server.listen(1)
    return server


def _accept_and_handle(server, handle_conn):
    conn, _ = server.accept()
    try:
        handle_conn(conn)
    finally:
        conn.close()
        server.close()


def _start_server_thread(server, handle_conn):
    # daemon=True is a safety net: even if a test aborts before reaching
    # thread.join(), a leftover thread can't block process exit.
    thread = threading.Thread(target=_accept_and_handle, args=(server, handle_conn), daemon=True)
    thread.start()
    return thread


def test_request_returns_result(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        assert request["method"] == "ping"
        assert request["params"] == {}
        response = json.dumps({"id": request["id"], "result": {"type": "pong"}}) + "\n"
        conn.sendall(response.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    result = client.request("req_1", "ping", {})
    assert result == {"type": "pong"}
    client.close()
    thread.join(timeout=2)


def test_request_raises_on_error(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        response = (
            json.dumps({"id": request["id"], "error": {"code": "not_found", "message": "pane not found"}})
            + "\n"
        )
        conn.sendall(response.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    with pytest.raises(HerdrRequestError) as exc_info:
        client.request("req_1", "pane.get", {"pane_id": "w1:p1"})
    assert exc_info.value.code == "not_found"
    client.close()
    thread.join(timeout=2)


def test_subscribe_yields_pushed_events(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        assert request["method"] == "events.subscribe"
        ack = json.dumps({"id": request["id"], "result": {"type": "subscribed"}}) + "\n"
        conn.sendall(ack.encode())
        event = (
            json.dumps(
                {
                    "event": "pane.output_matched",
                    "data": {"matched_line": "5-hour limit reached - resets 3pm"},
                }
            )
            + "\n"
        )
        conn.sendall(event.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    events = client.subscribe("sub_1", {"subscriptions": [{"type": "pane.output_matched"}]})
    first_event = next(events)
    assert first_event["data"]["matched_line"] == "5-hour limit reached - resets 3pm"
    client.close()
    thread.join(timeout=2)


def test_subscribe_raises_on_error_ack(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = _make_server(sock_path)

    def handle(conn):
        data = conn.recv(65536)
        request = json.loads(data.decode().strip())
        response = (
            json.dumps({"id": request["id"], "error": {"code": "invalid_regex", "message": "bad pattern"}})
            + "\n"
        )
        conn.sendall(response.encode())

    thread = _start_server_thread(server, handle)
    client = HerdrSocket(sock_path)
    events = client.subscribe("sub_1", {"subscriptions": [{"type": "pane.output_matched"}]})
    with pytest.raises(HerdrRequestError) as exc_info:
        next(events)
    assert exc_info.value.code == "invalid_regex"
    client.close()
    thread.join(timeout=2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_socket_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'socket_client'`

- [ ] **Step 3: Implement the socket client**

Create `scripts/lib/socket_client.py`:

```python
"""Minimal raw client for herdr's local socket API (newline-delimited JSON
over a Unix domain socket). No dependency beyond the standard library --
mirrors the approach herdr's own bundled Claude integration script uses.
"""
from __future__ import annotations

import json
import socket
from typing import Iterator


class HerdrRequestError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HerdrSocket:
    def __init__(self, socket_path: str, timeout: float = 5.0):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._buffer = b""

    def _read_line(self) -> dict:
        while b"\n" not in self._buffer:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("herdr socket closed")
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    def request(self, request_id: str, method: str, params: dict) -> dict:
        payload = json.dumps({"id": request_id, "method": method, "params": params})
        self._sock.sendall((payload + "\n").encode("utf-8"))
        response = self._read_line()
        if "error" in response:
            error = response["error"]
            raise HerdrRequestError(error.get("code", "unknown"), error.get("message", ""))
        return response["result"]

    def subscribe(self, request_id: str, params: dict) -> Iterator[dict]:
        """Send an events.subscribe request and yield each pushed event
        after the initial ack. This call blocks between yields."""
        payload = json.dumps({"id": request_id, "method": "events.subscribe", "params": params})
        self._sock.sendall((payload + "\n").encode("utf-8"))
        ack = self._read_line()
        if "error" in ack:
            error = ack["error"]
            raise HerdrRequestError(error.get("code", "unknown"), error.get("message", ""))
        while True:
            yield self._read_line()

    def close(self) -> None:
        self._sock.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_socket_client.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/socket_client.py tests/test_socket_client.py
git commit -m "Add raw herdr socket client"
```

---

### Task 5: StatusLine settings finder/wrapper (`statusline_settings.py`)

**Files:**
- Create: `scripts/lib/statusline_settings.py`
- Test: `tests/test_statusline_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `statusline_settings.find_statusline_settings_path(cwd: Path, home: Path | None = None) -> Path | None`, `statusline_settings.is_already_wrapped(settings_path: Path) -> bool`, `statusline_settings.wrap_statusline(settings_path: Path, bridge_script: Path, originals_file: Path) -> bool` (raises `ValueError` if no statusLine command exists to wrap), `statusline_settings.atomic_write(path: Path, content: str) -> None`. Used by `watcher.py` (Task 7) and `statusline_bridge.py` (Task 6, which reuses `atomic_write`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_statusline_settings.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_statusline_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'statusline_settings'`

- [ ] **Step 3: Implement the finder/wrapper**

Create `scripts/lib/statusline_settings.py`:

```python
"""Find and idempotently wrap a Claude Code project's statusLine command so
this plugin can observe rate_limits data without disturbing what the user
already sees. Never creates a statusLine that didn't already exist.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_BRIDGE_MARKER = "statusline_bridge.py"


def _settings_candidates(cwd: Path, home: Path | None) -> list[Path]:
    home = home if home is not None else Path.home()
    return [cwd / ".claude" / "settings.json", home / ".claude" / "settings.json"]


def _read_command(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return (data.get("statusLine") or {}).get("command")


def find_statusline_settings_path(cwd: Path, home: Path | None = None) -> Path | None:
    """Return the first settings.json (project, then user) that has a
    statusLine.command configured, or None if neither does."""
    for path in _settings_candidates(cwd, home):
        if path.is_file() and _read_command(path):
            return path
    return None


def is_already_wrapped(settings_path: Path) -> bool:
    command = _read_command(settings_path) or ""
    return _BRIDGE_MARKER in command


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content)
    os.replace(tmp_path, path)


def wrap_statusline(settings_path: Path, bridge_script: Path, originals_file: Path) -> bool:
    """Rewrite settings_path's statusLine.command to route through
    bridge_script, recording the original command in originals_file keyed by
    the settings path. Idempotent: returns False without changes if already
    wrapped. Raises ValueError if there is no existing statusLine to wrap."""
    data = json.loads(settings_path.read_text())
    status_line = data.get("statusLine") or {}
    original_command = status_line.get("command")
    if not original_command:
        raise ValueError(f"no statusLine command configured in {settings_path}")
    if _BRIDGE_MARKER in original_command:
        return False

    originals: dict[str, str] = {}
    if originals_file.is_file():
        try:
            originals = json.loads(originals_file.read_text())
        except json.JSONDecodeError:
            originals = {}
    originals[str(settings_path)] = original_command
    atomic_write(originals_file, json.dumps(originals, indent=2))

    status_line["command"] = f"python3 {bridge_script} --settings {settings_path}"
    data["statusLine"] = status_line
    atomic_write(settings_path, json.dumps(data, indent=2))
    return True
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_statusline_settings.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/lib/statusline_settings.py tests/test_statusline_settings.py
git commit -m "Add statusLine finder and idempotent wrapper"
```

---

### Task 6: StatusLine bridge script (`statusline_bridge.py`)

**Files:**
- Create: `scripts/statusline_bridge.py`
- Test: `tests/test_statusline_bridge.py`

**Interfaces:**
- Consumes: `statusline_settings.atomic_write` (Task 5).
- Produces: `statusline_bridge.extract_rate_limit_snapshot(payload: dict) -> dict | None` (returns `{"session_id", "used_percentage", "resets_at"}` or `None`), `statusline_bridge.write_snapshot(state_dir: Path, snapshot: dict) -> None`, `statusline_bridge.original_command_for(settings_path: str, originals_file: Path) -> str | None`, `statusline_bridge.main(argv: list[str]) -> int`. `watcher.py` (Task 7) reads the files this script writes under `state_dir / "statusline" / "<session_id>.json"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_statusline_bridge.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_statusline_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'statusline_bridge'`

- [ ] **Step 3: Implement the bridge script**

Create `scripts/statusline_bridge.py`:

```python
#!/usr/bin/env python3
"""Chained Claude Code statusLine command installed by
statusline_settings.wrap_statusline(). Reads the JSON Claude Code sends on
stdin, records rate_limits.five_hour data for the watcher to poll, then
re-invokes the project's original statusLine command with the same stdin
and forwards its output unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from statusline_settings import atomic_write  # noqa: E402


def extract_rate_limit_snapshot(payload: dict) -> dict | None:
    """Pull the fields the watcher needs out of a statusLine JSON payload.
    Returns None if the payload has no session_id or no five_hour data."""
    session_id = payload.get("session_id")
    five_hour = (payload.get("rate_limits") or {}).get("five_hour")
    if not session_id or not five_hour:
        return None
    resets_at = five_hour.get("resets_at")
    used_percentage = five_hour.get("used_percentage")
    if resets_at is None or used_percentage is None:
        return None
    return {"session_id": session_id, "used_percentage": used_percentage, "resets_at": resets_at}


def write_snapshot(state_dir: Path, snapshot: dict) -> None:
    path = state_dir / "statusline" / f"{snapshot['session_id']}.json"
    atomic_write(path, json.dumps(snapshot))


def original_command_for(settings_path: str, originals_file: Path) -> str | None:
    if not originals_file.is_file():
        return None
    try:
        originals = json.loads(originals_file.read_text())
    except json.JSONDecodeError:
        return None
    return originals.get(settings_path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", required=True)
    args = parser.parse_args(argv)

    stdin_bytes = sys.stdin.buffer.read()

    state_dir = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR", "."))
    try:
        payload = json.loads(stdin_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        payload = {}
    snapshot = extract_rate_limit_snapshot(payload)
    if snapshot is not None:
        write_snapshot(state_dir, snapshot)

    originals_file = state_dir / "statusline" / "originals.json"
    original_command = original_command_for(args.settings, originals_file)
    if not original_command:
        return 0

    # shell=True is intentional and safe here: original_command is the
    # user's own pre-existing statusLine command from their own
    # .claude/settings.json, which Claude Code itself already runs via a
    # shell (its docs: "The command field runs in a shell"). It is not
    # attacker-controlled input, and shell parsing is required to support
    # the pipes/quoting/`~`-expansion users commonly write in this field
    # (e.g. `jq -r '...'`). Splitting it with shlex would break those.
    result = subprocess.run(original_command, shell=True, input=stdin_bytes, capture_output=True)
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_statusline_bridge.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/statusline_bridge.py tests/test_statusline_bridge.py
git commit -m "Add statusLine bridge script"
```

---

### Task 7: Per-pane watcher daemon (`watcher.py`)

**Files:**
- Create: `scripts/watcher.py`
- Test: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `config.load_config` (Task 1), `reset_time.parse_reset_epoch` (Task 2), `state.remove_watcher_pidfile` (Task 3), `socket_client.HerdrSocket`/`HerdrRequestError` (Task 4), `statusline_settings.find_statusline_settings_path`/`is_already_wrapped`/`wrap_statusline` (Task 5).
- Produces: `watcher.pick_resets_at(statusline_snapshot: dict | None, banner_epoch: int | None) -> int | None`, `watcher.should_send_resume(pane_exists: bool, agent_status: str | None) -> bool`, `watcher.read_statusline_snapshot(state_dir: Path, session_id: str | None) -> dict | None`, `watcher.ensure_statusline_bridge(cwd: Path, state_dir: Path, home: Path | None = None) -> None`, `watcher.wait_for_rate_limit_hit(sock, pane_id, state_dir, session_id, request_prefix, now_fn=...) -> int`, `watcher.sleep_until(target_epoch, buffer_seconds, pane_id, sock, request_prefix, time_fn=..., sleep_fn=...) -> bool`, `watcher.resume_pane(sock, pane_id, message, notify, request_prefix) -> None`, `watcher.run_cycle(sock, pane_id, cwd, state_dir, config, session_id, cycle_index) -> None`, `watcher.main(argv) -> int`. `on_agent_detected.py` (Task 8) spawns this script as `python3 scripts/watcher.py --pane-id <id> --cwd <cwd>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watcher.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_watcher.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'watcher'`

- [ ] **Step 3: Implement the watcher**

Create `scripts/watcher.py`:

```python
#!/usr/bin/env python3
"""Long-running per-pane watcher: detects the Claude Code 5-hour rate-limit
banner or statusLine data, waits until the window resets, then resumes the
session by sending a configured message into the pane.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from config import load_config  # noqa: E402
from reset_time import parse_reset_epoch  # noqa: E402
from socket_client import HerdrRequestError, HerdrSocket  # noqa: E402
from state import remove_watcher_pidfile  # noqa: E402
from statusline_settings import (  # noqa: E402
    find_statusline_settings_path,
    is_already_wrapped,
    wrap_statusline,
)

BRIDGE_SCRIPT = Path(__file__).parent / "statusline_bridge.py"
POLL_INTERVAL_SECONDS = 60


def pick_resets_at(statusline_snapshot: dict | None, banner_epoch: int | None) -> int | None:
    """Prefer the statusLine snapshot's resets_at (exact) over a
    banner-text-derived epoch (approximate) when both are available."""
    if statusline_snapshot is not None and statusline_snapshot.get("used_percentage", 0) >= 100:
        return int(statusline_snapshot["resets_at"])
    return banner_epoch


def should_send_resume(pane_exists: bool, agent_status: str | None) -> bool:
    """Decide whether it's safe to send the auto-continue message: the pane
    must still exist and must not already be working (the user may have
    resumed manually before the timer fired)."""
    if not pane_exists:
        return False
    return agent_status != "working"


def read_statusline_snapshot(state_dir: Path, session_id: str | None) -> dict | None:
    if not session_id:
        return None
    path = state_dir / "statusline" / f"{session_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def ensure_statusline_bridge(cwd: Path, state_dir: Path, home: Path | None = None) -> None:
    """Best-effort: if this project already has a statusLine configured and
    it isn't already wrapped, wrap it. Never creates one from scratch. Any
    failure here is non-fatal -- the screen-scrape path still works."""
    try:
        settings_path = find_statusline_settings_path(cwd, home=home)
        if settings_path is None:
            return
        if is_already_wrapped(settings_path):
            return
        originals_file = state_dir / "statusline" / "originals.json"
        wrap_statusline(settings_path, BRIDGE_SCRIPT, originals_file)
    except (OSError, ValueError):
        pass


def wait_for_rate_limit_hit(
    sock,
    pane_id: str,
    state_dir: Path,
    session_id: str | None,
    request_prefix: str,
    now_fn=lambda: datetime.now().astimezone(),
) -> int:
    """Blocks until the pane either reports a statusLine snapshot at 100%
    usage or prints a rate-limit banner line. Returns a `resets_at` epoch."""
    subscription = sock.subscribe(
        f"{request_prefix}:sub",
        {
            "subscriptions": [
                {
                    "type": "pane.output_matched",
                    "pane_id": pane_id,
                    "source": "recent",
                    "match": {
                        "type": "regex",
                        "value": (
                            r"(?i)(5-hour limit reached|usage limit reached|"
                            r"session limit|rate limit hit).*resets?\s"
                        ),
                    },
                }
            ]
        },
    )
    while True:
        snapshot = read_statusline_snapshot(state_dir, session_id)
        resets_at = pick_resets_at(snapshot, None)
        if resets_at is not None:
            return resets_at

        event = next(subscription, None)
        if event is None:
            continue
        matched_line = event.get("data", {}).get("matched_line", "")
        banner_epoch = parse_reset_epoch(matched_line, now_fn())
        if banner_epoch is not None:
            return banner_epoch


def sleep_until(
    target_epoch: int,
    buffer_seconds: int,
    pane_id: str,
    sock,
    request_prefix: str,
    time_fn=time.time,
    sleep_fn=time.sleep,
) -> bool:
    """Sleeps until target_epoch + buffer_seconds, waking periodically to
    confirm the pane still exists. Returns False if the pane disappeared."""
    wake_at = target_epoch + buffer_seconds
    while True:
        remaining = wake_at - time_fn()
        if remaining <= 0:
            return True
        sleep_fn(min(remaining, POLL_INTERVAL_SECONDS))
        try:
            sock.request(f"{request_prefix}:probe", "pane.get", {"pane_id": pane_id})
        except HerdrRequestError:
            return False


def resume_pane(sock, pane_id: str, message: str, notify: bool, request_prefix: str) -> None:
    try:
        pane = sock.request(f"{request_prefix}:get", "pane.get", {"pane_id": pane_id})
    except HerdrRequestError:
        return
    if should_send_resume(True, pane.get("agent_status")):
        sock.request(f"{request_prefix}:send_text", "pane.send_text", {"pane_id": pane_id, "text": message})
        sock.request(f"{request_prefix}:send_keys", "pane.send_keys", {"pane_id": pane_id, "keys": ["enter"]})
    if notify:
        sock.request(
            f"{request_prefix}:notify",
            "notification.show",
            {"title": "Claude Code resumed", "body": f"Rate limit window reset for {pane_id}"},
        )


def run_cycle(
    sock,
    pane_id: str,
    cwd: Path,
    state_dir: Path,
    config: dict,
    session_id: str | None,
    cycle_index: int,
) -> None:
    """One detect -> wait -> resume cycle."""
    request_prefix = f"claude-resume:{pane_id}:{cycle_index}"
    resets_at = wait_for_rate_limit_hit(sock, pane_id, state_dir, session_id, request_prefix)
    still_open = sleep_until(resets_at, config["resume_buffer_seconds"], pane_id, sock, request_prefix)
    if not still_open:
        return
    resume_pane(sock, pane_id, config["resume_message"], config["notify"], request_prefix)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pane-id", required=True)
    parser.add_argument("--cwd", required=True)
    args = parser.parse_args(argv)

    pane_id = args.pane_id
    cwd = Path(args.cwd)
    socket_path = os.environ["HERDR_SOCKET_PATH"]
    state_dir = Path(os.environ["HERDR_PLUGIN_STATE_DIR"])
    config_dir = Path(os.environ["HERDR_PLUGIN_CONFIG_DIR"])
    config = load_config(config_dir)

    if not config["enabled"]:
        return 0

    ensure_statusline_bridge(cwd, state_dir)

    try:
        cycle_index = 0
        while True:
            sock = HerdrSocket(socket_path)
            try:
                pane = sock.request(
                    f"claude-resume:{pane_id}:{cycle_index}:pane-get", "pane.get", {"pane_id": pane_id}
                )
                session_id = (pane.get("agent_session") or {}).get("value")
                run_cycle(sock, pane_id, cwd, state_dir, config, session_id, cycle_index)
            except HerdrRequestError:
                return 0
            finally:
                sock.close()
            cycle_index += 1
    finally:
        remove_watcher_pidfile(state_dir, pane_id)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_watcher.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/watcher.py tests/test_watcher.py
git commit -m "Add per-pane rate-limit watcher daemon"
```

---

### Task 8: Agent-detected hook (`on_agent_detected.py`)

**Files:**
- Create: `scripts/on_agent_detected.py`
- Test: `tests/test_on_agent_detected.py`
- Modify: `tests/test_manifest.py` (add a script-existence check now that the file exists)

**Interfaces:**
- Consumes: `socket_client.HerdrSocket` (Task 4), `state.is_watcher_running`/`write_watcher_pidfile` (Task 3).
- Produces: `on_agent_detected.parse_event(event_json: str) -> dict`, `on_agent_detected.should_watch(event: dict) -> bool`, `on_agent_detected.maybe_spawn_watcher(pane_id, cwd, state_dir, watcher_script=WATCHER_SCRIPT, popen_fn=subprocess.Popen) -> bool`, `on_agent_detected.main() -> int`. This is the manifest's `pane.agent_detected` event hook command.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_on_agent_detected.py`:

```python
import os
from pathlib import Path

from on_agent_detected import maybe_spawn_watcher, parse_event, should_watch
from state import is_watcher_running, write_watcher_pidfile


def test_parse_event_valid_json():
    assert parse_event('{"agent": "claude", "pane_id": "w1:p1"}') == {
        "agent": "claude",
        "pane_id": "w1:p1",
    }


def test_parse_event_invalid_json_returns_empty_dict():
    assert parse_event("not json") == {}


def test_should_watch_true_for_claude():
    assert should_watch({"agent": "claude"}) is True


def test_should_watch_false_for_other_agent():
    assert should_watch({"agent": "codex"}) is False


def test_should_watch_false_for_missing_agent():
    assert should_watch({}) is False


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_on_agent_detected.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'on_agent_detected'`

- [ ] **Step 3: Implement the hook script**

Create `scripts/on_agent_detected.py`:

```python
#!/usr/bin/env python3
"""Event hook: on = "pane.agent_detected". Starts a detached watcher.py for
newly-detected Claude Code panes, deduped by pidfile so repeated detections
of the same pane (model switches, /clear, compaction) don't stack watchers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from socket_client import HerdrSocket  # noqa: E402
from state import is_watcher_running, write_watcher_pidfile  # noqa: E402

WATCHER_SCRIPT = Path(__file__).parent / "watcher.py"


def parse_event(event_json: str) -> dict:
    try:
        return json.loads(event_json)
    except json.JSONDecodeError:
        return {}


def should_watch(event: dict) -> bool:
    return event.get("agent") == "claude"


def maybe_spawn_watcher(
    pane_id: str,
    cwd: str,
    state_dir: Path,
    watcher_script: Path = WATCHER_SCRIPT,
    popen_fn=subprocess.Popen,
) -> bool:
    """Spawns a detached watcher for pane_id unless one is already running.
    Returns True if a new watcher was spawned."""
    if is_watcher_running(state_dir, pane_id):
        return False
    process = popen_fn(
        ["python3", str(watcher_script), "--pane-id", pane_id, "--cwd", cwd],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    write_watcher_pidfile(state_dir, pane_id, process.pid)
    return True


def main() -> int:
    event = parse_event(os.environ.get("HERDR_PLUGIN_EVENT_JSON", "{}"))
    if not should_watch(event):
        return 0
    pane_id = os.environ.get("HERDR_PANE_ID")
    if not pane_id:
        return 0

    state_dir = Path(os.environ["HERDR_PLUGIN_STATE_DIR"])
    socket_path = os.environ["HERDR_SOCKET_PATH"]

    sock = HerdrSocket(socket_path)
    try:
        pane = sock.request("claude-resume:on-detect:pane-get", "pane.get", {"pane_id": pane_id})
    finally:
        sock.close()
    cwd = pane.get("cwd") or os.getcwd()

    maybe_spawn_watcher(pane_id, cwd, state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python3 -m pytest tests/test_on_agent_detected.py -v`
Expected: 8 passed, well under a second (the last test blocks on `os.waitpid`
rather than a fixed sleep, so it finishes as soon as the 0.5s stub child
actually exits)

- [ ] **Step 5: Add the manifest script-existence check now that the file exists**

Append to `tests/test_manifest.py`:

```python


def test_event_hook_commands_point_to_existing_scripts(manifest):
    from pathlib import Path

    root = Path(__file__).parent.parent
    events_by_hook = {event["on"]: event["command"] for event in manifest["events"]}
    for command in events_by_hook.values():
        script_path = root / command[-1]
        assert script_path.is_file(), f"{script_path} referenced by manifest does not exist"
```

- [ ] **Step 6: Run all tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass (this one now checks `scripts/on_agent_detected.py` exists; `scripts/on_pane_closed.py` is still missing and will be added in Task 9 — if this step is run strictly in order, temporarily narrow the assertion to only check `pane.agent_detected`'s command, then broaden it back to both hooks at the end of Task 9)

- [ ] **Step 7: Commit**

```bash
git add scripts/on_agent_detected.py tests/test_on_agent_detected.py tests/test_manifest.py
git commit -m "Add pane.agent_detected hook that spawns the watcher"
```

---

### Task 9: Pane-closed cleanup hook and status action

**Files:**
- Create: `scripts/on_pane_closed.py`
- Create: `scripts/status.py`
- Test: `tests/test_on_pane_closed.py`
- Test: `tests/test_status.py`
- Modify: `tests/test_manifest.py` (broaden the Task 8 check to cover both event hooks, plus the status action)

**Interfaces:**
- Consumes: `state.kill_watcher` (Task 3), `state.list_watched_pane_ids` (Task 3).
- Produces: `on_pane_closed.main() -> int` (the manifest's `pane.closed` event hook command), `status.main() -> int` (the manifest's `status` action command).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_on_pane_closed.py`:

```python
import subprocess
import sys

from on_pane_closed import main
from state import watcher_pidfile_path, write_watcher_pidfile


def test_main_kills_running_watcher(tmp_path, monkeypatch):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    write_watcher_pidfile(tmp_path, "w1:p1", process.pid)
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))

    result = main()

    assert result == 0
    process.wait(timeout=5)
    assert process.returncode is not None
    assert not watcher_pidfile_path(tmp_path, "w1:p1").exists()


def test_main_noop_when_pane_id_missing(monkeypatch):
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    assert main() == 0


def test_main_noop_when_no_pidfile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_PANE_ID", "w1:p1")
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))
    assert main() == 0
```

Create `tests/test_status.py`:

```python
import os
import subprocess
import sys

from state import write_watcher_pidfile
from status import main


def test_main_reports_no_watchers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))
    assert main() == 0
    assert "No Claude Code panes" in capsys.readouterr().out


def test_main_lists_alive_watchers(tmp_path, monkeypatch, capsys):
    write_watcher_pidfile(tmp_path, "w1:p1", os.getpid())
    monkeypatch.setenv("HERDR_PLUGIN_STATE_DIR", str(tmp_path))

    assert main() == 0
    output = capsys.readouterr().out
    assert "w1:p1" in output
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_on_pane_closed.py tests/test_status.py -v`
Expected: FAIL with `ModuleNotFoundError` for both `on_pane_closed` and `status`

- [ ] **Step 3: Implement the cleanup hook and status action**

Create `scripts/on_pane_closed.py`:

```python
#!/usr/bin/env python3
"""Event hook: on = "pane.closed". Terminates and cleans up any watcher
still running for the closed pane."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from state import kill_watcher  # noqa: E402


def main() -> int:
    pane_id = os.environ.get("HERDR_PANE_ID")
    if not pane_id:
        return 0
    state_dir = Path(os.environ["HERDR_PLUGIN_STATE_DIR"])
    kill_watcher(state_dir, pane_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `scripts/status.py`:

```python
#!/usr/bin/env python3
"""Manifest action: lists panes this plugin is currently watching."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))

from state import list_watched_pane_ids  # noqa: E402


def main() -> int:
    state_dir = Path(os.environ["HERDR_PLUGIN_STATE_DIR"])
    pane_ids = list_watched_pane_ids(state_dir)
    if not pane_ids:
        print("No Claude Code panes are currently being watched.")
    else:
        print("Watching for rate-limit resets on:")
        for pane_id in pane_ids:
            print(f"  - {pane_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python3 -m pytest tests/test_on_pane_closed.py tests/test_status.py -v`
Expected: 5 passed

- [ ] **Step 5: Broaden the manifest script-existence check**

Replace the `test_event_hook_commands_point_to_existing_scripts` test added in
Task 8 with a version that also checks the `status` action, in
`tests/test_manifest.py`:

```python
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
```

- [ ] **Step 6: Run the full test suite to verify everything passes**

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass, no failures, no errors

- [ ] **Step 7: Commit**

```bash
git add scripts/on_pane_closed.py scripts/status.py tests/test_on_pane_closed.py tests/test_status.py tests/test_manifest.py
git commit -m "Add pane-closed cleanup hook and status action"
```

---

### Task 10: README and manual verification checklist

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: nothing new — this documents the finished plugin from Tasks 1-9.
- Produces: install instructions and the manual verification checklist from the spec, for a human to run once against real herdr + Claude Code.

- [ ] **Step 1: Write the README**

Create `README.md`:

```markdown
# claude-resume

A herdr plugin that detects when a Claude Code pane hits its 5-hour usage
limit and automatically resumes the session once the window resets.

## How it works

- Watches every herdr pane where Claude Code is detected.
- Prefers exact reset timing from Claude Code's `statusLine` data
  (`rate_limits.five_hour.resets_at`) when a project already has a
  `statusLine` configured — this plugin chains onto it transparently and
  never creates one from scratch.
- Falls back to watching pane output for the rate-limit banner text
  (e.g. `"5-hour limit reached - resets 3pm"`) otherwise.
- When the window resets, sends a configurable message (default
  `"continue"`) into the pane and shows a notification.

## Install

Local development:

```bash
herdr plugin link .
herdr plugin config-dir claude-resume
```

## Configure

Copy `config.example.json` to the directory printed by
`herdr plugin config-dir claude-resume` as `config.json`, and edit:

```json
{
  "enabled": true,
  "resume_message": "continue",
  "resume_buffer_seconds": 45,
  "notify": true
}
```

## Check what's being watched

```bash
herdr plugin action invoke claude-resume.status
```

## Run the tests

```bash
python3 -m pytest tests/ -v
```

## Manual verification checklist

Automated tests cover the parsing, state, socket-protocol, and
statusLine-wrapping logic without needing a real herdr server or a real
Claude Code rate-limit hit. After linking the plugin, verify the following
by hand against a real herdr + Claude Code session:

1. Link the plugin (`herdr plugin link .`), open a Claude Code pane, and
   confirm a watcher started: check for a pidfile under
   `$(herdr plugin config-dir claude-resume)/../state/watchers/` (or run
   `herdr plugin action invoke claude-resume.status` and see the pane
   listed).
2. Exercise the screen-scrape path without waiting hours: from another pane,
   use `herdr pane send-text <pane> "..."` (or similar) to write a line
   containing `"5-hour limit reached - resets <a few minutes from now>"`
   into a scratch pane the plugin is watching, and confirm the notification
   and `"continue"` message arrive at the right time.
3. Confirm a project with an existing `statusLine` still shows the same
   visible status line after linking the plugin, and that
   `$(herdr plugin config-dir claude-resume)/../state/statusline/originals.json`
   records the original command.
4. Close the watched pane and confirm the watcher process exits (no
   lingering `watcher.py` process, pidfile removed).
5. Let a real 5-hour limit hit and reset happen at least once, and confirm
   the session actually resumes unattended.
```

- [ ] **Step 2: Run the full test suite one final time**

Run: `python3 -m pytest tests/ -v`
Expected: all tests pass

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Add README with install instructions and manual verification checklist"
```

---

## Self-Review Notes

- **Spec coverage:** every spec section maps to a task — scaffold/manifest
  (Task 1), screen-scrape parsing (Task 2), pidfile dedup/cleanup (Tasks 3,
  9), raw socket protocol (Task 4), statusLine chaining (Tasks 5, 6),
  detection+wait+resume orchestration (Task 7), the `pane.agent_detected`
  spawn hook (Task 8), the manual verification checklist (Task 10).
- **Placeholder scan:** no TBD/TODO markers; every step has complete,
  runnable code and exact commands.
- **Type/interface consistency:** `state.py`'s pidfile format (`pid\npane_id\n`)
  is written once in Task 3 and consumed identically by `is_watcher_running`,
  `kill_watcher`, and `list_watched_pane_ids` — no divergent formats.
  `watcher.py`'s `sock` parameter duck-types `.request(request_id, method,
  params)` and `.subscribe(request_id, params)` identically to
  `socket_client.HerdrSocket`, which is exactly what `FakeSocket` in Task 7's
  tests implements and what `on_agent_detected.py`/`watcher.main()` construct
  via the real `HerdrSocket`. `pick_resets_at`, `should_send_resume`, and
  `read_statusline_snapshot` signatures used inside `watcher.py` match their
  test calls exactly.
