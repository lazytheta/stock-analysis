"""Daily closes into Supabase `price_history`, and reading them back.

US tickers come from Nasdaq; non-US watchlist tickers with an ISIN in their
config come from Boerse Frankfurt (Xetra) and are stored under the watchlist
ticker (e.g. "RMS.PA").

Why a table: Yahoo blocks the app's hosts, and a chart that fetched ten years
of history on every page view would be exactly the traffic that gets a source
blocked. A Cloud Run Job (scripts/run_price_history.py) writes once per
trading day; the app only reads.
"""

from __future__ import annotations

import calendar
import logging
import time
from datetime import UTC, date, datetime, timedelta

import quotes

logger = logging.getLogger(__name__)

HISTORY_URL = ("https://api.nasdaq.com/api/quote/{symbol}/historical"
               "?assetclass={cls}&fromdate={start}&todate={end}&limit=9999")
FRANKFURT_HISTORY_URL = ("https://api.boerse-frankfurt.de/v1/tradingview/history"
                         "?symbol={mic}:{isin}&resolution=D&from={start}&to={end}")
FRANKFURT_MIC = "XETR"
TABLE = "price_history"
YEARS = 10
BENCHMARK = "SPY"
PAGE = 1000
OVERLAP_DAYS = 7          # re-fetched days compared against stored closes
SPLIT_TOLERANCE = 0.01    # relative close difference that means a split


