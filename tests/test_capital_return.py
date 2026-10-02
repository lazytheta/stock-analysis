"""Capital Return tab: pure computations on synthetic EDGAR-shaped inputs,
plus the HTML / figure builders.

Fixture shapes follow gather_data: `fund` holds aligned lists keyed by
fiscal year (money in $M, shares as a raw split-adjusted count); the cash-flow
statement holds aligned lists with outflows negative (dividends_paid,
stock_buybacks, debt_repayment) and inflows positive (stock_issuance,
debt_issuance).
"""

import pytest

import capital_return as cr

YEARS = list(range(2015, 2026))          # 11 fiscal years, FY2025 latest
THEME = {"accent": "#81b29a", "text_muted": "#888", "text": "#222"}


def make_fund(shares_growth=-0.02, fcf=500.0, net_income=450.0, years=YEARS,
              shares=None, dps_tagged=None):
    n = len(years)
    if shares is None:
        shares = [1_000e6 * (1 + shares_growth) ** i for i in range(n)]
    return {
        "years": list(years),
        "revenue": [5_000.0] * n,
        "shares": list(shares),
        "fcf": [fcf] * n,
        "cfo": [fcf + 100] * n,
        "capex": [-100.0] * n,
        "net_income": [net_income] * n,
        "dividends_per_share": dps_tagged or [None] * n,
    }


def make_cashflow(div0=100.0, div_growth=0.05, buybacks=300.0, issuance=20.0,
                  repay=200.0, debt_issue=50.0, years=YEARS):
    n = len(years)
    return {
        "years": list(years),
        "dividends_paid": ([-div0 * (1 + div_growth) ** i for i in range(n)]
                           if div0 else [None] * n),
        "stock_buybacks": [-buybacks] * n if buybacks else [None] * n,
        "stock_issuance": [issuance] * n,
        "debt_repayment": [-repay] * n,
        "debt_issuance": [debt_issue] * n,
        "fcf": [None] * n,
    }


def _drop_years(statement, drop):
    keep = [i for i, y in enumerate(statement["years"]) if y not in drop]
    return {k: ([v[i] for i in keep] if isinstance(v, list) else v)
            for k, v in statement.items()}


# ── headline ───────────────────────────────────────────────────────────────

def test_headline_payer_numbers():
    fund, cf = make_fund(), make_cashflow()
    h = cr.headline(fund, cf, price=100.0)
    shares_fy = 1_000e6 * 0.98 ** 10
    mcap = 100.0 * shares_fy / 1e6
    div_fy = 100.0 * 1.05 ** 10
    assert h["fy"] == 2025
    assert h["market_cap_m"] == pytest.approx(mcap)
    assert h["shareholder_yield"] == pytest.approx((div_fy + 300 - 20) / mcap)
    assert h["dividend_yield"] == pytest.approx(div_fy / mcap)
    assert h["share_cagr_5y"] == pytest.approx(-0.02)
    div5 = sum(100.0 * 1.05 ** i for i in range(6, 11))
    assert h["fcf_returned_5y"] == pytest.approx((div5 + 5 * 300) / (5 * 500))
    assert h["dividends_5y"] == pytest.approx(div5)
    assert h["buybacks_5y"] == pytest.approx(1500)


def test_headline_without_price_or_cashflow_is_none_not_raise():
    h = cr.headline(make_fund(), None, price=None)
    assert h["shareholder_yield"] is None
    assert h["dividend_yield"] is None
    assert h["fcf_returned_5y"] is None
    assert h["share_cagr_5y"] == pytest.approx(-0.02)   # needs only fund
    empty = cr.headline({}, {}, None)
    assert all(empty[k] is None for k in
               ("fy", "shareholder_yield", "dividend_yield", "share_cagr_5y",
                "fcf_returned_5y"))


def test_net_issuer_has_negative_shareholder_yield_component():
    """Net buybacks = repurchases - issuance; heavy issuance outweighs."""
    cf = make_cashflow(div0=0, buybacks=10.0, issuance=200.0)
    h = cr.headline(make_fund(shares_growth=0.03), cf, price=50.0)
    assert h["shareholder_yield"] < 0
    assert h["dividend_yield"] == 0.0


# ── the one-sentence summary ───────────────────────────────────────────────

def test_sentence_payer_mostly_buybacks_and_shrinking():
    h = cr.headline(make_fund(), make_cashflow(), price=100.0)
    s = cr.summary_sentence(h)
    pct = round(h["fcf_returned_5y"] * 100)
    assert s == (f"Gives back about {pct}% of free cash flow, mostly through "
                 f"buybacks; the share count falls about 2.0% a year.")


def test_sentence_does_not_flag_more_than_it_earns_at_a_rounded_100():
    """100.4% would read "more than it earns: about 100%"; the flag needs the
    rounded percentage above 100."""
    h = {"dividends_5y": 100.0, "buybacks_5y": 402.0, "fcf_5y": 500.0,
         "fcf_returned_5y": 1.004, "share_cagr_5y": -0.03}
    s = cr.summary_sentence(h)
    assert "more than it earns" not in s
    assert s.startswith("Gives back about 100% of free cash flow")


