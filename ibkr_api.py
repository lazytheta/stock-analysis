"""
IBKR API module — fetch portfolio data from Interactive Brokers.
Returns the same data structures as tastytrade_api.py for adapter compatibility.
"""

import json
import logging
import ssl
import tempfile
import urllib.request
from collections import defaultdict
from datetime import date, datetime

import streamlit as st

logger = logging.getLogger(__name__)


def _get_ibkr_client():
    """Create an IBKRHttpClient from stored credentials."""
    from pathlib import Path
    from ibkr_web_client import IBKRConfig, IBKRHttpClient

    creds = st.session_state.get("ibkr_credentials")
    if not creds:
        raise RuntimeError("IBKR credentials not configured")

    # Write key material to temp files (ibkr_web_client expects file paths)
    tmp_dir = tempfile.mkdtemp(prefix="ibkr_")
    enc_path = Path(tmp_dir) / "encryption.pem"
    sig_path = Path(tmp_dir) / "signature.pem"
    dh_path = Path(tmp_dir) / "dhparam.pem"

    enc_path.write_text(creds["ibkr_encryption_key"])
    sig_path.write_text(creds["ibkr_signing_key"])

    # DH params can be generated once; use a default if not provided
    if "ibkr_dh_param" in creds:
        dh_path.write_text(creds["ibkr_dh_param"])
    else:
        import subprocess
        subprocess.run(
            ["openssl", "dhparam", "-out", str(dh_path), "2048"],
            capture_output=True, check=True,
        )

    config = IBKRConfig(
        token_access=creds["ibkr_access_token"],
        token_secret=creds["ibkr_access_token_secret"],
        consumer_key=creds["ibkr_consumer_key"],
        dh_param_path=dh_path,
        dh_private_encryption_path=enc_path,
        dh_private_signature_path=sig_path,
    )
    return IBKRHttpClient(config)


def _get_account_id(client):
    """Get the first brokerage account ID."""
    accounts = client.portfolio_accounts()
    if isinstance(accounts, list) and accounts:
        return accounts[0].get("accountId") or accounts[0].get("id")
    raise RuntimeError("No IBKR accounts found")


def _normalize_ibkr_trade(tx):
    """Convert an IBKR transaction dict to the standard trade record format."""
    net_value = float(tx.get("amount", 0) or tx.get("netAmount", 0) or 0)
    quantity = abs(float(tx.get("quantity", 0) or 0))
    price = float(tx.get("price", 0) or 0)

    asset_class = tx.get("assetClass", "").upper()
    side = (tx.get("side", "") or tx.get("buySell", "")).upper()
    tx_type = (tx.get("type", "") or tx.get("transactionType", "")).upper()

    if "DIVIDEND" in tx_type or "DIV" in tx_type:
        label = "Dividend"
    elif asset_class in ("OPT", "FOP"):
        if "BUY" in side:
            label = "BTC" if net_value < 0 else "Buy Option"
        else:
            label = "CSP" if "PUT" in tx.get("description", "").upper() else "CC"
    elif asset_class in ("STK", "STOCK"):
        label = "Stock Buy" if "BUY" in side else "Stock Sell"
    else:
        label = tx_type or "Other"

    raw_date = tx.get("date", "") or tx.get("tradeDate", "") or tx.get("settleDate", "")
    try:
        if isinstance(raw_date, str) and len(raw_date) >= 10:
            trade_date = date.fromisoformat(raw_date[:10])
        else:
            trade_date = date.today()
    except (ValueError, TypeError):
        trade_date = date.today()

    return {
        "date": trade_date,
        "label": label,
        "type": tx_type,
        "sub_type": tx.get("subType", ""),
        "description": tx.get("description", ""),
        "symbol": tx.get("symbol", ""),
        "action": side,
        "quantity": quantity,
        "price": price,
        "net_value": net_value,
        "instrument_type": "Option" if asset_class in ("OPT", "FOP") else "Equity",
    }


# ── Public API (same signatures as tastytrade_api) ──

