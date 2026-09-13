"""Tests for Phase 2-A watchlist UI helpers."""
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

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
    """2026-07-30: the watchlist surfaces ONLY the DCF lens. Even with every
    other lens active, exactly one dot renders and the label reads '1 lens'."""
    lenses = {
        "dcf": {}, "dividend": {},
        "multiples": {}, "historical": {}, "reverse_dcf": {},  # not surfaced
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1
    assert 'class="ld-off"' not in html
    assert "1 lens" in html


def test_render_lens_dots_dcf_only():
    """Only DCF active → 1 filled dot, 0 grey dots (DCF is the only surfaced
    lens now), '1 lens' label."""
    lenses = {"dcf": {}, "dividend": None,
              "multiples": None, "historical": None, "reverse_dcf": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1
    assert 'class="ld-off"' not in html
    assert "1 lens" in html


def test_render_lens_dots_only_dcf_ever_surfaces():
    """Peers, Historical, Dividend and SOTP are never surfaced on the watchlist
    even when active — only DCF is (2026-07-30 'one lens' request)."""
    lenses = {"dcf": {}, "multiples": {}, "historical": {},
              "dividend": {}, "reverse_dcf": {}}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1   # only DCF
    for gone in ("Peers", "Historical", "Dividend", "SOTP"):
        assert gone not in html
    assert "1 lens" in html


def test_render_lens_dots_empty_dict():
    """No lenses at all → 'no lenses' label, the single DCF dot grey."""
    html = streamlit_app._render_lens_dots({}, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert html.count('class="ld-off"') == 1
    assert "no lenses" in html


def _theme_stub():
    return {"text": "#eee", "text_muted": "#888", "accent": "#6e8a76"}


def test_render_fv_cell_full_summary():
    """With a complete valuation_summary, render mid + range + bar. The lens-dots
    row and the 'details ›' football-field tooltip were removed 2026-07-31 (the
    watchlist surfaces the DCF fair value only)."""
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
    assert 'class="ld-on"' not in html   # lens dots removed
    assert "lens" not in html            # no "{N} lens(es)" label
    assert "details" not in html         # no details trigger


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


def test_render_lens_dots_zero_active():
    """No lenses active → 'no lenses' label, all dots grey."""
    lenses = {
        "dcf": None, "multiples": None, "historical": None, "reverse_dcf": None,
        "dividend": None,
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert "no lenses" in html


def test_render_football_field_only_dcf_surfaces():
    """2026-07-30: the football field surfaces ONLY the DCF lens. Peers,
    Historical, Dividend, SOTP and Reverse DCF are all absent — one bar."""
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
            "reverse_dcf": {"fv_low": 100.0, "fv_mid": 100.0, "fv_high": 100.0},
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    assert "DCF" in html
    for gone in ("Dividend", "SOTP", "Peers", "Historical", "Reverse DCF"):
        assert gone not in html
    assert "$100" in html or "100.00" in html
    assert html.count('class="ff-bar"') == 1


def test_render_football_field_handles_missing_lens():
    """DCF=None → its bar greys out with a '(skipped)' label (no crash)."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 90.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 110.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":         None,
            "historical":  {"fv_low": 95.0, "fv_mid": 105.0, "fv_high": 115.0},
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    assert "(skipped)" in html


def test_render_football_field_dcf_renders_as_point_not_range():
    """The single surfaced DCF row is a point (fv_mid = index break-even), not a
    range bar — and no other lens renders a range now."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 80.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 120.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":        {"fv_low": 90.0, "fv_mid": 100.0, "fv_high": 110.0},
            "multiples":  {"fv_low": 70.0, "fv_mid": 95.0,  "fv_high": 130.0},
            "historical": {"fv_low": 95.0, "fv_mid": 105.0, "fv_high": 115.0},
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    # Exactly one point marker — the DCF row — and no range fills (DCF is the
    # only surfaced lens and it renders as a point).
    assert html.count('class="ff-point"') == 1
    assert html.count('class="ff-range"') == 0
    assert "index break-even" in html
    assert "$100" in html


def test_render_football_field_dcf_missing_falls_back_to_skipped():
    """DCF lens absent (None) → no point; the row greys out like any missing
    lens instead of crashing."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 90.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 110.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":        None,
            "multiples":  {"fv_low": 70.0, "fv_mid": 95.0, "fv_high": 130.0},
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    assert 'class="ff-point"' not in html
    assert "(skipped)" in html


def test_render_football_field_handles_no_summary():
    """Empty/None summary → returns a placeholder (no crash)."""
    assert streamlit_app._render_football_field(None, theme=_theme_stub()) != ""
    assert streamlit_app._render_football_field({}, theme=_theme_stub()) != ""


def _ff_summary_stale_price():
    """Summary whose stored stock_price ($320.50) is a stale snapshot from the
    last valuation refresh — the live price has since moved."""
    return {
        "stock_price": 320.50,
        "weighted_fv_low": 300.0,
        "weighted_fv_mid": 340.0,
        "weighted_fv_high": 380.0,
        "buy_price": 300.0,
        "lenses": {
            "dcf":        {"fv_low": 300.0, "fv_mid": 330.0, "fv_high": 360.0},
            "multiples":  {"fv_low": 320.0, "fv_mid": 350.0, "fv_high": 380.0},
        },
    }


def test_render_football_field_price_marker_uses_live_price():
    """Regression: the Price marker must track the LIVE price, not the frozen
    valuation_summary['stock_price'] snapshot. Given a stale snapshot of
    $320.50 and a live price of $348.97, the marker shows the live price."""
    summary = _ff_summary_stale_price()
    html = streamlit_app._render_football_field(
        summary, theme=_theme_stub(), live_price=348.97
    )
    assert "Price $348.97" in html
    assert "Price $320.50" not in html


def test_render_football_field_falls_back_to_summary_price_without_live():
    """No usable live price (None or 0) → fall back to the stored snapshot so
    the marker never disappears."""
    summary = _ff_summary_stale_price()
    for missing in (None, 0.0):
        html = streamlit_app._render_football_field(
            summary, theme=_theme_stub(), live_price=missing
        )
        assert "Price $320.50" in html


# ── FCF Yield cell ─────────────────────────────────────────────────────────
# Regression guard: the watchlist blanked FCF Yield for tickers that had data,
# because a failed SEC fetch was cached as an empty result for 24h. These pin
# the arithmetic; tests/test_edgar_fetch_resilience.py pins the fetch layer.

def test_fcf_yield_per_share_uses_price():
    # fcf in $M, shares a RAW count — the units fetch_fundamentals returns.
    fund = {"fcf": [1000.0, 2000.0], "shares": [100_000_000, 200_000_000]}
    # 2000 $M / 200M shares = $10/share; at $250 → 4%
    assert streamlit_app._latest_fcf_yield(fund, None, 250.0) == pytest.approx(0.04)


def test_fcf_yield_reads_shares_as_a_raw_count():
    """NTDOY: FCF $1,603M over 4.61bn shares is $0.35 a share, 2.6% at $13.18.

    Its 2,638,257% row was a fundamentals *override* entered in millions, not a
    unit bug in this function — and "correcting" the function to match the bad
    override put every EDGAR-derived yield at 0.0%.
    """
    fund = {"fcf": [1603.0], "shares": [4_610_000_000]}
    assert streamlit_app._latest_fcf_yield(fund, None, 13.18) == pytest.approx(0.0264, abs=1e-4)


def test_fcf_yield_pairs_fcf_and_shares_from_the_same_year():
    """The latest year reports FCF but no share count. Reaching back for an
    older year's share count would divide FY2 FCF by FY1 shares."""
    fund = {"fcf": [1000.0, 2000.0], "shares": [100_000_000, None]}
    # Must not return (2000e6/100e6)/250 = 8%. Falls back to FCF / market cap.
    assert streamlit_app._latest_fcf_yield(fund, 50_000.0, 250.0) == pytest.approx(0.04)


def test_fcf_yield_falls_back_to_market_cap_without_shares():
    """Visa never tags a share count in XBRL."""
    fund = {"fcf": [21_577.0], "shares": []}
    assert streamlit_app._latest_fcf_yield(fund, 595_014.0, 320.5) == pytest.approx(0.0363, abs=1e-4)


def test_fcf_yield_falls_back_to_market_cap_when_price_missing():
    fund = {"fcf": [1000.0], "shares": [100_000_000]}
    assert streamlit_app._latest_fcf_yield(fund, 25_000.0, 0.0) == pytest.approx(0.04)


def test_fcf_yield_skips_trailing_none_fcf_years():
    fund = {"fcf": [1000.0, None], "shares": [100_000_000, 100_000_000]}
    assert streamlit_app._latest_fcf_yield(fund, None, 100.0) == pytest.approx(0.10)


def test_fcf_yield_none_when_no_fcf_at_all():
    assert streamlit_app._latest_fcf_yield({"fcf": [], "shares": []}, 1000.0, 50.0) is None
    assert streamlit_app._latest_fcf_yield({"fcf": [None]}, 1000.0, 50.0) is None


def test_fcf_yield_none_when_nothing_to_scale_by():
    fund = {"fcf": [1000.0], "shares": [None]}
    assert streamlit_app._latest_fcf_yield(fund, None, 100.0) is None
    assert streamlit_app._latest_fcf_yield(fund, 0, 0) is None


def test_fcf_yield_empty_fundamentals_from_failed_fetch():
    """A failed EDGAR fetch yields {} — must be None, and the caller renders a
    warning glyph rather than "—", which would claim the filer has no FCF."""
    assert streamlit_app._latest_fcf_yield({}, 500_000.0, 100.0) is None


def test_fcf_yield_negative_fcf_is_reported_not_swallowed():
    fund = {"fcf": [-500.0], "shares": [100_000_000]}
    assert streamlit_app._latest_fcf_yield(fund, None, 100.0) == pytest.approx(-0.05)


# ── WACC persistence: only store per-year / terminal WACC when overridden ──
# Guards against re-freezing an auto-computed discount rate into the config,
# which caused the detail page to drift from the watchlist multi-lens after the
# opportunity_cost switch.

def test_wacc_persistence_removes_default_per_year():
    """Per-year WACC all equal to the live compute_wacc default → key removed
    so the rate is taken live, and the caller learns it was not overridden."""
    cfg = {"wacc_per_year": [0.0893] * 10}   # a previously-frozen value
    default = 0.089269
    wacc_over, tv_over = streamlit_app._apply_wacc_persistence(
        cfg, [default] * 10, default, default)
    assert "wacc_per_year" not in cfg
    assert wacc_over is False
    assert tv_over is False


def test_wacc_persistence_default_at_display_rounding_still_removed():
    """Widget returns the value rounded to display precision (8.93% ↔ 0.0893)
    while the live default is 0.089269 — must be treated as 'not overridden'."""
    cfg = {}
    default = 0.089269
    wacc_over, _ = streamlit_app._apply_wacc_persistence(
        cfg, [0.0893] * 10, 0.0893, default)
    assert "wacc_per_year" not in cfg
    assert wacc_over is False


def test_wacc_persistence_stores_overridden_per_year():
    """A genuine per-year edit (differs at 2-decimal-percent precision) is
    persisted verbatim."""
    cfg = {}
    default = 0.089269
    edited = [default] * 10
    edited[3] = 0.095          # 9.50% vs 8.93% default
    wacc_over, _ = streamlit_app._apply_wacc_persistence(
        cfg, edited, default, default)
    assert cfg["wacc_per_year"] == edited
    assert wacc_over is True


def test_wacc_persistence_terminal_default_removed_override_stored():
    cfg_default = {"terminal_wacc": 0.0798}
    d = 0.089269
    _, tv_over = streamlit_app._apply_wacc_persistence(
        cfg_default, [d] * 10, d, d)
    assert "terminal_wacc" not in cfg_default
    assert tv_over is False

    cfg_edit = {}
    _, tv_over2 = streamlit_app._apply_wacc_persistence(
        cfg_edit, [d] * 10, 0.10, d)      # terminal 10% vs 8.93% default
    assert cfg_edit["terminal_wacc"] == 0.10
    assert tv_over2 is True


def test_wacc_persistence_self_heals_stale_frozen_value():
    """A config still carrying a stale frozen WACC equal to today's live default
    gets the key stripped on the next editor render."""
    cfg = {"wacc_per_year": [0.0798] * 10, "terminal_wacc": 0.0798}
    d = 0.089269
    streamlit_app._apply_wacc_persistence(cfg, [d] * 10, d, d)
    assert "wacc_per_year" not in cfg
    assert "terminal_wacc" not in cfg


# ── DCF editor: the stored sector beta is a deliberate input ────────────────────

_DAM = {"Shoe": 0.93, "Machinery": 0.87, "Semiconductor": 1.49}


def test_sector_beta_default_keeps_the_stored_beta_when_sector_unchanged():
    """The editor used to overwrite the config's beta with Damodaran's current
    figure on every render, even untouched. DECK was authored at 1.14 for
    "Shoe"; Damodaran now publishes 0.93, so simply opening the ticker page
    showed 8.75% instead of the intended 9.68% — and persisted it on save."""
    assert streamlit_app._sector_beta_default("Shoe", 1.14, "Shoe", _DAM) == 1.14


def test_sector_beta_default_adopts_the_new_sector_beta_when_changed():
    """Picking a different sector is an explicit act, so its live beta is the
    sensible starting point."""
    assert streamlit_app._sector_beta_default(
        "Shoe", 1.14, "Semiconductor", _DAM) == 1.49


def test_sector_beta_default_falls_back_when_new_sector_is_unknown():
    """A hand-typed sector Damodaran doesn't publish keeps the stored beta
    rather than silently dropping to a placeholder."""
    assert streamlit_app._sector_beta_default(
        "Shoe", 1.14, "Online Travel Services", _DAM) == 1.14


# ── The editor's summary must match what the engine runs on ───────────────────

def test_effective_stc_prefers_the_per_year_list_over_the_scalar():
    """The 'Used in DCF' line read sales_to_capital straight from the config,
    so NVDA displayed 8.00 while the projection ran on stc_per_year's 5.0."""
    cfg = {"sales_to_capital": 8.0, "stc_per_year": [5.0] * 10,
           "terminal_stc": 5.0, "revenue_growth": [0.05] * 10}
    per_year, terminal = streamlit_app._effective_stc(cfg)
    assert per_year == [5.0] * 10
    assert terminal == 5.0


def test_effective_stc_expands_a_lone_scalar():
    cfg = {"sales_to_capital": 3.0, "revenue_growth": [0.05] * 10}
    per_year, terminal = streamlit_app._effective_stc(cfg)
    assert per_year == [3.0] * 10
    assert terminal == 3.0


def test_effective_stc_matches_what_the_engine_reports_it_used():
    """Pin the display to the engine. If precedence ever changes in
    dcf_calculator, this fails rather than letting the page drift back into
    showing a number the projection never saw."""
    import dcf_calculator
    cfg = {
        "discount_mode": "capm", "risk_free_rate": 0.046, "erp": 0.045,
        "tax_rate": 0.23, "equity_market_value": 10000, "debt_market_value": 0,
        "credit_spread": 0.004, "sector_betas": [("S", 1.0, 1.0)],
        "base_revenue": 1000, "revenue_growth": [0.05] * 5,
        "op_margins": [0.20] * 5, "terminal_growth": 0.025, "terminal_margin": 0.20,
        "sales_to_capital": 8.0, "stc_per_year": [1.5, 1.6], "terminal_stc": 2.4,
        "cash_bridge": 100, "shares_outstanding": 100, "margin_of_safety": 0.2,
        "stock_price": 50.0,
    }
    per_year, terminal = streamlit_app._effective_stc(cfg)
    out = dcf_calculator.compute_intrinsic_value(cfg)
    assert per_year == out["stc_used"]
    assert terminal == out["terminal_stc_used"]


class TestResolveWatchlistPrice:
    """A missing Yahoo quote must never reach the table as 0.

    Yahoo throttles Streamlit Cloud's datacenter IP, so quotes go missing in
    normal operation. Rendering that as $0.00 does not merely blank the price
    column — upside, P/E and FCF-yield are all derived from it and silently
    collapse too, which is what made the bug hard to spot.
    """

    def test_live_price_wins(self):
        cfg = {"stock_price": 100.0}
        price, stale = streamlit_app._resolve_watchlist_price(cfg, 123.45)
        assert price == 123.45
        assert stale is False

    def test_falls_back_to_stored_price(self):
        cfg = {"stock_price": 100.0}
        price, stale = streamlit_app._resolve_watchlist_price(cfg, None)
        assert price == 100.0, "must not render a missing quote as 0"
        assert stale is True

    def test_zero_live_price_is_treated_as_missing(self):
        cfg = {"stock_price": 100.0}
        price, stale = streamlit_app._resolve_watchlist_price(cfg, 0.0)
        assert price == 100.0
        assert stale is True

    def test_no_stored_price_either(self):
        price, stale = streamlit_app._resolve_watchlist_price({}, None)
        assert price == 0.0
        assert stale is True

    def test_stored_price_of_zero_is_not_a_price(self):
        price, stale = streamlit_app._resolve_watchlist_price(
            {"stock_price": 0.0}, None)
        assert price == 0.0
        assert stale is True
