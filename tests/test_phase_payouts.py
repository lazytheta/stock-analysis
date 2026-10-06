import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import phase_payouts as pp

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
    assert caption.endswith("positive in 4 of 5 years.")  # cfo [10, -5, 20, 15, 30]


def test_caption_growth_period_is_calendar_span():
    years, revenue, cfo = [2018, 2024, 2025, 2026], [100.0, 150.0, 180.0, 200.0], [1.0] * 4
    caption = pp.revenue_ocf_caption(years, revenue, cfo)
    assert caption.startswith("Revenue grew 9.1% a year over 8 years; ")  # 2^(1/8) - 1


def test_caption_counts_only_years_with_cfo():
    years = [2022, 2023, 2024, 2025, 2026]
    caption = pp.revenue_ocf_caption(years, [100.0] * 5, [1.0, None, 2.0, -1.0, None])
    assert caption.endswith("operating cash flow was positive in 2 of 3 years with data.")


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
