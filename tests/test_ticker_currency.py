import pytest

import ticker_currency as tc

USD_PER = {"EUR": 1.12, "DKK": 0.15, "GBP": 1.30, "JPY": 0.0067}.get


@pytest.mark.parametrize("ticker,cfg,listed,reported", [
    ("JNJ", {}, "USD", "USD"),
    ("RMS.PA", {}, "EUR", "EUR"),
    ("3064.T", {}, "JPY", "JPY"),
    ("RMV.L", {}, "GBX", "GBP"),
    ("RMV.L", {"currency": "GBP"}, "GBX", "GBP"),          # legacy key
    ("ASML", {"reporting_currency": "EUR"}, "USD", "EUR"),
    ("NVO", {"reporting_currency": "DKK", "listing_currency": "USD"}, "USD", "DKK"),
    ("EL.PA", {"currency": "EUR"}, "EUR", "EUR"),
])
def test_listing_and_reporting_currency(ticker, cfg, listed, reported):
    assert tc.listing_currency(ticker, cfg) == listed
    assert tc.reporting_currency(ticker, cfg) == reported


def test_us_names_are_untouched():
    assert tc.quote_factor("META", {}, usd_per=USD_PER) == 1.0
    assert tc.to_reporting_price(700.0, "META", {}, usd_per=USD_PER) == 700.0


def test_pence_become_pounds():
    assert tc.to_reporting_price(546.0, "RMV.L", {}, usd_per=USD_PER) == pytest.approx(5.46)


def test_an_adr_is_shown_in_the_reporting_currency():
    # ASML on Nasdaq at $1,120 with EUR at $1.12 -> EUR 1,000
    assert tc.to_reporting_price(1120.0, "ASML", {"reporting_currency": "EUR"},
                                 usd_per=USD_PER) == pytest.approx(1000.0)


def test_the_source_currency_beats_the_suffix():
    # Frankfurt quotes a Tokyo line in EUR: convert from EUR, not from JPY
    got = tc.to_reporting_price(13.4, "3064.T", {}, quote_currency="EUR", usd_per=USD_PER)
    assert got == pytest.approx(13.4 * 1.12 / 0.0067)


def test_no_fx_rate_means_no_price_not_one_to_one():
    assert tc.to_reporting_price(1120.0, "ASML", {"reporting_currency": "EUR"},
                                 usd_per=lambda c: None) is None


def test_symbols():
    assert tc.symbol("JPY") == "¥" and tc.symbol("GBX") == "GBX " and tc.symbol("EUR") == "€"


def test_valuation_back_in_the_listing_currency_for_holdings():
    row = {"ticker": "ASML", "currency": "EUR", "listing_currency": "USD",
           "fv_mid": 1000.0, "buy_price": 800.0, "verdict": "x"}
    got = tc.valuation_in_listing(row, usd_per=USD_PER)
    assert got["fv_mid"] == pytest.approx(1120.0) and got["buy_price"] == pytest.approx(896.0)
    assert got["currency"] == "USD" and got["verdict"] == "x"
    us = {"ticker": "META", "currency": "USD", "listing_currency": "USD", "fv_mid": 5.0}
    assert tc.valuation_in_listing(us) is us
    pence = {"ticker": "RMV.L", "currency": "GBP", "listing_currency": "GBX", "buy_price": 5.0}
    assert tc.valuation_in_listing(pence, usd_per=USD_PER)["buy_price"] == pytest.approx(500.0)


def test_page_symbol_is_scoped_to_the_ticker_page():
    import business_revenue
    import overview_metrics as om
    import phase_page
    assert tc.money_symbol() == "$" and om.fmt_money_m(2400) == "$2.4B"
    with tc.page_symbol("$"):
        tc.set_page_symbol("€")                 # what the detail page does
        assert om.fmt_money_m(2400) == "€2.4B"
        assert om.fmt_money_m(510) == "€510M"
        assert business_revenue._fmt_musd(3195.0) == "€3.20B"
        assert phase_page._tick_label(0) == "€0"
        tc.set_page_symbol("kr ")
        assert om.fmt_money_m(1_500_000) == "kr1.50T"
    assert tc.money_symbol() == "$" and om.fmt_money_m(2400) == "$2.4B"
