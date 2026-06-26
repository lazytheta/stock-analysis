"""Tests for the Cashflow Champions screen.

Two halves, mirroring the module:
  • Pure ranking math on a synthetic universe (no network) — exact, deterministic.
  • Robustness of the batch pipeline: partial failures don't abort the run,
    financials are excluded and counted, and the disk cache prevents re-fetch.
"""
import json

import cashflow_champions as cc
from cashflow_champions import ChampRow


# ── Pure ranking math ──────────────────────────────────────────────────────────

def test_percentiles_monotonic_and_ties():
    assert cc._percentiles([]) == []
    assert cc._percentiles([42.0]) == [1.0]
    # strictly increasing → 0 … 1
    assert cc._percentiles([1, 2, 3]) == [0.0, 0.5, 1.0]
    # ties share the average rank
    p = cc._percentiles([5, 5, 9])
    assert p[0] == p[1] == 0.25
    assert p[2] == 1.0


def _row(t, cfo, ta, mc, sic=None):
    return ChampRow(ticker=t, cfo=cfo, total_assets=ta, market_cap=mc, sic=sic)


def test_rank_synthetic_universe_orders_and_cuts_top_20pct():
    # 5 clean names. Champion = high Cash ROA (cfo/assets) AND cheap (low P/CF).
    # GOOD: high cash ROA + cheap. JUNK: low cash ROA + expensive.
    rows = [
        _row("GOOD", cfo=100, ta=200, mc=500),   # ROA 0.50, yield 0.20
        _row("OKAY", cfo=80, ta=400, mc=800),    # ROA 0.20, yield 0.10
        _row("MEH",  cfo=60, ta=600, mc=1200),   # ROA 0.10, yield 0.05
        _row("WEAK", cfo=40, ta=800, mc=1600),   # ROA 0.05, yield 0.025
        _row("JUNK", cfo=20, ta=1000, mc=2000),  # ROA 0.02, yield 0.01
    ]
    out = cc.rank_universe(rows, exclude_financials=True, top_pct=0.20)
    by_t = {r.ticker: r for r in out["rows"]}

    assert by_t["GOOD"].rank == 1
    assert by_t["JUNK"].rank == 5
    assert by_t["GOOD"].is_champion is True
    # top 20% of 5 = ceil(1.0) = 1 champion
    assert out["summary"]["champions"] == 1
    assert sum(1 for r in out["rows"] if r.is_champion) == 1
    # ratios computed correctly
    assert abs(by_t["GOOD"].cash_roa - 0.5) < 1e-9
    assert abs(by_t["GOOD"].price_to_cf - 5.0) < 1e-9


def test_negative_and_missing_cfo_are_excluded_not_ranked():
    rows = [
        _row("OK", cfo=100, ta=200, mc=500),
        _row("NEG", cfo=-50, ta=200, mc=500),
        _row("MISS", cfo=None, ta=200, mc=500),
        _row("NOCAP", cfo=100, ta=200, mc=None),
    ]
    out = cc.rank_universe(rows)
    by_t = {r.ticker: r for r in out["rows"]}
    assert by_t["OK"].status == "ok"
    assert by_t["NEG"].reason == "negative_cfo"
    assert by_t["MISS"].reason == "missing_data"
    assert by_t["NOCAP"].reason == "missing_data"
    # only the clean name is ranked; all four still present in the output
    assert out["summary"]["ranked"] == 1
    assert len(out["rows"]) == 4


def test_implausible_pcf_excluded_as_data_quality():
    # A P/CF below the floor (market cap ≈ CFO) is a data error (e.g. an uncaught
    # multi-class share undercount) — excluded, not ranked #1.
    rows = [
        _row("REAL", cfo=100, ta=200, mc=900),   # P/CF 9 — fine
        _row("BADMC", cfo=100, ta=200, mc=120),   # P/CF 1.2 — implausible
    ]
    out = cc.rank_universe(rows)
    by_t = {r.ticker: r for r in out["rows"]}
    assert by_t["BADMC"].reason == "data_quality"
    assert by_t["BADMC"].rank is None
    assert out["summary"]["ranked"] == 1


def test_financials_excluded_and_counted():
    rows = [
        _row("TECH", cfo=100, ta=200, mc=500, sic=7372),   # software
        _row("BANK", cfo=100, ta=200, mc=500, sic=6020),   # national commercial bank
        _row("INSUR", cfo=100, ta=200, mc=500, sic=6311),  # life insurance
    ]
    out = cc.rank_universe(rows, exclude_financials=True)
    by_t = {r.ticker: r for r in out["rows"]}
    assert by_t["BANK"].reason == "financial"
    assert by_t["INSUR"].reason == "financial"
    assert out["summary"]["excluded_financials"] == 2
    assert out["summary"]["ranked"] == 1

    # with the flag off, financials are ranked
    rows2 = [_row("BANK", cfo=100, ta=200, mc=500, sic=6020)]
    out2 = cc.rank_universe(rows2, exclude_financials=False)
    assert out2["summary"]["ranked"] == 1


