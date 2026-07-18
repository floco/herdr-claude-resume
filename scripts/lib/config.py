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
