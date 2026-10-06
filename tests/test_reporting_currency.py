import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reporting_currency as rc

NOW = 1.17          # USD per EUR today
HIST = {date(2026, 3, 2): 1.05, date(2026, 3, 3): 1.06, date(2026, 6, 1): 1.12}


def _buy(day, net, qty=1.0):
    return {"instrument_type": "Equity", "type": "Trade", "action": "Buy to Open",
            "quantity": qty, "net_value": net, "date": day}


def test_normalise_defaults_to_eur():
    assert rc.normalise(None) == "EUR"
    assert rc.normalise("usd") == "USD"
    assert rc.normalise("GBP") == "EUR"


def test_rate_on_uses_the_last_rate_before_a_gap():
    assert rc.rate_on(HIST, date(2026, 3, 3)) == 1.06
    # Saturday 7 March: last known is the 3rd.
    assert rc.rate_on(HIST, date(2026, 3, 7)) == 1.06
    assert rc.rate_on(HIST, "2026-06-15") == 1.12
    assert rc.rate_on(HIST, date(2026, 1, 1)) is None
    assert rc.rate_on({}, date(2026, 3, 3)) is None


def test_meta_trading_212_uses_its_own_euro_cost():
    # 2026-10-05: 3 META at $556.46, T212 totalCost EUR 1,442.01.
    row = {"broker": "Trading 212", "shares_held": 3.0, "equity_cost": -1669.38,
           "option_pl": 0.0, "account_currency": "EUR", "account_cost": 1442.01,
           "native_currency": "USD", "fx_rate": 1.0, "market_value": 3 * 743.75,
           "trades": [_buy(date(2026, 3, 2), -1669.38, 3.0)]}
    rc.annotate(row, NOW, HIST)
    assert row["equity_cost_eur"] == -1442.01
    assert row["fx_missing"] is False
    value = rc.value_eur(row, NOW)
    assert value == pytest.approx(3 * 743.75 / NOW)
    pl = value + row["equity_cost_eur"]
    # Return in EUR includes the currency effect: higher than the 33.7% USD move.
    assert pl / 1442.01 == pytest.approx((3 * 743.75 / NOW - 1442.01) / 1442.01)


def test_tastytrade_purchase_converts_at_the_trade_date():
    # $1,000 bought when a euro cost $1.05 cost EUR 952.38, not 1000/1.17.
    row = {"broker": "Tastytrade", "equity_cost": -1000.0, "option_pl": 0.0,
           "trades": [_buy(date(2026, 3, 2), -1000.0)]}
    rc.annotate(row, NOW, HIST)
    assert row["equity_cost_eur"] == pytest.approx(-1000 / 1.05)
    assert row["fx_missing"] is False


def test_dividends_are_not_part_of_the_cost():
    row = {"broker": "Tastytrade", "equity_cost": -1000.0, "option_pl": 0.0,
           "trades": [_buy(date(2026, 3, 2), -1000.0),
                      {"instrument_type": "Equity", "type": "Money Movement",
                       "net_value": 12.0, "date": date(2026, 6, 1)}]}
    rc.annotate(row, NOW, HIST)
    assert row["equity_cost_eur"] == pytest.approx(-1000 / 1.05)


def test_part_of_the_cost_without_trades_converts_at_today_s_rate():
    # History starts after the position did: only $600 of the $1,000 is in trades.
    row = {"broker": "Tastytrade", "equity_cost": -1000.0, "option_pl": 0.0,
           "trades": [_buy(date(2026, 3, 2), -600.0)]}
    rc.annotate(row, NOW, HIST)
    assert row["equity_cost_eur"] == pytest.approx(-600 / 1.05 - 400 / NOW)


