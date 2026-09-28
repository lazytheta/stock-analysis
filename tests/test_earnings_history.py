import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import earnings_history as eh
from tests.test_price_history import FakeClient, _first_page

REPO = Path(__file__).resolve().parent.parent


def _payload(rows):
    return {"data": {"earningsSurpriseTable": {"rows": rows}}}


def test_parse_rows_strings_numbers_and_na():
    p = _payload([
        {"fiscalQtrEnd": "Jul 2026", "dateReported": "8/26/2026", "eps": 1.68,
         "consensusForecast": "1.62", "percentageSurprise": "3.7"},
        {"fiscalQtrEnd": "Apr 2026", "dateReported": "5/28/2026", "eps": "-0.12",
         "consensusForecast": "N/A", "percentageSurprise": ""},
        {"fiscalQtrEnd": "Feb 2028", "dateReported": "bad", "eps": "$1,234.5",
         "consensusForecast": None, "percentageSurprise": 0},
        {"fiscalQtrEnd": "garbage", "dateReported": "8/26/2026", "eps": 1},
    ])
    assert eh.parse_rows(p, "NVDA") == [
        {"ticker": "NVDA", "fiscal_qtr_end": "2026-07-31",
         "date_reported": "2026-08-26", "eps": 1.68, "eps_consensus": 1.62,
         "surprise_pct": 3.7, "source": "nasdaq"},
        {"ticker": "NVDA", "fiscal_qtr_end": "2026-04-30",
         "date_reported": "2026-05-28", "eps": -0.12, "eps_consensus": None,
         "surprise_pct": None, "source": "nasdaq"},
        {"ticker": "NVDA", "fiscal_qtr_end": "2028-02-29",
         "date_reported": None, "eps": 1234.5, "eps_consensus": None,
         "surprise_pct": 0.0, "source": "nasdaq"},
    ]


def test_parse_rows_empty_payloads():
    assert eh.parse_rows(None, "X") == []
    assert eh.parse_rows({"data": None}, "X") == []
    assert eh.parse_rows(_payload(None), "X") == []


def test_fetch_uses_url():
    urls = []
    out = eh.fetch("NVDA", fetch=lambda u: urls.append(u) or _payload([]))
    assert out == []
    assert urls == ["https://api.nasdaq.com/api/company/NVDA/earnings-surprise"]


def _row(q):
    return {"fiscalQtrEnd": q, "dateReported": "8/26/2026", "eps": 1,
            "consensusForecast": "1", "percentageSurprise": "0"}


def test_run_upserts_continues_on_error_and_excludes_spy():
    db = {("data", "watchlist_configs"): _first_page(
        [{"ticker": "NFLX"}, {"ticker": "BOOM"}, {"ticker": "SPY"},
         {"ticker": "RMS.PA"}])}
    asked, sleeps = [], []

    def fetch(url):
        sym = url.split("/company/")[1].split("/")[0]
        asked.append(sym)
        if sym == "BOOM":
            raise RuntimeError("down")
        return _payload([_row("Jul 2026"), _row("Apr 2026")])

    out = eh.run(FakeClient(db), fetch=fetch, sleep=sleeps.append)
    assert asked == ["NFLX", "BOOM"]
    assert out == {"tickers": 2, "rows": 2, "errors": ["BOOM"]}
    assert sleeps == [0.3]
    (name, recs, oc), = db["upserts"]
    assert name == "earnings_history" and oc == "ticker,fiscal_qtr_end"
    assert [r["fiscal_qtr_end"] for r in recs] == ["2026-07-31", "2026-04-30"]
    assert set(recs[0]) == {"ticker", "fiscal_qtr_end", "date_reported", "eps",
                            "eps_consensus", "surprise_pct", "source",
                            "updated_at"}


def test_run_skips_upsert_when_no_rows():
    db = {("data", "watchlist_configs"): _first_page([{"ticker": "NFLX"}])}
    out = eh.run(FakeClient(db), fetch=lambda u: _payload([]), sleep=lambda s: None)
    assert out == {"tickers": 1, "rows": 0, "errors": []}
    assert "upserts" not in db


def test_load_orders_ascending_and_filters_ticker():
    seen = []

    def data(t):
        seen.append((t.filters, t._order))
        return [{"fiscal_qtr_end": "2026-04-30", "eps": "1.1"},
                {"fiscal_qtr_end": "2026-07-31", "eps": 1.2}]

    out = eh.load(FakeClient({("data", "earnings_history"): data}), "NFLX")
    assert [r["fiscal_qtr_end"] for r in out] == ["2026-04-30", "2026-07-31"]
    assert seen[0] == ([("eq", "ticker", "NFLX")], ("fiscal_qtr_end", False))


def test_dockerfile_prices_copies_job_modules():
    text = (REPO / "Dockerfile.prices").read_text()
    copy = " ".join(line for line in text.splitlines() if line.startswith("COPY"))
    for mod in ("gather_data.py", "quotes.py", "price_history.py",
                "earnings_history.py", "scripts/run_price_history.py"):
        assert mod in copy


def _run_script(monkeypatch, price_result, earnings):
    import importlib
    import types

    monkeypatch.setenv("SUPABASE_URL", "http://x")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setitem(sys.modules, "supabase", types.SimpleNamespace(
        create_client=lambda url, key: object()))
    sys.path.insert(0, str(REPO / "scripts"))
    job = importlib.import_module("run_price_history")
    monkeypatch.setattr(job.price_history, "run", lambda c: price_result)
    monkeypatch.setattr(job.earnings_history, "run", earnings)
    try:
        job.main()
    except SystemExit as e:
        return e.code
    finally:
        sys.path.remove(str(REPO / "scripts"))
    return 0


def test_job_runs_earnings_after_prices_and_survives_earnings_failure(monkeypatch):
    calls = []

    def boom(client):
        calls.append("earnings")
        raise RuntimeError("down")

    code = _run_script(monkeypatch, {"tickers": 2, "rows": 5, "errors": ["X"]}, boom)
    assert code == 0 and calls == ["earnings"]


def test_job_exits_nonzero_only_when_every_price_ticker_failed(monkeypatch):
    code = _run_script(monkeypatch, {"tickers": 2, "rows": 0, "errors": ["A", "B"]},
                       lambda c: {"tickers": 1, "rows": 4, "errors": []})
    assert code == 1
