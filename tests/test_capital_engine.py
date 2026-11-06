"""Capital engine (scorecard_utils.capital_engine): ROIIC with a one-year
capital lag, reinvestment rate, flags, and its place in get_fundamentals and
the prompts. Fixtures are the real EDGAR series for META (FY2016-FY2025) and
DECK (FY2017-FY2026) as get_fundamentals returned them on 2026-11-05."""
import json

import pytest

import growth_page
from scorecard_utils import capital_engine, capital_engine_text, window_tax_rate

META = {
    "years": list(range(2016, 2026)),
    "operating_income": [12427, 20203, 24913, 23986, 32671, 46753, 28944, 46751, 69380, 83276],
    "tax_provision": [2301, 4660, 3249, 6327, 4034, 7914, 5619, 8330, 8303, 25474],
    "pretax_income": [12518, 20594, 25361, 24812, 33180, 47284, 28819, 47428, 70663, 85932],
    "total_assets": [64961, 84524, 97334, 133376, 159316, 165987, 185727, 229623, 276054, 366021],
    "current_liabilities": [2875, 3760, 7017, 15053, 14981, 21135, 27026, 31960, 33596, 41836],
    "cash": [8903, 8079, 10019, 19079, 17576, 16601, 14681, 41862, 43889, 35873],
    "short_term_investments": [20546, 33632, 31095, 35776, 44378, 31397, 26057, 23541, 33926, 45719],
}

DECK = {
    "years": list(range(2017, 2027)),
    "operating_income": [-2, 223, 327, 338, 504, 565, 653, 928, 1179, 1263],
    "tax_provision": [-13, 106, 65, 65, 119, 113, 149, 219, 277, 302],
    "pretax_income": [-7, 221, None, None, None, None, None, None, None, None],
    "total_assets": [1192, 1264, 1427, 1765, 2168, 2332, 2556, 3136, 3570, 3688],
    "current_liabilities": [159, 189, 251, 301, 468, 542, 497, 720, 770, 804],
    "cash": [292, 430, 590, 649, 1089, 844, 982, 1502, 1889, 1907],
    "short_term_investments": [None] * 10,
}


def _last(fund, n):
    return {k: v[-n:] for k, v in fund.items()}


def test_meta_window_ending_fy2025():
    ce = capital_engine(META)
    assert ce["window_years"] == 5
    assert (ce["start_year"], ce["end_year"]) == (2020, 2025)
    assert (ce["capital_start_year"], ce["capital_end_year"]) == (2019, 2024)
    assert 35 <= ce["roiic_pct"] <= 45
    assert 50 <= ce["reinvestment_rate_window_pct"] <= 58
    assert ce["reinvestment_rate_latest_pct"] > 100
    assert ce["tax_rate_fallback"] is False
    f = ce["flags"]
    assert f["lag_hides_recent_capex"] is True       # IC +47% in FY2025
    assert f["external_funding"] is True
    assert f["small_delta_capital"] is False
    assert f["negative_delta"] is False
    assert f["growth_mismatch"] is False             # 22.5% implied vs 20.6% actual
    assert [x["end_year"] for x in ce["trend"]] == [2025, 2024, 2023, 2022]


def test_deck_window_ending_fy2026():
    ce = capital_engine(DECK)
    assert ce["end_year"] == 2026 and ce["window_years"] == 5
    assert ce["roiic_pct"] > 200                     # hundreds of %
    assert 2 <= ce["reinvestment_rate_window_pct"] <= 5
    assert ce["flags"]["small_delta_capital"] is True
    # pretax missing for every window year → the 21% fallback, said so
    assert ce["tax_rate_pct"] == pytest.approx(21.0)
    assert ce["tax_rate_fallback"] is True


def test_identity_implied_growth_is_rr_times_roiic():
    ce = capital_engine(META)
    assert ce["implied_growth_pct"] == pytest.approx(
        ce["reinvestment_rate_window_pct"] * ce["roiic_pct"] / 100, abs=0.05)


def test_six_years_falls_back_to_three_year_window():
    ce = capital_engine(_last(META, 6))
    assert ce["window_years"] == 3
    assert ce["flags"]["short_window"] is True
    assert capital_engine(META)["flags"]["short_window"] is False
    assert (ce["start_year"], ce["end_year"]) == (2022, 2025)
    assert (ce["capital_start_year"], ce["capital_end_year"]) == (2021, 2024)


def test_fewer_than_five_years_is_none_not_a_crash():
    assert capital_engine(_last(META, 4)) is None
    assert capital_engine({"years": []}) is None
    assert capital_engine({}) is None
    assert "unavailable" in capital_engine_text(None)


def test_missing_input_is_never_estimated():
    fund = {k: list(v) for k, v in META.items()}
    fund["total_assets"][3] = None                  # IC FY2019 = capital start of the 5y window
    ce = capital_engine(fund)
    assert ce["window_years"] == 3                  # the 5y window is not guessed at


def test_missing_cash_is_not_read_as_zero():
    fund = {k: list(v) for k, v in META.items()}
    fund["cash"][3] = None                          # FY2019, capital start of the 5y window
    assert capital_engine(fund)["window_years"] == 3


def test_shrinking_capital_nulls_roiic_but_keeps_the_rest():
    fund = {k: list(v) for k, v in DECK.items()}
    fund["total_assets"][8] = 3000                  # IC FY2025 below IC FY2020
    ce = capital_engine(fund)
    assert ce["roiic_pct"] is None
    assert ce["flags"]["negative_delta"] is True
    assert ce["reinvestment_rate_window_pct"] < 0