def test_wheel_premiums_convert_each_on_its_own_date():
    trades = [_buy(date(2026, 3, 2), -5000.0, 100.0),
              {"instrument_type": "Equity Option", "type": "Trade",
               "net_value": 120.0, "date": date(2026, 3, 3)},
              {"instrument_type": "Equity Option", "type": "Trade",
               "net_value": 80.0, "date": date(2026, 6, 1)}]
    row = {"broker": "Tastytrade", "equity_cost": -5000.0, "option_pl": 200.0,
           "trades": trades}
    rc.annotate(row, NOW, HIST)
    assert row["option_pl_eur"] == pytest.approx(120 / 1.06 + 80 / 1.12)
    assert rc.wheel_cost_eur(trades, -5000.0, NOW, HIST) == pytest.approx(-5000 / 1.05)


def test_no_history_falls_back_to_today_and_says_so():
    row = {"broker": "Tastytrade", "equity_cost": -1000.0, "option_pl": 0.0,
           "trades": [_buy(date(2026, 3, 2), -1000.0)]}
    rc.annotate(row, NOW, {})
    assert row["equity_cost_eur"] == pytest.approx(-1000 / NOW)
    assert row["fx_missing"] is True


def test_trading_212_without_wallet_impact_uses_its_fills_in_euros():
    row = {"broker": "Trading 212", "equity_cost": -1669.38, "option_pl": 0.0,
           "account_currency": "", "account_cost": None,
           "trades": [dict(_buy(date(2026, 3, 2), -1669.38, 3.0), wallet_net_value=-1442.01)]}
    rc.annotate(row, NOW, HIST)
    assert row["equity_cost_eur"] == pytest.approx(-1442.01)


def test_euro_quoted_instrument_is_not_converted():
    # IEQU: 250 at EUR 11.957, converted to USD at 1.16 on the way in.
    row = {"native_currency": "EUR", "fx_rate": 1.16, "market_value": 250 * 11.957 * 1.16}
    assert rc.value_eur(row, NOW) == pytest.approx(250 * 11.957)


def test_balance_of_a_euro_account_comes_back_in_its_own_euros():
    t212 = {"net_liquidating_value": 12000 * 1.16, "cash_balance": 300 * 1.16,
            "native_currency": "EUR", "fx_rate": 1.16}
    assert rc.balance_eur(t212, NOW) == pytest.approx((12000, 300))
    tt = {"net_liquidating_value": 11700.0, "cash_balance": 117.0}
    assert rc.balance_eur(tt, NOW) == pytest.approx((10000, 100))


def test_fmt_money():
    assert rc.fmt_money(1234.4, "EUR") == "€1,234"
    assert rc.fmt_money(-250, "USD", signed=True) == "$-250"
    assert rc.fmt_money(556.456, "EUR", decimals=2) == "€556.46"


def test_fx_rate_cache_expires_after_an_hour(monkeypatch):
    import fx
    import gather_data
    gather_data._FX_CACHE.clear()
    rates = iter([1.10, 1.20])
    monkeypatch.setattr(fx, "spot", lambda code: next(rates))
    clock = [1000.0]
    monkeypatch.setattr(gather_data.time, "time", lambda: clock[0])
    assert gather_data.fetch_fx_rate("EUR") == 1.10
    clock[0] += 1800
    assert gather_data.fetch_fx_rate("EUR") == 1.10   # still cached
    clock[0] += 1801
    assert gather_data.fetch_fx_rate("EUR") == 1.20   # refetched
    gather_data._FX_CACHE.clear()


def test_merge_sums_the_euro_basis_per_broker():
    import portfolio_metrics as pm
    tt = {"symbol": "NVDA", "broker": "Tastytrade", "shares_held": 5, "equity_cost": -900.0,
          "option_pl": 0.0, "total_pl": -900.0, "trades": [], "equity_cost_eur": -800.0,
          "option_pl_eur": 0.0, "fx_missing": False}
    t2 = {"symbol": "NVDA", "broker": "Trading 212", "shares_held": 3, "equity_cost": -620.0,
          "option_pl": 0.0, "total_pl": -620.0, "trades": [], "equity_cost_eur": -533.46,
          "option_pl_eur": 0.0, "fx_missing": True}
    merged = pm.merge_by_symbol({"NVDA (Tastytrade)": tt, "NVDA (Trading 212)": t2})["NVDA"]
    assert merged["equity_cost_eur"] == pytest.approx(-1333.46)
    assert merged["fx_missing"] is True
    t2.pop("equity_cost_eur")
    merged = pm.merge_by_symbol({"a": tt, "b": t2})["NVDA"]
    assert "equity_cost_eur" not in merged


