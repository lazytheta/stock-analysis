import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import overview_metrics as om
import overview_page as op

GOOD_PROFILE = {"sector": "Communication Services", "industry": "Entertainment",
                "capital_type": "Asset-light", "difficulty": "Moderate",
                "founded": 1997, "employees": 16000,
                "tags": ["Subscription", "Ad-based"],
                "mission": "To entertain the world."}

YEARS = list(range(2016, 2027))  # 11 years, FY2026 last


def _metrics(**fund_over):
    n = len(YEARS)
    fund = {
        "years": YEARS,
        "revenue": [100.0 * 1.1 ** i for i in range(n)],
        "gross_profit": [50.0 * 1.1 ** i for i in range(n)],
        "operating_income": [30.0 * 1.1 ** i for i in range(n)],
        "net_income": [20.0 * 1.1 ** i for i in range(n)],
        "fcf": [25.0 * 1.1 ** i for i in range(n)],
        "cash": [4000.0] * n,
        "short_term_investments": [1000.0] * n,
        "total_debt": [60.0] * n,
        "total_equity": [120.0] * n,
        "shares": [1_000_000.0] * n,
        "eps": [2.0 * 1.2 ** i for i in range(n)],
    }
    fund.update(fund_over)
    income = {"years": YEARS, "interest_expense": [5.0] * n}
    cash = {"years": YEARS, "dividends_paid": [-3.0] * n,
            "stock_buybacks": [-6.0] * n, "debt_repayment": [-4.0] * n,
            "debt_issuance": [1.0] * n}
    return om.compute(fund, income, cash, 300.0)


def _styles(html):
    return re.findall(r"<style>(.*?)</style>", html, re.S)


def _body(html):
    """The HTML after the leading <style> blocks."""
    return re.sub(r"<style>.*?</style>", "", html, flags=re.S)


GLANCE = {"roce_metric": "ROCE", "roce_pct": 31.24, "net_cash_m": 4940.0,
          "fcf_conversion": 1.55, "share_change_5y": -0.021, "fy": 2025,
          "gross_margin": 0.82, "op_margin": 0.414, "margin_years": 10,
          "revenue_cagr_5y": 0.185,
          "fcf_yield": 0.025}


def test_company_section_with_profile():
    html = op.company_section_html(GOOD_PROFILE, 343190.0, GLANCE)
    assert html.startswith("<")
    assert 'class="qc-section"' in html and ">Company</div>" in html
    for text in ("Profile", "At a glance", "SECTOR", "Communication Services",
                 "Entertainment", "&#36;343.2B", "Asset-light", "Moderate", "1997",
                 "16,000", "Subscription", "Ad-based"):
        assert text in html, text
    assert "ov-chip" in html and "ov-diff" in html
    # The mission statement is no longer shown; the summary replaces it.
    assert "MISSION" not in html and "To entertain the world." not in html
    assert "$" not in html
    styles = _styles(html)
    assert styles and all("\n" not in s for s in styles)
    assert "@media (max-width:800px)" in html


def test_company_section_profile_order():
    html = _body(op.company_section_html(GOOD_PROFILE, 343190.0, GLANCE,
                                         summary="Netflix streams films."))
    order = ["SECTOR", "INDUSTRY", "MARKET CAP", "CAPITAL TYPE", "COMPLEXITY",
             "FOUNDED", "EMPLOYEES", "TAGS", "WHAT IT DOES", "At a glance"]
    positions = [html.index(label) for label in order]
    assert positions == sorted(positions)


def test_company_section_glance_tiles():
    body = _body(op.company_section_html(GOOD_PROFILE, None, GLANCE))
    for text in ("ROCE", "31.2%", "10-year average", "NET CASH", "&#36;4.9B",
                 "cash &amp; investments − debt", "FCF CONVERSION", "155%",
                 "free cash flow / net income", "SHARES PER YEAR (5Y)", "-2.1%",
                 "buybacks"):
        assert text in body, text
    assert "ov-gval ov-pos" in body


def test_company_section_glance_net_debt_and_dilution():
    g = dict(GLANCE, roce_metric="ROE", net_cash_m=-750.0, share_change_5y=0.012)
    body = _body(op.company_section_html(GOOD_PROFILE, None, g))
    assert "ROE" in body and "NET DEBT" in body and "&#36;750M" in body
    assert "-&#36;" not in body
    # Net debt is never green (only net cash is).
    assert '<div class="ov-gval">&#36;750M</div>' in body
    assert "+1.2%" in body and "dilution" in body


def test_glance_has_eight_tiles_in_order():
    body = _body(op.company_section_html(GOOD_PROFILE, 1.0, GLANCE))
    order = ["ROCE", "GROSS MARGIN", "OPERATING MARGIN", "REVENUE GROWTH",
             "FCF CONVERSION", "FCF YIELD", "NET CASH", "SHARES PER YEAR"]
    glance = body[body.index("At a glance"):]
    positions = [glance.index(label) for label in order]
    assert positions == sorted(positions)
    assert glance.count("10-year average") == 3   # ROCE and both margins
    for text in ("82.0%", "41.4%", "+18.5%", "2.5%"):
        assert text in glance, text


