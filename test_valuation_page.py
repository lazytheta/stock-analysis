"""Valuation tab cards: only the cards a type uses; missing inputs are one line."""

import type_valuation as tv
import valuation_page as vp


def _light(**vt):
    return tv.evaluate({"type": "capital_light", **vt}, 409,
                       {"eps": 12.03, "pe_history": [30] * 10}, implied_return=0.072)


def test_priced_light_page_has_hero_main_and_control():
    html = vp.page_html(_light(achievable_growth=0.11), {"rationale": "FCF/NI 107%"},
                        {"weighted_fv_mid": 264, "buy_price": 300.75})
    assert "Not at this price" in html and "&#36;300.75" in html
    assert "Required EPS growth" in html and "Control test" in html
    assert "Why this type" in html and "DCF range" in html
    assert "Tangible book" not in html          # no other type's rows


def test_missing_input_is_one_line_not_dashes():
    html = vp.page_html(_light(), {"rationale": "x"}, {})
    assert "No buy price yet: Achievable growth" in html
    assert "Still to set: Achievable growth" in html


def test_no_type_falls_back_to_dcf_text():
    html = vp.page_html(None, None, {"buy_price": 211.55})
    assert "No company type set yet" in html and "211.55" in html


def test_buyable_says_yes():
    res = tv.evaluate({"type": "asset", "asset_value_per_share": 400}, 256, {})
    assert "Yes" in vp.hero_html(res)


def test_no_raw_dollar_reaches_streamlit_markdown():
    """Two raw $ signs in one st.markdown render as LaTeX."""
    html = vp.page_html(_light(achievable_growth=0.11), {"rationale": "x"},
                        {"weighted_fv_mid": 264, "buy_price": 300.75})
    assert "$" not in html
