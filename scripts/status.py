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
