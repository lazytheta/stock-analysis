"""Capital Return for filers without an EDGAR cash-flow statement, the
debt-paydown tile and the rule-based verdict (owner request 2026-10-23)."""
import capital_return as cr

FUND = {
    "years": [2021, 2022, 2023, 2024, 2025],
    "revenue": [9.0, 11.0, 13.0, 15.0, 16.0],
    "shares": [104.6e6, 104.5e6, 104.7e6, 104.8e6, 104.8e6],
    "cfo": [3400.0, 4200.0, 4300.0, 5100.0, 5400.0],
    "capex": [-400.0, -400.0, -600.0, -1100.0, -700.0],
    "dividends_paid": [None, 1400.0, 1900.0, 1600.0, 1700.0],
    "stock_buybacks": [70.0, 20.0, 50.0, 100.0, 160.0],
    "dividends_per_share": [8.0, 13.0, 15.0, 16.0, 18.0],
}
FUND["fcf"] = [c + x for c, x in zip(FUND["cfo"], FUND["capex"])]


def test_cashflow_from_fund_uses_the_entered_fields_and_estimates_the_gap():
    cf = cr.cashflow_from_fund(FUND)
    assert cf["years"] == [2021, 2022, 2023, 2024, 2025]
    assert cf["dividends_paid"][1] == 1400.0
    assert abs(cf["dividends_paid"][0] - 8.0 * 104.6) < 1e-6      # dps x shares
    assert cf["estimated"] == [2021]
    assert cr.cashflow_from_fund({"years": [2025], "revenue": [1.0]}) is None


def test_effective_cashflow_prefers_edgar_when_it_covers_the_year():
    edgar = {"years": [2025], "dividends_paid": [5.0]}
    assert cr.effective_cashflow(FUND, edgar) is edgar
    assert cr.effective_cashflow(FUND, None)["years"][-1] == 2025


def test_headline_from_manual_cashflow_with_debt_paydown_and_verdict():
    cf = cr.effective_cashflow(dict(FUND, debt_repayment=[0, 0, 0, 0, 300.0]), None)
    h = cr.headline(dict(FUND, debt_repayment=[0, 0, 0, 0, 300.0]), cf, 1264.0)
    mcap = 1264.0 * 104.8
    assert abs(h["dividend_yield"] - 1700.0 / mcap) < 1e-9
    assert abs(h["total_yield"] - (1700.0 + 160.0 + 300.0) / mcap) < 1e-9
    assert h["estimated_years"] == [2021]
    level, why = cr.verdict(h)
    assert level == 2 and "free cash flow" in why     # ~45% of FCF, no dilution
    html = cr.headline_section_html(h)
    assert "INCL. DEBT PAYDOWN" in html and "Moderate:" in html and "estimated" in html


def test_verdict_rules():
    assert cr.verdict({"fcf_returned_5y": 0.9, "share_cagr_5y": -0.01,
                       "dividends_5y": 1, "buybacks_5y": 1})[0] == 3
    assert cr.verdict({"fcf_returned_5y": 0.9, "share_cagr_5y": 0.03,
                       "dividends_5y": 1, "buybacks_5y": 1})[0] == 1     # dilution
    assert cr.verdict({"fcf_returned_5y": 0.2, "dividends_5y": 1, "buybacks_5y": 0})[0] == 1
    assert cr.verdict({"shareholder_yield": 0.05, "fcf_returned_5y": 0.5,
                       "dividends_5y": 1, "buybacks_5y": 1})[0] == 3
    assert cr.verdict({}) is None
