import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import strategy_attribution as sa

S = date(2026, 7, 28)
T = date(2026, 10, 9)


def _buy(day, qty, price):
    return {"instrument_type": "Equity", "type": "Trade", "action": "Buy to Open",
            "quantity": qty, "price": price, "net_value": -qty * price, "date": day}


def _sell(day, qty, price):
    return {"instrument_type": "Equity", "type": "Trade", "action": "Sell to Close",
            "quantity": qty, "price": price, "net_value": qty * price, "date": day}


def test_dietz_parts_matches_the_page_rule():
    series = [{"time": "2026-07-28", "close": 10_000}, {"time": "2026-10-09", "close": 12_000}]
    transfers = {2026: {"total": 1000, "months": {8: 1000}}}
    pl, denom = sa.dietz_parts(series, transfers, S)
    assert pl == 1000 and denom == 10_500
    assert sa.dietz_parts(series[:1], transfers, S) is None


def test_pieces_fifo_sold_and_held():
    trades = [_buy(date(2026, 3, 1), 2, 360), _buy(date(2026, 7, 29), 1, 400),
              _sell(date(2026, 8, 10), 1, 380)]
    ps = sa.pieces(trades, 520)
    assert [(p["qty"], p["buy_price"], p["end_price"], p["end_date"]) for p in ps] == [
        (1, 360, 380, date(2026, 8, 10)), (1, 360, 520, None), (1, 400, 520, None)]


def test_attribution_buckets_add_up_to_the_portfolio():
    rows = {
        "MSFT": {"symbol": "MSFT", "current_price": 520.0,
                 "trades": [_buy(date(2026, 3, 30), 1, 360), _buy(date(2026, 7, 29), 1, 400)]},
        "IBIT": {"symbol": "IBIT", "current_price": 46.0,
                 "trades": [_buy(date(2025, 11, 22), 100, 52)]},
        "TTD": {"symbol": "TTD", "current_price": 0.0,
                "trades": [_buy(date(2026, 5, 8), 250, 22), _sell(date(2026, 8, 6), 250, 13.5),
                           {"instrument_type": "Equity Option", "type": "Trade",
                            "net_value": 50.0, "date": date(2026, 8, 1)}]},
    }
    start_prices = {"MSFT": 500.0, "IBIT": 50.0, "TTD": 15.0}
    out = sa.attribute(rows, S, start_prices, total_pl=-300.0, denom=20_000.0, spy_pct=4.7,
                       today=T)
    assert out["new"]["pl"] == pytest.approx(120)               # MSFT 400 -> 520
    assert out["old_held"]["pl"] == pytest.approx(20 + -400)    # MSFT 500->520, IBIT 50->46 x100
    assert out["old_sold"]["pl"] == pytest.approx(250 * (13.5 - 15))
    assert out["income"]["pl"] == pytest.approx(50)
    parts = sum(out[k]["pts"] for k in ("new", "old_held", "old_sold", "income", "other"))
    assert parts == pytest.approx(out["total_pts"]) == pytest.approx(-1.5)
    assert set(out["old_held"]["names"]) == {"MSFT", "IBIT"}
    assert 0 < out["cash_share"] < 1 and out["cash_drag_pts"] < 0


def test_missing_start_price_falls_into_other():
    rows = {"X": {"symbol": "X", "current_price": 10.0, "trades": [_buy(date(2026, 1, 5), 10, 8)]}}
    out = sa.attribute(rows, S, {}, total_pl=30.0, denom=1000.0, spy_pct=None, today=T)
    assert out["old_held"]["pl"] == 0 and out["other"]["pl"] == 30.0
    assert out["cash_drag_pts"] is None