def test_company_section_glance_missing_values_are_dashes():
    for glance in (None, {}, dict.fromkeys(GLANCE), {"roce_pct": "bad"}):
        html = op.company_section_html(GOOD_PROFILE, 1.0, glance)
        body = _body(html)
        assert "At a glance" in body
        assert body.count('class="ov-gval">—<') == 8, glance


def test_company_section_complexity_meter():
    easy = _body(op.company_section_html(dict(GOOD_PROFILE, difficulty="Easy"), None))
    hard = _body(op.company_section_html(dict(GOOD_PROFILE, difficulty="Hard"), None))
    moderate = _body(op.company_section_html(GOOD_PROFILE, None))
    assert "COMPLEXITY" in moderate and "DIFFICULTY" not in moderate
    assert easy.count('<i class="on">') == 1
    assert moderate.count('<i class="on">') == 2
    assert hard.count('<i class="on">') == 3
    # Neutral meter: no traffic-light colours.
    assert "var(--red)" not in hard


def test_company_section_complexity_reason():
    with_reason = _body(op.company_section_html(
        dict(GOOD_PROFILE, difficulty_reason="Three segments, ads plus subs"), None))
    assert 'class="ov-why">Three segments, ads plus subs<' in with_reason
    without = _body(op.company_section_html(GOOD_PROFILE, None))
    assert 'class="ov-why"' not in without


def test_company_section_none_values_are_dashes():
    html = _body(op.company_section_html(dict(GOOD_PROFILE, founded=None, employees=None),
                                         None, GLANCE))
    # market cap, founded and employees all missing
    assert html.count("—") >= 3


def test_company_section_without_profile():
    html = op.company_section_html(None, 510.0, GLANCE)
    body = _body(html)
    assert html.startswith("<")
    assert "&#36;510M" in body
    assert "Company profile not filled yet" in body
    assert "SECTOR" not in body and "MISSION" not in body
    assert "At a glance" in body and "155%" in body
    assert "$" not in html


def test_company_section_profile_without_mission():
    body = _body(op.company_section_html(dict(GOOD_PROFILE, mission=""), None, GLANCE))
    assert "MISSION" not in body and "At a glance" in body


def test_company_section_shows_the_summary_under_what_it_does():
    html = op.company_section_html(GOOD_PROFILE, None, GLANCE,
                                   summary="Netflix streams films and series for a monthly fee.")
    body = _body(html)
    assert "WHAT IT DOES" in body and "Netflix streams films" in body
    assert "MISSION" not in body


def test_company_section_without_summary_shows_no_what_it_does_block():
    body = _body(op.company_section_html(GOOD_PROFILE, None, GLANCE))
    assert "WHAT IT DOES" not in body


def test_company_section_escapes_text():
    html = op.company_section_html(
        dict(GOOD_PROFILE, tags=["<b>x</b>", "$y"]), None, summary="$1 <i>a</i>")
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html
    assert "<i>a</i>" not in html
    assert "$" not in html


def test_key_figures_section():
    html = op.key_figures_section_html(_metrics())
    assert html.startswith("<")
    assert 'class="qc-section"' in html and ">Key figures</div>" in html
    for text in ("Profitability", "Financial Health", "Valuation",
                 "Shareholder Returns", "LATEST FISCAL YEAR (FY2026)",
                 "AT CURRENT PRICE", "END OF FY2026",
                 "FY2026 PAYOUTS ÷ MARKET CAP",
                 "×", "&#36;5.0B", "Gross margin", "50.0%",
                 "Total shareholder yield"):
        assert text in html, text
    body = _body(html)
    assert body.count('class="ov-card"') == 4
    # Growth moved to the Growth tab (2026-10-08).
    assert ">Growth<" not in body and 'class="ov-gtab"' not in body
    assert "$" not in html
    styles = _styles(html)
    assert styles and all("\n" not in s for s in styles)
    for bp in ("(min-width:600px)", "(min-width:900px)", "(min-width:1200px)"):
        assert bp in html, bp
    assert "repeat(4,minmax(0,1fr))" in html


def test_key_figures_every_card_has_one_subtitle_line():
    # Rows only line up across the cards when each has exactly one subtitle
    # line above them.
    body = _body(op.key_figures_section_html(_metrics()))
    assert body.count('class="ov-sub"') == 4


def test_key_figures_css_equal_heights_and_plain_table():
    html = op.key_figures_section_html(_metrics())
    assert "align-items:stretch" in html and "align-items:start" not in html
    assert ".ov-metrics > .ov-card{height:100%" in html
    assert ".ov-gtab,.ov-gtab tr,.ov-gtab th,.ov-gtab td{border:none !important" in html
    assert "nth-child(even)" in html
    assert ".ov-gtab tr:last-child td{border-bottom:none !important}" in html