def test_sentence_mostly_dividends():
    cf = make_cashflow(div0=400.0, div_growth=0.0, buybacks=50.0)
    s = cr.summary_sentence(cr.headline(make_fund(), cf, 100.0))
    assert "mostly through dividends" in s


def test_sentence_more_than_it_earns():
    cf = make_cashflow(buybacks=900.0)
    s = cr.summary_sentence(cr.headline(make_fund(), cf, 100.0))
    assert "more than it earns" in s


def test_sentence_non_payer_and_diluter():
    cf = make_cashflow(div0=0, buybacks=100.0, issuance=10.0)
    s = cr.summary_sentence(cr.headline(make_fund(shares_growth=0.03), cf, 100.0))
    assert "pays no dividend" in s
    assert "shares are being diluted" in s
    assert "3.0% a year" in s


def test_sentence_no_buybacks():
    cf = make_cashflow(buybacks=0)
    s = cr.summary_sentence(cr.headline(make_fund(shares_growth=0.0), cf, 100.0))
    assert "does not buy back shares" in s
    assert "roughly flat" in s


def test_sentence_returns_nothing():
    cf = make_cashflow(div0=0, buybacks=0)
    s = cr.summary_sentence(cr.headline(make_fund(shares_growth=0.0), cf, 100.0))
    assert "pays no dividend" in s and "does not buy back shares" in s


def test_sentence_without_data():
    assert cr.summary_sentence(cr.headline({}, None, None)) == cr.NO_DATA


# ── where the cash went ────────────────────────────────────────────────────

def test_annual_flows_values_and_window():
    rows = cr.annual_flows(make_fund(), make_cashflow())
    assert [r["year"] for r in rows] == list(range(2016, 2026))   # 10 years
    last = rows[-1]
    assert last["dividends"] == pytest.approx(100 * 1.05 ** 10)
    assert last["buybacks"] == 300
    assert last["issuance"] == 20
    assert last["debt_paydown"] == 150          # 200 repaid - 50 issued
    assert last["fcf"] == 500


def test_debt_paydown_only_when_positive():
    cf = make_cashflow(repay=10.0, debt_issue=500.0)
    rows = cr.annual_flows(make_fund(), cf)
    assert all(r["debt_paydown"] == 0.0 for r in rows)


def test_missing_cashflow_years_are_skipped_not_zero_filled():
    cf = _drop_years(make_cashflow(), {2019, 2020})
    rows = cr.annual_flows(make_fund(), cf)
    years = [r["year"] for r in rows]
    assert 2019 not in years and 2020 not in years
    assert len(rows) == 8
    assert cr.missing_years(make_fund(), cf) == [2019, 2020]
    cap = cr.cash_use_caption(make_fund(), cf)
    assert "FY2019" in cap and "FY2020" in cap


def test_fcf_returned_uses_available_years_only():
    cf = _drop_years(make_cashflow(div0=0, buybacks=250.0), {2023})
    h = cr.headline(make_fund(), cf, 100.0)
    # four covered years (2021, 2022, 2024, 2025): 4*250 / 4*500
    assert h["fcf_returned_5y"] == pytest.approx(0.5)


def test_cash_use_figure_is_stacked_with_fcf_line():
    rows = cr.annual_flows(make_fund(), _drop_years(make_cashflow(), {2019}))
    fig = cr.cash_use_figure(rows, THEME)
    names = [t.name for t in fig.data]
    assert names == ["Dividends", "Buybacks", "Debt paydown", "Free cash flow"]
    assert fig.layout.barmode == "relative"
    assert "FY2019" not in list(fig.data[0].x)
    assert fig.data[3].type == "scatter"


# ── dividend ───────────────────────────────────────────────────────────────

def test_dividend_stats_payer():
    fund, cf = make_fund(), make_cashflow()
    d = cr.dividend_stats(fund, cf)
    assert d["pays"] is True
    # DPS = paid / split-adjusted shares; grows (1.05 / 0.98) a year.
    g = 1.05 / 0.98 - 1
    for n in (3, 5, 10):
        assert d["growth"][n] == pytest.approx(g, rel=1e-9)
    assert d["years"] == list(range(2016, 2026))
    assert d["dps"][-1] == pytest.approx(100 * 1.05 ** 10 * 1e6 / (1_000e6 * 0.98 ** 10))
    assert d["consecutive_increases"] == 10      # 2016..2025 each above the year before
    assert d["payout_net_income"] == pytest.approx(100 * 1.05 ** 10 / 450)
    assert d["payout_fcf"] == pytest.approx(100 * 1.05 ** 10 / 500)


