from datetime import datetime, timedelta, timezone

from reset_time import parse_reset_epoch


def _now(hour, minute=0):
    return datetime(2026, 7, 18, hour, minute, tzinfo=timezone.utc)


def test_reached_with_future_time_today():
    now = _now(10)
    result = parse_reset_epoch("5-hour limit reached - resets 3pm", now)
    assert result == int(now.replace(hour=15, minute=0).timestamp())


def test_reached_with_past_time_rolls_to_tomorrow():
    now = _now(16)
    result = parse_reset_epoch("5-hour limit reached - resets 3pm", now)
    expected = now.replace(hour=15, minute=0) + timedelta(days=1)
    assert result == int(expected.timestamp())


def test_continuing_with_credits_returns_none():
    now = _now(10)
    line = "5-hour limit resets 3pm - continuing with usage credits"
    assert parse_reset_epoch(line, now) is None


def test_unrelated_line_returns_none():
    assert parse_reset_epoch("hello world", _now(10)) is None


def test_am_time_rolls_to_tomorrow_when_already_past():
    now = _now(10)
    result = parse_reset_epoch("You've hit your session limit · resets 2am", now)
    expected = now.replace(hour=2, minute=0) + timedelta(days=1)
    assert result == int(expected.timestamp())


def test_minutes_are_parsed():
    now = _now(10)
    result = parse_reset_epoch("Rate limit hit. Resets at 4:30pm", now)
    assert result == int(now.replace(hour=16, minute=30).timestamp())


def test_no_time_token_uses_five_hour_fallback():
    now = _now(10)
    result = parse_reset_epoch("usage limit reached", now)
    expected = now + timedelta(hours=5)
    assert result == int(expected.timestamp())


def test_case_insensitive_matching():
    now = _now(10)
    result = parse_reset_epoch("RATE LIMIT HIT. RESETS AT 4PM", now)
    assert result == int(now.replace(hour=16, minute=0).timestamp())


def test_out_of_range_time_token_uses_fallback():
    now = _now(10)
    result = parse_reset_epoch("usage limit reached, resets 13pm", now)
    expected = now + timedelta(hours=5)
    assert result == int(expected.timestamp())
