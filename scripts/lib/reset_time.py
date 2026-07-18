"""Pure functions for detecting Claude Code's 5-hour rate-limit banner in
terminal output and computing when the window resets.

No I/O, no herdr/socket dependency -- fully unit-testable in isolation.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# Phrases that mean Claude Code is currently blocked by the 5-hour limit.
_BLOCKED_PHRASES = re.compile(
    r"(5-hour limit reached|usage limit reached|session limit|rate limit hit)",
    re.IGNORECASE,
)

# This variant means Claude is NOT blocked -- it's still working, spending
# paid usage credits instead of stopping. Must never arm the resume timer.
_NOT_BLOCKED_PHRASE = re.compile(r"continuing with usage credits", re.IGNORECASE)

# Matches a 12-hour clock time token like "3pm", "4:30pm", "10:15 AM".
_TIME_TOKEN = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b", re.IGNORECASE)

FALLBACK_WAIT = timedelta(hours=5)


def parse_reset_epoch(line: str, now: datetime) -> int | None:
    """Return the Unix epoch (seconds) the 5-hour limit resets at, or None
    if `line` does not indicate the limit was reached.

    `now` must be a timezone-aware datetime representing "the current time"
    in the timezone the reset time token should be interpreted in.
    """
    if _NOT_BLOCKED_PHRASE.search(line):
        return None
    if not _BLOCKED_PHRASES.search(line):
        return None

    match = _TIME_TOKEN.search(line)
    if match is None:
        return int((now + FALLBACK_WAIT).timestamp())

    hour_raw = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = match.group(3).lower()

    if not (1 <= hour_raw <= 12) or not (0 <= minute <= 59):
        return int((now + FALLBACK_WAIT).timestamp())

    hour = 0 if hour_raw == 12 else hour_raw
    if meridiem == "pm":
        hour += 12

    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return int(candidate.timestamp())
