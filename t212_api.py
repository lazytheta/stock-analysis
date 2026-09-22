"""
Trading 212 read-only broker client.

Fetches Invest-account positions and cash from the Trading 212 Public API
(beta) and normalises them into the app's portfolio contract. Read-only:
no order/write endpoints are called. Live environment only.
"""

import base64
import logging
import time
from datetime import datetime

import requests

import gather_data

logger = logging.getLogger(__name__)

LIVE_BASE_URL = "https://live.trading212.com/api/v0"

# Module-level per-path timestamp of the last request, so we honour T212's
# per-endpoint rate limits without a shared client object.
_LAST_CALL: dict = {}

# Module-level cache of instrument metadata (code -> resolved info).
_INSTRUMENTS_CACHE: dict | None = None

# Order and cash history, cached briefly. Both are paginated behind a six-second
# throttle, and a single Results load wants each of them twice — once to build
# the positions and once to rebuild the account curve. Short enough that a new
# fill shows up on the next page load; long enough that one load does not wait
# for the same pages twice.
_HISTORY_TTL = 120.0
_HISTORY_CACHE: dict = {}


# Per-page-load call tally, read by load_profiler. Keyed without the query
# string, so a paginated history collapses into one row and a second visit to
# an account endpoint reads as a second visit rather than hiding behind its
# cursor. That distinction is what surfaced the 32-second account/info stall.
LAST_CALL_STATS: dict = {"by_path": {}}


def reset_call_stats():
    """Zero the tally. Called once at the top of a page render."""
    LAST_CALL_STATS["by_path"] = {}


def _note_path(path, *, rate_limited=False, slept=0.0):
    row = LAST_CALL_STATS["by_path"].setdefault(
        path.split("?", 1)[0], {"n": 0, "rate_limited": 0, "slept_s": 0.0})
    row["n"] += 1
    if rate_limited:
        row["rate_limited"] += 1
    row["slept_s"] += slept


# Account id and base currency, cached for an hour because neither changes and
# this is the strictest-limited endpoint T212 exposes. Two calls per cold load
# — fetch_portfolio_data wants the id, fetch_account_balances wants the
# currency — cost 32 seconds of 429 back-off on a measured load, which was 76%
# of the whole page. One call, reused.
_ACCOUNT_INFO_TTL = 3600.0
_ACCOUNT_INFO_CACHE: dict = {}


def fetch_account_info(creds: dict) -> dict:
    """The account's id and base currency. Cached — see _ACCOUNT_INFO_CACHE."""
    hit = _ACCOUNT_INFO_CACHE.get("info")
    if hit and (time.time() - hit[0]) < _ACCOUNT_INFO_TTL:
        return hit[1]
    info = _get("/equity/account/info", creds, min_interval=5.0) or {}
    _ACCOUNT_INFO_CACHE["info"] = (time.time(), info)
    return info


def _clear_history_cache():
    """Drop the cached order/cash history. For tests and after a reconnect."""
    _HISTORY_CACHE.clear()
    _ACCOUNT_INFO_CACHE.clear()


def _cached_history(key, build):
    hit = _HISTORY_CACHE.get(key)
    if hit and (time.time() - hit[0]) < _HISTORY_TTL:
        return hit[1]
    value = build()
    _HISTORY_CACHE[key] = (time.time(), value)
    return value


def _get(path: str, creds: dict, *, min_interval: float = 1.0, max_retries: int = 3):
    """GET a T212 endpoint with throttling + 429 retry. Returns parsed JSON."""
    # nextPagePath komt sinds 2026-09 mét het /api/v0-prefix terug dat de
    # basis-URL al draagt. Ongefilterd werd dat .../api/v0/api/v0/... en een
    # 404 op elke tweede pagina; de except in de history-lussen maakte daar
    # een lege lijst van, en de Results-pagina miste stilletjes elke
    # Trading 212-storting en de hele curve.
    _prefix = LIVE_BASE_URL[LIVE_BASE_URL.index("/api/"):]
    if path.startswith(_prefix + "/") or path.startswith(_prefix + "?"):
        path = path[len(_prefix):]
    url = f"{LIVE_BASE_URL}{path}"
    headers = _auth_header(creds)
    for attempt in range(max_retries):
        slept = 0.0
        wait = min_interval - (time.time() - _LAST_CALL.get(path, 0.0))
        if wait > 0:
            time.sleep(wait)
            slept = wait
        resp = requests.get(url, headers=headers, timeout=30)
        _LAST_CALL[path] = time.time()
        if resp.status_code == 429:
            retry_after = gather_data.bounded_retry_after(
                resp.headers.get("Retry-After"), min_interval)
            # Warning, not debug: this is dead time the user waits through,
            # and it is how the 32-second account/info stall was found.
            logger.warning("T212 429 on %s; retry in %ss", path, retry_after)
            _note_path(path, rate_limited=True, slept=slept + retry_after)
            time.sleep(retry_after)
            continue
        _note_path(path, slept=slept)
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return resp.json()