# ── Step 2: euro copy for Results ──

def _sell(day, net, qty=1.0):
    return {"instrument_type": "Equity", "type": "Trade", "action": "Sell to Close",
            "quantity": qty, "net_value": net, "date": day}


def test_msft_example_realizes_thirty_euros_not_a_hundred_dollars():
    import portfolio_metrics as pm
    hist = {date(2026, 3, 2): 1.05, date(2026, 6, 1): 1.12}
    cb = {"MSFT": {"broker": "Tastytrade", "shares_held": 0, "equity_cost": 100.0,
                   "option_pl": 0.0, "dividends": 0.0, "total_pl": 100.0,
                   "trades": [_buy(date(2026, 3, 2), -1000.0, 2.0),
                              _sell(date(2026, 6, 1), 1100.0, 2.0)]}}
    eur = rc.to_eur_cost_basis(cb, NOW, hist)["MSFT"]
    realized = sum(s["realized"] for s in pm.fifo_realized(eur["trades"]))
    assert realized == pytest.approx(1100 / 1.12 - 1000 / 1.05)   # ~ +29.8
    assert eur["total_pl"] == pytest.approx(1100 / 1.12 - 1000 / 1.05)
    assert cb["MSFT"]["trades"][0]["net_value"] == -1000.0          # original untouched


def test_euro_copy_of_a_trading_212_row():
    cb = {"META": {"broker": "Trading 212", "shares_held": 3.0, "equity_cost": -1669.38,
                   "option_pl": 0.0, "dividends": 0.0, "total_pl": -1669.38,
                   "account_currency": "EUR", "account_cost": 1442.01,
                   "native_currency": "USD", "fx_rate": 1.0,
                   "current_price": 743.75, "previous_close": 730.0,
                   "trades": [dict(_buy(date(2026, 3, 2), -1669.38, 3.0),
                                   price=556.46, wallet_net_value=-1442.01)]}}
    eur = rc.to_eur_cost_basis(cb, NOW, HIST)["META"]
    assert eur["trades"][0]["net_value"] == -1442.01
    assert eur["trades"][0]["price"] == pytest.approx(1442.01 / 3)
    assert eur["equity_cost"] == -1442.01
    assert eur["current_price"] == pytest.approx(743.75 / NOW)
    assert eur["market_value"] == pytest.approx(3 * 743.75 / NOW)
    assert eur["total_pl_real"] == pytest.approx(-1442.01 + 3 * 743.75 / NOW)
    assert eur["currency"] == "EUR" and eur["fx_rate"] == 1.0


def test_euro_copy_categorises_dividends_and_options():
    trades = [_buy(date(2026, 3, 2), -1000.0),
              {"instrument_type": "Equity", "type": "Money Movement",
               "net_value": 10.6, "date": date(2026, 3, 3)},
              {"instrument_type": "Equity Option", "type": "Trade",
               "net_value": 112.0, "date": date(2026, 6, 1)}]
    cb = {"X": {"broker": "Tastytrade", "shares_held": 1, "equity_cost": -1000.0,
                "option_pl": 112.0, "dividends": 10.6, "total_pl": -877.4,
                "current_price": 1100.0, "trades": trades}}
    eur = rc.to_eur_cost_basis(cb, NOW, HIST)["X"]
    assert eur["equity_cost"] == pytest.approx(-1000 / 1.05)
    assert eur["dividends"] == pytest.approx(10.6 / 1.06)
    assert eur["option_pl"] == pytest.approx(112 / 1.12)