def _close(text):
    if not isinstance(text, str):
        return None
    try:
        value = float(text.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def parse_rows(payload) -> list:
    data = payload.get("data") if isinstance(payload, dict) else None
    table = (data or {}).get("tradesTable") if isinstance(data, dict) else None
    rows = (table or {}).get("rows") or []
    out = []
    for row in rows:
        try:
            day = datetime.strptime(row.get("date", ""), "%m/%d/%Y").date()
        except (ValueError, TypeError, AttributeError):
            continue
        close = _close(row.get("close"))
        if close is not None:
            out.append((day.isoformat(), close))
    return sorted(out)


def fetch_history(symbol, start, end, fetch=None) -> list:
    fetch = fetch or quotes._nasdaq_get
    for cls in ("stocks", "etf"):
        rows = parse_rows(fetch(HISTORY_URL.format(
            symbol=symbol, cls=cls, start=start.isoformat(), end=end.isoformat())))
        if rows:
            return rows
    return []


def parse_frankfurt(payload) -> list:
    """TradingView-style {"s":"ok","t":[unix],"c":[close]} -> ascending
    [(iso_day, close)]. Days are UTC dates; a day seen twice keeps the last
    close; closes <= 0 or non-numeric are dropped. "no_data" -> []."""
    if not isinstance(payload, dict) or payload.get("s") != "ok":
        return []
    times, closes = payload.get("t") or [], payload.get("c") or []
    by_day = {}
    for stamp, close in zip(times, closes):
        if isinstance(close, bool) or not isinstance(close, (int, float)) or close <= 0:
            continue
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        day = datetime.fromtimestamp(stamp, UTC).date().isoformat()
        by_day[day] = float(close)
    return sorted(by_day.items())


def _unix(day) -> int:
    return calendar.timegm(day.timetuple())


def fetch_frankfurt_history(isin, start, end, fetch=None) -> list:
    """Xetra daily closes for an ISIN from start through end (inclusive)."""
    fetch = fetch or quotes._http_get_json
    return parse_frankfurt(fetch(FRANKFURT_HISTORY_URL.format(
        mic=FRANKFURT_MIC, isin=isin, start=_unix(start),
        end=_unix(end + timedelta(days=1)) - 1)))


def start_date(last_day, today, years: int = YEARS):
    if last_day is None:
        try:
            return today.replace(year=today.year - years)
        except ValueError:
            # today is Feb 29 and today.year - years isn't a leap year.
            return today.replace(month=2, day=28, year=today.year - years)
    return last_day + timedelta(days=1)


def _split_suspected(rows, stored) -> bool:
    """True when a fetched close differs >1% from the stored close for the
    same day. Nasdaq history is split-adjusted, so after a split every stored
    close is stale and the overlap shows it."""
    for day, close in rows:
        old = (stored or {}).get(day)
        if old and abs(close - old) / old > SPLIT_TOLERANCE:
            return True
    return False


def update_ticker(ticker, today, last_day, fetch, upsert, batch=500,
                  stored=None) -> int:
    """Fetch and upsert new closes. `fetch(ticker, start, end)` is the
    source's history function (Nasdaq for US, Frankfurt bound to the ISIN for
    EU). With a last_day, the fetch overlaps the
    last OVERLAP_DAYS so a split (stored closes no longer matching) triggers a
    full re-fetch that overwrites the whole window."""
    if last_day is None:
        rows = fetch(ticker, start_date(None, today), today)
    else:
        if last_day >= today:
            return 0
        rows = fetch(ticker, last_day - timedelta(days=OVERLAP_DAYS), today)
        if _split_suspected(rows, stored):
            logger.info("price history for %s: stored closes differ, re-fetching", ticker)
            rows = fetch(ticker, start_date(None, today), today)
        else:
            rows = [(d, c) for d, c in rows if d > last_day.isoformat()]
    records = [{"ticker": ticker, "day": d, "close": c} for d, c in rows]
    for i in range(0, len(records), batch):
        upsert(records[i:i + batch])
    return len(records)


def _watchlist_rows(client, cols) -> list:
    """All watchlist_configs rows, paged with a stable order (without one,
    PostgREST pages may overlap or skip rows)."""
    out, start = [], 0
    while True:
        rows = (client.table("watchlist_configs").select(cols).order("ticker")
                .range(start, start + PAGE - 1).execute().data or [])
        if not rows:
            break
        out.extend(rows)
        start += len(rows)
    return out


def _tickers(client) -> list:
    names = [r.get("ticker") for r in _watchlist_rows(client, "ticker")]
    us = [t for t in dict.fromkeys(names) if quotes._nasdaq_symbol_ok(t)]
    return [t for t in us if t != BENCHMARK] + [BENCHMARK]


def _eu_tickers(client) -> dict:
    """{ticker: isin} for non-US watchlist tickers whose config has an ISIN."""
    out = {}
    for r in _watchlist_rows(client, "ticker, isin:config->>isin"):
        ticker, isin = r.get("ticker"), r.get("isin")
        if (isinstance(ticker, str) and ticker and not quotes._nasdaq_symbol_ok(ticker)
                and isinstance(isin, str) and isin.strip()):
            out.setdefault(ticker, isin.strip())
    return out


def _recent(client, ticker):
    """(last stored day or None, {iso_day: close} for the last stored rows)."""
    rows = (client.table(TABLE).select("day, close").eq("ticker", ticker)
            .order("day", desc=True).limit(OVERLAP_DAYS).execute().data or [])
    if not rows:
        return None, {}
    return (date.fromisoformat(rows[0]["day"]),
            {r["day"]: float(r["close"]) for r in rows})


def run(client, today=None, fetch=None, fetch_eu=None, sleep=time.sleep) -> dict:
    """US tickers via `fetch(symbol, start, end)` (Nasdaq), then non-US
    tickers with an ISIN via `fetch_eu(isin, start, end)` (Frankfurt)."""
    today = today or date.today()
    fetch = fetch or fetch_history
    fetch_eu = fetch_eu or fetch_frankfurt_history
    jobs = [(t, fetch) for t in _tickers(client)]
    for ticker, isin in _eu_tickers(client).items():
        jobs.append((ticker, lambda _t, a, b, isin=isin: fetch_eu(isin, a, b)))

    def upsert(records):
        client.table(TABLE).upsert(records, on_conflict="ticker,day").execute()

    total, errors = 0, []
    for i, (ticker, source) in enumerate(jobs):
        if i:
            sleep(0.3)
        try:
            last_day, stored = _recent(client, ticker)
            total += update_ticker(ticker, today, last_day, source, upsert,
                                   stored=stored)
        except Exception as e:
            logger.warning("price history for %s failed: %s: %s",
                           ticker, type(e).__name__, e)
            errors.append(ticker)
    return {"tickers": len(jobs), "rows": total, "errors": errors}


def load_series(client, tickers, since) -> dict:
    """{ticker: [(iso_day, close), ...]} ascending, paging until an empty page
    (the server's row cap may be below the page size)."""
    out = {t: [] for t in tickers}
    for ticker in tickers:
        start = 0
        while True:
            data = (client.table(TABLE).select("day, close").eq("ticker", ticker)
                    .gte("day", since.isoformat()).order("day")
                    .range(start, start + PAGE - 1).execute().data or [])
            if not data:
                break
            out[ticker].extend((r["day"], float(r["close"])) for r in data)
            start += len(data)
    return out
