import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import phase_payouts as pp
import question_cards as qc

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}

YEARS = list(range(2016, 2027))  # 11 years, FY2026 last


def _fund(**over):
    n = len(YEARS)
    base = {
        "years": YEARS,
        "revenue": [100.0] * n,
        "net_income": [20.0] * n,
        "cfo": [15.0] * n,
        "shares": [1_000_000.0] * n,
        "dividends_per_share": [1.0] * n,
    }
    base.update(over)
    return base


def _cash(**over):
    n = len(YEARS)
    base = {"years": YEARS, "stock_buybacks": [0.0] * n, "dividends_paid": [0.0] * n,
            "capex": [-5.0] * n}
    base.update(over)
    return base


def _point(card, label):
    return next(p["text"] for p in card["points"] if p["label"] == label)


# ── card set ──────────────────────────────────────────────────────────


def test_payouts_cardset_shape():
    assert pp.PAYOUTS.title == "Payouts"
    assert pp.PAYOUTS.directions is None
    assert [i[0] for i in pp.PAYOUTS.items] == ["buybacks", "dividend"]
    buybacks = next(i for i in pp.PAYOUTS.items if i[0] == "buybacks")
    assert buybacks[3] == ("No", "Yes & diluting", "Yes & shrinking")
    dividend = next(i for i in pp.PAYOUTS.items if i[0] == "dividend")
    assert dividend[3] == ("No", "Yes & stable", "Yes & growing")


# ── buybacks ──────────────────────────────────────────────────────────


def test_buyback_none():
    card = pp.buyback_card(_fund(), _cash(), 100.0)
    assert card["pick"] == 0
    assert card["summary"] == "No buybacks in the last five years."
    assert len(card["points"]) == 3


def test_buyback_shrinking_share_count():
    shares = [1_000_000.0] * len(YEARS)
    shares[YEARS.index(2026)] = 960_000.0  # -4% vs FY2021
    cash = _cash(stock_buybacks=[-1.0] * len(YEARS))
    card = pp.buyback_card(_fund(shares=shares), cash, 100.0)
    assert card["pick"] == 2
    assert "4.0% fewer shares than FY2021" in card["summary"]


def test_buyback_diluting_share_count():
    shares = [1_000_000.0] * len(YEARS)
    shares[YEARS.index(2026)] = 1_023_000.0  # +2.3% vs FY2021
    cash = _cash(stock_buybacks=[-1.0] * len(YEARS))
    card = pp.buyback_card(_fund(shares=shares), cash, 100.0)
    assert card["pick"] == 1
    assert "2.3% more shares than FY2021" in card["summary"]


def test_buyback_untagged_year_in_window_counts_as_zero_not_skipped():
    n = len(YEARS)
    buybacks = [-2.0] * n
    buybacks[YEARS.index(2023)] = None  # tagged year, but this line missing
    cash = _cash(stock_buybacks=buybacks)
    card = pp.buyback_card(_fund(), cash, 100.0)
    # Window 2022-2026: 4 years at $2M + 2023 counted as $0 = $8M total.
    assert "$8M" in _point(card, "Bought back")


def test_buyback_yield_uses_last_year_over_market_cap():
    cash = _cash(stock_buybacks=[-1.0] * (len(YEARS) - 1) + [-50.0])
    card = pp.buyback_card(_fund(), cash, 100.0)
    assert "50.0%" in _point(card, "Buyback yield")


def test_buyback_yield_dash_without_price():
    cash = _cash(stock_buybacks=[-5.0] * len(YEARS))
    card = pp.buyback_card(_fund(), cash, None)
    assert _point(card, "Buyback yield") == "—"


def test_buyback_card_tolerates_a_missing_cashflow_statement():
    card = pp.buyback_card(_fund(), None, 100.0)
    assert card["pick"] == 0
    assert card["summary"] == "No buybacks in the last five years."
    assert len(card["points"]) == 3


# ── dividend ──────────────────────────────────────────────────────────


def test_dividend_none():
    cash = _cash(stock_buybacks=[-10.0] * len(YEARS), capex=[-4.0] * len(YEARS))
    card = pp.dividend_card(_fund(), cash, None, 100.0, net_cash_m=25.0)
    assert card["pick"] == 0
    assert card["summary"] == "No dividend: all cash returned through buybacks or reinvested."
    labels = [p["label"] for p in card["points"]]
    assert labels == ["Cash used instead", "Reinvestment", "Net cash"]
    assert "$10M" in _point(card, "Cash used instead")
    assert "$4M" in _point(card, "Reinvestment")
    assert "$25M" in _point(card, "Net cash")


