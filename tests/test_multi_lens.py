"""Tests for multi-lens fair value (Phase 1)."""
from unittest.mock import MagicMock

import pytest


def make_cfg(**overrides):
    cfg = {
        "company": "Test Co",
        "ticker": "TEST",
        "stock_price": 100.0,
        "equity_market_value": 100_000,
        "debt_market_value": 10_000,
        "risk_free_rate": 0.04,
        "erp": 0.05,
        "credit_spread": 0.01,
        "tax_rate": 0.21,
        "sector_betas": [("Sector", 1.0, 1.0)],
        "base_revenue": 50_000,
        "revenue_growth": [0.05] * 5,
        "op_margins": [0.20] * 5,
        "terminal_growth": 0.025,
        "terminal_margin": 0.18,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.02,
        "shares_outstanding": 1_000,
        "buyback_rate": 0.0,
        "margin_of_safety": 0.20,
        "cash_bridge": 5_000,
        "securities": 0,
        "bull_growth_adj": 0.02,
        "bear_growth_adj": -0.04,
        "bull_margin_adj": 0.02,
        "bear_margin_adj": -0.02,
        "peers": [],
    }
    cfg.update(overrides)
    return cfg


def make_peer(**overrides):
    p = {
        "ticker": "PEER1",
        "name": "Peer Co",
        "ev_revenue": 5.0,
        "ev_ebitda": 12.0,
        "pe": 20.0,
        "fwd_pe": 18.0,
        "op_margin": 0.20,
        "rev_growth": 0.05,
        "roic": 0.15,
    }
    p.update(overrides)
    return p


SAMPLE_VALUATION_INPUTS = {
    "forward_eps": 5.0,
    "historical_fwd_pe": 20.0,
    "ttm_ebitda": 12_000.0,
    "target_dividend_yield": 0.02,
    "current_dividend": 2.0,
    "expected_dividend_growth": 0.07,
}

from scorecard_utils import parse_scorecard, parse_scorecard_json


# ---------------------------------------------------------------- scorecard

def test_parse_scorecard_json_fenced():
    raw = """
Some preamble.

```json
{"verdict": "deep_dive", "phase": {"number": 5, "name": "Capital Return"}}
```

trailing text
"""
    assert parse_scorecard_json(raw) == {
        "verdict": "deep_dive",
        "phase": {"number": 5, "name": "Capital Return"},
    }


def test_parse_scorecard_json_unfenced():
    raw = '{"verdict": "pass"}'
    assert parse_scorecard_json(raw) == {"verdict": "pass"}


def test_parse_scorecard_json_empty():
    assert parse_scorecard_json("") is None
    assert parse_scorecard_json(None) is None


def test_parse_scorecard_returns_verdict_and_phase():
    ai_notes = {
        "Scorecard": '```json\n{"verdict":"revisit","phase":{"number":4,"name":"Op. Lev."}}\n```'
    }
    assert parse_scorecard(ai_notes) == {"verdict": "revisit", "phase": 4}


def test_parse_scorecard_no_section_returns_nones():
    assert parse_scorecard({}) == {"verdict": None, "phase": None}
    assert parse_scorecard({"Other": "x"}) == {"verdict": None, "phase": None}


def test_parse_scorecard_section_unparseable_returns_nones():
    assert parse_scorecard({"Scorecard": "not json"}) == {"verdict": None, "phase": None}


def test_parse_scorecard_compact_phase():
    """Plain-int and string-digit `phase` values should be extracted, not silently dropped."""
    int_form = {"Scorecard": '```json\n{"verdict":"pass","phase":3}\n```'}
    assert parse_scorecard(int_form) == {"verdict": "pass", "phase": 3}

    str_form = {"Scorecard": '```json\n{"verdict":"pass","phase":"4"}\n```'}
    assert parse_scorecard(str_form) == {"verdict": "pass", "phase": 4}


# ---------------------------------------------------------------- config preservation


