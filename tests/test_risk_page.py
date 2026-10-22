from datetime import date, timedelta

import pytest

import risk_page as rp

FUND = {
    "years": [2024, 2025],
    "operating_income": [90.0, 100.0],
    "da": [18.0, 20.0],
    "fcf": [70.0, 80.0],
    "total_debt": [300.0, 300.0],
    "short_term_debt": [20.0, 30.0],
    "operating_lease_liabilities": [15.0, 20.0],
    "finance_lease_liabilities": [None, 10.0],
    "pension_liabilities": [None, None],
    "cash": [100.0, 120.0],
}
INCOME = {"years": [2024, 2025], "interest_expense": [-12.0, -16.0]}


def test_balance_metrics_match_the_hand_calculation():
    m = rp.balance_metrics(FUND, INCOME)
    adj = 300 + 30 + 20 + 10                      # Financials tab: adjusted debt
    assert m["fy"] == 2025 and m["adj_debt"] == adj
    assert m["net_debt"] == adj - 120
    assert m["nd_ebitda"] == pytest.approx((adj - 120) / (100 + 20))
    assert m["nd_fcf_years"] == pytest.approx((adj - 120) / 80)
    assert m["ebit_interest"] == pytest.approx(100 / 16)


def test_net_cash_reads_as_net_cash_not_a_negative_multiple():
    rich = dict(FUND, cash=[100.0, 1000.0])
    m = rp.balance_metrics(rich, None)
    assert m["net_debt"] < 0 and m["nd_ebitda"] is None and m["ebit_interest"] is None
    html = rp.balance_card_html(m)
    assert "Net cash" in html and "rk-good" in html


def test_bands_follow_the_robustness_axis():
    m = rp.balance_metrics(FUND, INCOME)           # 2.0x -> mid, 6.25x cover -> mid
    assert rp._tone_nd_ebitda(m) == rp.MID
    assert rp._tone_cover(m["ebit_interest"]) == rp.MID
    assert rp._tone_cover(10) == rp.GOOD and rp._tone_cover(2) == rp.BAD


def _series():
    """100 for a year, up to 150, down to 75 (-50%), back to 160."""
    d, pts = date(2020, 1, 1), []
    for price in [100] * 260 + list(range(100, 151)) + list(range(150, 74, -1)) + \
            list(range(75, 161)) + [160] * 100:
        pts.append((d.isoformat(), float(price)))
        d += timedelta(days=1)
    return pts


def test_drawdown_stats():
    s = rp.drawdown_stats(_series())
    assert s["max_dd"] == pytest.approx(-0.5)
    assert s["recovered_day"] is not None and s["recovery_days"] > 0
    assert s["from_high"] == pytest.approx(0.0)
    assert rp.drawdown_stats(_series()[:100]) is None     # too short


def test_worst_full_calendar_year():
    pts = [(f"{y}-{m:02d}-15", p) for y, closes in
           ((2021, [100] * 12), (2022, [90] * 11 + [60]), (2023, [70] * 12), (2024, [80] * 12))
           for m, p in zip(range(1, 13), closes)]
    pts = [(d, p) for d, p in pts for _ in range(20)]       # enough points
    s = rp.drawdown_stats(pts)
    assert s["worst_year"] == 2022 and s["worst_year_ret"] == pytest.approx(-0.4)


def test_section_renders_with_missing_sources():
    html = rp.risk_figures_section_html({}, None, [])
    assert "In numbers" in html and "Not enough price history" in html