def fetch_account_balances():
    """Fetch IBKR account balances via portfolio summary."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)
        summary = client.get_portfolio_summary(account_id)

        def _val(key, default=0.0):
            entry = summary.get(key, {})
            if isinstance(entry, dict):
                return float(entry.get("amount", default))
            return float(entry) if entry else default

        return {
            "net_liquidating_value": _val("netliquidation"),
            "cash_balance": _val("totalcashvalue"),
            "equity_buying_power": _val("buyingpower"),
            "derivative_buying_power": _val("buyingpower"),
            "maintenance_requirement": _val("maintenancemarginreq"),
            "maintenance_excess": _val("excessliquidity"),
            "margin_equity": _val("grosspositionvalue"),
            "used_derivative_buying_power": 0.0,
            "reg_t_margin_requirement": _val("initmarginreq"),
        }
    except Exception as e:
        logger.error("IBKR fetch_account_balances failed: %s", e)
        return {
            "net_liquidating_value": 0, "cash_balance": 0,
            "equity_buying_power": 0, "derivative_buying_power": 0,
            "maintenance_requirement": 0, "maintenance_excess": 0,
            "margin_equity": 0, "used_derivative_buying_power": 0,
            "reg_t_margin_requirement": 0,
        }


def fetch_portfolio_data():
    """Fetch IBKR positions and transactions, compute cost basis per ticker."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)

        positions = client.get_positions(account_id, page_id=0)
        if not isinstance(positions, list):
            positions = []

        try:
            tx_resp = client.get_accounts_transactions(
                account_ids=[account_id], contract_ids=[], days=365,
            )
            transactions = tx_resp.get("transactions", []) if isinstance(tx_resp, dict) else []
        except Exception as e:
            logger.warning("IBKR transaction fetch failed: %s", e)
            transactions = []

        position_map = {}
        for pos in positions:
            ticker = (pos.get("ticker") or pos.get("contractDesc", "")
                      or pos.get("symbol", "UNKNOWN")).split(" ")[0].upper()
            if ticker not in position_map:
                position_map[ticker] = {"shares": 0}
            asset_class = (pos.get("assetClass", "") or "").upper()
            qty = float(pos.get("position", 0) or pos.get("quantity", 0) or 0)
            if asset_class in ("STK", "STOCK", ""):
                position_map[ticker]["shares"] += int(qty)

        trades_by_ticker = defaultdict(list)
        for tx in transactions:
            trade = _normalize_ibkr_trade(tx)
            ticker = trade["symbol"].split(" ")[0].upper() if trade["symbol"] else "UNKNOWN"
            trades_by_ticker[ticker].append(trade)

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

            from tastytrade_api import _detect_wheels
            wheels = _detect_wheels(trades)

            cost_basis[ticker] = {
                "total_credits": total_credits,
                "total_debits": total_debits,
                "dividends": dividends,
                "shares_held": shares_held,
                "option_pl": option_pl,
                "equity_cost": equity_cost,
                "total_pl": total_pl,
                "total_pl_real": total_pl,
                "adjusted_cost": adjusted_cost,
                "cost_per_share": cost_per_share,
                "trades": trades,
                "wheels": wheels,
            }

        return cost_basis, account_id
    except Exception as e:
        logger.error("IBKR fetch_portfolio_data failed: %s", e)
        return {}, ""