def test_window_tax_rate_clamped_and_fallback():
    assert window_tax_rate({"tax_provision": [50], "pretax_income": [100]}, [0]) == (0.35, False)
    assert window_tax_rate({"tax_provision": [-5], "pretax_income": [100]}, [0]) == (0.0, False)
    assert window_tax_rate({"tax_provision": [5], "pretax_income": [-100]}, [0]) == (0.21, True)
    # years missing either side drop out of both sums
    assert window_tax_rate({"tax_provision": [20, 99], "pretax_income": [100, None]},
                           [0, 1]) == (pytest.approx(0.20), False)


# ── get_fundamentals ──────────────────────────────────────────────────────

def _patch(monkeypatch, cfg, fund):
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(mcp_server.config_store, "load_config", lambda c, t, user_id=None: dict(cfg))
    monkeypatch.setattr(mcp_server.gather_data, "fetch_fundamentals",
                        lambda ticker, n_years=10, manual_currency=None:
                        {k: list(v) for k, v in fund.items()})
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")
    return mcp_server


def test_get_fundamentals_headline_carries_capital_engine(monkeypatch):
    mcp_server = _patch(monkeypatch, {"ticker": "META"}, META)
    hl = json.loads(mcp_server._get_fundamentals_impl("META"))["headline"]
    assert hl["capital_engine"]["roiic_pct"] == hl["incremental_roic_pct"]
    assert 35 <= hl["incremental_roic_pct"] <= 45
    # the old 3-delta figure lives on under its own name (≈28% for META)
    assert hl["incremental_roic_unlagged_pct"] == pytest.approx(28.3, abs=1.0)


def test_overrides_are_applied_before_the_calculation(monkeypatch):
    base = _patch(monkeypatch, {"ticker": "META"}, META)
    plain = json.loads(base._get_fundamentals_impl("META"))["headline"]["capital_engine"]
    cfg = {"ticker": "META",
           "fundamentals_overrides": {"operating_income": {"2025": 100000.0}}}
    mcp_server = _patch(monkeypatch, cfg, META)
    hl = json.loads(mcp_server._get_fundamentals_impl("META"))["headline"]
    assert hl["capital_engine"]["roiic_pct"] > plain["roiic_pct"]
    assert hl["capital_engine"]["nopat_latest_m"] == pytest.approx(
        100000 * (1 - hl["capital_engine"]["tax_rate_pct"] / 100), rel=1e-3)


def test_short_history_headline_is_null(monkeypatch):
    mcp_server = _patch(monkeypatch, {"ticker": "NEW"}, _last(META, 4))
    hl = json.loads(mcp_server._get_fundamentals_impl("NEW"))["headline"]
    assert hl["capital_engine"] is None
    assert hl["incremental_roic_pct"] is None


# ── prompts ───────────────────────────────────────────────────────────────

def test_prompt_placeholder_is_filled():
    import mcp_server
    block = capital_engine_text(capital_engine(META))
    out = mcp_server._fill_prompt_template("{company} ({ticker})\n{capital_engine}",
                                           "meta", "Meta", {}, block)
    assert "ROIIC (return on new capital): 40.5%" in out
    assert "lag_hides_recent_capex" in out and "external_funding" in out
    assert "{capital_engine}" not in mcp_server._fill_prompt_template(
        "{ticker} {capital_engine}", "x", "X", {})


def test_default_prompts_carry_the_placeholder():
    import management_cards
    import prescan_prompts
    ltp = next(p for p in prescan_prompts.DEFAULT_AI_PROMPTS if p["title"] == "Long-Term Potential")
    assert "{capital_engine}" in ltp["prompt"]
    assert "{capital_engine}" in management_cards.PROMPT
    key_metrics = next(p for p in prescan_prompts.DEFAULT_AI_PROMPTS if p["title"] == "Key Metrics")
    assert "{capital_engine}" not in key_metrics["prompt"]


# ── UI ────────────────────────────────────────────────────────────────────

THEME = {"text_muted": "#888"}


def test_section_greys_out_noisy_roiic():
    html = growth_page.capital_engine_section_html(capital_engine(DECK), 63.4, "ROCE", THEME)
    assert "Capital engine" in html and 'class="gr-dim"' in html
    assert "Capital barely moved" in html


def test_section_without_engine_shows_notice():
    html = growth_page.capital_engine_section_html(None, 40.0, "ROCE", THEME)
    assert growth_page.ENGINE_NOTICE in html


def test_short_window_is_indicative_everywhere(monkeypatch):
    mcp_server = _patch(monkeypatch, {"ticker": "META"}, _last(META, 6))
    hl = json.loads(mcp_server._get_fundamentals_impl("META"))["headline"]
    assert hl["capital_engine"]["roiic_pct"] is not None   # shown, never hidden
    assert hl["incremental_roic_pct"] is None              # but not the headline
    text = capital_engine_text(hl["capital_engine"])
    assert "short_window" in text and "indicative" in text
    html = growth_page.capital_engine_section_html(hl["capital_engine"], 40.0, "ROCE", THEME)
    assert 'class="gr-dim"' in html and "Only three years" in html


def test_glance_short_window_tile_is_greyed():
    import overview_metrics
    import overview_page as op
    g = overview_metrics.glance(_last(META, 6), None)
    assert g["roiic_short"] is True and g["engine_window"] == 3
    body = op.company_section_html(None, None, g)
    assert "3-year window — indicative" in body and "ov-gval ov-dim" in body
