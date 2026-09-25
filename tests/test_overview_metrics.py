import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import overview_metrics as om

YEARS = list(range(2016, 2027))  # 11 years, FY2026 last


def _fund(**over):
    n = len(YEARS)
    base = {
        "years": YEARS,
        "revenue": [100.0 * 1.1 ** i for i in range(n)],
        "gross_profit": [50.0 * 1.1 ** i for i in range(n)],
        "operating_income": [30.0 * 1.1 ** i for i in range(n)],
        "net_income": [20.0 * 1.1 ** i for i in range(n)],
        "fcf": [25.0 * 1.1 ** i for i in range(n)],
        "cash": [40.0] * n,
        "short_term_investments": [10.0] * n,
        "total_debt": [60.0] * n,
        "total_equity": [120.0] * n,
        "shares": [1_000_000.0] * n,          # raw count
        "eps": [2.0 * 1.2 ** i for i in range(n)],
    }
    base.update(over)
    return base


def _income(**over):
    base = {"years": YEARS, "interest_expense": [5.0] * len(YEARS)}
    base.update(over)
    return base


def _cash(**over):
    n = len(YEARS)
    base = {"years": YEARS, "dividends_paid": [-3.0] * n,
            "stock_buybacks": [-6.0] * n, "debt_repayment": [-4.0] * n,
            "debt_issuance": [1.0] * n}
    base.update(over)
    return base


def _get(pairs, label):
    return dict(pairs)[label]


def test_fiscal_year_is_last_year_with_revenue():
    f = _fund()
    f["revenue"][-1] = None
    assert om.compute(f, _income(), _cash(), 100.0)["fy"] == 2025


def test_profitability_margins():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    p = out["profitability"]
    assert abs(_get(p, "Gross margin") - 0.5) < 1e-9
    assert abs(_get(p, "Operating margin") - 0.3) < 1e-9
    assert abs(_get(p, "Net margin") - 0.2) < 1e-9
    assert abs(_get(p, "FCF margin") - 0.25) < 1e-9


def test_financial_health():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    h = out["health"]
    assert _get(h, "Cash & investments") == 50.0
    assert _get(h, "Total debt") == 60.0
    assert abs(_get(h, "Debt / Equity") - 0.5) < 1e-9
    ebit = 30.0 * 1.1 ** 10
    assert abs(_get(h, "EBIT / Interest") - ebit / 5.0) < 1e-9


def test_negative_equity_and_no_interest_give_none():
    f = _fund(total_equity=[-5.0] * len(YEARS))
    out = om.compute(f, _income(interest_expense=[0.0] * len(YEARS)), _cash(), 100.0)
    assert _get(out["health"], "Debt / Equity") is None
    assert _get(out["health"], "EBIT / Interest") is None
    assert _get(out["valuation"], "P/B") is None


def test_growth_cagr_per_horizon():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    g = {(lab, h): v for lab, h, v in out["growth"]}
    assert abs(g[("Revenue", 3)] - 0.10) < 1e-9
    assert abs(g[("Revenue", 10)] - 0.10) < 1e-9
    assert abs(g[("EPS", 5)] - 0.20) < 1e-9
    assert abs(g[("FCF", 3)] - 0.10) < 1e-9


def test_growth_none_when_start_not_positive_or_too_short():
    n = len(YEARS)
    f = _fund(fcf=[-1.0] * (n - 3) + [5.0, 6.0, 7.0])
    out = om.compute(f, _income(), _cash(), 100.0)
    g = {(lab, h): v for lab, h, v in out["growth"]}
    assert g[("FCF", 5)] is None
    short = {k: (v[-4:] if isinstance(v, list) else v) for k, v in _fund().items()}
    out2 = om.compute(short, None, None, 100.0)
    g2 = {(lab, h): v for lab, h, v in out2["growth"]}
    assert g2[("Revenue", 3)] is not None and g2[("Revenue", 5)] is None


def test_valuation_and_returns_at_price():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    mcap = 100.0 * 1_000_000 / 1e6            # $100M
    rev = 100.0 * 1.1 ** 10
    v = out["valuation"]
    assert abs(_get(v, "P/S") - mcap / rev) < 1e-9
    assert abs(_get(v, "P/E") - 100.0 / (2.0 * 1.2 ** 10)) < 1e-9
    assert abs(_get(v, "P/B") - mcap / 120.0) < 1e-9
    r = out["returns"]
    assert abs(_get(r, "Dividend yield") - 0.03) < 1e-9
    assert abs(_get(r, "Buyback yield") - 0.06) < 1e-9
    assert abs(_get(r, "Debt paydown yield") - 0.03) < 1e-9
    assert abs(_get(r, "Total shareholder yield") - 0.12) < 1e-9


