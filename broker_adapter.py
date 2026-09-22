"""
Broker Adapter — routes all broker API calls to the active broker backend.

Delegates to either tastytrade_api or ibkr_api based on
st.session_state["active_broker"]. Callers never pass refresh tokens
or credentials; the adapter handles that internally.
"""

import logging
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

import t212_api
import tastytrade_api

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def get_active_broker():
    """Return the name of the currently active broker.

    If not explicitly set, auto-detect based on which brokers are connected.
    """
    explicit = st.session_state.get("active_broker")
    if explicit:
        return explicit
    # Auto-detect: if only one broker is connected, use that one.
    # If more than one are connected the sidebar switcher should be shown so
    # the user picks explicitly; default to tastytrade until they do.
    has_tt = bool(st.session_state.get("tt_refresh_token"))
    has_ibkr = bool(st.session_state.get("ibkr_credentials"))
    has_t212 = bool(st.session_state.get("t212_credentials"))
    if has_t212 and not has_tt and not has_ibkr:
        return "t212"
    if has_ibkr and not has_tt:
        return "ibkr"
    if has_tt and not has_ibkr:
        return "tastytrade"
    # Multiple connected — default to tastytrade (sidebar switcher lets user change)
    return "tastytrade"


def has_active_broker():
    """Return True if the user has at least one broker connected."""
    return bool(
        st.session_state.get("tt_refresh_token")
        or st.session_state.get("ibkr_credentials")
        or st.session_state.get("t212_credentials")
    )


def _get_ibkr():
    """Lazy-import ibkr_api so the module loads even before ibkr_api.py exists."""
    import ibkr_api
    return ibkr_api


# Module-level cache so worker threads (ThreadPoolExecutor) can resolve the
# refresh token even though st.session_state is unreachable without a
# ScriptRunContext. Main-thread reads keep the cache fresh; worker reads fall
# back to it. Single-user app, so no cross-user contamination concern.
_TT_RT_CACHE: str | None = None


def _get_refresh_token():
    """Get the TastyTrade refresh token, working from main and worker threads.

    Main thread: reads st.session_state and refreshes the module cache.
    Worker thread: st.session_state silently returns None (no ScriptRunContext)
    so we fall back to the cache populated by an earlier main-thread call.
    Without this, ThreadPool callers get None and tastytrade_api falls back to
    the stale TASTYTRADE_REFRESH_TOKEN in st.secrets — TT immediately revokes
    the grant chain on that dead token.
    """
    global _TT_RT_CACHE
    try:
        rt = st.session_state.get("tt_refresh_token")
        if rt:
            _TT_RT_CACHE = rt
            return rt
    except Exception:
        pass
    return _TT_RT_CACHE


# Module-level cache mirroring _TT_RT_CACHE above, so worker threads can
# resolve T212 credentials too. Single-user app, no cross-user concern.
_T212_CREDS_CACHE: dict | None = None


def _get_t212_creds():
    """Get T212 credentials, working from main and worker threads (see _get_refresh_token)."""
    global _T212_CREDS_CACHE
    try:
        creds = st.session_state.get("t212_credentials")
        if creds:
            _T212_CREDS_CACHE = creds
            return creds
    except Exception:
        pass
    return _T212_CREDS_CACHE


# ---------------------------------------------------------------------------
# Routed broker-specific functions
# ---------------------------------------------------------------------------