def fetch_margin_requirements():
    """Fetch per-position margin requirements from IBKR."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)

        positions = client.get_positions(account_id, page_id=0)
        if not isinstance(positions, list):
            return {}

        result = {}
        for pos in positions:
            ticker = (pos.get("ticker") or pos.get("contractDesc", "")
                      or pos.get("symbol", "")).split(" ")[0].upper()
            if not ticker:
                continue
            margin = float(pos.get("maintenanceMarginReq", 0) or pos.get("maintMarginReq", 0) or 0)
            init_margin = float(pos.get("initMarginReq", 0) or 0)

            result[ticker] = {
                "description": pos.get("contractDesc", ticker),
                "margin_requirement": margin,
                "maintenance_requirement": margin,
                "initial_requirement": init_margin,
                "buying_power": margin,
                "margin_type": "Margin",
                "point_of_no_return_pct": None,
                "expected_down_pct": None,
            }
        return result
    except Exception as e:
        logger.error("IBKR fetch_margin_requirements failed: %s", e)
        return {}


def fetch_margin_for_position(ticker, quantity):
    """Dry-run margin check. IBKR web client lacks this endpoint.
    Returns: None
    """
    return None


def fetch_net_liq_history(time_back="1y"):
    """Fetch IBKR net liquidating value history via performance endpoint."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)

        period_map = {
            "1d": "1D", "1m": "1M", "3m": "3M",
            "6m": "6M", "1y": "1Y", "all": "10Y",
        }
        period = period_map.get(time_back, "1Y")

        perf = client.get_accounts_performance(account_ids=[account_id], period=period)

        result = []
        if isinstance(perf, dict):
            nav_data = perf.get("nav", {}).get("data", [])
            if not nav_data:
                nav_data = perf.get("data", [])
            for series in nav_data:
                if isinstance(series, dict):
                    dates = series.get("dates", [])
                    values = series.get("values", []) or series.get("nav", [])
                    for d, v in zip(dates, values):
                        try:
                            result.append({"time": str(d), "close": float(v)})
                        except (ValueError, TypeError):
                            continue
        return result
    except Exception as e:
        logger.error("IBKR fetch_net_liq_history failed: %s", e)
        return []


def fetch_portfolio_greeks():
    """Fetch Greeks for all open option positions from IBKR."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)
        positions = client.get_positions(account_id, page_id=0)
        if not isinstance(positions, list):
            return {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}}

        option_positions = []
        totals = {"delta": 0.0, "theta": 0.0, "gamma": 0.0, "vega": 0.0}

        for pos in positions:
            asset_class = (pos.get("assetClass", "") or "").upper()
            if asset_class not in ("OPT", "FOP"):
                continue
            qty = float(pos.get("position", 0) or pos.get("quantity", 0) or 0)
            multiplier = float(pos.get("multiplier", 100) or 100)

            delta = float(pos.get("delta", 0) or 0) * qty * multiplier
            theta = float(pos.get("theta", 0) or 0) * qty * multiplier
            gamma = float(pos.get("gamma", 0) or 0) * qty * multiplier
            vega = float(pos.get("vega", 0) or 0) * qty * multiplier
            iv = float(pos.get("impliedVol", 0) or pos.get("iv", 0) or 0) * 100

            ticker = (pos.get("ticker") or pos.get("contractDesc", "")
                      or pos.get("symbol", "")).split(" ")[0].upper()

            option_positions.append({
                "symbol": pos.get("contractDesc", ""),
                "underlying": ticker,
                "quantity": qty,
                "direction": "Long" if qty > 0 else "Short",
                "delta": delta, "theta": theta, "gamma": gamma, "vega": vega, "iv": iv,
            })
            totals["delta"] += delta
            totals["theta"] += theta
            totals["gamma"] += gamma
            totals["vega"] += vega

        return {"positions": option_positions, "totals": totals}
    except Exception as e:
        logger.error("IBKR fetch_portfolio_greeks failed: %s", e)
        return {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}}


def fetch_beta_weighted_delta():
    """Calculate portfolio Beta-Weighted Delta relative to SPY."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)
        positions = client.get_positions(account_id, page_id=0)
        if not isinstance(positions, list):
            return {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0}

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        spy_url = "https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=1d&interval=1d"
        req = urllib.request.Request(spy_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx) as resp:
            spy_data = json.loads(resp.read())
        spy_price = spy_data["chart"]["result"][0]["meta"]["regularMarketPrice"]

        ticker_positions = defaultdict(float)
        for pos in positions:
            ticker = (pos.get("ticker") or pos.get("symbol", "")).split(" ")[0].upper()
            qty = float(pos.get("position", 0) or 0)
            asset_class = (pos.get("assetClass", "") or "").upper()
            if asset_class in ("OPT", "FOP"):
                delta_per = float(pos.get("delta", 0) or 0)
                multiplier = float(pos.get("multiplier", 100) or 100)
                ticker_positions[ticker] += delta_per * qty * multiplier
            else:
                ticker_positions[ticker] += qty

        betas = {}
        for t in ticker_positions:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1d&interval=1d"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, context=ctx) as resp:
                    data = json.loads(resp.read())
                price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
                betas[t] = {"beta": 1.0, "price": price}
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
        return {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0}


