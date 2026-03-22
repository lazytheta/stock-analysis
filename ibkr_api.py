"""
IBKR API module — fetch portfolio data from Interactive Brokers via Flex Queries.
Returns the same data structures as tastytrade_api.py for adapter compatibility.

Uses the ibflex library to download and parse Flex Query reports.
Users only need a Flex Web Service token and a Query ID (set up in IBKR portal).
"""

import json
import logging
import ssl
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

import streamlit as st

logger = logging.getLogger(__name__)

from error_logger import log_error
from trade_utils import detect_wheels as _detect_wheels

# How long to cache the Flex statement (seconds)
_CACHE_TTL = 300  # 5 minutes


def _ssl_context():
    """Create an SSL context, using certifi if available."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx


def _patch_ibflex_parser():
    """Patch ibflex parser to skip unknown XML attributes instead of crashing.

    IBKR sometimes sends fields (e.g. lastTradedDate) that aren't in ibflex's
    dataclass definitions. The stock parser raises FlexParserError; we patch
    parse_data_element to silently skip unknown attributes.
    """
    from ibflex import parser, Types

    if getattr(parser.parse_data_element, '_patched', False):
        return  # Already patched

    _orig_parse_data_element = parser.parse_data_element

    def _lenient_parse_data_element(elem):
        Class = getattr(Types, elem.tag)
        known = set(Class.__annotations__)
        # Remove unknown attributes before the original parser sees them
        unknown = [k for k in elem.attrib if k not in known]
        for k in unknown:
            del elem.attrib[k]
        try:
            return _orig_parse_data_element(elem)
        except (ValueError, TypeError):
            # Some fields (e.g. Trade.notes='FP;P') can't be converted to
            # the expected type (tuple). Remove problematic fields and retry.
            import dataclasses, typing
            for field in dataclasses.fields(Class):
                if field.name in elem.attrib:
                    origin = getattr(typing, 'get_origin', lambda x: None)(field.type)
                    if origin is tuple or field.type is tuple:
                        del elem.attrib[field.name]
            return _orig_parse_data_element(elem)

    _lenient_parse_data_element._patched = True
    parser.parse_data_element = _lenient_parse_data_element


try:
    _patch_ibflex_parser()
except ImportError:
    pass  # ibflex not installed; IBKR features unavailable


def _get_flex_statement():
    """Download and cache the Flex Query statement."""
    cached = st.session_state.get("_ibkr_flex_cache")
    if cached:
        ts, stmt = cached
        if (datetime.now() - ts).total_seconds() < _CACHE_TTL:
            return stmt

    from ibflex import client, parser

    creds = st.session_state.get("ibkr_credentials")
    if not creds:
        raise RuntimeError("IBKR credentials not configured")

    token = creds["ibkr_flex_token"]
    query_id = creds["ibkr_flex_query_id"]

    raw = client.download(token, query_id)
    response = parser.parse(raw)

    if not response.FlexStatements:
        raise RuntimeError("Flex Query returned no statements")

    stmt = response.FlexStatements[0]
    st.session_state["_ibkr_flex_cache"] = (datetime.now(), stmt)
    return stmt


def _dec(val, default=0.0):
    """Convert Decimal/None to float."""
    if val is None:
        return default
    return float(val)


def _normalize_flex_trade(trade):
    """Convert an ibflex Trade object to the standard trade record format."""
    from ibflex.enums import AssetClass, BuySell, PutCall

    qty = abs(_dec(trade.quantity))
    price = _dec(trade.tradePrice)
    net_value = _dec(trade.netCash)
    asset_class = trade.assetCategory
    side = trade.buySell
    description = trade.description or ""

    if asset_class == AssetClass.OPTION:
        if side in (BuySell.BUY,):
            label = "BTC" if net_value < 0 else "Buy Option"
        else:
            label = "CSP" if trade.putCall == PutCall.PUT else "CC"
    elif asset_class == AssetClass.STOCK:
        label = "Stock Buy" if side in (BuySell.BUY,) else "Stock Sell"
    else:
        label = str(side) if side else "Other"

    trade_date = trade.tradeDate or trade.reportDate or date.today()

    return {
        "date": trade_date,
        "label": label,
        "type": str(trade.transactionType) if trade.transactionType else "",
        "sub_type": "",
        "description": description,
        "symbol": trade.symbol or "",
        "action": str(side) if side else "",
        "quantity": qty,
        "price": price,
        "net_value": net_value,
        "instrument_type": "Option" if asset_class == AssetClass.OPTION else "Equity",
    }


# ── Public API (same signatures as tastytrade_api) ──

def fetch_account_balances():
    """Fetch IBKR account balances from Flex Query data."""
    try:
        stmt = _get_flex_statement()

        nav = stmt.ChangeInNAV
        net_liq = _dec(nav.endingValue) if nav else 0

        # Get cash and equity summary from EquitySummaryInBase
        cash = 0
        equity = 0
        if hasattr(stmt, "EquitySummaryInBase") and stmt.EquitySummaryInBase:
            summary = stmt.EquitySummaryInBase[-1]  # most recent date
            cash = _dec(summary.cash)
            equity = _dec(summary.total)

        # Cash report fallback
        if not cash and stmt.CashReport:
            for cr in stmt.CashReport:
                if hasattr(cr, "endingCash"):
                    cash = _dec(cr.endingCash)
                    break

        return {
            "net_liquidating_value": net_liq or equity,
            "cash_balance": cash,
            "equity_buying_power": cash,
            "derivative_buying_power": cash,
            "maintenance_requirement": 0.0,
            "maintenance_excess": 0.0,
            "margin_equity": equity,
            "used_derivative_buying_power": 0.0,
            "reg_t_margin_requirement": 0.0,
        }
    except Exception as e:
        logger.error("IBKR fetch_account_balances failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_account_balances: {e}", page="Portfolio")
        return {
            "net_liquidating_value": 0, "cash_balance": 0,
            "equity_buying_power": 0, "derivative_buying_power": 0,
            "maintenance_requirement": 0, "maintenance_excess": 0,
            "margin_equity": 0, "used_derivative_buying_power": 0,
            "reg_t_margin_requirement": 0,
        }


def fetch_portfolio_data():
    """Fetch IBKR positions and trades from Flex Query, compute cost basis."""
    try:
        stmt = _get_flex_statement()
        from ibflex.enums import AssetClass

        # Build position map from OpenPositions
        position_map = {}
        for pos in (stmt.OpenPositions or []):
            ticker = (pos.symbol or "UNKNOWN").split(" ")[0].upper()
            if ticker not in position_map:
                position_map[ticker] = {"shares": 0}
            if pos.assetCategory in (AssetClass.STOCK, None):
                position_map[ticker]["shares"] += int(_dec(pos.position))

        # Build trades from Trades section
        trades_by_ticker = defaultdict(list)
        for trade in (stmt.Trades or []):
            rec = _normalize_flex_trade(trade)
            ticker = rec["symbol"].split(" ")[0].upper() if rec["symbol"] else "UNKNOWN"
            trades_by_ticker[ticker].append(rec)

        all_tickers = set(position_map.keys()) | set(trades_by_ticker.keys())
        cost_basis = {}

        for ticker in all_tickers:
            trades = sorted(trades_by_ticker.get(ticker, []), key=lambda t: t["date"])
            pos_info = position_map.get(ticker, {"shares": 0})

            total_credits = sum(t["net_value"] for t in trades if t["net_value"] > 0)
            total_debits = sum(t["net_value"] for t in trades if t["net_value"] < 0)
            dividends = sum(t["net_value"] for t in trades if t["label"] == "Dividend")
            option_pl = sum(t["net_value"] for t in trades if t["instrument_type"] == "Option")
            equity_cost = sum(t["net_value"] for t in trades
                             if t["instrument_type"] == "Equity" and t["label"] != "Dividend")
            shares_held = pos_info["shares"]
            total_pl = total_credits + total_debits
            adjusted_cost = equity_cost + option_pl
            cost_per_share = adjusted_cost / shares_held if shares_held else 0

            wheels = _detect_wheels(trades)

            cost_basis[ticker] = {
                "total_credits": total_credits,
                "total_debits": total_debits,
                "dividends": dividends,
                "shares_held": shares_held,
                "option_pl": option_pl,
                "equity_cost": equity_cost,
                "total_pl": total_pl,
                "adjusted_cost": adjusted_cost,
                "cost_per_share": cost_per_share,
                "trades": trades,
                "wheels": wheels,
            }

        account_id = stmt.accountId or ""
        return cost_basis, account_id
    except Exception as e:
        logger.error("IBKR fetch_portfolio_data failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_portfolio_data: {e}", page="Portfolio")
        return {}, ""


def fetch_margin_requirements():
    """Margin per position is not available via Flex Queries."""
    return {}


def fetch_margin_for_position(ticker, quantity):
    """Dry-run margin check not available via Flex Queries."""
    return None


def fetch_net_liq_history(time_back="1y"):
    """Build net liq history from EquitySummaryByReportDateInBase."""
    try:
        stmt = _get_flex_statement()

        if not hasattr(stmt, "EquitySummaryInBase") or not stmt.EquitySummaryInBase:
            # Fallback: just return current NAV as single point
            nav = stmt.ChangeInNAV
            if nav and nav.endingValue:
                return [{"time": str(date.today()), "close": _dec(nav.endingValue)}]
            return []

        result = []
        for entry in stmt.EquitySummaryInBase:
            if entry.reportDate and entry.total is not None:
                result.append({
                    "time": str(entry.reportDate),
                    "close": _dec(entry.total),
                })

        # Filter by time_back
        if result and time_back != "all":
            from datetime import timedelta
            days_map = {"1d": 1, "1m": 30, "3m": 90, "6m": 180, "1y": 365}
            days = days_map.get(time_back, 365)
            cutoff = date.today() - timedelta(days=days)
            result = [r for r in result if r["time"] >= str(cutoff)]

        return sorted(result, key=lambda r: r["time"])
    except Exception as e:
        logger.error("IBKR fetch_net_liq_history failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_net_liq_history: {e}", page="Portfolio")
        return []


def fetch_portfolio_greeks():
    """Greeks are not available in Flex Queries. Return positions without Greeks."""
    try:
        stmt = _get_flex_statement()
        from ibflex.enums import AssetClass

        option_positions = []
        totals = {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0}

        for pos in (stmt.OpenPositions or []):
            if pos.assetCategory != AssetClass.OPTION:
                continue
            qty = _dec(pos.position)
            ticker = (pos.symbol or "").split(" ")[0].upper()

            option_positions.append({
                "symbol": pos.description or pos.symbol or "",
                "underlying": ticker,
                "quantity": qty,
                "direction": "Long" if qty > 0 else "Short",
                "delta": 0, "theta": 0, "gamma": 0, "vega": 0, "iv": 0,
            })

        return {"positions": option_positions, "totals": totals}
    except Exception as e:
        logger.error("IBKR fetch_portfolio_greeks failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_portfolio_greeks: {e}", page="Portfolio")
        return {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}}


def fetch_beta_weighted_delta():
    """Calculate portfolio Beta-Weighted Delta from Flex positions."""
    try:
        stmt = _get_flex_statement()
        from ibflex.enums import AssetClass, PutCall

        ctx = _ssl_context()

        spy_url = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=1d"
        req = urllib.request.Request(spy_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx) as resp:
            spy_data = json.loads(resp.read())
        spy_price = spy_data["chart"]["result"][0]["meta"]["regularMarketPrice"]

        # Aggregate share-equivalent deltas per ticker
        ticker_positions = defaultdict(float)
        for pos in (stmt.OpenPositions or []):
            ticker = (pos.symbol or "").split(" ")[0].upper()
            qty = _dec(pos.position)
            if pos.assetCategory == AssetClass.OPTION:
                multiplier = _dec(pos.multiplier, 100)
                # Approximate delta: calls +0.5, puts -0.5 (sign from qty handles long/short)
                delta_approx = 0.5 if pos.putCall == PutCall.CALL else -0.5
                ticker_positions[ticker] += delta_approx * qty * multiplier
            else:
                ticker_positions[ticker] += qty

        import yfinance as yf
        betas = {}
        for t in ticker_positions:
            try:
                info = yf.Ticker(t).info
                price = info.get("regularMarketPrice", 0) or 0
                beta = info.get("beta", 1.0) or 1.0
                betas[t] = {"beta": beta, "price": price}
            except Exception:
                betas[t] = {"beta": 1.0, "price": 0}

        bwd_positions = []
        total_bwd = 0
        total_dollar = 0

        for ticker, raw_delta in ticker_positions.items():
            beta = betas.get(ticker, {}).get("beta", 1.0)
            price = betas.get(ticker, {}).get("price", 0)
            bwd = raw_delta * beta * price / spy_price if spy_price else 0
            dollar_per_1pct = bwd * spy_price * 0.01

            bwd_positions.append({
                "ticker": ticker, "raw_delta": raw_delta, "beta": beta,
                "price": price, "bwd": bwd, "dollar_per_1pct": dollar_per_1pct,
            })
            total_bwd += bwd
            total_dollar += dollar_per_1pct

        return {
            "positions": bwd_positions, "portfolio_bwd": total_bwd,
            "spy_price": spy_price, "dollar_per_1pct": total_dollar,
        }
    except Exception as e:
        logger.error("IBKR fetch_beta_weighted_delta failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_beta_weighted_delta: {e}", page="Portfolio")
        return {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0}


def fetch_greeks_and_bwd():
    """Fetch Greeks and BWD together."""
    greeks = fetch_portfolio_greeks()
    bwd = fetch_beta_weighted_delta()
    return greeks, bwd


def fetch_yearly_transfers():
    """Fetch net cash transfers by year from Flex CashTransactions."""
    try:
        stmt = _get_flex_statement()
        from ibflex.enums import CashAction

        deposit_types = {CashAction.DEPOSITWITHDRAW}

        result = {}
        for tx in (stmt.CashTransactions or []):
            if tx.type not in deposit_types:
                continue
            amount = _dec(tx.amount)
            tx_date = tx.reportDate or (tx.dateTime.date() if tx.dateTime else None)
            if not tx_date:
                continue

            year, month = tx_date.year, tx_date.month
            if year not in result:
                result[year] = {"total": 0, "months": {}}
            result[year]["total"] += amount
            result[year]["months"][month] = result[year]["months"].get(month, 0) + amount
        return result
    except Exception as e:
        logger.error("IBKR fetch_yearly_transfers failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_yearly_transfers: {e}", page="Portfolio")
        return {}


def fetch_margin_interest():
    """Fetch margin interest from Flex CashTransactions."""
    try:
        stmt = _get_flex_statement()
        from ibflex.enums import CashAction

        interest_types = {CashAction.BROKERINTPAID, CashAction.BROKERINTRCVD}

        now = datetime.now()
        current_month_total = 0
        ytd_total = 0
        all_time_total = 0
        monthly = {}

        for tx in (stmt.CashTransactions or []):
            if tx.type not in interest_types:
                continue
            amount = _dec(tx.amount)
            tx_date = tx.reportDate or (tx.dateTime.date() if tx.dateTime else None)
            if not tx_date:
                continue

            all_time_total += amount
            if tx_date.year == now.year:
                ytd_total += amount
                if tx_date.month == now.month:
                    current_month_total += amount
            monthly[(tx_date.year, tx_date.month)] = monthly.get(
                (tx_date.year, tx_date.month), 0
            ) + amount

        return {"current_month": current_month_total, "ytd": ytd_total,
                "total": all_time_total, "monthly": monthly}
    except Exception as e:
        logger.error("IBKR fetch_margin_interest failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_margin_interest: {e}", page="Portfolio")
        return {"current_month": 0, "ytd": 0, "total": 0, "monthly": {}}


def fetch_option_chain(ticker, option_type='Put', min_dte=7, max_dte=60,
                       num_strikes=8, fallback_price=0.0):
    """Fetch option chain via yfinance (not available in Flex Queries)."""
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        current_price = stock.info.get("regularMarketPrice") or fallback_price
        today = date.today()
        expirations = []

        for exp_str in (stock.options or []):
            try:
                exp_date = date.fromisoformat(exp_str)
            except ValueError:
                continue
            dte = (exp_date - today).days
            if dte < min_dte or dte > max_dte:
                continue

            chain = stock.option_chain(exp_str)
            df = chain.puts if option_type == 'Put' else chain.calls
            if df.empty:
                continue

            df = df.copy()
            df["_dist"] = abs(df["strike"] - current_price)
            df = df.nsmallest(num_strikes, "_dist").sort_values("strike")

            strikes = []
            for _, row in df.iterrows():
                strikes.append({
                    "strike": float(row["strike"]),
                    "bid": float(row.get("bid", 0) or 0),
                    "ask": float(row.get("ask", 0) or 0),
                    "mid": (float(row.get("bid", 0) or 0) + float(row.get("ask", 0) or 0)) / 2,
                    "delta": 0, "theta": 0, "gamma": 0, "vega": 0,
                    "iv": float(row.get("impliedVolatility", 0) or 0) * 100,
                })
            expirations.append({
                "expiration_date": exp_str, "dte": dte,
                "expiration_type": "Regular" if exp_date.weekday() == 4 else "Weekly",
                "strikes": strikes,
            })
        return {"underlying_price": current_price, "expirations": expirations}
    except Exception as e:
        logger.error("IBKR fetch_option_chain failed: %s", e)
        log_error("IBKR_ERROR", f"fetch_option_chain ({ticker}): {e}", page="Watchlist")
        return {"underlying_price": fallback_price, "expirations": []}


def fetch_earnings_dates(tickers):
    """Fetch next earnings dates via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return {t: None for t in tickers}

    result = {}
    for ticker in tickers:
        try:
            stock = yf.Ticker(ticker)
            cal = stock.calendar
            if cal is not None and isinstance(cal, dict):
                earnings_date = cal.get("Earnings Date")
                if isinstance(earnings_date, list) and earnings_date:
                    earnings_date = earnings_date[0]
                if earnings_date:
                    result[ticker] = {
                        "date": earnings_date.date() if hasattr(earnings_date, 'date') else earnings_date,
                        "time": None,
                        "estimated": True,
                    }
                else:
                    result[ticker] = None
            else:
                result[ticker] = None
        except Exception:
            result[ticker] = None
    return result
