"""
Tastytrade API module — fetch transactions and calculate per-ticker cost basis.
Used by the Portfolio page in streamlit_app.py.
"""

import asyncio
import json
import logging
import os
import ssl
import urllib.request
from collections import defaultdict
from decimal import Decimal

logger = logging.getLogger(__name__)

from error_logger import log_error
from dotenv import load_dotenv
from tastytrade import Session, Account, DXLinkStreamer
from tastytrade.dxfeed import Greeks as GreeksEvent, Quote as QuoteEvent
from tastytrade.instruments import Option, Equity, NestedOptionChain
from tastytrade.metrics import get_market_metrics
from tastytrade.order import NewOrder, OrderType, OrderTimeInForce, OrderAction


def _get_secret(key):
    """Get a secret from .env (local) or st.secrets (Streamlit Cloud)."""
    load_dotenv()
    val = os.environ.get(key)
    if val:
        return val.strip()
    try:
        import streamlit as st
        return str(st.secrets[key]).strip()
    except Exception:
        raise KeyError(key)


def _get_session(refresh_token=None):
    """Create a Tastytrade Session.

    When refresh_token is provided (multi-user mode), uses that token.
    Falls back to TASTYTRADE_REFRESH_TOKEN env var / secret for CLI usage.
    """
    if refresh_token is None:
        refresh_token = _get_secret("TASTYTRADE_REFRESH_TOKEN")
    return Session(
        provider_secret=_get_secret("TASTYTRADE_CLIENT_SECRET"),
        refresh_token=refresh_token,
    )


def fetch_portfolio_data(refresh_token=None):
    """
    Fetch all transactions from Tastytrade and compute cost basis.
    Returns (cost_basis_dict, account_number).
    """
    session = _get_session(refresh_token)

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


def fetch_yearly_transfers(refresh_token=None):
    """Fetch net cash transfers (deposits minus withdrawals) per year and month.

    Returns:
        Dict of {year: {"total": net_amount, "months": {month_int: net_amount}}}.
    """
    session = _get_session(refresh_token)

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


def fetch_margin_interest(refresh_token=None):
    """Fetch margin interest charges from transaction history.

    Returns:
        Dict with 'current_month', 'ytd', 'total', and 'monthly' breakdown.
    """
    session = _get_session(refresh_token)

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


def fetch_account_balances(refresh_token=None):
    """Fetch account balances (net liq, cash, buying power) from Tastytrade."""
    session = _get_session(refresh_token)

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


def fetch_margin_requirements(refresh_token=None):
    """Fetch per-position margin requirements from Tastytrade.

    Returns dict keyed by underlying symbol with margin details.
    """
    session = _get_session(refresh_token)

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            report = await acct.get_margin_requirements(session)
            result = {}
            for entry in report.groups:
                if isinstance(entry, dict):
                    continue  # skip EmptyDict entries
                sym = entry.underlying_symbol or entry.code
                if not sym:
                    continue
                result[sym] = {
                    "description": entry.description,
                    "margin_requirement": float(entry.margin_requirement),
                    "maintenance_requirement": float(entry.maintenance_requirement) if entry.maintenance_requirement else None,
                    "initial_requirement": float(entry.initial_requirement) if entry.initial_requirement else None,
                    "buying_power": float(entry.buying_power),
                    "margin_type": entry.margin_calculation_type,
                    "point_of_no_return_pct": float(entry.point_of_no_return_percent) if entry.point_of_no_return_percent else None,
                    "expected_down_pct": float(entry.expected_price_range_down_percent) if entry.expected_price_range_down_percent else None,
                }
            return result

    try:
        return asyncio.run(_run())
    except Exception as e:
        print(f"[Margin requirements] Error: {e}")
        log_error("TASTYTRADE_ERROR", f"fetch_margin_requirements: {e}", page="Portfolio")
        return {}


def fetch_margin_for_position(ticker, quantity, refresh_token=None):
    """Dry-run an order to get the real margin requirement from Tastytrade.

    Returns dict with margin fields, or None on error.
    """
    session = _get_session(refresh_token)

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            symbol = await Equity.get(session, ticker.upper())
            leg = symbol.build_leg(Decimal(str(abs(quantity))), OrderAction.BUY_TO_OPEN)
            order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.MARKET,
                legs=[leg],
            )
            resp = await acct.place_order(session, order, dry_run=True)
            bp = resp.buying_power_effect
            fees = resp.fee_calculation
            return {
                "change_in_margin": float(bp.change_in_margin_requirement),
                "change_in_buying_power": float(bp.change_in_buying_power),
                "current_buying_power": float(bp.current_buying_power),
                "new_buying_power": float(bp.new_buying_power),
                "isolated_margin": float(bp.isolated_order_margin_requirement),
                "total_fees": float(fees.total_fees) if fees else 0,
            }

    try:
        return asyncio.run(_run())
    except Exception as e:
        print(f"[Margin dry-run] Error for {ticker}: {e}")
        log_error("TASTYTRADE_ERROR", f"fetch_margin_for_position ({ticker}): {e}", page="Portfolio")
        return None


