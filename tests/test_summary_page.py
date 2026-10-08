"""summary_page: the read-only Summary tab (Verdict, Scorecard, Robustness,
Pre-mortem & action triggers). Pure HTML builders."""
import re

import summary_page as sp

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd",
         "bg_secondary": "#f5f3ee", "border_light": "#e8e8ed", "card": "#fff"}

INV = ("**Verdict: Deep dive 🟢 · High conviction**\n\n"
       "A toll road on $608M of payments that keeps compounding.\n\n"
       "- **Strongest point**: 30% ROCE for ten years.\n"
       "- **Biggest concern**: Two customers are 25% of revenue.\n"
       "- **Capital returns**: Buybacks shrink the share count 3% a year.\n\n"
       "**What would change this:** ROCE under 15%.\n\n## Sources\n[1] x")

VS = {"weighted_fv_low": 80.0, "weighted_fv_mid": 100.0, "weighted_fv_high": 130.0,
      "buy_price": 75.0, "current_vs_mid": 0.2, "stock_price": 120.0}


def _no_raw_dollar(html):
    # A bare $ pair renders as LaTeX in st.markdown.
    assert "$" not in html


def _no_newline(html):
    # A newline inside an HTML block ends it early in Streamlit's markdown.
    assert "\n" not in html


# ── Verdict ──────────────────────────────────────────────────────────────────

def test_verdict_section_shows_verdict_conviction_lead_and_three_reasons():
    html = sp.verdict_section_html(INV, VS, 152.0, THEME)
    assert 'qc-label">Verdict</div>' in html
    assert "DEEP DIVE" in html.upper()
    assert "HIGH CONVICTION" in html.upper()
    assert "toll road" in html
    for label in ("Strongest point", "Biggest concern", "Capital returns"):
        assert label in html
    assert "Sources" not in html
    _no_raw_dollar(html)
    _no_newline(html)


def test_verdict_section_has_the_verdict_block_and_the_valuation_bar():
    html = sp.verdict_section_html(INV, VS, 152.0, THEME)
    assert 'class="vd-head"' in html and 'class="vd-lead"' in html
    assert html.count('class="vd-pt"') >= 1
    assert 'class="vb-card"' in html and 'class="vb-track"' in html
    assert "Now &#36;152.00" in html


def test_price_card_shows_fair_values_buy_and_current_price():
    html = sp.verdict_section_html(INV, VS, 152.0, THEME)
    for v in ("&#36;80.00", "&#36;100.00", "&#36;130.00", "&#36;75.00", "&#36;152.00"):
        assert v in html, v


def test_price_above_fair_value_mid():
    html = sp.verdict_section_html(INV, VS, 152.0, THEME)
    assert "+52% above fair value" in html


def test_price_below_fair_value_mid_uses_a_real_minus():
    html = sp.verdict_section_html(INV, VS, 89.0, THEME)
    assert "−11% below fair value" in html
    assert "under the buy price" not in html.lower()


def test_price_under_buy_price_gets_a_muted_line():
    html = sp.verdict_section_html(INV, VS, 70.0, THEME)
    assert "−30% below fair value" in html
    assert "Cheap &lt; &#36;75.00" in html


def test_price_falls_back_to_the_stored_snapshot():
    html = sp.verdict_section_html(INV, VS, None, THEME)
    assert "&#36;120.00" in html
    assert "+20% above fair value" in html


def test_missing_valuation_shows_dashes_and_never_raises():
    for vs in (None, {}, {"weighted_fv_mid": None}, "garbage"):
        html = sp.verdict_section_html(INV, vs, None, THEME)
        assert "—" in html
        assert "above fair value" not in html and "below fair value" not in html


def test_missing_investment_summary_shows_a_muted_note():
    for text in (None, "", "   "):
        html = sp.verdict_section_html(text, VS, 152.0, THEME)
        assert "No Investment Summary yet" in html
        assert 'class="vb-card"' in html


def test_unparseable_investment_summary_shows_a_note_not_a_crash():
    html = sp.verdict_section_html("Some old free-form essay without a verdict.", VS, 1.0, THEME)
    assert "verdict format" in html


def test_verdict_text_is_escaped():
    bad = INV.replace("A toll road", "<script>x</script> A toll road")
    html = sp.verdict_section_html(bad, VS, 152.0, THEME)
    assert "<script>" not in html


# ── Scorecard ────────────────────────────────────────────────────────────────

def _scorecard(**kw):
    data = {
        "phase": {"number": 5, "name": "Capital Return"},
        "all_phases": {
            "business_description": {"rating": "green", "note": "simple"},
            "moat": {"rating": "yellow", "note": "narrow"},
            "long_term_potential": {"rating": "green", "note": "runway"},
        },
        "key_metrics": [
            {"name": "Revenue 3YR CAGR", "rating": "green", "value": "Over 10%"},
            {"name": "FCF / Net Income", "rating": "yellow", "value": "76%"},
            {"name": "EBIT / Interest", "rating": "green", "value": "5+"},
            {"name": "ROIC", "rating": "red", "value": "$9 on $100"},
            {"name": "Capital Returns", "rating": "green", "value": "Yes"},
        ],
        "execution_risk": {"rating": "yellow", "note": "medium"},
        "verdict": "deep_dive",
        "summary": "One. Two. Three.",
    }
    data.update(kw)
    return data


