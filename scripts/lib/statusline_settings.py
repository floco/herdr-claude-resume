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