def _auth_header(creds: dict) -> dict:
    """Build the HTTP Basic auth header from key+secret."""
    raw = f"{creds['t212_api_key']}:{creds['t212_api_secret']}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _suffix_strip(code: str) -> dict:
    """Fallback: derive symbol + exchange from a T212 code like AAPL_US_EQ."""
    parts = code.split("_")
    symbol = parts[0] if parts else code
    exchange = parts[1] if len(parts) > 2 else ""
    return {"symbol": symbol, "currency": "", "isin": "", "exchange": exchange}


def _resolve_instruments(creds: dict) -> dict:
    """Fetch + cache the T212 instrument metadata as code -> resolved dict."""
    global _INSTRUMENTS_CACHE
    if _INSTRUMENTS_CACHE is not None:
        return _INSTRUMENTS_CACHE
    out = {}
    for item in _get("/equity/metadata/instruments", creds, min_interval=5.0):
        code = item.get("ticker")
        if not code:
            continue
        fb = _suffix_strip(code)
        out[code] = {
            "symbol": item.get("shortName") or fb["symbol"],
            "currency": item.get("currencyCode") or "",
            "isin": item.get("isin") or "",
            "exchange": fb["exchange"],
        }
    _INSTRUMENTS_CACHE = out
    return out


def _clean(code: str, creds: dict) -> dict:
    """Resolve one instrument code, falling back to suffix-strip if unknown."""
    return _resolve_instruments(creds).get(code) or _suffix_strip(code)


