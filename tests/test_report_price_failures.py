"""Week- en maandrapport: een mislukte koers mag niet stil als 0 meetellen."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import urllib.request

import streamlit_app


def _cost_basis_with_shares():
    # De aggregatie leest `shares_held`, niet `shares`.
    return {"HELD": {"shares_held": 10, "trades": [], "avg_cost": 100.0}}


def _urlopen_that_fails(*a, **k):
    raise OSError("429 Too Many Requests")


def test_month_report_names_the_tickers_whose_quote_failed(monkeypatch):
    """`except Exception: pass` liet equity_pl op 0 en has_equity op False:
    het rapport rendert dan zonder de aandelenbeen, en de 'net P/L' telt
    alleen optiepremie. Wie een 429 kreeg zag geen enkel signaal."""
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_that_fails)
    agg = streamlit_app._aggregate_month_trades(_cost_basis_with_shares(), 2026, 8)
    assert agg["price_failures"] == ["HELD"]


def test_week_report_names_the_tickers_whose_quote_failed(monkeypatch):
    from datetime import date
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_that_fails)
    agg = streamlit_app._aggregate_week_trades(
        _cost_basis_with_shares(), date(2026, 8, 3), date(2026, 8, 9))
    assert agg["price_failures"] == ["HELD"]


def test_a_report_without_held_shares_has_no_failures(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _urlopen_that_fails)
    agg = streamlit_app._aggregate_month_trades({}, 2026, 8)
    assert agg["price_failures"] == []


# ── Europese lijnen van Trading 212 ──────────────────────────────────────────
#
# De rij heet bij T212 "RMS" en "IEQU"; bij Yahoo bestaan die namen niet, wel
# RMS.PA en IEQU.MI. De rij draagt beurs, valuta en ISIN, dus de kandidaten
# zijn af te leiden -- t212_history doet dat al voor de net-liq-curve.

import json
import urllib.error
from datetime import UTC, date, datetime

import pytest


def _yahoo_that_knows(symbols_ok, closes_by_day):
    """urlopen dat alleen `symbols_ok` kent; al het andere krijgt een 404."""
    def _urlopen(req, *a, **k):
        url = req.full_url
        symbol = url.split("/chart/")[1].split("?")[0]
        if symbol not in symbols_ok:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        stamps = [int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
                  for d in closes_by_day]
        body = {"chart": {"result": [{
            "timestamp": stamps,
            "indicators": {"quote": [{"close": list(closes_by_day.values())}]},
        }]}}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps(body).encode()
        return _Resp()
    return _urlopen


def _t212_row(symbol, key=None, currency="EUR", isin=""):
    return {key or symbol: {"shares_held": 1, "trades": [], "symbol": symbol,
                            "exchange": "", "native_currency": currency,
                            "isin": isin}}


def test_month_report_finds_a_paris_line_under_its_yahoo_suffix(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _yahoo_that_knows(
        {"RMS.PA"}, {date(2026, 8, 1): 1300.0, date(2026, 9, 1): 1400.0}))
    agg = streamlit_app._aggregate_month_trades(
        _t212_row("RMS", isin="FR0000052292"), 2026, 9)
    assert agg["price_failures"] == []
    assert agg["leaders_pl"][0]["equity_pl"] == pytest.approx(100.0)


def test_week_report_finds_a_milan_etf_under_its_yahoo_suffix(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _yahoo_that_knows(
        {"IEQU.MI"}, {date(2026, 9, 4): 11.0, date(2026, 9, 11): 12.0}))
    agg = streamlit_app._aggregate_week_trades(
        _t212_row("IEQU", isin="IE00BQN1K562"),
        date(2026, 9, 7), date(2026, 9, 13))
    assert agg["price_failures"] == []
    assert agg["leaders_pl"][0]["equity_pl"] == pytest.approx(1.0)


def test_a_two_broker_key_is_looked_up_on_its_bare_symbol(monkeypatch):
    """Met DECK bij twee brokers heet de sleutel "DECK (Trading 212)"."""
    monkeypatch.setattr(urllib.request, "urlopen", _yahoo_that_knows(
        {"DECK"}, {date(2026, 8, 1): 80.0, date(2026, 9, 1): 90.0}))
    agg = streamlit_app._aggregate_month_trades(
        _t212_row("DECK", key="DECK (Trading 212)", currency="USD"), 2026, 9)
    assert agg["price_failures"] == []


def test_a_line_yahoo_knows_under_no_name_is_still_reported(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _yahoo_that_knows(set(), {}))
    agg = streamlit_app._aggregate_month_trades(_t212_row("XXXX"), 2026, 9)
    assert agg["price_failures"] == ["XXXX"]


def test_a_euro_line_is_counted_in_the_reports_dollars(monkeypatch):
    """Yahoo noteert RMS.PA in EUR; het rapport telt in USD. De rij draagt de
    koers waarmee T212 de positie al is omgerekend, dus die geldt hier ook."""
    monkeypatch.setattr(urllib.request, "urlopen", _yahoo_that_knows(
        {"RMS.PA"}, {date(2026, 8, 1): 1300.0, date(2026, 9, 1): 1400.0}))
    rows = _t212_row("RMS", isin="FR0000052292")
    rows["RMS"]["fx_rate"] = 1.15
    agg = streamlit_app._aggregate_month_trades(rows, 2026, 9)
    assert agg["leaders_pl"][0]["equity_pl"] == pytest.approx(115.0)