# ── Batch pipeline robustness ───────────────────────────────────────────────────

_SYNTH_UNIVERSE = {
    "as_of": "2026-06-26",
    "constituents": [
        {"ticker": "AAA", "name": "Alpha", "cik": 111, "exchange": "NYSE", "indices": ["sp500"]},
        {"ticker": "BBB", "name": "Beta", "cik": 222, "exchange": "Nasdaq", "indices": ["sp500"]},
        {"ticker": "CCC", "name": "Gamma", "cik": 333, "exchange": "NYSE", "indices": ["dow30"]},
    ],
}


def test_partial_failure_does_not_abort_run(monkeypatch):
    monkeypatch.setattr(cc, "load_universe", lambda: _SYNTH_UNIVERSE)
    monkeypatch.setattr(cc, "_install_cik_cache", lambda u: None)

    def fake_fetch_one(item, max_cache_age_days):
        if item["ticker"] == "BBB":
            raise TimeoutError("simulated delisted / network timeout")
        return {"ticker": item["ticker"], "fiscal_year": 2025, "sic": 7372,
                "cfo": 100.0, "total_assets": 200.0, "shares": 1e6,
                "price": 50.0, "market_cap": 500.0, "from_cache": False}

    monkeypatch.setattr(cc, "_fetch_one", fake_fetch_one)

    res = cc.compute_champions(concurrency=2)
    by_t = {r.ticker: r for r in res["rows"]}
    # the run completed for all three despite BBB blowing up
    assert len(res["rows"]) == 3
    assert by_t["BBB"].status == "failed"
    assert "TimeoutError" in by_t["BBB"].reason
    # the two survivors were still ranked
    assert res["summary"]["failed"] == 1
    assert res["summary"]["ranked"] == 2
    assert res["summary"]["failures"] == [{"ticker": "BBB", "reason": by_t["BBB"].reason}]


def test_disk_cache_prevents_refetch(monkeypatch, tmp_path):
    # redirect the cache to a temp dir
    monkeypatch.setattr(cc, "_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "_cache_path",
                        lambda t: str(tmp_path / f"{cc._norm(t)}.json"))

    calls = {"facts": 0, "price": 0, "submissions": 0}

    def fake_facts(cik):
        calls["facts"] += 1
        return {"_cik": cik}  # opaque; _extract_inputs is stubbed below

    monkeypatch.setattr(cc.gather_data, "fetch_company_facts", fake_facts)
    monkeypatch.setattr(cc, "_extract_inputs", lambda facts: (2025, 100.0, 200.0, 1e6))
    monkeypatch.setattr(cc, "_fetch_price", lambda t: (calls.__setitem__("price", calls["price"] + 1), 50.0)[1])
    monkeypatch.setattr(cc.gather_data, "fetch_company_submissions",
                        lambda cik: (calls.__setitem__("submissions", calls["submissions"] + 1), {"sic": "7372"})[1])

    item = {"ticker": "ZZZ", "cik": 999}
    first = cc._fetch_one(item, max_cache_age_days=30)
    assert first["from_cache"] is False
    assert first["cfo"] == 100 and first["total_assets"] == 200
    assert first["market_cap"] == 50.0  # 50 * 1e6 / 1e6
    assert calls["facts"] == 1

    # second call within max age → served from cache, zero new fetches
    second = cc._fetch_one(item, max_cache_age_days=30)
    assert second["from_cache"] is True
    assert calls["facts"] == 1
    assert calls["price"] == 1

    # an expired cache (max age 0) forces a re-fetch
    third = cc._fetch_one(item, max_cache_age_days=0)
    assert third["from_cache"] is False
    assert calls["facts"] == 2


def test_universe_snapshot_is_well_formed():
    """The checked-in snapshot exists and has the expected shape."""
    uni = cc.load_universe()
    assert uni["count"] == len(uni["constituents"]) > 400
    sample = uni["constituents"][0]
    assert {"ticker", "name", "cik", "exchange", "indices"} <= set(sample)
    # every constituent belongs to at least one index and has a CIK
    assert all(c["indices"] for c in uni["constituents"])
    assert all(isinstance(c["cik"], int) for c in uni["constituents"])
    # snapshot records an as-of date
    json.dumps(uni)  # serialisable
    assert uni["as_of"]