def test_dividend_stats_non_payer():
    d = cr.dividend_stats(make_fund(), make_cashflow(div0=0))
    assert d["pays"] is False
    assert d["consecutive_increases"] == 0
    html = cr.dividend_section_body_html(d)
    assert "Pays no dividend." in html


def test_dividend_streak_breaks_on_a_cut():
    cf = make_cashflow(div_growth=0.0)
    cf["dividends_paid"][7] = -50.0              # FY2022 cut
    d = cr.dividend_stats(make_fund(shares_growth=0.0), cf)
    # flat dividends on flat shares never count as increases
    assert d["consecutive_increases"] == 0
    cf2 = make_cashflow(div_growth=0.10)
    cf2["dividends_paid"][8] = -10.0             # FY2023 cut, then two raises
    d2 = cr.dividend_stats(make_fund(shares_growth=0.0), cf2)
    assert d2["consecutive_increases"] == 2


def test_split_year_uses_split_adjusted_shares_not_tagged_dps():
    """A 4-for-1 split in FY2021: fetch_fundamentals already multiplied the
    earlier share counts by 4; the tagged dividends_per_share is NOT
    adjusted (it jumps down 4x). DPS growth must stay smooth."""
    shares = [4_000e6] * len(YEARS)
    tagged = [4.0] * 6 + [1.0] * 5               # unadjusted, pre/post split
    fund = make_fund(shares=shares, dps_tagged=tagged)
    cf = make_cashflow(div0=400.0, div_growth=0.0)
    d = cr.dividend_stats(fund, cf)
    assert d["growth"][5] == pytest.approx(0.0, abs=1e-12)
    assert all(v == pytest.approx(0.1) for v in d["dps"])
    rows = cr.share_count_rows(fund, cf)
    assert all(r["share_change"] == pytest.approx(0.0) for r in rows if r["share_change"] is not None)


def test_dps_figure_builds():
    d = cr.dividend_stats(make_fund(), make_cashflow())
    fig = cr.dps_figure(d["years"], d["dps"], THEME)
    assert fig.data[0].type == "bar"
    assert list(fig.data[0].x)[-1] == "FY2025"


# ── share count ────────────────────────────────────────────────────────────

def test_share_count_series_and_rows():
    fund, cf = make_fund(), make_cashflow()
    years, shares = cr.share_count_series(fund)
    assert years == list(range(2016, 2026))
    assert shares[-1] == pytest.approx(1_000e6 * 0.98 ** 10)
    rows = cr.share_count_rows(fund, cf)
    assert rows[-1]["year"] == 2025
    assert rows[-1]["buybacks"] == 300
    assert rows[-1]["issuance"] == 20
    assert rows[-1]["share_change"] == pytest.approx(-0.02)


def test_share_rows_diluter_and_missing_cashflow_year():
    fund = make_fund(shares_growth=0.04)
    cf = _drop_years(make_cashflow(buybacks=100.0, issuance=400.0), {2022})
    rows = {r["year"]: r for r in cr.share_count_rows(fund, cf)}
    assert rows[2025]["share_change"] == pytest.approx(0.04)
    assert rows[2022]["buybacks"] is None       # unknown, not zero
    assert rows[2022]["share_change"] == pytest.approx(0.04)


def test_shares_figure_builds():
    years, shares = cr.share_count_series(make_fund())
    fig = cr.shares_figure(years, shares, THEME)
    assert fig.data[0].type == "scatter"


# ── HTML ───────────────────────────────────────────────────────────────────

def test_headline_section_html_tiles_and_sentence():
    h = cr.headline(make_fund(), make_cashflow(), 100.0)
    html = cr.headline_section_html(h)
    for label in ("SHAREHOLDER YIELD", "DIVIDEND YIELD", "SHARE COUNT", "FCF RETURNED"):
        assert label in html
    assert "qc-section" in html and "Capital return" in html
    assert "-2.0%" in html
    assert "mostly through buybacks" in html
    assert "<style>" in html and "\n" not in html.split("<style>")[1].split("</style>")[0]


def test_headline_section_html_missing_values_show_dash():
    html = cr.headline_section_html(cr.headline({}, None, None))
    assert html.count("—") >= 4
    assert cr.NO_DATA in html


def test_html_builders_never_raise_on_garbage():
    assert cr.headline_section_html(None)
    assert cr.dividend_section_body_html(None)
    assert cr.share_table_html(None) is not None
    assert cr.headline_section_html({"fy": "x", "shareholder_yield": "bad"})


def test_share_table_html_escapes_dollars():
    rows = cr.share_count_rows(make_fund(), make_cashflow())
    html = cr.share_table_html(rows)
    assert "$" not in html and "&#36;" in html
    assert "FY2025" in html


def test_pure_functions_tolerate_none():
    assert cr.annual_flows(None, None) == []
    assert cr.share_count_rows(None, None) == []
    assert cr.share_count_series(None) == ([], [])
    d = cr.dividend_stats(None, None)
    assert d["pays"] is None
