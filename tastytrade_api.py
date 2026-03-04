"""
Tastytrade API module — fetch transactions and calculate per-ticker cost basis.
Used by the Portfolio page in streamlit_app.py.
"""

import asyncio
import json
import os
import ssl
import urllib.request
from collections import defaultdict
from decimal import Decimal

from dotenv import load_dotenv
from tastytrade import Session, Account, DXLinkStreamer
from tastytrade.dxfeed import Greeks as GreeksEvent
from tastytrade.instruments import Option


def _get_secret(key):
    """Get a secret from .env (local) or st.secrets (Streamlit Cloud)."""
    load_dotenv()
    val = os.environ.get(key)
    if val:
        return val
    try:
        import streamlit as st
        return st.secrets[key]
    except Exception:
        raise KeyError(key)


def _get_session():
    """Create a Tastytrade Session from .env or Streamlit Cloud secrets."""
    return Session(
        provider_secret=_get_secret("TASTYTRADE_CLIENT_SECRET"),
        refresh_token=_get_secret("TASTYTRADE_REFRESH_TOKEN"),
    )


def fetch_portfolio_data():
    """
    Fetch all transactions from Tastytrade and compute cost basis.
    Returns (cost_basis_dict, account_number).
    """
    session = _get_session()

    async def _run():
        async with session:
            accounts = await Account.get(session)
            all_txns = []
            acct_num = None
            for acct in accounts:
                acct_num = acct.account_number
                txns = await acct.get_history(
                    session,
                    per_page=250,
                    page_offset=None,  # None = fetch all pages
                    sort="Asc",
                )
                all_txns.extend(txns)
            return all_txns, acct_num

    transactions, account_number = asyncio.run(_run())
    return calculate_cost_basis(transactions), account_number


def calculate_cost_basis(transactions):
    """
    Process transactions into per-ticker cost basis.

    Returns dict keyed by ticker with:
        total_credits, total_debits, dividends, shares_held,
        option_pl, equity_cost, total_pl, adjusted_cost,
        cost_per_share, trades
    """
    tickers = defaultdict(lambda: {
        "total_credits": Decimal(0),
        "total_debits": Decimal(0),
        "dividends": Decimal(0),
        "shares_held": Decimal(0),
        "option_pl": Decimal(0),
        "equity_cost": Decimal(0),
        "total_pl": Decimal(0),
        "trades": [],
    })

    for txn in transactions:
        ticker = txn.underlying_symbol or txn.symbol
        if not ticker:
            continue

        data = tickers[ticker]
        net = txn.net_value if txn.net_value is not None else Decimal(0)
        qty = txn.quantity if txn.quantity is not None else Decimal(0)
        inst = txn.instrument_type.value if txn.instrument_type else ""
        txn_type = txn.transaction_type or ""
        sub_type = txn.transaction_sub_type or ""
        action_str = txn.action.value if txn.action else ""

        # Determine trade label (CSP, CC, etc.)
        desc = txn.description or ""
        is_put = "Put" in desc
        is_call = "Call" in desc
        if "dividend" in sub_type.lower():
            label = "Dividend"
        elif "Option" in inst:
            if action_str == "Sell to Open":
                label = "CSP" if is_put else "CC"
            elif action_str == "Buy to Close":
                label = "BTC CSP" if is_put else "BTC CC"
            elif action_str == "Buy to Open":
                label = "BTO Put" if is_put else "BTO Call"
            elif action_str == "Sell to Close":
                label = "STC Put" if is_put else "STC Call"
            elif sub_type == "Expiration":
                label = "Expired"
            elif sub_type == "Assignment":
                label = "Assignment"
            else:
                label = "Option"
        elif inst == "Equity":
            if txn_type == "Receive Deliver":
                label = "Assignment"
            elif "Buy" in action_str:
                label = "Stock Buy"
            elif "Sell" in action_str:
                label = "Stock Sell"
            else:
                label = "Equity"
        else:
            label = sub_type or txn_type

        # Trade detail record
        data["trades"].append({
            "date": txn.transaction_date,
            "label": label,
            "type": txn_type,
            "sub_type": sub_type,
            "description": txn.description,
            "symbol": txn.symbol,
            "action": action_str,
            "quantity": float(qty),
            "price": float(txn.price) if txn.price is not None else 0.0,
            "net_value": float(net),
            "instrument_type": inst,
        })

        # Running totals
        data["total_pl"] += net
        if net > 0:
            data["total_credits"] += net
        elif net < 0:
            data["total_debits"] += net

        # Categorize
        is_dividend = "dividend" in sub_type.lower()

        if is_dividend:
            data["dividends"] += net
        elif "Option" in inst:
            data["option_pl"] += net
        elif inst == "Equity":
            data["equity_cost"] += net
            if txn_type == "Receive Deliver":
                # Assignment / exercise — direction from cash flow
                if net < 0:
                    data["shares_held"] += qty  # bought shares (put assignment)
                elif net > 0:
                    data["shares_held"] -= qty  # sold shares (call assignment)
            else:
                # Regular equity trade
                if "Buy" in action_str:
                    data["shares_held"] += qty
                elif "Sell" in action_str:
                    data["shares_held"] -= qty

    # Finalize results — sort by most recent trade date (newest first)
    result = {}
    for ticker, data in sorted(
        tickers.items(),
        key=lambda x: x[1]["trades"][-1]["date"] if x[1]["trades"] else None,
        reverse=True,
    ):
        shares = data["shares_held"]
        adjusted = data["equity_cost"] + data["option_pl"]
        cps = float(adjusted / shares) if shares != 0 else 0.0

        wheels = _detect_wheels(data["trades"])

        result[ticker] = {
            "total_credits": float(data["total_credits"]),
            "total_debits": float(data["total_debits"]),
            "dividends": float(data["dividends"]),
            "shares_held": int(data["shares_held"]),
            "option_pl": float(data["option_pl"]),
            "equity_cost": float(data["equity_cost"]),
            "total_pl": float(data["total_pl"]),
            "adjusted_cost": float(adjusted),
            "cost_per_share": cps,
            "trades": data["trades"],
            "wheels": wheels,
        }

    return result


