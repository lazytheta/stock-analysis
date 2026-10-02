"""tickers_stale_earnings_brief: watchlist tickers whose Earnings Brief is
missing or older than the newest quarter in earnings_history. Offline: the
Supabase client and config_store are faked."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _brief(qtr_end):
    return json.dumps({
        "quarter": {"fiscal_qtr_end": qtr_end, "label": "Q"},
        "tone": "Neutral", "summary": "Fine quarter.",
        "points": [{"label": "What went well", "text": "a"},
                   {"label": "What worries", "text": "b"},
                   {"label": "Management expects", "text": "c"}],
        "next": {"period": "Q next"}, "as_of": "2026-08-01", "source": "SEC-MCP"})


class _Query:
    def __init__(self, rows, log):
        self.rows, self.log, self.lo, self.hi = rows, log, 0, None

    def select(self, cols):
        self.log.append(("select", cols))
        return self

    def in_(self, col, values):
        self.log.append(("in", col, tuple(values)))
        self.rows = [r for r in self.rows if r[col] in values]
        return self

    def order(self, *a, **k):
        return self

    def range(self, lo, hi):
        self.lo, self.hi = lo, hi
        return self

    def execute(self):
        return MagicMock(data=self.rows[self.lo:self.hi + 1])


class FakeClient:
    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.log, self.tables = rows, fail, [], []

    def table(self, name):
        self.tables.append(name)
        if self.fail:
            raise RuntimeError("earnings_history unavailable")
        return _Query(list(self.rows), self.log)


@pytest.fixture
def mcp(monkeypatch):
    import mcp_server
    store, rows = {}, []
    state = {"client": None}

    def client():
        state["client"] = state["client"] or FakeClient(rows)
        return state["client"]
    monkeypatch.setattr(mcp_server, "get_supabase_client", client)
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")
    seen = {}

    def load_all(c, user_id=None, include_ai_notes=True):
        seen["user_id"] = user_id
        return dict(store)
    monkeypatch.setattr(mcp_server.config_store, "load_all_configs", load_all)
    return mcp_server, store, rows, state, seen


def _call(m, **kw):
    return json.loads(m._tickers_stale_earnings_brief_impl(**kw))


def test_missing_stale_and_current_briefs(mcp):
    m, store, rows, _state, _seen = mcp
    store.update({
        # brief covers an older quarter than Nasdaq's newest -> stale
        "AAA": {"ai_notes": {"Earnings Brief": _brief("2026-03-31")}},
        # brief matches the newest quarter (SEC end date a few days before
        # Nasdaq's month-end) -> current
        "BBB": {"ai_notes": {"Earnings Brief": _brief("2026-06-27")}},
        # no brief, earnings rows exist -> missing
        "CCC": {"ai_notes": {}},
        # no brief, no rows, but Business Analysis -> missing
        "DDD": {"ai_notes": {"Business Analysis": "x"}},
        # no brief, no rows, no Business Analysis -> skipped
        "EEE": {"ai_notes": {}},
        # brief present, no rows at all -> nothing to compare, current
        "FFF": {"ai_notes": {"Earnings Brief": _brief("2026-06-30")}},
        # stored brief no longer valid -> treated as missing
        "GGG": {"ai_notes": {"Earnings Brief": "not json", "Business Analysis": "x"}},
    })
    rows.extend([
        {"ticker": "AAA", "fiscal_qtr_end": "2026-03-31"},
        {"ticker": "AAA", "fiscal_qtr_end": "2026-06-30"},
        {"ticker": "BBB", "fiscal_qtr_end": "2026-03-31"},
        {"ticker": "BBB", "fiscal_qtr_end": "2026-06-30"},
        {"ticker": "CCC", "fiscal_qtr_end": "2026-06-30"},
        {"ticker": "ZZZ", "fiscal_qtr_end": "2026-09-30"},   # not on this watchlist
    ])
    assert _call(m) == {"tickers": ["AAA", "CCC", "DDD", "GGG"], "remaining": 0}


def test_limit_and_remaining(mcp):
    m, store, _rows, _state, _seen = mcp
    for t in ("A1", "A2", "A3"):
        store[t] = {"ai_notes": {"Business Analysis": "x"}}
    assert _call(m, limit=2) == {"tickers": ["A1", "A2"], "remaining": 1}
    assert _call(m, limit=0) == {"tickers": [], "remaining": 3}


def test_default_limit_is_six(mcp):
    m, store, _rows, _state, _seen = mcp
    for i in range(8):
        store[f"T{i}"] = {"ai_notes": {"Business Analysis": "x"}}
    assert _call(m) == {"tickers": [f"T{i}" for i in range(6)], "remaining": 2}


def test_reads_earnings_history_for_the_watchlist_tickers_only(mcp):
    m, store, _rows, state, seen = mcp
    store.update({"AAA": {"ai_notes": {}}, "BBB": {"ai_notes": {}}})
    _call(m, user_id="someone")
    assert seen["user_id"] == "someone"
    client = state["client"]
    assert client.tables == ["earnings_history"]
    assert ("in", "ticker", ("AAA", "BBB")) in client.log


def test_pages_through_more_rows_than_one_page(mcp, monkeypatch):
    m, store, rows, _state, _seen = mcp
    monkeypatch.setattr(m, "_EARNINGS_PAGE", 2)
    store["AAA"] = {"ai_notes": {"Earnings Brief": _brief("2026-03-31")}}
    rows.extend({"ticker": "AAA", "fiscal_qtr_end": d}
                for d in ("2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31",
                          "2026-06-30"))
    assert _call(m)["tickers"] == ["AAA"]


def test_a_failed_earnings_read_still_reports_missing_briefs(mcp, monkeypatch):
    m, store, _rows, _state, _seen = mcp
    monkeypatch.setattr(m, "get_supabase_client", lambda: FakeClient([], fail=True))
    store.update({"AAA": {"ai_notes": {"Earnings Brief": _brief("2026-03-31")}},
                  "BBB": {"ai_notes": {"Business Analysis": "x"}}})
    assert _call(m) == {"tickers": ["BBB"], "remaining": 0}


def test_tool_wrapper_returns_json_error(monkeypatch):
    import mcp_server

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(mcp_server, "_tickers_stale_earnings_brief_impl", boom)
    assert json.loads(mcp_server.tickers_stale_earnings_brief()) == {"error": "db down"}
