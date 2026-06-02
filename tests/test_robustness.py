import json

import robustness


def test_band_for_roce_gate():
    # never green under 20% (the Prasad gate)
    assert robustness.band_for_roce(25.0) == "robust"
    assert robustness.band_for_roce(20.0) == "robust"
    assert robustness.band_for_roce(19.9) == "mid"
    assert robustness.band_for_roce(12.0) == "mid"
    assert robustness.band_for_roce(11.9) == "fragile"
    assert robustness.band_for_roce(None) == "fragile"


def test_band_for_net_debt():
    assert robustness.band_for_net_debt(-0.5) == "robust"   # net cash
    assert robustness.band_for_net_debt(1.0) == "robust"
    assert robustness.band_for_net_debt(1.5) == "mid"
    assert robustness.band_for_net_debt(2.0) == "mid"
    assert robustness.band_for_net_debt(2.5) == "fragile"
    # EBITDA ratio unknown -> fall back to net-debt sign
    assert robustness.band_for_net_debt(None, net_debt_m=-100.0) == "robust"
    assert robustness.band_for_net_debt(None, net_debt_m=500.0) == "mid"
    assert robustness.band_for_net_debt(None, net_debt_m=None) == "mid"


def _axes(**bands):
    """Build an axes dict; unspecified axes default to 'robust'."""
    out = {}
    for key, _, _, src in robustness.AXES:
        out[key] = {"band": bands.get(key, "robust"), "source": src}
    return out


def test_verdict_all_robust():
    v = robustness.derive_verdict(_axes())
    assert v["verdict"] == "robust"
    assert v["verdict_mapped"] == "deep_dive"


def test_verdict_management_red_is_fragile():
    v = robustness.derive_verdict(_axes(management="fragile"))
    assert v["verdict"] == "fragile"
    assert v["verdict_mapped"] == "pass"


def test_verdict_roce_gate_caps_at_borderline():
    # ROCE mid (12-20%) with everything else green -> cannot be robust
    v = robustness.derive_verdict(_axes(roce="mid"))
    assert v["verdict"] == "borderline"
    assert v["verdict_mapped"] == "revisit"
    assert "gate" in v["verdict_reason"].lower()


def test_verdict_net_debt_amber_is_borderline():
    v = robustness.derive_verdict(_axes(net_debt="mid"))
    assert v["verdict"] == "borderline"


def test_verdict_disney_exception_softens_industry():
    # industry red + barriers green -> softened to mid -> stays robust
    v = robustness.derive_verdict(_axes(industry="fragile", barriers="robust"))
    assert v["verdict"] == "robust"


def test_verdict_two_noncritical_reds_is_borderline():
    # customers + industry red, barriers mid (no Disney softening) -> borderline
    v = robustness.derive_verdict(_axes(customers="fragile", industry="fragile", barriers="mid"))
    assert v["verdict"] == "borderline"


def test_compute_data_axes_from_headline():
    headline = {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                "latest_net_debt_ebitda": -0.4, "latest_adjusted_net_debt_m": -20000.0}
    axes = robustness.compute_data_axes(headline)
    assert axes["roce"]["band"] == "robust"
    assert axes["roce"]["value"] == 30.0
    assert axes["net_debt"]["band"] == "robust"


def test_parse_ai_axes_from_json_section():
    ai_notes = {"Robustness": json.dumps({"axes": {
        "customers": {"band": "robust", "note": "millions of advertisers"},
        "barriers": {"band": "robust", "note": "wide moat"},
        "management": {"band": "mid", "note": "founder control"},
        "industry": {"band": "fragile", "note": "fast AI shifts"},
    }})}
    ai = robustness.parse_ai_axes(ai_notes)
    assert ai["management"]["band"] == "mid"
    assert ai["industry"]["band"] == "fragile"


def test_parse_ai_axes_missing_returns_empty():
    assert robustness.parse_ai_axes({}) == {}
    assert robustness.parse_ai_axes({"Robustness": "no json here"}) == {}


