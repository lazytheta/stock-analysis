"""Tests for multi-lens fair value (Phase 1)."""
from unittest.mock import MagicMock  # noqa: F401 — used by later tasks in this file

import pytest  # noqa: F401 — used by later tasks in this file


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
