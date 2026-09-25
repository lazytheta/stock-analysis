"""Nasdaq-koersen, offline: de HTTP-kant is een geinjecteerde fetch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import quotes


@pytest.fixture(autouse=True)
def _reset_nasdaq_breaker():
    """Elke test begint met een dichte teller.

    fetch_nasdaq_quotes houdt de storingsteller module-breed bij (nodig om
    ook buiten een enkele aanroep om drie achtereenvolgende missers te tellen),
    en zonder reset lekt een RuntimeError uit de ene test de teller van de
    volgende in."""
    quotes._nasdaq_breaker_reset()
    yield
    quotes._nasdaq_breaker_reset()


def _info(price="$71.6993", change="-0.0207",
          ts="Sep 25, 2026 9:02 AM ET"):
    return {"data": {"primaryData": {
        "lastSalePrice": price, "netChange": change,
        "lastTradeTimestamp": ts, "isRealTime": True}}}


def _extended(market_status, primary_price="$71.53", primary_change="0.00",
              secondary=None):
    """Antwoord met marketStatus + primary/secondaryData, zoals het info-
    endpoint buiten de reguliere sessie teruggeeft."""
    return {"data": {
        "marketStatus": market_status,
        "primaryData": {
            "lastSalePrice": primary_price, "netChange": primary_change,
            "lastTradeTimestamp": "Sep 25, 2026 8:58 AM ET", "isRealTime": True,
        },
        "secondaryData": secondary,
    }}


def _fetcher(by_url_part):
    calls = []

    def fetch(url):
        calls.append(url)
        for part, body in by_url_part.items():
            if part in url:
                if isinstance(body, Exception):
                    raise body
                return body
        return {"data": None}
    fetch.calls = calls
    return fetch


def test_parses_price_previous_close_and_asof():
    fetch = _fetcher({"/NFLX/info?assetclass=stocks": _info()})
    out = quotes.fetch_nasdaq_quotes(["NFLX"], fetch=fetch)
    q = out["NFLX"]
    assert q["price"] == 71.6993
    assert abs(q["previousClose"] - 71.72) < 1e-9
    assert q["asof"] == "Sep 25, 2026 9:02 AM ET"
    assert q["venue"] == "Nasdaq"


def test_commas_and_positive_change():
    fetch = _fetcher({"/BKNG/": _info(price="$5,123.40", change="+12.10")})
    q = quotes.fetch_nasdaq_quotes(["BKNG"], fetch=fetch)["BKNG"]
    assert q["price"] == 5123.40
    assert abs(q["previousClose"] - 5111.30) < 1e-9


def test_etf_is_asked_when_stocks_has_no_data():
    fetch = _fetcher({"/SPY/info?assetclass=etf": _info(price="$760.10",
                                                        change="1.10")})
    q = quotes.fetch_nasdaq_quotes(["SPY"], fetch=fetch)["SPY"]
    assert q["price"] == 760.10
    assert [u.split("assetclass=")[1] for u in fetch.calls] == ["stocks", "etf"]


def test_unknown_everywhere_is_none():
    fetch = _fetcher({})
    assert quotes.fetch_nasdaq_quotes(["ZZZZ"], fetch=fetch) == {"ZZZZ": None}


def test_non_us_symbols_are_skipped_without_a_request():
    fetch = _fetcher({})
    out = quotes.fetch_nasdaq_quotes(["ENX.PA", "BRK-B", "EURUSD=X", "^GSPC"],
                                     fetch=fetch)
    assert out == {"ENX.PA": None, "BRK-B": None, "EURUSD=X": None, "^GSPC": None}
    assert fetch.calls == []


def test_one_failure_does_not_take_the_others():
    fetch = _fetcher({"/MSFT/": RuntimeError("timeout"), "/NFLX/": _info()})
    out = quotes.fetch_nasdaq_quotes(["MSFT", "NFLX"], fetch=fetch)
    assert out["MSFT"] is None
    assert out["NFLX"]["price"] == 71.6993


def test_zero_or_garbage_price_is_none():
    fetch = _fetcher({"/AAA/": _info(price="N/A"), "/BBB/": _info(price="$0.00")})
    out = quotes.fetch_nasdaq_quotes(["AAA", "BBB"], fetch=fetch)
    assert out == {"AAA": None, "BBB": None}


def test_missing_change_gives_none_previous_close():
    fetch = _fetcher({"/NFLX/": _info(change="")})
    q = quotes.fetch_nasdaq_quotes(["NFLX"], fetch=fetch)["NFLX"]
    assert q["price"] == 71.6993 and q["previousClose"] is None


def test_duplicates_once_and_empty_input():
    fetch = _fetcher({"/NFLX/": _info()})
    out = quotes.fetch_nasdaq_quotes(["NFLX", "NFLX"], fetch=fetch)
    assert list(out) == ["NFLX"] and len(fetch.calls) == 1
    assert quotes.fetch_nasdaq_quotes([], fetch=fetch) == {}


# ---------------------------------------------------------------------------
# Buiten reguliere handelstijd: primaryData vs secondaryData
# ---------------------------------------------------------------------------

def test_pre_market_prefers_secondary_over_primary():
    secondary = {"lastSalePrice": "$71.72", "netChange": "-0.50",
                 "lastTradeTimestamp": "Sep 24, 2026 4:00 PM ET"}
    payload = _extended("Pre-Market", primary_price="$71.53",
                        primary_change="0.19", secondary=secondary)
    fetch = _fetcher({"/NFLX/info?assetclass=stocks": payload})
    q = quotes.fetch_nasdaq_quotes(["NFLX"], fetch=fetch)["NFLX"]
    assert q["price"] == 71.72
    assert abs(q["previousClose"] - 72.22) < 1e-9
    assert q["asof"] == "Sep 24, 2026 4:00 PM ET"


def test_market_open_uses_primary_even_with_secondary_present():
    secondary = {"lastSalePrice": "$71.72", "netChange": "-0.50",
                 "lastTradeTimestamp": "Sep 24, 2026 4:00 PM ET"}
    payload = _extended("Market Open", primary_price="$71.90",
                        primary_change="0.37", secondary=secondary)
    fetch = _fetcher({"/NFLX/info?assetclass=stocks": payload})
    q = quotes.fetch_nasdaq_quotes(["NFLX"], fetch=fetch)["NFLX"]
    assert q["price"] == 71.90
    assert abs(q["previousClose"] - 71.53) < 1e-9


def test_closed_with_null_secondary_falls_back_to_primary():
    payload = _extended("Closed", primary_price="$71.53", primary_change="0.19",
                        secondary=None)
    fetch = _fetcher({"/NFLX/info?assetclass=stocks": payload})
    q = quotes.fetch_nasdaq_quotes(["NFLX"], fetch=fetch)["NFLX"]
    assert q["price"] == 71.53
    assert abs(q["previousClose"] - 71.34) < 1e-9


# ---------------------------------------------------------------------------
# Breaker: drie storingen op rij sluit Nasdaq drie minuten
# ---------------------------------------------------------------------------

def test_three_failures_open_the_breaker():
    fetch = _fetcher({"/AAA/": RuntimeError("timeout"),
                      "/BBB/": RuntimeError("timeout"),
                      "/CCC/": RuntimeError("timeout"),
                      "/DDD/": _info()})
    quotes.fetch_nasdaq_quotes(["AAA"], fetch=fetch)
    quotes.fetch_nasdaq_quotes(["BBB"], fetch=fetch)
    quotes.fetch_nasdaq_quotes(["CCC"], fetch=fetch)
    n_calls_before = len(fetch.calls)

    out = quotes.fetch_nasdaq_quotes(["DDD"], fetch=fetch)

    assert out == {"DDD": None}
    assert len(fetch.calls) == n_calls_before  # geen vierde verzoek uitgegaan


def test_breaker_tries_again_after_cooldown(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(quotes, "_nasdaq_clock", lambda: clock["t"])

    fetch = _fetcher({"/AAA/": RuntimeError("timeout"),
                      "/BBB/": RuntimeError("timeout"),
                      "/CCC/": RuntimeError("timeout"),
                      "/DDD/": _info()})
    quotes.fetch_nasdaq_quotes(["AAA"], fetch=fetch)
    quotes.fetch_nasdaq_quotes(["BBB"], fetch=fetch)
    quotes.fetch_nasdaq_quotes(["CCC"], fetch=fetch)

    clock["t"] += 300.0
    out = quotes.fetch_nasdaq_quotes(["DDD"], fetch=fetch)
    assert out["DDD"]["price"] == 71.6993


def test_a_success_resets_the_failure_count():
    fetch = _fetcher({"/AAA/": RuntimeError("timeout"),
                      "/BBB/": RuntimeError("timeout"),
                      "/CCC/": _info(),
                      "/DDD/": RuntimeError("timeout"),
                      "/EEE/": RuntimeError("timeout"),
                      "/FFF/": _info()})
    quotes.fetch_nasdaq_quotes(["AAA"], fetch=fetch)
    quotes.fetch_nasdaq_quotes(["BBB"], fetch=fetch)
    quotes.fetch_nasdaq_quotes(["CCC"], fetch=fetch)  # succes: teller terug naar 0
    quotes.fetch_nasdaq_quotes(["DDD"], fetch=fetch)
    quotes.fetch_nasdaq_quotes(["EEE"], fetch=fetch)  # nu pas twee op rij

    out = quotes.fetch_nasdaq_quotes(["FFF"], fetch=fetch)
    assert out["FFF"]["price"] == 71.6993
