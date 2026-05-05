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


def test_render_lens_dots_all_active():
    lenses = {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    # Three filled dots
    assert html.count('class="ld-on"') == 3
    assert 'class="ld-off"' not in html
    assert "3 lenses" in html


def test_render_lens_dots_dcf_only():
    lenses = {"dcf": {}, "multiples": None, "reverse_dcf": None, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1
    assert html.count('class="ld-off"') == 2
    assert "DCF only" in html


def test_render_lens_dots_dcf_plus_reverse():
    lenses = {"dcf": {}, "multiples": None, "reverse_dcf": {}, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 2
    assert "DCF + reverse" in html


def test_render_lens_dots_empty_dict():
    """No lenses at all → 'no lenses' label, all grey."""
    html = streamlit_app._render_lens_dots({}, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert html.count('class="ld-off"') == 3
    assert "no lenses" in html


def _theme_stub():
    return {"text": "#eee", "text_muted": "#888", "accent": "#6e8a76"}


def test_render_fv_cell_full_summary():
    """With a complete valuation_summary, render mid + range + bar + dots."""
    summary = {
        "weighted_fv_low": 60.0,
        "weighted_fv_mid": 80.0,
        "weighted_fv_high": 100.0,
        "lenses": {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None},
    }
    html = streamlit_app._render_fv_cell(
        price=70.0, summary=summary, legacy_intrinsic=None, theme=_theme_stub()
    )
    assert "$80" in html              # mid
    assert "$60" in html              # low
    assert "$100" in html             # high
    assert "range-bar" in html        # bar present
    assert 'class="ld-on"' in html    # lens dots present


def test_render_fv_cell_legacy_fallback():
    """Without summary, fall back to legacy_intrinsic + 'single-lens' badge."""
    html = streamlit_app._render_fv_cell(
        price=72.0, summary=None, legacy_intrinsic=95.0, theme=_theme_stub()
    )
    assert "$95" in html
    assert "single-lens" in html
    assert "range-bar" not in html
    assert "Refresh all" in html


def test_render_fv_cell_neither_summary_nor_legacy():
    """Defensive: both missing → em-dash placeholder."""
    html = streamlit_app._render_fv_cell(
        price=72.0, summary=None, legacy_intrinsic=None, theme=_theme_stub()
    )
    assert "—" in html


def test_render_fv_cell_marker_past_high_red_tinted():
    summary = {
        "weighted_fv_low": 60.0, "weighted_fv_mid": 80.0, "weighted_fv_high": 100.0,
        "lenses": {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None},
    }
    html = streamlit_app._render_fv_cell(
        price=200.0, summary=summary, legacy_intrinsic=None, theme=_theme_stub()
    )
    assert "left:99%" in html.replace(" ", "")  # marker clamped to 99
    # red tint applied — implementation uses inline color override or extra class
    assert "#d96a5a" in html or "past-high" in html
