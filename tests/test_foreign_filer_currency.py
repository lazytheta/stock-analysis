"""20-F filers that report in their own currency (owner request 2026-10-10).

add_aspirant("SPOT") and add_aspirant("NVO") failed with "Could not find IFRS
revenue data": the IFRS parser read only USD, and Spotify tags EUR, Novo
Nordisk DKK. These tests pin the fix: the reporting currency is detected from
the XBRL unit, the fundamentals stay in it, and the DCF config is restated in
the price's currency at one recorded rate.

The NVO figures are the real FY2025 20-F values (DKK), so the ROCE test is
the same arithmetic as checking the annual report by hand.
"""
import contextlib
from unittest.mock import patch

import pytest

import gather_data as g
from dcf_calculator import compute_intrinsic_value
from scorecard_utils import capital_employed, roce_for_year

B = 1_000_000_000


def _annual(unit, *pairs, form="20-F"):
    return {"units": {unit: [
        {"form": form, "start": f"{y}-01-01", "end": f"{y}-12-31", "val": v,
         "filed": f"{y + 1}-02-01"} for y, v in pairs]}}


def _point(unit, *pairs, form="20-F"):
    return {"units": {unit: [
        {"form": form, "end": f"{y}-12-31", "val": v, "filed": f"{y + 1}-02-01"}
        for y, v in pairs]}}


def _nvo_facts():
    """Novo Nordisk: ifrs-full in DKK only, no dei cover-page share count."""
    return {"facts": {"ifrs-full": {
        "Revenue": _annual("DKK", (2024, 290.403 * B), (2025, 309.064 * B)),
        "CostOfSales": _annual("DKK", (2024, 44.522 * B), (2025, 55.000 * B)),
        "ProfitLossFromOperatingActivities": _annual("DKK", (2024, 128.339 * B),
                                                     (2025, 127.658 * B)),
        "ProfitLoss": _annual("DKK", (2024, 100.988 * B), (2025, 102.000 * B)),
        "ProfitLossBeforeTax": _annual("DKK", (2025, 130.000 * B)),
        "IncomeTaxExpenseContinuingOperations": _annual("DKK", (2025, 28.000 * B)),
        "Assets": _point("DKK", (2024, 465.630 * B), (2025, 542.902 * B)),
        "CurrentAssets": _point("DKK", (2024, 160.000 * B), (2025, 170.000 * B)),
        "CurrentLiabilities": _point("DKK", (2024, 217.614 * B), (2025, 215.661 * B)),
        "CashAndCashEquivalents": _point("DKK", (2024, 15.655 * B), (2025, 26.464 * B)),
        "PropertyPlantAndEquipment": _point("DKK", (2024, 150.000 * B), (2025, 190.000 * B)),
        "NoncurrentBorrowings": _point("DKK", (2025, 100.000 * B)),
        "NumberOfSharesOutstanding": _point("shares", (2024, 4_441_000_000),
                                            (2025, 4_444_000_000)),
    }}}


def _spot_facts():
    """Spotify: ifrs-full in EUR, ordinary shares on the NYSE (dei count)."""
    return {"facts": {
        "ifrs-full": {
            "Revenue": _annual("EUR", (2024, 15.673 * B), (2025, 17.186 * B)),
            "ProfitLossFromOperatingActivities": _annual("EUR", (2024, 1.365 * B),
                                                         (2025, 2.198 * B)),
            "ProfitLoss": _annual("EUR", (2025, 1.900 * B)),
            "Assets": _point("EUR", (2025, 14.000 * B)),
            "CurrentLiabilities": _point("EUR", (2025, 7.000 * B)),
            "CashAndCashEquivalents": _point("EUR", (2025, 5.258 * B)),
        },
        "dei": {"EntityCommonStockSharesOutstanding": _point("shares", (2025, 205_832_527))},
    }}