def _detect_wheels(trades):
    """
    Detect completed wheel cycles from a ticker's trade list.

    A wheel cycle = all trades from one "shares at 0" to the next.
    Completed when shares return to 0 after being held.
    Whatever remains with shares > 0 is an active (in-progress) wheel.
    """
    cycles = []
    cycle_trades = []
    cycle_pl = 0.0
    shares = 0
    had_shares = False
    cycle_start = None
    pending_complete = False
    pending_end_date = None

    for trade in trades:
        # If wheel was just completed, check if this trade is same-date cleanup
        if pending_complete:
            if trade["date"] == pending_end_date and trade["net_value"] == 0.0:
                # Same-date zero-value cleanup (e.g. option removal) — keep in this cycle
                cycle_trades.append(trade)
                continue
            else:
                # Finalize the completed wheel
                cycles.append({
                    "status": "completed",
                    "start": cycle_start,
                    "end": pending_end_date,
                    "pl": cycle_pl,
                    "num_trades": len(cycle_trades),
                    "trades": cycle_trades,
                })
                cycle_trades = []
                cycle_pl = 0.0
                cycle_start = None
                had_shares = False
                pending_complete = False

        cycle_trades.append(trade)
        cycle_pl += trade["net_value"]

        if cycle_start is None:
            cycle_start = trade["date"]

        # Track share changes (same logic as main categorization)
        inst = trade["instrument_type"]
        txn_type = trade["type"]
        action = trade["action"]
        qty = trade["quantity"]
        net = trade["net_value"]

        if inst == "Equity":
            if txn_type == "Receive Deliver":
                if net < 0:
                    shares += qty
                elif net > 0:
                    shares -= qty
            else:
                if "Buy" in action:
                    shares += qty
                elif "Sell" in action:
                    shares -= qty

        if shares > 0:
            had_shares = True

        # Wheel complete: shares back to 0 after having held some
        # Don't finalize yet — wait to absorb same-date cleanup trades
        if shares == 0 and had_shares:
            pending_complete = True
            pending_end_date = trade["date"]

    # Finalize any pending completed wheel
    if pending_complete:
        cycles.append({
            "status": "completed",
            "start": cycle_start,
            "end": pending_end_date,
            "pl": cycle_pl,
            "num_trades": len(cycle_trades),
            "trades": cycle_trades,
        })
    elif cycle_trades:
        # Remaining trades = active wheel or CSP-only income
        cycles.append({
            "status": "active" if shares > 0 else "options_only",
            "start": cycle_start,
            "end": cycle_trades[-1]["date"],
            "pl": cycle_pl,
            "num_trades": len(cycle_trades),
            "trades": cycle_trades,
        })

    return cycles


