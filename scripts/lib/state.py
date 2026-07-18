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