def _asml_facts():
    """ASML: a 20-F under us-gaap, EUR only."""
    return {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax":
            _annual("EUR", (2024, 28.263 * B), (2025, 32.667 * B)),
        "OperatingIncomeLoss": _annual("EUR", (2024, 9.016 * B), (2025, 11.301 * B)),
        "Assets": _point("EUR", (2025, 50.000 * B)),
        "WeightedAverageNumberOfDilutedSharesOutstanding":
            _annual("shares", (2024, 394_000_000), (2025, 389_000_000)),
    }}}


_RATES = {"EUR": 1.12, "DKK": 0.15}


def _build(ticker, facts, price):
    with contextlib.ExitStack() as s:
        s.enter_context(patch.object(g, "get_cik", return_value="0000353278"))
        s.enter_context(patch.object(g, "fetch_company_submissions",
                                     return_value={"name": ticker, "sic": "2834",
                                                   "sicDescription": "Pharma"}))
        s.enter_context(patch.object(g, "resolve_sector_betas",
                                     return_value=[("Drugs", 1.0, 1.0)]))
        s.enter_context(patch.object(g, "fetch_company_facts", return_value=facts))
        s.enter_context(patch.object(g, "fetch_fx_rate", side_effect=_RATES.get))
        return g.build_base_config(ticker, stock_price=price)


# ── currency detection ─────────────────────────────────────────────────

def test_the_reporting_currency_comes_from_the_xbrl_unit():
    assert g.parse_financials(_nvo_facts())["currency"] == "DKK"
    assert g.parse_financials(_spot_facts())["currency"] == "EUR"
    assert g.parse_financials(_asml_facts())["currency"] == "EUR"


def test_a_us_filer_stays_on_dollars():
    facts = {"facts": {"us-gaap": {
        "Revenues": _annual("USD", (2024, 10 * B), (2025, 12 * B), form="10-K")}}}
    assert g._gaap_unit(facts) == "USD"
    assert g.parse_financials(facts)["currency"] == "USD"


def test_a_dollar_translation_is_still_preferred_where_it_exists():
    """TSM tags TWD and USD; the USD figures stay the ones used."""
    facts = {"facts": {"ifrs-full": {"Revenue": {"units": {
        "TWD": [{"form": "20-F", "start": "2025-01-01", "end": "2025-12-31", "val": 3e12}],
        "USD": [{"form": "20-F", "start": "2025-01-01", "end": "2025-12-31", "val": 9e10}],
    }}}}}
    fin = g.parse_financials(facts)
    assert fin["currency"] == "USD" and fin["revenue"] == [90_000]


def test_nvo_shares_fall_back_to_the_ifrs_count_without_a_cover_page():
    fin = g.parse_financials(_nvo_facts())
    assert fin["shares"][-1] == 4_444
    assert fin["revenue"][-1] == 309_064          # DKK millions, untouched


# ── restating in the price currency ────────────────────────────────────

def test_to_price_currency_converts_money_and_leaves_counts():
    fin = g.parse_financials(_nvo_facts())
    with patch.object(g, "fetch_fx_rate", side_effect=_RATES.get):
        out, fx = g.to_price_currency(fin, "USD")
    assert fx["reporting_currency"] == "DKK" and fx["fx_rate"] == 0.15 and fx["fx_date"]
    assert out["revenue"][-1] == round(309_064 * 0.15)
    assert out["shares"] == fin["shares"] and out["years"] == fin["years"]
    assert out["currency"] == "USD"
    assert fin["currency"] == "DKK"                # the input is not mutated


def test_no_rate_is_an_error_not_a_silent_one_to_one():
    fin = g.parse_financials(_nvo_facts())
    with patch.object(g, "fetch_fx_rate", return_value=None), \
            pytest.raises(ValueError, match="DKK/USD"):
        g.to_price_currency(fin, "USD")


def test_a_dollar_filer_passes_through_unchanged():
    fin = {"years": [2025], "revenue": [100.0], "currency": "USD"}
    out, fx = g.to_price_currency(fin, "USD")
    assert out is fin and fx["fx_rate"] == 1.0
    assert g.currency_fields(fx, "JNJ") == {}      # US configs gain no keys


