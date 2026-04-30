"""
Broker Adapter — routes all broker API calls to the active broker backend.

Delegates to either tastytrade_api or ibkr_api based on
st.session_state["active_broker"]. Callers never pass refresh tokens
or credentials; the adapter handles that internally.
"""

import streamlit as st
import tastytrade_api


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
    # If both are connected the sidebar switcher should be shown so the
    # user picks explicitly; default to tastytrade until they do.
    has_tt = bool(st.session_state.get("tt_refresh_token"))
    has_ibkr = bool(st.session_state.get("ibkr_credentials"))
    if has_ibkr and not has_tt:
        return "ibkr"
    if has_tt and not has_ibkr:
        return "tastytrade"
    # Both connected — default to tastytrade (sidebar switcher lets user change)
    return "tastytrade"


def has_active_broker():
    """Return True if the user has at least one broker connected."""
    return bool(
        st.session_state.get("tt_refresh_token")
        or st.session_state.get("ibkr_credentials")
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


# ---------------------------------------------------------------------------
# Routed broker-specific functions
# ---------------------------------------------------------------------------

def fetch_portfolio_data():
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_portfolio_data()
    return tastytrade_api.fetch_portfolio_data(refresh_token=_get_refresh_token())


def fetch_account_balances():
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_account_balances()
    return tastytrade_api.fetch_account_balances(refresh_token=_get_refresh_token())


def fetch_margin_requirements():
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_requirements()
    return tastytrade_api.fetch_margin_requirements(refresh_token=_get_refresh_token())


def fetch_margin_for_position(ticker, quantity):
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_margin_for_position(ticker, quantity)
    return tastytrade_api.fetch_margin_for_position(
        ticker, quantity, refresh_token=_get_refresh_token()
    )


def fetch_net_liq_history(time_back="1y"):
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_net_liq_history(time_back=time_back)
    return tastytrade_api.fetch_net_liq_history(
        time_back=time_back, refresh_token=_get_refresh_token()
    )


def fetch_portfolio_greeks():
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_portfolio_greeks()
    return tastytrade_api.fetch_portfolio_greeks(refresh_token=_get_refresh_token())


def fetch_greeks_and_bwd():
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_greeks_and_bwd()
    return tastytrade_api.fetch_greeks_and_bwd(refresh_token=_get_refresh_token())


def fetch_beta_weighted_delta():
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_beta_weighted_delta()
    return tastytrade_api.fetch_beta_weighted_delta(refresh_token=_get_refresh_token())


def fetch_yearly_transfers():
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_yearly_transfers()
    return tastytrade_api.fetch_yearly_transfers(refresh_token=_get_refresh_token())


def fetch_margin_interest():
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


def fetch_earnings_dates(tickers):
    if get_active_broker() == "ibkr":
        return _get_ibkr().fetch_earnings_dates(tickers)
    return tastytrade_api.fetch_earnings_dates(
        tickers, refresh_token=_get_refresh_token()
    )


# ---------------------------------------------------------------------------
# Shared functions (broker-independent, always route to tastytrade_api)
# ---------------------------------------------------------------------------

def fetch_current_prices(tickers):
    return tastytrade_api.fetch_current_prices(tickers)


def fetch_ticker_profiles(tickers):
    return tastytrade_api.fetch_ticker_profiles(tickers)


def fetch_benchmark_returns():
    return tastytrade_api.fetch_benchmark_returns()


def fetch_benchmark_monthly_returns():
    return tastytrade_api.fetch_benchmark_monthly_returns()


def fetch_sp500_yearly_returns():
    return tastytrade_api.fetch_sp500_yearly_returns()
