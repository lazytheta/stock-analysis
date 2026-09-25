"""Nasdaq-koersen, offline: de HTTP-kant is een geinjecteerde fetch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quotes


def _info(price="$71.6993", change="-0.0207",
          ts="Sep 25, 2026 9:02 AM ET"):
    return {"data": {"primaryData": {
        "lastSalePrice": price, "netChange": change,
        "lastTradeTimestamp": ts, "isRealTime": True}}}


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
