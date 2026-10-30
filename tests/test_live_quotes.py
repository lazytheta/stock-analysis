"""De koersketen: broker -> Nasdaq -> Frankfurt -> stockanalysis -> Yahoo, offline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quotes


def q(price):
    return {"price": price, "previousClose": price - 1, "asof": None, "venue": "x"}


class Source:
    def __init__(self, answers=None, boom=False):
        self.answers, self.boom, self.calls = answers or {}, boom, []

    def __call__(self, arg):
        self.calls.append(arg)
        if self.boom:
            raise RuntimeError("down")
        keys = arg.keys() if isinstance(arg, dict) else arg
        return {t: self.answers.get(t) for t in keys}


def test_each_step_only_gets_what_the_previous_missed():
    broker = Source({"MSFT": q(451)})
    nasdaq = Source({"NFLX": q(72)})
    yahoo = Source({"RMS.PA": q(1412)})
    frank = Source({})
    out = quotes.live_quotes(["MSFT", "NFLX", "RMS.PA"], broker=broker,
                             nasdaq=nasdaq, yahoo=yahoo, frankfurt=frank,
                             isin_by_ticker={"RMS.PA": "FR0000052292"}, stockanalysis=Source())
    # RMS.PA is not US-style, so the broker step never sees it (see
    # test_broker_only_asked_for_us_style_tickers) -- it goes to Nasdaq/Yahoo
    # like the rest.
    assert broker.calls == [["MSFT", "NFLX"]]
    assert nasdaq.calls == [["NFLX", "RMS.PA"]]
    assert yahoo.calls == [["RMS.PA"]]
    assert frank.calls == [{"RMS.PA": "FR0000052292"}]
    assert [out[t]["price"] for t in ("MSFT", "NFLX", "RMS.PA")] == [451, 72, 1412]


def test_frankfurt_only_for_isin_tickers():
    frank = Source({"RMS.PA": q(1412)})
    out = quotes.live_quotes(["RMS.PA", "ENX.PA"], nasdaq=Source(), yahoo=Source(),
                             frankfurt=frank,
                             isin_by_ticker={"RMS.PA": "FR0000052292"}, stockanalysis=Source())
    assert frank.calls == [{"RMS.PA": "FR0000052292"}]
    assert out["RMS.PA"]["price"] == 1412 and out["ENX.PA"] is None


def test_a_failing_step_falls_through():
    yahoo = Source({"NFLX": q(72)})
    out = quotes.live_quotes(["NFLX"], broker=Source(boom=True),
                             nasdaq=Source(boom=True), yahoo=yahoo,
                             frankfurt=Source(), stockanalysis=Source())
    assert out["NFLX"]["price"] == 72


def test_zero_price_counts_as_missing():
    yahoo = Source({"NFLX": q(72)})
    out = quotes.live_quotes(["NFLX"], nasdaq=Source({"NFLX": q(0)}), yahoo=yahoo,
                             frankfurt=Source())
    assert yahoo.calls == [["NFLX"]] and out["NFLX"]["price"] == 72


def test_every_ticker_is_a_key_in_input_order_once():
    out = quotes.live_quotes(["B", "A", "B"], nasdaq=Source(), yahoo=Source(),
                             frankfurt=Source(), stockanalysis=Source())
    assert list(out) == ["B", "A"] and out == {"B": None, "A": None}
    assert quotes.live_quotes([], stockanalysis=Source()) == {}


def test_no_broker_starts_at_nasdaq():
    nasdaq = Source({"NFLX": q(72)})
    yahoo = Source()
    quotes.live_quotes(["NFLX"], nasdaq=nasdaq, yahoo=yahoo, frankfurt=Source(), stockanalysis=Source())
    assert nasdaq.calls == [["NFLX"]] and yahoo.calls == []


def test_broker_only_asked_for_us_style_tickers():
    # RMS.PA is a Paris line; Tastytrade's broker feed doesn't carry it and
    # asking anyway would cost its full 10s timeout for nothing. It should
    # still reach Nasdaq/Yahoo/Frankfurt like any other ticker.
    broker = Source({"MSFT": q(451)})
    nasdaq = Source({"RMS.PA": q(1412)})
    out = quotes.live_quotes(["MSFT", "RMS.PA"], broker=broker, nasdaq=nasdaq,
                             yahoo=Source(), frankfurt=Source(), stockanalysis=Source())
    assert broker.calls == [["MSFT"]]
    assert nasdaq.calls == [["RMS.PA"]]
    assert out["MSFT"]["price"] == 451 and out["RMS.PA"]["price"] == 1412


# ── stockanalysis.com (2026-10-22) ─────────────────────────────────────

_SA_HTML = ('<div>Delayed Price · Currency is GBP · Price in GBX</div><script>'
            'quote:{c:-5.5,h:550,p:546,u:"Oct 22, 2026, 10:33 AM BST",v:1}</script>')


def test_stockanalysis_path():
    assert quotes.stockanalysis_path("3064.T") == ("tyo", "3064")
    assert quotes.stockanalysis_path("NOVO-B.CO") == ("cph", "NOVO.B")
    assert quotes.stockanalysis_path("META") is None
    assert quotes.stockanalysis_path("X.ZZ") is None


def test_stockanalysis_price_and_currency():
    got = quotes.parse_stockanalysis(_SA_HTML)
    assert got["price"] == 546 and got["currency"] == "GBX"
    assert got["asof"].startswith("Oct 22")
    assert quotes.parse_stockanalysis("<html>no quote</html>") is None


def test_stockanalysis_only_asks_for_foreign_lines():
    urls = []
    got = quotes.fetch_stockanalysis_quotes(
        ["RMV.L", "META"], fetch=lambda u: urls.append(u) or _SA_HTML)
    assert urls == ["https://stockanalysis.com/quote/lon/RMV/"]
    assert list(got) == ["RMV.L"]


def test_frankfurt_is_not_asked_for_a_non_euro_line():
    frank = Source({"3064.T": q(13.4)})
    out = quotes.live_quotes(["3064.T"], nasdaq=Source(), yahoo=Source(),
                             stockanalysis=Source(), frankfurt=frank,
                             isin_by_ticker={"3064.T": "JP3922950005"})
    assert frank.calls == [] and out["3064.T"] is None


def test_stockanalysis_answers_before_yahoo_and_the_run_is_timed():
    """Yahoo is blocked on the cloud hosts; it must not be asked for a
    foreign line stockanalysis already answered (2026-10-26)."""
    yahoo = Source()
    out = quotes.live_quotes(["MSFT", "RMS.PA"], nasdaq=Source({"MSFT": q(451)}),
                             stockanalysis=Source({"RMS.PA": q(1249)}), yahoo=yahoo,
                             frankfurt=Source())
    assert out["RMS.PA"]["price"] == 1249 and yahoo.calls == []
    assert [step[0] for step in quotes.LAST_RUN] == ["nasdaq", "stockanalysis"]
    assert quotes.LAST_RUN[1][1:3] == (1, 1)


def test_frankfurt_answers_a_euro_line_before_stockanalysis():
    """stockanalysis has no previous close and its Paris pages lag (RMS.PA
    stood at 11:46 at 13:59 on 2026-10-30), so a T212 row read +0.00%."""
    frank = Source({"RMS.PA": q(1297.5)})
    sa = Source({"RMS.PA": q(1288)})
    got = quotes.live_quotes(["RMS.PA"], nasdaq=Source(), yahoo=Source(),
                             stockanalysis=sa, frankfurt=frank,
                             isin_by_ticker={"RMS.PA": "FR0000052292"})
    assert got["RMS.PA"]["price"] == 1297.5
    assert sa.calls == []


def test_watchlist_prefers_frankfurt_over_stockanalysis_for_a_euro_line():
    entries = [{"ticker": "RMS.PA", "isin": "FR0000052292", "stock_price": 1250.5}]
    sa = Source({"RMS.PA": q(1288)})
    quotes.apply_live_prices(entries, primary=Source(), secondary=sa,
                             frankfurt=Source({"RMS.PA": q(1297.5)}))
    assert entries[0]["stock_price"] == 1297.5
    assert sa.calls == []


def test_a_bare_broker_symbol_with_an_isin_reaches_frankfurt():
    """Trading 212 calls Hermes "RMS": no suffix, so it counted as USD and the
    Portfolio page's Day % stood at +0.00%."""
    frank = Source({"RMS": q(1297.5)})
    got = quotes.live_quotes(["RMS"], nasdaq=Source(), yahoo=Source(),
                             stockanalysis=Source(), frankfurt=frank,
                             isin_by_ticker={"RMS": "FR0000052292"})
    assert got["RMS"]["price"] == 1297.5


def test_a_bare_us_isin_stays_away_from_frankfurt():
    frank = Source({"TSM": q(150)})
    got = quotes.live_quotes(["TSM"], nasdaq=Source(), yahoo=Source(),
                             stockanalysis=Source(), frankfurt=frank,
                             isin_by_ticker={"TSM": "US8740391003"})
    assert got["TSM"] is None
    assert frank.calls == []
