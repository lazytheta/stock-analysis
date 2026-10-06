"""Reporting currency: the Portfolio figures in euros as well as dollars.

The app prices everything in USD. The owner invests from euros, so a USD
return leaves out the currency effect the Trading 212 app does show (META
+33.6% in dollars, ~38% in euros). In EUR mode:

1. A Trading 212 position's cost is Trading 212's own `totalCost` in the
   account currency -- the rate it actually converted at.
2. A cost built from trades (Tastytrade) converts every trade amount at the
   ECB rate of the trade date, or the last rate before it.
3. A current value converts at today's rate; an instrument quoted in EUR keeps
   its own euro value.

Pure: no Streamlit, no network. The caller supplies today's rate and the ECB
history ({date: USD per EUR}). Amounts keep the app's sign convention: costs
are negative (cash that left the account).

Spec: docs/superpowers/specs/2026-10-06-reporting-currency-portfolio-design.md
"""

from datetime import date, datetime

CURRENCIES = ("EUR", "USD")
DEFAULT = "EUR"
SYMBOL = {"EUR": "€", "USD": "$"}


def normalise(value):
    """A supported currency code; anything else is the default."""
    code = (value or "").upper()
    return code if code in CURRENCIES else DEFAULT


def _day(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def rate_on(history, day):
    """USD per EUR on `day`, or on the last day before it with a rate (ECB
    publishes no rate on weekends and holidays). None when nothing is known
    on or before that day."""
    d = _day(day)
    if not history or d is None:
        return None
    best_day, best = None, None
    for k, v in history.items():
        kd = _day(k)
        if kd is None or kd > d or not v:
            continue
        if best_day is None or kd > best_day:
            best_day, best = kd, v
    return best


def is_equity_cost_trade(t):
    # Same trades that build equity_cost / wheel_equity_cost: equity rows,
    # minus dividends and other cash movements booked on the instrument.
    return t.get("instrument_type") == "Equity" and (t.get("type") or "") != "Money Movement"


def is_option_trade(t):
    return "Option" in (t.get("instrument_type") or "")


def convert_trades(trades, usd_total, usd_per_eur_now, history, include):
    """EUR equivalent of `usd_total`, the sum of the included trades' USD
    net_value, converting each trade at its own date's rate.

    Whatever part of usd_total the trades do not explain (a history that
    starts after the position did) converts at today's rate, so the result
    always reconciles with the USD figure it stands for.

    Returns (eur_total, missing) where missing is True when at least one
    trade had no historical rate and was converted at today's rate.
    """
    eur, usd_seen, missing = 0.0, 0.0, False
    for t in trades or []:
        if not include(t):
            continue
        net = t.get("net_value") or 0.0
        rate = rate_on(history, t.get("date"))
        if rate is None:
            rate, missing = usd_per_eur_now, True
        eur += net / rate
        usd_seen += net
    eur += ((usd_total or 0.0) - usd_seen) / usd_per_eur_now
    return eur, missing


def _t212_eur_cost(row):
    """Trading 212's own cost in EUR (negative), or None when absent."""
    if row.get("account_currency") != "EUR":
        return None
    cost = row.get("account_cost")
    if cost is None:
        return None
    return -abs(cost)


def annotate(row, usd_per_eur_now, history):
    """Add the EUR cost fields to one per-broker row, in place.

    equity_cost_eur  -- the row's equity_cost in EUR (negative)
    option_pl_eur    -- option premiums in EUR, each at its own date
    fx_missing       -- True when some amount fell back to today's rate

    Done per broker row, before rows of one symbol are merged, so a holding
    split across brokers keeps each broker's own basis.
    """
    trades = row.get("trades") or []
    t212_cost = _t212_eur_cost(row) if row.get("broker") == "Trading 212" else None
    if t212_cost is not None:
        row["equity_cost_eur"] = t212_cost
        missing = False
    elif row.get("broker") == "Trading 212":
        # No walletImpact: Trading 212's fills carry their own EUR amount,
        # which is the rate actually paid. Their USD net_value was converted
        # at today's rate and must not be divided by a historical one.
        eur = sum(t.get("wallet_net_value") or 0.0 for t in trades
                  if t.get("wallet_net_value") is not None)
        if eur:
            row["equity_cost_eur"], missing = eur, False
        else:
            row["equity_cost_eur"] = (row.get("equity_cost") or 0.0) / usd_per_eur_now
            missing = True
    else:
        row["equity_cost_eur"], missing = convert_trades(
            trades, row.get("equity_cost"), usd_per_eur_now, history, is_equity_cost_trade)
    row["option_pl_eur"], opt_missing = convert_trades(
        trades, row.get("option_pl"), usd_per_eur_now, history, is_option_trade)
    row["fx_missing"] = missing or opt_missing
    return row


def value_eur(row, usd_per_eur_now):
    """The row's market value (USD, live) in EUR. An instrument quoted in EUR
    divides by the rate it was converted at, which gives back its own euro
    value exactly."""
    mv = row.get("market_value") or 0.0
    if row.get("native_currency") == "EUR" and row.get("fx_rate"):
        return mv / row["fx_rate"]
    return mv / usd_per_eur_now


def wheel_cost_eur(cycle_trades, wheel_equity_cost, usd_per_eur_now, history):
    """EUR equivalent of a wheel cycle's equity cost (negative)."""
    eur, _ = convert_trades(cycle_trades, wheel_equity_cost, usd_per_eur_now,
                            history, is_equity_cost_trade)
    return eur


def balance_eur(broker_balance, usd_per_eur_now):
    """(net_liq_eur, cash_eur) for one broker's balance dict. A broker whose
    account is in EUR converts back with the rate it was converted at, which
    returns its own euro figures; a USD account converts at today's rate."""
    nlv = broker_balance.get("net_liquidating_value") or 0.0
    cash = broker_balance.get("cash_balance") or 0.0
    if broker_balance.get("native_currency") == "EUR" and broker_balance.get("fx_rate"):
        rate = broker_balance["fx_rate"]
    else:
        rate = usd_per_eur_now
    return nlv / rate, cash / rate


def fmt_money(amount, ccy, decimals=0, signed=False):
    """'€1,234' / '$1,234' / '€+1,234' / '€-1,234'."""
    sym = SYMBOL.get(ccy, "$")
    if signed:
        return f"{sym}{amount:+,.{decimals}f}"
    return f"{sym}{amount:,.{decimals}f}"


# ── Step 2: a euro copy of the data for the Results page ──────────────────
# Spec: docs/superpowers/specs/2026-10-06-reporting-currency-results-design.md


def _trade_eur(t, usd_per_eur_now, history):
    """A copy of one trade with net_value and price in EUR."""
    out = dict(t)
    qty = abs(t.get("quantity") or 0.0)
    if t.get("wallet_net_value") is not None:
        # Trading 212: the euros the account actually moved.
        out["net_value"] = t["wallet_net_value"]
        out["price"] = abs(out["net_value"]) / qty if qty else (t.get("price") or 0.0)
        return out
    rate = rate_on(history, t.get("date")) or usd_per_eur_now
    out["net_value"] = (t.get("net_value") or 0.0) / rate
    out["price"] = (t.get("price") or 0.0) / rate
    return out


def _is_dividend_row(t):
    return t.get("instrument_type") == "Equity" and (t.get("type") or "") == "Money Movement"


def to_eur_cost_basis(cost_basis, usd_per_eur_now, history):
    """A new cost_basis with every amount in EUR (the input is not modified).

    Trades convert at their own date (Trading 212 fills use their own euro
    amount); sums are rebuilt from the converted trades, with any USD
    remainder the trades do not explain at today's rate; live prices convert
    at today's rate, EUR-quoted lines keep their euros. Every Results
    computation can then run on it unchanged.
    """
    from trade_utils import detect_wheels

    out = {}
    for key, row in (cost_basis or {}).items():
        r = dict(row)
        usd_trades = row.get("trades") or []
        trades = [_trade_eur(t, usd_per_eur_now, history) for t in usd_trades]
        r["trades"] = trades

        def _sum(pred, usd_field, _usd=usd_trades, _eur=trades, _row=row):
            eur = sum(t.get("net_value") or 0.0 for t in _eur if pred(t))
            usd_seen = sum(t.get("net_value") or 0.0 for t in _usd if pred(t))
            return eur + ((_row.get(usd_field) or 0.0) - usd_seen) / usd_per_eur_now

        r["equity_cost"] = _sum(is_equity_cost_trade, "equity_cost")
        if row.get("broker") == "Trading 212":
            own = _t212_eur_cost(row)
            if own is not None and row.get("shares_held"):
                r["equity_cost"] = own
        r["option_pl"] = _sum(is_option_trade, "option_pl")
        r["dividends"] = _sum(_is_dividend_row, "dividends")
        r["total_pl"] = _sum(lambda t: True, "total_pl")
        r["total_credits"] = sum(t["net_value"] for t in trades if t["net_value"] > 0)
        r["total_debits"] = sum(t["net_value"] for t in trades if t["net_value"] < 0)
        r["adjusted_cost"] = r["equity_cost"] + r["option_pl"]
        shares = row.get("shares_held") or 0
        r["cost_per_share"] = r["adjusted_cost"] / shares if shares else 0.0

        if row.get("native_currency") == "EUR" and row.get("fx_rate"):
            live_rate = row["fx_rate"]
        else:
            live_rate = usd_per_eur_now
        for field in ("current_price", "previous_close", "broker_price", "purchase_price"):
            if row.get(field) is not None:
                r[field] = row[field] / live_rate
        r["market_value"] = (r.get("current_price") or 0.0) * shares
        r["total_pl_real"] = r["total_pl"] + r["market_value"]
        r["wheels"] = detect_wheels(trades) if row.get("wheels") else []
        r["currency"] = "EUR"
        r["fx_rate"] = 1.0
        r["_eur_history"] = history
        out[key] = r
    return out


def series_to_eur(series, history, usd_per_eur_now):
    """A curve [{"time", "close"}] in USD → EUR, each point at its day's rate."""
    out = []
    for p in series or []:
        rate = rate_on(history, p.get("time")) or usd_per_eur_now
        out.append({**p, "close": (p.get("close") or 0.0) / rate})
    return out


def closes_to_eur(closes, history, usd_per_eur_now):
    """{date: close} in USD → EUR at each day's rate."""
    return {d: c / (rate_on(history, d) or usd_per_eur_now)
            for d, c in (closes or {}).items() if c}


def transfers_monthly_to_eur(transfers, history, usd_per_eur_now):
    """Fallback for a broker that only reports monthly totals (IBKR): each
    month at its mid-month rate."""
    out = {}
    for year, entry in (transfers or {}).items():
        months = {}
        for month, amount in (entry.get("months") or {}).items():
            rate = rate_on(history, date(int(year), int(month), 15)) or usd_per_eur_now
            months[month] = amount / rate
        out[year] = {"total": sum(months.values()), "months": months}
    return out


def report_move(shares, close_start, close_end, day_start, day_end, row):
    """Unrealized move of a held position between two report dates, in the
    row's currency. A euro copy (row has _eur_history) converts a non-EUR
    close at each date's own rate, so the currency move is in it."""
    hist = row.get("_eur_history")
    if hist is not None and (row.get("native_currency") or "USD") != "EUR":
        r0 = rate_on(hist, day_start)
        r1 = rate_on(hist, day_end)
        if r0 and r1:
            return shares * (close_end / r1 - close_start / r0)
    return shares * (close_end - close_start) * (row.get("fx_rate") or 1.0)
