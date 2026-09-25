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


def test_profile_panel_with_profile():
    html = op.profile_panel_html(GOOD_PROFILE, 343190.0)
    assert html.startswith("<")
    for text in ("SECTOR", "Communication Services", "Entertainment",
                 "&#36;343.2B", "Asset-light", "Moderate", "1997", "16,000",
                 "Subscription", "Ad-based"):
        assert text in html, text
    assert "ov-chip" in html and "ov-diff" in html
    assert "$" not in html
    assert all("\n" not in s for s in _styles(html))


def test_profile_panel_difficulty_colours():
    easy = op.profile_panel_html(dict(GOOD_PROFILE, difficulty="Easy"), None)
    hard = op.profile_panel_html(dict(GOOD_PROFILE, difficulty="Hard"), None)
    moderate = op.profile_panel_html(GOOD_PROFILE, None)
    assert "var(--red)" in hard
    assert "var(--accent)" in moderate.split("ov-diff")[-1]
    assert easy != moderate != hard


def test_profile_panel_none_values_are_dashes():
    html = op.profile_panel_html(dict(GOOD_PROFILE, founded=None, employees=None), None)
    # market cap, founded and employees all missing
    assert html.count("—") >= 3


def test_profile_panel_without_profile():
    html = op.profile_panel_html(None, 510.0)
    assert "&#36;510M" in html
    assert "Company profile not filled yet" in html
    assert "SECTOR" not in html
    assert "$" not in html


def test_profile_panel_escapes_text():
    html = op.profile_panel_html(dict(GOOD_PROFILE, tags=["<b>x</b>", "$y"]), None)
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html
    assert "$" not in html


def test_mission():
    html = op.mission_html(GOOD_PROFILE)
    assert html.startswith("<")
    assert "MISSION" in html and "To entertain the world." in html
    assert op.mission_html(None) == ""


def test_metrics_html():
    html = op.metrics_html(_metrics())
    assert html.startswith("<")
    for text in ("Profitability", "Financial Health", "Growth", "Valuation",
                 "Shareholder Returns", "LATEST FISCAL YEAR (FY2026)",
                 "COMPOUND ANNUAL GROWTH", "AT CURRENT PRICE",
                 "3Y", "5Y", "10Y", "×", "&#36;5.0B", "Gross margin", "50.0%",
                 "+10.0%", "Total shareholder yield"):
        assert text in html, text
    assert "$" not in html
    styles = _styles(html)
    assert styles and all("\n" not in s for s in styles)
    assert "@media (max-width:900px)" in html


def test_metrics_html_none_values_render_as_dash():
    html = op.metrics_html(_metrics(total_equity=[-5.0] * len(YEARS)))
    assert "—" in html  # Debt / Equity and P/B with negative equity


def test_metrics_html_without_fiscal_year():
    html = op.metrics_html(om.compute({}, None, None, None))
    assert "LATEST FISCAL YEAR" in html and "FYNone" not in html
    assert "—" in html


def test_ticker_page_has_overview_tab_first():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert '["Overview", "Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF"' in src
    assert 'key="qc_overview_section"' in src
    assert ".st-key-qc_overview_section" in src
    assert "overview_page.metrics_html(" in src
    assert "overview_page.profile_panel_html(" in src


def test_profile_panel_order_market_cap_alone_on_its_row():
    html = op.profile_panel_html(GOOD_PROFILE, 343190.0)
    order = ["SECTOR", "INDUSTRY", "MARKET CAP", "CAPITAL TYPE", "DIFFICULTY",
             "FOUNDED", "EMPLOYEES", "TAGS"]
    positions = [html.index(label) for label in order]
    assert positions == sorted(positions)
    between = html[html.index("MARKET CAP"):html.index("CAPITAL TYPE")]
    assert "<div></div>" in between


def test_mission_does_not_repeat_profile_style():
    profile = op.profile_panel_html(GOOD_PROFILE, None)
    mission = op.mission_html(GOOD_PROFILE)
    assert ".ov-profile{" in profile and ".ov-profile{" not in mission
    assert all("\n" not in s for s in _styles(mission))


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
