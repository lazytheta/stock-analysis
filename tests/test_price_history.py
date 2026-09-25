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
        self._payload, self._select = None, None

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


def test_run_collects_tickers_skips_non_us_and_continues_on_error():
    db = {
        ("data", "watchlist_configs"): lambda t: [
            {"ticker": "NFLX"}, {"ticker": "RMS.PA"}, {"ticker": "NFLX"},
            {"ticker": "BOOM"}],
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
