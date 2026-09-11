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