def fetch_net_liq_history(time_back="1y", refresh_token=None):
    """Fetch net liquidating value history from Tastytrade.

    Args:
        time_back: One of '1d', '1m', '3m', '6m', '1y', 'all'.
        refresh_token: Per-user refresh token (optional, falls back to env).

    Returns:
        List of {"time": str, "close": float} dicts.
    """
    session = _get_session(refresh_token)

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
    except Exception as e:
        logger.debug("Yearly returns fetch failed: %s", e)
        return {}


def _fetch_monthly_returns(symbol):
    """Fetch monthly returns for a Yahoo Finance symbol.

    Returns:
        Dict of {(year, month): return_pct}, e.g. {(2025, 1): 2.3, (2025, 2): -1.1}.
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
        month_close = {}
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            dt = _dt.utcfromtimestamp(ts)
            month_close[(dt.year, dt.month)] = close

        periods = sorted(month_close.keys())
        returns = {}
        for i in range(1, len(periods)):
            prev = periods[i - 1]
            cur = periods[i]
            prev_close = month_close[prev]
            if prev_close > 0:
                returns[cur] = round((month_close[cur] - prev_close) / prev_close * 100, 1)
        return returns
    except Exception as e:
        logger.debug("Monthly returns fetch failed for %s: %s", symbol, e)
        return {}


MONTHLY_BENCHMARKS = {
    "S&P 500": "%5EGSPC",
    "Nasdaq": "%5ENDX",
}


def fetch_benchmark_monthly_returns():
    """Fetch monthly returns for benchmarks.

    Returns:
        Dict of {benchmark_name: {(year, month): return_pct}}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_fetch_monthly_returns, symbol): name
            for name, symbol in MONTHLY_BENCHMARKS.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()
    return results


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
    """Fetch sector and country for each ticker via Yahoo Finance search API (no auth needed)."""
    import urllib.parse
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ua = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"

    EXCHANGE_COUNTRY = {
        "NMS": "United States", "NYQ": "United States", "NGM": "United States",
        "NCM": "United States", "ASE": "United States", "BTS": "United States",
        "PCX": "United States", "OTC": "United States", "NAS": "United States",
        "TOR": "Canada", "VAN": "Canada", "CNQ": "Canada",
        "LSE": "United Kingdom", "IOB": "United Kingdom",
        "AMS": "Netherlands", "PAR": "France", "GER": "Germany",
        "FRA": "Germany", "MIL": "Italy", "MCE": "Spain",
        "TAE": "Israel", "JPX": "Japan", "HKG": "Hong Kong",
        "KSC": "South Korea", "TWO": "Taiwan", "TAI": "Taiwan",
        "SHH": "China", "SHZ": "China", "ASX": "Australia",
    }

    def _fetch_one(ticker):
        try:
            url = (
                f"https://query1.finance.yahoo.com/v1/finance/search"
                f"?q={urllib.parse.quote(ticker)}&quotesCount=1&newsCount=0"
            )
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                data = json.loads(resp.read())
            quotes = data.get("quotes", [])
            if not quotes:
                return ticker, {"sector": "Unknown", "country": "Unknown"}
            q = quotes[0]
            qt = q.get("quoteType", "")
            sector = q.get("sector") or ("Cash & Equivalents" if qt in ("MONEYMARKET", "MUTUALFUND") else "Unknown")
            country = EXCHANGE_COUNTRY.get(q.get("exchange", ""), "Unknown")
            return ticker, {"sector": sector, "country": country}
        except Exception as e:
            logger.debug("Profile fetch failed for %s: %s", ticker, e)
            return ticker, {"sector": "Unknown", "country": "Unknown"}

    profiles = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
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

    def _fetch_one(ticker):
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = json.loads(resp.read())
                meta = data["chart"]["result"][0]["meta"]
                return ticker, {
                    "price": meta["regularMarketPrice"],
                    "previousClose": meta.get("chartPreviousClose", meta.get("previousClose")),
                }
        except Exception as e:
            logger.debug("Price fetch failed for %s: %s", ticker, e)
            return ticker, None

    async def _fetch_all():
        loop = asyncio.get_event_loop()
        tasks = [loop.run_in_executor(None, _fetch_one, t) for t in tickers]
        return await asyncio.gather(*tasks)

    prices = {}
    try:
        results = asyncio.run(_fetch_all())
        for ticker, result in results:
            prices[ticker] = result
    except Exception as e:
        logger.debug("Async price fetch failed, falling back to sequential: %s", e)
        for ticker in tickers:
            _, result = _fetch_one(ticker)
            prices[ticker] = result
    return prices