def test_euro_quoted_line_keeps_its_euros_in_the_copy():
    cb = {"IEQU": {"broker": "Trading 212", "shares_held": 250.0, "equity_cost": -3500.0,
                   "option_pl": 0.0, "dividends": 0.0, "total_pl": -3500.0,
                   "account_currency": "EUR", "account_cost": 3044.0,
                   "native_currency": "EUR", "fx_rate": 1.16,
                   "current_price": 11.957 * 1.16, "trades": []}}
    eur = rc.to_eur_cost_basis(cb, NOW, HIST)["IEQU"]
    assert eur["current_price"] == pytest.approx(11.957)


def test_curve_converts_each_day_at_its_own_rate():
    series = [{"time": "2026-03-02", "close": 1050.0}, {"time": "2026-06-01", "close": 1120.0}]
    out = rc.series_to_eur(series, HIST, NOW)
    assert [p["close"] for p in out] == pytest.approx([1000.0, 1000.0])


def test_closes_to_eur():
    assert rc.closes_to_eur({date(2026, 3, 2): 105.0}, HIST, NOW) == {date(2026, 3, 2): pytest.approx(100.0)}


def test_monthly_transfers_fallback_uses_mid_month_rate():
    out = rc.transfers_monthly_to_eur({2026: {"total": 1120.0, "months": {6: 1120.0}}},
                                      {date(2026, 6, 1): 1.12}, NOW)
    assert out[2026]["months"][6] == pytest.approx(1000.0)
    assert out[2026]["total"] == pytest.approx(1000.0)


def test_report_move_includes_the_currency_move_in_a_euro_copy():
    hist = {date(2026, 2, 27): 1.05, date(2026, 3, 31): 1.10}
    row = {"_eur_history": hist, "native_currency": "USD", "fx_rate": 1.0}
    # Price flat in USD, dollar weaker: a euro loss.
    move = rc.report_move(10, 100.0, 100.0, date(2026, 2, 28), date(2026, 3, 31), row)
    assert move == pytest.approx(10 * (100 / 1.10 - 100 / 1.05))
    usd_row = {"fx_rate": 1.0}
    assert rc.report_move(10, 100.0, 110.0, None, None, usd_row) == pytest.approx(100.0)


def test_trading_212_deposits_in_eur_are_its_own_amounts(monkeypatch):
    import gather_data
    import t212_api
    moves = [{"date": date(2026, 3, 2), "amount": 1000.0, "type": "DEPOSIT"},
             {"date": date(2026, 6, 1), "amount": -200.0, "type": "WITHDRAW"},
             {"date": date(2026, 6, 2), "amount": 3.0, "type": "INTEREST"}]
    monkeypatch.setattr(t212_api, "fetch_cash_movements", lambda creds: moves)
    monkeypatch.setattr(gather_data, "fetch_fx_history", lambda c, y: HIST)
    eur = t212_api.fetch_yearly_transfers({}, "EUR")
    assert eur[2026]["total"] == 800.0
    assert eur[2026]["months"] == {3: 1000.0, 6: -200.0}
    usd = t212_api.fetch_yearly_transfers({})
    assert usd[2026]["total"] == pytest.approx(1000 * 1.05 - 200 * 1.12)


def test_transfer_between_own_brokers_nets_to_its_fx_difference():
    import broker_adapter
    # $1,120 out of Tastytrade on 1 June (rate 1.12) arrives as EUR 995 at T212.
    tt = {2026: {"total": -1120 / 1.12, "months": {6: -1120 / 1.12}}}
    t212 = {2026: {"total": 995.0, "months": {6: 995.0}}}
    merged = broker_adapter.merge_yearly_transfers([tt, t212])
    assert merged[2026]["total"] == pytest.approx(-5.0)


def test_benchmark_yearly_return_in_eur():
    import tastytrade_api
    points = [(date(2025, 12, 1), 100.0), (date(2026, 3, 1), 110.0)]
    hist = {date(2025, 12, 31): 1.00, date(2026, 3, 31): 1.10}
    usd = tastytrade_api.yearly_returns_from_monthly(points)
    eur = tastytrade_api.yearly_returns_from_monthly(points, hist)
    assert usd[2026] == pytest.approx(10.0)
    assert eur[2026] == pytest.approx(0.0)    # the whole gain was the dollar's
