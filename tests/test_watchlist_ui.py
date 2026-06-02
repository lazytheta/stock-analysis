"""Tests for Phase 2-A watchlist UI helpers."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

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
    """All five forward-looking lenses active → 5 filled dots, '5 lenses' label.
    (reverse_dcf intentionally not rendered — it anchors at price.)"""
    lenses = {
        "dcf": {}, "multiples": {}, "historical": {}, "dividend": {}, "sotp": {},
        "reverse_dcf": {}, "dividend_stub": None,
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 5
    assert 'class="ld-off"' not in html
    assert "5 lenses" in html


def test_render_lens_dots_dcf_only():
    """Only DCF active → 1 filled dot, 4 grey dots, '1 lens' label."""
    lenses = {"dcf": {}, "multiples": None, "historical": None, "dividend": None, "sotp": None, "reverse_dcf": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1
    assert html.count('class="ld-off"') == 4
    assert "1 lens" in html


def test_render_lens_dots_dcf_plus_historical():
    """DCF + Historical active → 2 filled dots, '2 lenses' label.
    (Replaces test_render_lens_dots_dcf_plus_reverse — reverse_dcf no longer renders.)"""
    lenses = {"dcf": {}, "multiples": None, "historical": {}, "reverse_dcf": None, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 2
    assert "2 lenses" in html


def test_render_lens_dots_empty_dict():
    """No lenses at all → 'no lenses' label, all 5 dots grey."""
    html = streamlit_app._render_lens_dots({}, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert html.count('class="ld-off"') == 5
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


def test_refresh_filters_to_stale_only():
    """Configs without summary OR with summary > 7 days old are stale; fresh ones are skipped."""
    now = datetime.now(UTC)
    cfgs = {
        "FRESH": {"valuation_summary": {"calculated_at": now.isoformat(),
                                          "weighted_fv_mid": 50.0}},
        "OLD": {"valuation_summary": {"calculated_at": (now - timedelta(days=10)).isoformat(),
                                        "weighted_fv_mid": 50.0}},
        "EMPTY": {},
    }

    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote") as mock_calc, \
         patch.object(streamlit_app, "save_config") as mock_save:
        mock_calc.return_value = {"calculated_at": now.isoformat(), "weighted_fv_mid": 99.0}
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1", force=False
        )
    assert set(result["computed"]) == {"OLD", "EMPTY"}
    assert result["skipped"] == ["FRESH"]
    assert result["errors"] == []


def test_refresh_force_includes_fresh():
    now = datetime.now(UTC)
    cfgs = {
        "FRESH": {"valuation_summary": {"calculated_at": now.isoformat(),
                                          "weighted_fv_mid": 50.0}},
    }
    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote") as mock_calc, \
         patch.object(streamlit_app, "save_config") as mock_save:
        mock_calc.return_value = {"calculated_at": now.isoformat(), "weighted_fv_mid": 99.0}
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1", force=True
        )
    assert result["computed"] == ["FRESH"]
    assert result["skipped"] == []


def test_refresh_one_ticker_error_others_succeed():
    now = datetime.now(UTC)
    cfgs = {"GOOD": {}, "BAD": {}}

    def fake_calc(cfg):
        if cfg.get("ticker") == "BAD":
            raise ValueError("boom")
        return {"calculated_at": now.isoformat(), "weighted_fv_mid": 50.0}

    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote", side_effect=fake_calc), \
         patch.object(streamlit_app, "save_config"), \
         patch("gather_data.fetch_market_inputs", return_value={}), \
         patch("gather_data.fetch_historical_multiples", return_value={}), \
         patch("gather_data.enrich_peer_with_market_data", side_effect=lambda p: dict(p)):
        # Ensure cfgs have ticker so the side_effect can branch
        cfgs["GOOD"]["ticker"] = "GOOD"
        cfgs["BAD"]["ticker"] = "BAD"
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1"
        )
    assert "GOOD" in result["computed"]
    assert any("BAD" in e for e in result["errors"])


def test_refresh_unparseable_calculated_at_treated_as_stale():
    cfgs = {
        "WEIRD": {"valuation_summary": {"calculated_at": "garbage",
                                          "weighted_fv_mid": 50.0}},
    }
    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote") as mock_calc, \
         patch.object(streamlit_app, "save_config"):
        mock_calc.return_value = {"calculated_at": datetime.now(UTC).isoformat(),
                                  "weighted_fv_mid": 99.0}
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1"
        )
    assert result["computed"] == ["WEIRD"]


def test_refresh_invokes_on_progress_callback():
    """Caller can pass on_progress to receive (done, total) updates per ticker."""
    now = datetime.now(UTC)
    cfgs = {"A": {"ticker": "A"}, "B": {"ticker": "B"}, "C": {"ticker": "C"}}
    progress_calls = []

    def cb(done, total):
        progress_calls.append((done, total))

    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote") as mock_calc, \
         patch.object(streamlit_app, "save_config"), \
         patch("gather_data.fetch_market_inputs", return_value={}), \
         patch("gather_data.fetch_historical_multiples", return_value={}), \
         patch("gather_data.enrich_peer_with_market_data", side_effect=lambda p: dict(p)):
        mock_calc.return_value = {"calculated_at": now.isoformat(), "weighted_fv_mid": 50.0}
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1", on_progress=cb
        )

    assert len(result["computed"]) == 3
    # 3 progress callbacks, in some order — final one always reports (3, 3)
    assert len(progress_calls) == 3
    assert progress_calls[-1] == (3, 3)
    # Each call's done value strictly increases
    dones = [d for d, _ in progress_calls]
    assert dones == sorted(dones)


def test_render_lens_dots_empty_dict_is_active_not_inactive():
    """Pin the data contract: an empty dict {} is an ACTIVE lens (not None),
    even though {} is falsy. This guards against regressions if someone
    changes the active-check from `is not None` to bare truthiness."""
    lenses_with_empty = {"dcf": {}, "multiples": None, "historical": None, "reverse_dcf": None, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses_with_empty, theme={"text_muted": "#888"})
    # {} is active → 1 ld-on (not 0)
    assert html.count('class="ld-on"') == 1, \
        "Empty dict {} should be treated as active lens (not None semantics)"
    assert "1 lens" in html


def test_render_lens_dots_dividend_skipped_for_non_payer():
    """Non-payer with 3 forward lenses active + dividend & sotp skipped → 3 dots on, 2 off."""
    lenses = {
        "dcf": {}, "multiples": {}, "historical": {},
        "dividend": None, "reverse_dcf": {},
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 3
    assert html.count('class="ld-off"') == 2
    assert "3 lenses" in html


def test_render_lens_dots_zero_active():
    """No lenses active → 'no lenses' label, all dots grey."""
    lenses = {
        "dcf": None, "multiples": None, "historical": None, "reverse_dcf": None,
        "dividend": None,
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert "no lenses" in html


def test_render_lens_dots_includes_dividend_when_active():
    """Dividend lens is now a first-class forward lens (Phase 2-C) — when
    non-None it renders its own dot alongside DCF/Peers/Historical."""
    lenses = {
        "dcf": {}, "multiples": None, "historical": None, "reverse_dcf": None,
        "dividend": {"fv_mid": 50.0},
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    # DCF + dividend → 2 ld-on dots, 3 ld-off (multiples + historical + sotp)
    assert html.count('class="ld-on"') == 2
    assert html.count('class="ld-off"') == 3
    assert "2 lenses" in html


def test_render_football_field_renders_all_active_lenses():
    """Full summary → HTML contains 5 forward-lens bars (DCF, Peers,
    Historical, Dividend, SOTP) + price marker. Reverse DCF intentionally absent."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 80.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 120.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":         {"fv_low": 90.0,  "fv_mid": 100.0, "fv_high": 110.0},
            "multiples":   {"fv_low": 70.0,  "fv_mid": 95.0,  "fv_high": 130.0},
            "historical":  {"fv_low": 95.0,  "fv_mid": 105.0, "fv_high": 115.0},
            "dividend":    {"fv_low": 85.0,  "fv_mid": 95.0,  "fv_high": 105.0},
            "sotp":        {"fv_low": 88.0,  "fv_mid": 98.0,  "fv_high": 108.0},
            "reverse_dcf": {"fv_low": 100.0, "fv_mid": 100.0, "fv_high": 100.0},
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    assert "DCF" in html
    assert "Peers" in html
    assert "Historical" in html
    assert "Dividend" in html
    assert "SOTP" in html
    assert "Reverse DCF" not in html
    assert "$100" in html or "100.00" in html
    assert html.count('class="ff-bar"') == 5


def test_render_football_field_handles_missing_lens():
    """Lens=None → bar greyed out with '(skipped)' label."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 90.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 110.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":         {"fv_low": 90.0, "fv_mid": 100.0, "fv_high": 110.0},
            "multiples":   None,
            "historical":  {"fv_low": 95.0, "fv_mid": 105.0, "fv_high": 115.0},
            "reverse_dcf": {"fv_low": 100.0, "fv_mid": 100.0, "fv_high": 100.0},
            "dividend":    None,
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    assert "(skipped)" in html


def test_render_football_field_handles_no_summary():
    """Empty/None summary → returns a placeholder (no crash)."""
    assert streamlit_app._render_football_field(None, theme=_theme_stub()) != ""
    assert streamlit_app._render_football_field({}, theme=_theme_stub()) != ""
