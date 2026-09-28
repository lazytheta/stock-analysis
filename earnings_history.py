"""Nasdaq earnings-surprise rows into Supabase `earnings_history`.

Nasdaq only shows the last four quarters. The price-history Cloud Run Job
calls run() daily and upserts whatever Nasdaq returns, so rows older than
Nasdaq's window stay in the table and the history grows past four quarters.
Rows are never deleted; an upsert refreshes values Nasdaq revises.
"""

from __future__ import annotations

import calendar
import logging
import time
from datetime import UTC, date, datetime

import price_history
import quotes

logger = logging.getLogger(__name__)

URL = "https://api.nasdaq.com/api/company/{symbol}/earnings-surprise"
TABLE = "earnings_history"
SOURCE = "nasdaq"
NUMERIC = ("eps", "eps_consensus", "surprise_pct")


def _number(value):
    """1.68 / "1.62" / "$1,234.5" -> float; "N/A", "", None -> None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    try:
        return float(value.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _quarter_end(text):
    """"Jul 2026" -> "2026-07-31" (last day of that month), else None."""
    try:
        d = datetime.strptime(str(text).strip(), "%b %Y").date()
    except (ValueError, TypeError):
        return None
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1]).isoformat()


def _reported(text):
    """"8/26/2026" -> "2026-08-26", else None."""
    try:
        return datetime.strptime(str(text).strip(), "%m/%d/%Y").date().isoformat()
    except (ValueError, TypeError):
        return None


def parse_rows(payload, ticker) -> list:
    data = payload.get("data") if isinstance(payload, dict) else None
    table = data.get("earningsSurpriseTable") if isinstance(data, dict) else None
    rows = (table or {}).get("rows") if isinstance(table, dict) else None
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        qtr = _quarter_end(row.get("fiscalQtrEnd"))
        if qtr is None:
            continue
        out.append({
            "ticker": ticker,
            "fiscal_qtr_end": qtr,
            "date_reported": _reported(row.get("dateReported")),
            "eps": _number(row.get("eps")),
            "eps_consensus": _number(row.get("consensusForecast")),
            "surprise_pct": _number(row.get("percentageSurprise")),
            "source": SOURCE,
        })
    return out


def fetch(ticker, fetch=None) -> list:
    get = fetch or quotes._nasdaq_get
    return parse_rows(get(URL.format(symbol=ticker)), ticker)


# run() takes a `fetch` argument (the raw HTTP getter) that shadows the
# function above inside its body.
_fetch_ticker = fetch


def run(client, fetch=None, sleep=time.sleep) -> dict:
    """Upsert Nasdaq's earnings rows for every US watchlist ticker (not SPY)."""
    tickers = [t for t in price_history._tickers(client)
               if t != price_history.BENCHMARK]
    total, errors = 0, []
    for i, ticker in enumerate(tickers):
        if i:
            sleep(0.3)
        try:
            records = _fetch_ticker(ticker, fetch=fetch)
            if records:
                # Column default only fires on insert; stamp revisions too.
                stamp = datetime.now(UTC).isoformat()
                records = [{**r, "updated_at": stamp} for r in records]
                client.table(TABLE).upsert(
                    records, on_conflict="ticker,fiscal_qtr_end").execute()
            total += len(records)
        except Exception as e:
            logger.warning("earnings history for %s failed: %s: %s",
                           ticker, type(e).__name__, e)
            errors.append(ticker)
    return {"tickers": len(tickers), "rows": total, "errors": errors}


def load(client, ticker) -> list:
    """Stored rows for one ticker, ascending by fiscal_qtr_end."""
    rows = (client.table(TABLE)
            .select("fiscal_qtr_end, date_reported, eps, eps_consensus, "
                    "surprise_pct, source")
            .eq("ticker", ticker).order("fiscal_qtr_end").execute().data or [])
    out = []
    for r in rows:
        r = dict(r)
        for key in NUMERIC:
            if key in r:
                r[key] = _number(r[key])
        out.append(r)
    return out
