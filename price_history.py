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


def update_ticker(ticker, today, last_day, fetch, upsert, batch=500) -> int:
    start = start_date(last_day, today)
    if start > today:
        return 0
    rows = fetch(ticker, start, today)
    records = [{"ticker": ticker, "day": d, "close": c} for d, c in rows]
    for i in range(0, len(records), batch):
        upsert(records[i:i + batch])
    return len(records)


def _tickers(client) -> list:
    rows = client.table("watchlist_configs").select("ticker").execute().data or []
    names = [r.get("ticker") for r in rows]
    us = [t for t in dict.fromkeys(names) if quotes._nasdaq_symbol_ok(t)]
    return [t for t in us if t != BENCHMARK] + [BENCHMARK]


def _last_day(client, ticker):
    rows = (client.table(TABLE).select("day").eq("ticker", ticker)
            .order("day", desc=True).limit(1).execute().data or [])
    return date.fromisoformat(rows[0]["day"]) if rows else None


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
            total += update_ticker(ticker, today, _last_day(client, ticker),
                                   fetch, upsert)
        except Exception as e:
            logger.warning("price history for %s failed: %s: %s",
                           ticker, type(e).__name__, e)
            errors.append(ticker)
    return {"tickers": len(tickers), "rows": total, "errors": errors}


def load_series(client, tickers, since) -> dict:
    """{ticker: [(iso_day, close), ...]} ascending, paging past the 1000-row cap."""
    out = {t: [] for t in tickers}
    for ticker in tickers:
        start = 0
        while True:
            data = (client.table(TABLE).select("day, close").eq("ticker", ticker)
                    .gte("day", since.isoformat()).order("day")
                    .range(start, start + 999).execute().data or [])
            out[ticker].extend((r["day"], float(r["close"])) for r in data)
            if len(data) < 1000:
                break
            start += 1000
    return out
