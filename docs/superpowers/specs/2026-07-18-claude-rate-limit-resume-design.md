# Claude Code rate-limit auto-resume — herdr plugin design

Date: 2026-07-18
Status: approved (pending spec self-review)

## Problem

Claude Code's Pro/Max subscription enforces a rolling 5-hour usage window. When
a session hits it, Claude Code stops responding to new prompts until the
window resets (a timestamp announced in the terminal, and — for Pro/Max
subscribers after their first API response — available as structured data via
the `statusLine` hook). Today a user running Claude Code inside herdr has to
notice the banner, remember the reset time, and come back to manually
continue. This project builds a herdr plugin that watches Claude Code panes,
detects the reset time, waits, and automatically resumes the session
unattended.

## Non-goals

- No changes to herdr core (Rust source). This is a plugin only, using
  herdr's existing plugin manifest, socket API, and CLI surface.
- No Windows support in v1 (python3 invocation and process-detach semantics
  differ enough from linux/macos to defer).
- No handling of the weekly (`seven_day`) rate limit window — 5-hour window
  only, per the request.
- The plugin never creates a Claude Code `statusLine` from scratch. It only
  chains an already-configured one. Projects without a statusLine fall back
  to screen-scraping pane output.

## Architecture

New standalone git repo/directory: `/projects/herdr-claude-resume`, structured
as a herdr plugin (directory + `herdr-plugin.toml`, installable via
`herdr plugin link` for local dev or `herdr plugin install owner/repo` once
published).

```
herdr-claude-resume/
  herdr-plugin.toml
  scripts/
    on_agent_detected.py   # event hook: on = "pane.agent_detected"
    on_pane_closed.py      # event hook: on = "pane.closed" (cleanup)
    watcher.py             # long-running per-pane daemon
    statusline_bridge.py   # chained statusLine wrapper
    lib/
      socket_client.py     # raw HERDR_SOCKET_PATH JSON-lines client
      reset_time.py        # banner-text -> epoch parsing (pure, unit-testable)
      state.py             # pidfile / state-dir helpers
  tests/
    test_reset_time.py
    test_mock_socket.py
  config.example.json
```

Implementation language: Python 3, no third-party dependencies. This matches
herdr's own bundled Claude integration (`src/integration/assets/claude/herdr-agent-state.sh`),
which already requires `python3` and talks to `HERDR_SOCKET_PATH` with a raw
`AF_UNIX` socket and newline-delimited JSON — the same technique this plugin
reuses for consistency and to avoid adding new dependencies to a user's
machine.

Platforms declared in the manifest: `["linux", "macos"]`.

## Manifest

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

`pane.agent_detected` and `pane.closed` are both in herdr's
`PLUGIN_HOOK_EVENT_KINDS` allow-list (verified in
`src/api/schema/events.rs`), so both hooks link without warnings.

## Data flow

### 1. Detection trigger

`on_agent_detected.py` runs on every `pane.agent_detected` event. It:

- Reads `HERDR_PLUGIN_EVENT_JSON` / `HERDR_PLUGIN_CONTEXT_JSON` for `agent`,
  `pane_id`, `workspace_id`, and cwd.
- Exits immediately if `agent != "claude"`.
- Dedupes: if `HERDR_PLUGIN_STATE_DIR/watchers/<pane_id>.pid` names a still-
  running process, exits (already watching this pane).
- Otherwise double-forks (`os.fork` + `setsid`) a detached
  `watcher.py --pane-id <id> --cwd <cwd> --workspace-id <id>` that outlives
  this hook invocation, redirecting stdio to a per-pane log file under
  `HERDR_PLUGIN_STATE_DIR/logs/`.
- Writes the child pid to the pidfile before exiting.

### 2. Per-pane watcher loop (`watcher.py`)

Runs until the pane closes. Each iteration:

**StatusLine path (preferred, used only when a statusLine already exists):**

- Reads `<cwd>/.claude/settings.json` (falling back to `~/.claude/settings.json`)
  for `statusLine.command`.
- If present and not already wrapped (idempotency marker: a
  `# herdr-claude-resume wrapper` comment / sentinel field), rewrites it once
  to invoke `statusline_bridge.py <original-command...>`.
- `statusline_bridge.py` is what Claude Code actually invokes going forward.
  On every call it: reads stdin JSON once, writes
  `rate_limits.five_hour.{used_percentage,resets_at}` (when present) plus
  `session_id` to `HERDR_PLUGIN_STATE_DIR/statusline/<session_id>.json`
  (atomic rename), then re-invokes the original command with the same stdin
  and forwards its stdout/stderr/exit code unchanged. Visible status line
  behavior is unaffected.
- The watcher polls that JSON file (keyed by the pane's known
  `agent_session_id`, obtained via `agent.get`) for `used_percentage >= 100`
  and reads `resets_at` (Unix epoch seconds) directly — no text/timezone
  parsing needed.

