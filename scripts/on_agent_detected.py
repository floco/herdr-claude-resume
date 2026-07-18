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
