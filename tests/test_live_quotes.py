"""De koersketen: broker -> Nasdaq -> Yahoo -> Frankfurt, offline."""

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
                             isin_by_ticker={"RMS.PA": "FR0000052292"})
    # RMS.PA is not US-style, so the broker step never sees it (see
    # test_broker_only_asked_for_us_style_tickers) -- it goes to Nasdaq/Yahoo
    # like the rest.
    assert broker.calls == [["MSFT", "NFLX"]]
    assert nasdaq.calls == [["NFLX", "RMS.PA"]]
    assert yahoo.calls == [["RMS.PA"]]
    assert frank.calls == []
    assert [out[t]["price"] for t in ("MSFT", "NFLX", "RMS.PA")] == [451, 72, 1412]


def test_frankfurt_only_for_isin_tickers():
    frank = Source({"RMS.PA": q(1412)})
    out = quotes.live_quotes(["RMS.PA", "ENX.PA"], nasdaq=Source(), yahoo=Source(),
                             frankfurt=frank,
                             isin_by_ticker={"RMS.PA": "FR0000052292"})
    assert frank.calls == [{"RMS.PA": "FR0000052292"}]
    assert out["RMS.PA"]["price"] == 1412 and out["ENX.PA"] is None


def test_a_failing_step_falls_through():
    yahoo = Source({"NFLX": q(72)})
    out = quotes.live_quotes(["NFLX"], broker=Source(boom=True),
                             nasdaq=Source(boom=True), yahoo=yahoo,
                             frankfurt=Source())
    assert out["NFLX"]["price"] == 72


def test_zero_price_counts_as_missing():
    yahoo = Source({"NFLX": q(72)})
    out = quotes.live_quotes(["NFLX"], nasdaq=Source({"NFLX": q(0)}), yahoo=yahoo,
                             frankfurt=Source())
    assert yahoo.calls == [["NFLX"]] and out["NFLX"]["price"] == 72


def test_every_ticker_is_a_key_in_input_order_once():
    out = quotes.live_quotes(["B", "A", "B"], nasdaq=Source(), yahoo=Source(),
                             frankfurt=Source())
    assert list(out) == ["B", "A"] and out == {"B": None, "A": None}
    assert quotes.live_quotes([]) == {}


def test_no_broker_starts_at_nasdaq():
    nasdaq = Source({"NFLX": q(72)})
    yahoo = Source()
    quotes.live_quotes(["NFLX"], nasdaq=nasdaq, yahoo=yahoo, frankfurt=Source())
    assert nasdaq.calls == [["NFLX"]] and yahoo.calls == []


def test_broker_only_asked_for_us_style_tickers():
    # RMS.PA is a Paris line; Tastytrade's broker feed doesn't carry it and
    # asking anyway would cost its full 10s timeout for nothing. It should
    # still reach Nasdaq/Yahoo/Frankfurt like any other ticker.
    broker = Source({"MSFT": q(451)})
    nasdaq = Source({"RMS.PA": q(1412)})
    out = quotes.live_quotes(["MSFT", "RMS.PA"], broker=broker, nasdaq=nasdaq,
                             yahoo=Source(), frankfurt=Source())
    assert broker.calls == [["MSFT"]]
    assert nasdaq.calls == [["RMS.PA"]]
    assert out["MSFT"]["price"] == 451 and out["RMS.PA"]["price"] == 1412