def test_build_table_merges_and_derives():
    headline = {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                "latest_net_debt_ebitda": -0.4, "latest_adjusted_net_debt_m": -20000.0}
    ai_notes = {"Robustness": json.dumps({"axes": {
        "customers": {"band": "robust"}, "barriers": {"band": "robust"},
        "management": {"band": "robust"}, "industry": {"band": "fragile"}}})}
    table = robustness.build_table(headline, ai_notes)
    # industry red + barriers green -> Disney softens -> robust
    assert table["verdict"] == "robust"
    assert table["axes_base"]["industry"]["band"] == "fragile"  # base unchanged


def test_build_table_override_wins():
    headline = {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                "latest_net_debt_ebitda": -0.4, "latest_adjusted_net_debt_m": -20000.0}
    ai_notes = {"Robustness": json.dumps({"axes": {
        "customers": {"band": "robust"}, "barriers": {"band": "robust"},
        "management": {"band": "robust"}, "industry": {"band": "robust"}}})}
    table = robustness.build_table(headline, ai_notes, overrides={"management": "fragile"})
    assert table["axes"]["management"]["band"] == "fragile"
    assert table["axes"]["management"]["source"] == "override"
    assert table["verdict"] == "fragile"


def test_resolve_reapplies_overrides_without_headline():
    base = {k: {"band": "robust", "source": s} for k, _, _, s in robustness.AXES}
    effective, verdict = robustness.resolve(base, {"net_debt": "fragile"})
    assert effective["net_debt"]["band"] == "fragile"
    assert verdict["verdict"] == "fragile"


def test_verdict_disney_recount_leaves_one_noncritical_red():
    # customers red + industry red + barriers green: Disney softens industry to
    # mid, leaving a single non-critical red -> stays robust (locks the
    # softening-before-recount interaction).
    v = robustness.derive_verdict(_axes(customers="fragile", industry="fragile", barriers="robust"))
    assert v["verdict"] == "robust"


def test_parse_ai_axes_accepts_top_level_without_wrapper():
    # tolerate AI output that omits the {"axes": {...}} wrapper
    ai_notes = {"Robustness": json.dumps({
        "customers": {"band": "robust"}, "barriers": {"band": "mid"},
        "management": {"band": "fragile"}, "industry": {"band": "robust"}})}
    ai = robustness.parse_ai_axes(ai_notes)
    assert ai["management"]["band"] == "fragile"
    assert ai["barriers"]["band"] == "mid"


def test_compute_data_axes_value_and_net_debt_m():
    headline = {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                "latest_net_debt_ebitda": -0.4, "latest_adjusted_net_debt_m": -20000.0}
    axes = robustness.compute_data_axes(headline)
    assert axes["roce"]["metric"] == "ROCE"
    assert axes["net_debt"]["value"] == -0.4
    assert axes["net_debt"]["net_debt_m"] == -20000.0


def test_default_prompts_include_robustness():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert "Robustness" in titles
    entry = next(p for p in streamlit_app.DEFAULT_AI_PROMPTS if p["title"] == "Robustness")
    for key in ("customers", "barriers", "management", "industry"):
        assert key in entry["prompt"]
    assert "{prior:Moat Analysis}" in entry["prompt"]


def test_render_robustness_table_html_contains_axes_and_verdict():
    import streamlit_app
    cfg = {
        "robustness": {
            "axes": {
                "roce":       {"band": "robust", "value": 30.0, "metric": "ROCE"},
                "net_debt":   {"band": "robust", "value": -0.4, "unit": "x_ebitda"},
                "customers":  {"band": "robust", "note": "fragmented"},
                "barriers":   {"band": "robust", "note": "wide moat"},
                "management": {"band": "mid", "note": "founder control"},
                "industry":   {"band": "fragile", "note": "fast AI"},
            },
            "verdict": "borderline", "verdict_mapped": "revisit",
            "verdict_reason": "deal-breaker amber: management",
        }
    }
    html = streamlit_app._render_robustness_table(cfg, theme={"text": "#111", "text_muted": "#888"})
    assert "ROCE" in html
    assert "Management" in html
    assert "BORDERLINE" in html.upper()
    assert html.count('class="rb-row"') == 6
