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
