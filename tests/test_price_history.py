import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import price_history as ph


def _payload(rows):
    return {"data": {"tradesTable": {"rows": rows}}}


def test_parse_rows_sorts_ascending_and_cleans():
    p = _payload([
        {"date": "09/24/2026", "close": "$71.72"},
        {"date": "09/23/2026", "close": "$1,071.36"},
        {"date": "bad", "close": "$1.00"},
        {"date": "09/22/2026", "close": "N/A"},
    ])
    assert ph.parse_rows(p) == [("2026-09-23", 1071.36), ("2026-09-24", 71.72)]


def test_parse_rows_null_data():
    assert ph.parse_rows({"data": None}) == []
    assert ph.parse_rows(None) == []


def test_fetch_history_falls_back_to_etf():
    calls = []

    def fetch(url):
        calls.append(url)
        if "assetclass=etf" in url:
            return _payload([{"date": "09/24/2026", "close": "$760.00"}])
        return {"data": None}

    out = ph.fetch_history("SPY", date(2026, 9, 1), date(2026, 9, 25), fetch=fetch)
    assert out == [("2026-09-24", 760.0)]
    assert "fromdate=2026-09-01" in calls[0] and "todate=2026-09-25" in calls[0]
    assert "limit=9999" in calls[0]


def test_start_date_backfill_and_incremental():
    today = date(2026, 9, 25)
    assert ph.start_date(None, today) == date(2016, 9, 25)
    assert ph.start_date(date(2026, 9, 23), today) == date(2026, 9, 24)


def test_start_date_backfill_feb29_falls_back_to_feb28():
    assert ph.start_date(None, date(2028, 2, 29)) == date(2018, 2, 28)


def test_update_ticker_upserts_in_batches():
    rows = [(f"2026-01-{d:02d}", float(d)) for d in range(1, 8)]
    batches = []
    n = ph.update_ticker("NFLX", date(2026, 9, 25), None,
                         fetch=lambda s, a, b: rows,
                         upsert=lambda recs: batches.append(recs), batch=3)
    assert n == 7
    assert [len(b) for b in batches] == [3, 3, 1]
    assert batches[0][0] == {"ticker": "NFLX", "day": "2026-01-01", "close": 1.0}


def test_update_ticker_nothing_new_when_up_to_date():
    called = []
    n = ph.update_ticker("NFLX", date(2026, 9, 25), date(2026, 9, 25),
                         fetch=lambda *a: called.append(a) or [],
                         upsert=lambda recs: None)
    assert n == 0 and called == []


