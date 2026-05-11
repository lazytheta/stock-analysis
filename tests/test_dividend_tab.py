"""Tests for the per-ticker Dividend tab helpers in streamlit_app."""

import math

import pytest


def test_ddm_at_basic_math():
    """Two-stage DDM: 5y explicit growth + Gordon terminal, discounted at ke.

    Hand-computed for ttm=$4.00, g=0.06, ke=0.08, g_term=0.025, stage1_years=5:
      Year 1: D=4.24, PV=4.24/1.08 = 3.926
      Year 2: D=4.4944, PV=4.4944/1.08^2 = 3.852
      Year 3: D=4.7641, PV=4.7641/1.08^3 = 3.780
      Year 4: D=5.0499, PV=5.0499/1.08^4 = 3.708
      Year 5: D=5.3529, PV=5.3529/1.08^5 = 3.640
      Stage 1 PV ≈ 18.906
      Terminal: D5 * 1.025 / (0.08 - 0.025) = 5.486/0.055 = 99.751
      PV(Terminal) = 99.751 / 1.08^5 ≈ 67.872
      DDM FV ≈ 86.78
    """
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.06, ke=0.08, g_term=0.025, stage1_years=5
    )
    assert fv == pytest.approx(86.78, abs=0.5)


def test_ddm_at_returns_inf_when_ke_le_g_term():
    """ke ≤ g_term → Gordon perpetuity blows up; return inf so the matrix
    can render '—' for these cells."""
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.06, ke=0.020, g_term=0.025, stage1_years=5
    )
    assert math.isinf(fv)


def test_ddm_at_zero_growth_still_computes():
    """g=0 is mathematically valid (perpetuity at current dividend); should
    NOT blow up, just give a smaller FV."""
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.0, ke=0.08, g_term=0.025, stage1_years=5
    )
    assert fv > 0
    assert math.isfinite(fv)


def test_ddm_at_high_growth_above_lens_cap():
    """The matrix is exploratory — user can widen the slider beyond the
    lens's 15% cap. _ddm_at must compute regardless (no cap)."""
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.20, ke=0.10, g_term=0.025, stage1_years=5
    )
    assert fv > 0
    assert math.isfinite(fv)


def test_ddm_at_matches_compute_dividend_lens_baseline():
    """At the baseline (g=cfg.dividend_5y_cagr, ke=compute_cost_of_equity),
    _ddm_at must equal compute_dividend_lens's stored ddm_fv."""
    import streamlit_app
    import valuation_lenses
    import dcf_calculator

    cfg = {
        "stock_price": 100.0,
        "equity_market_value": 1000, "debt_market_value": 100,
        "sector_betas": [("Sector", 1.0, 1.0)],
        "tax_rate": 0.21, "risk_free_rate": 0.04, "erp": 0.05,
        "credit_spread": 0.01, "terminal_growth": 0.025,
        "valuation_inputs": {
            "ttm_dividend": 4.00,
            "dividend_5y_cagr": 0.06,
            "median_5y_yield": 0.030,
        },
    }
    lens = valuation_lenses.compute_dividend_lens(cfg)
    assert lens is not None
    expected_ddm_fv = lens["details"]["ddm_fv"]

    ke = dcf_calculator.compute_cost_of_equity(cfg)
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.06, ke=ke, g_term=0.025, stage1_years=5
    )
    assert fv == pytest.approx(expected_ddm_fv, abs=0.01)