def fetch_yearly_transfers():
    """Fetch net cash transfers (deposits minus withdrawals) per year and month.

    Returns:
        Dict of {year: {"total": net_amount, "months": {month_int: net_amount}}}.
    """
    session = _get_session()

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            txns = await acct.get_history(
                session,
                per_page=250,
                page_offset=None,
                sort="Asc",
            )

            yearly = {}
            for txn in txns:
                txn_type = txn.transaction_type or ""
                sub_type = txn.transaction_sub_type or ""
                if txn_type == "Money Movement" and sub_type in ("Deposit", "Withdrawal"):
                    net = float(txn.net_value) if txn.net_value is not None else 0.0
                    yr = txn.transaction_date.year
                    mo = txn.transaction_date.month
                    if yr not in yearly:
                        yearly[yr] = {"total": 0.0, "months": defaultdict(float)}
                    yearly[yr]["total"] += net
                    yearly[yr]["months"][mo] += net
            # Convert month defaultdicts to regular dicts
            for yr in yearly:
                yearly[yr]["months"] = dict(yearly[yr]["months"])
            return yearly

    return asyncio.run(_run())


def fetch_margin_interest():
    """Fetch margin interest charges from transaction history.

    Returns:
        Dict with 'current_month', 'ytd', 'total', and 'monthly' breakdown.
    """
    session = _get_session()

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            txns = await acct.get_history(
                session,
                per_page=250,
                page_offset=None,
                sort="Asc",
            )

            from datetime import date as _date
            today = _date.today()
            cur_year = today.year
            cur_month = today.month

            total = 0.0
            ytd = 0.0
            current_month = 0.0
            monthly = {}  # {(year, month): amount}

            for txn in txns:
                sub_type = txn.transaction_sub_type or ""
                if "Debit Interest" not in sub_type:
                    continue
                net = float(txn.net_value) if txn.net_value is not None else 0.0
                yr = txn.transaction_date.year
                mo = txn.transaction_date.month

                total += net
                key = (yr, mo)
                monthly[key] = monthly.get(key, 0.0) + net

                if yr == cur_year:
                    ytd += net
                    if mo == cur_month:
                        current_month += net

            return {
                "current_month": current_month,
                "ytd": ytd,
                "total": total,
                "monthly": monthly,
            }

    return asyncio.run(_run())


def fetch_account_balances():
    """Fetch account balances (net liq, cash, buying power) from Tastytrade."""
    session = _get_session()

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            balance = await acct.get_balances(session)
            return {
                "net_liquidating_value": float(balance.net_liquidating_value),
                "cash_balance": float(balance.cash_balance),
                "equity_buying_power": float(balance.equity_buying_power),
                "derivative_buying_power": float(balance.derivative_buying_power),
                "maintenance_requirement": float(balance.maintenance_requirement),
                "maintenance_excess": float(balance.maintenance_excess),
                "margin_equity": float(balance.margin_equity),
                "used_derivative_buying_power": float(balance.used_derivative_buying_power),
                "reg_t_margin_requirement": float(balance.reg_t_margin_requirement),
            }

    return asyncio.run(_run())


def fetch_net_liq_history(time_back="1y"):
    """Fetch net liquidating value history from Tastytrade.

    Args:
        time_back: One of '1d', '1m', '3m', '6m', '1y', 'all'.

    Returns:
        List of {"time": str, "close": float} dicts.
    """
    session = _get_session()

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            snapshots = await acct.get_net_liquidating_value_history(
                session, time_back=time_back
            )
            return [
                {"time": s.time, "close": float(s.close)}
                for s in snapshots
            ]

    return asyncio.run(_run())


