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

    sock = HerdrSocket(socket_path)
    try:
        cycle_index = 0
        while True:
            try:
                pane = sock.request(
                    f"claude-resume:{pane_id}:{cycle_index}:pane-get", "pane.get", {"pane_id": pane_id}
                )
                session_id = (pane.get("agent_session") or {}).get("value")
                run_cycle(sock, pane_id, cwd, state_dir, config, session_id, cycle_index)
            except HerdrRequestError:
                return 0
            cycle_index += 1
    finally:
        remove_watcher_pidfile(state_dir, pane_id)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
