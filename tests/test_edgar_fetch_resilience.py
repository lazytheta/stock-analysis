"""EDGAR transport failures must reach the caller, never masquerade as data.

A watchlist row that shows "—" for FCF Yield is a claim about the filer. When
the real cause is a throttled SEC request, that claim is a lie — and because
callers cache fetch_fundamentals() results for 24h, one lie sticks for a day.
These tests pin the boundary between "filer reports nothing" (empty result)
and "we could not ask" (EdgarFetchError).
"""
import json
import threading
import time
import urllib.error

import pytest

import gather_data as g


def _http_error(code):
    return urllib.error.HTTPError("https://data.sec.gov/x", code, "boom", {}, None)


@pytest.fixture(autouse=True)
def _reset_edgar_state():
    """The ticker index is process-cached; keep tests independent."""
    g._sec_ticker_map_cached.cache_clear()
    yield
    g._sec_ticker_map_cached.cache_clear()


# ── Transport failure vs. absent data ─────────────────────────────────────

def test_companyfacts_http_failure_raises_rather_than_returning_empty(monkeypatch):
    monkeypatch.setattr(g, "get_cik", lambda t: 320193)
    monkeypatch.setattr(g, "fetch_company_facts",
                        lambda cik: (_ for _ in ()).throw(_http_error(403)))

    with pytest.raises(g.EdgarFetchError):
        g.fetch_fundamentals("AAPL", n_years=5)


def test_ticker_index_http_failure_raises(monkeypatch):
    def boom(t):
        raise _http_error(429)
    monkeypatch.setattr(g, "get_cik", boom)

    with pytest.raises(g.EdgarFetchError):
        g.fetch_fundamentals("AAPL", n_years=5)


def test_unknown_ticker_is_absent_data_not_a_fetch_error(monkeypatch):
    """A ticker missing from SEC's index genuinely has no EDGAR data.
    That must stay cacheable — it is not an outage."""
    def not_found(t):
        raise ValueError(f"Ticker '{t}' not found in SEC database")
    monkeypatch.setattr(g, "get_cik", not_found)

    result = g.fetch_fundamentals("NOTATICKER", n_years=5)
    assert result["years"] == []
    assert result["fcf"] == []


def test_malformed_json_from_edgar_raises(monkeypatch):
    monkeypatch.setattr(g, "get_cik", lambda t: 320193)
    monkeypatch.setattr(g, "fetch_company_facts",
                        lambda cik: (_ for _ in ()).throw(
                            json.JSONDecodeError("bad", "", 0)))

    with pytest.raises(g.EdgarFetchError):
        g.fetch_fundamentals("AAPL", n_years=5)


# ── Retry / backoff ───────────────────────────────────────────────────────

def test_http_get_retries_throttling_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(req, **kw):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise _http_error(429)
        class _Resp:
            def read(self): return b'{"ok": true}'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return _Resp()

    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    assert g._http_get("https://data.sec.gov/x", {}) == b'{"ok": true}'
    assert len(calls) == 3


def test_http_get_does_not_retry_a_404(monkeypatch):
    """404 is an answer, not a failure — retrying wastes the rate budget."""
    calls = []

    def fake_urlopen(req, **kw):
        calls.append(req.full_url)
        raise _http_error(404)

    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(g.time, "sleep", lambda s: None)

    with pytest.raises(urllib.error.HTTPError):
        g._http_get("https://data.sec.gov/x", {})
    assert len(calls) == 1