def _fetch_yearly_returns(symbol):
    """Fetch yearly returns for a Yahoo Finance symbol.

    Returns:
        Dict of {year: return_pct}, e.g. {2023: 24.2, 2024: 10.5}.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        from datetime import datetime as _dt
        year_close = {}
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            yr = _dt.utcfromtimestamp(ts).year
            year_close[yr] = close

        years_sorted = sorted(year_close.keys())
        returns = {}
        for i in range(1, len(years_sorted)):
            prev_yr = years_sorted[i - 1]
            cur_yr = years_sorted[i]
            returns[cur_yr] = (year_close[cur_yr] - year_close[prev_yr]) / year_close[prev_yr] * 100
        return returns
    except Exception:
        return {}


def fetch_sp500_yearly_returns():
    """Fetch S&P 500 yearly returns."""
    return _fetch_yearly_returns("%5EGSPC")


BENCHMARKS = {
    "S&P 500": "%5EGSPC",
    "NASDAQ 100": "%5ENDX",
    "MSCI World": "URTH",
}


def fetch_benchmark_returns():
    """Fetch yearly returns for all benchmarks in parallel.

    Returns:
        Dict of {benchmark_name: {year: return_pct}}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(_fetch_yearly_returns, symbol): name
            for name, symbol in BENCHMARKS.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()
    return results


def fetch_ticker_profiles(tickers):
    """Fetch sector and country for each ticker using yfinance (parallelized)."""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(ticker):
        try:
            t = yf.Ticker(ticker)
            info = t.info
            return ticker, {
                "sector": info.get("sector") or "Unknown",
                "country": info.get("country") or "Unknown",
            }
        except Exception:
            return ticker, {"sector": "Unknown", "country": "Unknown"}

    profiles = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            ticker, profile = future.result()
            profiles[ticker] = profile
    return profiles


def fetch_current_prices(tickers):
    """Fetch current market prices from Yahoo Finance for a list of tickers."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    prices = {}
    for ticker in tickers:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = json.loads(resp.read())
                meta = data["chart"]["result"][0]["meta"]
                prices[ticker] = {
                    "price": meta["regularMarketPrice"],
                    "previousClose": meta.get("chartPreviousClose", meta.get("previousClose")),
                }
        except Exception:
            prices[ticker] = None
    return prices


def fetch_portfolio_greeks():
    """Fetch Greeks for all open option positions.

    Returns:
        Dict with 'positions' (list of per-option dicts) and 'totals'
        (aggregated portfolio delta/theta/gamma/vega in dollar terms).
    """
    session = _get_session()

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            positions = await acct.get_positions(session)

            option_positions = [
                p for p in positions
                if p.instrument_type.value == "Equity Option"
            ]

            _empty = {
                "positions": [],
                "totals": {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0},
            }

            if not option_positions:
                return _empty

            # Build OCC → streamer symbol mapping
            streamer_to_pos = {}
            for p in option_positions:
                ss = Option.occ_to_streamer_symbol(p.symbol)
                if ss:
                    streamer_to_pos[ss] = p

            streamer_symbols = list(streamer_to_pos.keys())
            if not streamer_symbols:
                return _empty

            greeks_map = {}
            _ctx = ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = ssl.CERT_NONE
            async with DXLinkStreamer(session, ssl_context=_ctx) as streamer:
                await streamer.subscribe(GreeksEvent, streamer_symbols)

                received = set()
                async for greek in streamer.listen(GreeksEvent):
                    sym = greek.event_symbol
                    if sym not in received and sym in streamer_to_pos:
                        greeks_map[sym] = greek
                        received.add(sym)
                        if len(received) >= len(streamer_symbols):
                            break

            # Aggregate — quantity is always positive, direction indicates sign
            totals = {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0}
            pos_details = []

            for ss, pos in streamer_to_pos.items():
                greek = greeks_map.get(ss)
                if not greek:
                    continue

                qty = float(pos.quantity)
                # Short positions: negate so sold options give positive theta
                if pos.quantity_direction == "Short":
                    qty = -qty
                mult = pos.multiplier  # typically 100
                underlying = pos.underlying_symbol

                d = float(greek.delta) * qty * mult
                t = float(greek.theta) * qty * mult
                g = float(greek.gamma) * qty * mult
                v = float(greek.vega) * qty * mult

                totals["delta"] += d
                totals["theta"] += t
                totals["gamma"] += g
                totals["vega"] += v

                pos_details.append({
                    "symbol": pos.symbol,
                    "underlying": underlying,
                    "quantity": float(pos.quantity),
                    "direction": pos.quantity_direction,
                    "delta": d,
                    "theta": t,
                    "gamma": g,
                    "vega": v,
                    "iv": float(greek.volatility) if greek.volatility else 0.0,
                })

            return {"positions": pos_details, "totals": totals}

    return asyncio.run(_run())
