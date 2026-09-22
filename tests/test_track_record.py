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


def test_the_verdict_says_what_you_could_have_had():
    import streamlit_app
    theme = {"accent": "#0a0", "red": "#a00", "text": "#000", "text_muted": "#888"}
    rows = [{"alpha_usd": -49387.0, "total_alpha_usd": -25661.0},
            {"alpha_usd": 0.0, "total_alpha_usd": 0.0}]
    html = streamlit_app._track_record_verdict_html(rows, theme)
    assert "You could have had $25,661 more" in html
    assert "trailed SPY by $49,387" in html
    assert "won back $23,726" in html


def test_a_winning_record_is_not_phrased_as_a_loss():
    import streamlit_app
    theme = {"accent": "#0a0", "red": "#a00", "text": "#000", "text_muted": "#888"}
    html = streamlit_app._track_record_verdict_html(
        [{"alpha_usd": 1000.0, "total_alpha_usd": 1500.0}], theme)
    assert "You have $1,500 more" in html and "beat SPY by $1,000" in html
