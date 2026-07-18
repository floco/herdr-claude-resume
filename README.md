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