def fetch_greeks_and_bwd():
    """Fetch Greeks and BWD together."""
    greeks = fetch_portfolio_greeks()
    bwd = fetch_beta_weighted_delta()
    return greeks, bwd


def fetch_yearly_transfers():
    """Fetch net cash transfers by year from IBKR transactions."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)
        tx_resp = client.get_accounts_transactions(
            account_ids=[account_id], contract_ids=[], days=3650,
        )
        transactions = tx_resp.get("transactions", []) if isinstance(tx_resp, dict) else []

        result = {}
        for tx in transactions:
            tx_type = (tx.get("type", "") or tx.get("transactionType", "")).upper()
            if not any(k in tx_type for k in ("DEPOSIT", "WITHDRAWAL", "TRANSFER")):
                continue
            amount = float(tx.get("amount", 0) or tx.get("netAmount", 0) or 0)
            raw_date = tx.get("date", "") or tx.get("settleDate", "")
            try:
                dt = datetime.fromisoformat(str(raw_date)[:10])
            except (ValueError, TypeError):
                continue
            year, month = dt.year, dt.month
            if year not in result:
                result[year] = {"total": 0, "months": {}}
            result[year]["total"] += amount
            result[year]["months"][month] = result[year]["months"].get(month, 0) + amount
        return result
    except Exception as e:
        logger.error("IBKR fetch_yearly_transfers failed: %s", e)
        return {}


def fetch_margin_interest():
    """Fetch margin interest charges from IBKR transactions."""
    try:
        client = _get_ibkr_client()
        account_id = _get_account_id(client)
        tx_resp = client.get_accounts_transactions(
            account_ids=[account_id], contract_ids=[], days=3650,
        )
        transactions = tx_resp.get("transactions", []) if isinstance(tx_resp, dict) else []

        now = datetime.now()
        current_month_total = 0
        ytd_total = 0
        all_time_total = 0
        monthly = {}

        for tx in transactions:
            tx_type = (tx.get("type", "") or tx.get("transactionType", "")).upper()
            if "INTEREST" not in tx_type:
                continue
            amount = float(tx.get("amount", 0) or tx.get("netAmount", 0) or 0)
            raw_date = tx.get("date", "") or tx.get("settleDate", "")
            try:
                dt = datetime.fromisoformat(str(raw_date)[:10])
            except (ValueError, TypeError):
                continue
            all_time_total += amount
            if dt.year == now.year:
                ytd_total += amount
                if dt.month == now.month:
                    current_month_total += amount
            monthly[(dt.year, dt.month)] = monthly.get((dt.year, dt.month), 0) + amount

        return {"current_month": current_month_total, "ytd": ytd_total, "total": all_time_total, "monthly": monthly}
    except Exception as e:
        logger.error("IBKR fetch_margin_interest failed: %s", e)
        return {"current_month": 0, "ytd": 0, "total": 0, "monthly": {}}


def fetch_option_chain(ticker, option_type='Put', min_dte=7, max_dte=60,
                       num_strikes=8, fallback_price=0.0):
    """Fetch option chain via yfinance (IBKR web client lacks this endpoint)."""
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        current_price = stock.info.get("regularMarketPrice") or fallback_price

        expirations = []
        today = date.today()

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
        return {"underlying_price": fallback_price, "expirations": []}


def fetch_earnings_dates(tickers):
    """Fetch next earnings dates via yfinance."""
    result = {}
    for ticker in tickers:
        try:
            import yfinance as yf
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
