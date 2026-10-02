"""quarterly_results: quarterly revenue and diluted EPS from SEC companyfacts.

Synthetic companyfacts only; the fetch wrapper is tested with the network
calls monkeypatched out."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quarterly_results as qr

REV = "RevenueFromContractWithCustomerExcludingAssessedTax"


def f(start, end, val, filed, form="10-Q"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def facts(revenue=None, eps=None, shares=None, rev_tag=REV, eps_tag="EarningsPerShareDiluted",
          extra=None):
    gaap = {}
    if revenue is not None:
        gaap[rev_tag] = {"units": {"USD": revenue}}
    if eps is not None:
        gaap[eps_tag] = {"units": {"USD/shares": eps}}
    if shares is not None:
        gaap["WeightedAverageNumberOfDilutedSharesOutstanding"] = {"units": {"shares": shares}}
    gaap.update(extra or {})
    return {"facts": {"us-gaap": gaap}}


# Calendar-year filer: 2024 Q1-Q3 standalone, FY2024 total, 2025 Q1-Q3.
CAL_REV = [
    f("2024-01-01", "2024-03-31", 100, "2024-04-30"),
    f("2024-04-01", "2024-06-30", 110, "2024-07-30"),
    f("2024-07-01", "2024-09-30", 120, "2024-10-30"),
    f("2024-01-01", "2024-12-31", 470, "2025-02-15", "10-K"),
    f("2025-01-01", "2025-03-31", 120, "2025-04-30"),
    f("2025-04-01", "2025-06-30", 121, "2025-07-30"),
    f("2025-07-01", "2025-09-30", 150, "2025-10-30"),
    # six-month year-to-date figure: neither a quarter nor a year
    f("2025-01-01", "2025-06-30", 241, "2025-07-30"),
]
CAL_EPS = [
    f("2024-01-01", "2024-03-31", 1.00, "2024-04-30"),
    f("2024-04-01", "2024-06-30", 1.10, "2024-07-30"),
    f("2024-07-01", "2024-09-30", -0.20, "2024-10-30"),
    f("2024-01-01", "2024-12-31", 3.40, "2025-02-15", "10-K"),
    f("2025-01-01", "2025-03-31", 1.20, "2025-04-30"),
    f("2025-04-01", "2025-06-30", 1.32, "2025-07-30"),
    f("2025-07-01", "2025-09-30", 0.50, "2025-10-30"),
]


def _by_end(rows):
    return {r["end"]: r for r in rows}


def test_calendar_year_quarters_labels_and_derived_q4():
    rows = qr.quarterly_results(facts(CAL_REV, CAL_EPS))
    assert [r["end"] for r in rows] == ["2024-03-31", "2024-06-30", "2024-09-30",
                                        "2024-12-31", "2025-03-31", "2025-06-30",
                                        "2025-09-30"]
    by = _by_end(rows)
    q4 = by["2024-12-31"]
    assert q4["revenue"] == 140 and q4["eps"] == pytest.approx(1.50)
    assert q4["derived_q4"] is True and by["2024-09-30"]["derived_q4"] is False
    assert q4["fiscal_label"] == "Q4 FY2024"
    assert by["2024-03-31"]["fiscal_label"] == "Q1 FY2024"
    # FY2025 hasn't closed: its fiscal year end is extrapolated from FY2024's.
    assert by["2025-09-30"]["fiscal_label"] == "Q3 FY2025"
    assert set(q4) >= {"end", "fiscal_label", "revenue", "revenue_yoy", "eps", "eps_yoy"}


def test_yoy_against_the_same_quarter_a_year_earlier():
    by = _by_end(qr.quarterly_results(facts(CAL_REV, CAL_EPS)))
    assert by["2025-03-31"]["revenue_yoy"] == pytest.approx(0.20)
    assert by["2025-06-30"]["revenue_yoy"] == pytest.approx(0.10)
    assert by["2025-06-30"]["eps_yoy"] == pytest.approx(0.20)
    # A year earlier lost money: growth on a negative base means nothing.
    assert by["2025-09-30"]["eps_yoy"] is None
    assert by["2025-09-30"]["revenue_yoy"] == pytest.approx(0.25)
    assert by["2024-03-31"]["revenue_yoy"] is None and by["2024-03-31"]["eps_yoy"] is None


def test_q4_is_not_derived_without_all_three_earlier_quarters():
    rev = [e for e in CAL_REV if e["end"] != "2024-06-30"]
    by = _by_end(qr.quarterly_results(facts(rev)))
    assert "2024-12-31" not in by and "2024-06-30" not in by
    assert by["2024-03-31"]["eps"] is None


def test_standalone_q4_beats_derivation():
    rev = [*CAL_REV, f("2024-10-01", "2024-12-31", 139, "2025-02-15", "10-K")]
    q4 = _by_end(qr.quarterly_results(facts(rev)))["2024-12-31"]
    assert q4["revenue"] == 139 and q4["derived_q4"] is False


def test_duplicate_filings_keep_the_latest():
    rev = [
        *CAL_REV,
        # the 2025 10-Q repeats Q1 2024 as a comparative, restated
        f("2024-01-01", "2024-03-31", 105, "2025-04-30"),
        # an older amendment must not win over the newer comparative
        f("2024-01-01", "2024-03-31", 99, "2024-05-15", "10-Q/A"),
    ]
    by = _by_end(qr.quarterly_results(facts(rev)))
    assert by["2024-03-31"]["revenue"] == 105
    assert by["2025-03-31"]["revenue_yoy"] == pytest.approx(120 / 105 - 1)


def test_fiscal_year_not_matching_the_calendar_year():
    # June year-end, 52/53-week calendar (FY2025 ends 2025-06-28).
    rev = [
        f("2023-06-25", "2024-06-30", 1000, "2024-08-10", "10-K"),
        f("2024-07-01", "2024-09-29", 240, "2024-10-25"),
        f("2024-09-30", "2024-12-29", 250, "2025-01-30"),
        f("2024-12-30", "2025-03-30", 260, "2025-04-25"),
        f("2024-07-01", "2025-06-28", 1050, "2025-08-10", "10-K"),
        f("2025-06-29", "2025-09-28", 280, "2025-10-25"),
    ]
    rows = qr.quarterly_results(facts(rev))
    by = _by_end(rows)
    assert by["2024-09-29"]["fiscal_label"] == "Q1 FY2025"
    assert by["2024-12-29"]["fiscal_label"] == "Q2 FY2025"
    assert by["2025-03-30"]["fiscal_label"] == "Q3 FY2025"
    assert by["2025-06-28"]["fiscal_label"] == "Q4 FY2025"
    assert by["2025-06-28"]["revenue"] == 300 and by["2025-06-28"]["derived_q4"]
    assert by["2025-09-28"]["fiscal_label"] == "Q1 FY2026"
    assert by["2025-09-28"]["revenue_yoy"] == pytest.approx(280 / 240 - 1)


def test_label_falls_back_without_any_fiscal_year():
    rows = qr.quarterly_results(facts([f("2025-04-01", "2025-06-30", 5, "2025-07-30")]))
    assert rows[0]["fiscal_label"] == "Q ending 2025-06-30"


def test_last_twelve_quarters_ascending():
    rev = []
    for i in range(20):
        y, q = 2020 + i // 4, i % 4
        start = f"{y}-{3 * q + 1:02d}-01"
        end = {0: f"{y}-03-31", 1: f"{y}-06-30", 2: f"{y}-09-30", 3: f"{y}-12-31"}[q]
        rev.append(f(start, end, 100 + i, f"{y + 1}-02-01"))
    rows = qr.quarterly_results(facts(rev))
    assert len(rows) == 12
    assert rows[0]["end"] == "2022-03-31" and rows[-1]["end"] == "2024-12-31"
    # YoY of the oldest shown quarter still uses the quarter before the window.
    assert rows[0]["revenue_yoy"] == pytest.approx(108 / 104 - 1)
    assert len(qr.quarterly_results(facts(rev), n=4)) == 4


def test_revenue_tags_in_gather_data_priority_with_gap_fill():
    assert qr.REVENUE_TAGS[0] == REV and "Revenues" in qr.REVENUE_TAGS
    old = [f("2023-01-01", "2023-03-31", 90, "2023-04-30"),
           f("2024-01-01", "2024-03-31", 999, "2024-04-30")]
    new = [f("2024-01-01", "2024-03-31", 100, "2024-04-30"),
           f("2025-01-01", "2025-03-31", 120, "2025-04-30")]
    data = facts(new, extra={"Revenues": {"units": {"USD": old}}})
    by = _by_end(qr.quarterly_results(data))
    assert by["2024-03-31"]["revenue"] == 100     # the newer tag wins overlaps
    assert by["2023-03-31"]["revenue"] == 90      # the older one fills the gap


def test_eps_falls_back_to_basic():
    eps = [f("2025-01-01", "2025-03-31", 0.7, "2025-04-30")]
    rows = qr.quarterly_results(facts(eps=eps, eps_tag="EarningsPerShareBasic"))
    assert rows[0]["eps"] == 0.7 and rows[0]["revenue"] is None


def test_split_scales_earlier_eps():
    eps = [f("2024-01-01", "2024-03-31", 4.00, "2024-04-30"),
           f("2024-04-01", "2024-06-30", 4.40, "2024-07-30"),
           f("2024-07-01", "2024-09-30", 1.20, "2024-10-30"),
           f("2025-04-01", "2025-06-30", 1.32, "2025-07-30")]
    shares = [f("2024-01-01", "2024-03-31", 100e6, "2024-04-30"),
              f("2024-04-01", "2024-06-30", 99e6, "2024-07-30"),
              f("2024-07-01", "2024-09-30", 396e6, "2024-10-30")]
    by = _by_end(qr.quarterly_results(facts(eps=eps, shares=shares)))
    assert by["2024-03-31"]["eps"] == pytest.approx(1.00)
    assert by["2024-06-30"]["eps"] == pytest.approx(1.10)
    assert by["2024-03-31"]["split_adjusted"] is True
    assert by["2024-09-30"]["eps"] == pytest.approx(1.20)
    assert by["2024-09-30"]["split_adjusted"] is False
    assert by["2025-06-30"]["eps_yoy"] == pytest.approx(0.20)


def test_derived_q4_eps_reconciles_a_split_restated_year():
    # FY2023's EPS comes from a later 10-K, restated for a 10-for-1 split;
    # its Q1-Q3 only from pre-split 10-Qs.
    eps = [f("2023-01-01", "2023-03-31", 10.0, "2023-04-30"),
           f("2023-04-01", "2023-06-30", 11.0, "2023-07-30"),
           f("2023-07-01", "2023-09-30", 12.0, "2023-10-30"),
           f("2023-01-01", "2023-12-31", 4.6, "2025-02-15", "10-K")]
    shares = [f("2023-01-01", "2023-03-31", 100e6, "2023-04-30"),
              f("2023-04-01", "2023-06-30", 100e6, "2023-07-30"),
              f("2023-07-01", "2023-09-30", 100e6, "2023-10-30"),
              f("2023-01-01", "2023-12-31", 1000e6, "2025-02-15", "10-K")]
    q4 = _by_end(qr.quarterly_results(facts(eps=eps, shares=shares)))["2023-12-31"]
    assert q4["eps"] == pytest.approx(4.6 - 3.3)
    assert q4["derived_q4"] is True


def test_garbage_never_raises():
    for bad in (None, {}, {"facts": None}, {"facts": {"us-gaap": {REV: {"units": None}}}},
                facts([{"end": "x"}, {"start": "2025-01-01", "end": "2025-03-31"},
                       f("2025-01-01", "2025-03-31", "abc", "2025-04-30"), "junk"])):
        assert qr.quarterly_results(bad) == []


def test_fetch_uses_gather_data_and_returns_empty_without_cik(monkeypatch):
    import gather_data
    calls = []
    monkeypatch.setattr(gather_data, "get_cik", lambda t: calls.append(t) or 320193)
    monkeypatch.setattr(gather_data, "fetch_company_facts",
                        lambda cik: facts(CAL_REV, CAL_EPS))
    rows = qr.fetch_quarterly_results("aapl")
    assert calls == ["AAPL"] and len(rows) == 7

    def no_cik(t):
        raise ValueError("not in SEC")
    monkeypatch.setattr(gather_data, "get_cik", no_cik)
    assert qr.fetch_quarterly_results("RMS.PA") == []