def fetch_portfolio_data():
    if get_active_broker() == "t212":
        return t212_api.fetch_portfolio_data(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_portfolio_data()
    return tastytrade_api.fetch_portfolio_data(refresh_token=_get_refresh_token())


def fetch_account_balances():
    if get_active_broker() == "t212":
        return t212_api.fetch_account_balances(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_account_balances()
    return tastytrade_api.fetch_account_balances(refresh_token=_get_refresh_token())


def fetch_margin_requirements():
    # T212 has no margin (cash/no-leverage broker); no TT/IBKR equivalent applies.
    if get_active_broker() == "t212":
        return {}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_requirements()
    return tastytrade_api.fetch_margin_requirements(refresh_token=_get_refresh_token())


def fetch_margin_for_position(ticker, quantity):
    if get_active_broker() == "t212":
        return None
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_for_position(ticker, quantity)
    return tastytrade_api.fetch_margin_for_position(
        ticker, quantity, refresh_token=_get_refresh_token()
    )


def fetch_net_liq_history(time_back="1y"):
    if get_active_broker() == "t212":
        # T212 has no history endpoint; the curve is rebuilt from fills, cash
        # movements and daily closes. See t212_history.
        return t212_api.fetch_net_liq_history(_get_t212_creds(), time_back)
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_net_liq_history(time_back=time_back)
    return tastytrade_api.fetch_net_liq_history(
        time_back=time_back, refresh_token=_get_refresh_token()
    )


def fetch_portfolio_greeks():
    # T212 is options-only-free (equities only for now); no Greeks to report.
    if get_active_broker() == "t212":
        return {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_portfolio_greeks()
    return tastytrade_api.fetch_portfolio_greeks(refresh_token=_get_refresh_token())


def fetch_greeks_and_bwd():
    if get_active_broker() == "t212":
        return fetch_portfolio_greeks(), fetch_beta_weighted_delta()
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_greeks_and_bwd()
    return tastytrade_api.fetch_greeks_and_bwd(refresh_token=_get_refresh_token())


def fetch_beta_weighted_delta():
    if get_active_broker() == "t212":
        return {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_beta_weighted_delta()
    return tastytrade_api.fetch_beta_weighted_delta(refresh_token=_get_refresh_token())


def fetch_yearly_transfers():
    if get_active_broker() == "t212":
        return t212_api.fetch_yearly_transfers(_get_t212_creds())
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_yearly_transfers()
    return tastytrade_api.fetch_yearly_transfers(refresh_token=_get_refresh_token())


def fetch_margin_interest():
    if get_active_broker() == "t212":
        return {"current_month": 0, "ytd": 0, "total": 0, "monthly": {}}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_interest()
    return tastytrade_api.fetch_margin_interest(refresh_token=_get_refresh_token())


def fetch_option_chain(
    ticker,
    option_type="Put",
    min_dte=7,
    max_dte=60,
    num_strikes=8,
    fallback_price=0.0,
):
    if get_active_broker() == "t212":
        return {"underlying_price": fallback_price, "expirations": []}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_option_chain(
            ticker,
            option_type=option_type,
            min_dte=min_dte,
            max_dte=max_dte,
            num_strikes=num_strikes,
            fallback_price=fallback_price,
        )
    return tastytrade_api.fetch_option_chain(
        ticker,
        option_type=option_type,
        min_dte=min_dte,
        max_dte=max_dte,
        num_strikes=num_strikes,
        fallback_price=fallback_price,
        refresh_token=_get_refresh_token(),
    )


# Tastytrade's market metrics cover US listings only, so a European line
# looked up under its home ticker (RMS.PA) gets nothing. Its US-listed ADR is
# the same company with the same report date, and Tastytrade does carry
# those (checked 2026-09-22: HESAY, EADSY, RNMBY, ATDRY all answer). Looked
# up under the proxy, reported under the ticker the page asked about.
EARNINGS_PROXY = {
    "RMS.PA": "HESAY",   # Hermes
    "AIR.PA": "EADSY",   # Airbus
    "RHM.DE": "RNMBY",   # Rheinmetall
    "AUTO.L": "ATDRY",   # Auto Trader
    "RMV.L": "RTMVY",    # Rightmove
    "ENX.PA": "EUXTF",   # Euronext
}


def fetch_earnings_dates(tickers):
    tickers = list(tickers)
    proxied = [EARNINGS_PROXY.get(t, t) for t in tickers]
    raw = _fetch_earnings_dates_raw(proxied)
    return {t: raw.get(p) for t, p in zip(tickers, proxied)}


def _fetch_earnings_dates_raw(tickers):
    if get_active_broker() == "t212":
        return {t: None for t in tickers}
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_earnings_dates(tickers)
    return tastytrade_api.fetch_earnings_dates(
        tickers, refresh_token=_get_refresh_token()
    )


# ---------------------------------------------------------------------------
# Multi-broker aggregation
# ---------------------------------------------------------------------------

BROKER_NAMES = {
    "tastytrade": "Tastytrade",
    "ibkr": "Interactive Brokers",
    "t212": "Trading 212",
}


def connected_brokers():
    """Return the connected brokers, in a stable display order."""
    out = []
    if st.session_state.get("tt_refresh_token"):
        out.append("tastytrade")
    if st.session_state.get("ibkr_credentials"):
        out.append("ibkr")
    if st.session_state.get("t212_credentials"):
        out.append("t212")
    return out


def _prime_credentials(brokers):
    """Resolve every broker's credentials on the calling thread.

    st.session_state is unreadable from a worker thread — it silently returns
    None rather than raising — and the getters below fall back to a module
    cache that only a main-thread call can fill. Touching them here means the
    workers find the token instead of falling through to a stale one in
    st.secrets, which Tastytrade answers by revoking the whole grant chain.
    """
    if "tastytrade" in brokers:
        _get_refresh_token()
    if "t212" in brokers:
        _get_t212_creds()
    if "ibkr" in brokers:
        _get_ibkr()


def _in_parallel(brokers, fn):
    """Run `fn(broker)` for every broker at once.

    Returns [(broker, ok, value_or_exception)] in the order given, so a caller
    can keep its display order. They used to run one after another, which made
    a cold portfolio load the sum of both brokers rather than the slower of
    them — 3.5s where 2.1s would do.

    Failures are returned, not raised: one unreachable broker must leave the
    other's positions on screen, with the caller saying the totals are short.
    """
    if not brokers:
        return []
    out, start = {}, time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(brokers)) as pool:
        futures = {pool.submit(fn, b): b for b in brokers}
        for future in as_completed(futures):
            b = futures[future]
            try:
                out[b] = (True, future.result())
            except Exception as e:
                out[b] = (False, e)
    logger.debug("%s across %d broker(s) in %.1fs", getattr(fn, "__name__", fn),
                 len(brokers), time.perf_counter() - start)
    return [(b, out[b][0], out[b][1]) for b in brokers if b in out]


def _fetch_one(broker):
    if broker == "t212":
        return t212_api.fetch_portfolio_data(_get_t212_creds())
    if broker == "ibkr":
        return _get_ibkr().fetch_portfolio_data()
    return tastytrade_api.fetch_portfolio_data(refresh_token=_get_refresh_token())


def fetch_all_portfolio_data():
    """Return (cost_basis, account_id, failures) across every connected broker.

    Positions stay separate per broker. Holding the same ticker at two brokers
    mid-transfer is a real state, and blending the two cost bases would print a
    purchase price that was never paid; two rows is what actually happened. On
    a collision BOTH keys get the broker suffix — a bare "DECK" sitting next to
    "DECK (Trading 212)" reads as though the first row belonged to no broker.

    Because the dict key is therefore a display key, each row carries "symbol"
    (the bare ticker) for price, logo and config lookups.

    `failures` is [(broker_name, exception)] for brokers that could not be
    reached. Their rows are simply absent, so any total struck from this data
    is incomplete — the caller has to say so rather than present a smaller
    number as the truth.
    """
    per_broker, failures = {}, []
    brokers = connected_brokers()
    _prime_credentials(brokers)
    for broker, ok, value in _in_parallel(brokers, _fetch_one):
        if ok:
            cb, acct = value
            per_broker[broker] = (cb or {}, acct)
        else:
            failures.append((BROKER_NAMES[broker], value))

    counts = {}
    for cb, _ in per_broker.values():
        for ticker in cb:
            counts[ticker] = counts.get(ticker, 0) + 1

    merged = {}
    for broker, (cb, _) in per_broker.items():
        name = BROKER_NAMES[broker]
        for ticker, data in cb.items():
            row = dict(data)
            row["broker"] = name
            row["symbol"] = ticker
            key = ticker if counts[ticker] == 1 else f"{ticker} ({name})"
            merged[key] = row

    active = get_active_broker()
    account_id = per_broker.get(active, (None, ""))[1]
    if not account_id and per_broker:
        account_id = next(iter(per_broker.values()))[1]
    return merged, account_id, failures


def _day_key(stamp):
    """The calendar day a net-liq point belongs to, as YYYY-MM-DD.

    Accepts what either broker hands back: a date string, an ISO timestamp, or
    a datetime object from the Tastytrade SDK.
    """
    if hasattr(stamp, "strftime"):
        return stamp.strftime("%Y-%m-%d")
    return str(stamp)[:10]


def merge_net_liq_series(series_list):
    """Add per-broker account curves into one, on the union of their dates.

    Each broker reports on its own grid — Tastytrade snapshots when it feels
    like it, Trading 212 one point per calendar day — so a series carries its
    last value forward across a date it did not print on. Treating that gap as
    zero would drop a whole account out of the curve for a weekend.

    An account counts only from its own first point. Back-filling it would
    invent money that was not there yet; back-filling zero says the same thing
    more quietly and skews every return computed off the curve.
    """
    series_list = [s for s in series_list if s]
    if not series_list:
        return []
    if len(series_list) == 1:
        return series_list[0]

    # Key on the calendar day, not on the string each broker happens to send:
    # Tastytrade stamps a timestamp and Trading 212 a bare date, so compared as
    # text they are different days and the union doubled up. Several snapshots
    # on one day collapse to the last — summing them would count the account
    # once per sample.
    lookups = []
    for series in series_list:
        by_day = {}
        for point in series:
            by_day[_day_key(point["time"])] = point["close"]
        lookups.append(by_day)

    dates = sorted({day for lu in lookups for day in lu})
    firsts = [min(lu) for lu in lookups]

    out, last = [], [None] * len(lookups)
    for day in dates:
        total = 0.0
        for i, lu in enumerate(lookups):
            if day in lu:
                last[i] = lu[day]
            if day < firsts[i] or last[i] is None:
                continue          # this account did not exist yet
            total += last[i]
        out.append({"time": day, "close": total})
    return out


def merge_yearly_transfers(transfer_dicts):
    """Add per-broker deposit histories.

    Money moved between your own brokers is a withdrawal at one and a deposit
    at the other; summed, the pair cancels — which is the honest answer, since
    no new money entered.
    """
    out: dict = {}
    for d in transfer_dicts:
        for year, entry in (d or {}).items():
            year_out = out.setdefault(year, {"total": 0.0, "months": {}})
            year_out["total"] += entry.get("total") or 0.0
            for month, amount in (entry.get("months") or {}).items():
                year_out["months"][month] = (
                    year_out["months"].get(month, 0.0) + amount
                )
    return out


TIME_BACK_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "all": None}


def slice_net_liq(series, time_back):
    """The trailing `time_back` window of a curve already in hand.

    The Results page fetched "all" and then fetched the selected period
    separately, rebuilding from the same fills a second time — 3.65s of a
    10.35s load, for points it was already holding. "all" contains every
    shorter window by definition, so the window comes from there.

    An unknown period returns the series untouched: showing more history than
    was asked for is cosmetic, showing none is a broken chart.
    """
    if not series:
        return []
    days = TIME_BACK_DAYS.get(time_back)
    if days is None:
        return list(series)
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    return [p for p in series if _day_key(p.get("time")) >= cutoff]


def fetch_all_net_liq_history(time_back="1y"):
    """The combined account curve across every connected broker."""
    def _one(broker):
        if broker == "t212":
            return t212_api.fetch_net_liq_history(_get_t212_creds(), time_back)
        if broker == "ibkr":
            return _get_ibkr().fetch_net_liq_history(time_back=time_back)
        return tastytrade_api.fetch_net_liq_history(
            time_back=time_back, refresh_token=_get_refresh_token())

    brokers = connected_brokers()
    _prime_credentials(brokers)
    series = []
    for broker, ok, value in _in_parallel(brokers, _one):
        if ok:
            series.append(value)
        else:
            logger.warning("Net liq history failed for %s: %s", broker, value)
    return merge_net_liq_series(series)


def fetch_all_yearly_transfers():
    """Combined deposits across every connected broker."""
    def _one(broker):
        if broker == "t212":
            return t212_api.fetch_yearly_transfers(_get_t212_creds())
        if broker == "ibkr":
            return _get_ibkr().fetch_yearly_transfers()
        return tastytrade_api.fetch_yearly_transfers(
            refresh_token=_get_refresh_token())

    brokers = connected_brokers()
    _prime_credentials(brokers)
    out = []
    for broker, ok, value in _in_parallel(brokers, _one):
        if ok:
            out.append(value)
        else:
            logger.warning("Transfers failed for %s: %s", broker, value)
    return merge_yearly_transfers(out)


def fetch_all_balances():
    """Return ({broker_name: balances}, failures) across every connected broker.

    Same caveat as fetch_all_portfolio_data: a broker in `failures` contributes
    nothing, so any total struck from this is a floor, not the answer.
    """
    def _one(broker):
        if broker == "t212":
            return t212_api.fetch_account_balances(_get_t212_creds())
        if broker == "ibkr":
            return _get_ibkr().fetch_account_balances()
        return tastytrade_api.fetch_account_balances(
            refresh_token=_get_refresh_token()
        )

    per_broker, failures = {}, []
    brokers = connected_brokers()
    _prime_credentials(brokers)
    for broker, ok, value in _in_parallel(brokers, _one):
        if ok:
            per_broker[BROKER_NAMES[broker]] = value or {}
        else:
            failures.append((BROKER_NAMES[broker], value))
    return per_broker, failures


def fetch_all_net_liq():
    """Return (total, {broker_name: net_liq}, failures) across all brokers."""
    per_broker, failures = fetch_all_balances()
    values = {
        name: (bal.get("net_liquidating_value") or 0.0)
        for name, bal in per_broker.items()
    }
    return sum(values.values()), values, failures


# ---------------------------------------------------------------------------
# Shared functions (broker-independent, always route to tastytrade_api)
# ---------------------------------------------------------------------------

def fetch_current_prices(tickers, isin_by_ticker=None):
    """Current prices, broker feed first and Yahoo only for what it missed.

    Yahoo used to be the whole story here. It throttles by source IP and has
    been widening the datacenter ranges it blocks — Cloud Run in June, the
    Streamlit Cloud app by September — and no amount of retrying fixes being
    blocked for where you are. The broker feed is authenticated, so it is not
    subject to that, and it covers the US listings that are most of any
    watchlist. Yahoo still handles what the broker does not carry, chiefly
    European lines, and the caller falls back to the stored price from there.

    Except that "Yahoo handles the rest" only holds where Yahoo answers at all.
    It blocks by source IP, and from the blocked ones the European lines were
    the only names with no second source — so they quietly kept the price from
    their last config write. On 2026-09-09 the watchlist showed Hermes at
    1613,50 while the market was at 1412, and nothing said the number was three
    weeks old.

    Deutsche Boerse is that second source, for any ticker whose config carries
    an ISIN — pass `isin_by_ticker` to enable it. It runs only on what the
    first two missed, so from an IP Yahoo still serves this never fires.
    """
    tickers = list(tickers)
    if not tickers:
        return {}

    out = {}
    if st.session_state.get("tt_refresh_token"):
        try:
            out = tastytrade_api.fetch_quotes_via_broker(
                tickers, refresh_token=_get_refresh_token())
        except Exception as e:
            logger.warning("Broker quotes failed, falling back to Yahoo: %s", e)

    missing = [t for t in tickers if not out.get(t)]
    if missing:
        out.update(tastytrade_api.fetch_current_prices(missing))

    # Wat na broker én Yahoo nog ontbreekt: in de praktijk de Europese lijnen.
    # Alleen namen met een ISIN, want de beurs kent geen tickers.
    still_missing = {t: (isin_by_ticker or {}).get(t) for t in tickers
                     if not out.get(t) and (isin_by_ticker or {}).get(t)}
    if still_missing:
        import quotes
        out.update({t: q for t, q
                    in quotes.fetch_frankfurt_quotes(still_missing).items() if q})
    return {t: out.get(t) for t in tickers}


def fetch_ticker_profiles(tickers):
    return tastytrade_api.fetch_ticker_profiles(tickers)


def fetch_benchmark_returns():
    return tastytrade_api.fetch_benchmark_returns()


def fetch_benchmark_monthly_returns():
    return tastytrade_api.fetch_benchmark_monthly_returns()


def fetch_sp500_yearly_returns():
    return tastytrade_api.fetch_sp500_yearly_returns()
