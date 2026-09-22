"""Per watchlistrij één munt: koers, fair value en koopdoel in de munt
waarin het aandeel noteert en wordt gekocht. Totalen blijven in dollars."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit_app


def test_a_us_ticker_is_dollars():
    assert streamlit_app._row_currency("MSFT", {}) == ("USD", "$", 1.0)


def test_a_paris_line_is_euros_from_its_suffix():
    assert streamlit_app._row_currency("RMS.PA", {}) == ("EUR", "€", 1.0)


def test_a_config_currency_wins_over_the_suffix():
    assert streamlit_app._row_currency("XYZ", {"currency": "CHF"})[:2] == ("CHF", "CHF ")


def test_london_quotes_in_pence_and_the_row_shows_pounds():
    code, symbol, scale = streamlit_app._row_currency("RMV.L", {"currency": "GBP"})
    assert (code, symbol, scale) == ("GBP", "£", 0.01)


def test_gbx_is_normalised_to_pounds():
    assert streamlit_app._row_currency("RMV.L", {"currency": "GBX"}) == ("GBP", "£", 0.01)


def test_the_fv_cell_carries_the_row_symbol():
    html = streamlit_app._render_fv_cell(
        price=1351.5, summary={"weighted_fv_low": 841.0, "weighted_fv_mid": 989.0,
                               "weighted_fv_high": 1138.0},
        legacy_intrinsic=None, theme={}, symbol="€")
    assert "€989" in html and "€841" in html and "$" not in html


def test_fmt_keeps_the_dollar_default():
    assert streamlit_app._fmt_fv_dollar(45.5) == "$45.50"
    assert streamlit_app._fmt_fv_dollar(989.0, "€") == "€989"
