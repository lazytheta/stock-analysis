"""Reverse DCF: own-history yardstick and growth solved at a fixed margin."""

import dcf_calculator as d


def test_own_history_cagr_and_margin_band():
    fund = {"years": list(range(2016, 2026)),
            "revenue": [100 * 1.1 ** i for i in range(10)],
            "operating_income": [10 * 1.1 ** i for i in range(10)]}
    h = d.own_history(fund)
    assert abs(h["rev_cagr_5y"] - 0.10) < 1e-9
    assert abs(h["margin_median"] - 0.10) < 1e-9
    assert h["first_year"] == 2016 and h["last_year"] == 2025


def test_own_history_tolerates_missing():
    assert d.own_history(None)["rev_cagr_5y"] is None
    assert d.own_history({"revenue": [1, 2]})["margin_median"] is None
