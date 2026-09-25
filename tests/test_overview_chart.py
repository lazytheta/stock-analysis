import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import overview_chart as oc


# ── with_live_point ──

def test_with_live_point_appends_when_today_after_last_day():
    series = [("2026-09-24", 10.0)]
    out = oc.with_live_point(series, 11.0, date(2026, 9, 25))
    assert out == [("2026-09-24", 10.0), ("2026-09-25", 11.0)]


def test_with_live_point_unchanged_when_today_is_last_day():
    series = [("2026-09-24", 10.0)]
    out = oc.with_live_point(series, 11.0, date(2026, 9, 24))
    assert out == series


def test_with_live_point_unchanged_when_price_zero():
    series = [("2026-09-24", 10.0)]
    out = oc.with_live_point(series, 0, date(2026, 9, 25))
    assert out == series


def test_with_live_point_unchanged_when_price_none():
    series = [("2026-09-24", 10.0)]
    out = oc.with_live_point(series, None, date(2026, 9, 25))
    assert out == series


def test_with_live_point_does_not_mutate_input():
    series = [("2026-09-24", 10.0)]
    oc.with_live_point(series, 11.0, date(2026, 9, 25))
    assert series == [("2026-09-24", 10.0)]


# ── range_start ──

def test_range_start_1m_clamps_end_of_month():
    assert oc.range_start("1M", date(2026, 3, 31)) == date(2026, 2, 28)


def test_range_start_6m():
    assert oc.range_start("6M", date(2026, 9, 25)) == date(2026, 3, 25)


def test_range_start_ytd():
    assert oc.range_start("YTD", date(2026, 9, 25)) == date(2026, 1, 1)


def test_range_start_5y_clamps_feb29():
    assert oc.range_start("5Y", date(2024, 2, 29)) == date(2019, 2, 28)


def test_range_start_10y():
    assert oc.range_start("10Y", date(2026, 9, 25)) == date(2016, 9, 25)


# ── aligned_pct ──

def test_aligned_pct_inner_join_and_percent_change():
    stock = [("2026-01-01", 10), ("2026-01-02", 11), ("2026-01-05", 12)]
    bench = [("2026-01-02", 100), ("2026-01-05", 110)]
    dates, stock_pcts, bench_pcts = oc.aligned_pct(stock, bench, date(2026, 1, 1))
    assert dates == ["2026-01-02", "2026-01-05"]
    assert stock_pcts[0] == 0.0
    assert abs(stock_pcts[1] - (12 / 11 - 1)) < 1e-9
    assert bench_pcts[0] == 0.0
    assert abs(bench_pcts[1] - 0.1) < 1e-9


def test_aligned_pct_fewer_than_two_common_points():
    stock = [("2026-01-01", 10)]
    bench = [("2026-01-01", 100)]
    assert oc.aligned_pct(stock, bench, date(2026, 1, 1)) == ([], [], [])


def test_aligned_pct_no_overlap():
    stock = [("2026-01-01", 10), ("2026-01-02", 11)]
    bench = [("2026-01-03", 100), ("2026-01-04", 110)]
    assert oc.aligned_pct(stock, bench, date(2026, 1, 1)) == ([], [], [])


def test_aligned_pct_respects_start():
    stock = [("2025-12-31", 9), ("2026-01-01", 10), ("2026-01-02", 11)]
    bench = [("2025-12-31", 90), ("2026-01-01", 100), ("2026-01-02", 110)]
    dates, stock_pcts, bench_pcts = oc.aligned_pct(stock, bench, date(2026, 1, 1))
    assert dates == ["2026-01-01", "2026-01-02"]
    assert stock_pcts[0] == 0.0
    assert bench_pcts[0] == 0.0


# ── total_and_cagr ──

def test_total_and_cagr_two_year_span():
    total, cagr = oc.total_and_cagr([0.0, 0.21], ["2024-09-25", "2026-09-25"])
    assert total == 0.21
    assert cagr is not None
    assert abs(cagr - 0.1) < 1e-3


def test_total_and_cagr_span_under_min_years_returns_none_cagr():
    total, cagr = oc.total_and_cagr([0.0, 0.05], ["2026-01-01", "2026-06-01"])
    assert total == 0.05
    assert cagr is None


def test_total_and_cagr_empty():
    assert oc.total_and_cagr([], []) == (None, None)


# ── header_html ──

def test_header_html_contains_expected_fragments():
    html = oc.header_html("NFLX", "5Y", 0.4, 0.07, 0.682, None)
    assert "NFLX · 5Y" in html
    assert "+40.0%" in html
    assert "CAGR +7.0%" in html
    assert "S&amp;P 500" in html
    assert "+68.2%" in html
    assert html.count("CAGR") == 1
    assert "$" not in html


def test_header_html_negative_values_use_red():
    html = oc.header_html("XYZ", "1Y", -0.1, None, -0.05, None)
    assert "var(--red" in html


def test_header_html_nonnegative_uses_green():
    html = oc.header_html("XYZ", "1Y", 0.0, None, 0.05, None)
    assert "var(--green" in html


def test_header_html_none_stock_total_renders_dash_and_omits_cagr():
    html = oc.header_html("NFLX", "1M", None, None, 0.05, None)
    assert "—" in html
    assert "+5.0%" in html
    assert "CAGR" not in html


def test_header_html_both_totals_none_renders_two_dashes():
    html = oc.header_html("NFLX", "1M", None, None, None, None)
    assert html.count("—") == 2


# ── figure ──

def test_figure_returns_two_traces_with_expected_y():
    dates = ["2026-01-01", "2026-01-02"]
    stock_pcts = [0.0, 0.05]
    bench_pcts = [0.0, 0.03]
    theme = {"accent": "#2f7d4f", "bench": "#888888"}
    fig = oc.figure(dates, stock_pcts, bench_pcts, "NFLX", theme)
    assert len(fig.data) == 2
    assert list(fig.data[0].y) == stock_pcts
    assert list(fig.data[1].y) == bench_pcts
