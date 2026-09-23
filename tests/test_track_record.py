"""Track record: elke positie ooit gehad, tegen de index over haar eigen dagen."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from portfolio_metrics import track_record

INDEX = {date(2025, 1, 6): 100.0, date(2026, 3, 2): 120.0, date(2026, 8, 11): 150.0}
TODAY = date(2026, 8, 11)


def _buy(day, qty, price):
    return {"instrument_type": "Equity", "type": "Trade", "action": "Buy to Open",
            "quantity": qty, "price": price, "net_value": -qty * price, "date": day}


def _sell(day, qty, price):
    return {"instrument_type": "Equity", "type": "Trade", "action": "Sell to Close",
            "quantity": qty, "price": price, "net_value": qty * price, "date": day}


def test_an_open_lot_matches_relative_performance():
    r = track_record([_buy(date(2025, 1, 6), 10, 50.0)], 90.0, INDEX, TODAY)
    assert r["price_return"] == pytest.approx(80.0)
    assert r["index_return"] == pytest.approx(50.0)
    assert r["alpha"] == pytest.approx(30.0)
    assert r["closed"] is False


def test_a_closed_position_ends_its_window_on_the_sale_date():
    """Gekocht op 100 (SPY 100), verkocht op 2 maart 2026 voor 132 (SPY 120):
    +32% tegen +20%. Wat SPY daarna deed telt niet meer mee, want het geld
    zat er toen niet meer in."""
    r = track_record([_buy(date(2025, 1, 6), 10, 100.0), _sell(date(2026, 3, 2), 10, 132.0)],
                     999.0, INDEX, TODAY)
    assert r["price_return"] == pytest.approx(32.0)
    assert r["index_return"] == pytest.approx(20.0)
    assert r["alpha"] == pytest.approx(12.0)
    assert r["closed"] is True
    assert r["days_held"] == (date(2026, 3, 2) - date(2025, 1, 6)).days


def test_a_partial_sale_splits_the_lot_into_two_windows():
    """Tien gekocht op 100, vijf verkocht op 2 maart voor 120, vijf nog in
    bezit op 150 vandaag. Verkocht deel: +20% tegen +20%. Open deel: +50%
    tegen +50%. Samen precies de index."""
    r = track_record([_buy(date(2025, 1, 6), 10, 100.0), _sell(date(2026, 3, 2), 5, 120.0)],
                     150.0, INDEX, TODAY)
    assert r["alpha"] == pytest.approx(0.0)
    assert r["closed"] is False


def test_premium_and_dividends_lift_the_total_but_not_the_price_alpha():
    r = track_record([_buy(date(2025, 1, 6), 10, 50.0)], 90.0, INDEX, TODAY,
                     option_pl=100.0, dividends=25.0)
    assert r["alpha"] == pytest.approx(30.0)
    # (900 + 125) / 500 - 1 = 105%; tegen 50% index: 55 punten
    assert r["total_return"] == pytest.approx(105.0)
    assert r["total_alpha"] == pytest.approx(55.0)


def test_a_lot_older_than_the_index_history_is_not_guessed():
    r = track_record([_buy(date(2020, 1, 1), 1, 50.0)], 75.0, INDEX, TODAY)
    assert r["alpha"] is None
    assert r["uncovered_cost"] == 50.0


def test_no_trades_is_no_claim():
    r = track_record([], 10.0, INDEX, TODAY)
    assert r["alpha"] is None and r["cost"] == 0.0


def test_the_pill_says_what_you_could_have_had():
    import streamlit_app
    theme = {"accent": "#0a0", "red": "#a00", "text": "#000", "text_muted": "#888"}
    rows = [{"alpha_usd": -49387.0, "total_alpha_usd": -25661.0, "cost": 200000.0},
            {"alpha_usd": 0.0, "total_alpha_usd": 0.0, "cost": 13805.0}]
    html = streamlit_app._track_record_pill_html(rows, theme)
    assert "vs SPY" in html and "-$25,661" in html
    assert "(-12 pts)" in html          # geldgewogen: -25661 / 213805
    assert "you could have had $25,661 more" in html
    assert "trailed SPY by $49,387" in html and "won back $23,726" in html


def test_a_winning_record_is_not_phrased_as_a_loss():
    import streamlit_app
    theme = {"accent": "#0a0", "red": "#a00", "text": "#000", "text_muted": "#888"}
    html = streamlit_app._track_record_pill_html(
        [{"alpha_usd": 1000.0, "total_alpha_usd": 1500.0, "cost": 10000.0}], theme)
    assert "+$1,500" in html and "(+15 pts)" in html and "beat SPY by $1,000" in html


def test_since_keeps_only_the_lots_bought_from_that_day():
    """MSFT: één stuk in maart, één in juli. Vanaf 28 juli telt alleen de
    juli-aankoop; de FIFO-loop blijft heel, want er is niets verkocht."""
    trades = [_buy(date(2026, 3, 30), 1, 359.0), _buy(date(2026, 7, 29), 1, 400.0)]
    idx = {date(2026, 3, 30): 100.0, date(2026, 7, 29): 120.0, date(2026, 8, 11): 150.0}
    r = track_record(trades, 480.0, idx, TODAY, since=date(2026, 7, 28))
    assert r["cost"] == pytest.approx(400.0)
    assert r["price_return"] == pytest.approx(20.0)
    assert r["index_return"] == pytest.approx(25.0)
    r_before = track_record(trades, 480.0, idx, TODAY, before=date(2026, 7, 28))
    assert r_before["cost"] == pytest.approx(359.0)


def test_a_sale_still_consumes_the_oldest_lot_when_filtering():
    """Tien in januari, tien in augustus, tien verkocht in september. FIFO
    verkoopt de januari-stukken; de augustus-stukken zijn nog open en horen
    volledig bij de nieuwe strategie."""
    trades = [_buy(date(2025, 1, 6), 10, 100.0), _buy(date(2026, 3, 2), 10, 120.0),
              _sell(date(2026, 8, 11), 10, 150.0)]
    r = track_record(trades, 150.0, INDEX, TODAY, since=date(2026, 3, 1))
    assert r["cost"] == pytest.approx(1200.0)
    assert r["closed"] is False


def test_premium_is_prorated_by_the_cost_inside_the_window():
    trades = [_buy(date(2025, 1, 6), 10, 50.0), _buy(date(2026, 3, 2), 10, 50.0)]
    r = track_record(trades, 90.0, INDEX, TODAY, option_pl=100.0, since=date(2026, 3, 1))
    # helft van de kosten in het venster -> helft van de premie
    assert r["total_return"] == pytest.approx((900 + 50) / 500 * 100 - 100)


def test_the_strategy_window_return_is_deposit_adjusted():
    """Van 100 naar 130 met 20 gestort: (130 - 100 - 20) / (100 + 10) = 9.1%."""
    import streamlit_app
    series = [{"time": "2026-07-28", "close": 100.0}, {"time": "2026-08-15", "close": 118.0},
              {"time": "2026-09-22", "close": 130.0}]
    transfers = {2026: {"total": 20.0, "months": {8: 20.0}}}
    out = streamlit_app._dietz_return(series, transfers, date(2026, 7, 28))
    assert out == pytest.approx(10 / 110 * 100)


def test_the_strategy_window_ignores_deposits_before_it():
    import streamlit_app
    series = [{"time": "2026-07-28", "close": 100.0}, {"time": "2026-09-22", "close": 110.0}]
    transfers = {2026: {"total": 50.0, "months": {3: 50.0}}}
    assert streamlit_app._dietz_return(series, transfers, date(2026, 7, 28)) == pytest.approx(10.0)


def test_rows_from_two_brokers_add_up_to_one_line_per_symbol():
    import streamlit_app
    rows = [{"ticker": "NVDA", "cost": 1000.0, "alpha_usd": 100.0, "total_alpha_usd": 100.0,
             "days_held": 40, "closed": False, "since_sale": None},
            {"ticker": "NVDA", "cost": 3000.0, "alpha_usd": -60.0, "total_alpha_usd": 0.0,
             "days_held": 80, "closed": False, "since_sale": None}]
    (m,) = streamlit_app._merge_track_rows(rows)
    assert m["cost"] == 4000.0 and m["alpha_usd"] == 40.0
    assert m["alpha"] == pytest.approx(1.0)
    assert m["days_held"] == 70          # kostengewogen: (1000*40 + 3000*80) / 4000
    assert m["closed"] is False