**Screen-scrape fallback (always available, used when no statusLine exists):**

- Opens a raw socket connection to `HERDR_SOCKET_PATH` and sends an
  `events.subscribe` request:
  ```json
  {"id":"sub_1","method":"events.subscribe","params":{"subscriptions":[
    {"type":"pane.output_matched","pane_id":"<id>","source":"recent",
     "match":{"type":"regex","value":"(?i)(5-hour limit reached|usage limit reached|session limit|rate limit hit).*resets?\\s"}}
  ]}}
  ```
- This blocks (no busy-polling) until a matching line arrives.
- `reset_time.py` parses the matched line:
  - Skips/ignores it if it also contains `"continuing with usage credits"`
    (that variant means Claude is *not* blocked — still working on paid
    credits — so it must never arm the resume timer).
  - Extracts a time token (`3pm`, `4:30pm`, `10:15 AM`, optional `(TZ)`
    suffix) and computes the next future occurrence of that wall-clock time
    in the given timezone (or local system timezone if none given).
  - Falls back to "now + 5 hours" if no time token is parseable, so the
    watcher never spins forever un-armed.

**Once a `resets_at` epoch is known (from either path):**

- Sleeps until `resets_at + resume_buffer_seconds` (default 45s), waking
  briefly every ~60s only to confirm via `pane.get` that the pane still
  exists (exits cleanly if it doesn't — cleanup also happens via
  `on_pane_closed.py`).
- At wake time: re-checks `pane.get`/`agent.get` — skips sending anything if
  the pane is gone, or if `agent_status` is already `"working"` (the user
  beat the watcher to it manually).
- Sends the configured resume message (default `"continue"`) via
  `pane.send_text`, then `pane.send_keys` with `"enter"`.
- Always calls `notification.show` ("Claude Code resumed in `<pane_id>`"),
  regardless of whether a message was actually sent, so the user has a
  record of what happened.
- Loops back to step 2's detection phase to keep watching the same pane for
  the *next* time it hits the limit (a long session can hit it more than
  once in a day).

### 3. Cleanup (`on_pane_closed.py`)

On `pane.closed`, kills the pidfile's process (if still alive) and removes
`HERDR_PLUGIN_STATE_DIR/watchers/<pane_id>.pid`.

## Config

`HERDR_PLUGIN_CONFIG_DIR/config.json`, all fields optional:

```json
{
  "enabled": true,
  "resume_message": "continue",
  "resume_buffer_seconds": 45,
  "notify": true
}
```

`enabled: false` makes `on_agent_detected.py` a no-op (existing watchers for
already-running panes keep running until their pane closes).

## Safety guards

- Never creates a `statusLine` from nothing — chains an existing one only.
- Never sends synthetic input to a pane that has closed or already resumed
  on its own.
- Pidfile dedup prevents duplicate watchers stacking up on the same pane
  across repeated `pane.agent_detected` events (e.g. model switches,
  `/clear`, compaction).
- Detached watchers exit gracefully if herdr restarts and the pane_id no
  longer resolves (`pane.get` returns `not_found`).
- The `"continuing with usage credits"` banner variant is explicitly
  excluded from arming the timer, since it indicates Claude is not actually
  blocked.

## Testing plan

Full end-to-end testing (real herdr TUI + real Claude Code hitting a real
5-hour limit) isn't practical to drive from an automated sandbox. The plan
splits verification into what can be tested now vs. what needs a manual pass
after install:

- **Automated, no herdr/Claude Code required:**
  - `tests/test_reset_time.py`: pure unit tests for `reset_time.py` — AM/PM
    parsing, day-rollover (time already passed today → tomorrow), timezone
    suffix handling, the "continuing with usage credits" exclusion, and the
    5-hour fallback when parsing fails.
  - `tests/test_mock_socket.py`: a throwaway `AF_UNIX` mock server standing
    in for herdr, asserting the exact JSON wire shape of the
    `events.subscribe`, `pane.get`, `pane.send_text`, `pane.send_keys`, and
    `notification.show` calls the plugin makes, without needing the real
    herdr binary.
- **Manual, after install against real herdr + Claude Code** (documented as
  a checklist in the plugin's README, not automated):
  1. Link the plugin locally (`herdr plugin link .`), open a Claude Code
     pane, confirm `on_agent_detected.py` starts a watcher (check the pidfile
     and log under `HERDR_PLUGIN_STATE_DIR`).
  2. Force/observe an actual rate-limit hit (or, for a faster loop, feed a
     synthetic banner line into a scratch pane to exercise the screen-scrape
     path without waiting hours).
  3. Confirm the notification fires and `"continue"` is sent at the right
     time when the window resets.
  4. Repeat with a project that already has a custom `statusLine` configured,
     confirming the visible status line is unaffected and the bridge file
     gets written.
