"""add_manual_aspirant: tickers without SEC filings (owner request 2026-10-22).

Three lines, three currencies: MonotaRO (3064.T, JPY), Rightmove (RMV.L,
quoted in pence, reports in GBP) and EssilorLuxottica (EL.PA, EUR). Each test
checks that nothing touches EDGAR, that amounts stay in the reporting
currency, and that ROCE and net debt equal the arithmetic done by hand.
"""
import json
from unittest.mock import patch

import pytest

import gather_data as g
import mcp_server
import screener
from scorecard_utils import capital_employed, compute_roce_metric, roce_for_year

YEARS = list(range(2016, 2026))


def _series(start, step):
    return {str(y): start + step * i for i, y in enumerate(YEARS)}


def _fundamentals(scale, shares):
    """Ten years of plausible statements; `scale` sets the currency's size."""
    return {
        "revenue": _series(100 * scale, 10 * scale),
        "operating_income": _series(20 * scale, 2 * scale),
        "net_income": _series(15 * scale, 1.5 * scale),
        "total_assets": _series(150 * scale, 5 * scale),
        "current_liabilities": _series(40 * scale, 1 * scale),
        "cash": _series(10 * scale, 1 * scale),
        "short_term_investments": _series(5 * scale, 0),
        "total_debt": _series(30 * scale, 0),
        "short_term_debt": _series(4 * scale, 0),
        "operating_lease_liabilities": _series(6 * scale, 0),
        "cfo": _series(22 * scale, 2 * scale),
        "capex": _series(-5 * scale, 0),
        "da": _series(4 * scale, 0),
        "shares": {str(y): shares for y in YEARS},
        "total_equity": _series(80 * scale, 4 * scale),
    }


CASES = [
    # ticker, name, reporting, listing, fundamentals, raw quote, quote -> reporting
    ("3064.T", "MonotaRO", "JPY", "JPY", _fundamentals(1000, 497_000_000), 2292.0, 2292.0),
    ("RMV.L", "Rightmove", "GBP", "GBX", _fundamentals(1, 780_000_000), 546.0, 5.46),
    ("EL.PA", "EssilorLuxottica", "EUR", "EUR", _fundamentals(100, 458_000_000), 174.4, 174.4),
]


@pytest.fixture
def no_edgar(monkeypatch):
    """Any EDGAR or network call fails the test."""
    def boom(*a, **k):
        raise AssertionError("EDGAR/network must not be called for a manual ticker")
    for name in ("get_cik", "fetch_company_facts", "fetch_company_submissions",
                 "fetch_stock_price"):
        monkeypatch.setattr(g, name, boom)
    monkeypatch.setattr(g, "fetch_sector_betas", lambda: {})


def _add(ticker, name, rep, lst, fund, raw):
    saved = {}
    with patch.object(mcp_server, "get_supabase_client", return_value=object()), \
            patch.object(mcp_server.config_store, "load_config", return_value=None), \
            patch.object(mcp_server.config_store, "save_config",
                         side_effect=lambda c, t, cfg, user_id=None: saved.update(cfg)):
        msg = mcp_server._add_manual_aspirant_impl(
            ticker, name, rep, lst, "12-31", fund, stock_price=raw, user_id="u1")
    return msg, saved


@pytest.mark.parametrize("ticker,name,rep,lst,fund,raw,price", CASES)
def test_manual_aspirant_is_built_without_edgar(no_edgar, ticker, name, rep, lst,
                                               fund, raw, price):
    msg, cfg = _add(ticker, name, rep, lst, fund, raw)
    assert msg.startswith(f"Added {ticker} as Aspirant (manual, {rep})"), msg
    assert cfg["source"] == "manual" and cfg["category"] == "Aspirant"
    assert cfg["dcf_placeholder"] is True
    assert cfg["reporting_currency"] == rep and cfg["listing_currency"] == lst
    assert cfg["stock_price"] == pytest.approx(price)          # pence -> pounds
    assert cfg["base_revenue"] == fund["revenue"]["2025"]      # not converted
    assert cfg["fund_slice"]["currency"] == rep