def fetch_earnings_dates(tickers, refresh_token=None):
    """Fetch next earnings dates for a list of tickers via Tastytrade market metrics.

    Returns dict: {ticker: {"date": date|None, "time": str|None, "estimated": bool} | None}
    """
    async def _fetch():
        session = _get_session(refresh_token)
        metrics = await get_market_metrics(session, list(tickers))
        result = {}
        for m in metrics:
            e = m.earnings
            if e and e.expected_report_date:
                result[m.symbol] = {
                    "date": e.expected_report_date,
                    "time": e.time_of_day,
                    "estimated": e.estimated,
                }
            else:
                result[m.symbol] = None
        return result

    try:
        return asyncio.run(_fetch())
    except Exception as e:
        logger.warning("Earnings dates fetch failed: %s", e)
        log_error("TASTYTRADE_ERROR", f"fetch_earnings_dates: {e}", page="Portfolio")
        return {t: None for t in tickers}


def fetch_portfolio_greeks(refresh_token=None):
    """Fetch Greeks for all open option positions.

    Returns:
        Dict with 'positions' (list of per-option dicts) and 'totals'
        (aggregated portfolio delta/theta/gamma/vega in dollar terms).
    """
    session = _get_session(refresh_token)

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
            async def _stream_greeks(streamer):
                await streamer.subscribe(GreeksEvent, streamer_symbols)
                received = set()
                async for greek in streamer.listen(GreeksEvent):
                    sym = greek.event_symbol
                    if sym not in received and sym in streamer_to_pos:
                        greeks_map[sym] = greek
                        received.add(sym)
                        if len(received) >= len(streamer_symbols):
                            break

            async with DXLinkStreamer(session, ssl_context=_ctx) as streamer:
                await asyncio.wait_for(_stream_greeks(streamer), timeout=12)

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