def _http_get_with_retry_after(monkeypatch, header_value):
    slept = []

    class _Err(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("u", 429, "slow down", {"Retry-After": header_value}, None)

    def fake_urlopen(req, **kw):
        raise _Err()

    monkeypatch.setattr(g.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(g.time, "sleep", slept.append)
    with pytest.raises(urllib.error.HTTPError):
        g._http_get("https://data.sec.gov/x", {}, retries=2)
    return slept


def test_http_get_honours_retry_after_header(monkeypatch):
    # `slept` also holds the sub-second rate-limiter pauses; the backoff is the
    # one that took the server's Retry-After over our own exponential default.
    slept = _http_get_with_retry_after(monkeypatch, "3")
    assert 3.0 in slept, f"Retry-After ignored, slept={slept}"


def test_http_get_caps_an_absurd_retry_after(monkeypatch):
    """`Retry-After: 3600` was letterlijk een uur time.sleep in een
    Streamlit-run. De header wint van onze eigen backoff, maar niet
    onbegrensd -- dezelfde cap als _QUOTE_RETRY_AFTER_CAP in tastytrade_api."""
    slept = _http_get_with_retry_after(monkeypatch, "3600")
    assert 3600.0 not in slept
    assert g.RETRY_AFTER_CAP in slept, f"cap niet toegepast, slept={slept}"


# ── Rate limiting & index caching ─────────────────────────────────────────

def test_sec_requests_are_throttled_but_other_hosts_are_not(monkeypatch):
    monkeypatch.setattr(g, "_SEC_MIN_INTERVAL", 0.05)
    g._sec_last_request = 0.0

    class _Resp:
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(g.urllib.request, "urlopen", lambda req, **kw: _Resp())

    t0 = time.monotonic()
    for _ in range(4):
        g._http_get("https://data.sec.gov/x", {})
    sec_elapsed = time.monotonic() - t0

    t0 = time.monotonic()
    for _ in range(4):
        g._http_get("https://query1.finance.yahoo.com/x", {})
    yahoo_elapsed = time.monotonic() - t0

    assert sec_elapsed >= 0.10   # at least 3 gaps of 0.05s after the first call
    assert yahoo_elapsed < 0.05  # Yahoo is not behind the SEC gate


def test_ticker_index_downloaded_once_under_concurrent_fanout(monkeypatch):
    downloads = []

    def fake_get_json(url, headers=None):
        downloads.append(url)
        time.sleep(0.02)  # widen the race window
        return {"0": {"ticker": "MA", "cik_str": 1141391, "title": "Mastercard Inc"}}

    monkeypatch.setattr(g, "_http_get_json", fake_get_json)

    results = []
    threads = [threading.Thread(target=lambda: results.append(g.get_cik("MA")))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [1141391] * 8
    assert len(downloads) == 1, f"index re-downloaded {len(downloads)}x"


# ── Debt tags whole sectors actually use ──────────────────────────────────

def _facts(**tags):
    """company-facts JSON carrying one annual value per named tag."""
    return {"facts": {"us-gaap": {
        tag: {"units": {"USD": [{"end": f"{2025}-12-31", "val": val, "form": "10-K",
                                "fy": 2025, "fp": "FY", "start": "2025-01-01"}]}}
        for tag, val in tags.items()
    }}}


def test_named_instrument_debt_tags_are_read(monkeypatch):
    """Homebuilders file NotesPayable, REITs SeniorNotes and some software
    filers UnsecuredLongTermDebt — none of them ever file a LongTermDebt tag.
    Read literally they are debt-free, which is the one error a screen whose
    second test is "no net debt" cannot afford: VeriSign's $1.8bn of senior
    notes made it look like a net-cash business."""
    for tag in ("SeniorNotes", "NotesPayable", "UnsecuredLongTermDebt",
                "SecuredDebt", "DebtAndCapitalLeaseObligations"):
        monkeypatch.setattr(g, "get_cik", lambda t: 1)
        monkeypatch.setattr(g, "fetch_company_facts",
                            lambda cik, _t=tag: _facts(Assets=10_000e6, Revenues=5_000e6, **{_t: 1_788e6}))
        f = g.fetch_fundamentals("X", n_years=3)
        debt = [v for v in (f.get("total_debt") or []) if v is not None]
        assert debt and debt[-1] == 1788, f"{tag} was not read as debt"


def test_bond_holdings_are_not_mistaken_for_borrowings(monkeypatch):
    """AvailableForSaleSecuritiesDebt* is the filer's own bond portfolio — an
    asset. Matching debt tags by the word "Debt" would turn Veeva's $5bn cash
    pile into $5bn of borrowings and fail it on the very test it passes."""
    monkeypatch.setattr(g, "get_cik", lambda t: 1)
    monkeypatch.setattr(g, "fetch_company_facts", lambda cik: _facts(
        Assets=10_000e6, Revenues=5_000e6,
        AvailableForSaleSecuritiesDebtSecuritiesNoncurrent=5_140e6,
        AvailableForSaleDebtSecuritiesAmortizedCostBasis=5_111e6,
    ))
    f = g.fetch_fundamentals("X", n_years=3)
    assert not [v for v in (f.get("total_debt") or []) if v]


def test_primary_debt_tag_wins_over_the_named_fallback(monkeypatch):
    """The fallback fills gaps; it must never overwrite a figure the filer
    already reported, or a company with both a term loan and senior notes
    would report only the notes."""
    monkeypatch.setattr(g, "get_cik", lambda t: 1)
    monkeypatch.setattr(g, "fetch_company_facts", lambda cik: _facts(
        Assets=10_000e6, Revenues=5_000e6, LongTermDebt=3_000e6, SeniorNotes=1_000e6))
    f = g.fetch_fundamentals("X", n_years=3)
    debt = [v for v in (f.get("total_debt") or []) if v is not None]
    assert debt[-1] == 3000