def fetch_portfolio_data(creds: dict):
    """Return (cost_basis_by_symbol, account_id) from T212 positions."""
    positions = _get("/equity/positions", creds, min_interval=1.0)
    # Fill history, so a T212 holding carries the same dated lots a Tastytrade
    # one does. Everything downstream — FIFO basis, realized P/L, performance
    # against the index — reads trades and cannot tell the brokers apart.
    trades_by_symbol = fetch_trades(creds)
    cost_basis = {}
    for pos in positions or []:
        # /equity/positions nests the instrument and names its money fields
        # differently from the rest of the API. Reading them flat yielded a
        # blank symbol and zeros for everything except the share count.
        instrument = pos.get("instrument") or {}
        wallet = pos.get("walletImpact") or {}
        info = _clean(instrument.get("ticker", ""), creds)
        symbol = info["symbol"]
        shares = pos.get("quantity") or 0

        # Two currencies live in one position: averagePricePaid / currentPrice
        # follow the *instrument* (RDDT in USD), walletImpact follows the
        # *account* (EUR here). Per-share figures use the instrument's, because
        # that is the currency the app prices everything in — a cost basis of
        # EUR 140.76 sitting next to a Yahoo quote of USD 158.69 is a
        # comparison of two different things.
        native_avg = pos.get("averagePricePaid") or 0.0
        native_price = pos.get("currentPrice") or 0.0
        native_ccy = info["currency"] or ""

        # Convert to USD, because that is the unit the app labels every figure
        # with. A EUR-denominated ETF left at its own numbers under a "$" sign
        # understates the holding and skews every weight in the table. When the
        # rate is unknown the position keeps its own currency — a row that says
        # EUR beats a dollar figure that is really euros.
        rate = gather_data.fetch_fx_rate(native_ccy)
        converted = rate is not None and native_ccy not in ("", "USD")
        avg = native_avg * rate if converted else native_avg
        price = native_price * rate if converted else native_price

        # equity_cost and cost_per_share are NEGATIVE by convention — cash that
        # left the account. Tastytrade builds them by summing signed trade
        # values, and the portfolio page relies on it: unrealized P/L is
        # `market_value + equity_cost`. Handing back a positive cost turned
        # that into an addition, which is how RDDT showed +212% on a losing
        # position.
        cost = -(shares * avg)

        # total_pl follows that same convention: the net cash a name has moved,
        # NOT its profit. Tastytrade sums signed transaction values into it and
        # the portfolio page finishes the sum with `total_pl + market_value`.
        #
        # This used to hold the finished unrealized P/L, so the page added the
        # market value to a number that already accounted for it: META's -$20
        # loss was drawn as a +$1,643 gain, its position's value, and every
        # Trading 212 holding turned up among the top performers. Two meanings
        # for one field, the mistake this codebase keeps paying for.
        #
        # Summed from the trades rather than taken from `cost`, because a name
        # that was partly sold has cash flowing both ways and `cost` only knows
        # the shares still held. For a plain buy the two agree exactly, which
        # is what the closed-position branch below already assumes.
        pl = sum(t["net_value"] for t in trades_by_symbol.get(symbol, [])) or cost

        cost_basis[symbol] = {
            "total_credits": 0,
            "total_debits": 0,
            "dividends": 0,
            "shares_held": shares,
            "option_pl": 0,
            "equity_cost": cost,
            "total_pl": pl,
            "adjusted_cost": cost,
            "cost_per_share": -avg,      # negative, same convention as above
            "trades": trades_by_symbol.get(symbol, []),
            "wheels": [],
            # No option legs and no wheel cycles — a plain holding. The
            # portfolio page reads this to pick the buy-and-hold column set
            # instead of the wheel one, whose cost basis and days-held come
            # from trades that do not exist here.
            "buy_and_hold": True,
            "purchase_price": avg,       # positive, for display
            # T212 quotes the instrument itself, so the page doesn't have to
            # guess a Yahoo symbol. It gets that wrong for anything not listed
            # in the US: the Amundi ETF is WEBN.DE on Xetra, and a lookup on
            # the bare "WEBN" 404s — leaving the row at $0 market value and
            # handing 100% of the portfolio weight to the other position.
            "broker_price": price,
            # What the figures above are in: USD once converted, otherwise the
            # instrument's own. The native values are kept alongside so a row
            # can show where it came from.
            "currency": "USD" if converted or native_ccy == "USD" else native_ccy,
            "native_currency": native_ccy,
            "native_purchase_price": native_avg,
            "native_price": native_price,
            "fx_rate": rate if converted else 1.0,
            "exchange": info["exchange"],
            # Parqet's logo index only resolves US-style tickers; the ISIN is
            # the fallback that makes a European ETF show its logo instead of
            # a blank square.
            "isin": instrument.get("isin") or info["isin"],
            # Same position expressed in the account's currency, straight from
            # T212. Kept so a multi-currency total can be struck without
            # inventing an FX rate.
            "account_currency": wallet.get("currency") or "",
            "account_cost": wallet.get("totalCost"),
            "account_value": wallet.get("currentValue"),
            "account_pl": wallet.get("unrealizedProfitLoss"),
        }
    # Names that were traded but are no longer held. T212 returns only open
    # positions, so a name you sold in full would disappear from the app the
    # moment you sold it — which is exactly when there is something to say
    # about it. Tastytrade reconstructs everything from transactions and keeps
    # closed cards; this makes T212 behave the same.
    for symbol, trades in trades_by_symbol.items():
        if symbol in cost_basis:
            continue
        cost_basis[symbol] = {
            "total_credits": 0,
            "total_debits": 0,
            "dividends": 0,
            "shares_held": 0,
            "option_pl": 0,
            "equity_cost": 0.0,
            "total_pl": sum(t["net_value"] for t in trades),
            "adjusted_cost": 0.0,
            "cost_per_share": 0.0,
            "trades": trades,
            "wheels": [],
            "buy_and_hold": True,
            "purchase_price": 0.0,
            "broker_price": 0.0,
            "currency": "USD",
            "exchange": "",
            "isin": _clean_isin(trades),
            "account_currency": "",
            "account_cost": None,
            "account_value": None,
            "account_pl": None,
        }

    info = fetch_account_info(creds)
    account_id = str(info.get("id") or "")
    return cost_basis, account_id


def _clean_isin(trades):
    """ISIN off any of the ticker's fills, for the logo fallback."""
    for t in trades:
        if t.get("isin"):
            return t["isin"]
    return ""


def fetch_trades(creds: dict) -> dict:
    """Return {symbol: [trade, ...]} from T212 fill history, oldest first.

    Cached for _HISTORY_TTL — see _cached_history.

    Same trade shape Tastytrade produces, so the FIFO lot engine, the cost
    basis and the index comparison all work on T212 positions without knowing
    which broker they came from. Without it a T212 holding has an average price
    and no history — no purchase date to measure against the index, and no way
    to tell one buy from two. WEBN was bought twice.

    A failure returns {} rather than propagating: the positions still render
    with the broker's average price, minus the history.
    """
    return _cached_history("trades", lambda: _fetch_trades_uncached(creds))