class FakeTable:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self._order, self._limit = db, name, [], None, None
        self._payload, self._select, self._range = None, None, None

    def select(self, cols):
        self._select = cols
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self.filters.append(("gte", col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def upsert(self, recs, on_conflict=None):
        self.db.setdefault("upserts", []).append((self.name, recs, on_conflict))
        self._payload = recs
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.db.get(("data", self.name), lambda t: [])(self)
        return r


class FakeClient:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return FakeTable(self.db, name)


def _first_page(rows):
    """Data lambda that serves `rows` on the first .range() page only."""
    return lambda t: rows if t._range is None or t._range[0] == 0 else []


def test_run_collects_tickers_skips_non_us_and_continues_on_error():
    db = {
        ("data", "watchlist_configs"): _first_page([
            {"ticker": "NFLX"}, {"ticker": "RMS.PA"}, {"ticker": "NFLX"},
            {"ticker": "BOOM"}]),
        ("data", "price_history"): lambda t: [],
    }

    def fetch(symbol, start, end):
        if symbol == "BOOM":
            raise RuntimeError("down")
        return [("2026-09-24", 1.0)]

    out = ph.run(FakeClient(db), today=date(2026, 9, 25), fetch=fetch,
                 sleep=lambda s: None)
    assert out["tickers"] == 3          # NFLX, BOOM, SPY (RMS.PA skipped)
    assert out["errors"] == ["BOOM"]
    assert out["rows"] == 2
    written = [recs[0]["ticker"] for name, recs, oc in db["upserts"]]
    assert sorted(written) == ["NFLX", "SPY"]
    assert all(oc == "ticker,day" for _, _, oc in db["upserts"])


def test_update_ticker_no_split_writes_only_new_days():
    stored = {"2026-09-21": 100.0, "2026-09-22": 101.0}
    calls, written = [], []

    def fetch(symbol, start, end):
        calls.append((start, end))
        return [("2026-09-21", 100.2), ("2026-09-22", 101.0),
                ("2026-09-23", 102.0), ("2026-09-24", 103.0)]

    n = ph.update_ticker("NFLX", date(2026, 9, 25), date(2026, 9, 22), fetch,
                         lambda recs: written.extend(recs), stored=stored)
    assert calls == [(date(2026, 9, 15), date(2026, 9, 25))]
    assert n == 2
    assert [r["day"] for r in written] == ["2026-09-23", "2026-09-24"]


def test_update_ticker_split_refetches_full_window_and_overwrites():
    stored = {"2026-09-21": 100.0, "2026-09-22": 100.0}
    full = [("2016-09-26", 10.0), ("2026-09-21", 50.0), ("2026-09-22", 50.0),
            ("2026-09-23", 51.0)]
    calls, written = [], []

    def fetch(symbol, start, end):
        calls.append((start, end))
        if start == date(2016, 9, 25):
            return full
        return full[1:]

    n = ph.update_ticker("NFLX", date(2026, 9, 25), date(2026, 9, 22), fetch,
                         lambda recs: written.extend(recs), stored=stored)
    assert calls == [(date(2026, 9, 15), date(2026, 9, 25)),
                     (date(2016, 9, 25), date(2026, 9, 25))]
    assert n == 4
    assert [(r["day"], r["close"]) for r in written] == full


def test_run_passes_stored_closes_and_detects_split():
    stored_rows = [{"day": "2026-09-22", "close": 100.0},
                   {"day": "2026-09-21", "close": 100.0}]
    db = {
        ("data", "watchlist_configs"): _first_page([{"ticker": "NFLX"}]),
        ("data", "price_history"): lambda t: (
            stored_rows if ("eq", "ticker", "NFLX") in t.filters else []),
    }
    calls = []

    def fetch(symbol, start, end):
        calls.append((symbol, start))
        if symbol == "NFLX" and start == date(2026, 9, 15):
            return [("2026-09-22", 50.0), ("2026-09-23", 51.0)]
        return [("2016-09-26", 5.0), ("2026-09-22", 50.0), ("2026-09-23", 51.0)]

    out = ph.run(FakeClient(db), today=date(2026, 9, 25), fetch=fetch,
                 sleep=lambda s: None)
    assert ("NFLX", date(2026, 9, 15)) in calls           # overlap check
    assert ("NFLX", date(2016, 9, 25)) in calls           # split -> full re-fetch
    nflx = [r for name, recs, oc in db["upserts"] for r in recs
            if r["ticker"] == "NFLX"]
    assert len(nflx) == 3
    assert out["errors"] == []


def test_load_series_pages_until_empty():
    pages = {0: [{"day": "2026-01-01", "close": 1.0}, {"day": "2026-01-02", "close": 2.0}],
             2: [{"day": "2026-01-03", "close": 3.0}]}
    db = {("data", "price_history"): lambda t: pages.get(t._range[0], [])}
    out = ph.load_series(FakeClient(db), ["NFLX"], date(2026, 1, 1))
    assert [d for d, _ in out["NFLX"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]


def test_tickers_pages_past_first_page():
    pages = {0: [{"ticker": "NFLX"}], 1: [{"ticker": "MSFT"}, {"ticker": "NFLX"}]}
    db = {("data", "watchlist_configs"): lambda t: pages.get(t._range[0], [])}
    assert ph._tickers(FakeClient(db)) == ["NFLX", "MSFT", "SPY"]
