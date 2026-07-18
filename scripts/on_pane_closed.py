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