def _fetch_trades_uncached(creds: dict) -> dict:
    out: dict = {}
    path = "/equity/history/orders?limit=50"
    pages = 0
    try:
        while path and pages < 40:      # backstop against a cursor that loops
            body = _get(path, creds, min_interval=6.0) or {}
            for item in body.get("items") or []:
                trade = _fill_to_trade(item, creds)
                if trade:
                    sym = trade.pop("_symbol")
                    trade["symbol"] = sym
                    out.setdefault(sym, []).append(trade)
            path = body.get("nextPagePath")
            pages += 1
    except Exception as e:
        logger.warning("T212 order history unavailable: %s", e)
        return {}

    # Oldest first: FIFO retires the oldest lot, and the broker's ordering is
    # not something to take on trust.
    for trades in out.values():
        trades.sort(key=lambda t: t["date"])
    return out


def _fill_to_trade(item: dict, creds: dict):
    """Turn one history entry into a trade dict, or None if it has no fill."""
    order = item.get("order") or {}
    fill = item.get("fill") or {}
    qty = fill.get("quantity")
    price = fill.get("price")
    filled_at = fill.get("filledAt")
    if not qty or price is None or not filled_at:
        return None
    # T212 geeft de hoeveelheid van een verkoop negatief terug. De richting
    # komt hieronder uit de orderzijde; ruw overgenomen telde een verkoop als
    # een extra aankoop en kwamen de verkochte stukken in de curve terug als
    # een tweede positie.
    qty = abs(float(qty))

    info = _clean(order.get("ticker") or "", creds)
    rate = gather_data.fetch_fx_rate(info["currency"] or "")
    fx = rate if rate is not None and info["currency"] not in ("", "USD") else 1.0
    price = price * fx

    is_buy = (order.get("side") or "").upper() != "SELL"
    try:
        day = datetime.fromisoformat(filled_at.replace("Z", "+00:00")).date()
    except ValueError:
        return None

    wallet = fill.get("walletImpact") or {}
    return {
        "_symbol": info["symbol"],
        "isin": (order.get("instrument") or {}).get("isin") or info["isin"],
        # Kept unconverted alongside the USD figures: a historical curve has to
        # convert at each day's own rate, and today's rate is the one thing it
        # must not use.
        "native_price": fill.get("price"),
        "native_currency": info["currency"] or "",
        # Signed here, not trusted from the API: T212 reports the wallet
        # impact as a magnitude, so a purchase came back positive and the
        # reconstruction added the money it had just spent.
        "wallet_net_value": (-abs(wallet["netValue"]) if is_buy
                             else abs(wallet["netValue"]))
                            if wallet.get("netValue") is not None else None,
        "date": day,
        "label": "Stock Buy" if is_buy else "Stock Sell",
        "type": "Trade",
        "sub_type": "Buy to Open" if is_buy else "Sell to Close",
        "description": f'{"Bought" if is_buy else "Sold"} {qty} {info["symbol"]} @ {price:.2f}',
        "symbol": info["symbol"],
        "action": "Buy to Open" if is_buy else "Sell to Close",
        "quantity": float(qty),
        "price": price,
        # Signed the way the rest of the app reads it: cash out is negative.
        "net_value": -(qty * price) if is_buy else (qty * price),
        "instrument_type": "Equity",
    }


def fetch_cash_movements(creds: dict) -> list:
    """Deposits, withdrawals, interest and fees, oldest first.

    Amounts are in the ACCOUNT's currency, unconverted — the caller decides
    which day's rate applies, and for a historical curve that is never today's.

    Cached for _HISTORY_TTL — see _cached_history.
    """
    return _cached_history("cash", lambda: _fetch_cash_movements_uncached(creds))


def _fetch_cash_movements_uncached(creds: dict) -> list:
    out, path, pages = [], "/equity/history/transactions?limit=50", 0
    try:
        while path and pages < 40:
            body = _get(path, creds, min_interval=6.0) or {}
            for item in body.get("items") or []:
                stamp = item.get("dateTime") or ""
                try:
                    day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
                except ValueError:
                    continue
                out.append({
                    "date": day,
                    "amount": float(item.get("amount") or 0.0),
                    "type": item.get("type") or "",
                    "currency": item.get("currency") or "",
                    "reference": item.get("reference") or "",
                })
            path = body.get("nextPagePath")
            pages += 1
    except Exception as e:
        logger.warning("T212 cash history unavailable: %s", e)
        return []
    out.sort(key=lambda m: m["date"])
    return out