def fetch_greeks_and_bwd(refresh_token=None):
    """Fetch Portfolio Greeks and Beta-Weighted Delta in a single session.

    Uses one DXLink streamer to avoid concurrent websocket conflicts.
    Returns (greeks_dict, bwd_dict).
    """
    session = _get_session(refresh_token)

    _empty_greeks = {
        "positions": [],
        "totals": {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0},
    }
    _empty_bwd = {"positions": [], "portfolio_bwd": 0, "spy_price": 0,
                  "dollar_per_1pct": 0}

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            positions = await acct.get_positions(session, include_marks=True)

            if not positions:
                return _empty_greeks, _empty_bwd

            # Categorize positions
            stock_positions = []
            option_positions = []
            underlyings = set()

            for p in positions:
                itype = p.instrument_type.value
                sym = getattr(p, 'underlying_symbol', None) or p.symbol
                underlyings.add(sym)
                if itype == "Equity":
                    stock_positions.append(p)
                elif itype == "Equity Option":
                    option_positions.append(p)

            underlyings.add("SPY")
            underlying_list = sorted(underlyings)

            # Fetch betas for BWD
            metrics = await get_market_metrics(session, underlying_list)
            beta_map = {}
            for m in metrics:
                if m.beta is not None:
                    beta_map[m.symbol] = float(m.beta)

            # Build streamer symbol mapping for options
            streamer_to_pos = {}
            for p in option_positions:
                ss = Option.occ_to_streamer_symbol(p.symbol)
                if ss:
                    streamer_to_pos[ss] = p

            # Pre-populate price_map with mark prices (fallback for off-hours)
            price_map = {}
            for p in positions:
                sym = getattr(p, 'underlying_symbol', None) or p.symbol
                mp = float(p.mark_price) if p.mark_price else 0
                if mp > 0:
                    price_map[sym] = mp

            # Stream live quotes + greeks via single DXLink connection
            _ctx = ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = ssl.CERT_NONE

            greeks_map = {}

            async def _collect_quotes(streamer):
                await streamer.subscribe(QuoteEvent, underlying_list)
                received = set()
                async for quote in streamer.listen(QuoteEvent):
                    sym = quote.event_symbol
                    if sym not in received:
                        bid = float(quote.bid_price) if quote.bid_price else 0
                        ask = float(quote.ask_price) if quote.ask_price else 0
                        mid = (bid + ask) / 2 if bid and ask else bid or ask
                        if mid > 0:
                            price_map[sym] = mid
                        received.add(sym)
                        if len(received) >= len(underlying_list):
                            break

            async def _collect_greeks(streamer):
                if not streamer_to_pos:
                    return
                await streamer.subscribe(GreeksEvent, list(streamer_to_pos.keys()))
                received = set()
                async for greek in streamer.listen(GreeksEvent):
                    sym = greek.event_symbol
                    if sym not in received and sym in streamer_to_pos:
                        greeks_map[sym] = greek
                        received.add(sym)
                        if len(received) >= len(streamer_to_pos):
                            break

            async with DXLinkStreamer(session, ssl_context=_ctx) as streamer:
                try:
                    await asyncio.wait_for(_collect_quotes(streamer), timeout=10)
                except asyncio.TimeoutError:
                    pass
                if streamer_to_pos:
                    try:
                        await asyncio.wait_for(_collect_greeks(streamer), timeout=12)
                    except asyncio.TimeoutError:
                        pass

            # Fallback: fetch SPY price from Yahoo Finance if still missing
            if price_map.get("SPY", 0) <= 0:
                try:
                    _yf_req = urllib.request.Request(
                        "https://query1.finance.yahoo.com/v8/finance/chart/SPY"
                        "?range=1d&interval=1d",
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    _yf_ctx = ssl.create_default_context()
                    _yf_ctx.check_hostname = False
                    _yf_ctx.verify_mode = ssl.CERT_NONE
                    with urllib.request.urlopen(_yf_req, context=_yf_ctx,
                                               timeout=5) as _yf_resp:
                        _yf = json.loads(_yf_resp.read())
                        _sp = _yf["chart"]["result"][0]["meta"]["regularMarketPrice"]
                        if _sp and float(_sp) > 0:
                            price_map["SPY"] = float(_sp)
                except Exception as e:
                    logger.debug("SPY price fallback failed: %s", e)

            # --- Build Greeks result ---
            totals = {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0}
            pos_details = []
            for ss, pos in streamer_to_pos.items():
                greek = greeks_map.get(ss)
                if not greek:
                    continue
                qty = float(pos.quantity)
                if pos.quantity_direction == "Short":
                    qty = -qty
                mult = pos.multiplier
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
                    "delta": d, "theta": t, "gamma": g, "vega": v,
                    "iv": float(greek.volatility) if greek.volatility else 0.0,
                })
            greeks_result = {"positions": pos_details, "totals": totals}

            # --- Build BWD result ---
            spy_price = price_map.get("SPY", 0)
            bwd_result = _empty_bwd

            if spy_price > 0:
                ticker_bwd = defaultdict(
                    lambda: {"raw_delta": 0.0, "beta": 0.0, "price": 0.0, "bwd": 0.0})

                for p in stock_positions:
                    sym = p.symbol
                    qty = float(p.quantity)
                    if p.quantity_direction == "Short":
                        qty = -qty
                    beta = beta_map.get(sym, 1.0)
                    price = price_map.get(sym, float(p.mark_price or 0))
                    bwd_val = qty * beta * (price / spy_price)
                    entry = ticker_bwd[sym]
                    entry["raw_delta"] += qty
                    entry["beta"] = beta
                    entry["price"] = price
                    entry["bwd"] += bwd_val

                for ss, pos in streamer_to_pos.items():
                    greek = greeks_map.get(ss)
                    if not greek:
                        continue
                    qty = float(pos.quantity)
                    if pos.quantity_direction == "Short":
                        qty = -qty
                    mult = pos.multiplier
                    underlying = pos.underlying_symbol
                    raw_delta = float(greek.delta) * qty * mult
                    beta = beta_map.get(underlying, 1.0)
                    price = price_map.get(underlying, 0)
                    bwd_val = raw_delta * beta * (price / spy_price)
                    entry = ticker_bwd[underlying]
                    entry["raw_delta"] += raw_delta
                    entry["beta"] = beta
                    entry["price"] = price
                    entry["bwd"] += bwd_val

                portfolio_bwd = sum(e["bwd"] for e in ticker_bwd.values())
                dollar_per_1pct = portfolio_bwd * spy_price * 0.01
                pos_list = []
                for sym, entry in sorted(ticker_bwd.items(),
                                         key=lambda x: abs(x[1]["bwd"]),
                                         reverse=True):
                    pos_list.append({
                        "ticker": sym, "raw_delta": entry["raw_delta"],
                        "beta": entry["beta"], "price": entry["price"],
                        "bwd": entry["bwd"],
                        "dollar_per_1pct": entry["bwd"] * spy_price * 0.01,
                    })
                bwd_result = {
                    "positions": pos_list,
                    "portfolio_bwd": portfolio_bwd,
                    "spy_price": spy_price,
                    "dollar_per_1pct": dollar_per_1pct,
                }

            return greeks_result, bwd_result

    try:
        return asyncio.run(_run())
    except Exception as e:
        print(f"[Greeks+BWD] Error: {e}")
        log_error("TASTYTRADE_ERROR", f"fetch_greeks_and_bwd: {e}", page="Portfolio")
        return _empty_greeks, _empty_bwd


