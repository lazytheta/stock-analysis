"""Daily closes from Nasdaq into Supabase `price_history`, and reading them back.

Why a table: Yahoo blocks the app's hosts, and a chart that fetched ten years
of history on every page view would be exactly the traffic that gets a source
blocked. A Cloud Run Job (scripts/run_price_history.py) writes once per
trading day; the app only reads.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import quotes

logger = logging.getLogger(__name__)

HISTORY_URL = ("https://api.nasdaq.com/api/quote/{symbol}/historical"
               "?assetclass={cls}&fromdate={start}&todate={end}&limit=9999")
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
    """Fetch and upsert new closes. With a last_day, the fetch overlaps the
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


def _tickers(client) -> list:
    names, start = [], 0
    while True:
        rows = (client.table("watchlist_configs").select("ticker")
                .range(start, start + PAGE - 1).execute().data or [])
        if not rows:
            break
        names.extend(r.get("ticker") for r in rows)
        start += len(rows)
    us = [t for t in dict.fromkeys(names) if quotes._nasdaq_symbol_ok(t)]
    return [t for t in us if t != BENCHMARK] + [BENCHMARK]


def _recent(client, ticker):
    """(last stored day or None, {iso_day: close} for the last stored rows)."""
    rows = (client.table(TABLE).select("day, close").eq("ticker", ticker)
            .order("day", desc=True).limit(OVERLAP_DAYS).execute().data or [])
    if not rows:
        return None, {}
    return (date.fromisoformat(rows[0]["day"]),
            {r["day"]: float(r["close"]) for r in rows})


def run(client, today=None, fetch=None, sleep=time.sleep) -> dict:
    today = today or date.today()
    fetch = fetch or fetch_history
    tickers = _tickers(client)

    def upsert(records):
        client.table(TABLE).upsert(records, on_conflict="ticker,day").execute()

    total, errors = 0, []
    for i, ticker in enumerate(tickers):
        if i:
            sleep(0.3)
        try:
            last_day, stored = _recent(client, ticker)
            total += update_ticker(ticker, today, last_day, fetch, upsert,
                                   stored=stored)
        except Exception as e:
            logger.warning("price history for %s failed: %s: %s",
                           ticker, type(e).__name__, e)
            errors.append(ticker)
    return {"tickers": len(tickers), "rows": total, "errors": errors}


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