def test_key_figures_none_values_render_as_dash():
    html = op.key_figures_section_html(_metrics(total_equity=[-5.0] * len(YEARS)))
    assert "—" in html  # Debt / Equity and P/B with negative equity


def test_key_figures_without_fiscal_year():
    html = op.key_figures_section_html(om.compute({}, None, None, None))
    assert "LATEST FISCAL YEAR" in html and "FYNone" not in html
    assert "—" in html


def test_ticker_page_has_overview_tab_first():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert '["Business", "Moat", "Growth", "Management", "Risk", "Summary", "Capital Return", "Earnings", "Financials", "DCF"' in src
    assert "overview_page.company_section_html(" in src
    assert "overview_page.key_figures_section_html(" in src


def test_overview_price_section_container():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert 'key="qc_ov_price"' in src
    assert ".st-key-qc_ov_price" in src
    assert "qc_overview_section" not in src
    assert '<div class="qc-label">Overview</div>' not in src


def test_overview_tab_wiring_guards():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert src.count("min_years=0.95") == 2
    assert 'st.caption("Key figures unavailable right now.")' in src


def test_overview_statement_loaders_refuse_empty_results():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert ("overview_metrics.require_years(\n        fetch_income_statement(ticker, n_years=11)"
            in src)
    assert ("overview_metrics.require_years(\n        fetch_cashflow_statement(ticker, n_years=11)"
            in src)


def test_overview_cagr_only_for_year_ranges():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert 'if _orng not in ("1Y", "3Y", "5Y", "10Y"):' in src


def test_overview_market_cap_uses_fiscal_year_shares():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert "_oshares = overview_metrics.shares_at_fiscal_year(fund)" in src


def test_profile_shows_the_phase_with_a_tooltip():
    phase = {"number": 5, "name": "Capital Return", "summary": "Returns cash, $25B new debt.",
             "tooltip": "Phase 5 of 6: Capital Return\nValuation fits: Trailing P/E"}
    html = _body(op.company_section_html(GOOD_PROFILE, 1.0, GLANCE, phase=phase))
    assert "PHASE" in html and "<i>5</i>Capital Return" in html
    assert 'title="Phase 5 of 6: Capital Return&#10;Valuation fits: Trailing P/E"' in html
    assert "&#36;25B" in html
    order = ["CAPITAL TYPE", "COMPLEXITY", "PHASE", "FOUNDED"]
    pos = [html.index(x) for x in order]
    assert pos == sorted(pos)
    # Unknown phase: a dash, not a missing field.
    assert "PHASE" in _body(op.company_section_html(GOOD_PROFILE, 1.0, GLANCE))


def test_phase_sentence_opens_on_click():
    phase = {"number": 5, "name": "Capital Return", "summary": "A long lead sentence.",
             "tooltip": "t"}
    html = op.company_section_html(GOOD_PROFILE, 1.0, GLANCE, phase=phase)
    assert '<details class="ov-more"><summary><div class="ov-why">A long lead sentence.</div>' in html
    assert ".ov-more[open] .ov-why" in html



def test_glance_figures_turn_green_above_the_market_reference():
    weak = dict(GLANCE, roce_pct=8.0, gross_margin=0.30, op_margin=0.10, revenue_cagr_5y=0.03,
                fcf_conversion=0.5, fcf_yield=0.02, share_change_5y=0.01)
    strong = dict(GLANCE, roce_pct=31.2, gross_margin=0.82, op_margin=0.41, revenue_cagr_5y=0.18,
                  fcf_conversion=1.2, fcf_yield=0.05, share_change_5y=-0.02)
    w = _body(op.company_section_html(GOOD_PROFILE, 1.0, weak))
    g = _body(op.company_section_html(GOOD_PROFILE, 1.0, strong))
    for value in ("31.2%", "82.0%", "41.0%", "+18.0%", "120%", "5.0%", "-2.0%"):
        assert f'<div class="ov-gval ov-pos">{value}</div>' in g, value
    assert w.count("ov-gval ov-pos") == 1      # only Net cash (positive) stays green


def test_every_tile_and_row_with_a_reference_has_a_help_mark():
    body = _body(op.company_section_html(GOOD_PROFILE, 1.0, GLANCE))
    assert body.count('class="ov-help"') == 7   # all tiles except Net cash
    assert "Market-wide reference, not adjusted for sector." in body
    kf = _body(op.key_figures_section_html(_metrics()))
    assert kf.count('class="ov-help"') >= 12


def test_key_figures_rows_green_when_beating_the_reference():
    kf = _body(op.key_figures_section_html(_metrics()))
    assert '<b class="ov-good">' in kf