def fetch_account_balances(creds: dict) -> dict:
    """Return the app balances dict from T212 account cash, in USD.

    A Dutch T212 account is denominated in EUR. These figures get added to a
    Tastytrade balance to form the portfolio's combined value, so they have to
    be in the same unit — otherwise the header understates the total by the
    whole FX difference. "currency" says which unit actually came out, since
    an unavailable rate leaves the numbers native rather than mislabelled.
    """
    cash = _get("/equity/account/cash", creds, min_interval=5.0) or {}
    info = fetch_account_info(creds)
    native = (info.get("currencyCode") or "USD").upper()

    rate = gather_data.fetch_fx_rate(native)
    converted = rate is not None and native != "USD"
    fx = rate if converted else 1.0

    total = (cash.get("total") or 0.0) * fx
    free = (cash.get("free") or 0.0) * fx
    return {
        "net_liquidating_value": total,
        "cash_balance": free,
        "equity_buying_power": free,
        "derivative_buying_power": free,
        "maintenance_requirement": 0.0,
        "maintenance_excess": 0.0,
        "margin_equity": total,
        "used_derivative_buying_power": 0.0,
        "reg_t_margin_requirement": 0.0,
        "currency": "USD" if converted or native == "USD" else native,
        "native_currency": native,
        "fx_rate": fx,
    }


_TIME_BACK_DAYS = {"1d": 2, "1m": 31, "3m": 92, "6m": 183, "1y": 366,
                   "all": 3650}


def fetch_net_liq_history(creds: dict, time_back: str = "1y"):
    """Rebuild the account-value curve. Returns [{"time", "close"}] in USD.

    T212 has no endpoint for this — see t212_history for why it can be
    computed anyway. Returns [] when there is nothing to rebuild from, which
    is the same thing the adapter returned before and renders as "unavailable".
    """
    from datetime import date, timedelta

    import gather_data
    from t212_history import reconstruct_net_liq, yahoo_candidates

    fills = [t for trades in fetch_trades(creds).values() for t in trades]
    moves = fetch_cash_movements(creds)
    if not fills and not moves:
        return []

    start = min([f["date"] for f in fills] + [m["date"] for m in moves])
    window = _TIME_BACK_DAYS.get(time_back, 366)
    start = max(start, date.today() - timedelta(days=window))
    end = date.today()

    years = max(1, (end - start).days // 365 + 1)
    fx = gather_data.fetch_daily_closes("EURUSD=X", years)

    closes = {}
    for symbol in {f["symbol"] for f in fills}:
        exchange = _clean_symbol_exchange(symbol, creds)
        currency = next((f.get("native_currency") for f in fills
                         if f["symbol"] == symbol), "")
        # First candidate that answers wins; a name Yahoo does not know under
        # any of them is carried at cost and reported, never guessed at.
        for candidate in yahoo_candidates(symbol, exchange, currency):
            series = gather_data.fetch_daily_closes(candidate, years)
            if series:
                closes[symbol] = series
                break

    prepared = [{
        "date": f["date"],
        "symbol": f["symbol"],
        "quantity": f["quantity"],
        "is_buy": "Buy" in (f.get("action") or ""),
        "native_price": f.get("native_price"),
        "native_currency": f.get("native_currency") or "",
        # Falls back to the USD figure when T212 gave no wallet impact; the
        # curve is then off by the FX difference rather than absent.
        "wallet_net_value": (f.get("wallet_net_value")
                             if f.get("wallet_net_value") is not None
                             else f.get("net_value") or 0.0),
    } for f in fills]

    series, unpriced = reconstruct_net_liq(prepared, moves, closes, fx, start, end)
    if unpriced:
        # Carried at cost inside the reconstruction; say so rather than let a
        # flat line pass for a valuation.
        logger.info("T212 net liq: no closes for %s — carried at cost",
                    ", ".join(unpriced))
    return series


def _clean_symbol_exchange(symbol: str, creds: dict) -> str:
    """The exchange segment for a resolved symbol, for the Yahoo guess."""
    for code, info in (_resolve_instruments(creds) or {}).items():
        if info.get("symbol") == symbol:
            return info.get("exchange") or ""
    return ""


def fetch_yearly_transfers(creds: dict) -> dict:
    """Net deposits per year and month, in USD."""
    import gather_data
    from t212_history import yearly_transfers

    moves = fetch_cash_movements(creds)
    if not moves:
        return {}
    years = max(1, (moves[-1]["date"] - moves[0]["date"]).days // 365 + 1)
    fx = gather_data.fetch_daily_closes("EURUSD=X", years)
    return yearly_transfers(moves, fx)