def test_save_config_preserves_valuation_keys():
    """save_config must merge in valuation_inputs/valuation_summary/lens_weights
    from the existing DB row when the caller's cfg omits them."""
    import config_store

    existing = {
        "company": "X",
        "ai_notes": {"foo": "bar"},
        "peers": [{"ticker": "P"}],
        "valuation_inputs": {"forward_eps": 5.0},
        "valuation_summary": {"weighted_fv_mid": 80.0},
        "lens_weights": {"dcf": 0.5},
    }
    new_cfg = {"company": "X", "stock_price": 100}

    captured = {}

    def upsert(row):
        captured["row"] = row
        return MagicMock(execute=lambda: None)

    fake_table = MagicMock()
    fake_table.upsert = upsert

    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    # Patch load_config to return our existing row
    orig_load = config_store.load_config
    config_store.load_config = lambda c, t, user_id=None: existing
    try:
        config_store.save_config(fake_client, "TEST", new_cfg, user_id="u1")
    finally:
        config_store.load_config = orig_load

    saved = captured["row"]["config"]
    assert saved["valuation_inputs"] == {"forward_eps": 5.0}
    assert saved["valuation_summary"] == {"weighted_fv_mid": 80.0}
    assert saved["lens_weights"] == {"dcf": 0.5}
    assert saved["ai_notes"] == {"foo": "bar"}
    assert saved["peers"] == [{"ticker": "P"}]


# ---------------------------------------------------------------- valuation_lenses


import valuation_lenses


def test_default_lens_weights():
    assert valuation_lenses.DEFAULT_LENS_WEIGHTS == {
        "dcf": 0.30,
        "multiples": 0.40,
        "reverse_dcf": 0.10,
        "dividend": 0.00,
    }


def test_dividend_lens_returns_none():
    assert valuation_lenses.compute_dividend_lens(make_cfg()) is None


def test_dcf_lens_basic_returns_band_around_intrinsic():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=False)
    base = lens["details"]["base_intrinsic"]
    assert lens["fv_mid"] == pytest.approx(base, rel=1e-9)
    assert lens["fv_low"] == pytest.approx(base * 0.85, rel=1e-9)
    assert lens["fv_high"] == pytest.approx(base * 1.15, rel=1e-9)
    assert lens["details"]["scenarios"] is None
    assert lens["details"]["wacc"] > 0


def test_dcf_lens_basic_intrinsic_positive_for_sample_cfg():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg)
    assert lens["fv_mid"] > 0
    assert lens["fv_low"] < lens["fv_mid"] < lens["fv_high"]


def test_dcf_lens_scenario_grid_uses_bull_bear_adjustments():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=True)
    assert lens["details"]["scenarios"] is not None
    scenarios = lens["details"]["scenarios"]
    assert len(scenarios) == 16  # 4 growth offsets * 4 margin offsets
    base = lens["details"]["base_intrinsic"]
    assert lens["fv_mid"] == pytest.approx(base, rel=1e-9)
    assert lens["fv_low"] == min(scenarios)
    assert lens["fv_high"] == max(scenarios)
    assert lens["fv_low"] < lens["fv_high"]


def test_dcf_lens_scenario_grid_default_adjustments_when_missing():
    cfg = make_cfg()
    for key in ("bull_growth_adj", "bear_growth_adj",
                "bull_margin_adj", "bear_margin_adj"):
        cfg.pop(key, None)
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=True)
    assert len(lens["details"]["scenarios"]) == 16


def test_reverse_dcf_lens_anchors_at_stock_price():
    cfg = make_cfg(stock_price=100.0)
    lens = valuation_lenses.compute_reverse_dcf_lens(cfg)
    assert lens["fv_low"] == 100.0
    assert lens["fv_mid"] == 100.0
    assert lens["fv_high"] == 100.0
    assert "implied_growth" in lens["details"]
    assert "implied_margin" in lens["details"]
    assert isinstance(lens["details"]["implied_growth"], float)
