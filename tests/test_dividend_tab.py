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


def test_dividend_conclusion_undervalued():
    """lens_mid >= price × 1.10 → undervaluation wording."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=181.0, price=155.0)
    assert "$181" in s
    assert "$155" in s
    assert "undervaluation" in s.lower()
    assert "%" in s


def test_dividend_conclusion_overvalued():
    """lens_mid <= price × 0.90 → overvaluation wording."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=181.0, price=220.0)
    assert "$181" in s
    assert "$220" in s
    assert "overvaluation" in s.lower()


def test_dividend_conclusion_fairly_priced():
    """0.90 × price ≤ lens_mid ≤ 1.10 × price → fairly priced wording."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=181.0, price=182.0)
    assert "$181" in s
    assert "$182" in s
    assert "fairly priced" in s.lower()


def test_dividend_conclusion_boundary_at_10pct_above():
    """Exactly +10% → still within fairly-priced band (inclusive)."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=110.0, price=100.0)
    assert "fairly priced" in s.lower()


def test_dividend_conclusion_just_above_10pct():
    """Just past +10% → undervaluation."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=110.01, price=100.0)
    assert "undervaluation" in s.lower()


def test_dividend_conclusion_threshold_constant_is_10pct():
    """The threshold lives as a module-level constant for tunability."""
    import streamlit_app
    assert streamlit_app._DIVIDEND_FAIR_THRESHOLD == 0.10


def _matrix_theme():
    """Minimal theme dict for matrix tests."""
    return {
        "border_medium": "#ccc",
        "card": "#fafafa",
        "text": "#111",
        "text_muted": "#666",
        "accent": "#6e8a76",
    }


def test_render_dividend_sensitivity_matrix_dimensions():
    """g_range has 5 steps, ke_range has 3 steps → matrix has 5 rows + 3 data cols."""
    import streamlit_app
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.08, 0.01),
        ke_range=(0.07, 0.09, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.06,
        baseline_ke=0.08,
        theme=_matrix_theme(),
    )
    assert html.count("<tr>") == 6
    assert html.count("<td") >= 20


def test_render_dividend_sensitivity_matrix_baseline_highlighted():
    """The cell at (baseline_g, baseline_ke) gets a highlight class/style."""
    import re
    import streamlit_app
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.08, 0.01),
        ke_range=(0.07, 0.09, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.06,
        baseline_ke=0.08,
        theme=_matrix_theme(),
    )
    cells = re.findall(r"<td[^>]*>.*?</td>", html, flags=re.DOTALL)
    highlighted = [
        c for c in cells
        if "#6e8a76" in c and ("bold" in c or "font-weight:700" in c)
    ]
    assert len(highlighted) == 1


def test_render_dividend_sensitivity_matrix_degenerate_cell_renders_dash():
    """Cells where ke ≤ g_term render '—' (Gordon doesn't converge)."""
    import streamlit_app
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.06, 0.01),
        ke_range=(0.01, 0.04, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.05,
        baseline_ke=0.03,
        theme=_matrix_theme(),
    )
    assert "—" in html


def test_render_dividend_sensitivity_matrix_uses_theme_colors():
    """Theme dict's colors flow into the rendered HTML (not hardcoded)."""
    import streamlit_app
    theme = {
        "border_medium": "#aabbcc",
        "card": "#112233",
        "text": "#ffffff",
        "text_muted": "#888888",
        "accent": "#ff00aa",
    }
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.06, 0.01),
        ke_range=(0.07, 0.09, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.05,
        baseline_ke=0.08,
        theme=theme,
    )
    assert "#aabbcc" in html
    assert "#112233" in html
    assert "#888888" in html
    assert "#ff00aa" in html


def test_render_dividend_sensitivity_matrix_cell_values_match_ddm_at():
    """Each cell's $ value is _ddm_at at that (g, ke). Sanity-check one cell."""
    import streamlit_app
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.05, 0.06, 0.01),
        ke_range=(0.08, 0.09, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.05,
        baseline_ke=0.08,
        theme=_matrix_theme(),
    )
    expected = streamlit_app._ddm_at(
        ttm=4.0, g=0.06, ke=0.08, g_term=0.025, stage1_years=5
    )
    expected_str = (
        f"${expected:.0f}" if abs(expected) >= 100 else f"${expected:.2f}"
    )
    assert expected_str in html