def test_scorecard_section_shows_phase_ratings_metrics_and_summary():
    html = sp.scorecard_section_html(_scorecard(), THEME)
    assert 'qc-label">Scorecard</div>' in html
    assert "Phase 5" in html and "Capital Return" in html
    for label in ("Business", "Moat", "Long-term potential", "Execution risk"):
        assert label in html
    for note in ("simple", "narrow", "runway", "medium"):
        assert note in html
    for name in ("Revenue 3YR CAGR", "FCF / Net Income", "EBIT / Interest", "ROIC",
                 "Capital Returns"):
        assert name in html
    assert "One. Two. Three." in html
    _no_raw_dollar(html)
    _no_newline(html)


def test_scorecard_lights_one_dot_per_rated_row():
    html = sp.scorecard_section_html(_scorecard(), THEME)
    # Four rating rows, three states each.
    assert html.count('data-active="1"') == 4
    assert html.count('data-active="0"') == 8


def test_scorecard_metric_ratings_are_words():
    html = sp.scorecard_section_html(_scorecard(), THEME)
    assert html.count(">Green<") == 3
    assert html.count(">Yellow<") == 1
    assert html.count(">Red<") == 1


def test_scorecard_unknown_rating_lights_nothing():
    data = _scorecard()
    data["execution_risk"] = {"rating": "", "note": ""}
    html = sp.scorecard_section_html(data, THEME)
    assert html.count('data-active="1"') == 3


def test_scorecard_renders_no_valuation_row():
    data = _scorecard()
    data["valuation"] = {"primary": {"name": "P/E", "rating": "yellow", "note": "Fairly valued"}}
    html = sp.scorecard_section_html(data, THEME)
    assert "Fairly valued" not in html


def test_scorecard_accepts_the_compact_phase_form():
    html = sp.scorecard_section_html(_scorecard(phase=3), THEME)
    assert "Phase 3" in html


def test_scorecard_missing_shows_a_notice():
    for data in (None, {}, "nope"):
        html = sp.scorecard_section_html(data, THEME)
        assert 'qc-label">Scorecard</div>' in html
        assert "No Scorecard yet" in html


def test_scorecard_with_partial_data_does_not_raise():
    html = sp.scorecard_section_html({"phase": {"number": "?"}, "key_metrics": None}, THEME)
    assert 'qc-label">Scorecard</div>' in html


# ── Robustness (moved from streamlit_app) ───────────────────────────────────

_ROB = {
    "robustness": {
        "axes": {
            "roce":       {"band": "robust", "value": 30.0, "metric": "ROCE"},
            "net_debt":   {"band": "robust", "value": -0.4, "unit": "x_ebitda"},
            "customers":  {"band": "robust", "note": "fragmented"},
            "barriers":   {"band": "robust", "note": "wide moat"},
            "management": {"band": "mid", "note": "founder control $5"},
            "industry":   {"band": "fragile", "note": "fast AI"},
        },
        "verdict": "borderline", "verdict_mapped": "revisit",
        "verdict_reason": "deal-breaker amber: management",
    }
}


def test_render_robustness_table_basic_output():
    html = sp.render_robustness_table(_ROB, THEME)
    assert "BORDERLINE" in html.upper()
    assert html.count('data-active="1"') == 7
    assert "net cash" in html and "30% ROCE" in html


def test_robustness_section_wraps_the_table():
    html = sp.robustness_section_html(_ROB, THEME)
    assert 'qc-label">Robustness</div>' in html
    assert html.count('data-active="1"') == 7
    _no_raw_dollar(html)
    _no_newline(html)


def test_robustness_section_not_assessed():
    html = sp.robustness_section_html({}, THEME)
    assert "Robustness not yet assessed" in html


# ── Pre-mortem (moved from streamlit_app) ───────────────────────────────────

_PM = {
    "current": "Spot $143 | FV mid $174 | buy $139",
    "sell": ["MARGIN — gross margin under 54%", "ROCE under 15%"],
    "add": ["Price under $139"],
    "ignore": ["A weak quarter"],
    "discipline": ["Re-read the thesis each quarter"],
}


def test_render_premortem_basic_output():
    html = sp.render_premortem(_PM, THEME)
    assert "Sell when" in html and "Add when" in html and "Not a reason" in html
    assert "Current view" in html
    assert "Discipline (1)" in html
    assert "MARGIN" in html and "gross margin under 54%" in html


def test_render_premortem_empty_or_legacy_returns_none():
    assert sp.render_premortem({}, THEME) is None
    assert sp.render_premortem("old free text", THEME) is None
    assert sp.render_premortem(None, THEME) is None


def test_premortem_section_wraps_the_board():
    html = sp.premortem_section_html(_PM, THEME)
    assert 'qc-label">Pre-mortem &amp; action triggers</div>' in html
    assert "Sell when" in html
    _no_raw_dollar(html)
    _no_newline(html)


