"""Where the portfolio's return since the strategy start came from.

The Results page shows two numbers that look like they disagree: the whole
portfolio since the start (deposit-adjusted, the net-liq curve) and the new
buys against SPY (each lot over its own days). They measure different money.
This splits the first into parts, in money and in percentage points of the
same Simple Dietz denominator, so they add up to the portfolio's return:

  new buys       lots bought on or after the start: what each made (sold or
                 held), from its purchase price
  old held       shares bought before the start and still held: their move
                 from the start-day close
  old sold       shares bought before the start and sold after it: from the
                 start-day close to the sale
  income         option premiums and dividends booked after the start
  other          the rest of the curve's P/L: interest, fees, currency moves,
                 positions without a start price

Plus the cost of uninvested cash against SPY: the average share of the money
not in any position over the window, times SPY's return. Approximate by
design (positions at cost, cash as the remainder), which the page says.

Pure: the caller supplies the rows, the start-day prices, the curve's P/L and
denominator and SPY's return. Owner request 2026-10-09.
"""

from datetime import date, datetime

from portfolio_metrics import _equity_lots


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def dietz_parts(series, transfers, start):
    """(P/L, denominator) of the Simple Dietz return from `start`, the same
    rule as the page's _dietz_return (deposits by month; the start month only
    when the window starts on or before the 15th). None when not computable."""
    pts = sorted((str(p["time"])[:10], p["close"]) for p in series or [])
    window = [(d, c) for d, c in pts if d >= start.isoformat()]
    if len(window) < 2:
        return None
    start_v, end_v = window[0][1], window[-1][1]
    net_dep = 0.0
    start_month = date(start.year, start.month, 1)
    for yr, entry in (transfers or {}).items():
        for mo, amount in ((entry or {}).get("months") or {}).items():
            first = date(int(yr), int(mo), 1)
            if first > start_month or (first == start_month and start.day <= 15):
                net_dep += amount
    denom = start_v + 0.5 * net_dep
    if denom <= 0:
        return None
    return end_v - start_v - net_dep, denom


def pieces(trades, current_price):
    """FIFO pieces of one account's equity trades:
    [{"qty", "buy_price", "buy_date", "end_price", "end_date"}] where end is
    the sale (price, date) or, for shares still held, today's price and None."""
    lots, out = [], []
    for t, qty, price, is_buy in _equity_lots(trades or []):
        if is_buy:
            lots.append([qty, price, _day(t.get("date"))])
            continue
        remaining = qty
        sale_price = abs(t.get("net_value") or 0.0) / qty if qty else price
        while remaining > 1e-9 and lots:
            take = min(lots[0][0], remaining)
            out.append({"qty": take, "buy_price": lots[0][1], "buy_date": lots[0][2],
                        "end_price": sale_price, "end_date": _day(t.get("date"))})
            lots[0][0] -= take
            remaining -= take
            if lots[0][0] <= 1e-9:
                lots.pop(0)
    for qty, price, day in lots:
        if qty > 1e-9:
            out.append({"qty": qty, "buy_price": price, "buy_date": day,
                        "end_price": current_price, "end_date": None})
    return out


def attribute(rows, start, start_prices, total_pl, denom, spy_pct, today=None):
    """The split, as a dict of buckets {"new", "old_held", "old_sold",
    "income", "other"} each {"pl", "pts", "names"}, plus "total_pts",
    "cash_share" (0-1) and "cash_drag_pts" (the cash's cost against SPY).

    rows: {key: cost-basis row} per broker account (trades, current_price,
    symbol); start_prices: {symbol: close on the start day} in the rows'
    currency; total_pl / denom from dietz_parts; spy_pct: SPY's return over
    the window in percent (None skips the cash line)."""
    today = today or date.today()
    window_days = max(1, (today - start).days)
    buckets = {k: {"pl": 0.0, "names": []} for k in ("new", "old_held", "old_sold", "income")}
    invested_days = 0.0

    def _name(bucket, sym):
        if sym not in buckets[bucket]["names"]:
            buckets[bucket]["names"].append(sym)

    for key, row in (rows or {}).items():
        sym = row.get("symbol") or key
        trades = row.get("trades") or []
        cur = row.get("current_price") or 0.0
        for p in pieces(trades, cur):
            bought, ended = p["buy_date"], p["end_date"]
            if ended is not None and ended < start:
                continue                       # in and out before the window
            end_day = ended or today
            if bought and bought >= start:
                buckets["new"]["pl"] += p["qty"] * (p["end_price"] - p["buy_price"])
                invested_days += p["qty"] * p["buy_price"] * (end_day - bought).days
                _name("new", sym)
                continue
            p0 = start_prices.get(sym)
            if not p0:
                continue                       # no start price: lands in "other"
            bucket = "old_held" if ended is None else "old_sold"
            buckets[bucket]["pl"] += p["qty"] * (p["end_price"] - p0)
            invested_days += p["qty"] * p0 * (end_day - start).days
            _name(bucket, sym)
        for t in trades:
            d = _day(t.get("date"))
            if d is None or d < start:
                continue
            inst = t.get("instrument_type") or ""
            dividend = inst == "Equity" and (t.get("type") or "") == "Money Movement"
            if "Option" in inst or dividend:
                buckets["income"]["pl"] += t.get("net_value") or 0.0
                _name("income", sym)

    known = sum(b["pl"] for b in buckets.values())
    buckets["other"] = {"pl": total_pl - known, "names": []}
    for b in buckets.values():
        b["pts"] = b["pl"] / denom * 100
    avg_invested = invested_days / window_days
    cash_share = min(1.0, max(0.0, 1 - avg_invested / denom))
    return {**buckets, "total_pts": total_pl / denom * 100, "cash_share": cash_share,
            "cash_drag_pts": (-cash_share * spy_pct) if spy_pct is not None else None}