def test_no_price_means_no_valuation_or_returns():
    out = om.compute(_fund(), _income(), _cash(), None)
    assert all(v is None for _, v in out["valuation"])
    assert all(v is None for _, v in out["returns"])


def test_missing_statements_are_tolerated():
    out = om.compute(_fund(), None, None, 100.0)
    assert _get(out["health"], "EBIT / Interest") is None
    assert _get(out["returns"], "Dividend yield") is None
    assert _get(out["returns"], "Total shareholder yield") is None


def test_pe_independent_of_mcap_when_shares_missing():
    f = _fund(shares=[None] * len(YEARS))
    out = om.compute(f, _income(), _cash(), 100.0)
    v = out["valuation"]
    eps_value = 2.0 * 1.2 ** 10
    assert abs(_get(v, "P/E") - 100.0 / eps_value) < 1e-9
    assert _get(v, "P/S") is None
    assert _get(v, "P/B") is None
    assert _get(v, "P/FCF") is None


def test_pe_independent_of_mcap_when_shares_zero():
    f = _fund(shares=[0.0] * len(YEARS))
    out = om.compute(f, _income(), _cash(), 100.0)
    v = out["valuation"]
    eps_value = 2.0 * 1.2 ** 10
    assert abs(_get(v, "P/E") - 100.0 / eps_value) < 1e-9
    assert _get(v, "P/S") is None
    assert _get(v, "P/B") is None
    assert _get(v, "P/FCF") is None


def test_formatters():
    assert om.fmt_pct(0.4851) == "48.5%"
    assert om.fmt_pct(0.126, signed=True) == "+12.6%"
    assert om.fmt_pct(-0.02, signed=True) == "-2.0%"
    assert om.fmt_mult(31.72) == "31.7×"
    assert om.fmt_money_m(9120.0) == "$9.1B"
    assert om.fmt_money_m(510.4) == "$510M"
    assert om.fmt_pct(None) == om.fmt_mult(None) == om.fmt_money_m(None) == "—"


def test_fcf_falls_back_to_cfo_when_capex_untagged():
    n = len(YEARS)
    f = _fund(fcf=[25.0 * 1.1 ** i for i in range(n - 1)] + [None],
              cfo=[30.0 * 1.1 ** i for i in range(n)], capex=[None] * n)
    out = om.compute(f, _income(), _cash(), 100.0)
    cfo = 30.0 * 1.1 ** 10
    assert abs(_get(out["profitability"], "FCF margin") - cfo / (100.0 * 1.1 ** 10)) < 1e-9
    assert abs(_get(out["valuation"], "P/FCF") - 100.0 / cfo) < 1e-9
    g = {(lab, h): v for lab, h, v in out["growth"]}
    assert abs(g[("FCF", 3)] - ((cfo / (25.0 * 1.1 ** 7)) ** (1 / 3) - 1)) < 1e-9


def test_fcf_stays_none_when_capex_tagged_or_no_cfo():
    n = len(YEARS)
    no_fcf = [None] * n
    tagged = _fund(fcf=no_fcf, cfo=[30.0] * n, capex=[-5.0] * n)
    out = om.compute(tagged, _income(), _cash(), 100.0)
    assert _get(out["profitability"], "FCF margin") is None
    no_cfo = _fund(fcf=no_fcf, capex=[None] * n)
    out = om.compute(no_cfo, _income(), _cash(), 100.0)
    assert _get(out["valuation"], "P/FCF") is None


def test_untagged_debt_in_fiscal_year_is_zero():
    f = _fund(total_debt=[60.0] * (len(YEARS) - 1) + [None])
    out = om.compute(f, _income(), _cash(), 100.0)
    assert _get(out["health"], "Total debt") == 0.0
    assert _get(out["health"], "Debt / Equity") == 0.0
    out = om.compute({}, None, None, 100.0)
    assert _get(out["health"], "Total debt") is None


def test_untagged_cash_flow_lines_count_as_zero_when_year_present():
    cash = {"years": YEARS, "stock_buybacks": [-6.0] * len(YEARS)}
    out = om.compute(_fund(), _income(), cash, 100.0)
    r = out["returns"]
    assert _get(r, "Dividend yield") == 0.0
    assert _get(r, "Debt paydown yield") == 0.0
    assert abs(_get(r, "Total shareholder yield") - 0.06) < 1e-9


def test_cash_flow_lacking_the_year_keeps_none():
    cash = {"years": YEARS[:-1], "dividends_paid": [-3.0] * (len(YEARS) - 1)}
    out = om.compute(_fund(), _income(), cash, 100.0)
    assert all(v is None for _, v in out["returns"])
