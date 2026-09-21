"""Portfolio-pagina: de dagbeweging van een Europese Trading 212-lijn.

De rij kreeg van T212 een USD-koers maar geen vorige slotkoers, dus Day %
stond op +0,00% -- een storing die als "onveranderd" las. De watchlist had
al een tweede bron per ISIN (Xetra); de portfolio-pagina gaf die kaart nooit
mee. Xetra noteert in EUR en de pagina telt in USD, dus de beurs levert
alleen de verhouding vorige slot / laatste koers; de T212-koers blijft.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import streamlit_app


def _t212_row(symbol="RMS", broker_price=1543.45, currency="EUR",
              isin="FR0000052292"):
    return {"symbol": symbol, "broker_price": broker_price,
            "native_currency": currency, "isin": isin, "shares_held": 1}


def test_a_xetra_quote_supplies_the_day_move_on_the_broker_price():
    quote = {"price": 1329.0, "previousClose": 1327.5, "venue": "XETR"}
    out = streamlit_app._row_quote(quote, _t212_row())
    assert out["price"] == pytest.approx(1543.45)
    assert out["previousClose"] == pytest.approx(1543.45 * 1327.5 / 1329.0)


def test_without_any_quote_the_broker_price_stands_with_no_day_move():
    out = streamlit_app._row_quote(None, _t212_row())
    assert out == {"price": 1543.45, "previousClose": 1543.45}


def test_a_dollar_quote_passes_through_untouched():
    quote = {"price": 79.8, "previousClose": 78.43}
    out = streamlit_app._row_quote(quote, {"symbol": "DECK", "broker_price": 79.5})
    assert out is quote


def test_a_xetra_quote_without_previous_close_gives_no_day_move():
    quote = {"price": 1329.0, "previousClose": None, "venue": "XETR"}
    out = streamlit_app._row_quote(quote, _t212_row())
    assert out == {"price": 1543.45, "previousClose": 1543.45}


def test_no_quote_and_no_broker_price_is_unpriced():
    assert streamlit_app._row_quote(None, {"symbol": "X"}) is None


def test_isin_map_covers_only_non_dollar_lines_with_a_broker_price():
    rows = {
        "RMS": _t212_row("RMS"),
        "IEQU": _t212_row("IEQU", 13.78, isin="IE00BQN1K562"),
        # Amerikaanse lijn met ISIN: Tastytrade/Yahoo kennen die; Xetra hoeft
        # er niet aan te pas te komen, en zou een EUR-notering kunnen leveren.
        "DECK (Trading 212)": _t212_row("DECK", 79.5, "USD", "US2435371073"),
        # Gesloten: geen broker-koers om de verhouding op toe te passen.
        "OLD": {"symbol": "OLD", "broker_price": 0.0, "native_currency": "EUR",
                "isin": "NL0000000001"},
        "MSFT": {"symbol": "MSFT"},
    }
    assert streamlit_app._portfolio_isins(rows) == {
        "RMS": "FR0000052292", "IEQU": "IE00BQN1K562"}