# ── add_aspirant's route end to end ────────────────────────────────────

def test_nvo_config_records_currency_rate_and_adr_ratio():
    cfg = _build("NVO", _nvo_facts(), price=60.0)
    assert cfg["reporting_currency"] == "DKK"
    assert cfg["fx_rate"] == 0.15 and cfg["fx_date"]
    assert cfg["adr_ratio"] == 1                   # 1 ADR = 1 B share (20-F)
    assert "DKK" in cfg["_currency_note"]
    assert cfg["base_revenue"] == round(309_064 * 0.15)      # USD millions
    assert cfg["shares_outstanding"] == 4_444
    assert cfg["equity_market_value"] == 60.0 * 4_444


def test_nvo_fundamentals_stay_in_dkk_and_roce_matches_the_balance_sheet():
    cfg = _build("NVO", _nvo_facts(), price=60.0)
    fund = cfg["fund_slice"]
    assert fund["currency"] == "DKK"
    i = fund["years"].index(2025)
    assert fund["operating_income"][i] == 127_658
    # EBIT / (total assets − current liabilities − cash), all DKK millions
    ce = 542_902 - 215_661 - 26_464
    assert capital_employed(fund, i) == ce
    pct, capped = roce_for_year(fund, i)
    assert not capped and pct == pytest.approx(127_658 / ce * 100)
    assert pct == pytest.approx(42.44, abs=0.01)


def test_the_fair_value_is_in_the_price_currency():
    """Restating at one rate scales the per-share value by exactly that
    rate: the USD fair value equals the DKK fair value times 0.15."""
    usd = _build("NVO", _nvo_facts(), price=60.0)
    dkk = dict(usd)
    for key, val in usd.items():
        if key in ("hist_revenue", "hist_operating_income", "hist_net_income",
                   "hist_cost_of_revenue", "current_assets", "cash", "st_investments",
                   "current_liabilities", "st_debt", "st_leases", "net_ppe",
                   "goodwill_intang", "operating_cash"):
            dkk[key] = [v / 0.15 for v in val]
        elif key in ("base_revenue", "base_oi", "equity_market_value",
                     "debt_market_value", "cash_bridge", "securities",
                     "equity_investments", "minority_interest", "unfunded_pension",
                     "stock_price"):
            dkk[key] = val / 0.15
    dkk["debt_breakdown"] = [(n, v / 0.15) for n, v in usd["debt_breakdown"]]
    fv_usd = compute_intrinsic_value(usd)["intrinsic_value"]
    fv_dkk = compute_intrinsic_value(dkk)["intrinsic_value"]
    assert fv_usd == pytest.approx(fv_dkk * 0.15, rel=1e-3)


def test_spot_is_a_direct_listing_in_eur():
    cfg = _build("SPOT", _spot_facts(), price=600.0)
    assert cfg["reporting_currency"] == "EUR" and cfg["fx_rate"] == 1.12
    assert cfg["adr_ratio"] == 1
    assert cfg["shares_outstanding"] == 206        # dei cover page, millions
    assert cfg["base_revenue"] == round(17_186 * 1.12)
    assert cfg["fund_slice"]["currency"] == "EUR"
    i = cfg["fund_slice"]["years"].index(2025)
    assert cfg["fund_slice"]["operating_income"][i] == 2_198


def test_asml_us_gaap_in_eur_gets_its_fundamentals():
    cfg = _build("ASML", _asml_facts(), price=1000.0)
    assert cfg["reporting_currency"] == "EUR"
    fund = cfg["fund_slice"]
    assert fund["currency"] == "EUR"
    i = fund["years"].index(2025)
    assert fund["total_assets"][i] == 50_000       # read in EUR, not skipped


def test_a_company_without_sec_filings_gets_a_clear_error():
    with patch.object(g, "get_cik", side_effect=ValueError("not found")), \
            pytest.raises(ValueError, match="no SEC filings"):
        g.build_base_config("ESLOY", stock_price=50.0)