def test_dividend_growing_5pct_a_year():
    n = len(YEARS)
    dps = [1.0 * 1.05 ** i for i in range(n)]
    cash = _cash(dividends_paid=[-3.0] * n)
    card = pp.dividend_card(_fund(dividends_per_share=dps), cash, None, 100.0, net_cash_m=0.0)
    assert card["pick"] == 2
    assert "5.0% a year" in card["summary"]


def test_dividend_flat_is_stable():
    n = len(YEARS)
    cash = _cash(dividends_paid=[-3.0] * n)
    card = pp.dividend_card(_fund(dividends_per_share=[1.0] * n), cash, None, 100.0, net_cash_m=0.0)
    assert card["pick"] == 1
    assert card["summary"] == "Dividend paid, roughly flat."


def test_dividend_yield_dash_without_price():
    n = len(YEARS)
    cash = _cash(dividends_paid=[-3.0] * n)
    card = pp.dividend_card(_fund(), cash, None, None, net_cash_m=0.0)
    assert _point(card, "Yield") == "—"


def test_dividend_payout_ratio_falls_back_to_income_statement():
    n = len(YEARS)
    cash = _cash(dividends_paid=[-4.0] * n)
    fund = _fund(net_income=[None] * n)
    income = {"years": YEARS, "net_income": [16.0] * n}
    card = pp.dividend_card(fund, cash, income, 100.0, net_cash_m=0.0)
    assert "25.0%" in _point(card, "Payout ratio")  # 4 / 16


def test_dividend_card_tolerates_missing_cashflow_and_income():
    card = pp.dividend_card(_fund(), None, None, 100.0, net_cash_m=0.0)
    assert card["pick"] == 0
    assert card["summary"] == "No dividend: all cash returned through buybacks or reinvested."
    assert len(card["points"]) == 3


# ── every card ────────────────────────────────────────────────────────


def test_every_card_has_three_points_and_a_summary():
    cash = _cash(stock_buybacks=[-2.0] * len(YEARS), dividends_paid=[-3.0] * len(YEARS))
    for card in (pp.buyback_card(_fund(), cash, 100.0),
                 pp.dividend_card(_fund(), cash, None, 100.0, net_cash_m=10.0)):
        assert len(card["points"]) == 3
        assert all(p["label"] and p["text"] for p in card["points"])
        assert card["summary"]


def test_flip_card_html_renders_both_cards():
    cash = _cash(stock_buybacks=[-2.0] * len(YEARS), dividends_paid=[-3.0] * len(YEARS))
    buyback = pp.buyback_card(_fund(), cash, 100.0)
    dividend = pp.dividend_card(_fund(), cash, None, 100.0, net_cash_m=10.0)
    for card in (buyback, dividend):
        html = qc.flip_card_html(pp.PAYOUTS, card, THEME)
        assert html.startswith("<label")
        assert "\n" not in html and "$" not in html


# ── revenue / cfo series and caption ────────────────────────────────────


def _series_fund():
    revenue = [None, 40.0, 45.0, 50.0, 55.0, 60.0, 100.0, 110.0, 125.0, 135.0, 146.4]
    cfo = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 10.0, -5.0, 20.0, 15.0, 30.0]
    assert len(revenue) == len(YEARS) == len(cfo)
    return _fund(revenue=revenue, cfo=cfo)


def test_series_returns_last_n_fiscal_years_with_revenue():
    years, revenue, cfo = pp.revenue_ocf_series(_series_fund(), 5)
    assert years == [2022, 2023, 2024, 2025, 2026]
    assert revenue == [100.0, 110.0, 125.0, 135.0, 146.4]
    assert cfo == [10.0, -5.0, 20.0, 15.0, 30.0]


def test_series_skips_years_without_revenue():
    fund = _series_fund()
    years, _, _ = pp.revenue_ocf_series(fund, 20)
    assert 2016 not in years  # revenue[0] is None
    assert years == YEARS[1:]


def test_caption_growth_and_ocf_positive_count():
    years, revenue, cfo = pp.revenue_ocf_series(_series_fund(), 5)
    caption = pp.revenue_ocf_caption(years, revenue, cfo)
    assert caption.startswith("Revenue grew 10.0% a year over 4 years; ")
    assert "4 of 5 years" in caption  # cfo [10, -5, 20, 15, 30] → 4 positive of 5 shown


def test_caption_omits_growth_clause_when_first_revenue_not_positive():
    years = [2022, 2023, 2024, 2025, 2026]
    revenue = [-10.0, 20.0, 30.0, 40.0, 50.0]
    cfo = [1.0, -1.0, 2.0, 3.0, 4.0]
    caption = pp.revenue_ocf_caption(years, revenue, cfo)
    assert "grew" not in caption
    assert caption == "Operating cash flow was positive in 4 of 5 years."


def test_caption_empty_with_fewer_than_two_years():
    assert pp.revenue_ocf_caption([2026], [100.0], [10.0]) == ""
    assert pp.revenue_ocf_caption([], [], []) == ""