def test_premortem_section_legacy_string_is_shown_escaped():
    html = sp.premortem_section_html("Sell if <b>x</b> drops\nAdd at $50", THEME)
    assert "&lt;b&gt;" in html
    _no_raw_dollar(html)
    _no_newline(html)


def test_premortem_section_missing_shows_a_notice():
    for pm in (None, {}, "", "   "):
        html = sp.premortem_section_html(pm, THEME)
        assert "No pre-mortem yet" in html


# ── Read-only ────────────────────────────────────────────────────────────────

def test_sections_contain_no_form_controls():
    html = (sp.verdict_section_html(INV, VS, 152.0, THEME)
            + sp.scorecard_section_html(_scorecard(), THEME)
            + sp.robustness_section_html(_ROB, THEME)
            + sp.premortem_section_html(_PM, THEME))
    assert not re.search(r"<(button|input|textarea|select|form)\b", html)


# ── Wiring in streamlit_app ─────────────────────────────────────────────────

def _src():
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(encoding="utf-8")


def test_summary_tab_follows_risk_and_pre_scan_is_gone():
    src = _src()
    assert ('["Business", "Moat", "Growth", "Management", "Risk", "Summary", '
            '"Capital Return", "Earnings", "Financials", "DCF",') in src
    assert "_tab_risk, _tab_summary," in src
    assert '"Pre-Scan"' not in src and "with _tab_notes:" not in src
    assert "_gemini_run" not in src


def test_summary_block_renders_four_sections_read_only():
    src = _src()
    start = src.index("with _tab_summary:")
    end = src.index("with _tab_dcf:", start)
    block = src[start:end]
    order = [block.index(f"summary_page.{fn}(") for fn in (
        "verdict_section_html", "scorecard_section_html",
        "robustness_section_html", "premortem_section_html")]
    assert order == sorted(order)
    for widget in ("st.button", "st.text_area", "st.text_input", "st.expander",
                   "st.selectbox", "save_config"):
        assert widget not in block, widget


def test_premortem_current_view_splits_into_tiles_and_highlights_the_action():
    pm = dict(_PM, current="Spot ~$720 · 3 shares · cost basis $556 · boven buy, onder FV-mid → houden")
    html = sp.render_premortem(pm, THEME)
    assert 'class="pm-act"' in html and "houden" in html
    assert html.count('class="pm-chip"') == 3


def test_premortem_triggers_are_boxes_with_a_bold_head():
    html = sp.render_premortem(_PM, THEME)
    # An upper-case category ("MARGIN — ...") becomes a coloured tag.
    assert 'font-weight:700">MARGIN </span>gross margin under 54%' in html
    assert html.count('class="pm-item"') == 5
    pm = dict(_PM, sell=["Capex-ROI fails: ad growth under 15% while capex stays high"])
    assert "<strong>Capex-ROI fails</strong><span>ad growth under 15% while capex stays high" \
        in sp.render_premortem(pm, THEME)


def test_flags_from_what_would_change_this():
    text = ("**Verdict: Deep dive 🟢 · Medium conviction**\n\nLead.\n\n- **A**: b\n\n"
            "**What would change this:** ROCE falling below 20%, or a regulator forcing "
            "non-personalised ads, turns this into a Pass; an operating margin back above 38% "
            "makes it High conviction.")
    green, red = sp.flags(text)
    assert green == ["an operating margin back above 38%"]
    assert red == ["ROCE falling below 20%", "a regulator forcing non-personalised ads"]
    html = sp.flags_section_html(text, THEME)
    assert "Green flags" in html and "Red flags" in html
    assert sp.flags_section_html("no verdict", THEME) == ""


def test_valuation_score_bands():
    vs = {"weighted_fv_low": 80, "weighted_fv_mid": 100, "weighted_fv_high": 130, "buy_price": 75}
    assert sp.valuation_score(vs, 70)[0] == 5
    assert sp.valuation_score(vs, 90)[0] == 4
    assert sp.valuation_score(vs, 102)[0] == 3
    assert sp.valuation_score(vs, 120)[0] == 2
    assert sp.valuation_score(vs, 150)[0] == 1
    assert sp.valuation_score({}, 100) == (None, "")


def test_score_strip_composite_and_tiles():
    dims = [("Business", 4, "Like it"), ("Moat", 4, "Wide"), ("Growth", None, ""),
            ("Risk", 3, "Medium")]
    html = sp.score_strip_html(dims, "Deep dive", THEME)
    assert "<b>3.7</b>" in html and "DEEP DIVE" in html
    assert html.count('class="ss-dim"') == 4


def test_premortem_action_is_the_spaced_arrow_with_an_action_word():
    pm = dict(_PM, current="DCF $786 (ramp 0,36→0,85) · 3 shares · boven buy, onder FV-mid → houden, niet bijkopen")
    html = sp.render_premortem(pm, THEME)
    act = html[html.index('class="pm-act"'):]
    assert act.index("houden") < act.index("</div>")
    assert "0,36→0,85" in html and html.count('class="pm-chip"') == 2