@pytest.mark.parametrize("ticker,name,rep,lst,fund,raw,price", CASES)
def test_roce_and_net_debt_match_the_hand_calculation(no_edgar, ticker, name, rep, lst,
                                                     fund, raw, price):
    full = g.manual_fundamentals(_add(ticker, name, rep, lst, fund, raw)[1]
                                 ["fundamentals_overrides"], rep)
    i = full["years"].index(2025)
    v = {k: fund[k]["2025"] for k in fund}
    ce = v["total_assets"] - v["current_liabilities"] - v["cash"] - v["short_term_investments"]
    assert capital_employed(full, i) == pytest.approx(ce)
    pct, _ = roce_for_year(full, i)
    assert pct == pytest.approx(v["operating_income"] / ce * 100)
    metric, avg = compute_roce_metric(full)
    by_hand = sum(fund["operating_income"][str(y)] /
                  (fund["total_assets"][str(y)] - fund["current_liabilities"][str(y)]
                   - fund["cash"][str(y)] - fund["short_term_investments"][str(y)])
                  for y in YEARS) / len(YEARS) * 100
    assert metric == "ROCE" and avg == pytest.approx(by_hand)
    # net debt: debt - cash - short-term investments (screener definition)
    assert screener.net_debt_latest(full) == pytest.approx(
        v["total_debt"] - v["cash"] - v["short_term_investments"])
    # fcf derived as for EDGAR tickers: cfo + capex
    assert full["fcf"][i] == pytest.approx(v["cfo"] + v["capex"])


def test_get_fundamentals_for_a_manual_ticker_skips_edgar(no_edgar):
    _, cfg = _add(*CASES[0][:6])
    with patch.object(mcp_server, "get_supabase_client", return_value=object()), \
            patch.object(mcp_server.config_store, "load_config", return_value=cfg):
        out = json.loads(mcp_server._get_fundamentals_impl("3064.T", n_years=10))
    h = out["headline"]
    v = {k: CASES[0][4][k]["2025"] for k in CASES[0][4]}
    adj_debt = (v["total_debt"] + v["short_term_debt"] + v["operating_lease_liabilities"])
    assert h["latest_adjusted_net_debt_m"] == pytest.approx(adj_debt - v["cash"], abs=1)


def test_too_few_years_is_refused(no_edgar):
    fund = {k: {y: val for y, val in list(s.items())[-5:]}
            for k, s in _fundamentals(1, 1e6).items()}
    msg, cfg = _add("X.PA", "X", "EUR", "EUR", fund, 10.0)
    assert "at least 10 years" in json.loads(msg)["error"] and not cfg


def test_unknown_field_is_refused(no_edgar):
    fund = dict(_fundamentals(1, 1e6), ebitda={"2025": 1})
    msg, _ = _add("X.PA", "X", "EUR", "EUR", fund, 10.0)
    assert "Unknown fundamentals fields: ebitda" in json.loads(msg)["error"]


def test_an_existing_ticker_is_not_overwritten(no_edgar):
    with patch.object(mcp_server, "get_supabase_client", return_value=object()), \
            patch.object(mcp_server.config_store, "load_config", return_value={"x": 1}), \
            patch.object(mcp_server.config_store, "save_config") as save:
        msg = mcp_server._add_manual_aspirant_impl(
            "EL.PA", "E", "EUR", "EUR", None, {}, stock_price=1, user_id="u1")
    assert "already on the watchlist" in msg and not save.called


def test_manual_source_takes_the_currency_from_the_suffix():
    assert g.manual_source({"ticker": "RMS.PA", "source": "manual"}) == "EUR"
    assert g.manual_source({"ticker": "RMV.L", "source": "manual", "currency": "GBP"}) == "GBP"
    assert g.manual_source({"ticker": "3064.T", "source": "manual",
                            "reporting_currency": "JPY"}) == "JPY"
    assert g.manual_source({"ticker": "META"}) is None
