"""Tests for Phase 2-A watchlist UI helpers."""
from datetime import UTC, datetime, timedelta

import pytest  # noqa: F401

import streamlit_app


def test_scaffold_present():
    """Sanity: the test file is discovered and runs."""
    assert True


def test_format_relative_time_none():
    assert streamlit_app._format_relative_time(None) == "never"
    assert streamlit_app._format_relative_time("") == "never"


def test_format_relative_time_just_now():
    now = datetime.now(UTC)
    iso = now.isoformat()
    assert streamlit_app._format_relative_time(iso) == "just now"


def test_format_relative_time_minutes():
    past = datetime.now(UTC) - timedelta(minutes=5)
    assert streamlit_app._format_relative_time(past.isoformat()) == "5 minutes ago"


def test_format_relative_time_hours():
    past = datetime.now(UTC) - timedelta(hours=3)
    assert streamlit_app._format_relative_time(past.isoformat()) == "3 hours ago"


def test_format_relative_time_days():
    past = datetime.now(UTC) - timedelta(days=4)
    assert streamlit_app._format_relative_time(past.isoformat()) == "4 days ago"


def test_format_relative_time_future_treated_as_just_now():
    """Clock skew: future timestamps treated as current."""
    future = datetime.now(UTC) + timedelta(hours=2)
    assert streamlit_app._format_relative_time(future.isoformat()) == "just now"


def test_format_relative_time_unparseable():
    """Garbage input → 'unknown' (don't crash)."""
    assert streamlit_app._format_relative_time("not a timestamp") == "unknown"


def test_range_bar_marker_in_range():
    """Price between low and high → percent in (0, 100), not past_high."""
    pct, past = streamlit_app._range_bar_marker_position(80, 60, 100)
    assert pct == 50.0
    assert past is False


def test_range_bar_marker_at_low():
    pct, past = streamlit_app._range_bar_marker_position(60, 60, 100)
    assert pct == 0.0
    assert past is False


def test_range_bar_marker_at_high():
    pct, past = streamlit_app._range_bar_marker_position(100, 60, 100)
    assert pct == 100.0
    assert past is False


def test_range_bar_marker_below_low_clamps_to_one():
    """Price below low → 1% (just visible at left edge), not past_high."""
    pct, past = streamlit_app._range_bar_marker_position(40, 60, 100)
    assert pct == 1.0
    assert past is False


def test_range_bar_marker_above_high_clamps_to_99_and_flags_past_high():
    pct, past = streamlit_app._range_bar_marker_position(150, 60, 100)
    assert pct == 99.0
    assert past is True


def test_range_bar_marker_low_equals_high_returns_50():
    """Degenerate range — center the marker, no past_high."""
    pct, past = streamlit_app._range_bar_marker_position(80, 80, 80)
    assert pct == 50.0
    assert past is False


def test_range_bar_marker_invalid_inputs_return_50():
    """Missing/zero inputs → safe center fallback."""
    pct, _ = streamlit_app._range_bar_marker_position(0, 60, 100)
    assert pct == 50.0
    pct, _ = streamlit_app._range_bar_marker_position(80, 0, 100)
    assert pct == 50.0