def fetch_option_chain(ticker, option_type='Put', min_dte=7, max_dte=60, num_strikes=8, fallback_price=0.0, refresh_token=None):
    """Fetch option chain data for a ticker with greeks via DXLink.

    Args:
        ticker: Stock symbol (e.g. 'AAPL')
        option_type: 'Put' or 'Call'
        min_dte: Minimum days to expiration
        max_dte: Maximum days to expiration
        num_strikes: Number of strikes closest to ATM per expiration
        fallback_price: Price to use when streamer returns nothing (e.g. market closed)
        refresh_token: Per-user refresh token (optional, falls back to env).

    Returns dict with underlying_price and list of expirations with strike data.
    """
    _empty = {'underlying_price': 0, 'expirations': []}
    session = _get_session(refresh_token)

    async def _run():
        async with session:
            # Get nested chain structure
            try:
                chains = await NestedOptionChain.get(session, ticker)
            except Exception as e:
                logger.warning("Option chain fetch failed for %s: %s", ticker, e)
                return _empty
            if not chains:
                return _empty
            chain = chains[0] if isinstance(chains, list) else chains

            # Filter expirations by DTE range
            valid_exps = []
            for exp in chain.expirations:
                dte = exp.days_to_expiration
                if min_dte <= dte <= max_dte:
                    valid_exps.append(exp)

            if not valid_exps:
                return _empty

            # Sort: monthlies first (Regular), then by date
            valid_exps.sort(key=lambda e: (
                0 if e.expiration_type == 'Regular' else 1,
                e.expiration_date,
            ))

            # Stream underlying quote to get current price
            _ctx = ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = ssl.CERT_NONE

            underlying_price = 0.0

            # Collect streamer symbols for selected strikes (will be filled after we get price)
            # First pass: get underlying price
            async with DXLinkStreamer(session, ssl_context=_ctx) as streamer:
                # Get underlying price
                await streamer.subscribe(QuoteEvent, [ticker])
                try:
                    async def _get_underlying():
                        async for quote in streamer.listen(QuoteEvent):
                            if quote.event_symbol == ticker:
                                bid = float(quote.bid_price) if quote.bid_price else 0
                                ask = float(quote.ask_price) if quote.ask_price else 0
                                return (bid + ask) / 2 if bid and ask else bid or ask
                    underlying_price = await asyncio.wait_for(_get_underlying(), timeout=8)
                except asyncio.TimeoutError:
                    pass

            # Fallback: Yahoo Finance last close price
            if underlying_price <= 0:
                try:
                    _yf_req = urllib.request.Request(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                        "?range=1d&interval=1d",
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    _yf_ctx = ssl.create_default_context()
                    _yf_ctx.check_hostname = False
                    _yf_ctx.verify_mode = ssl.CERT_NONE
                    with urllib.request.urlopen(_yf_req, context=_yf_ctx, timeout=5) as _yf_resp:
                        _yf = json.loads(_yf_resp.read())
                        _sp = _yf["chart"]["result"][0]["meta"]["regularMarketPrice"]
                        if _sp and float(_sp) > 0:
                            underlying_price = float(_sp)
                except Exception as e:
                    logger.debug("YF price fallback failed for %s: %s", ticker, e)

            # Last resort: use caller-provided fallback
            if underlying_price <= 0:
                underlying_price = fallback_price

            if underlying_price <= 0:
                return _empty

            # Select strikes closest to ATM and collect streamer symbols
            symbol_map = {}  # streamer_symbol -> (exp_idx, strike_price)
            result_exps = []
            total_symbols = 0
            max_symbols = 80 if option_type == 'Call' else 50

            for exp in valid_exps:
                if total_symbols >= max_symbols:
                    break

                dte = exp.days_to_expiration
                exp_date = str(exp.expiration_date)
                exp_type = exp.expiration_type

                # For calls: bias towards OTM (higher strikes); for puts: closest to ATM
                if option_type == 'Call':
                    # Take a few ITM + mostly OTM strikes
                    atm_and_otm = [s for s in exp.strikes if float(s.strike_price) >= underlying_price * 0.97]
                    itm = [s for s in exp.strikes if float(s.strike_price) < underlying_price * 0.97]
                    atm_and_otm.sort(key=lambda s: float(s.strike_price))
                    itm.sort(key=lambda s: float(s.strike_price), reverse=True)
                    selected = atm_and_otm[:num_strikes] + itm[:max(2, num_strikes // 5)]
                else:
                    sorted_strikes = sorted(
                        exp.strikes,
                        key=lambda s: abs(float(s.strike_price) - underlying_price),
                    )
                    selected = sorted_strikes[:num_strikes]
                remaining = max_symbols - total_symbols
                if len(selected) > remaining:
                    selected = selected[:remaining]

                exp_entry = {
                    'expiration_date': exp_date,
                    'dte': dte,
                    'expiration_type': exp_type,
                    'strikes': [],
                }

                for s in sorted(selected, key=lambda s: float(s.strike_price)):
                    strike_price = float(s.strike_price)
                    if option_type == 'Put':
                        ss = s.put_streamer_symbol
                    else:
                        ss = s.call_streamer_symbol
                    if ss:
                        exp_idx = len(result_exps)
                        symbol_map[ss] = (exp_idx, strike_price)
                        exp_entry['strikes'].append({
                            'strike': strike_price,
                            'streamer_symbol': ss,
                            'bid': 0.0, 'ask': 0.0, 'mid': 0.0,
                            'delta': 0.0, 'theta': 0.0, 'gamma': 0.0,
                            'vega': 0.0, 'iv': 0.0,
                        })
                        total_symbols += 1

                if exp_entry['strikes']:
                    result_exps.append(exp_entry)

            if not symbol_map:
                return {'underlying_price': underlying_price, 'expirations': []}

            # Build quick lookup: streamer_symbol -> (exp_idx, strike_idx)
            strike_lookup = {}
            for exp_idx, exp_entry in enumerate(result_exps):
                for strike_idx, strike_data in enumerate(exp_entry['strikes']):
                    strike_lookup[strike_data['streamer_symbol']] = (exp_idx, strike_idx)

            all_symbols = list(symbol_map.keys())

            # Second pass: stream greeks + quotes for options
            quotes_received = {}
            greeks_received = {}

            async with DXLinkStreamer(session, ssl_context=_ctx) as streamer:
                async def _collect_option_quotes():
                    await streamer.subscribe(QuoteEvent, all_symbols)
                    received = set()
                    async for quote in streamer.listen(QuoteEvent):
                        sym = quote.event_symbol
                        if sym in symbol_map and sym not in received:
                            quotes_received[sym] = quote
                            received.add(sym)
                            if len(received) >= len(all_symbols):
                                break

                async def _collect_option_greeks():
                    await streamer.subscribe(GreeksEvent, all_symbols)
                    received = set()
                    async for greek in streamer.listen(GreeksEvent):
                        sym = greek.event_symbol
                        if sym in symbol_map and sym not in received:
                            greeks_received[sym] = greek
                            received.add(sym)
                            if len(received) >= len(all_symbols):
                                break

                try:
                    await asyncio.wait_for(_collect_option_quotes(), timeout=10)
                except asyncio.TimeoutError:
                    pass
                try:
                    await asyncio.wait_for(_collect_option_greeks(), timeout=10)
                except asyncio.TimeoutError:
                    pass

            # Populate results from streamed data
            for ss, (exp_idx, strike_idx) in strike_lookup.items():
                entry = result_exps[exp_idx]['strikes'][strike_idx]
                quote = quotes_received.get(ss)
                if quote:
                    bid = float(quote.bid_price) if quote.bid_price else 0.0
                    ask = float(quote.ask_price) if quote.ask_price else 0.0
                    entry['bid'] = bid
                    entry['ask'] = ask
                    entry['mid'] = (bid + ask) / 2 if bid and ask else bid or ask
                greek = greeks_received.get(ss)
                if greek:
                    entry['delta'] = float(greek.delta) if greek.delta else 0.0
                    entry['theta'] = float(greek.theta) if greek.theta else 0.0
                    entry['gamma'] = float(greek.gamma) if greek.gamma else 0.0
                    entry['vega'] = float(greek.vega) if greek.vega else 0.0
                    entry['iv'] = float(greek.volatility) if greek.volatility else 0.0
                    # Use greeks price as fallback for mid when quotes are missing
                    if entry['mid'] <= 0 and greek.price:
                        _gp = float(greek.price)
                        if _gp > 0:
                            entry['mid'] = _gp
                            entry['bid'] = _gp
                            entry['ask'] = _gp

            # Remove streamer_symbol from output; keep strikes that have any price data
            for exp_entry in result_exps:
                exp_entry['strikes'] = [
                    {k: v for k, v in s.items() if k != 'streamer_symbol'}
                    for s in exp_entry['strikes']
                    if s['bid'] > 0 or s['mid'] > 0
                ]

            # Remove expirations with no valid strikes
            result_exps = [e for e in result_exps if e['strikes']]

            return {'underlying_price': underlying_price, 'expirations': result_exps}

    try:
        return asyncio.run(_run())
    except Exception as e:
        print(f"[OptionChain] Error: {e}")
        log_error("TASTYTRADE_ERROR", f"fetch_option_chain ({ticker}): {e}", page="Watchlist")
        return _empty


def fetch_beta_weighted_delta(refresh_token=None):
    """Calculate portfolio Beta-Weighted Delta relative to SPY.

    Converts all positions (stocks + options) to SPY-equivalent delta.
    Formula per position: position_delta * beta * (underlying_price / spy_price)

    Returns dict with per-ticker breakdown and portfolio totals.
    """
    session = _get_session(refresh_token)

    async def _run():
        async with session:
            accounts = await Account.get(session)
            acct = accounts[0]
            positions = await acct.get_positions(session, include_marks=True)

            if not positions:
                return {"positions": [], "portfolio_bwd": 0, "spy_price": 0,
                        "dollar_per_1pct": 0}

            # Separate stock and option positions
            stock_positions = []
            option_positions = []
            underlyings = set()

            for p in positions:
                itype = p.instrument_type.value
                sym = getattr(p, 'underlying_symbol', None) or p.symbol
                underlyings.add(sym)
                if itype == "Equity":
                    stock_positions.append(p)
                elif itype == "Equity Option":
                    option_positions.append(p)

            underlyings.add("SPY")
            underlying_list = sorted(underlyings)

            # Fetch betas via market metrics
            metrics = await get_market_metrics(session, underlying_list)
            beta_map = {}
            for m in metrics:
                if m.beta is not None:
                    beta_map[m.symbol] = float(m.beta)

            # Pre-populate price_map with mark prices (fallback for off-hours)
            price_map = {}
            for p in positions:
                sym = getattr(p, 'underlying_symbol', None) or p.symbol
                mp = float(p.mark_price) if p.mark_price else 0
                if mp > 0:
                    price_map[sym] = mp

            # Fetch SPY price + underlying prices via DXLink quotes
            _ctx = ssl.create_default_context()
            _ctx.check_hostname = False
            _ctx.verify_mode = ssl.CERT_NONE

            greeks_map = {}
            streamer_to_pos = {}

            async def _collect_quotes(streamer):
                await streamer.subscribe(QuoteEvent, underlying_list)
                received = set()
                async for quote in streamer.listen(QuoteEvent):
                    sym = quote.event_symbol
                    if sym not in received:
                        bid = float(quote.bid_price) if quote.bid_price else 0
                        ask = float(quote.ask_price) if quote.ask_price else 0
                        mid = (bid + ask) / 2 if bid and ask else bid or ask
                        if mid > 0:
                            price_map[sym] = mid
                        received.add(sym)
                        if len(received) >= len(underlying_list):
                            break

            async def _collect_greeks(streamer):
                for p in option_positions:
                    ss = Option.occ_to_streamer_symbol(p.symbol)
                    if ss:
                        streamer_to_pos[ss] = p
                if not streamer_to_pos:
                    return
                await streamer.subscribe(GreeksEvent, list(streamer_to_pos.keys()))
                received_g = set()
                async for greek in streamer.listen(GreeksEvent):
                    sym = greek.event_symbol
                    if sym not in received_g and sym in streamer_to_pos:
                        greeks_map[sym] = greek
                        received_g.add(sym)
                        if len(received_g) >= len(streamer_to_pos):
                            break

            async with DXLinkStreamer(session, ssl_context=_ctx) as streamer:
                try:
                    await asyncio.wait_for(_collect_quotes(streamer), timeout=10)
                except asyncio.TimeoutError:
                    pass  # use whatever quotes we got
                if option_positions and price_map.get("SPY", 0) > 0:
                    try:
                        await asyncio.wait_for(_collect_greeks(streamer), timeout=10)
                    except asyncio.TimeoutError:
                        pass  # use whatever greeks we got

            # Fallback: fetch SPY price from Yahoo Finance if still missing
            if price_map.get("SPY", 0) <= 0:
                try:
                    _yf_req = urllib.request.Request(
                        "https://query1.finance.yahoo.com/v8/finance/chart/SPY"
                        "?range=1d&interval=1d",
                        headers={"User-Agent": "Mozilla/5.0"},
                    )
                    _yf_ctx = ssl.create_default_context()
                    _yf_ctx.check_hostname = False
                    _yf_ctx.verify_mode = ssl.CERT_NONE
                    with urllib.request.urlopen(_yf_req, context=_yf_ctx,
                                               timeout=5) as _yf_resp:
                        _yf = json.loads(_yf_resp.read())
                        _sp = _yf["chart"]["result"][0]["meta"]["regularMarketPrice"]
                        if _sp and float(_sp) > 0:
                            price_map["SPY"] = float(_sp)
                except Exception as e:
                    logger.debug("SPY price fallback failed (BWD): %s", e)

            spy_price = price_map.get("SPY", 0)
            if spy_price <= 0:
                return {"positions": [], "portfolio_bwd": 0, "spy_price": 0,
                        "dollar_per_1pct": 0}

            ticker_bwd = defaultdict(lambda: {"raw_delta": 0.0, "beta": 0.0,
                                              "price": 0.0, "bwd": 0.0})

            for p in stock_positions:
                sym = p.symbol
                qty = float(p.quantity)
                if p.quantity_direction == "Short":
                    qty = -qty
                beta = beta_map.get(sym, 1.0)
                price = price_map.get(sym, float(p.mark_price or 0))
                bwd = qty * beta * (price / spy_price)
                entry = ticker_bwd[sym]
                entry["raw_delta"] += qty
                entry["beta"] = beta
                entry["price"] = price
                entry["bwd"] += bwd

            for ss, pos in streamer_to_pos.items():
                greek = greeks_map.get(ss)
                if not greek:
                    continue
                qty = float(pos.quantity)
                if pos.quantity_direction == "Short":
                    qty = -qty
                mult = pos.multiplier
                underlying = pos.underlying_symbol
                raw_delta = float(greek.delta) * qty * mult
                beta = beta_map.get(underlying, 1.0)
                price = price_map.get(underlying, 0)
                bwd = raw_delta * beta * (price / spy_price)
                entry = ticker_bwd[underlying]
                entry["raw_delta"] += raw_delta
                entry["beta"] = beta
                entry["price"] = price
                entry["bwd"] += bwd

            portfolio_bwd = sum(e["bwd"] for e in ticker_bwd.values())
            dollar_per_1pct = portfolio_bwd * spy_price * 0.01

            pos_list = []
            for sym, entry in sorted(ticker_bwd.items(),
                                     key=lambda x: abs(x[1]["bwd"]),
                                     reverse=True):
                pos_list.append({
                    "ticker": sym,
                    "raw_delta": entry["raw_delta"],
                    "beta": entry["beta"],
                    "price": entry["price"],
                    "bwd": entry["bwd"],
                    "dollar_per_1pct": entry["bwd"] * spy_price * 0.01,
                })

            return {
                "positions": pos_list,
                "portfolio_bwd": portfolio_bwd,
                "spy_price": spy_price,
                "dollar_per_1pct": dollar_per_1pct,
            }

    try:
        return asyncio.run(_run())
    except Exception as e:
        print(f"[BWD] Error: {e}")
        log_error("TASTYTRADE_ERROR", f"fetch_beta_weighted_delta: {e}", page="Portfolio")
        return {"positions": [], "portfolio_bwd": 0, "spy_price": 0,
                "dollar_per_1pct": 0}
