"""
Streamlit web app for Stock Analysis tools — v2.
- DCF Valuation Model Generator
- Holdings, results and track record across Tastytrade, IBKR and Trading 212
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import io
import logging
import os
import sys
import contextlib
import time
from datetime import date, datetime, timedelta
from collections import defaultdict
import re

logger = logging.getLogger(__name__)

import aspirant
import moat_cards
import risk_cards
import business_cards
import company_profile
import business_revenue
import overview_chart
import overview_metrics
import overview_page
import price_history
from error_logger import log_error, log_error_with_trace
from dcf_calculator import (compute_wacc, compute_intrinsic_value, compute_reverse_dcf,
                            DEFAULT_DISCOUNT_MODE, DEFAULT_HURDLE_RATE)
from valuation_lenses import FORWARD_LENSES
from thesis import thesis_vs_history, HEROIC_RATIO
from config_store import ASSUMPTION_LOG_KEY, save_config, load_config, load_all_configs, list_watchlist, remove_from_watchlist, load_user_prefs, save_user_prefs, load_credential, delete_credential, load_ibkr_credentials, save_ibkr_credentials, delete_ibkr_credentials, load_t212_credentials, save_t212_credentials, delete_t212_credentials, log_page_view
import gather_data
from gather_data import (
    get_cik,
    fetch_company_submissions,
    fetch_company_facts,
    parse_financials,
    fetch_stock_price,
    RISK_FREE_RATE_DEFAULT,
    fetch_historical_prices,
    fetch_treasury_yield,
    synthetic_credit_rating,
    fetch_sector_betas,
    fetch_sector_margins,
    fetch_sector_s2c,
    fetch_peer_data,
    build_config,
    resolve_sector_betas,
    TERMINAL_GROWTH_DEFAULT,
    MARGIN_OF_SAFETY_DEFAULT,
    fetch_fundamentals,
    apply_fundamentals_overrides,
    fetch_income_statement,
    fetch_cashflow_statement,
)
from broker_adapter import (
    _day_key,
    fetch_current_prices, fetch_account_balances,
    fetch_net_liq_history, fetch_benchmark_returns,
    fetch_ticker_profiles, fetch_yearly_transfers, fetch_margin_requirements,
    fetch_earnings_dates, has_active_broker, get_active_broker,
    fetch_benchmark_monthly_returns,
    fetch_all_portfolio_data, fetch_all_balances, connected_brokers, BROKER_NAMES,
    fetch_all_net_liq_history, fetch_all_yearly_transfers, slice_net_liq,
)
import load_profiler
import plotly.graph_objects as go
from portfolio_metrics import (compute_deployment, display_basis, has_option_legs,
                               merge_by_symbol,
                               held_share_cost, fifo_realized, open_lots,
                               relative_performance,
                               lots_cover,
                               average_buy_price, hindsight, position_start,
                               DEFAULT_TARGET_POS_PCT, track_record)
from prescan_render import parse_verdict_section, gauge_fraction, band_tone
from scorecard_utils import (compute_roce_metric, capital_employed, roce_for_year,
                             slim_fundamentals, slice_is_usable, window_start,
                             ROCE_WINDOW_YEARS, ROCE_CEILING)
from scorecard_utils import parse_scorecard_json as _parse_scorecard_json
from scorecard_utils import prettify_company_name as _prettify_company

# ── Input sanitization ──
def sanitize_ticker(raw: str) -> str | None:
    """Validate and clean a ticker symbol. Returns None if invalid."""
    cleaned = raw.strip().upper()
    if re.match(r'^[A-Z]{1,5}$', cleaned):
        return cleaned
    return None


def _format_relative_time(iso_or_none: str | None) -> str:
    """Convert an ISO-8601 UTC string to "3 days ago" / "just now" / "never"."""
    if not iso_or_none:
        return "never"
    from datetime import datetime, UTC
    try:
        ts = datetime.fromisoformat(iso_or_none.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
    except (ValueError, AttributeError):
        return "unknown"
    delta = datetime.now(UTC) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = secs // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"


def _range_bar_marker_position(price: float, low: float, high: float) -> tuple[float, bool]:
    """Where on a 0-100% bar should the price marker sit?

    Returns (percent, past_high_flag).
    - percent: clamped to [1, 99] when out of range so the marker stays visible
    - past_high_flag: True when price > high (caller may color the marker red)
    - returns (50.0, False) for degenerate or missing inputs
    """
    if not price or not low or not high:
        return 50.0, False
    if high <= low:
        return 50.0, False
    raw = (price - low) / (high - low) * 100.0
    if raw < 0:
        return 1.0, False
    if raw > 100:
        return 99.0, True
    return raw, False


def _render_lens_dots(lenses: dict, theme: dict) -> str:
    """Render N dots showing which forward-looking lenses are active + a count label.

    Order from FORWARD_LENSES: dcf · dividend. Reverse DCF anchors at
    price by definition; multiples + historical demoted off the watchlist
    2026-07-30 (shown in the ticker-page "Multiples" tab) — all excluded here.

    Each lens key maps to a non-None lens dict (active, green dot) or None
    (skipped, grey dot). Hover-tooltip via native `title` attribute shows the
    lens name + active/skipped status. Label: "{N} lens" or "{N} lenses".
    """
    order = list(FORWARD_LENSES)  # (key, display_label) tuples
    actives = [key for key, _ in order if lenses.get(key) is not None]

    parts = []
    for key, label in order:
        is_active = lenses.get(key) is not None
        cls = "ld-on" if is_active else "ld-off"
        status = "active" if is_active else "skipped"
        # Use data-label for custom CSS tooltip + title as fallback for accessibility
        parts.append(
            f'<span class="{cls}" data-label="{label}: {status}" '
            f'title="{label}: {status}"></span>'
        )

    n = len(actives)
    if n == 0:
        label = "no lenses"
    elif n == 1:
        label = "1 lens"
    else:
        label = f"{n} lenses"

    color = theme.get("text_muted", "#888")
    return (
        f'<div style="font-size:0.7rem;color:{color};margin-top:1px">'
        f'{"".join(parts)} {label}</div>'
    )


def _fmt_fv_dollar(x: float, symbol: str = "$") -> str:
    """Format a money value for the FV cell — integer if >= 100, else 2dp."""
    if x is None:
        return "—"
    if abs(x) >= 100:
        return f"{symbol}{x:.0f}"
    return f"{symbol}{x:.2f}"


# Per rij één munt. Een aandeel wordt gekocht, gewaardeerd en genoteerd in
# zijn eigen munt: koers, fair value en koopdoel van Hermes staan in euro's,
# en zo vergelijk je ze ook bij een order. Alleen de totalen op de
# portfolio- en Results-pagina zijn in dollars, want daar tel je posities uit
# twee munten bij elkaar op. Tot 2026-09-22 stond hier overal een dollarteken,
# ook voor "$1351.50" Hermes, wat gewoon euro's waren.
#
# De config draagt soms een `currency`; anders zegt het tickersuffix genoeg.
# Londen noteert in pence: de koers deelt door honderd, want de DCF rekent in
# ponden en anders staat de upside een factor honderd naast de waarheid.
_SUFFIX_CURRENCY = {
    "PA": "EUR", "DE": "EUR", "AS": "EUR", "MI": "EUR", "F": "EUR", "BR": "EUR",
    "MC": "EUR", "VI": "EUR", "HE": "EUR", "LS": "EUR", "IR": "EUR",
    "L": "GBp", "SW": "CHF", "ST": "SEK", "CO": "DKK", "OL": "NOK", "TO": "CAD",
}
_CURRENCY_SYMBOL = {
    "USD": "$", "EUR": "€", "GBP": "£", "CHF": "CHF ", "SEK": "kr ", "DKK": "kr ",
    "NOK": "kr ", "CAD": "C$",
}


def _row_currency(ticker: str, cfg: dict | None = None) -> tuple[str, str, float]:
    """(valutacode, symbool, koersschaal) voor één watchlistrij.

    De schaal is wat een binnenkomende koers moet vermenigvuldigen om in de
    munt van de rij te staan: 0,01 voor Londense pence, anders 1.
    """
    code = ((cfg or {}).get("currency") or "").upper()
    suffix = ticker.rsplit(".", 1)[1].upper() if "." in ticker else ""
    if not code:
        code = _SUFFIX_CURRENCY.get(suffix, "USD")
    scale = 1.0
    # Een config die "GBP" zegt bij een Londense notering: de koers zelf komt
    # van de beurs nog steeds in pence.
    if code == "GBX" or (code == "GBP" and suffix == "L"):
        code, scale = "GBP", 0.01
    return code, _CURRENCY_SYMBOL.get(code, code + " "), scale


def _strategy_start():
    """De dag waarop de huidige strategie begon, of None.

    Eén instelling, in user_prefs, die op twee plekken werkt: de periode
    "Since ..." op Results en de splitsing van het Track record op Holdings.
    Voor deze gebruiker 2026-07-28: de dag dat de instroom in de huidige
    namen begon, na de verkoop van de laatste wheel-posities.
    """
    if "_strategy_start" not in st.session_state:
        raw = load_user_prefs(_sb_client).get("strategy_start")
        try:
            st.session_state["_strategy_start"] = date.fromisoformat(raw) if raw else None
        except (TypeError, ValueError):
            st.session_state["_strategy_start"] = None
    return st.session_state["_strategy_start"]


def _set_strategy_start(day):
    prefs = load_user_prefs(_sb_client)
    prefs["strategy_start"] = day.isoformat() if day else None
    save_user_prefs(_sb_client, prefs)
    st.session_state["_strategy_start"] = day


def _dietz_return(series, transfers, start, end=None):
    """Stortingsgecorrigeerd rendement over [start, end] in procenten, of None.

    Dezelfde Simple Dietz als de jaar- en maandcijfers: (eind - begin -
    netto stortingen) / (begin + halve stortingen). Stortingen per maand
    tellen mee als hun maand in het venster valt, de startmaand alleen als
    het venster voor de vijftiende begint.
    """
    if not series:
        return None
    pts = sorted((_day_key(pt["time"]), pt["close"]) for pt in series)
    in_window = [(d, c) for d, c in pts if d >= start.isoformat()
                 and (end is None or d <= end.isoformat())]
    if len(in_window) < 2:
        return None
    start_v, end_v = in_window[0][1], in_window[-1][1]
    net_dep = 0.0
    for yr, entry in (transfers or {}).items():
        for mo, amount in ((entry or {}).get("months") or {}).items():
            first = date(int(yr), int(mo), 1)
            start_month = date(start.year, start.month, 1)
            counts = first > start_month or (first == start_month and start.day <= 15)
            if counts and (end is None or first <= end):
                net_dep += amount
    denom = start_v + 0.5 * net_dep
    if denom <= 0:
        return None
    return (end_v - start_v - net_dep) / denom * 100


def _track_record_rows(cost_basis: dict, index_closes: dict, today,
                       since=None, before=None) -> list:
    """Track record per naam, met de dollarmaat erbij, of [] zonder index.

    Gedeeld door Holdings (de tabel per naam) en Results (het totaal), zodat
    de twee pagina's nooit een ander getal noemen voor hetzelfde ding.
    """
    if not index_closes:
        return []
    rows = []
    for t, d in cost_basis.items():
        r = track_record(d.get("trades") or [], d.get("current_price") or 0.0,
                         index_closes, today,
                         option_pl=d.get("option_pl") or 0.0,
                         dividends=d.get("dividends") or 0.0,
                         since=since, before=before)
        if r["alpha"] is None:
            continue
        r["ticker"] = d.get("symbol", t)
        r["isin"] = d.get("isin")
        r["alpha_usd"] = r["cost"] * r["alpha"] / 100
        r["total_alpha_usd"] = r["cost"] * r["total_alpha"] / 100
        rows.append(r)
    return rows


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_spy_closes(years: int = 5) -> dict:
    return gather_data.fetch_daily_closes("SPY", years)


# Overview tab loaders. Exceptions propagate on purpose: st.cache_data never
# caches a raised exception, so an EDGAR or Supabase hiccup retries on the
# next rerun instead of blanking the figures for the whole TTL. The EDGAR
# fetchers swallow their own errors into {'years': []}, so require_years turns
# that back into an exception. The Overview block catches them at the call site.
@st.cache_data(ttl=86400, show_spinner=False)
def _overview_income(ticker):
    return overview_metrics.require_years(
        fetch_income_statement(ticker, n_years=11), "income statement")


@st.cache_data(ttl=86400, show_spinner=False)
def _overview_cashflow(ticker):
    return overview_metrics.require_years(
        fetch_cashflow_statement(ticker, n_years=11), "cash flow statement")


@st.cache_data(ttl=3600, show_spinner=False)
def _overview_prices(ticker, since_iso):
    """Daily closes for the ticker and SPY from the price_history table, keyed
    on (ticker, since). The Supabase client is not hashable, so it is read
    from session state (KeyError when it is missing)."""
    client = st.session_state["supabase_client"]
    return price_history.load_series(client, [ticker, "SPY"],
                                     date.fromisoformat(since_iso))


def _merge_track_rows(rows: list) -> list:
    """Eén regel per symbool, uit rijen die per broker zijn gerekend.

    Rekenen gebeurt per brokerrekening, want daar loopt de FIFO: een
    verkoop bij Tastytrade verbruikt geen lot van Trading 212. Op de
    samengevoegde rij (NVDA bij twee brokers als één) deed hij dat wel, en
    Holdings noemde daardoor een ander totaal dan de pil op Results. Optellen
    kan pas na het rekenen: dollars zijn optelbaar, punten en dagen worden
    opnieuw op de kosten gewogen.
    """
    by = {}
    for r in rows:
        m = by.setdefault(r["ticker"], {"ticker": r["ticker"], "isin": r.get("isin"),
                                        "cost": 0.0, "alpha_usd": 0.0,
                                        "total_alpha_usd": 0.0, "_days": 0.0,
                                        "_ret": 0.0, "_idx": 0.0,
                                        "closed": True, "since_sale": None})
        m["isin"] = m["isin"] or r.get("isin")
        m["cost"] += r["cost"]
        m["alpha_usd"] += r["alpha_usd"]
        m["total_alpha_usd"] += r["total_alpha_usd"]
        m["_days"] += r["cost"] * (r["days_held"] or 0)
        m["_ret"] += r["cost"] * (r.get("total_return") or 0.0)
        m["_idx"] += r["cost"] * (r.get("index_return") or 0.0)
        m["closed"] = m["closed"] and r["closed"]
        m["since_sale"] = m["since_sale"] or r.get("since_sale")
    out = []
    for m in by.values():
        if m["cost"] <= 0:
            continue
        m["alpha"] = m["alpha_usd"] / m["cost"] * 100
        m["total_alpha"] = m["total_alpha_usd"] / m["cost"] * 100
        m["days_held"] = round(m.pop("_days") / m["cost"])
        m["total_return"] = m.pop("_ret") / m["cost"]
        m["index_return"] = m.pop("_idx") / m["cost"]
        out.append(m)
    return out


def _track_record_pill_html(rows: list, theme: dict, label: str = "vs SPY") -> str:
    """De ene stat-pil die zegt wat je had kunnen hebben: "vs SPY -$25,661".

    De strategie-maat, want de premie en het dividend zijn werkelijk
    ontvangen. De opbouw staat in de tooltip: de koers-alleen maat is uitleg
    waar het gat vandaan komt, geen tweede kop. Hoort in de rij naast
    Portfolio Value en CAGR; als losse alinea onder een grafiek las het als
    een voetnoot met een te grote kop.
    """
    if not rows:
        return ""
    import html as _html
    total = sum(r["total_alpha_usd"] for r in rows)
    stock = sum(r["alpha_usd"] for r in rows)
    cost = sum(r["cost"] for r in rows) or 1.0
    pts = total / cost * 100
    won_back = total - stock
    color = theme["accent"] if total >= 0 else theme["red"]
    tip = (
        f"Same money, same days, in SPY instead of what you bought: "
        f"{'you have' if total >= 0 else 'you could have had'} ${abs(total):,.0f} more. "
        f"Stock picks alone {'beat' if stock >= 0 else 'trailed'} SPY by ${abs(stock):,.0f}; "
        f"option premium and dividends {'won back' if won_back >= 0 else 'cost'} "
        f"${abs(won_back):,.0f}. Every position ever held, each lot over its own days. "
        f"SPY price return, last five years."
    )
    sign = "+" if total >= 0 else "-"
    return (f'<span class="stat-pill" title="{_html.escape(tip, quote=True)}" '
            f'style="cursor:help">{label} <b style="color:{color}">{sign}${abs(total):,.0f}</b> '
            f'<span style="color:{color}">({pts:+.0f} pts)</span></span>')


def _resolve_watchlist_price(cfg: dict,
                             live_price: float | None) -> tuple[float, bool]:
    """Return (price, is_stale) for one watchlist row.

    Yahoo throttles Streamlit Cloud's datacenter IP, so quotes go missing in
    normal operation. Falling back to 0 was worse than it looked: the price
    column blanked, but upside, P/E and FCF-yield are all computed from this
    number and collapsed with it, so a throttled row read as a real verdict.
    The config carries the price from its last refresh — show that, flagged.
    """
    if live_price and live_price > 0:
        return float(live_price), False
    stored = cfg.get('stock_price') or 0.0
    return (float(stored) if stored > 0 else 0.0), True


def _latest_fcf_yield(fund: dict, equity_market_value: float | None,
                      live_price: float | None) -> float | None:
    """Trailing FCF yield from the most recent year that reports FCF.

    Per-share when that same year also reports a share count: compacting the
    fcf and shares lists separately and taking [-1] of each pairs a year's FCF
    with whatever year last reported shares, which silently mixes fiscal years.
    Otherwise falls back to FCF / equity market value — some filers (V) never
    tag a share count in XBRL at all.

    Returns None when there is no FCF, or no way to scale it.
    """
    fcf = fund.get("fcf") or []
    shares = fund.get("shares") or []

    latest = next((i for i in range(len(fcf) - 1, -1, -1) if fcf[i] is not None), None)
    if latest is None:
        return None

    # The most recent cover-page count first, because it is on the same basis
    # as the price. A fiscal-year figure is not: Booking split ~25-for-1 in
    # April 2026, so its FY2025 count of 33M divided a full year's cash flow
    # into 25x too few shares and, against a post-split $210, reported a 133%
    # yield. The split detector could not catch it — the post-split number
    # arrived on a 10-Q and never entered the annual series.
    shares_same_year = fund.get("shares_latest") or (
        shares[latest] if latest < len(shares) else None)
    # Een IFRS-filer zonder dollarvertaling (Ferrari) levert FCF in zijn
    # eigen munt; de koers en de marktwaarde zijn dollars. Eerst omrekenen,
    # anders is de yield een factor wisselkoers naast de waarheid.
    fund_ccy = (fund.get("currency") or "USD").upper()
    if fund_ccy != "USD":
        from gather_data import fetch_fx_rate
        rate = fetch_fx_rate(fund_ccy)
        if not rate:
            return None
        fcf = [v * rate if v is not None else None for v in fcf]
    if live_price and live_price > 0 and shares_same_year:
        # fcf is in $M, shares is a RAW count — fetch_fundamentals says so in
        # its own docstring. parse_financials stores shares in millions and
        # fetch_fundamentals multiplies them back up, so the two sources of
        # "fundamentals" in this codebase deliberately disagree. Reading the
        # wrong one and dropping this factor put every EDGAR-derived yield at
        # 0.0%.
        return (fcf[latest] * 1e6 / shares_same_year) / live_price

    if equity_market_value and equity_market_value > 0:
        return fcf[latest] / equity_market_value  # both in $M
    return None


def _apply_wacc_persistence(cfg: dict, wacc_list: list, tv_wacc: float,
                            default_wacc: float) -> tuple[bool, bool]:
    """Persist per-year / terminal WACC only when they deviate from the live
    compute_wacc default, compared at 2-decimal-percent display precision (the
    granularity the editor's inputs expose — sidesteps float-rounding false
    positives). Otherwise remove the keys so the discount rate is always taken
    live, preventing frozen-WACC drift after an rf/ERP change.

    Mutates cfg. Returns (wacc_overridden, tv_overridden).
    """
    default_pct = round(default_wacc * 100, 2)
    wacc_overridden = any(round(w * 100, 2) != default_pct for w in wacc_list)
    tv_overridden = round(tv_wacc * 100, 2) != default_pct
    if wacc_overridden:
        cfg['wacc_per_year'] = wacc_list
    else:
        cfg.pop('wacc_per_year', None)
    if tv_overridden:
        cfg['terminal_wacc'] = tv_wacc
    else:
        cfg.pop('terminal_wacc', None)
    return wacc_overridden, tv_overridden


def _render_fv_cell(price: float, summary: dict | None,
                    legacy_intrinsic: float | None, theme: dict,
                    symbol: str = "$") -> str:
    """Return HTML for the Fair Value cell.

    Three render modes:
    - summary present → bold mid · (low–high) · range-bar with marker · lens-dots
    - summary missing, legacy_intrinsic present → bold mid · 'single-lens' badge · hint
    - both missing → em-dash
    """
    text = theme.get("text", "#eee")
    muted = theme.get("text_muted", "#888")

    if summary:
        low = summary.get("weighted_fv_low")
        mid = summary.get("weighted_fv_mid")
        high = summary.get("weighted_fv_high")
        if mid is None or low is None or high is None:
            return f'<span style="color:{muted}">—</span>'

        pct, past_high = _range_bar_marker_position(price, low, high)
        marker_color = "#d96a5a" if past_high else "#fff"
        pct_str = f"{pct:.0f}%" if pct == int(pct) else f"{pct:.1f}%"

        # Watchlist now surfaces the DCF fair value only (2026-07-31): the
        # lens-dots row, the "{N} lenses" label and the "details ›" football-
        # field tooltip were removed — a single lens has nothing to count or
        # drill into, and the tooltip showed nothing not already in the cell.
        return (
            f'<div>'
            f'<strong style="color:{text}">{_fmt_fv_dollar(mid, symbol)}</strong> '
            f'<span style="color:{muted};font-size:0.78rem">'
            f'({_fmt_fv_dollar(low, symbol)}–{_fmt_fv_dollar(high, symbol)})</span>'
            f'<div class="range-bar" style="position:relative;height:6px;'
            f'background:linear-gradient(90deg,#6cc07055,#d8a44855,#d96a5a55);'
            f'border-radius:3px;margin:4px 0 2px 0;min-width:110px">'
            f'<div style="position:absolute;top:-3px;width:2px;height:12px;'
            f'background:{marker_color};box-shadow:0 0 2px rgba(0,0,0,0.6);'
            f'left:{pct_str}"></div>'
            f'</div>'
            f'</div>'
        )

    if legacy_intrinsic is not None:
        return (
            f'<div>'
            f'<strong style="color:{text}">{_fmt_fv_dollar(legacy_intrinsic, symbol)}</strong> '
            f'<span style="font-size:0.65rem;color:{muted};background:#33333355;'
            f'padding:1px 5px;border-radius:3px;margin-left:4px">single-lens</span>'
            f'<div style="font-size:0.72rem;color:{muted};margin-top:4px">'
            f'DCF intrinsic only · run "Refresh all" to compute multi-lens</div>'
            f'</div>'
        )

    return f'<span style="color:{muted}">—</span>'


def _render_robustness_table(cfg: dict, theme: dict) -> str:
    """Render the Prasad robustness assessment as a headline verdict card plus
    one three-state row per axis. Pure HTML-string builder (no Streamlit calls).

    The verdict gets the large selector because it is the answer; the six axes
    get compact ones because they are the working. Three circles rather than a
    marker on a gradient: the bands ARE three states, and a continuous track
    invited reading a position between them that the data does not carry.
    """
    import html as _html

    import robustness as _rob
    from prescan_render import three_state_html

    rob = (cfg or {}).get("robustness") or {}
    axes = rob.get("axes") or {}
    text = theme.get("text", "#111")
    muted = theme.get("text_muted", "#888")
    if not axes:
        return (f'<div style="color:{muted};font-size:0.85rem;margin:6px 0 14px">'
                'Robustness not yet assessed — run the Robustness section.</div>')

    border_light = theme.get("border_light", "#e8e8ed")
    bg = theme.get("bg_secondary", "#f4f2ee")
    font = ("'DM Sans', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', "
            "Arial, sans-serif")

    def _val_label(key, ax):
        if key == "roce" and ax.get("value") is not None:
            return f'{ax["value"]:.0f}% {ax.get("metric", "ROCE")}'
        if key == "net_debt" and ax.get("value") is not None:
            v = ax["value"]
            return "net cash" if v <= 0 else f'{v:.1f}× EBITDA'
        return ax.get("note", "") or ""

    # ── Headline: the verdict, large ──
    verdict = rob.get("verdict", "")
    reason = _html.escape(rob.get("verdict_reason", ""))
    head = (
        f'<div style="background:{bg};border-radius:16px;padding:20px 22px 16px;'
        f'margin-bottom:14px">'
        f'<div style="text-align:center;font-size:0.7rem;font-weight:700;'
        f'letter-spacing:0.09em;color:{muted};text-transform:uppercase;'
        f'margin-bottom:16px">How robust is this business?</div>'
        + three_state_html(verdict, ("Fragile", "Borderline", "Robust"),
                           size=48, theme=theme)
        + (f'<div style="text-align:center;margin-top:16px;padding-top:14px;'
           f'border-top:1px solid {border_light};font-size:0.9rem;'
           f'line-height:1.5;color:{text}">{reason}</div>' if reason else '')
        + '</div>'
    )

    # ── Working: one compact row per axis ──
    rows = []
    for key, label, is_db, _src in _rob.AXES:
        ax = axes.get(key, {})
        note = _html.escape(_val_label(key, ax))
        flag = (f'<span title="deal-breaker" style="color:{muted};'
                f'font-size:0.68rem">&#9873;</span>' if is_db else '')
        rows.append(
            f'<div style="display:flex;align-items:center;gap:14px;'
            f'padding:7px 0;border-top:1px solid {border_light};font-size:0.82rem">'
            f'<div style="width:168px;flex:none;color:{text};white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis">{label} {flag}</div>'
            f'<div style="flex:none">'
            + three_state_html(ax.get("band"), ("", "", ""), size=17, theme=theme)
            + f'</div>'
            f'<div title="{note}" style="flex:1;min-width:0;color:{muted};'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{note}</div>'
            f'</div>'
        )

    legend = ('&#9873; deal-breaker (ROCE &middot; net debt &middot; management) — '
              'a red sinks the verdict, amber caps it at borderline')
    foot = (f'<div style="color:{muted};font-size:0.7rem;margin-top:10px;'
            f'border-top:1px solid {border_light};padding-top:8px">{legend}</div>')
    return (f'<div style="font-family:{font};margin:2px 0 4px">'
            f'{head}{"".join(rows)}{foot}</div>')


def _render_football_field(summary: dict | None, theme: dict,
                           live_price: float | None = None) -> str:
    """Render a football-field HTML block: one horizontal range bar per lens
    + vertical markers for current price, weighted mid, and buy price.

    Used inside an st.popover triggered from the watchlist row. Pure CSS;
    width fixes at ~600px so the popover sizes naturally.

    The Price marker tracks ``live_price`` when a positive value is given,
    falling back to the stored ``summary['stock_price']`` snapshot otherwise.
    ``summary['stock_price']`` is frozen at the last valuation refresh, so
    without this the marker drifts stale versus the live watchlist row.
    """
    text = theme.get("text", "#eee")
    muted = theme.get("text_muted", "#888")
    accent = theme.get("accent", "#6e8a76")
    accent_hover = theme.get("accent_hover", "#5a7561")

    if not summary or not isinstance(summary, dict) or not summary.get("lenses"):
        return (
            f'<div style="color:{muted};font-size:0.85rem;padding:12px">'
            f'No valuation summary available — run "Refresh all" or '
            f'<code>calculate_multi_lens_valuation</code>.'
            f'</div>'
        )

    # lens_order comes from valuation_lenses.FORWARD_LENSES (single source of
    # truth across lens-dots / football-field / _COUNTED_LENSES / CLI).
    # reverse_dcf intentionally omitted there — its bar would overlap the
    # Price marker (lens always returns fv = stock_price). See
    # docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md.
    lens_order = list(FORWARD_LENSES)
    lenses = summary.get("lenses") or {}

    # Live price wins over the frozen snapshot so the marker matches the
    # live watchlist row; snapshot is the fallback when no live price is given.
    price = (live_price if live_price and live_price > 0 else None) \
        or summary.get("stock_price") or 0.0
    mid = summary.get("weighted_fv_mid") or 0.0
    buy = summary.get("buy_price") or 0.0

    all_values = [price, mid, buy]
    for key, _ in lens_order:
        lens = lenses.get(key)
        if lens:
            all_values.extend([lens.get("fv_low") or 0, lens.get("fv_high") or 0])
    all_values = [v for v in all_values if v]
    if not all_values:
        return f'<div style="color:{muted};font-size:0.85rem">No valuation data.</div>'
    g_min, g_max = min(all_values), max(all_values)
    span = max(g_max - g_min, 1e-9)
    pad = span * 0.05
    g_min -= pad
    g_max += pad
    span = g_max - g_min

    def _x(v: float) -> float:
        return ((v - g_min) / span) * 100.0

    bar_rows = []
    for key, label in lens_order:
        lens = lenses.get(key)
        if lens is None:
            bar_rows.append(
                f'<div class="ff-row"><div class="ff-label">{label}</div>'
                f'<div class="ff-bar" style="background:#33333322"></div>'
                f'<div class="ff-range-label" style="color:{muted}">(skipped)</div>'
                f'</div>'
            )
            continue
        # DCF renders as a single point at its mid — under opportunity_cost
        # discounting that mid is the "index break-even" price (return = index),
        # so it reads as one actionable number, not a scenario range.
        dcf_mid = lens.get("fv_mid")
        if key == "dcf" and dcf_mid is not None:
            x_mid = _x(dcf_mid)
            bar_rows.append(
                f'<div class="ff-row">'
                f'<div class="ff-label">{label}</div>'
                f'<div class="ff-bar">'
                f'<div class="ff-point" style="left:{x_mid:.1f}%"></div>'
                f'</div>'
                f'<div class="ff-range-label" style="color:{text}">'
                f'${dcf_mid:.0f} · index break-even</div>'
                f'</div>'
            )
            continue
        low = lens.get("fv_low") or 0
        high = lens.get("fv_high") or 0
        x_low, x_high = _x(low), _x(high)
        width = max(x_high - x_low, 0.5)
        bar_rows.append(
            f'<div class="ff-row">'
            f'<div class="ff-label">{label}</div>'
            f'<div class="ff-bar">'
            f'<div class="ff-range" style="left:{x_low:.1f}%;width:{width:.1f}%"></div>'
            f'</div>'
            f'<div class="ff-range-label" style="color:{text}">${low:.0f} — ${high:.0f}</div>'
            f'</div>'
        )

    # Single marker: current price. Mid and Buy were too noisy alongside
    # the per-lens ranges; the user reads those from the watchlist row directly.
    price_x = _x(price)
    markers_html = (
        f'<div class="ff-marker ff-marker-price" style="left:{price_x:.2f}%">'
        f'  <span class="ff-marker-cap"></span>'
        f'  <span class="ff-marker-label">Price ${price:.2f}</span>'
        f'</div>'
    )

    css = f'''<style>
.ff-container {{ position:relative; width:100%; max-width:560px; padding:24px 4px 4px; }}
.ff-row {{ display:flex; align-items:center; gap:10px; margin-bottom:6px; font-size:0.78rem; }}
.ff-label {{ width:88px; color:{text}; font-weight:500; }}
.ff-bar {{
  position:relative; flex:1; height:14px;
  background:linear-gradient(90deg,#6cc07033,#d8a44833,#d96a5a33);
  border-radius:3px; overflow:hidden;
}}
.ff-range {{
  position:absolute; top:0; bottom:0;
  background:linear-gradient(90deg,#6cc070,#d8a448,#d96a5a);
  border-radius:3px; opacity:0.85;
}}
.ff-point {{
  position:absolute; top:50%; left:0; transform:translate(-50%,-50%);
  width:11px; height:11px; border-radius:50%;
  background:{accent}; box-shadow:0 0 3px rgba(0,0,0,0.45);
}}
.ff-range-label {{ width:120px; font-size:0.72rem; }}
.ff-markers {{
  position:absolute; top:8px; left:98px; right:130px; bottom:0; pointer-events:none;
}}
.ff-marker {{
  position:absolute; top:14px; bottom:0;
  pointer-events:auto;
}}
.ff-marker::before {{
  content:""; position:absolute; top:0; bottom:0; left:-1px; width:3px; border-radius:1px;
}}
.ff-marker-cap {{
  position:absolute; top:-5px; left:-5px; width:11px; height:11px;
  border-radius:50%; border:2px solid {theme.get("card", "#fff")};
  box-shadow:0 1px 3px rgba(0,0,0,0.3);
}}
.ff-marker-label {{
  position:absolute; top:-22px; transform:translateX(-50%); left:0;
  font-size:0.65rem; font-weight:600; white-space:nowrap;
  padding:1px 5px; border-radius:3px;
}}
.ff-marker-price::before {{ background:#444; }}
.ff-marker-price .ff-marker-cap {{ background:#444; }}
.ff-marker-price .ff-marker-label {{ color:white; background:#444; }}
</style>'''

    return (
        f'{css}'
        f'<div class="ff-container">'
        f'{"".join(bar_rows)}'
        f'<div class="ff-markers">{markers_html}</div>'
        f'</div>'
    )


def _ddm_at(ttm: float, g: float, ke: float, g_term: float,
            stage1_years: int = 5) -> float:
    """Two-stage DDM valuation at explicit assumptions.

    Computes PV of stage-1 dividends (D₀ × (1+g)ⁿ discounted at ke for
    n=1..stage1_years) plus PV of Gordon terminal value at end of stage 1.

    Returns float("inf") when ke ≤ g_term (Gordon doesn't converge) so
    callers can render the cell as "—" without raising. No growth cap —
    the lens's 15% cap is upstream; the matrix is exploratory.
    """
    if ke <= g_term:
        return float("inf")

    pv_stage1 = 0.0
    d = ttm
    for n in range(1, stage1_years + 1):
        d = d * (1 + g)
        pv_stage1 += d / ((1 + ke) ** n)

    terminal_value = d * (1 + g_term) / (ke - g_term)
    pv_terminal = terminal_value / ((1 + ke) ** stage1_years)
    return pv_stage1 + pv_terminal


_DIVIDEND_FAIR_THRESHOLD = 0.10


def _dividend_conclusion(lens_mid: float, price: float) -> str:
    """Return one of three conclusion-sentence variants comparing the
    Dividend lens midpoint to the current stock price.

    Threshold: ±10% around price → "fairly priced". Above → undervaluation
    signal. Below → overvaluation signal.

    Returned string is plain text (no HTML); the caller wraps it for
    styling via st.markdown with unsafe_allow_html.
    """
    upper = price * (1 + _DIVIDEND_FAIR_THRESHOLD)
    lower = price * (1 - _DIVIDEND_FAIR_THRESHOLD)

    if lens_mid > upper:
        pct = (lens_mid / price - 1) * 100
        return (
            f"Lens midpoint ${lens_mid:.0f} is {pct:.1f}% above current "
            f"${price:.0f} — potential undervaluation signal."
        )
    if lens_mid < lower:
        pct = (1 - lens_mid / price) * 100
        return (
            f"Lens midpoint ${lens_mid:.0f} is {pct:.1f}% below current "
            f"${price:.0f} — overvaluation signal."
        )
    return (
        f"Lens midpoint ${lens_mid:.0f} ≈ current ${price:.0f} — "
        f"fairly priced."
    )


def _render_dividend_sensitivity_matrix(
    ttm: float,
    g_range: tuple,
    ke_range: tuple,
    g_term: float,
    stage1_years: int,
    price: float,
    theme: dict,
) -> str:
    """Render a DDM sensitivity matrix as an HTML <table>.

    Rows = growth (g₁), columns = cost of equity (ke), cells = DDM FV.
    Cell coloring mirrors the Reverse DCF matrix:
    - Market-implied cell (FV closest to `price`): accent background, bold white
    - Undervalued cells (FV ≥ price): accent_fill (light green) background
    - Overvalued cells (FV < price): red_light (light peach) background
    - Degenerate cells (ke ≤ g_term): "—" with neutral background

    Pure function: returns HTML string. Theme dict must provide
    border_medium/card/text/text_muted/accent/accent_fill/red_light keys.
    """
    g_min, g_max, g_step = g_range
    ke_min, ke_max, ke_step = ke_range

    def _arange(lo, hi, step):
        out = []
        v = lo
        while v <= hi + step * 0.5:
            out.append(round(v, 6))
            v += step
        return out

    g_values = _arange(g_min, g_max, g_step)
    ke_values = _arange(ke_min, ke_max, ke_step)

    fv_grid = {}
    for g in g_values:
        for ke in ke_values:
            fv_grid[(g, ke)] = _ddm_at(
                ttm=ttm, g=g, ke=ke, g_term=g_term, stage1_years=stage1_years
            )

    finite_cells = [k for k, v in fv_grid.items() if v != float("inf")]
    market_implied = (
        min(finite_cells, key=lambda k: abs(fv_grid[k] - price))
        if finite_cells else None
    )

    hdr_style = (
        f"background:{theme['card']};color:{theme['text_muted']};"
        f"font-size:0.7rem;font-weight:600;padding:6px 8px;"
        f"text-align:center;position:sticky;top:0;z-index:1"
    )
    row_hdr_style = (
        f"background:{theme['card']};color:{theme['text']};"
        f"font-size:0.75rem;font-weight:600;padding:6px 8px;"
        f"text-align:left;position:sticky;left:0;z-index:1"
    )

    html = (
        f'<div style="overflow-x:auto;border:1px solid {theme["border_medium"]};'
        f'border-radius:12px;background:{theme["card"]}">'
        f'<table style="border-collapse:collapse;width:100%;font-size:0.75rem">'
    )

    html += f'<thead><tr><th style="{hdr_style};text-align:left">Growth \\ ke</th>'
    for ke in ke_values:
        html += f'<th style="{hdr_style}">{ke:.2%}</th>'
    html += "</tr></thead><tbody>"

    for g in g_values:
        html += f'<tr><td style="{row_hdr_style}">{g:.1%}</td>'
        for ke in ke_values:
            fv = fv_grid[(g, ke)]
            if fv == float("inf"):
                cell_text = "—"
                cell_style = (
                    f"padding:6px 8px;text-align:center;"
                    f"color:{theme['text_muted']};"
                )
            else:
                cell_text = _fmt_fv_dollar(fv)
                if (g, ke) == market_implied:
                    cell_style = (
                        f"background:{theme['accent']};color:#fff;"
                        f"font-weight:700;padding:6px 8px;text-align:center;"
                    )
                elif fv >= price:
                    cell_style = (
                        f"background:{theme['accent_fill']};"
                        f"color:{theme['text']};"
                        f"padding:6px 8px;text-align:center;"
                    )
                else:
                    cell_style = (
                        f"background:{theme['red_light']};"
                        f"color:{theme['text']};"
                        f"padding:6px 8px;text-align:center;"
                    )
            html += f'<td style="{cell_style}">{cell_text}</td>'
        html += "</tr>"

    html += "</tbody></table></div>"
    return html


def _effective_stc(cfg):
    """The sales-to-capital the DCF will actually run on: (per_year, terminal).

    Resolved exactly as dcf_calculator does — stc_per_year wins over the scalar,
    terminal_stc over the last projected year — and pinned to it by test. The
    editor's summary line read the raw scalar instead, so NVDA displayed 8.00
    under a label saying "Used in DCF" while the projection ran on 5.0 from
    stc_per_year. A number on screen has to be the number that was computed.
    """
    n = len(cfg.get('revenue_growth') or [])
    values = cfg.get('stc_per_year') or [cfg.get('sales_to_capital', 1.0)] * n
    values = [float(v) for v in values]
    if values and len(values) < n:
        values = values + [values[-1]] * (n - len(values))
    values = values[:n]
    terminal = float(cfg.get('terminal_stc', values[-1] if values else 1.0))
    return values, terminal


def _sector_beta_default(stored_name, stored_beta, chosen_name, dam_betas):
    """Unlevered beta to pre-fill for one sector row in the DCF editor.

    The stored beta wins whenever the sector is unchanged. It is a deliberate
    input — the MCP authored it, sometimes overriding Damodaran on purpose —
    and replacing it with Damodaran's current figure on every render moved
    DECK's discount rate from the intended 9.68% to 8.75% simply by opening the
    ticker page, then persisted that on the next save.

    Picking a *different* sector is an explicit act, so that sector's live beta
    becomes the starting point; an unknown sector keeps the stored value rather
    than dropping to a placeholder.
    """
    if chosen_name != stored_name:
        return float((dam_betas or {}).get(chosen_name, stored_beta))
    return float(stored_beta)


def calculate_multi_lens_valuation_remote(cfg: dict) -> dict:
    """Thin wrapper so tests can monkey-patch this name without touching
    the pure orchestrator."""
    import valuation_lenses
    return valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid=False)


def _refresh_stale_valuations(client, cfgs: dict, user_id: str | None = None,
                               force: bool = False, max_workers: int = 6,
                               on_progress=None) -> dict:
    """Run the multi-lens orchestrator across stale tickers in parallel.

    Stale = no valuation_summary OR calculated_at older than 7 days OR unparseable.
    Returns {"computed": [...], "errors": [...], "skipped": [...]}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import UTC, datetime, timedelta

    threshold = datetime.now(UTC) - timedelta(days=7)

    def _is_stale(cfg):
        s = cfg.get("valuation_summary") if isinstance(cfg, dict) else None
        if not s:
            return True
        ts_str = s.get("calculated_at")
        if not ts_str:
            return True
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except (ValueError, AttributeError):
            return True
        return ts < threshold

    targets = list(cfgs.keys()) if force else [t for t, c in cfgs.items() if _is_stale(c)]
    skipped = [t for t in cfgs if t not in targets]

    computed = []
    errors = []

    def _refresh_one(ticker):
        cfg = dict(cfgs[ticker])
        cfg.setdefault("ticker", ticker)
        summary = calculate_multi_lens_valuation_remote(cfg)
        cfg["valuation_summary"] = summary
        # Refresh the watchlist's EDGAR slice while we are already writing
        # this config. Without it the page falls back to a 5 MB companyfacts
        # download per ticker on the next cold load. A failure here must not
        # cost the valuation that just succeeded, so the old slice stays.
        try:
            _sl = slim_fundamentals(fetch_fundamentals(ticker, n_years=10))
            if _sl:
                cfg["fund_slice"] = _sl
        except Exception as _e:
            logger.warning("Slice refresh failed for %s (keeping previous): %s",
                           ticker, _e)
        save_config(client, ticker, cfg, user_id=user_id)
        return ticker

    if not targets:
        return {"computed": computed, "errors": errors, "skipped": skipped}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_refresh_one, t): t for t in targets}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                future.result()
                computed.append(ticker)
            except Exception as e:
                logger.warning("Refresh failed for %s: %s", ticker, e)
                errors.append(f"{ticker}: {e}")
            if on_progress is not None:
                try:
                    on_progress(len(computed) + len(errors), len(targets))
                except Exception as cb_err:
                    logger.debug("on_progress callback raised (ignored): %s", cb_err)

    return {"computed": computed, "errors": errors, "skipped": skipped}


# ── AI provider helpers (Groq primary, Gemini Flash fallback) ──
def _secret_or_env(name: str) -> str | None:
    try:
        v = st.secrets.get(name)
        if v:
            return v
    except Exception:
        pass
    return os.environ.get(name)


def _gemini_api_key() -> str | None:
    return _secret_or_env("GEMINI_API_KEY")


def _groq_api_key() -> str | None:
    return _secret_or_env("GROQ_API_KEY")


def _ai_ready() -> bool:
    return bool(_groq_api_key()) or bool(_gemini_api_key())


_RETRY_SUBSTRINGS = (
    "rate", "quota", "429", "resource_exhausted",
    "503", "unavailable", "overloaded", "high demand",
)


def _groq_call(prompt: str) -> tuple[str, str | None]:
    key = _groq_api_key()
    if not key:
        return "", "GROQ_API_KEY niet ingesteld."
    import urllib.request
    import urllib.error
    import json as _json
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=_json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key.strip()}",
            "Content-Type": "application/json",
            "User-Agent": "lazytheta-stock-analysis/1.0",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
        return (text, None) if text else ("", "groq: empty response")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return "", f"groq: HTTP {e.code} {e.reason} — {body[:500]}"
    except Exception as e:
        return "", f"groq: {e}"


def _gemini_flash_call(prompt: str) -> tuple[str, str | None]:
    key = _gemini_api_key()
    if not key:
        return "", "GEMINI_API_KEY niet ingesteld."
    try:
        from google import genai
    except ImportError:
        return "", "google-genai pakket niet geïnstalleerd."
    try:
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt,
        )
        text = (getattr(resp, "text", "") or "").strip()
        return (text, None) if text else ("", "gemini-2.5-flash: empty response")
    except Exception as e:
        return "", f"gemini-2.5-flash: {e}"


def _gemini_run(prompt: str, prefer_pro: bool = False) -> tuple[str, str | None]:
    """Run a prompt against the AI providers.

    Order: Groq Llama 3.3 70B → Gemini 2.5 Flash (fallback on rate limit/overload).
    Returns (text, error).
    """
    errors: list[str] = []
    for fn, name in (
        (_groq_call, "Groq Llama 3.3 70B"),
        (_gemini_flash_call, "Gemini 2.5 Flash"),
    ):
        text, err = fn(prompt)
        if text:
            return text, None
        if err:
            errors.append(f"{name} — {err}")
            low = err.lower()
            if any(s in low for s in _RETRY_SUBSTRINGS):
                continue  # try next provider
            return "", "AI error:\n\n" + "\n\n".join(errors)
    return "", "Alle AI providers faalden:\n\n" + "\n\n".join(errors)


# Backwards-compat alias (used by existing UI code)
def _gemini_ready() -> bool:
    return _ai_ready()


def _render_scorecard(data: dict, theme: dict, ticker: str, company: str) -> str:
    """Scorecard rendered in the same row style as the robustness table: one
    row per item (colored dot + gradient track + note), grouped, with a verdict
    pill. Flat (transparent) — the hero-card expander provides the white card."""
    import html as _html

    from prescan_render import three_state_html

    text = theme.get("text", "#111")
    muted = theme.get("text_muted", "#888")
    card = theme.get("card", "#fff")
    border_light = theme.get("border_light", "#e8e8ed")
    font = ("'DM Sans', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', "
            "Arial, sans-serif")
    lw = 172

    def _row(label, rating, note):
        # Three circles, not a marker on a gradient: red/yellow/green are three
        # states, and a continuous track invited reading a position between
        # them that the rating does not carry.
        note = _html.escape(note or "")
        label = _html.escape(label or "")
        return (
            f'<div style="display:flex;align-items:center;gap:14px;'
            f'padding:6px 0;font-size:0.82rem">'
            f'<div style="width:{lw}px;flex:none;color:{text};white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis">{label}</div>'
            f'<div style="flex:none">'
            + three_state_html(rating, ("", "", ""), size=17, theme=theme)
            + f'</div>'
            f'<div title="{note}" style="flex:1;min-width:0;color:{muted};'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{note}</div>'
            f'</div>'
        )

    def _group(title, first=False):
        sep = ("" if first
               else f'border-top:1px solid {border_light};margin-top:18px;padding-top:14px;')
        return (f'<div style="color:{muted};font-size:0.68rem;font-weight:700;'
                f'letter-spacing:0.09em;text-transform:uppercase;'
                f'{sep}margin-bottom:6px">{title}</div>')

    rows = [_group("Quality", first=True)]
    ap = data.get("all_phases", {}) or {}
    for key, label in (("business_description", "Business"), ("moat", "Moat"),
                       ("long_term_potential", "Long-term potential")):
        it = ap.get(key, {}) or {}
        rows.append(_row(label, it.get("rating"), it.get("note", "")))

    kms = data.get("key_metrics", []) or []
    if kms:
        rows.append(_group("Key metrics"))
        for km in kms:
            rows.append(_row(km.get("name", ""), km.get("rating"), km.get("value", "")))

    er = data.get("execution_risk", {}) or {}
    rows.append(_group("Risk"))
    rows.append(_row("Execution risk", er.get("rating"), er.get("note", "")))

    # No valuation rows: the DCF owns that judgement. A scorecard verdict of
    # "fairly valued" next to a fair-value band computed from the filings was
    # two answers to one question, and the softer one is the one that gets
    # quoted. Existing scorecards may still carry the key; it is simply not
    # rendered.

    # Verdict as the large selector, same treatment as the robustness card:
    # it is the answer, and the rows below it are the working. The three
    # verdicts map onto the same scale everything else uses.
    verdict = (data.get("verdict") or "").lower()
    _vband = {"pass": "red", "revisit": "yellow", "deep_dive": "green"}.get(verdict)
    phase = data.get("phase", {}) or {}
    phase_label = _html.escape(f"{company} ({ticker}) · Phase "
                               f"{phase.get('number', '?')} · "
                               f"{phase.get('name', '')}")
    header = (
        f'<div style="background:{theme.get("bg_secondary", "#f4f2ee")};'
        f'border-radius:16px;padding:18px 20px 14px;margin-bottom:16px">'
        f'<div style="text-align:center;font-size:0.7rem;font-weight:700;'
        f'letter-spacing:0.09em;color:{muted};text-transform:uppercase;'
        f'margin-bottom:14px">Is this worth the work?</div>'
        + three_state_html(_vband, ("Pass", "Revisit", "Deep dive"),
                           size=48, theme=theme)
        + f'<div style="text-align:center;margin-top:14px;color:{muted};'
        f'font-size:0.7rem;font-weight:600;letter-spacing:0.04em">'
        f'{phase_label}</div></div>'
    )

    summary = (data.get("summary") or "").strip()
    foot = ""
    if summary:
        import re as _re
        clean = _re.sub(r"^#+\s*", "", summary, flags=_re.MULTILINE)
        clean = _re.sub(r"\*\*(.*?)\*\*", r"\1", clean)
        clean = _re.sub(r"\*(.*?)\*", r"\1", clean)
        clean = _re.sub(r"^\s*[-*]\s+", "", clean, flags=_re.MULTILINE)
        clean = _re.sub(r"<[^>]+>", "", clean).strip()
        foot = (f'<div style="margin-top:14px;padding-top:10px;border-top:1px '
                f'solid {border_light};color:{muted};font-size:0.78rem;'
                f'line-height:1.5">{_html.escape(clean)}</div>')

    return f'<div style="font-family:{font}">{header}{"".join(rows)}{foot}</div>'


# Default AI research prompts loaded via "Load default prompts" button
DEFAULT_AI_PROMPTS: list[dict] = [
    {
        "title": "Robustness",
        "prompt": (
            "You are scoring **{company} ({ticker})** on Pulak Prasad's robustness "
            "framework (risk first). Judge ONLY these four qualitative axes; the ROCE "
            "and net-debt axes are computed from data elsewhere — do not output them.\n\n"
            "For each axis pick a band: \"robust\" (most robust pole), \"mid\", or "
            "\"fragile\" (least robust pole), and a one-line note grounded in the prior "
            "analysis.\n\n"
            "- **customers**: customer & supplier base — robust = highly fragmented (no "
            "dependence on any single party); fragile = concentrated.\n"
            "- **barriers**: competitive barriers / moat — robust = wide/widening; "
            "fragile = none/eroding.\n"
            "- **management**: stability & honesty of management/governance — robust = "
            "stable, honest signals, clean capital allocation; fragile = dubious, "
            "serial acquirer, turnaround.\n"
            "- **industry**: pace of industry change — robust = slow-changing/predictable; "
            "fragile = fast-changing.\n\n"
            "Use the prior sections as evidence:\n"
            "Moat: {prior:Moat Analysis}\n\n"
            "Risk: {prior:Risk Analysis}\n\n"
            "Disruption resilience: {prior:SaaSpocalypse Resistance}\n\n"
            "Business: {prior:Business Analysis}\n\n"
            "Respond with ONLY a fenced JSON block, no prose:\n"
            "```json\n"
            "{\n"
            '  "axes": {\n'
            '    "customers":  {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "barriers":   {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "management": {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "industry":   {"band": "robust|mid|fragile", "note": "..."}\n'
            "  }\n"
            "}\n"
            "```"
        ),
    },
    {
        "title": "Business Phase Analysis",
        "prompt": """# BUSINESS PHASE ANALYSIS v18.7

## YOUR IDENTITY
Financial analyst classifying companies into six growth phases based on operating income dynamics.

## YOUR MISSION
1. Request company name from user
2. Retrieve most recent financial data from SEC filings
3. Apply simple decision tree
4. Output ONLY the template below - nothing more

## EXECUTION TRIGGER
- If company name/ticker provided: Begin analysis
- If not provided: Output EXACTLY: "What company (name or ticker) would you like me to analyze?"
- WAIT FOR USER RESPONSE

## DATA ACQUISITION

### Priority (CRITICAL)
1. Identify current year from today's date
2. Search for MOST RECENT 10-Q from current year
3. If no current year 10-Q, use most recent 10-K
4. State explicitly: "Using [Q# YYYY 10-Q] filed on [date]" or "No 2025 10-Q available, using [FY YYYY 10-K]"

### Required Data
- Current period Revenue
- Prior year same period Revenue
- Current period Operating Income
- Prior year same period Operating Income
- Capital Returns (dividends + buybacks from Cash Flow Statement)

### Source Priority
- PRIMARY: SEC EDGAR only
- SECONDARY: Company IR page (official reports only)
- FORBIDDEN: Third-party aggregators

## CLASSIFICATION LOGIC (USE INTERNALLY ONLY)

### DECISION TREE (Apply in exact order)

STEP 1: Check Capital Returns
- Returning capital (dividends OR buybacks)? → Phase 5: CAPITAL RETURN [STOP]
- Otherwise → Continue

STEP 2: Check Operating Income
- Negative? → Go to Step 3
- Positive? → Go to Step 4

STEP 3: Analyze Losses (for negative Operating Income)
- Current loss worse than prior year? → Phase 1: STARTUP [STOP]
- Current loss same or better? → Phase 2: HYPERGROWTH [STOP]

STEP 4: Check Revenue Growth (for positive Operating Income)
- Revenue declining? → Phase 6: DECLINE [STOP]
- Revenue flat/growing? → Phase 4: OPERATING LEVERAGE [STOP]

## PHASE DEFINITIONS & VALUATION METHODS

### 🌱 Phase 1: STARTUP
- Characteristics: Losses expanding, finding product-market fit
- Valuation Methods: Forward Price to Sales, Total Addressable Market (TAM)
- Why These Fit: Company is pre-profit with expanding losses. Valuation relies on future revenue potential and market opportunity size.
- Avoid: P/E ratios, DCF models, any earnings-based methods

### 🚀 Phase 2: HYPERGROWTH
- Characteristics: Losses improving, proving viability
- Valuation Methods: Forward Price to Sales, Price to Gross Profit
- Why These Fit: Company shows improving unit economics with shrinking losses. Valuation focuses on revenue trajectory and gross profit margins.
- Avoid: P/E ratios, DCF models

### ⚖️ Phase 3: SELF FUNDING
- Characteristics: Near breakeven, validating model
- Valuation Methods: Price to Sales, Price to Gross Profit
- Why These Fit: Company is near breakeven, validating its business model. Current revenue and gross profit provide reliable valuation anchors.
- Avoid: Forward/Trailing P/E, Reverse DCF

### ⚙️ Phase 4: OPERATING LEVERAGE
- Characteristics: Profitable, maximizing margins
- Valuation Methods: Forward Price to Earnings, Forward Price to Free Cash Flow
- Why These Fit: Company demonstrates scalable profitability. Forward earnings and cash flow reflect the trajectory.
- Avoid: Dividend yield models

### 🎁 Phase 5: CAPITAL RETURN
- Characteristics: Mature, rewarding shareholders
- Valuation Methods: Trailing Price to Earnings, Trailing Price to Free Cash Flow, Reverse DCF
- Why These Fit: Company is mature with stable operations and capital returns. Current earnings and cash generation drive valuation.
- Avoid: High growth multiples, forward P/S

### 📉 Phase 6: DECLINE
- Characteristics: Revenue falling, business deteriorating
- Valuation Methods: Price to Book, Liquidation Value, Asset-Based Valuation
- Why These Fit: Traditional growth valuation methods are unreliable for declining businesses due to deteriorating fundamentals.
- Avoid: Growth multiples, forward earnings, DCF

Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body justifies it rather than restating it. EXACTLY three
bullets, each carrying a figure or a filing fact — a bullet that could be
swapped onto any company in the sector is a description, not a finding.
No tables, no subsections. You still do the full analysis above; you report
only what decides the answer.

# TEMPLATE (output exactly this shape, nothing before or after)

**Phase: [Loss-making / Growth / Margin expansion / Profitable growth / Capital return / Decline] · [N]/6**

[ONE sentence. What the company is doing with its money right now, and how you
know. Not a definition of the phase.]

- **[Two-to-three word label]**: [one line with the figure that places it in
  this phase — operating income, revenue growth, buybacks, dividend]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**What moves it on:** [one line — the observable change that would put it in
the next phase, or back into the previous one. Name the metric and the level.]

## Sources
[1] Source - domain.com

# RULES
- The number is the phase itself (1-6), not a rating — the name goes
  before it, the number after, so neither is written twice.
- Valuation method belongs in the bullets only if the phase changes which one
  applies. Do not restate the DCF's job.
""",
    },
    {
        "title": "Business Analysis",
        "prompt": """# BUSINESS ANALYSIS v2.1

CRITICAL: You are now executing a business analysis protocol. Follow each instruction precisely in order.

## YOUR IDENTITY
Expert financial analyst specializing in business model analysis from SEC filings.

## YOUR MISSION
1. Request company name from user
2. Retrieve and analyze the most recent 10-K
3. Answer the seven key questions about the company's business model
4. Output findings in clean Markdown format (DO NOT wrap in code blocks)
5. Provide concise but informative answers—not too brief, not overly detailed

## EXECUTION TRIGGER
- If this prompt contains a company name/ticker: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze?"
- Do NOT proceed without explicit company identification
- Do NOT default to any example company
- WAIT FOR USER RESPONSE BEFORE PROCEEDING

## EXECUTION SEQUENCE

### Step 1: User Input
If company not provided with prompt, output exactly:
"What company (name or ticker) would you like me to analyze? (I'll retrieve the most recent filings as of [current date])"
Wait for the response. Store as COMPANY_NAME.

### Step 2: Data Acquisition
**SEARCH PRIORITY (CRITICAL):**
1. First, identify the current year from today's date
2. Search for the MOST RECENT 10-Q from the CURRENT YEAR
3. Only use prior year 10-Q if current year is unavailable
4. If no current year 10-Q exists, explicitly state: "No 2025 10-Q available as of [date], using [specify what you're using instead]"

Gather in this order:
- Most recent 10-Q from current fiscal year (e.g., if in 2025, get Q1/Q2/Q3 2025)
- Most recent 10-K (for complete business model)
- If current year 10-Q unavailable, use earnings press releases or investor presentations

**VERIFICATION STEP:** Before proceeding, confirm which documents you found:
- State: "Using [Company] 10-K from [date] and 10-Q from [specific quarter and year]"
- If using older data, explain why newer isn't available

### Step 3: Business Analysis (from 10-K)
Answer these questions in plain English, with citations:

1. **What does the company do?** (Core products/services)
2. **How does it make money?** (Revenue streams & segments - list from most to least important with % breakdown)
3. **Who are its customers?** (Individuals, SMBs, enterprises, governments, etc.)
4. **Where does it operate?** (Key geographies with % breakdown if multiple)
5. **How often do customers buy?** (Recurring vs one-time, contracts, retention data)
6. **Can it raise prices?** (Evidence from margins, pricing commentary, risk factors)
7. **What happens in a recession?** (Cyclicality, past performance, management warnings)

Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body justifies it rather than restating it. EXACTLY three
bullets, each carrying a figure or a filing fact — a bullet that could be
swapped onto any company in the sector is a description, not a finding.
No tables, no subsections. You still do the full analysis above; you report
only what decides the answer.

# TEMPLATE (output exactly this shape, nothing before or after)

**Business: [Simple 🟢 / Understandable 🟡 / Opaque 🔴] · [how it earns, in two or three words]**

[ONE sentence. What you buy when you buy this share — the product, the payer,
and the geography. Someone who has never heard of the company should be able to
repeat it.]

- **Revenue Mix**: [the segments or geographies that matter, with their share]
- **[Two-to-three word label]**: [pricing power, repeat purchase, or whatever
  actually drives the economics — with the number that shows it]
- **[Two-to-three word label]**: [one line]

**In a downturn:** [one line — what happens to this business when customers
spend less, with evidence from a past cycle if there is one.]

## Sources
[1] Source - domain.com

# RULES
- "Simple" means you could explain it to someone at dinner. "Opaque" means the
  filings do not let you see how the money is made — say which part is dark.
""",
    },
    {
        "title": "Moat Analysis",
        "prompt": """# MOAT ANALYSIS v2.2

CRITICAL: You are now executing a moat analysis protocol. Follow each instruction precisely in order.
Platform Note: If sequential execution not possible, include company name in initial prompt.

##YOUR IDENTITY: World-class financial analyst specializing in economic moat assessment.

##YOUR MISSION:
1. Request company name from user
2. Retrieve current financial data and Morningstar analysis
3. Evaluate all five moat sources with evidence
4. Classify moat size and direction using STRICT CRITERIA
5. Output clean Markdown report (DO NOT wrap in code blocks)
--------------------------------------------
EXECUTION TRIGGER
--------------------------------------------
CRITICAL: Company selection protocol:
- If this prompt contains a company name/ticker in the same message: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze for moat assessment?"
- Do NOT proceed without explicit company identification
- Do NOT default to any example company (e.g., Apple, Microsoft)
- If uncertain, always ask for clarification
WAIT FOR USER RESPONSE BEFORE PROCEEDING
--------------------------------------------
DEFINITIONS AND FRAMEWORK
--------------------------------------------
MOAT SIZE CRITERIA:
WIDE MOAT (10+ years durability):
- Network Effect: Every new user makes product more valuable, market leadership
- Switching Costs: High friction to leaving; mission-critical product
- Intangible Assets: Brand provides significant pricing power; exclusive licenses
- Low-Cost Production: Lowest cost structure that competitors struggle to match
- Counter-Positioning: Incumbents unable to copy without self-harm
NARROW MOAT (3-10 years durability):
- Network Effect: Users loyal but not locked in; niche network
- Switching Costs: Some friction; customers stay from habit/convenience
- Intangible Assets: Some brand loyalty but price-sensitive customers
- Low-Cost Production: Some cost advantage but regionally limited
- Counter-Positioning: Challenges incumbents but they can fight back
NO MOAT (No durable advantage):
- Network Effect: No benefit when users join; small network
- Switching Costs: Customers leave easily with low attachment
- Intangible Assets: Undifferentiated brand with many substitutes
- Low-Cost Production: Higher costs than peers
- Counter-Positioning: Same business model as competitors
MOAT DIRECTION:
- Widening: Rising engagement, margin expansion, brand extending
- Stable: Flat growth/margins; high retention but no new advantages
- Narrowing: Increasing churn, margin compression, weakening brand
--------------------------------------------
EXECUTION SEQUENCE
--------------------------------------------
STEP 1: USER INPUT
If company not provided with prompt, output exactly: "What company (name or ticker) would you like me to analyze for moat assessment?"
Wait for response. Store as COMPANY_NAME.
STEP 2: DATA ACQUISITION
Perform web search to gather:
- Most recent 10-K, 10-Q filings
- Latest earnings call transcripts
- Morningstar analyst report (if available)
- Key metrics: Revenue growth, margins, retention rates, market share
STEP 3: MOAT EVALUATION
For each of the 5 moat sources:
- Start with assumption of "No Moat"
- Seek positive evidence to prove otherwise
- Require 2 hard data points + 1 quote per moat type
Note: Counter-Positioning requires the new model to harm incumbents if copied (e.g., Netflix streaming vs Blockbuster stores). Simply being different or innovative is NOT counter-positioning.
STEP 4: CLASSIFICATION
Apply criteria mechanically:
- Document each moat type as Present/Not Present
- If Present, classify as Wide/Narrow
- Determine direction as Widening/Stable/Narrowing
- Identify 1-2 primary moat sources
STEP 5: OUTPUT

Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body exists to justify it, not to restate it. Every
bullet must survive the question "would I still write this if I had to drop
one?" — if it would not, drop it.

# TEMPLATE (output exactly this shape, nothing before or after)

**Moat: [None ❌ / Narrow 🤏 / Wide 🛡️] · [Widening ↗️ / Stable ➡️ / Narrowing ↘️] · [0-5]/5**

[ONE sentence. Name the company, bold the verdict, and say where the moat comes
from — or why there isn't one. No preamble, no restating the question.]

- **[Two-to-three word label]**: [one line, with the number or fact that makes
  it true. No citation clutter — the source list is below.]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**Weakest link:** [one line — the moat source you looked for and did not find,
or the one most likely to erode first. This line is mandatory: a moat analysis
with nothing against it has not been done.]

## Sources
[1] Source - domain.com
[2] Source - domain.com

# RULES FOR THE TEMPLATE
- The score is the moat's strength on the five sources you assessed, not a
  confidence rating: 0 = none, 1-2 = narrow, 3-4 = wide, 5 = wide and widening.
- EXACTLY three bullets. Not four because a fourth is interesting. If two
  sources are strong and three are absent, three bullets still — use one to
  say what is absent and why it does not matter.
- A bullet whose label could be swapped onto any company in the sector is not
  a moat, it is a description. Rewrite it or drop it.
- No tables. No per-source subsections. No "Assessment: Present" lines. You
  still assess all five sources — you just report the ones that decide it.

""",
    },
    {
        # Five question cards for the Moat tab, built on the analysis above.
        "title": moat_cards.TITLE,
        "prompt": moat_cards.PROMPT,
    },
    {
        "title": "Long-Term Potential",
        "prompt": """# LONG-TERM POTENTIAL GROWTH DRIVERS ANALYSIS v2.2

CRITICAL: You are now executing a growth drivers analysis protocol. Follow each instruction precisely in order.

## YOUR IDENTITY
Expert growth strategist specializing in identifying and evaluating corporate growth mechanisms from financial filings and strategic initiatives.

## YOUR MISSION
1. Request company name from user
2. Retrieve and analyze recent 10-K, 10-Q, and supplementary sources
3. Evaluate growth drivers using the 2×4 framework (New Customers vs Existing Customers)
4. Assess strength of each driver and identify primary/secondary strategies
5. Output findings in clean Markdown format (DO NOT wrap in code blocks)

## EXECUTION TRIGGER
- If this prompt contains a company name/ticker: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze for growth drivers?"
- Do NOT proceed without explicit company identification
- WAIT FOR USER RESPONSE BEFORE PROCEEDING

## EXECUTION SEQUENCE

### Step 1: User Input
If company not provided with prompt, output exactly:
"What company (name or ticker) would you like me to analyze for growth drivers?"
Wait for response. Store as COMPANY_NAME.

### Step 2: Data Acquisition
**SEARCH PRIORITY:**
1. Most recent 10-K (business segments, strategy section, MD&A)
2. Latest 10-Q (recent developments, quarterly trends)
3. Web search for: "[Company] growth strategy", "[Company] expansion plans", "[Company] investor day"
4. Recent earnings call transcripts (CEO/CFO growth commentary)
State which documents found: "Analyzing [Company] using 10-K from [date], 10-Q from [quarter], and [other sources]"
### Step 3: Growth Driver Evaluation

**STRENGTH INDICATORS:**
- 🟢 = Strong: Clear evidence with metrics, major investment/focus
- 🟡 = Moderate: Some evidence, mentioned but not emphasized
- 🔴 = Weak: Limited or no evidence
- ⚫ = Not Applicable: No evidence found
CRITICAL: Only evaluate the 7 specified drivers. Do NOT add bonus categories or additional drivers.
Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body justifies it rather than restating it. EXACTLY three
bullets, each carrying a figure or a filing fact — a bullet that could be
swapped onto any company in the sector is a description, not a finding.
No tables, no subsections. You still do the full analysis above; you report
only what decides the answer.

# TEMPLATE (output exactly this shape, nothing before or after)

**Runway: [Long ↗️ / Moderate ➡️ / Short ↘️] · [0-5]/5**

[ONE sentence. Where the next decade of growth comes from — new customers, more
per customer, or price — and which of those is doing the work now.]

- **[Two-to-three word label]**: [the driver, with the growth rate or
  penetration figure that makes it credible]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**Biggest assumption:** [one line — the thing that must stay true for this
runway to exist. If it is "the market keeps growing", say what happens if it
does not.]

## Sources
[1] Source - domain.com

# RULES
- 0-1 = the business grows with GDP at best. 4-5 = a decade of reinvestment at
  high returns is visible in today's disclosures, not in management's ambition.
- An untapped opportunity the company is not pursuing is not runway. Say so.
""",
    },
    {
        "title": "Key Metrics",
        "prompt": """# BUSINESS PHASE KEY METRICS v3.3

## CONTEXT FROM PRIOR ANALYSIS
Use the phase identified in the following Business Phase Analysis (extract the phase number 1-6 automatically, do NOT ask the user):

{prior:Business Phase Analysis}

If the prior analysis above is missing or empty, default to asking for the phase.

## YOUR IDENTITY
Financial analyst evaluating company's phase-appropriate metrics using Red/Yellow/Green framework.

## YOUR MISSION
1. Extract the phase number from the prior Business Phase Analysis above
2. Retrieve and analyze the most recent 10-K, 10-Q, and earnings reports
3. Apply the exact phase-specific metrics and thresholds below
4. Score each metric as Red / Yellow / Green based on defined thresholds
5. Output ONLY the template below - nothing more

## EXECUTION TRIGGER
- Phase is provided via the prior analysis context above — begin analysis immediately
- Only if BOTH the prior analysis is missing AND no phase was given, output: "What company (name or ticker) and phase (1-6) would you like me to analyze for key metrics?"

## DATA ACQUISITION
### Priority (CRITICAL)
1. Identify current year from today's date
2. Search for MOST RECENT 10-Q from current year
3. If no current year 10-Q, use most recent 10-K
4. Recent 8-K filings (material events)
5. Earnings call transcripts (last 2 quarters) - optional

### Required Data (in priority order)
- Revenue (current and 3-year historical)
- Gross margin (quarterly for trend analysis)
- Operating margin/income
- Free cash flow
- Shares outstanding (current and 3-year historical)
- Capital returns (dividends + buybacks)
- ROIC components (operating income, tax rate, debt, equity, cash)
- Balance sheet (cash, debt, interest expense)
## PHASE-SPECIFIC METRICS & THRESHOLDS
### 🌱 Phase 1: STARTUP
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue** | None | Positive | Positive and >30% YoY Growth |
| **Gross Margin** | Negative | Positive | Positive and Improving (>0pp YoY) |
| **Cash Runway** | Less than 1.5 Years | Between 1.5 and 3 Years | 3+ Years (or FCF Positive) |
| **Revenue vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **Shares Outstanding 3YR CAGR** | Over 7% | Between 4% and 7% | Less than 4% |
### 🚀 Phase 2: HYPER GROWTH
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 20% | 20%-30% | 30%+ |
| **Gross Margin Direction** | Declining or Erratic (>3pp variance QoQ) | Stable (within ±1pp YoY) | Rising |
| **Cash Runway** | Less than 2 Years | Between 2 and 4 Years | 4+ Years (or FCF Positive) |
| **Revenue vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **Shares Outstanding 3YR CAGR** | Over 5% | Between 3% and 5% | Less than 3% |
### ⚖️ Phase 3: SELF FUNDING
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 15% | Between 15% and 25% | Over 25% |
| **Gross Margin Direction** | Declining | Stable (within ±1pp YoY) | Rising |
| **Operating Margin** | Declining or <-2% | Between -2% and +2% | >2% and Rising |
| **Free Cash Flow** | Negative | Positive | Positive and Rising |
| **Shares Outstanding 3YR CAGR** | More than 3% | Between 1% and 3% | Below 1% |
### ⚙️ Phase 4: OPERATING LEVERAGE
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 10% | Between 10% and 20% | Over 20% |
| **Operating Margin** | Declining or Cyclical | Positive and Stable (within ±1pp YoY) | Positive and Rising |
| **Free Cash Flow Margin** | Contracting or Negative | Positive | Positive and Rising |
| **Earnings vs. Estimates** | <5 of last 8 beats | 5-7 of last 8 beats | 4 of last 4 beats |
| **ROIC** | <0% or Declining | 0%-5% (no clear trend) | >5% and Rising (3 of 4 quarters) |
### 🎁 Phase 5: CAPITAL RETURN
| Metric | 🔴 Red | 🟡 Yellow | 🟢 Green |
|--------|--------|-----------|----------|
| **Revenue 3YR CAGR** | Less than 5% | Between 5% and 10% | Over 10% |
| **Free Cash Flow / Net Income** | Less than 50% | Between 50% and 90% | Over 90% |
| **EBIT / Interest Expense** | Less than 2 | Between 2 and 5 | 5+ (or debt-free) |
| **ROIC** | Less than 10% | Between 10% and 20% | Over 20% |
| **Capital Returns** | None | Yes, <5 Years | Yes, 5+ Years |
### 📉 Phase 6: DECLINE
**No metrics recommended** - Framework advises avoiding these companies as they are in permanent decline.
## KEY DEFINITIONS
- **Stable**: Within ±1 percentage point year-over-year
- **Erratic**: Variance >3pp between consecutive quarters
- **Rising ROIC**: Improved in 3 of last 4 quarters
- **Cash Runway**: If FCF positive, automatically Green
- **No Debt**: EBIT/Interest automatically Green
- **Boundary Rule**: When exactly on threshold, use better rating
---
Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body justifies it rather than restating it. EXACTLY three
bullets, each carrying a figure or a filing fact — a bullet that could be
swapped onto any company in the sector is a description, not a finding.
No tables, no subsections. You still do the full analysis above; you report
only what decides the answer.

# TEMPLATE (output exactly this shape, nothing before or after)

**Metrics: [Strong 🟢 / Mixed 🟡 / Weak 🔴] · [N]/5**

[ONE sentence. What the five phase metrics say together — not a list of them.]

- **[Metric name]**: [value, target, and the direction it is moving]
- **[Metric name]**: [value, target, direction]
- **[Metric name]**: [value, target, direction]

**Weakest metric:** [one line — the metric closest to failing its gate, with
the level at which it would. This line is mandatory even when all five pass.]

## Sources
[1] Source - domain.com

# RULES
- The score is how many of the five phase metrics pass their gate.
- The three bullets are the three that decide it: the failures first, then the
  ones nearest their threshold. A metric passing comfortably needs no line.
""",
    },
    {
        # Business overview, customer profile, revenue breakdown and four
        # question cards for the Business tab, built on Business Analysis,
        # Moat Analysis and Key Metrics.
        "title": business_cards.TITLE,
        "prompt": business_cards.PROMPT,
    },
    {
        # Sector/industry, capital type, difficulty and a few quick facts
        # for the Overview tab's profile panel, built on Business Analysis
        # and Moat Analysis.
        "title": company_profile.TITLE,
        "prompt": company_profile.PROMPT,
    },
    {
        "title": "Risk Analysis",
        "prompt": """# RISK ANALYSIS v2.0
CRITICAL: You are now executing an execution risk assessment protocol. Follow each instruction precisely in order.
## YOUR IDENTITY
Expert risk analyst specializing in identifying and evaluating operational and strategic risks from financial filings.
## YOUR MISSION
1. Request company name from user
2. Retrieve and analyze the most recent 10-K/10-Q filings
3. Assess four critical risk dimensions: Concentration, Disruption, Outside Forces, and Competition
4. Classify each risk using Red/Yellow/Green framework with evidence
5. Output findings in clean Markdown format (DO NOT wrap in code blocks)
## EXECUTION TRIGGER
- If this prompt contains a company name/ticker: Extract it and begin analysis
- If interactive dialog is available: Output EXACTLY and ONLY: "What company (name or ticker) would you like me to analyze for execution risk?"
- Do NOT proceed without explicit company identification
- WAIT FOR USER RESPONSE BEFORE PROCEEDING
## EXECUTION SEQUENCE
### Step 1: User Input
If company not provided with prompt, output exactly:
"What company (name or ticker) would you like me to analyze for execution risk?"
Wait for response. Store as COMPANY_NAME.
### Step 2: Data Acquisition
**SEARCH PRIORITY:**
1. Most recent 10-K (risk factors, MD&A, business overview)
2. Latest 10-Q (recent developments, updated risks)
3. Only if critical data missing: Web search for "[Company] customer concentration", "[Company] competitive pressure"
State which documents found: "Analyzing [Company] using 10-K from [date] and 10-Q from [quarter]"
### Step 3: Risk Assessment Framework
**RISK CLASSIFICATIONS:**
**Concentration Risk**
- 🔴 Red: Few customers >20% of revenue
- 🟡 Yellow: Largest customer <15% of revenue
- 🟢 Green: Highly diversified customer base
**Disruption Risk**
- 🔴 Red: Identifiable disruption threat
- 🟡 Yellow: Normal industry evolution
- 🟢 Green: Company is the disruptor
**Outside Forces Risk**
- 🔴 Red: High exposure (regulation, commodities, government, economy, interest rates)
- 🟡 Yellow: Normal exposure
- 🟢 Green: Low exposure
**Competition Risk**
- 🔴 Red: Severe pricing pressure, fragmented market
- 🟡 Yellow: Normal competitive environment
- 🟢 Green: Monopoly/Duopoly dynamics
## OUTPUT TEMPLATE

Write for someone scanning ten of these. Rate all four risk factors — then
report the ones that decide the answer. A risk that is Yellow because nothing
is wrong does not need a paragraph.

# TEMPLATE (output exactly this shape, nothing before or after)

**Risk: [High 🔴 / Medium 🟡 / Low 🟢] · [Concentration / Disruption / Outside forces / Competition — the one that drives the rating]**

[ONE sentence. What would actually have to go wrong for this to hurt, and how
exposed the company is to it. Not a list of risk categories.]

- **[Two-to-three word label]**: [one line, with the number that makes it real
  — a customer share, a margin move, a revenue concentration]
- **[Two-to-three word label]**: [one line]
- **[Two-to-three word label]**: [one line]

**What would change this:** [one line — the specific, observable thing that
would move the rating up or down. "Macro improves" is not an answer; "gross
margin back above 54% for two quarters" is.]

## Sources
[1] [Company] 10-K [Date] - sec.gov
[2] Source - domain.com

# RULES FOR THE TEMPLATE
- EXACTLY three bullets, ordered worst first. If only two risks are real, use
  the third to name the one you expected to find and did not — an analysis
  that finds nothing reassuring is as incomplete as one that finds nothing
  wrong.
- Every bullet carries a figure or a filing fact. "Faces competition" is not a
  risk; "gross margin fell 500bps in two years while two entrants scaled" is.
- No tables, no matrix, no per-factor subsections. The four ratings still get
  made; they just do not each get a heading.
- Default to Medium when the evidence is thin, and say the evidence is thin.

## BEHAVIORAL GUARDRAILS
- Apply Red/Yellow/Green strictly per the criteria above
- Prioritize filing data from the last 12 months
- State "Limited disclosure" where the company does not break it out
- Overall rating is the weighted average (Red=3, Yellow=2, Green=1):
  2.5+ = High, 1.5-2.4 = Medium, below 1.5 = Low

""",
    },
    {
        "title": "Price & Sentiment Analysis",
        "prompt": """PRICE & SENTIMENT ANALYSIS v1.9

YOUR IDENTITY
Expert market analyst focused on price causation and layered sentiment (analyst / investor / media) over the past 12 months.

YOUR MISSION
Identify why the stock moved over the last year and where sentiment sits now.
 Deliver a scan-friendly, citation-backed Markdown analysis.
 Never speculate. Never hype. Every statement must be verifiable.

INITIAL INPUT
Begin with:
 "What company (name or ticker) would you like me to analyze for price and sentiment changes over the past year?"
 If the user provides a company/ticker, begin immediately.
EXECUTION SEQUENCE
Step 1 – Input
Store company name as COMPANY_NAME and ticker as TICKER.
Step 2 – Data Acquisition (Priority Order)
Retrieve:
• 1-year price performance (% change, 52-week range, vs 50 / 200-day MAs)


• Major catalysts (earnings reactions, analyst actions, product launches, macro/regulatory headlines)


• Sentiment signals:


    ◦ Analyst reports (targets & ratings)
    ◦ Investor flows (institutional vs retail)
    ◦ Media tone (headlines, social, forums)


Step 3 – Perspective Analysis
Summarize 2–3 concise arguments for both the bullish and bearish cases.
 Use bullet points only. Include citations when available.
 If fewer than 2 sources per side → note "Limited recent coverage."


Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body justifies it rather than restating it. EXACTLY three
bullets, each carrying a figure or a filing fact — a bullet that could be
swapped onto any company in the sector is a description, not a finding.
No tables, no subsections. You still do the full analysis above; you report
only what decides the answer.

# TEMPLATE (output exactly this shape, nothing before or after)

**Sentiment: [Bullish 🟢 / Mixed 🟡 / Bearish 🔴] · [what moved the price]**

[ONE sentence. Why the price is where it is, and what the market is currently
arguing about.]

- **Price Action**: [1-year move, distance from the 52-week range, versus the
  index]
- **Bull Case**: [the strongest argument the buyers are making, in one line]
- **Bear Case**: [the strongest argument against, in one line]

**Next catalyst:** [one line — the dated event that resolves part of the
argument. "Earnings" is not enough; say which quarter and what to watch in it.]

## Sources
[1] Source - domain.com

# RULES
- Sentiment describes the market's mood, not your verdict on the company. A
  bearish tape on a good business is information, not a warning.
- No price targets as a recommendation. Report the consensus as a fact if it
  is relevant, and never as agreement.
""",
    },
    {
        "title": "SaaSpocalypse Resistance",
        "prompt": """YOUR IDENTITY
Act as a financial analyst who is focused on the long-term viability of a company's moat, or competitive advantage.

YOUR MISSION
Your task is to perform a viability and risk assessment of this company in the context of the AI revolution.

EXECUTION TRIGGER
If company name/ticker provided: Begin analysis. If not provided: Output EXACTLY: "What company (name or ticker) would you like me to analyze?" WAIT FOR USER RESPONSE.

DATA ACQUISITION
Evaluate the company across the following four lenses using the following rating scale. Provide a logical justification for each rating, prioritizing failure points and structural risks.

## The Rating Scale
- 🔴 **Fragile (Red):** High risk of disruption or structural weakness.
- 🟡 **Robust (Yellow):** Defensible and stable, but lacks significant upside from AI.
- 🟢 **Anti-Fragile (Green):** Structurally benefits from AI and gains strength from disruption.

## 1. Liability Lens (The Hallucination Risk)
**Assessment:** Is the cost of failure high?

Scale:
- 🟢 **Anti-Fragile (Green):** High cost of error. "If it's 90% right, that's catastrophic." Examples: Medical diagnostics, cybersecurity, grid management.
- 🔴 **Fragile (Red):** Low cost of error. "If it's 90% right, that's fine." Examples: Marketing copy, basic code generation, graphic design.

## 2. Business Model Lens (The Monetization Structure)
**Assessment:** Does the company charge for work (usage), or per worker (seats)?

Scale:
- 🟢 **Anti-Fragile (Green):** Verified Usage-Based. >80% of current revenue is explicitly tied to usage/credits. If AI agents replace 10 analysts, the revenue shifts to the compute/credits used by those agents.
- 🔴 **Fragile (Red):** Seat-Based Dominance. >80% of revenue is derived from per-user subscriptions. If AI allows 1 person to do the work of 10, the company loses 9 revenue streams.
- Note: Do not rate Green based on "planned" transitions; use current revenue mix.

## 3. Physical World Lens (Integration)
**Assessment:** Can an agent simulate this, or does it require real-world feedback?

Scale:
- 🟢 **Anti-Fragile (Green):** Hardware Integration. Software used in conjunction with tangible hardware or physical infrastructure cannot be easily replaced by pure AI agents.
- 🔴 **Fragile (Red):** Purely Software. Software is approaching zero marginal cost; it can be easily replicated or simulated by an agent.

## 4. Network Lens (Data Gravity)
**Assessment:** Does the data get better as more agents join?

Scale:
- 🟢 **Anti-Fragile (Green):** Proprietary Context. The company owns unique, non-public data that AI needs to be effective. Two-sided networks or proprietary security databases cannot be easily replicated.
- 🔴 **Fragile (Red):** Public Knowledge. The company relies on data that can be quickly migrated to a cheaper platform or scraped from the public web.

Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body justifies it rather than restating it. EXACTLY three
bullets, each carrying a figure or a filing fact — a bullet that could be
swapped onto any company in the sector is a description, not a finding.
No tables, no subsections. You still do the full analysis above; you report
only what decides the answer.

# TEMPLATE (output exactly this shape, nothing before or after)

**AI exposure: [Anti-fragile 🟢 / Resilient 🟡 / Exposed 🔴] · [N]/4**

[ONE sentence. Whether AI is a tool this company uses, a threat to what it
sells, or irrelevant to it — and why.]

- **[Lens name]**: [one line on the lens that decides it, with evidence]
- **[Lens name]**: [one line]
- **[Lens name]**: [one line]

**Where it would break:** [one line — the specific development that would turn
this from resilient to exposed. Mandatory even for a physical-goods business:
if nothing plausible exists, say what you looked for.]

## Sources
[1] Source - domain.com

# RULES
- The score is how many of the four lenses (liability, business model,
  physical world, network) come back resilient.
- Seat-based pricing on knowledge work is the exposure that matters. A company
  selling units of a physical thing is not exposed just because it uses
  software.
""",
    },
    {
        # Four question cards for the Risk tab, built on Risk Analysis and
        # SaaSpocalypse Resistance.
        "title": risk_cards.TITLE,
        "prompt": risk_cards.PROMPT,
    },
    {
        "title": "Investment Summary",
        "prompt": """# INVESTMENT SUMMARY & VERDICT

## YOUR IDENTITY
Senior portfolio manager synthesizing multiple prior analyses into a single, actionable investment verdict.

## YOUR MISSION
Read all the prior research below for this company and produce a concise, decisive summary with an overall investment verdict. Do NOT repeat the underlying analyses — synthesize them.

## PRIOR RESEARCH

### Business Phase Analysis
{prior:Business Phase Analysis}

### Business Analysis
{prior:Business Analysis}

### Moat Analysis
{prior:Moat Analysis}

### Long-Term Potential
{prior:Long-Term Potential}

### Key Metrics
{prior:Key Metrics}

### Risk Analysis
{prior:Risk Analysis}

### Price & Sentiment Analysis
{prior:Price & Sentiment Analysis}

### SaaSpocalypse Resistance
{prior:SaaSpocalypse Resistance}

---

Write for someone scanning ten of these. The verdict must be readable without
reading the body; the body justifies it rather than restating it. EXACTLY three
bullets, each carrying a figure or a filing fact — a bullet that could be
swapped onto any company in the sector is a description, not a finding.
No tables, no subsections. You still do the full analysis above; you report
only what decides the answer.

# TEMPLATE (output exactly this shape, nothing before or after)

**Verdict: [Deep dive 🟢 / Revisit 🟡 / Pass 🔴] · [High / Medium / Low] conviction**

[ONE sentence thesis. What this company is and why it is or is not worth more
work. This sentence is the one that gets quoted back — make it carry.]

- **Strongest point**: [one line, with the figure behind it]
- **Biggest concern**: [one line, with the figure behind it]
- **[Two-to-three word label]**: [the third thing that actually moves the
  decision — not a filler strength]

**What would change this:** [one line — the observable event that flips the
verdict, in either direction. Name the metric and the level.]

## Sources
[1] Source - domain.com

# RULES
- Say nothing about whether the shares are cheap. The DCF owns that judgement,
  and a prose opinion next to a computed fair value is the one that gets
  quoted. Verdict means "is this worth the work", not "is this a buy".
- Conviction is about the evidence, not the outcome: Low conviction on a Deep
  dive is a legitimate and useful answer.
""",
    },
    {
        "title": "Scorecard",
        "prompt": """# SCORECARD DATA EXTRACTOR

## YOUR MISSION
Read all prior analyses for this company and extract a structured JSON scorecard. This JSON is parsed programmatically — output EXACTLY the JSON block, nothing else before or after.

## PRIOR RESEARCH

### Business Phase Analysis
{prior:Business Phase Analysis}

### Business Analysis
{prior:Business Analysis}

### Moat Analysis
{prior:Moat Analysis}

### Long-Term Potential
{prior:Long-Term Potential}

### Key Metrics
{prior:Key Metrics}

### Risk Analysis
{prior:Risk Analysis}

### Investment Summary
{prior:Investment Summary}

---

## OUTPUT INSTRUCTIONS
Output ONLY a fenced JSON code block. No prose, no explanations. Use the exact schema below. Use lowercase color names: "red", "yellow", or "green".

Rating meanings:
- green = positive / strong / low risk / fairly or undervalued
- yellow = neutral / moderate / mixed signals
- red = negative / weak / high risk / overvalued

For `verdict`: use "deep_dive" (strongly interesting), "revisit" (park for later), or "pass" (skip).

```json
{
  "phase": {
    "number": 5,
    "name": "Capital Return"
  },
  "all_phases": {
    "business_description": {
      "rating": "green",
      "note": "Clear business model, well understood"
    },
    "moat": {
      "rating": "green",
      "note": "Wide / Expanding"
    },
    "long_term_potential": {
      "rating": "yellow",
      "note": "Moderate growth runway"
    }
  },
  "key_metrics": [
    {"name": "Revenue 3YR CAGR", "rating": "green", "value": "Over 10%"},
    {"name": "FCF / Net Income", "rating": "yellow", "value": "Between 50% and 90%"},
    {"name": "EBIT / Interest Expense", "rating": "green", "value": "5+"},
    {"name": "ROIC", "rating": "green", "value": "Over 20%"},
    {"name": "Capital Returns", "rating": "green", "value": "Yes, 5+ Years"}
  ],
  "execution_risk": {
    "rating": "yellow",
    "note": "Medium"
  },
  "verdict": "revisit",
  "summary": "Three concise sentences summarizing the investment case. Sentence 1: the core business and what makes it interesting or not. Sentence 2: the biggest strength or concern. Sentence 3: the verdict rationale — why deep dive, revisit, or pass."
}
```

## GUARDRAILS
- Output ONLY the JSON code block. Nothing else.
- Use EXACTLY the keys shown above — do not rename or add keys.
- The `key_metrics` list should contain exactly the 5 metrics used by the phase from the Key Metrics analysis.
- If a prior analysis is missing or cannot be interpreted, use rating "yellow" and note "Insufficient data".
- Derive `verdict` from the Investment Summary if present; otherwise base it on the overall pattern of ratings.
- `summary` must be EXACTLY 3 sentences, concise, plain English, derived from the Investment Summary.
""",
    },
]


# ── Rate limiting ──
def rate_limited_lookup() -> bool:
    """Returns True if the lookup is allowed, False if rate limited."""
    now = time.time()
    key = '_api_call_times'
    if key not in st.session_state:
        st.session_state[key] = []
    # Clean entries older than 60 seconds
    st.session_state[key] = [t for t in st.session_state[key] if now - t < 60]
    # Max 10 lookups per minute
    if len(st.session_state[key]) >= 10:
        st.warning("Too many requests. Please wait a moment before trying again.")
        return False
    st.session_state[key].append(now)
    return True


# ── Page config ──
from pathlib import Path as _Path
_favicon = _Path(__file__).parent / "assets" / "favicon.png"
st.set_page_config(
    page_title="Lazy Theta",
    page_icon=str(_favicon) if _favicon.exists() else "\U0001f4ca",
    layout="wide",
)

# ── Authentication gate ──
from auth import (render_login_page, logout, restore_session_into_state,
                  save_session_to_browser, note_session_expiry, session_expiring)

# The stored token comes from the browser through a component (see
# auth.read_browser_token): Streamlit Cloud's proxy strips cookies, and the URL
# is no place for a credential. The component answers on its first render and
# that answer starts a new run; until then there is nothing to decide, so the
# run stops. Bounded, so a browser that never answers still gets the login
# form. And no rerun on success: the rotated token is written by a script in
# this run, and a rerun would cut the run off before the browser ran it.
if "supabase_client" not in st.session_state:
    _restored = restore_session_into_state()
    if _restored is None:
        _waiting_since = st.session_state.get("_lt_store_wait")
        if not isinstance(_waiting_since, (int, float)):
            _waiting_since = time.time()
            st.session_state["_lt_store_wait"] = _waiting_since
        if time.time() - _waiting_since < 4:
            st.stop()
        _restored = False
    st.session_state.pop("_lt_store_wait", None)
    if not _restored:
        render_login_page()
        st.stop()

# Save remember-me token to browser if flagged during login
_sb_client = st.session_state["supabase_client"]
if st.session_state.pop("_save_remember_token", False):
    save_session_to_browser(_sb_client)
    note_session_expiry(_sb_client)


def _drop_session_for_restore():
    """Laat de gate de sessie opnieuw uit de browseropslag herstellen.

    Alleen de sessie zelf; broker-tokens en geladen data blijven staan, want
    die horen bij de gebruiker en niet bij deze access token.
    """
    for key in ("supabase_client", "user", "_lt_expires_at", "_auth_checked_at"):
        st.session_state.pop(key, None)
    st.rerun()


# Nooit in het geheugen verversen. De token in dit proces kan al verbruikt
# zijn door een herlaad of een andere tab; de browseropslag heeft altijd de
# nieuwste. Een refresh met een oude token ziet Supabase als misbruik en dan
# trekt hij de hele familie in -- dat was het uitloggen van 2026-09-22.
if session_expiring():
    _drop_session_for_restore()

# Validate session still active (check at most once per 5 minutes)
_last_auth_check = st.session_state.get("_auth_checked_at", 0)
if time.time() - _last_auth_check > 300:
    try:
        _sb_client.auth.get_user()
        st.session_state["_auth_checked_at"] = time.time()
    except Exception as e:
        # Type only: the message may carry a token.
        logger.warning("Session check failed, restoring from browser: %s",
                       type(e).__name__)
        _drop_session_for_restore()


def _get_tt_token():
    """Get per-user Tastytrade refresh token from session or DB."""
    if "tt_refresh_token" not in st.session_state:
        st.session_state["tt_refresh_token"] = load_credential(_sb_client, "tastytrade_refresh_token")
    return st.session_state.get("tt_refresh_token")


def _get_ibkr_credentials():
    """Get per-user IBKR credentials from session or DB."""
    if "ibkr_credentials" not in st.session_state:
        st.session_state["ibkr_credentials"] = load_ibkr_credentials(_sb_client)
    if "t212_credentials" not in st.session_state:
        st.session_state["t212_credentials"] = load_t212_credentials(_sb_client)
    return st.session_state.get("ibkr_credentials")


def _is_auth_error(exc):
    """Detect if an exception is a broker authentication/token error."""
    msg = str(exc).lower()
    return any(p in msg for p in (
        "401", "unauthorized", "invalid_token", "token expired",
        "refresh_token", "authentication", "forbidden",
        "invalid_grant", "grant revoked",
    ))


def _render_welcome_page():
    """Full welcome page for users without a Tastytrade connection."""
    st.markdown(
        "<style>.block-container { max-width: 900px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="hero-card">'
        '<p class="hero-value" style="font-size:2.4rem;letter-spacing:-0.02em">Welcome to Lazy Theta</p>'
        '<p class="hero-sub" style="font-size:1.05rem;max-width:560px;margin:12px auto 0">'
        'See every holding against its fair value, the index and your own record.</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    _num = (
        'display:inline-flex;align-items:center;justify-content:center;'
        'width:36px;height:36px;border-radius:50%;'
        'color:#fff;font-weight:700;font-size:1rem;margin-bottom:12px'
    )
    _card = (
        'background:var(--card);border:1px solid var(--border-medium);'
        'border-radius:16px;padding:28px 20px;text-align:center'
    )

    st.markdown(
        f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px">'
        f'<div style="{_card}">'
        f'<div style="{_num};background:var(--accent)">1</div>'
        f'<h4 style="margin:0 0 8px 0;font-size:1rem">Connect your Broker</h4>'
        f'<p style="color:var(--text-muted);font-size:0.85rem;margin:0">'
        f'Link Tastytrade, Interactive Brokers or Trading 212 to see positions, P&L and your track record in real time.</p>'
        f'<p style="color:var(--accent);font-size:0.8rem;font-weight:600;margin:10px 0 0 0">'
        f'Important: please read below</p>'
        f'</div>'
        f'<div style="{_card}">'
        f'<div style="{_num};background:var(--accent)">2</div>'
        f'<h4 style="margin:0 0 8px 0;font-size:1rem">Track your Portfolio</h4>'
        f'<p style="color:var(--text-muted);font-size:0.85rem;margin:0">'
        f'Positions, day moves, deployment and how each holding does against the index</p>'
        f'</div>'
        f'<div style="{_card}">'
        f'<div style="{_num};background:var(--accent)">3</div>'
        f'<h4 style="margin:0 0 8px 0;font-size:1rem">Build your Watchlist</h4>'
        f'<p style="color:var(--text-muted);font-size:0.85rem;margin:0">'
        f'Run DCF valuations, set a buy price and let the quality screen find the next name</p>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div style="height:24px"></div>', unsafe_allow_html=True)
    _, btn1, _, btn2, _ = st.columns([1, 1.2, 0.6, 1.2, 1])
    with btn1:
        st.button("Connect Account", type="primary", width="stretch",
                   key="welcome_connect",
                   on_click=lambda: st.session_state.update({"_account_page": "Connect your Broker"}))
    with btn2:
        st.button("Explore Watchlist", type="primary", width="stretch",
                   key="welcome_watchlist",
                   on_click=lambda: st.session_state.update({"nav_radio": "Watchlist", "_account_page": None}))

    st.markdown(
        '<div style="background:var(--card);border:1px solid var(--border-medium);'
        'border-radius:16px;padding:24px 28px;margin-top:8px;'
        'display:flex;align-items:flex-start;gap:16px">'
        '<span style="font-size:1.6rem;line-height:1">&#x1f512;</span>'
        '<div>'
        '<p style="margin:0 0 6px 0;font-weight:600;font-size:0.95rem">'
        '<span style="color:var(--accent);font-weight:700">Important!</span> Read-only connection</p>'
        '<p style="margin:0;color:var(--text-muted);font-size:0.85rem;line-height:1.5">'
        'Lazy Theta uses <b>read-only</b> API access for both Tastytrade and Interactive Brokers. '
        'We can only <b>view</b> your positions and history, '
        'we cannot place trades, move funds, or modify your account in any way. '
        'Your credentials are encrypted and stored securely. '
        'You can disconnect at any time in Connect your Broker.</p>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def _render_connect_prompt():
    """Compact prompt shown on pages that require Tastytrade connection."""
    st.markdown(
        '<div style="background:var(--card);border:1px solid var(--border-medium);'
        'border-radius:16px;padding:32px;text-align:center;max-width:520px;margin:80px auto">'
        '<p style="font-size:1.6rem;margin:0 0 8px 0">&#x1f512;</p>'
        '<h3 style="margin:0 0 8px 0">Connect a Broker</h3>'
        '<p style="color:var(--text-muted);font-size:0.9rem;margin:0 0 20px 0">'
        'This page requires a broker connection (Tastytrade, Interactive Brokers or Trading 212). '
        'We use <b>read-only</b> access, no trades can be placed through this app.</p>'
        '</div>',
        unsafe_allow_html=True,
    )
    _, btn_col, _ = st.columns([1, 1, 1])
    with btn_col:
        st.button("Connect your Broker", type="primary", width="stretch",
                   key=f"connect_btn_{st.session_state.get('nav_radio', '')}",
                   on_click=lambda: st.session_state.update({"_account_page": "Connect your Broker"}))
    st.stop()


# ── Theme ──
if 'dark_mode' not in st.session_state:
    st.session_state.dark_mode = False

THEME = {
    'light': {
        'bg':             '#fafaf8',
        'bg_secondary':   '#f5f4f0',
        'card':           '#fff',
        'card_alt':       '#f9f9fb',
        'text':           '#1d1d1f',
        'text_muted':     '#86868b',
        'border':         'rgba(0,0,0,0.04)',
        'border_medium':  '#d2d2d7',
        'border_light':   '#e8e8ed',
        'shadow':         '0 1px 3px rgba(0,0,0,0.04)',
        'shadow_hover':   '0 2px 8px rgba(0,0,0,0.06)',
        'accent':         '#81b29a',
        'accent_hover':   '#6fa88a',
        'accent_light':   'rgba(129,178,154,0.06)',
        'accent_fill':    'rgba(129,178,154,0.15)',
        'accent_focus':   'rgba(129,178,154,0.2)',
        'red':            '#e07a5f',
        'red_light':      'rgba(224,122,95,0.15)',
        'pill_bg':        'rgba(255,255,255,0.7)',
        'pill_border':    'rgba(255,255,255,0.5)',
        'scrollbar':      '#c4c4c6',
        'grid':           '#f0f0f2',
        'input_bg':       '#fafafa',
        'info_bg':        '#f7f8fa',
        'noise_opacity':  '0.03',
        'divider':        'rgba(0,0,0,0.06)',
        'separator':      'rgba(128,128,128,0.25)',
        'row_alt':        '#f9f9fb',
        'spinner_border': '#e5e5ea',
        'overlay_bg':     '#fafaf8',
        'delete_bg':      '#fee2e2',
        'delete_border':  '#ef4444',
        'delete_text':    '#dc2626',
        'chart_font':     '#1d1d1f',
        'chart_grid':     '#f0f0f2',
        'chart_paper':    'rgba(0,0,0,0)',
        'chart_plot':     'rgba(0,0,0,0)',
        'chart_zero':     '#d2d2d7',
        'tv_bg':          'rgba(0,0,0,0.03)',
    },
    'dark': {
        'bg':             '#1c1c1e',
        'bg_secondary':   '#2c2c2e',
        'card':           '#2c2c2e',
        'card_alt':       '#3a3a3c',
        'text':           '#f5f5f7',
        'text_muted':     '#98989d',
        'border':         'rgba(255,255,255,0.06)',
        'border_medium':  '#5a5a5e',
        'border_light':   '#3a3a3c',
        'shadow':         '0 1px 3px rgba(0,0,0,0.3)',
        'shadow_hover':   '0 2px 8px rgba(0,0,0,0.4)',
        'accent':         '#81b29a',
        'accent_hover':   '#93c4ac',
        'accent_light':   'rgba(129,178,154,0.12)',
        'accent_fill':    'rgba(129,178,154,0.25)',
        'accent_focus':   'rgba(129,178,154,0.3)',
        'red':            '#e07a5f',
        'red_light':      'rgba(224,122,95,0.25)',
        'pill_bg':        'transparent',
        'pill_border':    'transparent',
        'scrollbar':      '#48484a',
        'grid':           '#636366',
        'input_bg':       '#3a3a3c',
        'info_bg':        '#2c2c2e',
        'noise_opacity':  '0.015',
        'divider':        'rgba(255,255,255,0.08)',
        'separator':      'rgba(128,128,128,0.25)',
        'row_alt':        '#252527',
        'spinner_border': '#48484a',
        'overlay_bg':     '#1c1c1e',
        'delete_bg':      'rgba(220,38,38,0.15)',
        'delete_border':  '#ef4444',
        'delete_text':    '#f87171',
        'chart_font':     '#f5f5f7',
        'chart_grid':     '#3a3a3c',
        'chart_paper':    'rgba(0,0,0,0)',
        'chart_plot':     'rgba(0,0,0,0)',
        'chart_zero':     '#48484a',
        'tv_bg':          'rgba(255,255,255,0.04)',
    },
}

_mode = 'dark' if st.session_state.dark_mode else 'light'
T = THEME[_mode]

# ── Custom CSS ──
st.markdown(f"""
<style>
:root {{
    --bg: {T['bg']};
    --bg-secondary: {T['bg_secondary']};
    --card: {T['card']};
    --card-alt: {T['card_alt']};
    --text: {T['text']};
    --text-muted: {T['text_muted']};
    --border: {T['border']};
    --border-medium: {T['border_medium']};
    --border-light: {T['border_light']};
    --shadow: {T['shadow']};
    --shadow-hover: {T['shadow_hover']};
    --accent: {T['accent']};
    --accent-hover: {T['accent_hover']};
    --accent-light: {T['accent_light']};
    --accent-fill: {T['accent_fill']};
    --accent-focus: {T['accent_focus']};
    --red: {T['red']};
    --red-light: {T['red_light']};
    --pill-bg: {T['pill_bg']};
    --pill-border: {T['pill_border']};
    --scrollbar: {T['scrollbar']};
    --grid: {T['grid']};
    --input-bg: {T['input_bg']};
    --info-bg: {T['info_bg']};
    --noise-opacity: {T['noise_opacity']};
    --divider: {T['divider']};
    --row-alt: {T['row_alt']};
    --spinner-border: {T['spinner_border']};
    --overlay-bg: {T['overlay_bg']};
}}

    /* ── Theme overrides — force Streamlit containers to use our palette ── */
    .stApp {{
        background-color: var(--bg) !important;
    }}
    .stApp > header {{
        background-color: var(--bg) !important;
    }}
    [data-testid="stHeader"] {{
        background-color: var(--bg) !important;
    }}
    [data-testid="stToolbar"] {{
        background-color: var(--bg) !important;
    }}
    .stApp [data-testid="stAppViewContainer"] {{
        background-color: var(--bg) !important;
    }}
    .stApp [data-testid="stMain"] {{
        background-color: var(--bg) !important;
    }}
    section[data-testid="stSidebar"] > div {{
        background-color: var(--bg-secondary) !important;
    }}

    /* ── Refined with Edge ── */

    /* Global typography — DM Serif Display (headers) + DM Sans (body) */
    @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {{
        font-family: 'DM Sans', -apple-system, BlinkMacSystemFont,
                     'Helvetica Neue', Arial, sans-serif;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }}

    /* Subtle noise texture overlay */
    body::before {{
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: var(--noise-opacity);
        background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
        background-repeat: repeat;
        background-size: 256px 256px;
    }}

    /* Page load animation */
    @keyframes fadeInUp {{
        from {{ opacity: 0; transform: translateY(12px); }}
        to {{ opacity: 1; transform: translateY(0); }}
    }}

    /* Custom scrollbar */
    ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
    ::-webkit-scrollbar-track {{ background: transparent; }}
    ::-webkit-scrollbar-thumb {{ background: var(--scrollbar); border-radius: 3px; }}
    ::-webkit-scrollbar-thumb:hover {{ background: var(--accent); }}

    /* Focus states */
    *:focus-visible {{
        outline: 2px solid var(--accent) !important;
        outline-offset: 2px !important;
    }}

    /* Main content area */
    .main .block-container {{
        padding-top: 3rem;
    }}

    /* Headings — Editorial serif */
    h1, h2, h3 {{
        font-family: 'DM Serif Display', Georgia, 'Times New Roman', serif !important;
        color: var(--text) !important;
        font-weight: 400 !important;
        letter-spacing: -0.01em !important;
    }}
    h2 {{ font-size: 2rem !important; }}
    h3 {{ font-size: 1.4rem !important; }}

    p, li, label, span {{
        color: var(--text);
    }}

    /* Metric cards — with subtle depth */
    [data-testid="stMetric"] {{
        background: var(--card);
        border: none;
        border-radius: 18px;
        padding: 20px 24px;
        box-shadow: var(--shadow);
        animation: fadeInUp 0.4s ease-out both;
    }}
    [data-testid="stMetric"]:nth-child(1) {{ animation-delay: 0s; }}
    [data-testid="stMetric"]:nth-child(2) {{ animation-delay: 0.05s; }}
    [data-testid="stMetric"]:nth-child(3) {{ animation-delay: 0.1s; }}
    [data-testid="stMetric"]:nth-child(4) {{ animation-delay: 0.15s; }}
    [data-testid="stMetric"] label {{
        color: var(--text-muted);
        font-size: 0.75rem;
        font-weight: 500;
        letter-spacing: 0.01em;
        text-transform: uppercase;
    }}
    [data-testid="stMetric"] [data-testid="stMetricValue"] {{
        font-weight: 600;
        color: var(--text);
        font-size: 1.3rem;
    }}

    /* Hero card — editorial with green accent */
    .hero-card {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 48px 32px;
        box-shadow: var(--shadow);
        text-align: center;
        margin-bottom: 32px;
        animation: fadeInUp 0.4s ease-out both;
    }}
    .hero-card .hero-label {{
        color: var(--text-muted);
        font-size: 0.85rem;
        font-weight: 500;
        margin: 0 0 8px 0;
        letter-spacing: 0.01em;
        text-transform: uppercase;
    }}
    .hero-card .hero-value {{
        font-family: 'DM Sans', -apple-system, sans-serif;
        font-size: 3.2rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.03em;
    }}
    .hero-card .hero-sub {{
        color: var(--text-muted);
        font-size: 0.95rem;
        font-weight: 400;
        margin: 12px 0 0 0;
    }}
    .hero-green {{ color: var(--accent); }}
    .hero-red {{ color: var(--red); }}

    /* Stat blocks — label on top, value below (dashboard-style) */
    .stat-row {{
        display: flex;
        justify-content: center;
        gap: 32px;
        margin: 24px 0 0 0;
        flex-wrap: wrap;
        align-items: flex-start;
    }}
    .stat-pill {{
        background: transparent;
        backdrop-filter: none;
        -webkit-backdrop-filter: none;
        border: none;
        border-radius: 0;
        padding: 0;
        font-size: 0.82rem;
        color: var(--text-muted);
        font-weight: 400;
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
        line-height: 1.35;
        min-width: 60px;
    }}
    .stat-pill b {{
        color: var(--text);
        font-weight: 700;
        font-size: 1.15rem;
        margin-top: 4px;
    }}

    /* Tabs: bar and each content panel are separate blocks (not one card) */
    [data-testid="stTabs"] {{
        background: transparent;
        padding: 0;
        margin-bottom: 8px;
        animation: fadeInUp 0.4s ease-out both;
    }}
    /* The tab bar as its own card strip */
    [data-testid="stTabs"] [data-baseweb="tab-list"] {{
        background: var(--card);
        border-radius: 18px;
        border-top: 3px solid var(--accent);
        box-shadow: var(--shadow);
        padding: 4px 18px;
        margin-bottom: 6px;
    }}
    /* Tab content panels stay transparent; each tab supplies its own cards. */
    /* Per-tab content cards (DCF / Reverse DCF / Peers / Dividend / SOTP) */
    [class*="st-key-tabcard_"] {{
        background: var(--card);
        border-top: 3px solid var(--accent);
        border-radius: 24px;
        box-shadow: var(--shadow);
        padding: 24px 28px;
        margin-bottom: 22px;
    }}
    /* Business tab: the Revenue section wraps the segment/geography panels
       in a qc-section-styled card of its own (built with st.columns rather
       than question_cards.section_html, since the geography column holds a
       Plotly chart). .qc-label is repeated here so the label above it
       renders correctly even before question_cards.STYLE has been emitted
       elsewhere on the page. */
    .st-key-qc_revenue_section {{
        background: var(--card);
        border-top: 3px solid var(--accent);
        border-radius: 24px;
        box-shadow: var(--shadow);
        padding: 20px 24px 24px;
        margin: 0 0 18px;
    }}
    /* Overview tab: the "Price vs S&P 500" section holds the range control
       and the Plotly chart, so it is a keyed container styled like
       .st-key-qc_revenue_section instead of question_cards.section_html.
       The Company and Key figures sections around it are pure HTML. */
    .st-key-qc_ov_price {{
        background: var(--card);
        border-top: 3px solid var(--accent);
        border-radius: 24px;
        box-shadow: var(--shadow);
        padding: 20px 24px 24px;
        margin: 0 0 18px;
    }}
    /* Business tab: the "BY GEOGRAPHY" column (header + Plotly map + legend)
       as one continuous flat panel, matching the segment panel's own flat
       background instead of three separately-rounded pieces with a seam
       around the chart. business_revenue.geography_header_html/
       geography_legend_html render transparent so this is the only
       background. The inner-block gap is tightened so the header, chart and
       legend sit close together like one panel. */
    .st-key-qc_geo_panel {{
        background: color-mix(in srgb, var(--text) 4%, var(--card));
        border-radius: 16px;
        padding: 16px 18px;
        min-height: 470px;
        box-sizing: border-box;
    }}
    .st-key-qc_geo_panel [data-testid="stVerticalBlock"] {{
        gap: .25rem !important;
    }}
    .qc-label {{
        font-size: .72rem;
        font-weight: 700;
        letter-spacing: .07em;
        text-transform: uppercase;
        color: var(--text-muted);
        margin: 0 0 14px;
    }}
    /* Inputs inside tabs card — subtle spreadsheet cell style */
    [data-testid="stTabs"] .stNumberInput > div,
    [data-testid="stTabs"] .stNumberInput > div > div,
    [data-testid="stTabs"] .stNumberInput [data-baseweb="input"],
    [data-testid="stTabs"] .stNumberInput [data-baseweb="input"] > div {{
        background: {T['bg_secondary']} !important;
        border: none !important;
        border-radius: 4px !important;
        box-shadow: none !important;
    }}
    [data-testid="stTabs"] .stNumberInput > div > div {{
        border: 1px solid var(--grid) !important;
    }}
    [data-testid="stTabs"] .stNumberInput > div > div:focus-within {{
        border-color: var(--accent) !important;
    }}
    [data-testid="stTabs"] .stNumberInput > div > div > input,
    [data-testid="stTabs"] .stNumberInput input[type="number"] {{
        background: transparent !important;
        border: none !important;
        border-radius: 0 !important;
        padding: 4px 6px !important;
        font-size: 0.82rem !important;
        text-align: right !important;
        box-shadow: none !important;
    }}
    [data-testid="stTabs"] .stNumberInput button {{
        display: none !important;
    }}

    /* Success banner (DCF page) */
    .success-banner {{
        background: var(--card);
        border: none;
        border-radius: 24px;
        padding: 40px 32px;
        margin: 24px 0;
        text-align: center;
        box-shadow: var(--shadow);
        animation: fadeInUp 0.4s ease-out both;
    }}
    .success-banner h2 {{
        color: var(--text);
        margin: 0 0 8px 0;
        font-size: 1.5rem;
        font-weight: 600;
    }}
    .success-banner p {{
        color: var(--text-muted);
        margin: 0;
        font-size: 0.95rem;
        font-weight: 400;
    }}

    /* Chart container */
    .chart-label {{
        color: var(--text-muted);
        font-size: 0.75rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 8px;
    }}

    /* Hide streamlit branding */
    #MainMenu {{visibility: hidden;}}
    footer {{visibility: hidden;}}

    /* Form styling — Apple clean */
    .stForm {{
        border: none !important;
        border-radius: 0 !important;
        padding: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
    }}
    [data-testid="stFormBorder"] {{
        border: none !important;
        padding: 0 !important;
        background: transparent !important;
        box-shadow: none !important;
    }}

    /* Buttons — Green accent */
    .stButton > button[kind="primary"],
    .stDownloadButton > button[kind="primary"],
    .stFormSubmitButton > button[kind="primary"] {{
        background-color: var(--accent) !important;
        color: white !important;
        border: none !important;
        border-radius: 980px !important;
        padding: 12px 24px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
        transition: background-color 0.2s ease !important;
    }}
    .stButton > button[kind="primary"]:hover,
    .stDownloadButton > button[kind="primary"]:hover,
    .stFormSubmitButton > button[kind="primary"]:hover {{
        background-color: var(--accent-hover) !important;
    }}

    .stButton > button[kind="secondary"],
    .stDownloadButton > button[kind="secondary"] {{
        background-color: transparent !important;
        color: var(--accent) !important;
        border: none !important;
        border-radius: 980px !important;
        padding: 12px 24px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
    }}
    .stButton > button[kind="secondary"]:hover,
    .stDownloadButton > button[kind="secondary"]:hover {{
        background-color: var(--accent-light) !important;
    }}

    /* Text inputs — clean Apple style */
    .stTextInput > div > div,
    .stNumberInput > div > div {{
        border: 1px solid var(--border-medium) !important;
        border-radius: 12px !important;
        background: var(--card) !important;
        transition: border-color 0.2s ease !important;
    }}
    .stTextInput > div > div:focus-within,
    .stNumberInput > div > div:focus-within {{
        border-color: var(--accent) !important;
        box-shadow: 0 0 0 3px var(--accent-focus) !important;
    }}
    .stTextInput > div > div > input,
    .stNumberInput > div > div > input {{
        border: none !important;
        padding: 10px 14px !important;
        font-size: 0.95rem !important;
        background: transparent !important;
        color: var(--text) !important;
        outline: none !important;
        box-shadow: none !important;
    }}

    /* Widget labels — force theme color */
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] label,
    .stNumberInput label,
    .stTextInput label,
    .stSelectbox label,
    .stTextArea label,
    .stSlider label,
    .stCheckbox label,
    .stMultiSelect label {{
        color: var(--text) !important;
    }}

    /* Number input — full override for dark mode */
    .stNumberInput > div > div > div > button,
    .stNumberInput button {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
        color: var(--text) !important;
    }}
    .stNumberInput > div > div,
    .stNumberInput > div > div > div {{
        background-color: var(--card) !important;
    }}
    .stNumberInput [data-baseweb="input"],
    .stNumberInput [data-baseweb="input"] > div {{
        background-color: var(--card) !important;
        border: none !important;
        box-shadow: none !important;
    }}
    .stNumberInput input[type="number"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}
    .stTextInput [data-baseweb="input"],
    .stTextInput [data-baseweb="input"] > div {{
        background-color: var(--card) !important;
        border: none !important;
        box-shadow: none !important;
    }}
    .stTextInput input[type="text"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}
    /* Text area */
    .stTextArea textarea {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border: 1px solid var(--border-medium) !important;
    }}
    .stTextArea [data-baseweb="textarea"],
    .stTextArea [data-baseweb="textarea"] > div {{
        background-color: var(--card) !important;
    }}

    /* Catch-all for any remaining white inputs */
    [data-baseweb="input"],
    [data-baseweb="input"] > div,
    [data-baseweb="input"] > div > div,
    [data-baseweb="select"] > div,
    [data-baseweb="select"] > div > div {{
        background-color: var(--card) !important;
        background: var(--card) !important;
    }}
    [data-baseweb="input"] input {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}

    /* Container with border=True */
    [data-testid="stVerticalBlockBorderWrapper"] > div,
    [data-testid="stVerticalBlockBorderWrapper"] {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
        color: var(--text) !important;
    }}

    /* All text inside containers */
    [data-testid="stVerticalBlockBorderWrapper"] p,
    [data-testid="stVerticalBlockBorderWrapper"] span,
    [data-testid="stVerticalBlockBorderWrapper"] label,
    [data-testid="stVerticalBlockBorderWrapper"] div {{
        color: var(--text);
    }}

    /* Tabs — text color */
    .stTabs [data-baseweb="tab-list"] button {{
        color: var(--text-muted) !important;
    }}
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {{
        color: var(--text) !important;
    }}

    /* Markdown text inside widgets and expanders */
    [data-testid="stExpanderDetails"] p,
    [data-testid="stExpanderDetails"] span,
    [data-testid="stExpanderDetails"] label,
    [data-testid="stExpanderDetails"] div {{
        color: var(--text);
    }}

    /* Global table styling for dark mode */
    table td, table th {{
        color: var(--text) !important;
        border-color: var(--grid) !important;
    }}
    table tr {{
        border-color: var(--grid) !important;
    }}
    table {{
        color: var(--text) !important;
        border-color: var(--grid) !important;
    }}
    table thead tr {{
        border-bottom: 1px solid var(--grid) !important;
    }}
    table tbody tr {{
        border-top: 1px solid var(--grid) !important;
    }}

    /* Slider label + value */
    .stSlider label, .stSlider [data-testid="stTickBarMin"],
    .stSlider [data-testid="stTickBarMax"] {{
        color: var(--text) !important;
    }}

    /* Form submit button */
    [data-testid="stFormSubmitButton"] button {{
        background-color: var(--accent) !important;
        color: white !important;
        border: none !important;
    }}

    [data-baseweb="select"] {{
        background-color: var(--card) !important;
    }}

    /* Multiselect */
    .stMultiSelect > div > div,
    .stMultiSelect [data-baseweb="select"],
    .stMultiSelect [data-baseweb="select"] > div,
    .stMultiSelect [data-baseweb="input"],
    .stMultiSelect [data-baseweb="input"] > div {{
        background-color: var(--card) !important;
        background: var(--card) !important;
        border-color: var(--border-medium) !important;
        color: var(--text) !important;
    }}
    .stMultiSelect > div > div {{
        border-radius: 12px !important;
    }}
    .stMultiSelect [data-baseweb="tag"] {{
        background-color: var(--accent-fill) !important;
        color: var(--text) !important;
    }}
    .stMultiSelect [data-baseweb="tag"] span {{
        color: var(--text) !important;
    }}
    .stMultiSelect input {{
        background-color: var(--card) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
    }}
    .stMultiSelect svg {{
        fill: var(--text-muted) !important;
    }}
    /* Form submit button */
    .stFormSubmitButton button {{
        background-color: var(--accent) !important;
        color: #fff !important;
        border: none !important;
    }}

    /* Select boxes */
    .stSelectbox > div > div {{
        border-radius: 12px !important;
        border-color: var(--border-medium) !important;
        background-color: var(--card) !important;
    }}
    .stSelectbox > div > div > div {{
        color: var(--text) !important;
    }}
    /* Selectbox placeholder text */
    .stSelectbox [data-baseweb="select"] [data-testid="stMarkdownContainer"],
    .stSelectbox [data-baseweb="select"] span[aria-live="polite"] {{
        color: var(--text-muted) !important;
    }}
    /* Selectbox / multiselect dropdown list — cover all Streamlit/BaseWeb variants */
    [data-baseweb="popover"],
    [data-baseweb="popover"] > div,
    [data-baseweb="menu"],
    [data-baseweb="menu"] > div,
    [data-baseweb="list"],
    [data-baseweb="list"] > div,
    [role="listbox"],
    ul[id^="bui-"] {{
        background-color: var(--card) !important;
        border: 1px solid var(--border-medium) !important;
        color: var(--text) !important;
    }}
    [data-baseweb="popover"] li,
    [data-baseweb="menu"] li,
    [data-baseweb="list"] li,
    [role="listbox"] li,
    [role="option"],
    ul[id^="bui-"] li {{
        color: var(--text) !important;
        background-color: var(--card) !important;
    }}
    [data-baseweb="popover"] li:hover,
    [data-baseweb="menu"] li:hover,
    [data-baseweb="list"] li:hover,
    [role="option"]:hover,
    ul[id^="bui-"] li:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Highlighted/focused/selected option in dropdown —
       BaseWeb sets inline styles on focused items; override every possible state */
    [data-baseweb="menu"] li[aria-selected="true"],
    [data-baseweb="list"] li[aria-selected="true"],
    [role="option"][aria-selected="true"],
    [role="option"][data-highlighted="true"],
    [data-baseweb="menu"] [data-highlighted="true"],
    [data-baseweb="list"] [data-highlighted="true"],
    [role="option"]:focus,
    [role="option"]:focus-visible,
    [role="option"][aria-current="true"],
    li[aria-selected="true"],
    li[data-highlighted="true"] {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Force all selectbox dropdown overlay elements dark */
    div[data-baseweb="popover"] *,
    div[data-baseweb="select"] [role="listbox"] *,
    .stSelectbox div[data-baseweb] ul,
    .stSelectbox div[data-baseweb] ul li,
    .stMultiSelect div[data-baseweb] ul,
    .stMultiSelect div[data-baseweb] ul li {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    .stSelectbox div[data-baseweb] ul li:hover,
    .stMultiSelect div[data-baseweb] ul li:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Nuclear override: BaseWeb applies inline background-color on highlighted
       items via style attribute. Target every possible li inside dropdown
       containers with attribute selectors to beat inline specificity. */
    [data-baseweb="popover"] li[style],
    [data-baseweb="menu"] li[style],
    [data-baseweb="list"] li[style],
    [role="listbox"] li[style],
    ul[id^="bui-"] li[style] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-baseweb="popover"] li[style]:hover,
    [data-baseweb="menu"] li[style]:hover,
    [data-baseweb="list"] li[style]:hover,
    [role="listbox"] li[style]:hover,
    ul[id^="bui-"] li[style]:hover,
    [data-baseweb="popover"] li[style][aria-selected="true"],
    [data-baseweb="menu"] li[style][aria-selected="true"],
    [data-baseweb="list"] li[style][aria-selected="true"],
    [role="listbox"] li[style][aria-selected="true"],
    ul[id^="bui-"] li[style][aria-selected="true"] {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Ultra-aggressive: target Streamlit emotion-cache classes inside dropdowns.
       Streamlit injects CSS-in-JS classes (st-emotion-cache-*) that set white
       backgrounds on highlighted items. Boost specificity with :where(:root) hack. */
    :root [data-baseweb="popover"] [class*="st-emotion-cache"],
    :root [data-baseweb="menu"] [class*="st-emotion-cache"],
    :root [data-baseweb="list"] [class*="st-emotion-cache"],
    :root [role="listbox"] [class*="st-emotion-cache"],
    :root ul[id^="bui-"] [class*="st-emotion-cache"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    :root [data-baseweb="popover"] [class*="st-emotion-cache"]:hover,
    :root [data-baseweb="menu"] [class*="st-emotion-cache"]:hover,
    :root [data-baseweb="list"] [class*="st-emotion-cache"]:hover,
    :root [role="listbox"] [class*="st-emotion-cache"]:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Also target Streamlit's auto-generated st-XX classes on option items */
    :root [role="option"][class*="st-"] {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    :root [role="option"][class*="st-"]:hover,
    :root [role="option"][class*="st-"][aria-selected="true"] {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Final fallback: any element inside a popover/listbox with white-ish bg */
    :root :is([data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"]) li {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    :root :is([data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"]) li:hover {{
        background-color: var(--accent-light) !important;
        color: var(--text) !important;
    }}
    /* Streamlit popover (st.popover) button & content */
    [data-testid="stPopover"] button,
    [data-testid="stPopover"] button * {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-testid="stPopover"] button {{
        border: 1px solid var(--border-medium) !important;
    }}
    [data-testid="stPopoverBody"],
    [data-testid="stPopoverBody"] > div {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
    }}
    /* st.pills — dark mode overrides */
    [data-testid="stBaseButton-pills"],
    [data-testid="stBaseButton-pills"] * {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-testid="stBaseButton-pills"] {{
        border: 1px solid var(--border-medium) !important;
    }}
    [data-testid="stBaseButton-pillsActive"],
    [data-testid="stBaseButton-pillsActive"] * {{
        background-color: var(--accent) !important;
        color: white !important;
    }}
    [data-testid="stBaseButton-pillsActive"] {{
        border-color: var(--accent) !important;
    }}
    /* Toggle styling */
    .stToggle label span {{
        color: var(--text) !important;
    }}
    /* Metric card text overrides */
    [data-testid="stMetricValue"] {{
        color: var(--text) !important;
    }}
    /* Tab labels */
    .stTabs [data-baseweb="tab-list"] button {{
        color: var(--text-muted) !important;
    }}
    .stTabs [data-baseweb="tab-list"] button[aria-selected="true"] {{
        color: var(--text) !important;
    }}
    .stTabs [data-baseweb="tab-highlight"] {{
        background-color: var(--accent) !important;
    }}
    .stTabs [data-baseweb="tab-border"] {{
        background-color: transparent !important;
    }}

    /* Sliders — Green accent */
    .stSlider [data-baseweb="slider"] [role="slider"] {{
        background-color: var(--accent) !important;
    }}

    /* Expanders — card style with accent left border */
    [data-testid="stExpander"] {{
        background-color: var(--card) !important;
        border: 1px solid var(--border-medium);
        border-left: 3px solid var(--accent);
        border-radius: 12px;
        box-shadow: var(--shadow);
        overflow: hidden;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        animation: fadeInUp 0.4s ease-out both;
    }}
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] summary * {{
        background-color: var(--card) !important;
        color: var(--text) !important;
    }}
    [data-testid="stExpander"] [data-testid="stExpanderDetails"] {{
        background-color: var(--card) !important;
    }}
    [data-testid="stExpander"]:hover {{
        transform: translateY(-2px);
        box-shadow: var(--shadow-hover);
    }}

    /* Dataframes — rounded, clean */
    [data-testid="stDataFrame"] {{
        border-radius: 14px;
        overflow: hidden;
    }}

    /* Sidebar — minimal Apple style */
    section[data-testid="stSidebar"] {{
        background: var(--card);
        border-right: none;
    }}
    section[data-testid="stSidebar"] [data-testid="stRadio"] label {{
        font-weight: 500;
        color: var(--text);
        transition: background-color 0.2s ease;
    }}
    /* Radio / checkbox accent — green */
    [data-testid="stRadio"] [role="radiogroup"] label[data-checked="true"]::before,
    .stRadio div[role="radiogroup"] label span[data-checked="true"] {{
        background-color: var(--accent) !important;
        border-color: var(--accent) !important;
    }}
    input[type="radio"]:checked {{
        accent-color: var(--accent) !important;
    }}
    /* Pills active state */
    button[data-active="true"],
    [data-testid="stPills"] button[aria-pressed="true"],
    [data-testid="stPills"] button[aria-selected="true"] {{
        background-color: var(--accent) !important;
        color: white !important;
        border-color: var(--accent) !important;
    }}
    /* Streamlit primary color override */
    :root {{
        --primary-color: var(--accent) !important;
    }}

    /* Toolbar: remove gap between buttons */
    .st-key-toolbar_inline [data-testid="stHorizontalBlock"] {{
        gap: 0 !important;
    }}
    .st-key-toolbar_inline [data-testid="stColumn"] {{
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }}

    /* Dividers — consistent subtle separators */
    hr {{
        border-color: var(--divider) !important;
        opacity: 1;
    }}

    /* Links */
    a {{
        color: var(--accent) !important;
        text-decoration: none !important;
    }}
    a:hover {{
        text-decoration: underline !important;
    }}

    /* Status widget */
    [data-testid="stStatusWidget"] {{
        border-radius: 18px;
    }}

    /* Cumulative Returns — white block, green accent */
    .st-key-cumulative_block {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 32px;
        box-shadow: var(--shadow);
    }}
    .st-key-cumulative_block .performer-block {{
        background: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
    }}
    .st-key-cumulative_block .performer-block:hover {{
        transform: none;
        box-shadow: none;
    }}

    /* Results hero + chart — single continuous white block */
    .st-key-results_hero {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 32px;
        box-shadow: var(--shadow);
    }}
    .st-key-results_hero .hero-card {{
        background: none;
        border-top: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
        margin-bottom: 0;
        animation: none;
    }}

    /* Valuation Bridge — same card style as tabs */
    .st-key-valuation_bridge_card {{
        background: transparent;
        border-top: 1px solid var(--border-light);
        padding: 18px 0 0 0;
        margin-top: 22px;
    }}
    .st-key-valuation_bridge_card .stNumberInput > div,
    .st-key-valuation_bridge_card .stNumberInput > div > div,
    .st-key-valuation_bridge_card .stNumberInput [data-baseweb="input"],
    .st-key-valuation_bridge_card .stNumberInput [data-baseweb="input"] > div {{
        background: var(--bg) !important;
        border: none !important;
        border-radius: 4px !important;
        box-shadow: none !important;
    }}
    .st-key-valuation_bridge_card .stNumberInput > div > div {{
        border: 1px solid var(--grid) !important;
    }}
    .st-key-valuation_bridge_card .stNumberInput > div > div:focus-within {{
        border-color: var(--accent) !important;
    }}
    .st-key-valuation_bridge_card .stNumberInput input[type="number"] {{
        background: transparent !important;
    }}

    /* Portfolio Allocation — white block, no outer frame */
    .st-key-allocation_block {{
        background: var(--card);
        border-radius: 24px;
        padding: 32px;
        box-shadow: var(--shadow);
    }}

    /* Greeks / BWD / Interest — CSS Grid, equal-height cards */
    .greeks-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 16px;
        margin-bottom: 24px;
    }}
    .greeks-grid .hero-card {{
        height: 100%;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        /* Top-aligned so both cards' headings start at the same height, but
           horizontally centred: the tables read better as a centred block than
           stretched to the card's edges. */
        justify-content: flex-start;
        align-items: center;
    }}
    /* The heading and the summary centre against the CARD; only the row table
       below them is sized by its content. Without this they centre against
       their own width, which sits off to one side of the card. */
    .greeks-grid .hero-card > h4,
    .greeks-grid .hero-card > div:not([style*="grid"]) {{
        align-self: stretch;
        text-align: center;
    }}

    /* Deployment card — single continuous white block */
    .st-key-deployment_block,
    .st-key-screener_block {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 32px;
        box-shadow: var(--shadow);
    }}
    /* The target-size stepper as ONE pill. Streamlit gives the input and the
       -/+ block their own border and radius, which on a card reads as two
       controls with a seam between them. Border on the container, nothing on
       the parts. */
    .st-key-deployment_block [data-testid="stNumberInputContainer"] {{
        background: var(--bg-secondary) !important;
        border: 1px solid var(--border-medium) !important;
        border-radius: 999px !important;
        overflow: hidden;
        box-shadow: none !important;
    }}
    .st-key-deployment_block .stNumberInput input {{
        background: transparent !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        font-weight: 600;
        padding-left: 16px;
    }}
    .st-key-deployment_block .stNumberInput button {{
        background: transparent !important;
        border: none !important;
        box-shadow: none !important;
        color: var(--text-muted) !important;
    }}
    .st-key-deployment_block .stNumberInput button:hover {{
        background: var(--grid) !important;
        color: var(--text) !important;
    }}
    /* Centre the label over the pill, otherwise a centred control sits under
       a left-aligned caption. */
    .st-key-deployment_block .stNumberInput [data-testid="stWidgetLabel"] {{
        justify-content: center;
        width: 100%;
    }}
    .st-key-deployment_block .stNumberInput [data-testid="stWidgetLabel"] p {{
        text-align: center;
        width: 100%;
    }}
    .st-key-deployment_block .hero-card {{
        background: none;
        border-top: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
        margin-bottom: 0;
        animation: none;
    }}

    /* ── Ticker cards (Holdings) ── */
    [class*="st-key-wheel_card_"] {{
        background: var(--card);
        border-radius: 24px;
        border-top: 3px solid var(--accent);
        padding: 24px 32px;
        box-shadow: var(--shadow);
        margin-bottom: 16px;
    }}
    .card-header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 14px;
    }}
    .card-left .tk-title {{
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .card-left .tk-logo {{
        width: 28px;
        height: 28px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
    }}
    .card-left .tk-name {{
        font-size: 1.1rem;
        font-weight: 700;
        color: var(--text);
        margin: 0;
    }}
    .tk-tag {{
        font-size: 0.68rem;
        padding: 1px 7px;
        border-radius: 9px;
        background: color-mix(in srgb, var(--accent) 14%, transparent);
        color: var(--accent);
        font-weight: 600;
        white-space: nowrap;
    }}
    .tk-pl {{
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 3px;
    }}
    .pl-toggle {{
        cursor: pointer;
        user-select: none;
        margin: 0;
    }}
    .pl-toggle .pl-badge {{ min-width: 96px; text-align: center; }}
    .pl-toggle-cb {{ display: none; }}
    .pl-toggle .pl-pct {{ display: none; }}
    .pl-toggle-cb:checked ~ .pl-usd {{ display: none; }}
    .pl-toggle-cb:checked ~ .pl-pct {{ display: inline-block; }}
    /* The basics as label / value rows. */
    .tk-rows {{
        margin: 0 0 14px 0;
    }}
    .tk-row {{
        display: grid;
        grid-template-columns: 72px 1fr;
        gap: 12px;
        padding: 5px 0;
        border-top: 1px solid var(--divider);
        font-size: 0.86rem;
        line-height: 1.35;
    }}
    .tk-row:last-child {{ border-bottom: 1px solid var(--divider); }}
    .tk-row-label {{
        color: var(--text-muted);
        font-size: 0.8rem;
    }}
    .tk-row-value {{
        color: var(--text);
        font-variant-numeric: tabular-nums;
    }}
    .pl-badge {{
        display: inline-block;
        padding: 6px 16px;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
        color: #fff;
    }}
    .pl-badge-green {{ background: var(--accent); }}
    .pl-badge-red {{ background: var(--red); }}

    .trade-row {{
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        align-items: center;
        gap: 16px;
        padding: 10px 0;
        border-bottom: 1px solid var(--divider);
    }}
    .trade-row:last-child {{ border-bottom: none; }}
    .trade-row .tr-desc {{ min-width: 0; }}
    .trade-row .tr-desc .tr-label {{
        font-weight: 600;
        font-size: 0.9rem;
        color: var(--text);
        margin: 0;
    }}
    .trade-row .tr-detail {{
        font-weight: 400;
        color: var(--text-muted);
        font-variant-numeric: tabular-nums;
    }}
    .trade-row .tr-desc .tr-date {{
        font-size: 0.76rem;
        color: var(--text-muted);
        margin: 0;
    }}
    .trade-row .tr-amt {{
        font-size: 0.9rem;
        font-weight: 600;
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
        margin: 0;
    }}
    .trade-row .status-badge {{
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        color: #fff;
    }}
    .status-closed {{ background: var(--accent); }}
    .status-open {{ background: var(--accent); }}
    .status-assigned {{ background: var(--text-muted); }}

    /* Section title bar */
    .section-title-bar {{
        background: var(--card);
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 10px;
        font-family: 'DM Serif Display', Georgia, serif;
        font-size: 1.1rem;
        font-weight: 400;
        color: var(--text);
        box-shadow: var(--shadow);
    }}

    /* Returns header — selectbox overlaid on title bar */
    .st-key-ret_pick_wrap {{
        position: relative;
        top: -52px;
        margin-bottom: -66px;
        padding-left: 55%;
        padding-right: 16px;
        z-index: 10;
    }}
    .st-key-ret_pick_wrap [data-testid="stVerticalBlock"] {{
        gap: 0 !important;
    }}
    /* Returns selectbox — ensure dark-mode readability */
    .st-key-ret_pick_wrap [data-baseweb="select"] {{
        background-color: var(--card) !important;
        border-color: var(--border-medium) !important;
        border-radius: 12px !important;
    }}
    .st-key-ret_pick_wrap [data-baseweb="select"] * {{
        color: var(--text) !important;
    }}
    .st-key-ret_pick_wrap [data-baseweb="select"] [data-testid="stMarkdownContainer"],
    .st-key-ret_pick_wrap [data-baseweb="select"] input::placeholder {{
        color: var(--text-muted) !important;
    }}

    /* ── Performer block — with hover lift ── */
    .performer-block {{
        background: var(--card);
        border-radius: 18px;
        padding: 24px;
        box-shadow: var(--shadow);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }}
    .performer-block:hover {{
        transform: translateY(-2px);
        box-shadow: var(--shadow-hover);
    }}
    .performer-block h4 {{
        margin: 0 0 12px 0;
        font-size: 1rem !important;
    }}
    .performer-block .portfolio-cards {{
        flex-direction: row;
        flex-wrap: wrap;
        justify-content: center;
        align-items: stretch;
    }}
    .performer-block .portfolio-card {{
        flex: 1;
        min-width: 180px;
    }}

    /* ── Portfolio strip cards ── */
    .portfolio-cards {{
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 8px;
    }}
    .portfolio-card {{
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        padding: 12px 16px;
        background: var(--card);
        border: 1px solid var(--border-medium);
        border-left: 3px solid var(--accent);
        border-radius: 14px;
        flex-wrap: wrap;
        width: 100%;
        box-sizing: border-box;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }}
    .portfolio-card:hover {{
        transform: translateY(-2px);
        box-shadow: var(--shadow-hover);
    }}
    .portfolio-card .pf-logo {{
        width: 30px;
        height: 30px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
    }}
    .portfolio-card .pf-ticker {{
        font-weight: 700;
        font-size: 1.05rem;
        color: var(--text);
        min-width: 52px;
        flex-shrink: 0;
    }}
    .portfolio-card .pf-cell {{
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
    }}
    .portfolio-card .pf-label {{
        font-size: 0.7rem;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.03em;
        line-height: 1.1;
        white-space: nowrap;
    }}
    .portfolio-card .pf-val {{
        font-size: 0.95rem;
        font-weight: 600;
        color: var(--text);
        line-height: 1.3;
        white-space: nowrap;
    }}
    .portfolio-card .pf-green {{ color: var(--accent); }}
    .portfolio-card .pf-red {{ color: var(--red); }}

    /* ── Aligned position rows ──
       A flex card sizes every cell to its own content, so "Trading 212" in one
       row and "Tastytrade" in the next push everything after them to different
       places and no column lines up with the one above it. A grid with the same
       track list on every card fixes the widths whatever is in them. The track
       count is the number of columns on screen, so it arrives as --pf-grid from
       the code that knows which are selected.

       Scoped to direct children: the option sub-cards inside <details> have
       four cells of their own and must keep sizing themselves, and everything
       else using .portfolio-card — the performer blocks, the yearly cards — is
       left alone for the same reason. */
    .pf-aligned > .portfolio-card,
    .pf-aligned > .pf-details > summary > .portfolio-card {{
        display: grid;
        grid-template-columns: var(--pf-grid);
        align-items: center;
        /* Tracks are absolute, so the leftover width collects at both ends
           rather than between the columns. */
        justify-content: center;
        /* Wider insets do double duty: they give the row air at both edges and
           they narrow the content box, and since the tracks are equal
           fractions of it, the figures move closer together at the same time.
           Right is the larger of the two because the chevron sits in it —
           measured they differ, seen they read as the same margin. */
        padding-left: 28px;
        padding-right: 34px;
        /* Tighter than the flex default: equal tracks already hold the columns
           apart, so 16px on top of that was spacing twice. */
        gap: 10px;
    }}
    /* Labels may wrap now that a track has a width of its own; the figures may
       not, because a number broken over two lines reads as two numbers. Every
       card carries the same labels, so they wrap alike and the rows stay level. */
    .pf-aligned .pf-label {{ white-space: normal; }}
    @media (max-width: 768px) {{
        /* Eight columns cannot line up on a phone. Back to wrapping, and the
           wide insets go with it — there is no room to spend on margins. */
        .pf-aligned > .portfolio-card,
        .pf-aligned > .pf-details > summary > .portfolio-card {{
            display: flex;
            padding-left: 12px;
            padding-right: 12px;
            gap: 10px;
        }}
    }}


    /* ── Performer grid (Top/Bottom side by side, stacked on mobile) ── */
    .performer-grid {{
        display: flex;
        gap: 12px;
    }}
    .performer-grid > div {{ flex: 1; min-width: 0; }}
    @media (max-width: 768px) {{
        .performer-grid {{ flex-direction: column; gap: 24px; }}
        .portfolio-card {{ gap: 10px; padding: 10px 12px; }}
        .portfolio-card .pf-cell {{ flex: 1; }}
    }}

    /* ── CSS tooltip ── */
    .css-tip {{
        position: relative;
        cursor: help;
    }}
    .css-tip::after {{
        content: attr(data-tip);
        position: absolute;
        bottom: 130%;
        right: 0;
        background: var(--card);
        color: var(--text);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 0.72rem;
        font-weight: 400;
        white-space: nowrap;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.15s;
        z-index: 100;
    }}
    .css-tip:hover::after {{
        opacity: 1;
    }}

    /* ── Expandable position cards ── */
    .pf-details {{ width: 100%; }}
    .pf-details summary {{
        list-style: none;
        cursor: pointer;
    }}
    .pf-details summary::-webkit-details-marker {{ display: none; }}
    .pf-details summary .portfolio-card {{
        border-bottom-left-radius: 14px;
        border-bottom-right-radius: 14px;
        transition: border-radius 0.15s ease;
        position: relative;
    }}
    .pf-details summary .portfolio-card::after {{
        content: "›";
        font-size: 1.6rem;
        color: var(--text-muted);
        flex-shrink: 0;
        position: absolute;
        right: 16px;
        transition: transform 0.2s ease;
    }}
    .pf-details[open] summary .portfolio-card::after {{
        transform: rotate(90deg);
    }}
    .pf-details[open] summary .portfolio-card {{
        border-bottom-left-radius: 0;
        border-bottom-right-radius: 0;
        border-bottom: none;
    }}
    .pf-details[open] > .portfolio-card {{
        border-top-left-radius: 0;
        border-top-right-radius: 0;
        margin-top: 0 !important;
    }}

    /* ── Page transition loading overlay (only on full rerun, not fragment) ── */
    @keyframes pf-spin {{
        to {{ transform: rotate(360deg); }}
    }}
    body:has([data-testid="stSidebar"] [data-stale="true"]) [data-testid="stMain"]::before {{
        content: "";
        position: fixed;
        inset: 0;
        background: var(--overlay-bg);
        z-index: 9998;
    }}
    body:has([data-testid="stSidebar"] [data-stale="true"]) [data-testid="stMain"]::after {{
        content: "";
        position: fixed;
        top: 50%;
        left: 50%;
        width: 28px;
        height: 28px;
        margin: -14px 0 0 -14px;
        border: 3px solid var(--spinner-border);
        border-top-color: var(--accent);
        border-radius: 50%;
        animation: pf-spin 0.6s linear infinite;
        z-index: 9999;
    }}

</style>
""", unsafe_allow_html=True)


# ── Helper functions ──
def _flush_clean(buf, prev_pos, status):
    """Write only key progress lines (skip noisy debug output)."""
    buf.seek(prev_pos)
    new_text = buf.read()
    # We don't show the raw output — status steps handle the display
    return buf.tell()


TELEGRAM_BOT_USERNAME = "LazyTheta_bot"


def _notif_bell_svg(color, size=20):
    """Filled bell icon in the given colour (emoji can't be recoloured)."""
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="{color}" '
        f'style="vertical-align:middle;flex:none">'
        f'<path d="M12 22a2.5 2.5 0 0 0 2.45-2h-4.9A2.5 2.5 0 0 0 12 22zm7-5v-1l-1.6-1.6'
        f'V9.5a5.5 5.5 0 0 0-4.4-5.39V3.5a1 1 0 0 0-2 0v.61A5.5 5.5 0 0 0 6.6 9.5v4.9'
        f'L5 16v1z"/></svg>'
    )


def _notif_friendly_date(iso_str, today):
    """'Today' / 'Tomorrow' / weekday / 'Mon 23 Jun', or 'Overdue · …'."""
    from datetime import date as _d
    try:
        d = _d.fromisoformat(str(iso_str))
    except Exception:
        return str(iso_str), False
    delta = (d - today).days
    if delta < 0:
        return f"Overdue · {d.strftime('%-d %b')}", True
    if delta == 0:
        return "Today", False
    if delta == 1:
        return "Tomorrow", False
    if delta < 7:
        return d.strftime("%A"), False
    return d.strftime("%a %-d %b"), False


# Fixed pre-mortem sections — same for every ticker (mirror mcp_server).
_PM_SECTIONS = [
    ("current", "Current view"),
    ("sell", "Sell triggers"),
    ("add", "Add triggers"),
    ("ignore", "Not a sell reason"),
    ("discipline", "Discipline"),
]


def _pm_lines(s):
    """Split a textarea string into cleaned bullet items (one per line)."""
    return [ln.strip(" -•\t") for ln in (s or "").split("\n") if ln.strip(" -•\t")]


def _render_premortem(pm, theme):
    """Render a structured pre-mortem as a decision board.

    Five stacked lists ran to forty-odd items on the bigger tickers — CPRT has
    45 — which is a wall you skim rather than a rule you check. The two lists
    you act on sit side by side, the one you deliberately do NOT act on sits
    beside them greyed, and process goes underneath.

    Returns None for a legacy string / empty (caller falls back to markdown).
    """
    if not isinstance(pm, dict):
        return None
    import html as _h

    from prescan_render import split_trigger

    muted = theme.get("text_muted", "#888")
    txt = theme.get("text", "#111")
    border = theme.get("border_light", "#e8e8ed")
    bg = theme.get("bg_secondary", "#f4f2ee")
    red, green = "#c0603f", "#2f8f4e"

    def _items(key):
        return [str(i) for i in (pm.get(key) or []) if str(i).strip()]

    sell, add, ignore = _items("sell"), _items("add"), _items("ignore")
    discipline = _items("discipline")
    current = str(pm.get("current", "") or "").strip()
    if not any((sell, add, ignore, discipline, current)):
        return None

    def _column(title, items, tone, dim=False):
        if not items:
            return ""
        rows = []
        for raw in items:
            cat, body = split_trigger(raw)
            tag = (f'<div style="font-size:0.62rem;font-weight:700;'
                   f'letter-spacing:0.07em;color:{tone};opacity:0.9;'
                   f'margin-bottom:1px">{_h.escape(cat)}</div>' if cat else "")
            rows.append(
                f'<li style="margin:0 0 9px 0;line-height:1.45;'
                f'font-size:0.86rem;color:{muted if dim else txt}">'
                f'{tag}{_h.escape(body)}</li>'
            )
        return (
            f'<div style="flex:1;min-width:230px">'
            f'<div style="display:flex;align-items:baseline;gap:7px;'
            f'padding-bottom:7px;margin-bottom:10px;'
            f'border-bottom:2px solid {tone}{"55" if dim else ""}">'
            f'<span style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;'
            f'text-transform:uppercase;color:{tone}">{title}</span>'
            f'<span style="font-size:0.68rem;color:{muted}">{len(items)}</span>'
            f'</div>'
            f'<ul style="margin:0;padding-left:16px">{"".join(rows)}</ul></div>'
        )

    parts = []
    if current:
        parts.append(
            f'<div style="background:{bg};border-radius:12px;padding:12px 16px;'
            f'margin-bottom:16px;font-size:0.86rem;line-height:1.5;color:{txt}">'
            f'<span style="font-size:0.62rem;font-weight:700;letter-spacing:0.08em;'
            f'text-transform:uppercase;color:{muted};display:block;'
            f'margin-bottom:4px">Current view</span>'
            f'{_h.escape(current)}</div>'
        )

    # Sell first: the trigger you most need to have decided in advance is the
    # one you will least want to act on in the moment.
    cols = (_column("Sell when", sell, red)
            + _column("Add when", add, green)
            + _column("Not a reason", ignore, muted, dim=True))
    if cols:
        parts.append(f'<div style="display:flex;gap:26px;flex-wrap:wrap;'
                     f'align-items:flex-start">{cols}</div>')

    if discipline:
        # Process, not triggers — folded away so it stops competing with the
        # rules you actually check a position against.
        rows = "".join(
            f'<li style="margin:0 0 6px 0;line-height:1.45;font-size:0.82rem;'
            f'color:{muted}">{_h.escape(i)}</li>' for i in discipline
        )
        parts.append(
            f'<details style="margin-top:18px;padding-top:12px;'
            f'border-top:1px solid {border}">'
            f'<summary style="cursor:pointer;font-size:0.68rem;font-weight:700;'
            f'letter-spacing:0.08em;text-transform:uppercase;color:{muted}">'
            f'Discipline ({len(discipline)})</summary>'
            f'<ul style="margin:10px 0 0;padding-left:16px">{rows}</ul></details>'
        )

    return "".join(parts)


def _render_notifications_panel():
    """Agenda-style notifications dashboard at the top of the watchlist: a green
    bell header with summary chips, upcoming reminders grouped by date, a recent-
    alerts feed, an add-reminder form, and Telegram linking. Fails quiet if the
    tables/session aren't ready."""
    import notifications as _notif
    from datetime import date

    try:
        items = _notif.list_notifications(_sb_client, limit=30)
        reminders = _notif.list_custom_reminders(_sb_client)
        connected = _notif.telegram_connected(_sb_client)
    except Exception:
        return

    n_unread = sum(1 for i in items if not i.get("read_at"))
    today = date.today()
    accent = T["accent"]
    muted = T["text_muted"]
    fill = T.get("accent_fill", accent)
    red = T.get("red", "#e07a5f")

    st.markdown(f"""<style>
    .st-key-notif_dash {{ border:1px solid {T['border_light']}; border-radius:16px;
        padding:14px 20px 8px; background:{T['card']}; margin:4px 0 18px;
        box-shadow:{T.get('shadow', '0 1px 3px rgba(0,0,0,0.06)')}; }}
    .notif-chip {{ font-size:0.76rem; padding:2px 10px; border-radius:980px;
        background:{fill}; color:{accent}; font-weight:600; margin-left:6px;
        white-space:nowrap; }}
    .notif-date {{ font-size:0.74rem; font-weight:700; color:{muted};
        text-transform:uppercase; letter-spacing:0.05em; margin:12px 0 2px; }}
    .notif-tk {{ background:{fill}; color:{accent}; font-weight:700; font-size:0.7rem;
        padding:1px 7px; border-radius:6px; margin-right:7px; }}
    </style>""", unsafe_allow_html=True)

    with st.container(key="notif_dash"):
        up_chip = f'<span class="notif-chip">{len(reminders)} upcoming</span>' if reminders else ""
        un_chip = f'<span class="notif-chip">{n_unread} new</span>' if n_unread else ""
        tg_chip = ('<span class="notif-chip">✓ Telegram</span>' if connected
                   else '<span class="notif-chip" style="opacity:0.6">Telegram off</span>')
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:9px;margin-bottom:2px">'
            f'{_notif_bell_svg(accent)}'
            f'<span style="font-weight:700;font-size:1.08rem">Notifications</span>'
            f'<span style="flex:1"></span>{up_chip}{un_chip}{tg_chip}</div>',
            unsafe_allow_html=True)

        # ── Agenda: upcoming reminders grouped by date ──
        if reminders:
            last_label = None
            for rem in reminders:
                label, overdue = _notif_friendly_date(rem["fire_date"], today)
                if label != last_label:
                    _c = red if overdue else muted
                    st.markdown(f'<div class="notif-date" style="color:{_c}">{label}</div>',
                                unsafe_allow_html=True)
                    last_label = label
                ac1, ac2 = st.columns([9, 0.6], vertical_alignment="center")
                tkb = f'<span class="notif-tk">{rem["ticker"]}</span>' if rem.get("ticker") else ""
                ac1.markdown(f'<div style="padding:2px 0">{tkb}{rem["text_body"]}</div>',
                             unsafe_allow_html=True)
                if ac2.button("✕", key=f"notif_rem_del_{rem['id']}", help="Delete reminder"):
                    _notif.delete_custom_reminder(_sb_client, rem["id"])
                    st.rerun()
        else:
            st.markdown(f'<div style="color:{muted};font-size:0.9rem;margin:8px 0">'
                        'Niets gepland — voeg hieronder een reminder toe.</div>',
                        unsafe_allow_html=True)

        # ── Add a reminder ──
        rc1, rc2, rc3 = st.columns([1.1, 2.8, 0.7], vertical_alignment="bottom")
        r_date = rc1.date_input("Date", key="notif_rem_date", min_value=today,
                                label_visibility="collapsed")
        r_text = rc2.text_input("Reminder", key="notif_rem_text",
                                placeholder="New reminder — e.g. Re-check AVGO after earnings",
                                label_visibility="collapsed")
        if rc3.button("Add", key="notif_rem_add", width="stretch") and r_text:
            _notif.add_custom_reminder(_sb_client, r_date, r_text)
            st.toast("Reminder added")
            st.rerun()

        # ── Price-target alerts (standalone, one-shot; not the buy-price auto) ──
        try:
            _palerts = _notif.list_price_alerts(_sb_client)
        except Exception:
            _palerts = []
        with st.expander(f"Price alerts · {len(_palerts)}" if _palerts else "Price alerts",
                         expanded=False):
            st.caption("Fire once when a ticker crosses a target price "
                       "(separate from the buy-price alert).")
            for _a in _palerts:
                _ac1, _ac2 = st.columns([6, 0.5], vertical_alignment="center")
                _arrow = "≥" if _a.get("direction") == "above" else "≤"
                _note = f" · {_a['note']}" if _a.get("note") else ""
                _ac1.markdown(f"**{_a['ticker']}** {_arrow} ${float(_a['target']):g}{_note}")
                if _ac2.button("✕", key=f"pa_del_{_a['id']}", help="Delete alert"):
                    _notif.delete_price_alert(_sb_client, _a["id"])
                    st.rerun()
            pc1, pc2, pc3, pc4 = st.columns([1.4, 1, 1.2, 0.8], vertical_alignment="bottom")
            _pa_tk = pc1.text_input("Ticker", key="pa_tk", placeholder="META",
                                    label_visibility="collapsed")
            _pa_tg = pc2.number_input("Target", key="pa_tg", min_value=0.0, step=1.0,
                                      label_visibility="collapsed")
            _pa_dir = pc3.selectbox("Dir", ["below", "above"], key="pa_dir",
                                    label_visibility="collapsed")
            if pc4.button("Add", key="pa_add", width="stretch") and _pa_tk and _pa_tg > 0:
                _notif.add_price_alert(_sb_client, _pa_tk.strip().upper(), _pa_tg, _pa_dir)
                st.toast("Price alert added")
                st.rerun()

        # ── Recent alerts feed (collapsed) ──
        with st.expander(f"Recent alerts · {n_unread} new" if n_unread else "Recent alerts",
                         expanded=False):
            if any(not i.get("read_at") for i in items) and st.button(
                    "Mark all read", key="notif_mark_all"):
                _notif.mark_all_read(_sb_client)
                st.rerun()
            if items:
                for it in items[:3]:
                    dot = "🟢" if not it.get("read_at") else "⚪"
                    tk = f"{it['ticker']} · " if it.get("ticker") else ""
                    st.markdown(
                        f"<div style='padding:1px 0;font-size:0.9rem;white-space:nowrap;"
                        f"overflow:hidden;text-overflow:ellipsis'>{dot} {tk}"
                        f"{it.get('title', '')}</div>", unsafe_allow_html=True)
                if len(items) > 3:
                    st.caption(f"+{len(items) - 3} more")
            else:
                st.caption("No alerts yet.")

        # ── Per-ticker alert opt-in (Yes-category only) ──
        # Guarded: never let this section crash the whole watchlist (e.g. a stale
        # imported-module cache on Streamlit Cloud after a deploy).
        _yes_err = False
        try:
            yes_tickers = _notif.list_yes_tickers(_sb_client)
        except Exception:
            yes_tickers, _yes_err = [], True
        _on = sum(1 for y in yes_tickers if y["enabled"])
        with st.expander(
                f"Alerts per ticker · {_on}/{len(yes_tickers)} on" if yes_tickers
                else "Alerts per ticker", expanded=False):
            st.caption("Only **Yes**-category tickers get price & earnings alerts.")
            if _yes_err:
                st.caption("⚠️ Couldn't load the list — reboot the app (Manage app → Reboot).")
            elif not yes_tickers:
                st.caption("No tickers in the **Yes** category yet.")
            for y in yes_tickers:
                tc1, tc2 = st.columns([5, 1], vertical_alignment="center")
                tc1.markdown(f"**{y['ticker']}**")
                _new = tc2.toggle("alerts", value=y["enabled"],
                                  key=f"notif_tk_{y['ticker']}", label_visibility="collapsed")
                if _new != y["enabled"]:
                    _notif.set_ticker_alert(_sb_client, y["ticker"], _new)
                    st.rerun()

        # ── Telegram linking ──
        if not connected:
            tok = _notif.ensure_link_token(_sb_client)
            url = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start={tok}"
            st.markdown(
                f'<div style="margin:6px 0 2px;font-size:0.9rem">🔗 '
                f'<a href="{url}" target="_blank" style="color:{accent};font-weight:600">'
                f'Connect Telegram for push alerts</a> — tap <b>Start</b> in the bot.</div>',
                unsafe_allow_html=True)


def _reject_aspirant(sb_client, ticker):
    """Human veto on an AI-proposed aspirant: send it straight to No.

    Kept as its own function, outside the watchlist render block below —
    test_fund_slice.TestSlimConfigLoad.test_the_watchlist_rows_never_write_a_config
    pins that block against any save_config call (a partial config handed to
    save_config from there wiped prescan sections once). This loads the full
    config first, so the merge is safe either way, but the write lives here.
    """
    cfg_no = load_config(sb_client, ticker)
    if cfg_no is not None:
        cfg_no['category'] = 'No'
        save_config(sb_client, ticker, cfg_no)


def _watchlist_overview():
    st.markdown("## Watchlist")
    st.markdown(
        f'<p style="color: {T["text_muted"]}; font-size: 1.05rem; line-height: 1.6; max-width: 560px;">'
        'Track intrinsic value vs market price for your watchlist. '
        'Click a ticker to edit the full DCF model.'
        '</p>',
        unsafe_allow_html=True,
    )

    # Red hover effect for delete buttons
    st.markdown(f"""<style>
    button[data-testid="stBaseButton-secondary"]:has(span[data-testid="stIconMaterial"]):hover {{
        background: {T['delete_bg']} !important;
        border-color: {T['delete_border']} !important;
        color: {T['delete_text']} !important;
    }}
    /* Pin Refresh-all button to the same sage-green pill as Add to Watchlist */
    .st-key-wl_refresh_button button {{
        background-color: {T['accent']} !important;
        color: white !important;
        border: none !important;
        border-radius: 980px !important;
        padding: 12px 24px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        transition: background-color 0.2s ease !important;
    }}
    .st-key-wl_refresh_button button:hover {{
        background-color: {T['accent_hover']} !important;
    }}
    </style>""", unsafe_allow_html=True)

    # ── Add ticker + Refresh ──
    st.markdown("")
    wl_add_col1, wl_add_col2, wl_add_col3 = st.columns([3, 1, 1], vertical_alignment="center")
    with wl_add_col1:
        wl_ticker = st.text_input(
            "Add ticker",
            placeholder="e.g. AAPL",
            label_visibility="collapsed",
            key="wl_ticker_input",
        )
    with wl_add_col2:
        wl_add = st.button("Add to Watchlist", width="stretch", type="primary")
    with wl_add_col3:
        wl_refresh = st.button(
            "↻ Refresh all",
            width="stretch",
            type="primary",
            key="wl_refresh_button",
            help="Recompute multi-lens fair value for all watchlist tickers.",
        )

    if wl_add and wl_ticker:
        ticker_clean = sanitize_ticker(wl_ticker)
        _existing_wl_tickers = {item["ticker"].upper() for item in list_watchlist(_sb_client)}
        if ticker_clean is None:
            st.warning("Invalid ticker. Use 1–5 letters only (e.g. AAPL).")
        elif ticker_clean in _existing_wl_tickers:
            st.info(f"✓ **{ticker_clean}** is already in your watchlist — "
                    "no need to re-analyse. Open it from the list below to view "
                    "its valuation, or use **↻ Refresh all** to recompute.")
        elif not rate_limited_lookup():
            pass
        else:
            try:
                wl_cfg, _ = run_analysis(
                    ticker_clean,
                    peer_mode="Auto-discover",
                    manual_peers="",
                    margin_of_safety=MARGIN_OF_SAFETY_DEFAULT,
                    terminal_growth=TERMINAL_GROWTH_DEFAULT,
                )
                save_config(_sb_client, ticker_clean, wl_cfg)
                st.cache_data.clear()      # the watchlist listing is cached
                st.success(f"{ticker_clean} added to watchlist")
                st.rerun()
            except ValueError as e:
                err_msg = str(e)
                if "not found in SEC" in err_msg:
                    st.warning(f"**{ticker_clean}** is not available for DCF analysis. "
                               "Only individual stocks with SEC filings (10-K) can be added. "
                               "ETFs, mutual funds, and indices are not supported.")
                else:
                    logger.error("Watchlist analysis failed for %s: %s", ticker_clean, e)
                    log_error("WATCHLIST_ERROR", str(e), page="Watchlist", metadata={"ticker": ticker_clean})
                    st.error(f"Could not analyse {ticker_clean}. Please try again. ({type(e).__name__})")
            except Exception as e:
                import traceback; traceback.print_exc()
                logger.error("Watchlist analysis failed for %s: %s", ticker_clean, e)
                log_error_with_trace("WATCHLIST_ERROR", e, page="Watchlist", metadata={"ticker": ticker_clean})
                st.error(f"Could not analyse {ticker_clean}. Please try again. ({type(e).__name__})")

    # ── Refresh handler ──
    if wl_refresh:
        # One query rather than a pool of per-ticker loads — same reason as the
        # watchlist's own load, and this one runs on every Refresh All click.
        _refresh_cfgs = load_all_configs(_sb_client)

        # An aspirant's config is facts plus flat placeholder curves; a fair value
        # from it would read as a verdict. Its DCF gets filled in first.
        _refresh_cfgs = {t: c for t, c in _refresh_cfgs.items() if not aspirant.is_placeholder(c)}

        if not _refresh_cfgs:
            st.info("Watchlist is empty — nothing to refresh.")
        else:
            _bar = st.progress(0.0, text="Computing valuations...")

            def _on_refresh_progress(done, total):
                _bar.progress(done / total if total else 1.0,
                              text=f"Computing {done}/{total}...")

            # User-triggered Refresh All always recomputes every ticker — the
            # 7-day staleness check is only meaningful for an automatic /
            # background path that we don't currently expose.
            _result = _refresh_stale_valuations(
                _sb_client, _refresh_cfgs,
                user_id=st.session_state["user"]["id"], force=True,
                on_progress=_on_refresh_progress,
            )
            _bar.empty()
            _total = len(_refresh_cfgs)
            _done = len(_result["computed"])
            _err = len(_result["errors"])
            if _err:
                st.warning(
                    f"Refreshed {_done}/{_total}. {_err} errors. "
                    f"Errors: {', '.join(_result['errors'][:5])}"
                )
            else:
                st.success(f"Refreshed {_done} ticker{'s' if _done != 1 else ''}.")
            st.cache_data.clear()
            st.rerun()

    # ── Overview table ──
    # Cached: list_watchlist selects the full `config` column to derive its
    # metadata, so it pulled the same 2.5 MB as the config load — 0.50s on
    # every rerun, i.e. every click and every widget change, before anything
    # was even drawn. Adding or removing a ticker clears the cache.
    @st.cache_data(ttl=30, show_spinner=False)
    def _cached_watchlist(user_id):
        return list_watchlist(_sb_client, user_id=user_id)

    watchlist = _cached_watchlist(st.session_state["user"]["id"])
    if not watchlist:
        st.info("Your watchlist is empty. Add a ticker above or use 'Add to Watchlist' on the DCF page.")
        return

    @st.cache_data(ttl=60)
    def _fetch_prices_batch(tickers_tuple, isin_pairs=()):
        # Omit the symbols that came back empty rather than mapping them to
        # 0.0. A 0 here is indistinguishable from a real quote downstream, and
        # the row builder needs to know the difference to fall back.
        # isin_pairs komt als tuple binnen omdat st.cache_data alleen hashbare
        # argumenten accepteert; een dict zou de cache-sleutel laten klappen.
        prices = fetch_current_prices(list(tickers_tuple), dict(isin_pairs))
        return {t: p["price"] for t, p in prices.items() if p and p.get("price")}

    # Load all configs once (avoid redundant load_config calls)
    # Clear cache if returning from editor
    if st.session_state.pop("_wl_config_dirty", False):
        st.cache_data.clear()

    @st.cache_data(ttl=30, show_spinner=False)
    def _load_all_configs(user_id, tickers_tuple):
        # One query, not one per ticker: this used to fan 64 requests across a
        # thread pool for 2.5 MB that a single select returns, and repeat that
        # every 30 seconds when the cache expired. tickers_tuple stays in the
        # signature so the cache key still invalidates when the list changes.
        #
        # Without ai_notes: 79% of the payload, and the rows below read it
        # zero times. These configs are for rendering only — the editor and
        # the refresh handler load the complete config for themselves.
        cfgs = load_all_configs(_sb_client, user_id=user_id, include_ai_notes=False)
        wanted = set(tickers_tuple)
        return {t: c for t, c in cfgs.items() if t in wanted} if wanted else cfgs

    _wl_configs = _load_all_configs(st.session_state["user"]["id"], tuple(item['ticker'] for item in watchlist))
    wl_tickers = list(_wl_configs.keys())
    # De Europese lijnen hangen op hun ISIN; zonder die kaart valt de tweede
    # koersbron stil terug op de bevroren prijs.
    _wl_isins = tuple(sorted(
        (t, cfg.get("isin")) for t, cfg in _wl_configs.items() if cfg.get("isin")))
    batch_prices = (_fetch_prices_batch(tuple(wl_tickers), _wl_isins)
                    if wl_tickers else {})

    @st.cache_data(ttl=86400, show_spinner=False)
    def _cached_fundamentals(t):
        # 10 years so the watchlist can show a meaningful Avg ROCE.
        # Exceptions deliberately propagate: st.cache_data never caches a
        # raised exception, so a transient SEC outage retries on the next
        # rerun. Swallowing it here would cache an empty result for 24h and
        # blank the ticker's FCF Yield for a full day.
        return fetch_fundamentals(t, n_years=10)

    # Fundamentals: stored slice first, EDGAR only for what is missing.
    #
    # This page needs three numbers per ticker and used to download a ~5 MB
    # companyfacts file per name to get them — 380 MB and 13 seconds for a
    # 77-name list. st.cache_data lives in the container's memory, so every
    # Streamlit Cloud restart threw that away and the next visitor paid it
    # again. The slice is the same series, stored on the config, so the
    # arithmetic below is unchanged and the network is not touched.
    from concurrent.futures import ThreadPoolExecutor

    _fund_map = {}
    _fund_unavailable = set()
    _needs_fetch = []
    for t in wl_tickers:
        _slice = (_wl_configs.get(t) or {}).get("fund_slice")
        if slice_is_usable(_slice):
            _fund_map[t] = _slice
        else:
            _needs_fetch.append(t)

    if _needs_fetch:
        # Only the stragglers: a newly added ticker, or one whose slice has
        # not been written yet. Falling back to a live fetch keeps the page
        # correct while the backfill catches up.
        with ThreadPoolExecutor(max_workers=6) as _fund_exec:
            _fund_futures = {t: _fund_exec.submit(_cached_fundamentals, t)
                             for t in _needs_fetch}
        for t, f in _fund_futures.items():
            try:
                _fund_map[t] = f.result()
            except Exception as e:
                logger.warning("Fundamentals fetch failed for %s: %s", t, e)
                _fund_map[t] = {}
                _fund_unavailable.add(t)

    rows = []
    for t, cfg_wl in _wl_configs.items():
        try:
            _ccy, _sym, _scale = _row_currency(t, cfg_wl)
            _live = batch_prices.get(t)
            live_price, _price_stale = _resolve_watchlist_price(
                cfg_wl, _live * _scale if _live else _live)
            if not _price_stale:
                cfg_wl['stock_price'] = live_price
            # Use Valuation Bridge values if available, otherwise compute
            if '_computed_intrinsic' in cfg_wl:
                _wl_intrinsic = cfg_wl['_computed_intrinsic']
                _wl_buy = cfg_wl['_computed_buy']
            else:
                val = compute_intrinsic_value(cfg_wl)
                _wl_intrinsic = val['intrinsic_value']
                _wl_buy = val['buy_price']
            # Multi-lens summary (Phase 1) preferred over single-DCF intrinsic
            summary = cfg_wl.get('valuation_summary')
            if summary and summary.get('weighted_fv_mid') and live_price > 0:
                upside = summary['weighted_fv_mid'] / live_price - 1
                _wl_buy = summary.get('buy_price', _wl_buy)
            else:
                upside = (_wl_intrinsic / live_price - 1) if live_price > 0 else 0
            ni = cfg_wl.get('hist_net_income', [])
            sh = cfg_wl.get('shares_outstanding', 0)
            eps = ni[-1] / sh if ni and sh else 0
            pe = live_price / eps if eps > 0 else None
            # FCF Yield — from fundamentals (cached 24h)
            _fund = _fund_map.get(t, {})
            # Apply per-year overrides silently so the watchlist row
            # reflects corrected values for tickers with broken EDGAR
            # tagging (e.g. MCD operating leases post-FY2023).
            _fund_overrides = cfg_wl.get('fundamentals_overrides') or {}
            if _fund_overrides and _fund:
                _fund = apply_fundamentals_overrides(_fund, _fund_overrides)
            fcf_yield_val = _latest_fcf_yield(
                _fund, cfg_wl.get('equity_market_value'), live_price)
            # Avg ROCE (EBIT/(TA−CL)) with float ROE-fallback + manual override
            # — shared single source of truth (scorecard_utils.compute_roce_metric).
            roce_metric, roce_avg = compute_roce_metric(_fund, cfg_wl)
            # Phase-aware capital-returns axis from the robustness engine (when the
            # ticker has been assessed): names the metric that actually applied for
            # its phase (e.g. "Rule of 40" for a phase-2 name) with its own band,
            # instead of a misleading raw ROCE. Falls back to Avg ROCE below.
            _rob_roce = ((cfg_wl.get('robustness') or {}).get('axes') or {}).get('roce') or {}
        except Exception as e:
            logger.warning("Watchlist row build failed for %s: %s", t, e)
            continue
        rows.append({
            'ticker': t,
            'company': _prettify_company(cfg_wl.get('company', t)),
            'category': cfg_wl.get('category', 'Uncategorized'),
            'promoted_by': cfg_wl.get('promoted_by'),
            'promoted_at': cfg_wl.get('promoted_at'),
            'price': live_price,
            'price_stale': _price_stale,
            'currency': _ccy,
            'symbol': _sym,
            'intrinsic': _wl_intrinsic,
            'buy_price': _wl_buy,
            'upside': upside,
            'roce_avg': roce_avg,
            'roce_metric': roce_metric,
            'cap_band': _rob_roce.get('band'),
            'cap_metric': _rob_roce.get('metric'),
            'cap_value': _rob_roce.get('value'),
            'cap_phase': _rob_roce.get('phase'),
            'cap_basis': _rob_roce.get('basis'),
            'fcf_yield': fcf_yield_val,
            'fcf_unavailable': t in _fund_unavailable,
            'valuation_summary': cfg_wl.get('valuation_summary'),
        })

    rows.sort(key=lambda r: r['upside'], reverse=True)

    # Say it out loud. This failure mode was invisible for as long as it was:
    # the quotes just quietly became $0.00 and every metric derived from them
    # went with it, so the table looked authoritative while it was wrong.
    _stale_rows = [r['ticker'] for r in rows if r.get('price_stale')]
    if _stale_rows:
        st.warning(
            f"Live quotes unavailable for {len(_stale_rows)} of {len(rows)} "
            f"tickers — showing the last stored price for "
            f"{', '.join(_stale_rows[:8])}"
            f"{' and others' if len(_stale_rows) > 8 else ''}. "
            "Upside and multiples for those rows are based on that older price."
        )

    # Fetch earnings dates (cached 5 min)
    @st.cache_data(ttl=3600, show_spinner=False)
    def _cached_earnings(tickers_tuple):
        return fetch_earnings_dates(list(tickers_tuple))

    _earnings_map = _cached_earnings(tuple(wl_tickers)) if wl_tickers else {}

    # Resolve every logo before the table starts drawing. One per row, in row
    # order, was 5.4s of this page.
    prewarm_logos(wl_tickers)

    # ── Category definitions ──
    _categories = list(aspirant.CATEGORIES)
    _cat_icons = {"Yes": "✅", "Aspirant": "🌱", "Maybe": "🤔", "Watch Later": "⏳", "No": "❌", "Uncategorized": ""}

    # Per-phase definitions for the inline "?" tooltip on each Capital cell.
    _CAP_DEFS = {
        "Rule of 40": "Rule of 40 = 3-year revenue growth% + FCF margin%; ≥40 is the phase-2 hyper-growth bar",
        "Incr. ROIC": "Incremental ROIC = ΔNOPAT / ΔInvested capital — the return on newly deployed capital",
        "ROCE (latest)": "Latest-year ROCE = EBIT / (Total Assets − Current Liabilities)",
        "ROCE": "ROCE = EBIT / (Total Assets − Current Liabilities); ≥20% is the mature Prasad quality gate",
        "ROE": "ROE = Net income / Total Equity — used for float businesses where capital employed is too small for a meaningful ROCE",
    }

    def _cap_cell_md(row):
        """HTML for the Capital cell: the figure plus the same circle-"?" hover
        tooltip used on the Fundamentals tab (CSS hover-span, not a `title` attr
        which Streamlit strips). The tooltip explains exactly what the figure is
        for this ticker's phase. Colour follows the robustness band (phase-aware)
        or the 20% screen-bar (fallback)."""
        import html as _html

        green = T.get("green", "#81b29a")
        red = T.get("red", "#e07a5f")
        card = T.get("card", "#ffffff")
        txt = T.get("text", "#111111")
        muted = T.get("text_muted", "#888888")
        border = T.get("border_medium", "#dddddd")
        shadow = T.get("shadow_hover", "0 6px 20px rgba(0,0,0,0.15)")

        def _tip(text_val, color, help_text):
            # Fixed-width right-aligned number box so figures line up under each
            # other (1 vs 2 digits, decimals) while the cell stays centered.
            _ns = "display:inline-block;min-width:42px;text-align:right"
            num = (f'<span style="{_ns};color:{color};font-weight:500">{text_val}</span>'
                   if color else f'<span style="{_ns}">{text_val}</span>')
            return (
                f'{num}'
                f'<span class="cap-tip" style="position:relative;cursor:help;margin-left:5px">'
                f'<svg width="14" height="14" viewBox="0 0 16 16" fill="none" '
                f'style="opacity:0.4;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{muted}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" '
                f'font-weight="600" fill="{muted}">?</text></svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;right:20px;'
                f'top:-10px;background:{card};color:{txt};border:1px solid {border};'
                f'border-radius:8px;padding:9px 12px;font-size:0.75rem;line-height:1.45;'
                f'font-weight:400;width:230px;white-space:normal;z-index:999;'
                f'box-shadow:{shadow};pointer-events:none;transition:opacity 0.15s ease">'
                f'{_html.escape(help_text)}</span></span>'
                f'<style>.cap-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>'
            )

        band, metric, value = row.get('cap_band'), row.get('cap_metric'), row.get('cap_value')
        basis = row.get('cap_basis')
        if band == "n/a":
            return _tip("— defer", None, "Phase 1 — too early to judge capital returns; defer.")
        def _suffix(metric_name):
            """Name the metric in the cell when it isn't the column's own.

            The header used to say "Capital" because the figure is ROCE for
            most names but ROE for a float business and, for one, Rule of 40.
            Under a ROCE header those rows would be mislabelled, and the
            tooltip is not where a contradiction should be resolved.
            """
            if not metric_name or metric_name.upper().startswith("ROCE"):
                return ""
            return (f'<span style="font-size:0.65rem;color:{muted};'
                    f'margin-left:3px">{_html.escape(metric_name)}</span>')

        if band and metric and value is not None:
            base = _CAP_DEFS.get(metric, metric)
            help_text = f"{base}. {basis}." if basis else f"{base}."
            color = {"robust": green, "fragile": red}.get(band)
            return _tip(f"{value:.0f}%", color, help_text) + _suffix(metric)
        # Fallback: raw multi-year Avg ROCE/ROE (ticker not yet assessed)
        rv, rm = row.get('roce_avg'), row.get('roce_metric', 'ROCE')
        if rv is None:
            return "—"
        help_text = (f"{_CAP_DEFS.get(rm, rm)} — multi-year average; this ticker "
                     "isn't assessed in the Robustness table yet.")
        color = green if rv >= 20 else (red if rv < 10 else None)
        return _tip(f"{rv:.1f}%", color, help_text) + _suffix(rm)

    def _render_wl_header():
        hdr = st.columns([0.3, 1.0, 1.6, 0.8, 1.5, 0.8, 0.7, 0.6, 0.7, 0.7, 0.3])
        _wl_hdr = ["", "Ticker", "Company", "Price", "Fair Value", "Buy", "Upside", "ROCE", "FCF Yield", "Earnings", ""]
        for col, label in zip(hdr, _wl_hdr):
            if not label:
                continue
            if label == "ROCE":
                # Centered header above the centered figure column. Named for
                # the metric it carries for all but a handful of tickers; those
                # few name their own in the cell.
                col.markdown('<div style="text-align:center"><b>ROCE</b></div>',
                             unsafe_allow_html=True)
            else:
                col.markdown(f"**{label}**")

    def _render_wl_row(row):
        t = row['ticker']
        up_color = "green" if row['upside'] > 0 else "red"
        cols = st.columns([0.3, 1.0, 1.6, 0.8, 1.5, 0.8, 0.7, 0.6, 0.7, 0.7, 0.3], vertical_alignment="center")
        with cols[0]:
            if st.button("", key=f"wl_edit_{t}", icon=":material/edit:"):
                st.query_params["edit"] = t
                st.rerun()
        _wl_logo = _logo_img(
            t, None, "",
            "width:24px;height:24px;border-radius:50%;object-fit:cover;"
            "vertical-align:middle;margin-right:6px")
        cols[1].markdown(
            f'{_wl_logo}<strong>{t}</strong>',
            unsafe_allow_html=True,
        )
        cols[2].markdown(row['company'])
        if row.get('promoted_by') == 'claude' and row.get('promoted_at'):
            cols[2].caption(f"by Claude · {date.fromisoformat(row['promoted_at']):%-d %b}")
        if row.get('price_stale'):
            # Grey and dotted-underlined so a stored price can never be
            # mistaken for a live one at a glance.
            cols[3].markdown(
                f'<span title="Live quote unavailable — last stored price" '
                f'style="color:{T["text_muted"]};border-bottom:1px dotted '
                f'{T["text_muted"]};cursor:help">'
                f'{row.get("symbol", "$")}{row["price"]:.2f}</span>',
                unsafe_allow_html=True,
            )
        else:
            cols[3].markdown(f"{row.get('symbol', '$')}{row['price']:.2f}")
        cols[4].markdown(
            _render_fv_cell(
                price=row['price'],
                summary=row.get('valuation_summary'),
                legacy_intrinsic=row.get('intrinsic'),
                theme=T,
                symbol=row.get('symbol', '$'),
            ),
            unsafe_allow_html=True,
        )
        cols[5].markdown(f"{row.get('symbol', '$')}{row['buy_price']:.2f}")
        cols[6].markdown(f":{up_color}[{row['upside']:+.1%}]")
        # Capital returns — figure + per-cell "?" tooltip. Cell centered; the
        # fixed-width number box inside keeps the figures aligned under each other.
        cols[7].markdown(
            f'<div style="text-align:center;white-space:nowrap">{_cap_cell_md(row)}</div>',
            unsafe_allow_html=True,
        )
        if row['fcf_yield'] is not None:
            cols[8].markdown(f"{row['fcf_yield']:.1%}")
        elif row['fcf_unavailable']:
            # SEC fetch failed — say so rather than implying the filer has no FCF
            cols[8].markdown(
                f'<span title="SEC EDGAR unavailable — retries on next refresh" '
                f'style="color:{T["text_muted"]}">⚠</span>',
                unsafe_allow_html=True,
            )
        else:
            cols[8].markdown("—")
        _earn = _earnings_map.get(t)
        if _earn and _earn.get('date'):
            _days_to_earn = (_earn['date'] - date.today()).days
            if _days_to_earn >= 0:
                _earn_est = " (est)" if _earn.get('estimated') else ""
                if _days_to_earn <= 7:
                    _earn_col = T['red']
                elif _days_to_earn <= 14:
                    _earn_col = T['text_muted']
                else:
                    _earn_col = T['text']
                cols[9].markdown(
                    f'<span style="color:{_earn_col}">{_earn["date"].strftime("%b %d")}{_earn_est}</span>',
                    unsafe_allow_html=True,
                )
            else:
                _next_est = _earn['date'] + timedelta(days=91)
                cols[9].markdown(
                    f'<span style="color:{T["text_muted"]};font-size:0.85rem">~{_next_est.strftime("%b %d")}</span>',
                    unsafe_allow_html=True,
                )
        else:
            cols[9].markdown("—")
        with cols[10]:
            # Aspirant is the only pile the MCP won't let the AI clear out on its
            # own (it can only promote or set No) — the human veto lives here.
            if row.get('category') == 'Aspirant' and st.button(
                "", key=f"wl_no_row_{t}", icon=":material/block:",
                help="Reject: move to No",
            ):
                _reject_aspirant(_sb_client, t)
                st.cache_data.clear()
                st.rerun()
            # An Aspirant has no delete: removing it would let the weekly
            # routine re-add the same name next run. Reject via the No
            # button above instead — that's a durable "No", not a no-op.
            if row.get('category') != 'Aspirant' and st.button(
                "", key=f"wl_rm_row_{t}", icon=":material/close:"):
                remove_from_watchlist(_sb_client, t)
                st.cache_data.clear()      # the watchlist listing is cached
                st.rerun()

    # ── Group rows by category and render ──
    _grouped = {c: [] for c in _categories}
    for row in rows:
        _cat = row.get('category', 'Uncategorized')
        if _cat not in _grouped:
            _cat = 'Uncategorized'
        _grouped[_cat].append(row)

    # Hero-card style per category
    _cat_keys = {_cat: f"wl_cat_{i}" for i, _cat in enumerate(_categories)}
    st.markdown(
        f'''<style>
        .ld-on, .ld-off {{
            display:inline-block;width:6px;height:6px;border-radius:50%;
            margin-right:2px;cursor:help;
            transition:transform 0.1s ease;
            position:relative;
        }}
        .ld-on {{ background:{T["accent"]}; }}
        .ld-off {{ background:{T["border_medium"]}; }}
        .ld-on:hover, .ld-off:hover {{
            transform:scale(1.5);
        }}
        /* Custom CSS tooltip on hover — appears immediately, no browser delay */
        .ld-on::after, .ld-off::after {{
            content: attr(data-label);
            position:absolute;
            bottom: calc(100% + 6px);
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0,0,0,0.88);
            color: #fff;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.72rem;
            font-weight: 500;
            white-space: nowrap;
            opacity: 0;
            visibility: hidden;
            transition: opacity 0.15s ease, visibility 0.15s ease;
            pointer-events: none;
            z-index: 1000;
        }}
        .ld-on:hover::after, .ld-off:hover::after {{
            opacity: 1;
            visibility: visible;
        }}
        .range-bar {{
            min-width:110px;
        }}
        /* Football-field hover-tooltip trigger: small pill on the lens-dots
           row. Pure CSS hover — no JS / no Streamlit widget. */
        .ff-trigger-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-top: 2px;
        }}
        .ff-trigger-wrap {{
            position: relative;
            display: inline-block;
            margin-left: 6px;
        }}
        .ff-trigger {{
            display: inline-block;
            font-size: 0.65rem;
            font-weight: 500;
            color: {T["text_muted"]};
            background: {T["row_alt"]};
            border: 1px solid {T["border_medium"]};
            border-radius: 999px;
            padding: 1px 8px;
            cursor: default;
            user-select: none;
        }}
        .ff-trigger-wrap:hover .ff-trigger {{
            background: {T["accent"]};
            border-color: {T["accent"]};
            color: white;
        }}
        .ff-tooltip {{
            display: none;
            position: absolute;
            bottom: calc(100% + 6px);
            right: 0;
            z-index: 1000;
            background: {T["card"]};
            border: 1px solid {T["border_medium"]};
            border-radius: 8px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
            padding: 8px;
            min-width: 480px;
        }}
        .ff-trigger-wrap:hover .ff-tooltip {{
            display: block;
        }}
        </style>''',
        unsafe_allow_html=True,
    )
    _active_cats = [c for c in _categories if _grouped[c]]
    # Every category now wraps st.expander → hero-card styling lives on the
    # expander so its native slide animation moves the visible card body.
    st.markdown(
        '<style>'
        + ''.join(
            f'.st-key-{_cat_keys[c]} [data-testid="stExpander"] {{'
            f'  background: {T["card"]};'
            f'  border: none !important;'
            f'  border-top: 3px solid {T["accent"]} !important;'
            f'  border-radius: 24px !important;'
            f'  box-shadow: {T["shadow"]};'
            f'  margin-bottom: 20px;'
            f'  overflow: hidden;'
            f'}}'
            # Strip every internal border + background so we never see a
            # seam between summary and content (Streamlit expanders ship
            # with a default hairline border-bottom on summary which was
            # the visible "rand" after opening).
            f'.st-key-{_cat_keys[c]} [data-testid="stExpander"] summary,'
            f'.st-key-{_cat_keys[c]} [data-testid="stExpander"] summary *,'
            f'.st-key-{_cat_keys[c]} [data-testid="stExpander"] details,'
            f'.st-key-{_cat_keys[c]} [data-testid="stExpander"] details > div {{'
            f'  border: none !important;'
            f'  background: transparent !important;'
            f'  box-shadow: none !important;'
            f'}}'
            f'.st-key-{_cat_keys[c]} [data-testid="stExpander"] details > summary {{'
            f'  padding: 20px 32px !important;'
            f'  font-weight: 700;'
            f'  font-size: 0.95rem;'
            f'  color: {T["text"]};'
            f'}}'
            f'.st-key-{_cat_keys[c]} [data-testid="stExpander"] details > div {{'
            f'  padding: 0 32px 28px 32px !important;'
            f'}}'
            for c in _active_cats
        )
        + '</style>',
        unsafe_allow_html=True,
    )

    # Yes is the active-decision pile → open by default. Other categories
    # collapse so they don't push the must-look-at items below the fold.
    _default_open = {"Yes": True}

    for _cat in _active_cats:
        _cat_rows = _grouped[_cat]
        with st.container(key=_cat_keys[_cat]):
            with st.expander(
                f"{_cat}  ·  {len(_cat_rows)}",
                expanded=_default_open.get(_cat, False),
            ):
                _render_wl_header()
                for row in _cat_rows:
                    _render_wl_row(row)

    st.markdown("")
    # Notifications hub — below the watchlist (reminders, alerts, Telegram).
    st.divider()
    _render_notifications_panel()


def _dcf_editor(ticker):
    """Full DCF editor page for a single ticker."""
    # ── Reset editor widget state when switching tickers ──
    # The DCF-editor number_input widgets use fixed keys (ed_*, rdcf_*) that are
    # NOT ticker-scoped. Without this, Streamlit keeps the previously-viewed
    # ticker's values in session_state, so the input cells show stale numbers
    # (and a save from that view would overwrite the real config). Clearing them
    # on a ticker change forces each widget to re-init from this ticker's cfg.
    # Runs before any ed_/rdcf_ widget is instantiated this run, so no rerun.
    if st.session_state.get("_dcf_editor_ticker") != ticker:
        for _k in [k for k in st.session_state
                   if k.startswith("ed_") or k.startswith("rdcf_")]:
            del st.session_state[_k]
        st.session_state["_dcf_editor_ticker"] = ticker
    cfg = load_config(_sb_client, ticker)
    if cfg is None:
        st.error(f"No config found for {ticker}")
        if st.button("\u2190 Watchlist", key="editor_back_err"):
            del st.query_params["edit"]
            st.rerun()
        return

    # ── Back button ──
    if st.button("\u2190 Watchlist", key="editor_back"):
        st.session_state["_wl_config_dirty"] = True
        del st.query_params["edit"]
        st.rerun()

    # ── Live price ──
    # Dezelfde keten als de watchlist (broker -> Nasdaq -> Yahoo -> Frankfurt).
    # Dit vroeg eerst alleen Yahoo, en dat blokkeert Streamlit Cloud: de pagina
    # viel dan stil terug op de koers van de laatste config-schrijfbeurt.
    @st.cache_data(ttl=30)
    def _price(t, isin=None):
        try:
            q = fetch_current_prices([t], {t: isin} if isin else None).get(t)
            return float(q["price"]) if q and q.get("price") else 0.0
        except Exception as e:
            logger.debug("Stock price fetch failed for %s: %s", t, e)
            return 0.0

    live_price = _price(ticker, cfg.get("isin"))
    if live_price > 0:
        cfg['stock_price'] = live_price

    # ── Valuation summary (hero card) — placeholder, filled after tabs with updated values ──
    val = compute_intrinsic_value(cfg)  # initial calc for tabs that need it
    _hero_placeholder = st.empty()

    # ── Status pills (inside hero card area) ──
    _cat_options = ["Uncategorized", *[c for c in aspirant.CATEGORIES if c != "Uncategorized"]]
    _cur_cat = cfg.get('category', 'Uncategorized')
    _cat_idx = _cat_options.index(_cur_cat) if _cur_cat in _cat_options else 0
    _new_cat = st.pills(
        "Status", _cat_options, default=_cat_options[_cat_idx],
        key="dcf_category",
    )
    if _new_cat and _new_cat != _cur_cat:
        cfg['category'] = _new_cat
        # A manual move off Aspirant is a human override of the AI's pick —
        # the "by Claude" label no longer applies once the human decided.
        cfg['promoted_by'] = None
        save_config(_sb_client, ticker, cfg)
        st.rerun()

    # ── Earnings warning (hero card) ──
    @st.cache_data(ttl=300, show_spinner=False)
    def _cached_earnings_single(t):
        return fetch_earnings_dates([t])

    _earn_data = _cached_earnings_single(ticker).get(ticker)
    _days_to_earn = None
    if _earn_data and _earn_data.get('date') and _earn_data['date'] >= date.today():
        _days_to_earn = (_earn_data['date'] - date.today()).days
        if _days_to_earn <= 14:
            _earn_color = T['red'] if _days_to_earn <= 7 else T['text_muted']
            _earn_label = "Earnings" if not _earn_data.get('estimated') else "Earnings (est)"
            _earn_time = " BMO" if _earn_data.get('time') == 'bmo' else (" AMC" if _earn_data.get('time') == 'amc' else "")
            st.markdown(
                f'<div style="text-align:center;margin:-8px 0 12px">'
                f'<span class="stat-pill" style="color:{_earn_color};border-color:{_earn_color}">'
                f'{_earn_label}: {_earn_data["date"].strftime("%b %d")}{_earn_time} ({_days_to_earn}d)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # Projections data (needed inside and after expander)
    base_year = cfg.get('base_year', 2025)
    growth = list(cfg.get('revenue_growth', []))
    margins = list(cfg.get('op_margins', []))

    # ── Tabs: DCF / Reverse DCF / Peer Comparison / Dividend / Fundamentals ──
    # Fundamentals are shared by Overview and the Fundamentals tab: one cached
    # fetch, per-year overrides applied once, so both tabs see the same data.
    # A fetch error (SEC outage) is held and re-raised inside the Fundamentals
    # tab, where it surfaced before; here it would blank every tab.
    @st.cache_data(ttl=300, show_spinner="Loading fundamentals...")
    def _cached_fundamentals(t):
        return fetch_fundamentals(t, n_years=11)

    _fund_error = None
    try:
        fund = _cached_fundamentals(ticker)
        # Apply per-year overrides silently so every section below uses
        # corrected values for tickers with broken EDGAR tagging.
        _fund_overrides = cfg.get('fundamentals_overrides') or {}
        if _fund_overrides:
            fund = apply_fundamentals_overrides(fund, _fund_overrides)
    except Exception as e:
        logger.warning("fundamentals for %s failed: %s", ticker, e)
        _fund_error, fund = e, {}

    (_tab_overview, _tab_notes, _tab_business, _tab_moat, _tab_risk, _tab_fundamentals,
     _tab_dcf, _tab_rdcf, _tab_peers, _tab_dividend, _tab_history) = st.tabs(
        ["Overview", "Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF",
         "Reverse DCF", "Peer Comparison", "Dividend", "History"])

    # Overview: three white sections -- Company (profile + mission), Price vs
    # S&P 500 and Key figures. Read-only; the profile comes from the "Company
    # Profile" section.
    with _tab_overview:
        _onotes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else {}
        _oprofile_raw = _onotes.get(company_profile.TITLE)
        try:
            _oprofile = (company_profile.parse_company_profile(_oprofile_raw)
                         if _oprofile_raw else None)
        except Exception as e:
            logger.debug("Company Profile for %s is invalid: %s", ticker, e)
            _oprofile = None
        _oshares = overview_metrics.shares_at_fiscal_year(fund)
        _omcap = (live_price * _oshares / 1e6
                  if live_price and live_price > 0 and _oshares else None)

        st.markdown(overview_page.company_section_html(_oprofile, _omcap),
                    unsafe_allow_html=True)

        with st.container(key="qc_ov_price"):
            st.markdown('<div class="qc-label">Price vs S&amp;P 500</div>',
                        unsafe_allow_html=True)
            _octl, _ohdr = st.columns([3, 2], vertical_alignment="center")
            with _octl:
                _orng = st.segmented_control(
                    "Range", overview_chart.RANGES, default=overview_chart.DEFAULT_RANGE,
                    key=f"ov_range_{ticker}", label_visibility="collapsed",
                ) or overview_chart.DEFAULT_RANGE
            _ochart = None
            try:
                _otoday = date.today()
                _osince = overview_chart.range_start("10Y", _otoday)
                _oseries = _overview_prices(ticker, _osince.isoformat())
                _ostock = overview_chart.with_live_point(
                    _oseries.get(ticker) or [], live_price, _otoday)
                # SPY gets its live point too: the chart inner-joins the
                # two series on day, so a stock-only point for today
                # would be dropped.
                _obench = overview_chart.with_live_point(
                    _oseries.get("SPY") or [], _price("SPY"), _otoday)
                if _ostock:
                    _olast = date.fromisoformat(_ostock[-1][0])
                    _odays, _ospct, _obpct = overview_chart.aligned_pct(
                        _ostock, _obench, overview_chart.range_start(_orng, _olast))
                    if _odays:
                        # 0.95: a 1Y window starts on the first trading day
                        # on/after the anniversary, often a few days short
                        # of 365, and would otherwise lose its CAGR.
                        _os_tot, _os_cagr = overview_chart.total_and_cagr(
                            _ospct, _odays, min_years=0.95)
                        _ob_tot, _ob_cagr = overview_chart.total_and_cagr(
                            _obpct, _odays, min_years=0.95)
                        # CAGR only means something over whole years.
                        if _orng not in ("1Y", "3Y", "5Y", "10Y"):
                            _os_cagr = _ob_cagr = None
                        _ochart = (
                            overview_chart.header_html(ticker, _orng, _os_tot, _os_cagr,
                                                       _ob_tot, _ob_cagr),
                            overview_chart.figure(_odays, _ospct, _obpct, ticker,
                                                  {"accent": T["accent"],
                                                   "bench": "#5b6cff"}),
                        )
            except Exception as e:
                logger.warning("Overview chart for %s failed: %s", ticker, e)
                _ochart = None
            if _ochart:
                with _ohdr:
                    st.markdown(f'<div style="text-align:right">{_ochart[0]}</div>',
                                unsafe_allow_html=True)
                st.plotly_chart(_ochart[1], width="stretch",
                                config={"displayModeBar": False})
            else:
                st.caption("No price history for this listing yet.")

        try:
            _oinc = _overview_income(ticker)
        except Exception as e:
            logger.warning("income statement for %s failed: %s", ticker, e)
            _oinc = None
        try:
            _ocf = _overview_cashflow(ticker)
        except Exception as e:
            logger.warning("cash flow statement for %s failed: %s", ticker, e)
            _ocf = None
        # live_price is 0.0 when every price source failed; None keeps
        # P/E and the yields at a dash instead of 0.0x.
        try:
            _ometrics_html = overview_page.key_figures_section_html(overview_metrics.compute(
                fund, _oinc, _ocf, live_price if live_price > 0 else None))
        except Exception as e:
            logger.warning("Overview key figures for %s failed: %s", ticker, e)
            _ometrics_html = None
        if _ometrics_html:
            st.markdown(_ometrics_html, unsafe_allow_html=True)
        else:
            st.caption("Key figures unavailable right now.")

    # Business: overview and customer profile, revenue by segment and region,
    # then the four business-quality cards. Read-only, like Moat and Risk.
    with _tab_business:
        _bnotes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else {}
        _bcontent = _bnotes.get(business_cards.TITLE)
        st.markdown(business_cards.overview_section_html(
            _bnotes.get("Business Analysis") or "", _bcontent, T), unsafe_allow_html=True)
        try:
            _brev = business_cards.parse_business_cards(_bcontent)["revenue"] if _bcontent else None
        except ValueError:
            _brev = None
        with st.container(key="qc_revenue_section"):
            st.markdown('<div class="qc-label">Revenue</div>', unsafe_allow_html=True)
            if _brev:
                _rl, _rr = st.columns(2)
                with _rl:
                    st.markdown(business_revenue.segments_panel_html(_brev, T),
                                unsafe_allow_html=True)
                with _rr:
                    # One continuous flat panel (header + map + legend), not
                    # three stacked pieces with a seam around the chart.
                    with st.container(key="qc_geo_panel"):
                        st.markdown(business_revenue.geography_header_html(_brev, T),
                                    unsafe_allow_html=True)
                        _fig = business_revenue.geography_figure(_brev, T)
                        if _fig is not None:
                            st.plotly_chart(_fig, width="stretch",
                                            config={"displayModeBar": False})
                            st.markdown(business_revenue.geography_legend_html(_brev, T),
                                        unsafe_allow_html=True)
                        else:
                            st.caption("No geographic split reported.")
                st.markdown(business_revenue.revenue_caption_html(_brev["period"], T),
                            unsafe_allow_html=True)
            else:
                st.caption("No revenue breakdown yet. It comes with the \"Business Cards\" section.")
        st.markdown(business_cards.quality_section_html(_bcontent, T), unsafe_allow_html=True)

    # Moat: the Moat Analysis as two summary cards, then the five question cards
    # from the "Moat Cards" section. Read-only; Pre-Scan stays the place to edit.
    with _tab_moat:
        _notes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else {}
        _moat_text = _notes.get("Moat Analysis") or ""
        # One HTML row, not two st.columns: the size and direction cards must
        # share a height, which separate columns cannot guarantee.
        _summary = moat_cards.summary_row_html(
            _moat_text, _notes.get(moat_cards.TITLE), T)
        if _summary:
            st.markdown(_summary, unsafe_allow_html=True)
        else:
            st.caption("No Moat Analysis in the verdict format yet.")
        st.markdown(moat_cards.cards_section_html(_notes.get(moat_cards.TITLE), T),
                    unsafe_allow_html=True)

    # Risk: Risk Analysis and SaaSpocalypse Resistance as two summary cards, then
    # the four question cards from "Risk Cards". Read-only, like Moat.
    with _tab_risk:
        _rnotes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else {}
        _rsum = risk_cards.summary_row_html(
            _rnotes.get("Risk Analysis") or "",
            _rnotes.get("SaaSpocalypse Resistance") or "", T)
        if _rsum:
            st.markdown(_rsum, unsafe_allow_html=True)
        else:
            st.caption("No Risk Analysis in the verdict format yet.")
        st.markdown(risk_cards.cards_section_html(_rnotes.get(risk_cards.TITLE), T),
                    unsafe_allow_html=True)

    with _tab_dcf:
        with st.container(key="tabcard_dcf_1"):
            st.markdown("#### Discounting Cash Flows")

            # ── WACC Inputs (collapsible) ──
            _ww_val = f'<div style="display:flex;justify-content:space-between;padding:6px 0;color:{T["text"]}"><span style="color:{T["text"]};{{extra}}">{{label}}</span><span style="color:{T["text"]};{{extra}}">{{value}}</span></div>'
            _ww_sep = f'<div style="border-top:1px solid {T["separator"]};margin:2px 0"></div>'

            with st.expander("### Hurdle rate", expanded=False):
              with st.container(border=True):
                _rf_label = "Risk-Free Rate % (TIPS — Real)" if cfg.get('valuation_basis') == 'real' else "Risk-Free Rate %"
                cfg['risk_free_rate'] = st.number_input(
                    _rf_label, value=cfg.get('risk_free_rate', 0.04) * 100,
                    step=0.1, format="%.2f", key="ed_rfr",
                ) / 100
                cfg['erp'] = st.number_input(
                    "Equity Risk Premium %", value=cfg.get('erp', 0.055) * 100,
                    step=0.1, format="%.2f", key="ed_erp",
                ) / 100
                cfg['credit_spread'] = st.number_input(
                    "Credit Spread %", value=cfg.get('credit_spread', 0.01) * 100,
                    step=0.1, format="%.2f", key="ed_cs",
                ) / 100
                cfg['tax_rate'] = st.number_input(
                    "Tax Rate %", value=cfg.get('tax_rate', 0.21) * 100,
                    step=0.5, format="%.1f", key="ed_tax",
                ) / 100

                _disc_modes = ["hurdle", "opportunity_cost", "capm"]
                _disc_labels = {
                    "hurdle": f"Fixed hurdle ({DEFAULT_HURDLE_RATE:.0%}, portfolio-wide)",
                    "opportunity_cost": "Opportunity cost (ke = rf + ERP, β = 1)",
                    "capm": "CAPM (ke = rf + levered β × ERP)",
                }
                # A config saved under a mode this build no longer offers must
                # not take the editor down; fall back to the default and let
                # the user re-pick.
                _cur_mode = cfg.get('discount_mode', DEFAULT_DISCOUNT_MODE)
                if _cur_mode not in _disc_modes:
                    logger.warning("Unknown discount_mode %r for %s; showing default",
                                   _cur_mode, ticker)
                    _cur_mode = DEFAULT_DISCOUNT_MODE
                cfg['discount_mode'] = st.selectbox(
                    "Discount mode",
                    _disc_modes,
                    index=_disc_modes.index(_cur_mode),
                    format_func=lambda m: _disc_labels[m],
                    key="ed_disc_mode",
                    help="Fixed hurdle: één vaste eis voor elk bedrijf — beweegt niet "
                         "mee met rente, β of kapitaalstructuur; risico hoort in het "
                         "groeipad en de marge. "
                         "Opportunity cost: rf + ERP, dus wél rentegevoelig. "
                         "CAPM: bedrijfsrisico via de sector-β in de discontovoet.",
                )

                st.markdown(_ww_sep, unsafe_allow_html=True)

                cfg['equity_market_value'] = int(st.number_input(
                    "Equity Market Value ($M)", value=int(cfg.get('equity_market_value', 0)),
                    step=1000, key="ed_eq_val",
                ))
                cfg['debt_market_value'] = int(st.number_input(
                    "Debt Market Value ($M)", value=int(cfg.get('debt_market_value', 0)),
                    step=100, key="ed_debt_val",
                ))

                _eq_val = cfg['equity_market_value']
                _debt_val = cfg['debt_market_value']
                _total_cap = _eq_val + _debt_val
                _eq_wt = _eq_val / _total_cap if _total_cap > 0 else 0
                _debt_wt = _debt_val / _total_cap if _total_cap > 0 else 0
                st.markdown(_ww_val.format(label="Equity Weight", value=f"{_eq_wt:.1%}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                st.markdown(_ww_val.format(label="Debt Weight", value=f"{_debt_wt:.1%}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)

                st.markdown(_ww_sep, unsafe_allow_html=True)

                # Sector betas
                @st.cache_data(ttl=3600, show_spinner=False)
                def _damodaran_betas():
                    return fetch_sector_betas()

                dam_betas = _damodaran_betas()
                sector_list = sorted(dam_betas.keys()) if dam_betas else []

                betas = list(cfg.get('sector_betas', []))
                # Auto-detect sector from SIC code if no betas configured yet
                if not betas:
                    betas = resolve_sector_betas(cfg.get('sic_code', 0),
                                                 cfg.get('sic_description', ''))
                st.markdown("**Sector Betas**")
                updated_betas = []
                for i, (name, beta, weight) in enumerate(betas):
                    bc1, bc2, bc3, bc4 = st.columns([3, 2, 2, 0.5])
                    with bc1:
                        if sector_list:
                            if name and name not in sector_list:
                                sector_list = [name, *sector_list]
                            idx = sector_list.index(name) if name in sector_list else 0
                            new_name = st.selectbox(
                                "Sector", sector_list, index=idx, key=f"ed_bn_{i}",
                            )
                            new_beta = _sector_beta_default(
                                name, beta, new_name, dam_betas)
                        else:
                            new_name = st.text_input("Sector", value=name, key=f"ed_bn_{i}")
                            new_beta = float(beta)
                    with bc2:
                        new_beta = st.number_input(
                            "Unlevered Beta", value=float(new_beta), step=0.01,
                            format="%.2f", key=f"ed_bb_{i}",
                        )
                    with bc3:
                        new_weight = st.number_input(
                            "Revenue Weight", value=float(weight), step=0.05,
                            format="%.2f", key=f"ed_bw_{i}",
                        )
                    with bc4:
                        st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
                        if st.button("\u2212", key=f"ed_bdel_{i}"):
                            continue
                    updated_betas.append((new_name, new_beta, new_weight))
                if st.button("+ Add sector", key="ed_badd"):
                    default_name = sector_list[0] if sector_list else "Market"
                    default_beta = dam_betas.get(default_name, 1.0) if dam_betas else 1.0
                    updated_betas.append((default_name, default_beta, 1.0))
                cfg['sector_betas'] = updated_betas

                _wu_beta = sum(ub * wt for _, ub, wt in cfg['sector_betas']) if cfg['sector_betas'] else 1.0
                _de_ratio = _debt_val / _eq_val if _eq_val > 0 else 0
                _capm_lev_beta = _wu_beta * (1 + (1 - cfg['tax_rate']) * _de_ratio)
                # Beta that actually feeds the discount rate — mirrors
                # dcf_calculator._effective_beta so the preview matches the engine.
                _eff_beta = 1.0 if cfg.get('discount_mode', DEFAULT_DISCOUNT_MODE) == 'opportunity_cost' else _capm_lev_beta
                st.markdown(_ww_val.format(label="Weighted Unlevered \u03b2", value=f"{_wu_beta:.2f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                if cfg.get('discount_mode', DEFAULT_DISCOUNT_MODE) == 'opportunity_cost':
                    st.markdown(_ww_val.format(label="Effective \u03b2 (opportunity cost)", value="1.00", extra="font-weight:700;"), unsafe_allow_html=True)
                    st.markdown(_ww_val.format(label="Levered \u03b2 (unused)", value=f"{_capm_lev_beta:.2f}", extra=f"color:{T['text_muted']};font-size:0.82rem;"), unsafe_allow_html=True)
                else:
                    st.markdown(_ww_val.format(label="Levered \u03b2", value=f"{_capm_lev_beta:.2f}", extra="font-weight:700;"), unsafe_allow_html=True)

                if cfg.get('valuation_basis') == 'real':
                    _be = cfg.get('breakeven_inflation', 0)
                    _nom_rf = cfg.get('nominal_risk_free_rate', 0)
                    st.markdown(
                        f'<div style="padding:6px 8px;margin:4px 0;border-radius:6px;'
                        f'background:{T["separator"]};font-size:0.82rem;color:{T["text"]};opacity:0.8">'
                        f'📐 Reële waardering — Nominale Rf: {_nom_rf:.2%} · '
                        f'Breakeven inflatie: {_be:.2%}</div>',
                        unsafe_allow_html=True,
                    )

                st.markdown(_ww_sep, unsafe_allow_html=True)

                _ke = cfg['risk_free_rate'] + _eff_beta * cfg['erp']
                _kd = (cfg['risk_free_rate'] + cfg['credit_spread']) * (1 - cfg['tax_rate'])
                st.markdown(_ww_val.format(label="Cost of Equity", value=f"{_ke:.2%}", extra="font-weight:700;"), unsafe_allow_html=True)
                st.markdown(_ww_val.format(label="Cost of Debt (after-tax)", value=f"{_kd:.2%}", extra="font-weight:700;"), unsafe_allow_html=True)

                st.markdown(_ww_sep, unsafe_allow_html=True)

                # Asked of the engine rather than recomputed here. This block
                # mirrored compute_wacc's arithmetic by hand, which is fine
                # until the engine grows a mode the mirror does not know — a
                # fixed hurdle ignores rf, beta and the debt blend entirely.
                _mode = cfg.get('discount_mode', DEFAULT_DISCOUNT_MODE)
                _rate = compute_wacc(cfg)
                _rate_label = {
                    "hurdle": "Hurdle rate",
                    "capm": "Hurdle rate (WACC)",
                }.get(_mode, "Hurdle rate")
                if _mode == 'capm' and _total_cap <= 0:
                    st.warning("Equity + Debt market value must be > 0 to compute the discount rate")
                else:
                    st.markdown(_ww_val.format(label=_rate_label, value=f"{_rate:.2%}",
                                               extra=f"font-weight:700;font-size:1.15rem;color:{T['accent']};"), unsafe_allow_html=True)
                    # What a WACC blend would have said, for reference only —
                    # it is not what the engine used.
                    if _mode != 'capm' and _total_cap > 0:
                        _wacc_blend = _eq_wt * _ke + _debt_wt * _kd
                        st.markdown(_ww_val.format(label="Blended WACC (unused)", value=f"{_wacc_blend:.2%}", extra=f"color:{T['text_muted']};font-size:0.82rem;"), unsafe_allow_html=True)

            _s2c_val = f'<div style="display:flex;justify-content:space-between;padding:6px 0;color:{T["text"]}"><span style="color:{T["text"]};{{extra}}">{{label}}</span><span style="color:{T["text"]};{{extra}}">{{value}}</span></div>'
            _s2c_sep = f'<div style="border-top:1px solid {T["separator"]};margin:2px 0"></div>'

            with st.expander("### Sales-to-Capital", expanded=False):
              with st.container(border=True):
                _s2c_years = cfg.get('ic_years', [])
                _s2c_rev = cfg.get('hist_revenue', [])
                _s2c_ca = cfg.get('current_assets', [])
                _s2c_cash = cfg.get('cash', [])
                _s2c_si = cfg.get('st_investments', [])
                _s2c_cl = cfg.get('current_liabilities', [])
                _s2c_sd = cfg.get('st_debt', [])
                _s2c_sl = cfg.get('st_leases', [])
                _s2c_ppe = cfg.get('net_ppe', [])
                _s2c_gi = cfg.get('goodwill_intang', [])
                _s2c_n = len(_s2c_years)

                # Niet alle bedrijven rapporteren elk balans-item (TSM mist bv.
                # st_investments/st_debt/st_leases voor sommige jaren). Vul korte
                # lijsten aan met 0 zodat de loop niet op een IndexError klapt;
                # een niet-gerapporteerd item telt als 0.
                def _pad(_lst, _n):
                    _lst = list(_lst or [])
                    if len(_lst) < _n:
                        _lst = _lst + [0.0] * (_n - len(_lst))
                    return _lst

                _s2c_ca = _pad(_s2c_ca, _s2c_n)
                _s2c_cash = _pad(_s2c_cash, _s2c_n)
                _s2c_si = _pad(_s2c_si, _s2c_n)
                _s2c_cl = _pad(_s2c_cl, _s2c_n)
                _s2c_sd = _pad(_s2c_sd, _s2c_n)
                _s2c_sl = _pad(_s2c_sl, _s2c_n)
                _s2c_ppe = _pad(_s2c_ppe, _s2c_n)
                _s2c_gi = _pad(_s2c_gi, _s2c_n)

                if _s2c_n >= 2 and len(_s2c_rev) >= _s2c_n:
                    _s2c_ratios = []
                    for _si in range(1, _s2c_n):
                        _rev_chg = _s2c_rev[_si] - _s2c_rev[_si - 1]
                        _ncwc_now = (_s2c_ca[_si] - _s2c_cash[_si] - _s2c_si[_si]) - (_s2c_cl[_si] - _s2c_sd[_si] - _s2c_sl[_si])
                        _ncwc_prev = (_s2c_ca[_si-1] - _s2c_cash[_si-1] - _s2c_si[_si-1]) - (_s2c_cl[_si-1] - _s2c_sd[_si-1] - _s2c_sl[_si-1])
                        _delta_ncwc = _ncwc_now - _ncwc_prev
                        _delta_ppe = _s2c_ppe[_si] - _s2c_ppe[_si - 1]
                        _delta_gi = _s2c_gi[_si] - _s2c_gi[_si - 1]
                        _ic_chg = _delta_ncwc + _delta_ppe + _delta_gi

                        _yr_label = f"{_s2c_years[_si-1]}\u2192{_s2c_years[_si]}"
                        st.markdown(_s2c_val.format(label=f"**{_yr_label}**", value="", extra="font-weight:700;"), unsafe_allow_html=True)
                        st.markdown(_s2c_val.format(label="\u2003\u0394 Revenue", value=f"${_rev_chg:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                        st.markdown(_s2c_val.format(label="\u2003\u0394 Non-cash WC", value=f"${_delta_ncwc:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                        st.markdown(_s2c_val.format(label="\u2003\u0394 Net PP&E", value=f"${_delta_ppe:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                        st.markdown(_s2c_val.format(label="\u2003\u0394 Goodwill & Intang.", value=f"${_delta_gi:,.0f}", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                        st.markdown(_s2c_val.format(label="\u2003\u0394 Invested Capital", value=f"${_ic_chg:,.0f}", extra="font-weight:700;"), unsafe_allow_html=True)
                        if _ic_chg > 0 and _rev_chg != 0:
                            _yr_s2c = _rev_chg / _ic_chg
                            _s2c_ratios.append(_yr_s2c)
                            st.markdown(_s2c_val.format(label="\u2003Sales-to-Capital", value=f"{_yr_s2c:.2f}", extra="font-weight:700;"), unsafe_allow_html=True)
                        else:
                            st.markdown(_s2c_val.format(label="\u2003Sales-to-Capital", value="n/a", extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                        st.markdown(_s2c_sep, unsafe_allow_html=True)

                    if _s2c_ratios:
                        _s2c_ratios.sort()
                        _s2c_median = _s2c_ratios[len(_s2c_ratios) // 2]
                        st.markdown(_s2c_val.format(label="Median Sales-to-Capital", value=f"{_s2c_median:.2f}",
                                                   extra=f"font-weight:700;font-size:1.15rem;color:{T['accent']};"), unsafe_allow_html=True)
                        _eff_stc, _eff_tv_stc = _effective_stc(cfg)
                        if _eff_stc and min(_eff_stc) != max(_eff_stc):
                            _eff_txt = f"{min(_eff_stc):.2f}–{max(_eff_stc):.2f}"
                        else:
                            _eff_txt = f"{(_eff_stc[0] if _eff_stc else 1.0):.2f}"
                        st.markdown(_s2c_val.format(label="Used in DCF", value=_eff_txt,
                                                   extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
                        st.markdown(_s2c_val.format(label=" terminal", value=f"{_eff_tv_stc:.2f}",
                                                   extra=f"color:{T['text_muted']};font-size:0.85rem;"), unsafe_allow_html=True)
                else:
                    st.info("Not enough historical data to compute Sales-to-Capital breakdown")

                # Sector reference from Damodaran
                st.markdown(_s2c_sep, unsafe_allow_html=True)
                @st.cache_data(ttl=3600, show_spinner=False)
                def _damodaran_s2c():
                    return fetch_sector_s2c()

                _dam_s2c = _damodaran_s2c()
                if _dam_s2c:
                    _sector_names = [name for name, _, _ in cfg.get('sector_betas', [])]
                    _matched = []
                    for _sn in _sector_names:
                        # Exact match first
                        if _sn in _dam_s2c:
                            _matched.append((_sn, _dam_s2c[_sn]))
                        else:
                            # Fuzzy: match on first word(s) before parentheses or common prefix
                            _sn_base = _sn.split("(")[0].strip().lower()
                            _sn_words = set(_sn.lower().split())
                            _best_name, _best_score = None, 0
                            for _ds in _dam_s2c:
                                _ds_base = _ds.split("(")[0].strip().lower()
                                _ds_words = set(_ds.lower().split())
                                _overlap = len(_sn_words & _ds_words)
                                if _sn_base == _ds_base:
                                    _overlap += 5  # strong boost for matching base name
                                if _overlap > _best_score:
                                    _best_score = _overlap
                                    _best_name = _ds
                            if _best_name and _best_score >= 1:
                                _matched.append((_best_name, _dam_s2c[_best_name]))
                    st.markdown("**Sector Reference (Damodaran)**")
                    if _matched:
                        for _sn, _sv in _matched:
                            st.markdown(_s2c_val.format(label=f"\u2003{_sn}", value=f"{_sv:.2f}",
                                                       extra=f"color:{T['text_muted']};"), unsafe_allow_html=True)
                    else:
                        st.markdown(f'<p style="color:{T["text_muted"]};font-size:0.85rem">No matching sector found</p>', unsafe_allow_html=True)

            st.markdown(f'<p style="color:{T["text_muted"]};font-size:0.85rem">In millions</p>', unsafe_allow_html=True)

            _n = len(growth)
            growth = [float(g) for g in growth]
            margins = [float(m) for m in margins]
            _base_rev = cfg.get('base_revenue', 0)
            _base_oi = cfg.get('base_oi', 0)
            _tg = float(cfg.get('terminal_growth', 0.03))
            _tm = float(cfg.get('terminal_margin', margins[-1] if margins else 0.30))

            # Expand single-value assumptions to per-year lists (all floats)
            _default_wacc = float(compute_wacc(cfg) if cfg.get('equity_market_value', 0) + cfg.get('debt_market_value', 0) > 0 else 0.08)
            _wacc_list = [float(x) for x in cfg.get('wacc_per_year', [_default_wacc] * _n)]
            if len(_wacc_list) < _n:
                _wacc_list.extend([_wacc_list[-1] if _wacc_list else _default_wacc] * (_n - len(_wacc_list)))
            _default_tax = float(cfg.get('tax_rate', 0.21))
            _tax_list = [float(x) for x in cfg.get('tax_per_year', [_default_tax] * _n)]
            if len(_tax_list) < _n:
                _tax_list.extend([_tax_list[-1] if _tax_list else _default_tax] * (_n - len(_tax_list)))
            _default_stc = float(cfg.get('sales_to_capital', 1.0))
            _stc_list = [float(x) for x in cfg.get('stc_per_year', [_default_stc] * _n)]
            if len(_stc_list) < _n:
                _stc_list.extend([_stc_list[-1] if _stc_list else _default_stc] * (_n - len(_stc_list)))

            # Terminal column editable values (defaults from config or last year)
            _tv_tax_default = float(cfg.get('terminal_tax', _tax_list[-1] if _tax_list else _default_tax))
            _tv_stc_default = float(cfg.get('terminal_stc', _stc_list[-1] if _stc_list else _default_stc))
            # Pre-read terminal WACC from session state (widget rendered after TV calc)
            _tv_wacc_default = cfg.get('terminal_wacc', _wacc_list[-1] if _wacc_list else _default_wacc)
            _tv_wacc = st.session_state.get("ed_w_tv", _tv_wacc_default * 100) / 100

            # Column layout: label + base year + 10 projection years + terminal
            _cw = [1.8] + [1] * (_n + 2)
            _tv_col = _n + 2  # terminal column index
            _cs = f'font-size:0.78rem;padding:2px 0;min-height:28px;display:flex;align-items:center;justify-content:right;color:{T["text"]}'
            _cs_bold = _cs + ';font-weight:700'
            _cs_label = f'font-size:0.78rem;padding:2px 0;min-height:28px;display:flex;align-items:center;color:{T["text"]}'
            _cs_label_bold = _cs_label + ';font-weight:700'
            _cs_sep = f'border-top:2px solid {T["border_medium"]};' + _cs
            _cs_hdr = f'font-size:0.78rem;padding:4px 0;min-height:32px;display:flex;align-items:center;justify-content:right;font-weight:700;border-bottom:2px solid {T["border_medium"]};color:{T["text"]}'
            _cs_hdr_label = f'font-size:0.78rem;padding:4px 0;min-height:32px;display:flex;align-items:center;font-weight:700;border-bottom:2px solid {T["border_medium"]};color:{T["text"]}'
            _tv_bg = f'border-left:2px solid {T["border_medium"]};padding-left:8px'

            def _dcf_row_label(cols, label, bold=False):
                with cols[0]:
                    st.markdown(f"<div style='{_cs_label_bold if bold else _cs_label}'>{label}</div>", unsafe_allow_html=True)

            def _dcf_row_val(cols, idx, text, bold=False, sep=False, tv=False):
                style = _cs_sep if sep else (_cs_bold if bold else _cs)
                if tv or idx == _tv_col:
                    style += f';{_tv_bg}'
                with cols[idx]:
                    st.markdown(f"<div style='{style}'>{text}</div>", unsafe_allow_html=True)

            def _dcf_row_input(cols, idx, key, value, step, fmt, is_pct=True):
                with cols[idx]:
                    val = round(float(value) * 100, 6) if is_pct else round(float(value), 6)
                    stp = round(float(step), 6)
                    # Force session state to float to prevent type mismatch on re-render
                    if key in st.session_state and not isinstance(st.session_state[key], float):
                        st.session_state[key] = float(st.session_state[key])
                    v = st.number_input(key, value=val, step=stp, format=fmt,
                                        key=key, label_visibility="collapsed")
                    return v / 100 if is_pct else v

            def _dcf_divider():
                st.markdown(f"<div style='border-top:1px solid {T['spinner_border']};margin:2px 0'></div>", unsafe_allow_html=True)

            # ── Year header row ──
            with st.container(border=True):
                hdr = st.columns(_cw)
                with hdr[0]:
                    st.markdown(f"<div style='{_cs_hdr_label}'></div>", unsafe_allow_html=True)
                with hdr[1]:
                    st.markdown(f"<div style='{_cs_hdr}'>{base_year}</div>", unsafe_allow_html=True)
                for i in range(_n):
                    with hdr[i + 2]:
                        st.markdown(f"<div style='{_cs_hdr}'>{base_year + i + 1}</div>", unsafe_allow_html=True)
                with hdr[_tv_col]:
                    st.markdown(f"<div style='{_cs_hdr};{_tv_bg}'>Terminal</div>", unsafe_allow_html=True)

                # ── Period row ──
                pr = st.columns(_cw)
                _dcf_row_label(pr, "Period")
                _dcf_row_val(pr, 1, "0")
                for i in range(_n):
                    _dcf_row_val(pr, i + 2, f"{0.5 + i:.1f}")
                _dcf_row_val(pr, _tv_col, "")

                # ── Revenue Growth (editable) ──
                gr = st.columns(_cw)
                _dcf_row_label(gr, "Revenue Growth", bold=True)
                _dcf_row_val(gr, 1, "")
                for i in range(_n):
                    growth[i] = _dcf_row_input(gr, i + 2, f"ed_g_{i}", growth[i], 0.5, "%.2f")
                _tg = _dcf_row_input(gr, _tv_col, "ed_tg_tv", _tg, 0.5, "%.2f")

                _revs = [_base_rev]
                for g in growth:
                    _revs.append(_revs[-1] * (1 + g))

                # ── Revenue (computed) ──
                rv = st.columns(_cw)
                _dcf_row_label(rv, "Revenue")
                _dcf_row_val(rv, 1, f"{_base_rev:,.0f}")
                for i in range(_n):
                    _dcf_row_val(rv, i + 2, f"{_revs[i + 1]:,.0f}")
                _tv_rev = _revs[-1] * (1 + _tg)
                _dcf_row_val(rv, _tv_col, f"{_tv_rev:,.0f}")

                # ── Operating Margin (editable) ──
                mr = st.columns(_cw)
                _dcf_row_label(mr, "Operating Margin", bold=True)
                _base_margin = cfg.get('base_op_margin', 0)
                _dcf_row_val(mr, 1, f"{_base_margin:.2%}")
                for i in range(_n):
                    margins[i] = _dcf_row_input(mr, i + 2, f"ed_m_{i}", margins[i], 0.5, "%.2f")
                _tm = _dcf_row_input(mr, _tv_col, "ed_tm_tv", _tm, 0.5, "%.2f")

                # ── Operating Income (computed) ──
                oi_row = st.columns(_cw)
                _dcf_row_label(oi_row, "Operating Income")
                _dcf_row_val(oi_row, 1, f"{_base_oi:,.0f}")
                _oi_vals = [_revs[i + 1] * margins[i] for i in range(_n)]
                for i in range(_n):
                    _dcf_row_val(oi_row, i + 2, f"{_oi_vals[i]:,.0f}")
                _tv_oi = _tv_rev * _tm
                _dcf_row_val(oi_row, _tv_col, f"{_tv_oi:,.0f}")

                _dcf_divider()  # ── Revenue → NOPAT ──

                # ── Tax Rate (editable) ──
                tr = st.columns(_cw)
                _dcf_row_label(tr, "Tax Rate", bold=True)
                _dcf_row_val(tr, 1, f"{_default_tax:.2%}")
                for i in range(_n):
                    _tax_list[i] = _dcf_row_input(tr, i + 2, f"ed_t_{i}", _tax_list[i], 0.5, "%.2f")
                _tv_tax = _dcf_row_input(tr, _tv_col, "ed_t_tv", _tv_tax_default, 0.5, "%.2f")

                # ── NOPAT (computed) ──
                np_row = st.columns(_cw)
                _dcf_row_label(np_row, "NOPAT")
                _base_nopat = _base_oi * (1 - _default_tax)
                _dcf_row_val(np_row, 1, f"{_base_nopat:,.0f}")
                _nopat_vals = [_oi_vals[i] * (1 - _tax_list[i]) for i in range(_n)]
                for i in range(_n):
                    _dcf_row_val(np_row, i + 2, f"{_nopat_vals[i]:,.0f}")
                _tv_nopat = _tv_oi * (1 - _tv_tax)
                _dcf_row_val(np_row, _tv_col, f"{_tv_nopat:,.0f}")

                _dcf_divider()  # ── NOPAT → Reinvestment ──

                # ── Sales-to-Capital (editable) ──
                sc_row = st.columns(_cw)
                _dcf_row_label(sc_row, "Sales-to-Capital", bold=True)
                _dcf_row_val(sc_row, 1, "")
                for i in range(_n):
                    _stc_list[i] = _dcf_row_input(sc_row, i + 2, f"ed_s_{i}", _stc_list[i], 0.05, "%.2f", is_pct=False)
                _tv_stc = _dcf_row_input(sc_row, _tv_col, "ed_s_tv", _tv_stc_default, 0.05, "%.2f", is_pct=False)

                # ── Reinvestment (computed) ──
                ri_row = st.columns(_cw)
                _dcf_row_label(ri_row, "Reinvestment")
                _dcf_row_val(ri_row, 1, "")
                _reinvest_vals = [(_revs[i + 1] - _revs[i]) / _stc_list[i] if _stc_list[i] else 0 for i in range(_n)]
                for i in range(_n):
                    _dcf_row_val(ri_row, i + 2, f"{_reinvest_vals[i]:,.0f}")
                _tv_reinvest = (_tv_rev - _revs[-1]) / _tv_stc if _tv_stc else 0
                _dcf_row_val(ri_row, _tv_col, f"{_tv_reinvest:,.0f}")

                _dcf_divider()  # ── Reinvestment → FCFF ──

                # ── FCFF (computed) ──
                # op_margins are GAAP (SBC already expensed in operating income),
                # so FCFF = NOPAT − reinvestment with no separate SBC line — matches
                # dcf_calculator.compute_intrinsic_value (SBC convention 2026-06-17).
                fcff_row = st.columns(_cw)
                _dcf_row_label(fcff_row, "FCFF")
                _dcf_row_val(fcff_row, 1, "")
                _fcff_vals = [_nopat_vals[i] - _reinvest_vals[i] for i in range(_n)]
                for i in range(_n):
                    _dcf_row_val(fcff_row, i + 2, f"{_fcff_vals[i]:,.0f}")
                _tv_fcff = _tv_nopat - _tv_reinvest
                _dcf_row_val(fcff_row, _tv_col, f"{_tv_fcff:,.0f}")

                # ── Undiscounted TV ──
                tv_row = st.columns(_cw)
                _dcf_row_label(tv_row, "Undiscounted TV")
                for i in range(_n + 1):
                    _dcf_row_val(tv_row, i + 1, "")
                _tv_undiscounted = _tv_fcff / (_tv_wacc - _tg) if (_tv_wacc - _tg) > 0 else 0
                _dcf_row_val(tv_row, _tv_col, f"{_tv_undiscounted:,.0f}")

                _dcf_divider()  # ── FCFF → Discounting ──

                # ── Hurdle rate (editable) ──
                wr = st.columns(_cw)
                _dcf_row_label(wr, "Hurdle rate", bold=True)
                _dcf_row_val(wr, 1, "")
                for i in range(_n):
                    _wacc_list[i] = _dcf_row_input(wr, i + 2, f"ed_w_{i}", _wacc_list[i], 0.1, "%.2f")
                _tv_wacc = _dcf_row_input(wr, _tv_col, "ed_w_tv", _tv_wacc, 0.1, "%.2f")

                # ── Cumulative Discount Factor (computed) ──
                df_row = st.columns(_cw)
                _dcf_row_label(df_row, "Cum. Discount Factor")
                _dcf_row_val(df_row, 1, "1")
                _df_vals = []
                for i in range(_n):
                    period = 0.5 + i
                    df = 1 / (1 + _wacc_list[i]) ** period if _wacc_list[i] > 0 else 1
                    _df_vals.append(df)
                    _dcf_row_val(df_row, i + 2, f"{df:.2f}")
                _dcf_row_val(df_row, _tv_col, "")

                # ── PV of FCFF (computed, with separator) ──
                pv_row = st.columns(_cw)
                _dcf_row_label(pv_row, "PV of FCFF", bold=True)
                _dcf_row_val(pv_row, 1, "", sep=True)
                _pv_vals = [_fcff_vals[i] * _df_vals[i] for i in range(_n)]
                for i in range(_n):
                    _dcf_row_val(pv_row, i + 2, f"{_pv_vals[i]:,.0f}", sep=True)
                _tv_df = 1 / (1 + _tv_wacc) ** (0.5 + _n - 1) if _tv_wacc > 0 and _n > 0 else 1
                _pv_tv = _tv_undiscounted * _tv_df
                _dcf_row_val(pv_row, _tv_col, f"{_pv_tv:,.0f}", sep=True)

                # ── Enterprise Value ──
                _sum_pv = sum(_pv_vals)
                _ev = _sum_pv + _pv_tv
                ev_row = st.columns(_cw)
                _dcf_row_label(ev_row, "Enterprise Value", bold=True)
                _dcf_row_val(ev_row, 1, f"{_ev:,.0f}", bold=True)
                for i in range(_n + 1):
                    _dcf_row_val(ev_row, i + 2, "")

            # Write back edited values and auto-save
            _prev_snapshot = (
                tuple(cfg.get('revenue_growth', [])), tuple(cfg.get('op_margins', [])),
                tuple(cfg.get('wacc_per_year', [])), tuple(cfg.get('tax_per_year', [])),
                tuple(cfg.get('stc_per_year', [])),
                cfg.get('terminal_growth'), cfg.get('terminal_margin'),
                cfg.get('terminal_tax'), cfg.get('terminal_stc'),
                cfg.get('terminal_wacc'),
            )
            cfg['revenue_growth'] = growth
            cfg['op_margins'] = margins
            cfg['tax_per_year'] = _tax_list
            cfg['stc_per_year'] = _stc_list
            cfg['terminal_growth'] = _tg
            cfg['terminal_margin'] = _tm
            cfg['terminal_tax'] = _tv_tax
            cfg['terminal_stc'] = _tv_stc
            # Persist per-year / terminal WACC only when the user overrode the
            # live compute_wacc default; otherwise leave them absent so the
            # discount rate is always taken live (no frozen-WACC drift).
            _apply_wacc_persistence(cfg, _wacc_list, _tv_wacc, _default_wacc)
            _new_snapshot = (
                tuple(growth), tuple(margins),
                tuple(cfg.get('wacc_per_year', [])), tuple(_tax_list),
                tuple(_stc_list),
                _tg, _tm, _tv_tax, _tv_stc, cfg.get('terminal_wacc'),
            )
            if _new_snapshot != _prev_snapshot:
                save_config(_sb_client, ticker, cfg)

            # Placeholder inside the DCF card. The Valuation Bridge (rendered
            # last, after all tabs, so it sees final cfg) fills this slot, so it
            # sits inside the DCF card without changing render order.
            _bridge_slot = st.container()

    with _tab_rdcf:
        with st.container(key="tabcard_rdcf"):

            st.markdown("#### Reverse DCF")

            # ── Adjustable ranges (expander) ──
            _rdcf_g_range = None
            _rdcf_m_range = None
            with st.expander("Adjust ranges"):
                _rc1, _rc2, _rc3 = st.columns(3)
                with _rc1:
                    st.markdown("**Revenue CAGR**")
                    _g_base = sum(cfg.get('revenue_growth', [0])) / max(len(cfg.get('revenue_growth', [0])), 1) * 100
                    _rg_min = st.number_input("Min %", value=max(0.0, float(round(_g_base - 5))), step=1.0, format="%.0f", key="rdcf_gmin") / 100
                    _rg_max = st.number_input("Max %", value=float(round(_g_base + 5)), step=1.0, format="%.0f", key="rdcf_gmax") / 100
                    _rg_step = st.number_input("Step %", value=0.5, step=0.5, format="%.1f", key="rdcf_gstep") / 100
                    if _rg_step > 0 and _rg_max > _rg_min:
                        _rdcf_g_range = (_rg_min, _rg_max, _rg_step)
                with _rc2:
                    st.markdown("**Operating Margin**")
                    _m_base = sum(cfg.get('op_margins', [0])) / max(len(cfg.get('op_margins', [0])), 1) * 100
                    _rm_min = st.number_input("Min %", value=max(1.0, float(round(_m_base - 5))), step=1.0, format="%.0f", key="rdcf_mmin") / 100
                    _rm_max = st.number_input("Max %", value=float(round(_m_base + 5)), step=1.0, format="%.0f", key="rdcf_mmax") / 100
                    _rm_step = st.number_input("Step %", value=0.5, step=0.5, format="%.1f", key="rdcf_mstep") / 100
                    if _rm_step > 0 and _rm_max > _rm_min:
                        _rdcf_m_range = (_rm_min, _rm_max, _rm_step)
                with _rc3:
                    st.markdown("**Hurdle rate**")
                    _rdcf_wacc = st.number_input(
                        "Hurdle rate %", value=val['wacc'] * 100,
                        step=0.1, format="%.2f", key="rdcf_wacc",
                    ) / 100

            # ── Compute reverse DCF ──
            _rdcf = compute_reverse_dcf(cfg, wacc=_rdcf_wacc,
                                         growth_range=_rdcf_g_range,
                                         margin_range=_rdcf_m_range)

            # ── Market vs Your Base Case comparison ──
            _bc = _rdcf['base_cagr']
            _bm = _rdcf['base_margin']
            _closest = _rdcf['closest']
            _impl_g, _impl_m = _closest if _closest else (_bc, _bm)

            _card_border = f'border-top:1px solid {T["border_medium"]};border-right:1px solid {T["border_medium"]};border-bottom:1px solid {T["border_medium"]};border-left:3px solid {T["accent"]}'
            _mc1, _mc2 = st.columns(2)
            with _mc1:
                st.markdown(
                    f'<div style="{_card_border};border-radius:12px;padding:20px;text-align:center;background:{T["card"]};box-shadow:{T["shadow"]}">'
                    f'<div style="color:{T["text_muted"]};font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Market implies</div>'
                    f'<div style="font-size:1.8rem;font-weight:700;margin:8px 0;color:{T["text"]}">{_impl_g:.1%} CAGR &nbsp;+&nbsp; {_impl_m:.1%} Margin</div>'
                    f'<div style="color:{T["text_muted"]};font-size:0.85rem">to justify ${_rdcf["market_price"]:.2f}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with _mc2:
                st.markdown(
                    f'<div style="{_card_border};border-radius:12px;padding:20px;text-align:center;background:{T["card"]};box-shadow:{T["shadow"]}">'
                    f'<div style="color:{T["text_muted"]};font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Your base case</div>'
                    f'<div style="font-size:1.8rem;font-weight:700;margin:8px 0;color:{T["text"]}">{_bc:.1%} CAGR &nbsp;+&nbsp; {_bm:.1%} Margin</div>'
                    f'<div style="color:{T["text_muted"]};font-size:0.85rem">DCF value ${val["intrinsic_value"]:.2f}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # ── Conclusion ──
            if _impl_g > _bc * 1.1 or _impl_m > _bm * 1.1:
                _conclusion = (f"Market is more optimistic — it prices in "
                               f"{_impl_g:.1%} CAGR / {_impl_m:.1%} margin "
                               f"vs your {_bc:.1%} / {_bm:.1%}.")
            elif _impl_g < _bc * 0.9 or _impl_m < _bm * 0.9:
                _conclusion = (f"Potential undervaluation — market only requires "
                               f"{_impl_g:.1%} CAGR / {_impl_m:.1%} margin, "
                               f"below your {_bc:.1%} / {_bm:.1%} base case.")
            else:
                _conclusion = (f"Fairly priced — market-implied assumptions "
                               f"({_impl_g:.1%} CAGR / {_impl_m:.1%} margin) "
                               f"are close to your base case ({_bc:.1%} / {_bm:.1%}).")
            st.markdown(
                f'<div style="color:{T["text_muted"]};font-size:0.85rem;text-align:center;margin:12px 0 16px">{_conclusion}</div>',
                unsafe_allow_html=True,
            )

            # ── Sensitivity matrix ──
            st.markdown(f"**Sensitivity Matrix** — Hurdle rate: {_rdcf['wacc']:.2%} | Market: ${_rdcf['market_price']:.2f}")

            _g_tests = _rdcf['growth_tests']
            _m_tests = _rdcf['margin_tests']
            _closest = _rdcf['closest']
            _mkt = _rdcf['market_price']

            # Build pivot lookup
            _matrix_data = {}
            for entry in _rdcf['matrix']:
                _matrix_data[(entry['growth'], entry['margin'])] = entry['price']

            # Render as HTML table for full dark-mode support
            _hdr_style = f'background:{T["card"]};color:{T["text_muted"]};font-size:0.7rem;font-weight:600;padding:6px 8px;text-align:center;position:sticky;top:0;z-index:1'
            _row_hdr = f'background:{T["card"]};color:{T["text"]};font-size:0.75rem;font-weight:600;padding:6px 8px;text-align:left;position:sticky;left:0;z-index:1'
            _html = f'<div style="overflow-x:auto;border:1px solid {T["border_medium"]};border-radius:12px;background:{T["card"]}">'
            _html += '<table style="border-collapse:collapse;width:100%;font-size:0.75rem">'
            # Header row
            _html += f'<thead><tr><th style="{_hdr_style};text-align:left">CAGR \\ Margin</th>'
            for mg in _m_tests:
                _html += f'<th style="{_hdr_style}">{mg:.1%}</th>'
            _html += '</tr></thead><tbody>'
            # Data rows
            for g in _g_tests:
                _html += f'<tr><td style="{_row_hdr}">{g:.1%}</td>'
                for mg in _m_tests:
                    price = _matrix_data.get((g, mg), 0)
                    if (g, mg) == _closest:
                        _bg = T["accent"]
                        _fg = '#fff'
                        _fw = 'bold'
                    elif price >= _mkt:
                        _bg = T["accent_fill"]
                        _fg = T["text"]
                        _fw = 'normal'
                    else:
                        _bg = T["red_light"]
                        _fg = T["text"]
                        _fw = 'normal'
                    _html += f'<td style="background:{_bg};color:{_fg};font-weight:{_fw};padding:6px 8px;text-align:center">${price:,.0f}</td>'
                _html += '</tr>'
            _html += '</tbody></table></div>'
            st.markdown(_html, unsafe_allow_html=True)

            # ── Legend ──
            st.markdown(
                f'<div style="display:flex;gap:20px;font-size:0.8rem;color:{T["text_muted"]};margin-top:4px">'
                f'<span><span style="display:inline-block;width:12px;height:12px;background:{T["accent"]};border-radius:2px;vertical-align:middle;margin-right:4px"></span>Market-implied</span>'
                f'<span><span style="display:inline-block;width:12px;height:12px;background:{T["accent_fill"]};border:1px solid {T["accent"]};border-radius:2px;vertical-align:middle;margin-right:4px"></span>Undervalued</span>'
                f'<span><span style="display:inline-block;width:12px;height:12px;background:{T["red_light"]};border:1px solid {T["red"]};border-radius:2px;vertical-align:middle;margin-right:4px"></span>Overvalued</span>'
                '</div>',
                unsafe_allow_html=True,
            )

    with _tab_peers:
        with st.container(key="tabcard_peers"):
            _base_margin_p = cfg.get('base_op_margin', 0)
            _rev_growth_p = growth[0] if growth else 0
            _ev_rev_p = _ev / _base_rev if _base_rev else 0
            st.markdown("#### Peer Comparison")
            # ── Peer-multiples fair value (relative value; not weighted into FV) ──
            import valuation_lenses as _vl_pm
            _price_pm = float(cfg.get("stock_price", 0) or 0)

            def _delta_pm(fv):
                if not _price_pm or not fv:
                    return None
                return f"{(fv - _price_pm) / _price_pm * 100:+.1f}% vs price"

            _mult_pm = _vl_pm.compute_multiples_lens(cfg)
            st.markdown("##### Peer-multiples fair value")
            st.caption("Relative value from peer trailing P/E and EV/EBIT — "
                       "excluded from the blended fair value and the watchlist; "
                       "shown here for reference only.")
            if _mult_pm:
                _dpm = _mult_pm.get("details", {})
                _pmc1, _pmc2, _pmc3 = st.columns(3)
                _pmc1.metric("Fair value (mid)", f"${_mult_pm['fv_mid']:,.0f}",
                             delta=_delta_pm(_mult_pm["fv_mid"]))
                _pe_fv_pm = _mult_pm.get("fv_mid_pe")
                _pmc2.metric(f"P/E anchor ({_dpm.get('pe_basis') or '—'})",
                             f"${_pe_fv_pm:,.0f}" if _pe_fv_pm else "—",
                             delta=_delta_pm(_pe_fv_pm))
                _ev_fv_pm = _mult_pm.get("fv_mid_ev")
                _pmc3.metric(f"EV/EBIT anchor ({_dpm.get('ev_basis') or '—'})",
                             f"${_ev_fv_pm:,.0f}" if _ev_fv_pm else "—",
                             delta=_delta_pm(_ev_fv_pm))
                st.markdown(
                    f'<div style="font-size:0.82rem;color:{T["text_muted"]}">'
                    f'Range ${_mult_pm["fv_low"]:,.0f} – ${_mult_pm["fv_high"]:,.0f} '
                    f'· closest peer: {_dpm.get("closest_peer") or "—"}</div>',
                    unsafe_allow_html=True)
            else:
                st.info("Peer-multiples unavailable — no peers with computable "
                        "trailing_pe/ev_ebit (or missing ttm_eps/ttm_ebit).")

            # ── Own historical multiples (relative value; not weighted) ──
            st.markdown("##### Own historical multiples")
            st.caption("Relative value from this ticker's own historical trailing "
                       "P/E and EV multiple — also excluded from the blended fair "
                       "value and the watchlist; reference only.")
            _hist_pm = _vl_pm.compute_historical_lens(cfg)
            if _hist_pm:
                _dh_pm = _hist_pm.get("details", {})
                _hc1, _hc2, _hc3 = st.columns(3)
                _hc1.metric("Fair value (mid)", f"${_hist_pm['fv_mid']:,.0f}",
                            delta=_delta_pm(_hist_pm["fv_mid"]))
                _tpe_h = _dh_pm.get("historical_trailing_pe_fv")
                _hc2.metric("Own trailing P/E",
                            f"${_tpe_h:,.0f}" if _tpe_h else "—",
                            delta=_delta_pm(_tpe_h))
                _hev_h = _dh_pm.get("historical_ev_ebitda_fv")
                _hc3.metric(f"Own EV multiple ({_dh_pm.get('ev_basis') or '—'})",
                            f"${_hev_h:,.0f}" if _hev_h else "—",
                            delta=_delta_pm(_hev_h))
            else:
                st.info("Own-history multiples unavailable — no historical "
                        "trailing P/E or EV multiple on file for this ticker.")
            st.markdown("---")

            # Compute metrics for current ticker
            _mkt_cap_p = cfg.get('equity_market_value', 0)
            _debt_p = cfg.get('debt_market_value', 0)
            _cash_p = cfg.get('cash_bridge', 0)
            _ev_calc_p = _mkt_cap_p + _debt_p - _cash_p
            _ebitda_p = _base_oi * 1.3 if _base_oi > 0 else 0
            _ev_ebitda_p = _ev_calc_p / _ebitda_p if _ebitda_p > 0 else 0
            _ni_list = cfg.get('hist_net_income', [])
            _ni_p = _ni_list[-1] if _ni_list else 0
            _pe_p = _mkt_cap_p / _ni_p if _ni_p > 0 else 0
            # ROIC: NOPAT / Invested Capital
            _ca_list = cfg.get('current_assets', [])
            _cl_list = cfg.get('current_liabilities', [])
            _ppe_list = cfg.get('net_ppe', [])
            _gi_list = cfg.get('goodwill_intang', [])
            _sd_list = cfg.get('st_debt', [])
            _ca_p = _ca_list[-1] if _ca_list else 0
            _cl_p = _cl_list[-1] if _cl_list else 0
            _ppe_p = _ppe_list[-1] if _ppe_list else 0
            _gi_p = _gi_list[-1] if _gi_list else 0
            _sd_p = _sd_list[-1] if _sd_list else 0
            _ic_p = (_ca_p - _cash_p) + _ppe_p + _gi_p - (_cl_p - _sd_p)
            _nopat_p = _base_oi * (1 - 0.21)
            _roic_p = _nopat_p / _ic_p if _ic_p > 0 else 0

            # Build all rows: current ticker first, then peers
            peers = cfg.get('peers', [])
            _peer_rows = [
                {"ticker": ticker, "ev_revenue": _ev_rev_p, "ev_ebitda": _ev_ebitda_p,
                 "pe": _pe_p, "op_margin": _base_margin_p, "rev_growth": _rev_growth_p,
                 "roic": _roic_p, "is_self": True},
            ] + [dict(**p, is_self=False) for p in peers]

            _peer_metrics = [
                ("EV/Rev", "ev_revenue", "x", 1),
                ("EV/EBITDA", "ev_ebitda", "x", 1),
                ("P/E", "pe", "x", 1),
                ("Op Margin", "op_margin", "%", 1),
                ("Rev Growth", "rev_growth", "%", 1),
                ("ROIC", "roic", "%", 0),
            ]

            _th_style = (f'text-align:right;padding:8px 12px;border-bottom:2px solid {T["border_medium"]};color:{T["text_muted"]};'
                         'font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em')
            _ptable = (
                '<div style="overflow-x:auto">'
                '<table style="width:100%;border-collapse:collapse;font-size:0.9rem">'
                '<thead><tr>'
                f'<th style="text-align:left;padding:8px 12px;border-bottom:2px solid {T["border_medium"]};color:{T["text_muted"]};'
                f'font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em">Company</th>'
            )
            for mlabel, _, _, _ in _peer_metrics:
                _ptable += f'<th style="{_th_style}">{mlabel}</th>'
            _ptable += '</tr></thead><tbody>'

            for idx_p, pr in enumerate(_peer_rows):
                _is_self = pr.get("is_self", False)
                _pt = pr.get("ticker", "")
                _peer_logo = _logo_img(
                    _pt, None, "",
                    "width:28px;height:28px;border-radius:50%;object-fit:cover")
                _row_bg = f'background:{T["row_alt"]};' if _is_self else ''
                _fw = 'font-weight:700;' if _is_self else ''
                _ptable += f'<tr style="{_row_bg}">'
                _ptable += (
                    f'<td style="padding:10px 12px;border-bottom:1px solid {T["border_light"]};color:{T["text"]};{_fw}">'
                    f'<div style="display:flex;align-items:center;gap:10px">'
                    f'{_peer_logo}'
                    f'<span>{_pt}</span>'
                    f'</div></td>'
                )
                for _, mkey, mfmt, mdec in _peer_metrics:
                    _mv = pr.get(mkey, 0)
                    if mfmt == "%" and _mv:
                        _mstr = f'{_mv:.{mdec}%}'
                    elif mfmt == "x" and _mv:
                        _mstr = f'{_mv:.{mdec}f}x'
                    else:
                        _mstr = "—"
                    _ptable += (
                        f'<td style="text-align:right;padding:10px 12px;border-bottom:1px solid {T["border_light"]};color:{T["text"]};{_fw}">'
                        f'{_mstr}</td>'
                    )
                _ptable += '</tr>'
            _ptable += '</tbody></table></div>'
            st.markdown(_ptable, unsafe_allow_html=True)

            # ── Manage peers ──
            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
            _peer_tickers = [p.get("ticker", "") for p in peers]
            if _peer_tickers:
                # Key includes the current peer set so the widget re-initializes
                # when peers are added/removed externally (avoids stale selection
                # silently dropping just-added peers).
                _ms_key = "ed_peer_select_" + "_".join(sorted(_peer_tickers))
                _kept = st.multiselect(
                    "Remove peers (deselect to remove)",
                    options=_peer_tickers, default=_peer_tickers,
                    key=_ms_key,
                    help="Deselect a ticker to remove it from this peer group.",
                )
                if set(_kept) != set(_peer_tickers):
                    cfg['peers'] = [p for p in peers if p.get("ticker") in _kept]
                    save_config(_sb_client, ticker, cfg)
                    st.rerun()

            st.markdown(
                f'<style>'
                f'div[data-testid="stForm"] div[data-testid="stTextInput"] input {{'
                f'  background-color: {T["row_alt"]} !important;'
                f'  border: 1px solid {T["border_medium"]} !important;'
                f'}}'
                f'</style>',
                unsafe_allow_html=True,
            )
            with st.form("add_peer_form"):
                _ac1, _ac2 = st.columns([5, 1])
                with _ac1:
                    _new_peer = st.text_input(
                        "Add peer ticker",
                        key="ed_add_peer",
                        placeholder="e.g. MSFT, GOOG",
                    )
                with _ac2:
                    st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
                    _add_clicked = st.form_submit_button("+ Add", width="stretch")
            if _add_clicked and _new_peer:
                _new_tickers = [t for t in (sanitize_ticker(t) for t in _new_peer.split(",")) if t]
                if not _new_tickers:
                    st.warning("Invalid ticker(s). Use 1–5 letters only (e.g. MSFT, GOOG).")
                _existing = {p.get("ticker") for p in peers}
                _to_fetch = [t for t in _new_tickers if t not in _existing and t != ticker]
                if _to_fetch:
                    with st.spinner(f"Fetching data for {', '.join(_to_fetch)}..."):
                        _new_peers = fetch_peer_data(_to_fetch)
                    if _new_peers:
                        peers.extend(_new_peers)
                        cfg['peers'] = peers
                        save_config(_sb_client, ticker, cfg)
                        st.rerun()
                    else:
                        st.warning("Could not fetch peer data. Check the ticker(s).")

    with _tab_dividend:
        with st.container(key="tabcard_dividend"):
            st.markdown("#### Dividend Lens")

            # Locate the lens output in the stored summary.
            _summary = cfg.get("valuation_summary") or {}
            _lenses = _summary.get("lenses") or {}
            _div_lens = _lenses.get("dividend")
            _inputs = cfg.get("valuation_inputs") or {}
            _ttm = _inputs.get("ttm_dividend") or 0.0
            _price = cfg.get("stock_price") or 0.0

            # ── Edge case: no stored summary at all ────────────────────
            if not _summary:
                st.info(
                    "Run **Refresh All** on the watchlist (or call "
                    "`calculate_multi_lens_valuation` via the MCP) to compute "
                    "the Dividend lens for this ticker first."
                )

            # ── Edge case: non-payer (lens skipped due to ttm=0) ───────
            elif _ttm <= 0:
                st.info(
                    f"**{ticker}** doesn't pay dividends — Dividend lens not "
                    f"applicable. Use the `update_valuation_inputs` MCP tool "
                    f"to inject a target dividend if you want scenario analysis."
                )

            # ── Edge case: lens computed but skipped (e.g. <3y history) ─
            elif _div_lens is None:
                st.warning(
                    f"Dividend lens skipped for {ticker}. Likely reason: "
                    f"insufficient dividend history (need ≥3y) or "
                    f"`cost_of_equity ≤ terminal_growth`. Re-run Refresh All "
                    f"after adjusting inputs."
                )

            else:
                _details = _div_lens.get("details") or {}
                _baseline_g = _details.get("growth_rate_stage1") or 0.0
                _baseline_ke = _details.get("cost_of_equity") or 0.0
                _g_term_used = _details.get("terminal_growth") or 0.025
                _stage1_years = _details.get("stage1_years") or 5
                _ddm_fv = _details.get("ddm_fv") or 0.0
                _yield_mr_fv = _details.get("yield_mr_fv")
                _median_yield = _details.get("median_5y_yield")

                if _baseline_ke <= _g_term_used:
                    st.warning(
                        f"Cost of equity ({_baseline_ke:.2%}) ≤ terminal "
                        f"growth ({_g_term_used:.2%}) — DDM formula doesn't "
                        f"converge for these assumptions. Adjust the DCF "
                        f"editor's risk-free rate, ERP, or terminal growth."
                    )
                else:
                    _div_g_range = (0.0, 0.12, 0.01)
                    _div_ke_range = (
                        max(0.0, _baseline_ke - 0.02),
                        _baseline_ke + 0.02,
                        0.005,
                    )

                    with st.expander("Adjust ranges"):
                        _dc_e1, _dc_e2 = st.columns(2)
                        with _dc_e1:
                            st.markdown("**Growth rate (g₁)**")
                            _dg_min = st.number_input(
                                "Min %", value=0.0,
                                step=1.0, format="%.0f",
                                key="div_gmin",
                            ) / 100
                            _dg_max = st.number_input(
                                "Max %", value=12.0,
                                step=1.0, format="%.0f",
                                key="div_gmax",
                            ) / 100
                            _dg_step = st.number_input(
                                "Step %", value=1.0,
                                step=0.5, format="%.1f",
                                key="div_gstep",
                            ) / 100
                            if _dg_step > 0 and _dg_max > _dg_min:
                                _div_g_range = (_dg_min, _dg_max, _dg_step)
                        with _dc_e2:
                            st.markdown("**Cost of equity (ke)**")
                            _dke_min = st.number_input(
                                "Min %", value=max(0.0, _baseline_ke * 100 - 2),
                                step=0.5, format="%.1f",
                                key="div_kemin",
                            ) / 100
                            _dke_max = st.number_input(
                                "Max %", value=_baseline_ke * 100 + 2,
                                step=0.5, format="%.1f",
                                key="div_kemax",
                            ) / 100
                            _dke_step = st.number_input(
                                "Step %", value=0.5,
                                step=0.1, format="%.1f",
                                key="div_kestep",
                            ) / 100
                            if _dke_step > 0 and _dke_max > _dke_min:
                                _div_ke_range = (_dke_min, _dke_max, _dke_step)

                    _card_border = (
                        f'border-top:1px solid {T["border_medium"]};'
                        f'border-right:1px solid {T["border_medium"]};'
                        f'border-bottom:1px solid {T["border_medium"]};'
                        f'border-left:3px solid {T["accent"]}'
                    )

                    _dc1, _dc2 = st.columns(2)
                    with _dc1:
                        st.markdown(
                            f'<div style="{_card_border};border-radius:12px;'
                            f'padding:20px;text-align:center;'
                            f'background:{T["card"]};box-shadow:{T["shadow"]}">'
                            f'<div style="color:{T["text_muted"]};font-size:0.75rem;'
                            f'text-transform:uppercase;letter-spacing:0.05em;'
                            f'font-weight:600">DDM Fair Value</div>'
                            f'<div style="font-size:1.8rem;font-weight:700;'
                            f'margin:8px 0;color:{T["text"]}">{_fmt_fv_dollar(_ddm_fv)}</div>'
                            f'<div style="color:{T["text_muted"]};font-size:0.85rem">'
                            f'{_baseline_g:.1%} growth · ke {_baseline_ke:.1%} · '
                            f'terminal {_g_term_used:.1%}</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    with _dc2:
                        if _yield_mr_fv is not None and _median_yield is not None:
                            _y_card_body = (
                                f'<div style="font-size:1.8rem;font-weight:700;'
                                f'margin:8px 0;color:{T["text"]}">'
                                f'{_fmt_fv_dollar(_yield_mr_fv)}</div>'
                                f'<div style="color:{T["text_muted"]};'
                                f'font-size:0.85rem">'
                                f'${_ttm:.2f} TTM / '
                                f'{_median_yield:.2%} historic median yield</div>'
                            )
                        else:
                            _y_card_body = (
                                f'<div style="font-size:1.4rem;font-weight:700;'
                                f'margin:8px 0;color:{T["text_muted"]}">'
                                f'Insufficient history</div>'
                                f'<div style="color:{T["text_muted"]};'
                                f'font-size:0.85rem">Needs ≥3y of dividend data</div>'
                            )
                        st.markdown(
                            f'<div style="{_card_border};border-radius:12px;'
                            f'padding:20px;text-align:center;'
                            f'background:{T["card"]};box-shadow:{T["shadow"]}">'
                            f'<div style="color:{T["text_muted"]};font-size:0.75rem;'
                            f'text-transform:uppercase;letter-spacing:0.05em;'
                            f'font-weight:600">Yield Mean-Reversion</div>'
                            f'{_y_card_body}'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    if _yield_mr_fv is not None:
                        _lens_mid = (_ddm_fv + _yield_mr_fv) / 2.0
                    else:
                        _lens_mid = _ddm_fv
                    _conclusion = _dividend_conclusion(
                        lens_mid=_lens_mid, price=_price
                    )
                    st.markdown(
                        f'<div style="color:{T["text_muted"]};font-size:0.85rem;'
                        f'text-align:center;margin:12px 0 16px">{_conclusion}</div>',
                        unsafe_allow_html=True,
                    )

                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:6px;'
                        f'flex-wrap:wrap;margin-bottom:8px">'
                        f'<span style="font-weight:700">Sensitivity Matrix</span>'
                        f'<span style="color:{T["text_muted"]}">— ke: '
                        f'{_baseline_ke:.2%} | Market: ${_price:.2f}</span>'
                        f'<span class="dvd-tip" style="position:relative;'
                        f'cursor:help;display:inline-flex;align-items:center">'
                        f'<svg width="15" height="15" viewBox="0 0 16 16" '
                        f'fill="none" style="opacity:0.4;vertical-align:middle">'
                        f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" '
                        f'stroke-width="1.5"/>'
                        f'<text x="8" y="11.5" text-anchor="middle" font-size="10" '
                        f'font-weight="600" fill="{T["text_muted"]}">?</text>'
                        f'</svg>'
                        f'<span style="visibility:hidden;opacity:0;position:absolute;'
                        f'left:22px;top:-8px;background:{T["card"]};color:{T["text"]};'
                        f'border:1px solid {T["border_medium"]};border-radius:8px;'
                        f'padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                        f'font-weight:400;width:280px;z-index:999;'
                        f'box-shadow:{T["shadow_hover"]};pointer-events:none;'
                        f'transition:opacity 0.15s ease">'
                        f'<b>g</b> = aanname voor toekomstige dividendgroei '
                        f'(jouw input — rijen).<br><br>'
                        f'<b>ke</b> = cost of equity, rendementseis van '
                        f'aandeelhouders. Automatisch berekend via CAPM: '
                        f'risicovrije rente + beta × equity risk premium '
                        f'(kolommen).'
                        f'</span></span></div>'
                        f'<style>.dvd-tip:hover > span:last-child'
                        f'{{visibility:visible!important;opacity:1!important}}</style>',
                        unsafe_allow_html=True,
                    )
                    _matrix_html = _render_dividend_sensitivity_matrix(
                        ttm=_ttm,
                        g_range=_div_g_range,
                        ke_range=_div_ke_range,
                        g_term=_g_term_used,
                        stage1_years=_stage1_years,
                        price=_price,
                        theme=T,
                    )
                    st.markdown(_matrix_html, unsafe_allow_html=True)

                    st.markdown(
                        f'<div style="display:flex;gap:20px;font-size:0.8rem;'
                        f'color:{T["text_muted"]};margin-top:4px">'
                        f'<span><span style="display:inline-block;width:12px;'
                        f'height:12px;background:{T["accent"]};border-radius:2px;'
                        f'vertical-align:middle;margin-right:4px"></span>'
                        f'Market-implied</span>'
                        f'<span><span style="display:inline-block;width:12px;'
                        f'height:12px;background:{T["accent_fill"]};'
                        f'border:1px solid {T["accent"]};border-radius:2px;'
                        f'vertical-align:middle;margin-right:4px"></span>'
                        f'Undervalued</span>'
                        f'<span><span style="display:inline-block;width:12px;'
                        f'height:12px;background:{T["red_light"]};'
                        f'border:1px solid {T["red"]};border-radius:2px;'
                        f'vertical-align:middle;margin-right:4px"></span>'
                        f'Overvalued</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    with _tab_fundamentals:
        st.markdown("#### Fundamentals")

        if _fund_error is not None:
            raise _fund_error
        _yrs = fund['years']
        _n = len(_yrs)

        # Chart style constants
        _COLORS = {
            'primary': T['accent'],
            'secondary': T['red'],
            'accent': '#3d405b',
            'tertiary': '#f2cc8f',
            'text_muted': T['text_muted'],
        }

        def _base_layout(fig, height=280):
            fig.update_layout(
                margin=dict(t=10, b=20, l=50, r=20),
                height=height,
                font=dict(
                    family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                    color=T['chart_font'],
                ),
                paper_bgcolor=T['chart_paper'],
                plot_bgcolor=T['chart_plot'],
                xaxis=dict(gridcolor=T['chart_grid'], dtick=1),
                yaxis=dict(gridcolor=T['chart_grid']),
                legend=dict(
                    orientation="h", yanchor="top", y=-0.15,
                    xanchor="center", x=0.5, font=dict(size=11),
                ),
                hovermode="x unified",
            )
            return fig

        def _pct_growth(values):
            result = [None]
            for i in range(1, len(values)):
                prev, curr = values[i - 1], values[i]
                if prev and prev != 0 and curr is not None:
                    result.append((curr / prev) - 1)
                else:
                    result.append(None)
            return result

        st.markdown(
            '<style>'
            f'[class*="st-key-fund_sec_"] {{'
            f'  background: {T["card"]} !important;'
            f'  border-top: 3px solid {T["accent"]} !important;'
            f'  border-radius: 24px !important; box-shadow: {T["shadow"]} !important;'
            f'  padding: 18px 28px 22px 28px !important; margin-bottom: 22px !important; }}'
            '</style>',
            unsafe_allow_html=True,
        )
        with st.container(key="fund_sec_0_roce"):
            # ── ROCE / ROE (float businesses) ──
            _fund_metric, _ = compute_roce_metric(fund, cfg)
            _roce_override = cfg.get('roce_metric_override') == 'ROE'
            if _fund_metric == 'ROE':
                _roce_tip = (
                    'Net Income / Total Equity — return on shareholders’ equity.<br><br>'
                    '<b>&gt;15%</b> solide<br>'
                    '<b>&gt;20%</b> kwaliteitsbar voor float-bedrijven<br>'
                    '<b>&lt;10%</b> zwak<br><br>'
                    'Gebruikt voor echte float-bedrijven (banken, verzekeraars, '
                    'settlement) waar capital employed te klein is voor zinvolle ROCE.'
                )
            else:
                _roce_tip = (
                    'EBIT / Capital Employed — pre-tax return on capital tied up in the operating business.<br><br>'
                    '<b>&gt;Discount rate</b> creates value<br>'
                    '<b>&gt;20%</b> Prasad/PE-screen quality bar — sustained 5+ jaar duidt op moat<br>'
                    '<b>&lt;Discount rate</b> destroys value<br><br>'
                    'Capital Employed = Total Assets − Current Liabilities − cash − kortlopende beleggingen.<br>'
                    'Goodwill blijft er <b>in</b> — zo meet je of overnames hun kapitaal terugverdienen.<br>'
                    'Cash gaat eruit omdat EBIT vóór het financieel resultaat staat: de rente op die '
                    'kaspositie zit niet in de teller, dus hoort de kas niet in de noemer. '
                    'Kortlopende beleggingen tellen als cash — daar staat de oorlogskas meestal.<br>'
                    f'Gemiddelde over de laatste {ROCE_WINDOW_YEARS} jaar, per jaar gemaximeerd op {int(ROCE_CEILING)}%.<br>'
                    'De float/ROE-fallback toetst op de onaangepaste (TA−CL)/TA, zodat een cash-berg '
                    'geen float-bedrijf van je maakt.<br>'
                    'PE-conventie zoals Nalanda Capital, gebruikt EBIT (pre-tax) ipv NOPAT.'
                )
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">{_fund_metric}</span>'
                f'<span class="roce-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:260px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'{_roce_tip}'
                f'</span></span></div>'
                f'<style>.roce-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            # Float-business flag — forces ROE (Net Income / Equity) instead of
            # ROCE. For genuine float businesses (banks, insurers, settlement
            # networks) where capital employed is too small for ROCE to mean
            # anything. The auto-detector (avg CE/TA < 25%) already catches
            # extreme cases; this is the manual override for the rest.
            _float_flag = st.toggle(
                "Float-bedrijf (toon ROE i.p.v. ROCE)",
                value=_roce_override,
                key=f"roce_float_{ticker}",
                help="Forceert ROE = Net Income / Equity. Alleen aanzetten voor "
                     "echte float-bedrijven (banken, verzekeraars, pure "
                     "betaalverwerkers). Auto-detectie pakt extreme gevallen "
                     "(gem. CE/TA < 25%) al zelf op.",
            )
            if _float_flag != _roce_override:
                if _float_flag:
                    cfg['roce_metric_override'] = 'ROE'
                else:
                    cfg.pop('roce_metric_override', None)
                save_config(_sb_client, ticker, cfg)
                st.rerun()
            if _n >= 3:
                # Numerator/denominator follow the chosen metric: ROCE uses
                # EBIT / (Total Assets − Current Liabilities); ROE uses
                # Net Income / Total Equity.
                if _fund_metric == 'ROE':
                    _num_tbl = fund.get('net_income') or []
                    _den_src = fund.get('total_equity') or []
                    _num_label, _den_label = 'Net Income', 'Equity'
                else:
                    _num_tbl = fund.get('operating_income') or []
                    _den_src = None
                    _num_label, _den_label = 'EBIT', 'Capital Employed'
                # Same trailing window the pill and the watchlist average over.
                # This tab fetches 11 years for its Key Ratios tables; showing
                # all of them here would put a different average on the chart
                # than on the hero pill for the same ticker.
                _roce_start = window_start(fund)
                _roce_yrs = _yrs[_roce_start:]
                roce_vals = []
                _ebit_tbl = []
                _ce_tbl = []
                for i in range(_roce_start, _n):
                    _num = _num_tbl[i] if i < len(_num_tbl) else None
                    if _fund_metric == 'ROE':
                        _den = _den_src[i] if i < len(_den_src) else None
                        _rv = _num / _den * 100 if _num is not None and _den and _den > 0 else None
                    else:
                        # Excess-liquidity-adjusted CE + ceiling cap — same basis
                        # as compute_roce_metric / the hero pill / watchlist.
                        _den = capital_employed(fund, i)
                        _rv = roce_for_year(fund, i)[0]
                    _ebit_tbl.append(_num)
                    _ce_tbl.append(_den if _den and _den != 0 else None)
                    roce_vals.append(_rv)

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_roce_yrs, y=roce_vals, name=_fund_metric,
                    line=dict(color=_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>' + _fund_metric + '</extra>',
                ))
                wacc_pct = val.get('wacc', 0) * 100
                if wacc_pct > 0:
                    fig.add_hline(
                        y=wacc_pct, line_dash="dash",
                        line_color=_COLORS['secondary'],
                        annotation_text=f"Hurdle rate {wacc_pct:.1f}%",
                        annotation_position="top right",
                    )
                # Prasad/PE screen bar at 20% — sustained 20%+ ROCE over 5+
                # years is the threshold for durable competitive advantage.
                fig.add_hline(
                    y=20, line_dash="dash", line_color=_COLORS['primary'],
                    annotation_text="20% (Prasad)",
                    annotation_position="bottom right",
                )
                # Historic average — single most useful summary stat for
                # Prasad-style persistence check (level + stability).
                _roce_valid_chart = [v for v in roce_vals if v is not None]
                if _roce_valid_chart:
                    _roce_chart_avg = sum(_roce_valid_chart) / len(_roce_valid_chart)
                    fig.add_hline(
                        y=_roce_chart_avg, line_dash="dot",
                        line_color=_COLORS['text_muted'],
                        annotation_text=f"Avg {_roce_chart_avg:.1f}%",
                        annotation_position="top left",
                    )
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")

                with st.expander("Details", expanded=False):
                    _rce_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _rce_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _rce_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _rce_avg = f'{_rce_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _rce_div = f'border-top:3px solid {T["text"]}'
                    _rce_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_rce_hdr};text-align:left"></th>'
                    )
                    for yr in _roce_yrs:
                        _rce_html += f'<th style="{_rce_hdr}">{yr}</th>'
                    _rce_html += f'<th style="{_rce_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _rce_html += '</tr></thead><tbody>'

                    # EBIT row
                    _eb_valid = [v for v in _ebit_tbl if v is not None]
                    _eb_avg = sum(_eb_valid) / len(_eb_valid) if _eb_valid else None
                    _rce_html += f'<tr><td style="{_rce_label}">{_num_label}</td>'
                    for v in _ebit_tbl:
                        _rce_html += f'<td style="{_rce_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rce_cell}">—</td>'
                    _rce_html += f'<td style="{_rce_avg}">{_eb_avg:,.0f}</td>' if _eb_avg is not None else f'<td style="{_rce_avg}">—</td>'
                    _rce_html += '</tr>'

                    # Capital Employed row
                    _ce_valid = [v for v in _ce_tbl if v is not None]
                    _ce_avg = sum(_ce_valid) / len(_ce_valid) if _ce_valid else None
                    _rce_html += f'<tr><td style="{_rce_label}">{_den_label}</td>'
                    for v in _ce_tbl:
                        _rce_html += f'<td style="{_rce_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rce_cell}">—</td>'
                    _rce_html += f'<td style="{_rce_avg}">{_ce_avg:,.0f}</td>' if _ce_avg is not None else f'<td style="{_rce_avg}">—</td>'
                    _rce_html += '</tr>'

                    # ROCE % row — thick top border
                    _roce_valid = [v for v in roce_vals if v is not None]
                    _roce_avg = sum(_roce_valid) / len(_roce_valid) if _roce_valid else None
                    _rce_html += f'<tr><td style="{_rce_label};{_rce_div}">{_fund_metric}</td>'
                    for v in roce_vals:
                        if v is not None:
                            # 20%+ is Prasad-screen kwaliteitsbar
                            _r_color = T['accent'] if v >= 20 else (T['red'] if v < wacc_pct else T['text'])
                            _rce_html += f'<td style="{_rce_cell};{_rce_div};color:{_r_color};font-weight:600">{v:.1f}%</td>'
                        else:
                            _rce_html += f'<td style="{_rce_cell};{_rce_div}">—</td>'
                    if _roce_avg is not None:
                        _ra_color = T['accent'] if _roce_avg >= 20 else (T['red'] if _roce_avg < wacc_pct else T['text'])
                        _rce_html += f'<td style="{_rce_avg};{_rce_div};color:{_ra_color}">{_roce_avg:.1f}%</td>'
                    else:
                        _rce_html += f'<td style="{_rce_avg};{_rce_div}">—</td>'
                    _rce_html += '</tr>'

                    _rce_html += '</tbody></table></div>'
                    st.markdown(_rce_html, unsafe_allow_html=True)
                    if _fund_metric == 'ROE':
                        st.caption("In $M. ROE = Net Income / Total Equity (float-business weergave).")
                    else:
                        st.caption(
                            "In $M. EBIT = Operating Income (proxy). Capital Employed = "
                            "Total Assets − Current Liabilities − cash − kortlopende "
                            "beleggingen. Goodwill blijft erin, cash niet. Laatste "
                            f"{ROCE_WINDOW_YEARS} jaar, per jaar gemaximeerd op "
                            f"{int(ROCE_CEILING)}%."
                        )
            else:
                st.info(f"Insufficient data for {_fund_metric} (need 3+ years)")

        with st.container(key="fund_sec_1_fcf_yield"):
            # ── FCF Yield ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">FCF Yield</span>'
                f'<span class="fy-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'(FCF per Share / Price) × 100.<br>'
                f'Cash return percentage on your investment.<br><br>'
                f'<b>&gt;5%</b> attractively priced<br>'
                f'<b>3–5%</b> redelijk<br>'
                f'<b>&lt;1%</b> expensive or low cash generation'
                f'</span></span></div>'
                f'<style>.fy-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            if _n >= 2 and live_price > 0:
                # Prefer per-share path when shares are available; otherwise fall
                # back to FCF / Market Cap using the current market cap from cfg
                # (e.g. V doesn't report shares in XBRL).
                _mcap_total = cfg.get('equity_market_value', 0) or 0  # $M
                fcf_yield = []
                _fcf_ps = []
                for i in range(_n):
                    sh = fund['shares'][i]
                    if sh and sh > 0 and fund['fcf'][i] is not None:
                        fps = fund['fcf'][i] * 1e6 / sh
                        _fcf_ps.append(fps)
                        fcf_yield.append(fps / live_price * 100)
                    elif fund['fcf'][i] is not None and _mcap_total > 0:
                        _fcf_ps.append(None)
                        fcf_yield.append(fund['fcf'][i] / _mcap_total * 100)
                    else:
                        _fcf_ps.append(None)
                        fcf_yield.append(None)

                current_fy = fcf_yield[-1] if fcf_yield[-1] is not None else 0
                fy_color = T['accent'] if current_fy > 3 else (T['red'] if current_fy < 1 else T['text'])
                st.markdown(
                    f'<div style="text-align:center;padding:8px 0">'
                    f'<span style="font-size:2rem;font-weight:700;color:{fy_color}">{current_fy:.1f}%</span>'
                    f'<span style="color:{T["text_muted"]};font-size:0.9rem;margin-left:8px">current FCF Yield</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=fcf_yield, name='FCF Yield',
                    line=dict(color=_COLORS['primary'], width=2.5),
                    fill='tozeroy', fillcolor=T['accent_fill'],
                    hovertemplate='%{y:.1f}%<extra>FCF Yield</extra>',
                ))
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig, height=250)
                st.plotly_chart(fig, width="stretch")

                with st.expander("Details", expanded=False):
                    _fy_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _fy_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _fy_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _fy_avg_s = f'{_fy_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _fy_div = f'border-top:3px solid {T["text"]}'
                    _fy_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_fy_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs:
                        _fy_html += f'<th style="{_fy_hdr}">{yr}</th>'
                    _fy_html += f'<th style="{_fy_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _fy_html += '</tr></thead><tbody>'

                    # FCF/Share row
                    _fps_valid = [v for v in _fcf_ps if v is not None]
                    _fps_avg = sum(_fps_valid) / len(_fps_valid) if _fps_valid else None
                    _fy_html += f'<tr><td style="{_fy_label}">FCF / Share</td>'
                    for v in _fcf_ps:
                        _fy_html += f'<td style="{_fy_cell}">${v:,.2f}</td>' if v is not None else f'<td style="{_fy_cell}">—</td>'
                    _fy_html += f'<td style="{_fy_avg_s}">${_fps_avg:,.2f}</td>' if _fps_avg is not None else f'<td style="{_fy_avg_s}">—</td>'
                    _fy_html += '</tr>'

                    # Price row
                    _fy_html += f'<tr><td style="{_fy_label}">Price</td>'
                    for _ in _yrs:
                        _fy_html += f'<td style="{_fy_cell}">${live_price:,.2f}</td>'
                    _fy_html += f'<td style="{_fy_avg_s}">${live_price:,.2f}</td>'
                    _fy_html += '</tr>'

                    # Yield row — thick border
                    _fyl_valid = [v for v in fcf_yield if v is not None]
                    _fyl_avg = sum(_fyl_valid) / len(_fyl_valid) if _fyl_valid else None
                    _fy_html += f'<tr><td style="{_fy_label};{_fy_div}">FCF Yield</td>'
                    for v in fcf_yield:
                        if v is not None:
                            _y_color = T['accent'] if v > 3 else (T['red'] if v < 1 else T['text'])
                            _fy_html += f'<td style="{_fy_cell};{_fy_div};color:{_y_color};font-weight:600">{v:.1f}%</td>'
                        else:
                            _fy_html += f'<td style="{_fy_cell};{_fy_div}">—</td>'
                    if _fyl_avg is not None:
                        _ya_color = T['accent'] if _fyl_avg > 3 else (T['red'] if _fyl_avg < 1 else T['text'])
                        _fy_html += f'<td style="{_fy_avg_s};{_fy_div};color:{_ya_color}">{_fyl_avg:.1f}%</td>'
                    else:
                        _fy_html += f'<td style="{_fy_avg_s};{_fy_div}">—</td>'
                    _fy_html += '</tr>'

                    _fy_html += '</tbody></table></div>'
                    st.markdown(_fy_html, unsafe_allow_html=True)
                    st.caption("FCF Yield = (FCF per Share / Price) × 100. Price = current price for all years.")
            else:
                st.info("Insufficient data for FCF Yield")

        with st.container(key="fund_sec_2_ebit_ev"):
            # ── EBIT / EV (Greenblatt Earnings Yield) ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">EBIT / EV</span>'
                f'<span class="ey-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:280px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'<b>Greenblatt Earnings Yield</b> — EBIT als percentage van Enterprise Value. Pre-tax, capital-structure-agnostic — paart met ROCE de "high quality at reasonable price"-screen uit de Magic Formula.<br><br>'
                f'EV = Market Cap + Total Debt − Cash.<br><br>'
                f'<b>&gt; 12%</b> goedkoop (hoge earnings yield)<br>'
                f'<b>8 − 12%</b> fairly valued<br>'
                f'<b>&lt; 6%</b> duur — alleen rechtvaardig bij hoge groei + sterke ROCE<br><br>'
                f'Onafhankelijk van rente en belasting → vergelijkbaar tussen sectoren en jurisdicties, dat is z\'n kracht boven P/E.'
                f'</span></span></div>'
                f'<style>.ey-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            if _n >= 2:
                # Fetch historical year-end prices to reconstruct EV per year
                @st.cache_data(ttl=86400, show_spinner=False)
                def _ey_historical_prices(t, years_tuple):
                    try:
                        return fetch_historical_prices(t, list(years_tuple))
                    except Exception:
                        return {}
                _ey_prices = _ey_historical_prices(ticker, tuple(_yrs))

                ey_vals = []
                _ey_ebit_tbl = []
                _ey_ev_tbl = []
                _ey_mcap_tbl = []
                for i in range(_n):
                    yr = _yrs[i]
                    oi = fund['operating_income'][i]
                    sh = fund['shares'][i] if i < len(fund['shares']) else None
                    debt_v = fund['total_debt'][i] if i < len(fund['total_debt']) else None
                    cash_v = fund['cash'][i] if i < len(fund['cash']) else 0
                    price_v = _ey_prices.get(yr)
                    # Year-end market cap = price × shares; fallback to current
                    # market cap × (shares[i] / shares[-1]) when historical price
                    # missing, then last resort: current market cap from cfg.
                    mcap_m = None
                    if price_v and sh and sh > 0:
                        mcap_m = price_v * sh / 1e6  # → $M
                    elif i == _n - 1:
                        # latest year — use current market cap if XBRL prices missing
                        mcap_m = cfg.get('equity_market_value', 0) or 0
                    _ey_mcap_tbl.append(mcap_m)
                    if mcap_m and debt_v is not None and oi is not None:
                        ev = mcap_m + debt_v - (cash_v or 0)
                        _ey_ebit_tbl.append(oi)
                        _ey_ev_tbl.append(ev)
                        ey_vals.append(oi / ev * 100 if ev > 0 else None)
                    else:
                        _ey_ebit_tbl.append(oi)
                        _ey_ev_tbl.append(None)
                        ey_vals.append(None)

                # Big number — current EBIT/EV (most recent valid year)
                current_ey = next((v for v in reversed(ey_vals) if v is not None), None)
                if current_ey is not None:
                    _ey_color = T['accent'] if current_ey > 12 else (T['red'] if current_ey < 6 else T['text'])
                    st.markdown(
                        f'<div style="text-align:center;padding:8px 0">'
                        f'<span style="font-size:2rem;font-weight:700;color:{_ey_color}">{current_ey:.1f}%</span>'
                        f'<span style="color:{T["text_muted"]};font-size:0.9rem;margin-left:8px">current EBIT / EV</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=ey_vals, name='EBIT / EV',
                    line=dict(color=_COLORS['primary'], width=2.5),
                    fill='tozeroy', fillcolor=T['accent_fill'],
                    hovertemplate='%{y:.1f}%<extra>EBIT/EV</extra>',
                ))
                fig.add_hline(y=12, line_dash="dash", line_color=_COLORS['accent'],
                              annotation_text="12% (cheap)", annotation_position="top right")
                fig.add_hline(y=6, line_dash="dot", line_color=_COLORS['secondary'],
                              annotation_text="6% (expensive)", annotation_position="bottom right")
                # Historic avg
                _ey_valid = [v for v in ey_vals if v is not None]
                if _ey_valid:
                    _ey_avg = sum(_ey_valid) / len(_ey_valid)
                    fig.add_hline(
                        y=_ey_avg, line_dash="dot",
                        line_color=_COLORS['text_muted'],
                        annotation_text=f"Avg {_ey_avg:.1f}%",
                        annotation_position="top left",
                    )
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")

                with st.expander("Details", expanded=False):
                    _ey_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _ey_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _ey_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _ey_avg_s = f'{_ey_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _ey_div = f'border-top:3px solid {T["text"]}'
                    _ey_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_ey_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs:
                        _ey_html += f'<th style="{_ey_hdr}">{yr}</th>'
                    _ey_html += f'<th style="{_ey_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _ey_html += '</tr></thead><tbody>'

                    def _ey_row(label, values, fmt='{:,.0f}', divider=False, color_fn=None):
                        nonlocal _ey_html
                        label_style = _ey_label + (';' + _ey_div if divider else '')
                        cell_base = _ey_cell + (';' + _ey_div if divider else '')
                        avg_style = _ey_avg_s + (';' + _ey_div if divider else '')
                        _ey_html += f'<tr><td style="{label_style}">{label}</td>'
                        for v in values:
                            if v is None:
                                _ey_html += f'<td style="{cell_base}">—</td>'
                            else:
                                extra = ''
                                if color_fn:
                                    c = color_fn(v)
                                    if c:
                                        extra = f';color:{c};font-weight:600'
                                _ey_html += f'<td style="{cell_base}{extra}">{fmt.format(v)}</td>'
                        _valid = [v for v in values if v is not None]
                        if _valid:
                            a = sum(_valid) / len(_valid)
                            extra = ''
                            if color_fn:
                                c = color_fn(a)
                                if c:
                                    extra = f';color:{c}'
                            _ey_html += f'<td style="{avg_style}{extra}">{fmt.format(a)}</td>'
                        else:
                            _ey_html += f'<td style="{avg_style}">—</td>'
                        _ey_html += '</tr>'

                    _ey_row("EBIT", _ey_ebit_tbl)
                    _ey_row("Market Cap", _ey_mcap_tbl)
                    _ey_row("Enterprise Value", _ey_ev_tbl)
                    _ey_row("EBIT / EV", ey_vals, fmt='{:.1f}%', divider=True,
                            color_fn=lambda v: T['accent'] if v > 12 else (T['red'] if v < 6 else T['text']))

                    _ey_html += '</tbody></table></div>'
                    st.markdown(_ey_html, unsafe_allow_html=True)
                    st.caption("In $M. EV = Market Cap (year-end price × shares) + Total Debt − Cash. EBIT = Operating Income. Greenblatt-style Earnings Yield.")
            else:
                st.info("Insufficient data for EBIT/EV")

        with st.container(key="fund_sec_3_op_leverage"):
            # ── Operating Leverage ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">Operating Leverage</span>'
                f'<span class="ol-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:260px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'DOL = OI Growth / Revenue Growth — measures how much operating income amplifies revenue growth.<br><br>'
                f'<b>&gt;1.0x</b> scale advantage (costs grow slower than revenue)<br>'
                f'<b>=1.0x</b> neutraal<br>'
                f'<b>&lt;1.0x</b> costs growing faster than revenue'
                f'</span></span></div>'
                f'<style>.ol-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            rev_g = _pct_growth(fund['revenue'])
            oi_g = _pct_growth(fund['operating_income'])
            if _n >= 3:
                # Chart: DOL (Degree of Operating Leverage)
                dol_values = []
                for r, o in zip(rev_g[1:], oi_g[1:]):
                    if r is not None and o is not None and r != 0:
                        dol_values.append(round(o / r, 2))
                    else:
                        dol_values.append(None)
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=dol_values,
                    name='DOL',
                    line=dict(color=_COLORS['accent'], width=2.5),
                    hovertemplate='%{y:.1f}x<extra>DOL</extra>',
                ))
                # Reference line at 1.0x
                fig.add_hline(y=1.0, line_dash="dot", line_color=_COLORS['text_muted'],
                              annotation_text="1.0x", annotation_position="right")
                fig.update_yaxes(ticksuffix='x')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")

                with st.expander("Details", expanded=False):
                    _tbl_border = f'border-top:1px solid {T["grid"]}'
                    _ol_cell = f'text-align:right;padding:6px 10px;font-size:0.85rem;color:{T["text"]};{_tbl_border}'
                    _ol_hdr = f'text-align:right;padding:6px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _ol_label = f'text-align:left;padding:6px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;{_tbl_border}'
                    _ol_avg_style = f'{_ol_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _ol_div = f'border-top:3px solid {T["text"]}'
                    _ol_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_ol_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs[1:]:
                        _ol_html += f'<th style="{_ol_hdr}">{yr}</th>'
                    _ol_html += f'<th style="{_ol_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _ol_html += '</tr></thead><tbody>'

                    # Revenue Growth row
                    _rev_vals = [rev_g[i] for i in range(1, _n) if rev_g[i] is not None]
                    _rev_avg = sum(_rev_vals) / len(_rev_vals) if _rev_vals else None
                    _ol_html += f'<tr><td style="{_ol_label}">Revenue Growth</td>'
                    for i in range(1, _n):
                        v = rev_g[i]
                        _ol_html += f'<td style="{_ol_cell}">{v*100:.1f}%</td>' if v is not None else f'<td style="{_ol_cell}">—</td>'
                    _ol_html += f'<td style="{_ol_avg_style}">{_rev_avg*100:.1f}%</td>' if _rev_avg is not None else f'<td style="{_ol_avg_style}">—</td>'
                    _ol_html += '</tr>'

                    # OI Growth row — green when OI growth > Rev growth, red when below
                    _oi_vals = [oi_g[i] for i in range(1, _n) if oi_g[i] is not None]
                    _oi_avg = sum(_oi_vals) / len(_oi_vals) if _oi_vals else None
                    _ol_html += f'<tr><td style="{_ol_label}">OI Growth</td>'
                    for i in range(1, _n):
                        r, o = rev_g[i], oi_g[i]
                        if o is not None:
                            if r is not None and o > r:
                                color = T['accent']
                            elif r is not None and o < r:
                                color = T['red']
                            else:
                                color = T['text']
                            weight = 'font-weight:600;' if color != T['text'] else ''
                            _ol_html += f'<td style="{_ol_cell};color:{color};{weight}">{o*100:.1f}%</td>'
                        else:
                            _ol_html += f'<td style="{_ol_cell}">—</td>'
                    if _oi_avg is not None and _rev_avg is not None:
                        _oi_avg_color = T['accent'] if _oi_avg > _rev_avg else T['red']
                        _ol_html += f'<td style="{_ol_avg_style};color:{_oi_avg_color}">{_oi_avg*100:.1f}%</td>'
                    else:
                        _ol_html += f'<td style="{_ol_avg_style}">—</td>'
                    _ol_html += '</tr>'

                    # DOL row — thick top border
                    _dol_vals = []
                    for i in range(1, _n):
                        r, o = rev_g[i], oi_g[i]
                        if r and o and r != 0:
                            _dol_vals.append(o / r)
                    _dol_avg = sum(_dol_vals) / len(_dol_vals) if _dol_vals else None
                    _ol_html += f'<tr><td style="{_ol_label};{_ol_div}">DOL</td>'
                    for i in range(1, _n):
                        r, o = rev_g[i], oi_g[i]
                        if r and o and r != 0:
                            dol = o / r
                            color = T['accent'] if dol > 1 else T['red']
                            _ol_html += f'<td style="{_ol_cell};{_ol_div};color:{color};font-weight:600">{dol:.1f}x</td>'
                        else:
                            _ol_html += f'<td style="{_ol_cell};{_ol_div}">—</td>'
                    if _dol_avg is not None:
                        _dol_avg_color = T['accent'] if _dol_avg > 1 else T['red']
                        _ol_html += f'<td style="{_ol_avg_style};{_ol_div};color:{_dol_avg_color}">{_dol_avg:.1f}x</td>'
                    else:
                        _ol_html += f'<td style="{_ol_avg_style};{_ol_div}">—</td>'
                    _ol_html += '</tr>'

                    _ol_html += '</tbody></table></div>'
                    st.markdown(_ol_html, unsafe_allow_html=True)
                    st.caption("DOL > 1 = each % revenue growth translates into more than 1% earnings growth (scale advantage)")
            else:
                st.info("Insufficient data for Operating Leverage (need 3+ years)")

        with st.container(key="fund_sec_4_margins"):
            # ── Margins ──
            st.markdown("")
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">Margins</span>'
                f'<span class="mg-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'Gross = (Revenue − COGS) / Revenue<br>'
                f'Operating = OI / Revenue<br>'
                f'FCF = Free Cash Flow / Revenue<br><br>'
                f'Rising margins = pricing power and economies of scale.'
                f'</span></span></div>'
                f'<style>.mg-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            if _n >= 3:
                rev = fund['revenue']
                _gp = fund.get('gross_profit') or [None] * _n
                _cor = fund.get('cost_of_revenue') or [None] * _n
                gross_m = []
                for i in range(_n):
                    if not rev[i]:
                        gross_m.append(None)
                    elif _gp[i] is not None:
                        gross_m.append(_gp[i] / rev[i] * 100)
                    elif _cor[i] is not None:
                        gross_m.append((rev[i] - _cor[i]) / rev[i] * 100)
                    else:
                        gross_m.append(None)
                op_m = [fund['operating_income'][i] / rev[i] * 100
                        if rev[i] and fund['operating_income'][i] is not None else None
                        for i in range(_n)]
                fcf_m = [fund['fcf'][i] / rev[i] * 100
                         if rev[i] and fund['fcf'][i] is not None else None
                         for i in range(_n)]
                # Detect when gross == operating (happens for financials like V
                # where cost_of_revenue falls back to CostsAndExpenses, which
                # already includes all operating costs). In that case the gross
                # line carries no extra info and just obscures the operating line.
                _gross_dup = (
                    any(v is not None for v in gross_m)
                    and all(
                        (g is None and o is None)
                        or (g is not None and o is not None and abs(g - o) < 0.5)
                        for g, o in zip(gross_m, op_m)
                    )
                )
                _series = [
                    ('Operating', op_m, _COLORS['accent']),
                    ('FCF', fcf_m, _COLORS['tertiary']),
                ]
                if not _gross_dup:
                    _series.insert(0, ('Gross', gross_m, _COLORS['primary']))
                fig = go.Figure()
                for name, vals, color in _series:
                    fig.add_trace(go.Scatter(
                        x=_yrs, y=vals, name=name,
                        line=dict(color=color, width=2.5),
                        hovertemplate='%{y:.1f}%<extra>' + name + ' Margin</extra>',
                    ))
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")
                if _gross_dup:
                    st.caption("Gross margin weggelaten: dit bedrijf rapporteert geen aparte COGS, waardoor gross gelijk is aan operating margin.")

                with st.expander("Details", expanded=False):
                    _m_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _m_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _m_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _m_avg_style = f'{_m_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _m_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_m_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs:
                        _m_html += f'<th style="{_m_hdr}">{yr}</th>'
                    _m_html += f'<th style="{_m_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _m_html += '</tr></thead><tbody>'

                    for label, vals in [('Gross', gross_m), ('Operating', op_m), ('FCF', fcf_m)]:
                        _valid = [v for v in vals if v is not None]
                        _avg = sum(_valid) / len(_valid) if _valid else None
                        _m_html += f'<tr style="border-top:1px solid {T["grid"]}"><td style="{_m_label}">{label}</td>'
                        for v in vals:
                            _m_html += f'<td style="{_m_cell}">{v:.1f}%</td>' if v is not None else f'<td style="{_m_cell}">—</td>'
                        _m_html += f'<td style="{_m_avg_style}">{_avg:.1f}%</td>' if _avg is not None else f'<td style="{_m_avg_style}">—</td>'
                        _m_html += '</tr>'

                    # Operating Margin delta row — expanding margin = operating leverage
                    _m_div = f'border-top:3px solid {T["text"]}'
                    _m_html += f'<tr><td style="{_m_label};{_m_div}">Op Margin \u0394</td>'
                    _delta_vals = []
                    for i in range(_n):
                        if i == 0:
                            _m_html += f'<td style="{_m_cell};{_m_div}">—</td>'
                        elif op_m[i] is not None and op_m[i - 1] is not None:
                            d = op_m[i] - op_m[i - 1]
                            _delta_vals.append(d)
                            color = T['accent'] if d > 0 else T['red']
                            sign = '+' if d > 0 else ''
                            _m_html += f'<td style="{_m_cell};{_m_div};color:{color};font-weight:600">{sign}{d:.1f}pp</td>'
                        else:
                            _m_html += f'<td style="{_m_cell};{_m_div}">—</td>'
                    _d_avg = sum(_delta_vals) / len(_delta_vals) if _delta_vals else None
                    if _d_avg is not None:
                        d_color = T['accent'] if _d_avg > 0 else T['red']
                        d_sign = '+' if _d_avg > 0 else ''
                        _m_html += f'<td style="{_m_avg_style};{_m_div};color:{d_color}">{d_sign}{_d_avg:.1f}pp</td>'
                    else:
                        _m_html += f'<td style="{_m_avg_style};{_m_div}">—</td>'
                    _m_html += '</tr>'

                    _m_html += '</tbody></table></div>'
                    st.markdown(_m_html, unsafe_allow_html=True)
                    st.caption("Op Margin \u0394 > 0 with growing revenue = operating leverage (cost scale advantage)")
            else:
                st.info("Insufficient data for Margins (need 3+ years)")

        with st.container(key="fund_sec_5_net_debt"):
            # ── Net Debt ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">Net Debt</span>'
                f'<span class="nd-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:260px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'Adjusted Debt − Cash. Moody&apos;s/S&amp;P-stijl voor credit-analyse.<br><br>'
                f'<b>Adjusted Debt</b> = LT Debt + ST Debt + Operating Leases + Finance Leases + Pension Underfunding. Pakt alle debt-like obligations mee.<br><br>'
                f'<b style="color:{T["red"]}">Rood</b> (positief) = netto debiteur<br>'
                f'<b style="color:{T["accent"]}">Groen</b> (negatief) = netto cash, geen credit-risk<br><br>'
                f'<b>0 − 1.5×</b> EBITDA: gezond<br>'
                f'<b>1.5 − 3×</b> EBITDA: acceptabel<br>'
                f'<b>3 − 4×</b> EBITDA: opgerekt<br>'
                f'<b>&gt; 4×</b> EBITDA: distress-risk<br><br>'
                f'EBITDA = OI + D&amp;A (proxy). Lijnkleur volgt het meest recente jaar; per-jaar markers tonen historische sign-flips.'
                f'</span></span></div>'
                f'<style>.nd-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            if _n >= 3:
                nd_vals = []
                nd_ebitda_vals = []
                _lt_debt_tbl = []
                _st_debt_tbl = []
                _op_lease_tbl = []
                _fin_lease_tbl = []
                _pension_tbl = []
                _adj_debt_tbl = []
                _cash_tbl = []
                _ebitda_tbl = []
                for i in range(_n):
                    lt_debt = fund['total_debt'][i]
                    st_debt = fund.get('short_term_debt', [None]*_n)[i]
                    op_lease = fund.get('operating_lease_liabilities', [None]*_n)[i]
                    fin_lease = fund.get('finance_lease_liabilities', [None]*_n)[i]
                    pension = fund.get('pension_liabilities', [None]*_n)[i]
                    cash_i = fund['cash'][i]
                    oi = fund['operating_income'][i]
                    da = fund['da'][i] if 'da' in fund else None
                    ebitda = (oi + (da or 0)) if oi is not None else None

                    _lt_debt_tbl.append(lt_debt)
                    _st_debt_tbl.append(st_debt)
                    _op_lease_tbl.append(op_lease)
                    _fin_lease_tbl.append(fin_lease)
                    _pension_tbl.append(pension)
                    _cash_tbl.append(cash_i)
                    _ebitda_tbl.append(ebitda)

                    # Sum all debt-like obligations (None treated as 0).
                    # Adjusted debt is None only if LT debt itself is missing.
                    if lt_debt is None:
                        _adj_debt_tbl.append(None)
                        nd_vals.append(None)
                        nd_ebitda_vals.append(None)
                    else:
                        adj_debt = (lt_debt + (st_debt or 0) + (op_lease or 0)
                                    + (fin_lease or 0) + (pension or 0))
                        _adj_debt_tbl.append(adj_debt)
                        if cash_i is not None:
                            nd = adj_debt - cash_i
                            nd_vals.append(nd)
                            nd_ebitda_vals.append(nd / ebitda if ebitda and ebitda > 0 else None)
                        else:
                            nd_vals.append(None)
                            nd_ebitda_vals.append(None)

                # Chart: Net Debt ($M) over time + zero reference line.
                # Line color reflects the most-recent year's signal (red = net
                # debtor, green = net cash). Per-marker colors show the
                # year-by-year status so historical sign-flips stay visible.
                _recent_nd = next((v for v in reversed(nd_vals) if v is not None), None)
                _line_color = T['red'] if (_recent_nd is not None and _recent_nd > 0) else T['accent']
                _marker_colors = [
                    (T['red'] if (v is not None and v > 0) else T['accent'])
                    for v in nd_vals
                ]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=nd_vals, name='Net Debt',
                    mode='lines+markers',
                    line=dict(color=_line_color, width=2.5),
                    marker=dict(color=_marker_colors, size=8,
                                line=dict(color=_line_color, width=1)),
                    hovertemplate='$%{y:,.0f}M<extra>Net Debt</extra>',
                ))
                fig.add_hline(
                    y=0, line_dash="dot", line_color=_COLORS['text_muted'],
                    annotation_text="0", annotation_position="right",
                )
                fig.update_yaxes(tickprefix='$', ticksuffix='M')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")

                with st.expander("Details", expanded=False):
                    _nd_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _nd_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _nd_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _nd_avg = f'{_nd_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _nd_div = f'border-top:3px solid {T["text"]}'
                    _nd_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_nd_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs:
                        _nd_html += f'<th style="{_nd_hdr}">{yr}</th>'
                    _nd_html += f'<th style="{_nd_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _nd_html += '</tr></thead><tbody>'

                    def _row(label, values, fmt='{:,.0f}', avg=True, divider=False, color_fn=None):
                        nonlocal _nd_html
                        label_style = _nd_label + (';' + _nd_div if divider else '')
                        cell_style_base = _nd_cell + (';' + _nd_div if divider else '')
                        avg_style = _nd_avg + (';' + _nd_div if divider else '')
                        _nd_html += f'<tr><td style="{label_style}">{label}</td>'
                        for v in values:
                            if v is None:
                                _nd_html += f'<td style="{cell_style_base}">—</td>'
                            else:
                                extra = ''
                                if color_fn:
                                    c = color_fn(v)
                                    if c:
                                        extra = f';color:{c};font-weight:600'
                                _nd_html += f'<td style="{cell_style_base}{extra}">{fmt.format(v)}</td>'
                        if avg:
                            _valid = [v for v in values if v is not None]
                            if _valid:
                                _a = sum(_valid) / len(_valid)
                                extra = ''
                                if color_fn:
                                    c = color_fn(_a)
                                    if c:
                                        extra = f';color:{c}'
                                _nd_html += f'<td style="{avg_style}{extra}">{fmt.format(_a)}</td>'
                            else:
                                _nd_html += f'<td style="{avg_style}">—</td>'
                        _nd_html += '</tr>'

                    _row("LT Debt", _lt_debt_tbl)
                    _row("ST Debt", _st_debt_tbl)
                    _row("Operating Leases", _op_lease_tbl)
                    _row("Finance Leases", _fin_lease_tbl)
                    _row("Pension Underfunding", _pension_tbl)
                    _row("Adjusted Debt", _adj_debt_tbl, divider=True)
                    _row("Cash", _cash_tbl)
                    _row("Net Debt", nd_vals, divider=True,
                         color_fn=lambda v: T['accent'] if v < 0 else (T['red'] if v > 0 else None))
                    _row("EBITDA", _ebitda_tbl)
                    _row("Net Debt / EBITDA", nd_ebitda_vals, fmt='{:.1f}x', divider=True,
                         color_fn=lambda v: T['accent'] if v < 1.5 else (T['red'] if v > 4 else None))

                    _nd_html += '</tbody></table></div>'
                    st.markdown(_nd_html, unsafe_allow_html=True)
                    st.caption("In $M. Adjusted Debt = LT + ST + Op Leases + Fin Leases + Pension. Net Debt = Adjusted Debt − Cash. EBITDA = OI + D&A. Velden zonder data tonen — (geen tag in EDGAR voor dat jaar).")
            else:
                st.info("Insufficient data for Net Debt (need 3+ years)")

        with st.container(key="fund_sec_6_net_debt_fcf"):
            # ── Net Debt / FCF ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">Net Debt / FCF</span>'
                f'<span class="df-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:260px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'Years of FCF needed to repay net debt. Gebruikt Adjusted Net Debt (incl. leases + pension) uit de Net Debt-sectie hierboven.<br><br>'
                f'<b>&lt; 0×</b> netto cash — geen schuld om af te lossen<br>'
                f'<b>0 − 3×</b> healthy<br>'
                f'<b>3 − 5×</b> acceptabel<br>'
                f'<b>&gt; 5×</b> high debt burden'
                f'</span></span></div>'
                f'<style>.df-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            if _n >= 3:
                netdebt_fcf = []
                for i in range(_n):
                    fcf_v = fund['fcf'][i]
                    nd_v = nd_vals[i]
                    if fcf_v and fcf_v > 0 and nd_v is not None:
                        netdebt_fcf.append(nd_v / fcf_v)
                    else:
                        netdebt_fcf.append(None)
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=netdebt_fcf, name='Net Debt/FCF',
                    line=dict(color=_COLORS['accent'], width=2.5),
                    hovertemplate='%{y:.1f}x<extra>Net Debt/FCF</extra>',
                ))
                # Reference lines: 0 (net-cash boundary), 3x (healthy), 5x (high)
                fig.add_hline(y=0, line_dash="dot", line_color=_COLORS['text_muted'],
                              annotation_text="0", annotation_position="right")
                fig.add_hline(y=3, line_dash="dash", line_color=_COLORS['primary'],
                              annotation_text="3x", annotation_position="top right")
                fig.add_hline(y=5, line_dash="dash", line_color=_COLORS['secondary'],
                              annotation_text="5x", annotation_position="top right")
                fig.update_yaxes(ticksuffix='x')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")

                with st.expander("Details", expanded=False):
                    _df_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _df_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _df_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _df_avg_s = f'{_df_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _df_div = f'border-top:3px solid {T["text"]}'
                    _df_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_df_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs:
                        _df_html += f'<th style="{_df_hdr}">{yr}</th>'
                    _df_html += f'<th style="{_df_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _df_html += '</tr></thead><tbody>'

                    # Net Debt row (re-uses values from the Net Debt section above)
                    _nd_valid_d = [v for v in nd_vals if v is not None]
                    _nd_avg_d = sum(_nd_valid_d) / len(_nd_valid_d) if _nd_valid_d else None
                    _df_html += f'<tr><td style="{_df_label}">Net Debt</td>'
                    for v in nd_vals:
                        if v is not None:
                            _c = T['accent'] if v < 0 else (T['red'] if v > 0 else T['text'])
                            _df_html += f'<td style="{_df_cell};color:{_c}">{v:,.0f}</td>'
                        else:
                            _df_html += f'<td style="{_df_cell}">—</td>'
                    if _nd_avg_d is not None:
                        _c = T['accent'] if _nd_avg_d < 0 else (T['red'] if _nd_avg_d > 0 else T['text'])
                        _df_html += f'<td style="{_df_avg_s};color:{_c}">{_nd_avg_d:,.0f}</td>'
                    else:
                        _df_html += f'<td style="{_df_avg_s}">—</td>'
                    _df_html += '</tr>'

                    # FCF row
                    _fcf2_vals = fund['fcf']
                    _fcf2_valid = [v for v in _fcf2_vals if v is not None]
                    _fcf2_avg = sum(_fcf2_valid) / len(_fcf2_valid) if _fcf2_valid else None
                    _df_html += f'<tr><td style="{_df_label}">Free Cash Flow</td>'
                    for v in _fcf2_vals:
                        _df_html += f'<td style="{_df_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_df_cell}">—</td>'
                    _df_html += f'<td style="{_df_avg_s}">{_fcf2_avg:,.0f}</td>' if _fcf2_avg is not None else f'<td style="{_df_avg_s}">—</td>'
                    _df_html += '</tr>'

                    # Net Debt / FCF row — thick border
                    _df_valid2 = [v for v in netdebt_fcf if v is not None]
                    _df_avg2 = sum(_df_valid2) / len(_df_valid2) if _df_valid2 else None

                    def _nd_fcf_color(v):
                        # < 0 net cash → green, 0-3 healthy → green, 3-5 neutral, > 5 red
                        if v < 3:
                            return T['accent']
                        if v > 5:
                            return T['red']
                        return T['text']

                    _df_html += f'<tr><td style="{_df_label};{_df_div}">Net Debt / FCF</td>'
                    for v in netdebt_fcf:
                        if v is not None:
                            _c = _nd_fcf_color(v)
                            _df_html += f'<td style="{_df_cell};{_df_div};color:{_c};font-weight:600">{v:.1f}x</td>'
                        else:
                            _df_html += f'<td style="{_df_cell};{_df_div}">—</td>'
                    if _df_avg2 is not None:
                        _c = _nd_fcf_color(_df_avg2)
                        _df_html += f'<td style="{_df_avg_s};{_df_div};color:{_c}">{_df_avg2:.1f}x</td>'
                    else:
                        _df_html += f'<td style="{_df_avg_s};{_df_div}">—</td>'
                    _df_html += '</tr>'

                    _df_html += '</tbody></table></div>'
                    st.markdown(_df_html, unsafe_allow_html=True)
                    st.caption("In $M. Net Debt = Adjusted Debt − Cash (zie Net Debt-sectie). Net Debt/FCF = Net Debt / Free Cash Flow.")
            else:
                st.info("Insufficient data for Net Debt / FCF (need 3+ years)")

        with st.container(key="fund_sec_7_roic"):
            # ── ROIC ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">ROIC</span>'
                f'<span class="roic-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:240px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'NOPAT / Invested Capital — measures how well a company generates returns on its capital.<br><br>'
                f'<b>&gt;Discount rate</b> creates value<br>'
                f'<b>&gt;20%</b> excellent<br>'
                f'<b>&lt;Discount rate</b> destroys value'
                f'</span></span></div>'
                f'<style>.roic-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            if _n >= 3:
                roic_vals = []
                _nopat_tbl = []
                _ic_tbl = []
                for i in range(_n):
                    oi = fund['operating_income'][i]
                    eq = fund['total_equity'][i]
                    debt = fund['total_debt'][i]
                    cash_v = fund['cash'][i]
                    tp = fund['tax_provision'][i]
                    pti = fund['pretax_income'][i]
                    tax_rate = tp / pti if tp is not None and pti and pti != 0 else 0.21
                    nopat = oi * (1 - tax_rate) if oi is not None else None
                    ic = (eq or 0) + (debt or 0) - (cash_v or 0)
                    _nopat_tbl.append(nopat)
                    _ic_tbl.append(ic if ic != 0 else None)
                    roic_vals.append(nopat / ic * 100 if nopat is not None and ic and ic > 0 else None)

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=roic_vals, name='ROIC',
                    line=dict(color=_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>ROIC</extra>',
                ))
                wacc_pct = val.get('wacc', 0) * 100
                if wacc_pct > 0:
                    fig.add_hline(
                        y=wacc_pct, line_dash="dash",
                        line_color=_COLORS['secondary'],
                        annotation_text=f"Hurdle rate {wacc_pct:.1f}%",
                        annotation_position="top right",
                    )
                # Historic average — same convention as ROCE chart
                _roic_valid_chart = [v for v in roic_vals if v is not None]
                if _roic_valid_chart:
                    _roic_chart_avg = sum(_roic_valid_chart) / len(_roic_valid_chart)
                    fig.add_hline(
                        y=_roic_chart_avg, line_dash="dot",
                        line_color=_COLORS['text_muted'],
                        annotation_text=f"Avg {_roic_chart_avg:.1f}%",
                        annotation_position="top left",
                    )
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")

                with st.expander("Details", expanded=False):
                    _rc_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _rc_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _rc_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _rc_avg = f'{_rc_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _rc_div = f'border-top:3px solid {T["text"]}'
                    _rc_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_rc_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs:
                        _rc_html += f'<th style="{_rc_hdr}">{yr}</th>'
                    _rc_html += f'<th style="{_rc_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _rc_html += '</tr></thead><tbody>'

                    # NOPAT row
                    _np_valid = [v for v in _nopat_tbl if v is not None]
                    _np_avg = sum(_np_valid) / len(_np_valid) if _np_valid else None
                    _rc_html += f'<tr><td style="{_rc_label}">NOPAT</td>'
                    for v in _nopat_tbl:
                        _rc_html += f'<td style="{_rc_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rc_cell}">—</td>'
                    _rc_html += f'<td style="{_rc_avg}">{_np_avg:,.0f}</td>' if _np_avg is not None else f'<td style="{_rc_avg}">—</td>'
                    _rc_html += '</tr>'

                    # Invested Capital row
                    _ic_valid = [v for v in _ic_tbl if v is not None]
                    _ic_avg = sum(_ic_valid) / len(_ic_valid) if _ic_valid else None
                    _rc_html += f'<tr><td style="{_rc_label}">Invested Capital</td>'
                    for v in _ic_tbl:
                        _rc_html += f'<td style="{_rc_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rc_cell}">—</td>'
                    _rc_html += f'<td style="{_rc_avg}">{_ic_avg:,.0f}</td>' if _ic_avg is not None else f'<td style="{_rc_avg}">—</td>'
                    _rc_html += '</tr>'

                    # ROIC % row — thick top border
                    _roic_valid = [v for v in roic_vals if v is not None]
                    _roic_avg = sum(_roic_valid) / len(_roic_valid) if _roic_valid else None
                    _rc_html += f'<tr><td style="{_rc_label};{_rc_div}">ROIC</td>'
                    for v in roic_vals:
                        if v is not None:
                            _r_color = T['accent'] if v >= 15 else (T['red'] if v < wacc_pct else T['text'])
                            _rc_html += f'<td style="{_rc_cell};{_rc_div};color:{_r_color};font-weight:600">{v:.1f}%</td>'
                        else:
                            _rc_html += f'<td style="{_rc_cell};{_rc_div}">—</td>'
                    if _roic_avg is not None:
                        _ra_color = T['accent'] if _roic_avg >= 15 else (T['red'] if _roic_avg < wacc_pct else T['text'])
                        _rc_html += f'<td style="{_rc_avg};{_rc_div};color:{_ra_color}">{_roic_avg:.1f}%</td>'
                    else:
                        _rc_html += f'<td style="{_rc_avg};{_rc_div}">—</td>'
                    _rc_html += '</tr>'

                    _rc_html += '</tbody></table></div>'
                    st.markdown(_rc_html, unsafe_allow_html=True)
                    st.caption("In $M. NOPAT = Operating Income × (1 − Tax Rate). IC = Equity + Debt − Cash.")
            else:
                st.info("Insufficient data for ROIC (need 3+ years)")

        with st.container(key="fund_sec_8_rev_per_share"):
            # ── Revenue per Share Growth ──
            st.markdown(
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<span style="font-weight:700">Revenue per Share Growth</span>'
                f'<span class="rps-tip" style="position:relative;cursor:help">'
                f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" style="opacity:0.35;vertical-align:middle">'
                f'<circle cx="8" cy="8" r="7" stroke="{T["text_muted"]}" stroke-width="1.5"/>'
                f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" fill="{T["text_muted"]}">?</text>'
                f'</svg>'
                f'<span style="visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;'
                f'background:{T["card"]};color:{T["text"]};border:1px solid {T["border_medium"]};'
                f'border-radius:8px;padding:10px 14px;font-size:0.78rem;line-height:1.5;'
                f'font-weight:400;width:260px;z-index:999;box-shadow:{T["shadow_hover"]};'
                f'pointer-events:none;transition:opacity 0.15s ease">'
                f'Compares total revenue growth with revenue per share.<br><br>'
                f'<b>Rev/Share &gt; Revenue</b> buybacks boost per-share growth<br>'
                f'<b>Rev/Share &lt; Revenue</b> dilution from share issuance'
                f'</span></span></div>'
                f'<style>.rps-tip:hover span{{visibility:visible!important;opacity:1!important}}</style>',
                unsafe_allow_html=True,
            )
            if _n >= 3:
                # Derive shares from EPS / Net Income when the direct share tags
                # are missing (e.g. Visa only reports shares in non-standard tags)
                _shares_eff = []
                _eps_l = fund.get('eps') or [None] * _n
                _ni_l = fund.get('net_income') or [None] * _n
                for i in range(_n):
                    s = fund['shares'][i]
                    if not s or s <= 0:  # noqa: SIM102 — nested form keeps "shares missing? derive from EPS×NI" intent clearer
                        if _eps_l[i] and _ni_l[i] is not None and _eps_l[i] != 0:
                            s = (_ni_l[i] * 1e6) / _eps_l[i]
                    _shares_eff.append(s if s and s > 0 else None)
                rps = [fund['revenue'][i] * 1e6 / _shares_eff[i]
                       if _shares_eff[i] and fund['revenue'][i] is not None
                       else None
                       for i in range(_n)]
                rps_g = _pct_growth(rps)
                rev_g_clean = _pct_growth(fund['revenue'])
                _has_shares = any(s is not None for s in _shares_eff)
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[r * 100 if r is not None else None for r in rev_g_clean[1:]],
                    name='Revenue Growth',
                    line=dict(color=_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>Rev Growth</extra>',
                ))
                if _has_shares:
                    fig.add_trace(go.Scatter(
                        x=_yrs[1:], y=[r * 100 if r is not None else None for r in rps_g[1:]],
                        name='Rev/Share Growth',
                        line=dict(color=_COLORS['accent'], width=2.5, dash='dash'),
                        hovertemplate='%{y:.1f}%<extra>Rev/Share Growth</extra>',
                    ))
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, width="stretch")
                if not _has_shares:
                    st.caption("Rev/Share niet beschikbaar: dit bedrijf rapporteert geen share-counts in EDGAR (bv. V).")

                with st.expander("Details", expanded=False):
                    _rps_cell = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text"]};border-top:1px solid {T["grid"]}'
                    _rps_hdr = f'text-align:right;padding:5px 10px;font-size:0.85rem;color:{T["text_muted"]};border-bottom:1px solid {T["grid"]}'
                    _rps_label = f'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:{T["text"]};white-space:nowrap;border-top:1px solid {T["grid"]}'
                    _rps_avg_s = f'{_rps_cell};font-weight:600;border-left:2px solid {T["border_medium"]}'
                    _rps_div = f'border-top:3px solid {T["text"]}'
                    _rps_html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse">'
                        '<thead><tr>'
                        f'<th style="{_rps_hdr};text-align:left"></th>'
                    )
                    for yr in _yrs:
                        _rps_html += f'<th style="{_rps_hdr}">{yr}</th>'
                    _rps_html += f'<th style="{_rps_hdr};border-left:2px solid {T["border_medium"]}">Avg</th>'
                    _rps_html += '</tr></thead><tbody>'

                    # Revenue row ($M)
                    _rev_valid = [v for v in fund['revenue'] if v is not None]
                    _rev_avg2 = sum(_rev_valid) / len(_rev_valid) if _rev_valid else None
                    _rps_html += f'<tr><td style="{_rps_label}">Revenue ($M)</td>'
                    for v in fund['revenue']:
                        _rps_html += f'<td style="{_rps_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rps_cell}">—</td>'
                    _rps_html += f'<td style="{_rps_avg_s}">{_rev_avg2:,.0f}</td>' if _rev_avg2 is not None else f'<td style="{_rps_avg_s}">—</td>'
                    _rps_html += '</tr>'

                    # Shares row
                    _sh_vals = [_shares_eff[i] / 1e6 if _shares_eff[i] else None for i in range(_n)]
                    _sh_valid = [v for v in _sh_vals if v is not None]
                    _sh_avg = sum(_sh_valid) / len(_sh_valid) if _sh_valid else None
                    _rps_html += f'<tr><td style="{_rps_label}">Shares (M)</td>'
                    for v in _sh_vals:
                        _rps_html += f'<td style="{_rps_cell}">{v:,.0f}</td>' if v is not None else f'<td style="{_rps_cell}">—</td>'
                    _rps_html += f'<td style="{_rps_avg_s}">{_sh_avg:,.0f}</td>' if _sh_avg is not None else f'<td style="{_rps_avg_s}">—</td>'
                    _rps_html += '</tr>'

                    # Rev/Share row — thick border
                    _rps_vals = [fund['revenue'][i] / _sh_vals[i] if _sh_vals[i] and _sh_vals[i] > 0 and fund['revenue'][i] is not None else None for i in range(_n)]
                    _rps_valid2 = [v for v in _rps_vals if v is not None]
                    _rps_avg2 = sum(_rps_valid2) / len(_rps_valid2) if _rps_valid2 else None
                    _rps_html += f'<tr><td style="{_rps_label};{_rps_div}">Rev/Share ($)</td>'
                    for v in _rps_vals:
                        _rps_html += f'<td style="{_rps_cell};{_rps_div}">${v:,.2f}</td>' if v is not None else f'<td style="{_rps_cell};{_rps_div}">—</td>'
                    _rps_html += f'<td style="{_rps_avg_s};{_rps_div}">${_rps_avg2:,.2f}</td>' if _rps_avg2 is not None else f'<td style="{_rps_avg_s};{_rps_div}">—</td>'
                    _rps_html += '</tr>'

                    _rps_html += '</tbody></table></div>'
                    st.markdown(_rps_html, unsafe_allow_html=True)
                    st.caption("Revenue in $M. Rev/Share = Revenue ($M) / Shares (M).")
            else:
                st.info("Insufficient data for Revenue per Share (need 3+ years)")

    with _tab_notes:
        st.markdown(
            f"""<style>
            [class*="st-key-ai_out_"],
            [class*="st-key-ai_out_"] * {{
                font-family: 'DM Sans', -apple-system, BlinkMacSystemFont,
                             'Helvetica Neue', Arial, sans-serif !important;
            }}
            [class*="st-key-ai_out_"] table {{
                border-collapse: collapse;
                width: 100%;
            }}
            [class*="st-key-ai_out_"] th,
            [class*="st-key-ai_out_"] td {{
                padding: 6px 10px;
                border: 1px solid rgba(0,0,0,0.08);
            }}
            </style>""",
            unsafe_allow_html=True,
        )
        _gem_ok = _gemini_ready()
        _company_name = cfg.get('company', ticker)

        # ── Two portfolio-style section panels (sage top accent + faint tint) ──
        st.markdown(
            '<style>'
            f'.st-key-prescan_seg_premortem, .st-key-prescan_seg_robustness, .st-key-prescan_seg_research {{'
            f'  background: {T["card"]} !important; border: none !important;'
            f'  border-top: 3px solid {T["accent"]} !important;'
            f'  border-radius: 24px !important; box-shadow: {T["shadow"]} !important;'
            f'  padding: 6px 28px 20px 28px !important;'
            f'  margin-bottom: 26px !important; }}'
            '</style>',
            unsafe_allow_html=True,
        )
        with st.container(key="prescan_seg_premortem"):
            st.markdown("#### Pre-mortem & action triggers")
            _pm_raw = cfg.get("premortem")
            _pm = _pm_raw if isinstance(_pm_raw, dict) else {}
            _pm_legacy = _pm_raw if isinstance(_pm_raw, str) and _pm_raw.strip() else ""
            _pm_has = any(_pm.get(k) for k, _ in _PM_SECTIONS) or bool(_pm_legacy)
            _pm_ek = f"premortem_edit_{ticker}"
            if st.session_state.get(_pm_ek, False) or not _pm_has:
                st.caption("Same fixed sections for every ticker — one item per line.")
                if _pm_legacy:
                    st.info("Old free-text below — copy the bits into the sections, then Save:")
                    st.code(_pm_legacy)
                _cur = st.text_input(
                    "Current view", value=_pm.get("current", ""), key=f"pm_cur_{ticker}",
                    placeholder="Spot $143 | cost basis $156.69 | FV mid $174 | buy $139")
                _ins = {}
                for _k, _lbl in _PM_SECTIONS:
                    if _k == "current":
                        continue
                    _ins[_k] = st.text_area(
                        _lbl, value="\n".join(_pm.get(_k, []) or []),
                        key=f"pm_{_k}_{ticker}", height=110)
                if st.button("Save", key=f"pm_save_{ticker}", type="primary"):
                    cfg["premortem"] = {
                        "current": _cur.strip(),
                        "sell": _pm_lines(_ins["sell"]), "add": _pm_lines(_ins["add"]),
                        "ignore": _pm_lines(_ins["ignore"]),
                        "discipline": _pm_lines(_ins["discipline"]),
                    }
                    save_config(_sb_client, ticker, cfg)
                    st.session_state[_pm_ek] = False
                    st.toast("Pre-mortem saved")
                    st.rerun()
            else:
                _pm_html = _render_premortem(_pm, T)
                st.markdown(_pm_html if _pm_html else _pm_legacy, unsafe_allow_html=True)
                if st.button("✏️ Edit", key=f"pm_edit_{ticker}"):
                    st.session_state[_pm_ek] = True
                    st.rerun()

        with st.container(key="prescan_seg_robustness"):
            st.markdown("#### Robustness")
            st.markdown(_render_robustness_table(cfg, T), unsafe_allow_html=True)

            # Override editor: adjust any axis band; re-derive verdict + persist.
            # Styled as a hero-card to match the section cards / Phase scorecard.
            _rob_state = cfg.get("robustness") or {}
            if _rob_state.get("axes_base"):
                import robustness as _rob_mod
                st.markdown(
                    '<style>'
                    f'.st-key-prescan_robust_editor [data-testid="stExpander"] {{'
                    f'  background: transparent !important; border: none !important;'
                    f'  border-top: 1px solid {T["border_light"]} !important;'
                    f'  border-radius: 0 !important; box-shadow: none !important;'
                    f'  margin-bottom: 0; }}'
                    f'.st-key-prescan_robust_editor [data-testid="stExpander"] summary,'
                    f'.st-key-prescan_robust_editor [data-testid="stExpander"] summary *,'
                    f'.st-key-prescan_robust_editor [data-testid="stExpander"] details,'
                    f'.st-key-prescan_robust_editor [data-testid="stExpander"] details > div {{'
                    f'  border: none !important; background: transparent !important;'
                    f'  box-shadow: none !important; }}'
                    f'.st-key-prescan_robust_editor [data-testid="stExpander"] details > summary {{'
                    f'  padding: 14px 2px !important; font-weight: 600;'
                    f'  font-size: 0.92rem; color: {T["text"]}; }}'
                    f'.st-key-prescan_robust_editor [data-testid="stExpander"] details > div {{'
                    f'  padding: 0 2px 16px 2px !important; }}'
                    '</style>',
                    unsafe_allow_html=True,
                )
                with st.container(key="prescan_robust_editor"), \
                        st.expander("Adjust robustness bands"):
                    _ov = dict(_rob_state.get("overrides") or {})
                    _changed = False
                    for _k, _lbl, _db, _src in _rob_mod.AXES:
                        _cur = (_rob_state["axes"].get(_k) or {}).get("band", "mid")
                        _new = st.selectbox(
                            _lbl, _rob_mod.BANDS, index=_rob_mod.BANDS.index(_cur),
                            key=f"rob_ov_{ticker}_{_k}")
                        _base_band = (_rob_state["axes_base"].get(_k) or {}).get("band", "mid")
                        if _new != _base_band:
                            _ov[_k] = _new
                        elif _k in _ov:
                            del _ov[_k]
                        if _new != _cur:
                            _changed = True
                    if _changed and st.button("Save bands", key=f"rob_save_{ticker}"):
                        _eff, _verdict = _rob_mod.resolve(_rob_state["axes_base"], _ov)
                        cfg["robustness"] = {**_rob_state, "axes": _eff,
                                             "overrides": _ov, **_verdict}
                        save_config(_sb_client, ticker, cfg)
                        st.session_state["_wl_config_dirty"] = True
                        st.rerun()



        with st.container(key="prescan_seg_research"):
            st.markdown("#### AI Research Sections")

            # Hero-card styling for the Phase scorecard, matching the section cards
            # below (see 10 UI Patterns → Collapsible hero-card).
            st.markdown(
                '<style>'
                f'.st-key-prescan_scorecard [data-testid="stExpander"] {{'
                f'  background: transparent !important; border: none !important;'
                f'  border-top: 1px solid {T["border_light"]} !important;'
                f'  border-radius: 0 !important; box-shadow: none !important;'
                f'  margin-bottom: 0; }}'
                f'.st-key-prescan_scorecard [data-testid="stExpander"] summary,'
                f'.st-key-prescan_scorecard [data-testid="stExpander"] summary *,'
                f'.st-key-prescan_scorecard [data-testid="stExpander"] details,'
                f'.st-key-prescan_scorecard [data-testid="stExpander"] details > div {{'
                f'  border: none !important; background: transparent !important;'
                f'  box-shadow: none !important; }}'
                f'.st-key-prescan_scorecard [data-testid="stExpander"] details > summary {{'
                f'  padding: 14px 2px !important; font-weight: 600;'
                f'  font-size: 0.92rem; color: {T["text"]}; }}'
                f'.st-key-prescan_scorecard [data-testid="stExpander"] details > div {{'
                f'  padding: 0 2px 16px 2px !important; }}'
                '</style>',
                unsafe_allow_html=True,
            )
            with st.container(key="prescan_scorecard"), \
                    st.expander("Phase scorecard", expanded=True):
                # ── Scorecard overview ──
                _sc_raw = (cfg.get('ai_notes') or {}).get('Scorecard', '') if isinstance(cfg.get('ai_notes'), dict) else ''
                _sc_data = _parse_scorecard_json(_sc_raw) if _sc_raw else None
                _sc_form_key = f"sc_form_editing_{ticker}"
                _sc_form_editing = bool(st.session_state.get(_sc_form_key, False))

                _sc_rating_options = ["red", "yellow", "green"]
                _sc_rating_labels = {"red": "🔴 Red", "yellow": "🟡 Yellow", "green": "🟢 Green"}
                _sc_verdict_options = [
                    ("pass", "No — Pass"),
                    ("revisit", "Kind Of — Revisit"),
                    ("deep_dive", "Yes — Deep Dive"),
                ]
                _sc_ap_keys = [
                    ("business_description", "Business Description"),
                    ("moat", "Moat"),
                    ("long_term_potential", "Long Term Potential"),
                ]

                def _sc_rating_index(value: str) -> int:
                    v = (value or "").lower().strip()
                    return _sc_rating_options.index(v) if v in _sc_rating_options else 1

                def _sc_clear_form_state():
                    for _k in list(st.session_state.keys()):
                        if _k.startswith(f"sc_f_{ticker}_"):
                            del st.session_state[_k]

                if _sc_form_editing and _sc_data:
                    # Fallback summary same as view mode so editing inherits derived value
                    if not (_sc_data.get("summary") or "").strip():
                        _inv_sum = (cfg.get('ai_notes') or {}).get('Investment Summary', '') \
                            if isinstance(cfg.get('ai_notes'), dict) else ''
                        if _inv_sum:
                            import re as _re2
                            _m = _re2.search(
                                r'One-line thesis[^\n:]*:\s*([^\n]+)', _inv_sum, _re2.IGNORECASE,
                            )
                            if _m:
                                _thesis = _m.group(1).strip()
                                _thesis = _re2.sub(r'^\**\s*\[?', '', _thesis)
                                _thesis = _re2.sub(r'\]?\s*\**$', '', _thesis)
                                if _thesis and not _thesis.startswith('['):
                                    _sc_data["summary"] = _thesis

                    with st.container(border=True):
                        _hcol1, _hcol2, _hcol3 = st.columns([4, 1, 1])
                        _hcol1.markdown("**Edit Scorecard fields**")
                        if _hcol2.button("💾 Save", key=f"sc_form_save_{ticker}",
                                         width="stretch", type="primary"):
                            def _g(suffix, default=""):
                                return st.session_state.get(f"sc_f_{ticker}_{suffix}", default)

                            _phase_num_raw = str(_g("pn", "")).strip()
                            try:
                                _phase_num = int(_phase_num_raw) if _phase_num_raw else _phase_num_raw
                            except ValueError:
                                _phase_num = _phase_num_raw

                            _km_count = int(st.session_state.get(f"sc_f_{ticker}_km_count", 0))
                            _new_metrics = []
                            for _i in range(_km_count):
                                _name = (_g(f"km_{_i}_name", "") or "").strip()
                                if not _name:
                                    continue
                                _new_metrics.append({
                                    "name": _name,
                                    "rating": _g(f"km_{_i}_r", "yellow"),
                                    "value": (_g(f"km_{_i}_v", "") or "").strip(),
                                })

                            _verdict_label = _g("verdict", _sc_verdict_options[1][1])
                            _verdict_code = next(
                                (v for v, lbl in _sc_verdict_options if lbl == _verdict_label),
                                "",
                            )

                            _new_sc = {
                                "phase": {
                                    "number": _phase_num,
                                    "name": _g("pname", ""),
                                },
                                "summary": _g("sum", ""),
                                "all_phases": {
                                    _k: {
                                        "rating": _g(f"ap_{_k}_r", "yellow"),
                                        "note": _g(f"ap_{_k}_n", ""),
                                    }
                                    for _k, _ in _sc_ap_keys
                                },
                                "key_metrics": _new_metrics,
                                "execution_risk": {
                                    "rating": _g("er_r", "yellow"),
                                    "note": _g("er_n", ""),
                                },
                                # No "valuation" key: those inputs are gone and
                                # writing them from absent widgets would save a
                                # row of empty yellows over whatever a previous
                                # scorecard held.
                                "verdict": _verdict_code,
                            }

                            import json as _json
                            _new_sc_text = (
                                "```json\n"
                                + _json.dumps(_new_sc, indent=2, ensure_ascii=False)
                                + "\n```"
                            )

                            _ai_notes = cfg.get('ai_notes') or {}
                            if not isinstance(_ai_notes, dict):
                                _ai_notes = {}
                            _ai_notes['Scorecard'] = _new_sc_text
                            cfg['ai_notes'] = _ai_notes
                            save_config(_sb_client, ticker, cfg)
                            _sc_clear_form_state()
                            st.session_state[_sc_form_key] = False
                            st.rerun()

                        if _hcol3.button("Cancel", key=f"sc_form_cancel_{ticker}",
                                         width="stretch"):
                            _sc_clear_form_state()
                            st.session_state[_sc_form_key] = False
                            st.rerun()

                        # Phase
                        _phase = _sc_data.get("phase", {}) or {}
                        _ph_c1, _ph_c2 = st.columns([1, 3])
                        _ph_c1.text_input(
                            "Phase #", value=str(_phase.get("number", "")),
                            key=f"sc_f_{ticker}_pn",
                        )
                        _ph_c2.text_input(
                            "Phase name", value=_phase.get("name", ""),
                            key=f"sc_f_{ticker}_pname",
                        )

                        st.text_area(
                            "Summary",
                            value=_sc_data.get("summary", "") or "",
                            key=f"sc_f_{ticker}_sum",
                            height=80,
                        )

                        st.markdown("**Assess for All Phases**")
                        _all_phases = _sc_data.get("all_phases", {}) or {}
                        for _k, _label in _sc_ap_keys:
                            _item = _all_phases.get(_k, {}) or {}
                            _r1, _r2, _r3 = st.columns([2, 1, 4])
                            _r1.markdown(f"**{_label}**")
                            _r2.selectbox(
                                f"{_label} rating",
                                _sc_rating_options,
                                format_func=lambda x: _sc_rating_labels[x],
                                index=_sc_rating_index(_item.get("rating")),
                                key=f"sc_f_{ticker}_ap_{_k}_r",
                                label_visibility="collapsed",
                            )
                            _r3.text_area(
                                f"{_label} note",
                                value=_item.get("note", "") or "",
                                key=f"sc_f_{ticker}_ap_{_k}_n",
                                label_visibility="collapsed",
                                height=80,
                            )

                        st.markdown("**Key Metrics**  *(empty rows are dropped on save)*")
                        _km_data = _sc_data.get("key_metrics", []) or []
                        _km_count = max(len(_km_data), 0) + 2
                        st.session_state[f"sc_f_{ticker}_km_count"] = _km_count
                        for _i in range(_km_count):
                            _m = _km_data[_i] if _i < len(_km_data) else {}
                            _km_c1, _km_c2, _km_c3 = st.columns([2, 1, 2])
                            _km_c1.text_input(
                                f"Metric {_i} name", value=_m.get("name", "") or "",
                                key=f"sc_f_{ticker}_km_{_i}_name",
                                placeholder="Metric name",
                                label_visibility="collapsed",
                            )
                            _km_c2.selectbox(
                                f"Metric {_i} rating",
                                _sc_rating_options,
                                format_func=lambda x: _sc_rating_labels[x],
                                index=_sc_rating_index(_m.get("rating")),
                                key=f"sc_f_{ticker}_km_{_i}_r",
                                label_visibility="collapsed",
                            )
                            _km_c3.text_input(
                                f"Metric {_i} value", value=_m.get("value", "") or "",
                                key=f"sc_f_{ticker}_km_{_i}_v",
                                placeholder="Value",
                                label_visibility="collapsed",
                            )

                        st.markdown("**Risk**")
                        _er = _sc_data.get("execution_risk", {}) or {}
                        _er_c1, _er_c2, _er_c3 = st.columns([2, 1, 4])
                        _er_c1.markdown("**Execution Risk**")
                        _er_c2.selectbox(
                            "Execution Risk rating",
                            _sc_rating_options,
                            format_func=lambda x: _sc_rating_labels[x],
                            index=_sc_rating_index(_er.get("rating")),
                            key=f"sc_f_{ticker}_er_r",
                            label_visibility="collapsed",
                        )
                        _er_c3.text_area(
                            "Execution Risk note",
                            value=_er.get("note", "") or "",
                            key=f"sc_f_{ticker}_er_n",
                            label_visibility="collapsed",
                            height=80,
                        )

                        st.markdown("**Verdict**")
                        _cur_verdict = (_sc_data.get("verdict") or "").lower().strip()
                        _cur_verdict_label = next(
                            (lbl for v, lbl in _sc_verdict_options if v == _cur_verdict),
                            _sc_verdict_options[1][1],
                        )
                        st.pills(
                            "Verdict",
                            [lbl for _, lbl in _sc_verdict_options],
                            default=_cur_verdict_label,
                            key=f"sc_f_{ticker}_verdict",
                            label_visibility="collapsed",
                        )
                elif _sc_data:
                    # Fallback: derive summary from Investment Summary result if the
                    # Scorecard JSON is missing a summary field
                    if not (_sc_data.get("summary") or "").strip():
                        _inv_sum = (cfg.get('ai_notes') or {}).get('Investment Summary', '') \
                            if isinstance(cfg.get('ai_notes'), dict) else ''
                        if _inv_sum:
                            import re as _re2
                            _m = _re2.search(
                                r'One-line thesis[^\n:]*:\s*([^\n]+)', _inv_sum, _re2.IGNORECASE,
                            )
                            if _m:
                                _thesis = _m.group(1).strip()
                                _thesis = _re2.sub(r'^\**\s*\[?', '', _thesis)
                                _thesis = _re2.sub(r'\]?\s*\**$', '', _thesis)
                                if _thesis and not _thesis.startswith('['):
                                    _sc_data["summary"] = _thesis
                    _hcol1, _hcol2 = st.columns([5, 1])
                    with _hcol2:
                        if st.button(
                            "✏ Edit fields", key=f"sc_form_edit_btn_{ticker}",
                            width="stretch",
                        ):
                            _sc_clear_form_state()
                            st.session_state[_sc_form_key] = True
                            st.rerun()
                    st.markdown(
                        _render_scorecard(
                            _sc_data, T, ticker, cfg.get('company', ticker),
                        ),
                        unsafe_allow_html=True,
                    )
                elif _sc_raw:
                    st.warning(
                        "Scorecard output exists but could not be parsed as JSON. "
                        "Click Clear and Run again on the Scorecard section below.",
                        icon="⚠️",
                    )
                else:
                    st.info(
                        "Run all analyses below, then run **Scorecard** at the bottom "
                        "to generate a visual overview here.",
                        icon="📋",
                    )


            # ── Load library (globaal, via user_prefs) ──
            _prefs = load_user_prefs(_sb_client)
            _library = list(_prefs.get('ai_prompts') or [])

            # ── Results per ticker ──
            # New format: cfg['ai_notes'] is dict {title: content}
            # Old format (legacy): list of {"title","prompt","content"}
            _raw = cfg.get('ai_notes') or {}
            _results: dict[str, str] = {}
            if isinstance(_raw, list):
                # Migrate old list format → library + results
                for _sec in _raw:
                    _t = _sec.get('title')
                    if not _t:
                        continue
                    _results[_t] = _sec.get('content', '')
                    # Promote prompt to library if not present yet
                    if _sec.get('prompt') and not any(
                        p.get('title') == _t for p in _library
                    ):
                        _library.append({"title": _t, "prompt": _sec['prompt']})
                cfg['ai_notes'] = _results
                _prefs['ai_prompts'] = _library
                save_user_prefs(_sb_client, _prefs)
                save_config(_sb_client, ticker, cfg)
            else:
                _results = dict(_raw)

            # ── Auto-load shipped defaults into library ──
            _lib_titles = {p.get('title', '') for p in _library}
            _missing_defaults = [p for p in DEFAULT_AI_PROMPTS if p['title'] not in _lib_titles]
            if _missing_defaults:
                for p in _missing_defaults:
                    _library.append({"title": p['title'], "prompt": p['prompt']})
                _prefs['ai_prompts'] = _library
                save_user_prefs(_sb_client, _prefs)

            # ── Per-ticker: render each library prompt with Run + result ──
            _results_changed = False
            def _fill_prompt(_prompt: str) -> str:
                """Apply {ticker}, {company}, and {prior:Section} substitutions.
                Used for both the Run button and the copy-prompt expander so the
                two stay in sync."""
                import re as _re
                def _sub_prior(_m):
                    _t = _m.group(1).strip()
                    _c = _results.get(_t, '').strip()
                    if not _c:
                        return f"(no prior '{_t}' analysis available for this ticker)"
                    return _c
                _filled = _re.sub(r'\{prior:([^}]+)\}', _sub_prior, _prompt)
                _filled = _filled.replace("{ticker}", ticker).replace(
                    "{company}", _company_name
                )
                if "{ticker}" not in _prompt and "{company}" not in _prompt and "{prior:" not in _prompt:
                    _filled = (
                        f"**IMPORTANT OVERRIDE:** The company to analyze is "
                        f"**{_company_name} (ticker: {ticker})**. "
                        f"Do NOT ask the user for a company — it is provided here. "
                        f"Begin the analysis immediately using this company.\n\n"
                        f"---\n\n{_filled}"
                    )
                return _filled

            import json as _json_for_copy
            import streamlit.components.v1 as _components

            # Hero-card styling for each prescan section + reset for the nested
            # Edit/paste expander inside it. Same pattern as the watchlist
            # categories (see 10 UI Patterns → Collapsible hero-card).
            if _library:
                st.markdown(
                    '<style>'
                    + ''.join(
                        f'.st-key-prescan_card_{i} [data-testid="stExpander"] {{'
                        f'  background: transparent !important;'
                        f'  border: none !important;'
                        f'  border-top: 1px solid {T["border_light"]} !important;'
                        f'  border-radius: 0 !important;'
                        f'  box-shadow: none !important;'
                        f'  margin-bottom: 0;'
                        f'}}'
                        f'.st-key-prescan_card_{i} [data-testid="stExpander"] summary,'
                        f'.st-key-prescan_card_{i} [data-testid="stExpander"] summary *,'
                        f'.st-key-prescan_card_{i} [data-testid="stExpander"] details,'
                        f'.st-key-prescan_card_{i} [data-testid="stExpander"] details > div {{'
                        f'  border: none !important;'
                        f'  background: transparent !important;'
                        f'  box-shadow: none !important;'
                        f'}}'
                        f'.st-key-prescan_card_{i} [data-testid="stExpander"] details > summary {{'
                        f'  padding: 14px 2px !important;'
                        f'  font-weight: 600;'
                        f'  font-size: 0.92rem;'
                        f'  color: {T["text"]};'
                        f'}}'
                        f'.st-key-prescan_card_{i} [data-testid="stExpander"] details > div {{'
                        f'  padding: 0 2px 16px 2px !important;'
                        f'}}'
                        # Reset for the inner Edit/paste expander so it
                        # doesn't inherit the hero-card look as a nested card.
                        f'.st-key-prescan_card_{i}_edit [data-testid="stExpander"] {{'
                        f'  background: transparent !important;'
                        f'  border: 1px solid {T["border_light"]} !important;'
                        f'  border-top: 1px solid {T["border_light"]} !important;'
                        f'  border-radius: 8px !important;'
                        f'  box-shadow: none !important;'
                        f'  margin-top: 12px;'
                        f'  margin-bottom: 0;'
                        f'  overflow: visible;'
                        f'}}'
                        f'.st-key-prescan_card_{i}_edit [data-testid="stExpander"] details > summary {{'
                        f'  padding: 10px 16px !important;'
                        f'  font-weight: 400;'
                        f'  font-size: 0.85rem;'
                        f'  color: {T["text_muted"]};'
                        f'}}'
                        f'.st-key-prescan_card_{i}_edit [data-testid="stExpander"] details > div {{'
                        f'  padding: 0 16px 12px 16px !important;'
                        f'}}'
                        for i in range(len(_library))
                    )
                    + '</style>',
                    unsafe_allow_html=True,
                )

            for _li, _lp in enumerate(_library):
                _title = _lp.get('title', f'Prompt {_li + 1}')
                # Scorecard + Robustness already have their own visuals above (the
                # Phase scorecard card and the robustness table). Keep them in the
                # library so they still run in the background, but don't render
                # duplicate prompt cards here.
                if _title in ("Scorecard", "Robustness"):
                    continue
                _prompt = _lp.get('prompt', '')
                _content = _results.get(_title, '')
                _widget_key = f"ed_ai_res_{_li}"
                with st.container(key=f"prescan_card_{_li}"), \
                        st.expander(_title, expanded=False):
                    _rb1, _rb2, _rb3, _rb4 = st.columns([1, 1, 1, 2])
                    with _rb1:
                        _run_clicked = st.button(
                            "▶ Run", key=f"ed_ai_run_{_li}",
                            width="stretch", type="primary",
                            disabled=not _gem_ok,
                        )
                    with _rb2:
                        _clear_clicked = st.button(
                            "Clear", key=f"ed_ai_clear_{_li}",
                            width="stretch", type="primary",
                            disabled=not _content,
                        )
                    with _rb3:
                        # Native HTML button so navigator.clipboard.writeText fires
                        # in the user-gesture context (st.button would roundtrip
                        # through the server first, breaking the gesture). The
                        # click handler is attached via addEventListener instead
                        # of inline onclick so apostrophes/quotes in the prompt
                        # body can't break HTML attribute parsing. Styling
                        # mirrors the LazyTheta primary-button override
                        # (sage-green pill) so it sits next to Run/Clear visually.
                        if _prompt.strip():
                            _filled_for_copy = _fill_prompt(_prompt)
                            _safe_payload = _json_for_copy.dumps(_filled_for_copy)
                            _btn_id = f"cp-btn-{_li}"
                            _accent = T['accent']
                            _accent_hover = T['accent_hover']
                            _btn_text_color = T['text']
                            _components.html(
                                f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Source+Sans+Pro:wght@400;600&display=swap');
      body {{ margin: 0; padding: 0; }}
      .lt-copy-btn {{
        width: 100%;
        background-color: {_accent};
        color: {_btn_text_color};
        border: none;
        border-radius: 980px;
        padding: 12px 24px;
        font-size: 1rem;
        font-weight: 400;
        cursor: pointer;
        font-family: 'Source Sans Pro', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
        transition: background-color 0.2s ease;
      }}
      .lt-copy-btn:hover {{ background-color: {_accent_hover}; }}
    </style>
    <button id="{_btn_id}" type="button" class="lt-copy-btn">Copy prompt</button>
    <script>
    (function() {{
      const text = {_safe_payload};
      const btn = document.getElementById("{_btn_id}");
      if (!btn) return;
      btn.addEventListener("click", function() {{
        navigator.clipboard.writeText(text).then(function() {{
          const original = btn.textContent;
          btn.textContent = "Gekopieerd";
          setTimeout(function() {{ btn.textContent = original; }}, 1500);
        }}).catch(function() {{ btn.textContent = "Failed"; }});
      }});
    }})();
    </script>
                                """,
                                height=60,
                            )

                    if _run_clicked:
                        if not _prompt.strip():
                            st.error("Prompt is leeg. Vul 'm in via de 📚 Prompt Library expander bovenaan.")
                            st.stop()
                        _filled = _fill_prompt(_prompt)
                        with st.spinner(f"AI aan het werk ({_title})..."):
                            _ans, _err = _gemini_run(_filled)
                        if _err:
                            st.error(_err)
                        elif not _ans or not _ans.strip():
                            st.warning(f"AI call returned empty response for {_title}")
                        else:
                            _results[_title] = _ans
                            cfg['ai_notes'] = _results
                            save_config(_sb_client, ticker, cfg)
                            st.session_state.pop(_widget_key, None)
                            st.rerun()

                    if _clear_clicked:
                        _results.pop(_title, None)
                        cfg['ai_notes'] = _results
                        save_config(_sb_client, ticker, cfg)
                        st.session_state.pop(_widget_key, None)
                        st.rerun()

                    if _content.strip():
                        with st.container(key=f"ai_out_{_li}"):
                            # Moat Cards / Risk Cards / Business Cards / Company
                            # Profile are JSON for their tabs; shown as raw text
                            # they read as a code dump. Draw the cards.
                            if _title == moat_cards.TITLE:
                                _card = moat_cards.cards_section_html(_content, T)
                            elif _title == risk_cards.TITLE:
                                _card = risk_cards.cards_section_html(_content, T)
                            elif _title == business_cards.TITLE:
                                _card = business_cards.quality_section_html(_content, T)
                            elif _title == company_profile.TITLE:
                                _card = company_profile.profile_list_html(_content, T)
                            else:
                                _card = _verdict_card_html(_content, _title)
                            if _card:
                                st.markdown(_card, unsafe_allow_html=True)
                            else:
                                st.markdown(_content)
                    else:
                        st.caption("_No output yet. Click ▶ Run or paste manually via Edit._")
                    with st.container(key=f"prescan_card_{_li}_edit"), \
                            st.expander("Edit / paste", expanded=False):
                        # Sync widget state to saved content when they diverge
                        # (e.g. after Run or a switch between tickers)
                        if _widget_key not in st.session_state:
                            st.session_state[_widget_key] = _content
                        _new_content = st.text_area(
                            "Content",
                            height=280,
                            key=_widget_key,
                            label_visibility="collapsed",
                            placeholder="Plak of bewerk output hier (markdown)...",
                        )
                        if _new_content != _content:
                            _results[_title] = _new_content
                            _results_changed = True

            if _results_changed:
                cfg['ai_notes'] = _results
                save_config(_sb_client, ticker, cfg)

    with _tab_dcf:
        with _bridge_slot:
            # ── Valuation Bridge (rendered into the DCF card via _bridge_slot) ──
            _bridge_keys = "ed_cash,ed_sec,ed_eqi,ed_debt,ed_min,ed_pen,ed_shares,ed_mos"
            _bk = _bridge_keys.split(",")
            _sel_input = ",\n".join(f'.st-key-{k} .stNumberInput input[type="number"]' for k in _bk)
            _sel_label = ",\n".join(
                f'.st-key-{k} [data-testid="stWidgetLabel"],\n'
                f'.st-key-{k} [data-testid="stWidgetLabel"] p,\n'
                f'.st-key-{k} .stNumberInput label' for k in _bk)
            st.markdown(f"""<style>
            {_sel_input} {{
                text-align: right !important;
                font-size: 1.15rem !important;
            }}
            {_sel_label} {{
                text-align: right !important;
                width: 100% !important;
                display: block !important;
            }}
            </style>""", unsafe_allow_html=True)
            _wf_val = f'<div style="display:flex;justify-content:space-between;padding:6px 0;color:{T["text"]}"><span style="color:{T["text"]};{{extra}}">{{label}}</span><span style="color:{T["text"]};{{extra}}">{{value}}</span></div>'
            _wf_sep = f'<div style="border-top:1px solid {T["separator"]};margin:2px 0"></div>'

            with st.container(key="valuation_bridge_card"):
                st.markdown("#### Valuation Bridge")

                st.markdown(_wf_val.format(label="Enterprise Value", value=f"${_ev:,.0f}",
                                           extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
                st.markdown(_wf_sep, unsafe_allow_html=True)

                # Bridge inputs: adds and subtracts side by side
                _bc1, _bc2, _bc3 = st.columns(3)
                with _bc1:
                    cfg['cash_bridge'] = int(st.number_input(
                        "+ Cash ($M)", value=int(cfg.get('cash_bridge', 0)),
                        step=100, key="ed_cash",
                    ))
                with _bc2:
                    cfg['securities'] = int(st.number_input(
                        "+ Securities ($M)", value=int(cfg.get('securities', 0)),
                        step=100, key="ed_sec",
                    ))
                with _bc3:
                    cfg['equity_investments'] = int(st.number_input(
                        "+ Equity Inv. ($M)", value=int(cfg.get('equity_investments', 0)),
                        step=100, key="ed_eqi",
                    ))
                _cash_sec = cfg['cash_bridge'] + cfg['securities'] + cfg['equity_investments']

                _bc4, _bc5, _bc6 = st.columns(3)
                with _bc4:
                    cfg['debt_market_value'] = int(st.number_input(
                        "\u2212 Debt ($M)", value=int(cfg.get('debt_market_value', 0)),
                        step=100, key="ed_debt",
                    ))
                with _bc5:
                    cfg['minority_interest'] = int(st.number_input(
                        "\u2212 Minority Int. ($M)", value=int(cfg.get('minority_interest', 0)),
                        step=100, key="ed_min",
                    ))
                with _bc6:
                    cfg['unfunded_pension'] = int(st.number_input(
                        "\u2212 Unfunded Pen. ($M)", value=int(cfg.get('unfunded_pension', 0)),
                        step=100, key="ed_pen",
                    ))
                _debt = cfg['debt_market_value'] + cfg['minority_interest'] + cfg['unfunded_pension']

                st.markdown(_wf_sep, unsafe_allow_html=True)
                _equity = _ev + _cash_sec - _debt
                st.markdown(_wf_val.format(label="Equity Value", value=f"${_equity:,.0f}",
                                           extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
                st.markdown(_wf_sep, unsafe_allow_html=True)

                # Shares and margin of safety side by side
                _bc7, _bc9 = st.columns(2)
                with _bc7:
                    cfg['shares_outstanding'] = int(st.number_input(
                        "\u00f7 Shares Outstanding (M)", value=int(cfg.get('shares_outstanding', 0)),
                        step=10, key="ed_shares",
                    ))
                with _bc9:
                    cfg['margin_of_safety'] = st.number_input(
                        "\u00d7 Margin of Safety %", value=int(cfg.get('margin_of_safety', 0.20) * 100),
                        step=5, key="ed_mos",
                    ) / 100
                _intrinsic = _equity / cfg['shares_outstanding'] if cfg['shares_outstanding'] > 0 else 0
                _mos = cfg['margin_of_safety']
                _buy = _intrinsic * (1 - _mos)

                # Store computed values for hero card and watchlist
                cfg['_computed_intrinsic'] = _intrinsic
                cfg['_computed_buy'] = _buy
                cfg['_computed_ev'] = _ev
                cfg['_computed_equity'] = _equity

                # Results summary
                _cur_price = cfg.get('stock_price', 0)
                _upside = (_intrinsic / _cur_price - 1) * 100 if _cur_price > 0 else 0
                _up_color = T['accent'] if _upside >= 0 else T['red']
                _up_label = "upside" if _upside >= 0 else "downside"

                st.markdown(
                    f'<div style="border-top:2px solid {T["border_medium"]};margin:12px 0 8px 0;padding-top:12px">'
                    f'<span style="font-size:1.05rem;font-weight:700;color:{T["text"]}">Result</span></div>',
                    unsafe_allow_html=True,
                )

                _result_html = (
                    f'<div style="display:flex;align-items:baseline;gap:32px;padding:4px 0;flex-wrap:wrap">'
                    f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">Intrinsic Value</span>'
                    f'<br><span style="color:{T["text"]};font-weight:700;font-size:1.4rem">${_intrinsic:,.2f}</span></div>'
                    f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">Buy Price</span>'
                    f'<br><span style="color:{T["accent"]};font-weight:700;font-size:1.4rem">${_buy:,.2f}</span></div>'
                )
                if _cur_price > 0:
                    _result_html += (
                        f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">Current Price</span>'
                        f'<br><span style="color:{T["text"]};font-weight:700;font-size:1.4rem">${_cur_price:,.2f}</span></div>'
                        f'<div><span style="color:{T["text_muted"]};font-size:0.85rem">{_up_label.title()}</span>'
                        f'<br><span style="color:{_up_color};font-weight:700;font-size:1.4rem">{_upside:+.1f}%</span></div>'
                    )
                _result_html += '</div>'
                st.markdown(_result_html, unsafe_allow_html=True)

    with _tab_history:
        with st.container(key="tabcard_history"):
            st.markdown("#### Thesis vs the business")

            # ── How hard is the assumption? ──
            # Answerable today, from the config and EDGAR. The other half —
            # did the company deliver — needs elapsed years, which is why the
            # log below exists at all.
            _hist_cagr = None
            _fund_hl = (cfg.get("fundamentals") or {}).get("headline") or {}
            if _fund_hl.get("revenue_cagr_3y_pct") is not None:
                _hist_cagr = _fund_hl["revenue_cagr_3y_pct"] / 100.0
            elif cfg.get("fundamentals", {}).get("revenue"):
                _rev = [r for r in cfg["fundamentals"]["revenue"] if r]
                if len(_rev) >= 4 and _rev[-4] > 0:
                    _hist_cagr = (_rev[-1] / _rev[-4]) ** (1 / 3) - 1

            _tvh = thesis_vs_history(cfg.get("revenue_growth"), _hist_cagr)
            if _tvh:
                _r = _tvh["ratio"]
                _rtxt = f"{_r:.2f}x" if _r is not None else "n.v.t."
                _rcol = T["red"] if _tvh["heroic"] else T["text"]
                st.markdown(
                    f'<div class="hero-card" style="padding:20px 24px">'
                    f'<div style="display:flex;gap:32px;flex-wrap:wrap">'
                    f'<div><div style="font-size:0.78rem;color:{T["text_muted"]}">'
                    f'You assume</div><div style="font-size:1.5rem;font-weight:700">'
                    f'{_tvh["assumed_cagr"]:.1%}</div>'
                    f'<div style="font-size:0.72rem;color:{T["text_muted"]}">'
                    f'5y revenue CAGR</div></div>'
                    f'<div><div style="font-size:0.78rem;color:{T["text_muted"]}">'
                    f'It has delivered</div><div style="font-size:1.5rem;'
                    f'font-weight:700">{_tvh["delivered_cagr"]:.1%}</div>'
                    f'<div style="font-size:0.72rem;color:{T["text_muted"]}">'
                    f'3y revenue CAGR</div></div>'
                    f'<div><div style="font-size:0.78rem;color:{T["text_muted"]}">'
                    f'Ratio</div><div style="font-size:1.5rem;font-weight:700;'
                    f'color:{_rcol}">{_rtxt}</div>'
                    f'<div style="font-size:0.72rem;color:{T["text_muted"]}">'
                    f'above {HEROIC_RATIO:.1f}x rests on a break in trend</div>'
                    f'</div></div></div>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    "Under 1x means the model underwrites less than the company "
                    "has been doing, which is what lets a fair value survive a "
                    "bad year. It is not automatically better — it can also mean "
                    "leaving the case unmade."
                )
            else:
                st.info("No revenue history to compare the growth path against yet.")

            # ── Revisions ──
            st.markdown("")
            st.markdown("##### Revisions")
            _alog = cfg.get(ASSUMPTION_LOG_KEY) or []
            if len(_alog) < 2:
                st.caption(
                    "Each time the growth path, margins or base year change, the "
                    "previous thesis is kept here with the fair value it produced. "
                    "Rebuilding a DCF used to overwrite them, so there was no way "
                    "to ask afterwards whether the business delivered what you "
                    "assumed. Recording starts from the first save after this — "
                    "there is nothing from before it to recover."
                )
            if _alog:
                _rows = ""
                _prev_fv = None
                for _e in reversed(_alog):
                    _g = _e.get("revenue_growth") or []
                    _gtxt = (f'{_g[0]:.1%} → {_g[-1]:.1%}' if _g else "—")
                    _fv = _e.get("fv_mid")
                    _fvtxt = f"${_fv:,.2f}" if _fv else "—"
                    _chg = ""
                    if _fv and _prev_fv:
                        _d = (_prev_fv / _fv - 1) * 100
                        _chg = (f'<span style="color:'
                                f'{T["red"] if _d > 0 else T["accent"]}">'
                                f'{_d:+.1f}%</span>')
                    _prev_fv = _fv or _prev_fv
                    _td = f'padding:6px 10px;border-top:1px solid {T["divider"]}'
                    _rows += (
                        f'<tr><td style="{_td}">{_e.get("as_of", "—")}</td>'
                        f'<td style="{_td}">{_e.get("base_year", "—")}</td>'
                        f'<td style="{_td}">{_gtxt}</td>'
                        f'<td style="{_td};text-align:right">{_fvtxt}</td>'
                        f'<td style="{_td};text-align:right">{_chg}</td></tr>'
                    )
                _th = (f'padding:6px 10px;color:{T["text_muted"]};'
                       f'font-weight:600;text-align:left')
                st.markdown(
                    f'<table style="width:100%;border-collapse:collapse;'
                    f'font-size:0.85rem"><thead><tr>'
                    f'<th style="{_th}">Set on</th><th style="{_th}">Base year</th>'
                    f'<th style="{_th}">Growth path</th>'
                    f'<th style="{_th};text-align:right">Fair value</th>'
                    f'<th style="{_th};text-align:right">vs next</th>'
                    f'</tr></thead><tbody>{_rows}</tbody></table>',
                    unsafe_allow_html=True,
                )
                if len(_alog) > 1:
                    st.caption(
                        "The last column is how each fair value compares to the one "
                        "that replaced it. A mid-point that keeps climbing toward "
                        "the price says more about the model than the company."
                    )

    # ── Action buttons ──
    st.markdown("---")
    btn1, btn3 = st.columns(2)
    with btn1:
        if st.button("Save", key="ed_save", width="stretch", type="primary"):
            # Pick up any unsubmitted peer ticker from the add-peer text field
            _pending_peer = (st.session_state.get("ed_add_peer") or "").strip()
            if _pending_peer:
                _pending = [t for t in (sanitize_ticker(x) for x in _pending_peer.split(",")) if t]
                _existing_t = {p.get("ticker") for p in cfg.get('peers', [])}
                _to_fetch_t = [t for t in _pending if t not in _existing_t and t != ticker]
                if _to_fetch_t:
                    with st.spinner(f"Fetching data for {', '.join(_to_fetch_t)}..."):
                        _added = fetch_peer_data(_to_fetch_t)
                    if _added:
                        cfg.setdefault('peers', []).extend(_added)
            save_config(_sb_client, ticker, cfg)
            st.success(f"{ticker} saved")
            st.rerun()
    with btn3:
        if st.button("Remove from Watchlist", key="ed_remove", width="stretch", type="primary"):
            remove_from_watchlist(_sb_client, ticker)
            del st.query_params["edit"]
            st.rerun()

    # Auto-save config at end of every editor render
    save_config(_sb_client, ticker, cfg)

    # ── Fill hero card placeholder ──
    # DCF FV / Buy / Upside pills removed in favour of multi-lens (richer
    # signal) + Avg ROCE/ROE + FCF Yield (quality + cash-yield context).

    # Multi-lens summary pills (only render if valuation_summary present)
    _ml = cfg.get('valuation_summary') or {}
    _ml_mid = _ml.get('weighted_fv_mid')
    _ml_low = _ml.get('weighted_fv_low')
    _ml_high = _ml.get('weighted_fv_high')
    _ml_buy = _ml.get('buy_price')
    _ml_pills = ''
    if _ml_mid and _ml_buy and live_price > 0:
        _ml_upside = (_ml_mid / live_price - 1)
        _ml_up_color = T['accent'] if _ml_upside >= 0 else T['red']
        _ml_up_sign = "+" if _ml_upside >= 0 else ""
        _range_txt = ''
        if _ml_low and _ml_high:
            _range_txt = (
                f' <span style="color:{T["text_muted"]};font-size:0.78em">'
                f'(${_ml_low:.0f}–${_ml_high:.0f})</span>'
            )
        _ml_pills = (
            f'<span class="stat-pill">Multi-lens FV <b>${_ml_mid:.2f}</b>{_range_txt}</span>'
            f'<span class="stat-pill">ML Buy <b>${_ml_buy:.2f}</b></span>'
            f'<span class="stat-pill">ML Upside <b style="color:{_ml_up_color}">'
            f'{_ml_up_sign}{_ml_upside:.1%}</b></span>'
        )


    # Quality + cash-yield pills (Avg ROCE/ROE + FCF Yield). Re-uses
    # `fund` already loaded in the Fundamentals tab body above. Wrapped
    # in try/except so the hero card still renders if fundamentals
    # fetching failed for this ticker.
    _quality_pills = ''
    try:
        _h_fund = fund if isinstance(fund, dict) and fund.get('years') else None
    except NameError:
        _h_fund = None
    if _h_fund:
        # Shared single source of truth — same metric/value as the watchlist.
        _h_metric_label, _h_metric_val = compute_roce_metric(_h_fund, cfg)
        if _h_metric_val is not None:
            _h_roce_color = (
                T['accent'] if _h_metric_val >= 20
                else (T['red'] if _h_metric_val < 10 else T['text'])
            )
            _quality_pills += (
                f'<span class="stat-pill">Avg {_h_metric_label} '
                f'<b style="color:{_h_roce_color}">{_h_metric_val:.1f}%</b></span>'
            )
        # FCF Yield (most recent year FCF / current market cap)
        _h_fcf_list = [v for v in (_h_fund.get('fcf') or []) if v is not None]
        if _h_fcf_list:
            _h_fcf_latest = _h_fcf_list[-1]
            _h_mc = cfg.get('equity_market_value', 0) or 0  # in $M
            if _h_mc > 0:
                _h_fcfy = _h_fcf_latest / _h_mc
                _h_fcfy_color = (
                    T['accent'] if _h_fcfy >= 0.05
                    else (T['red'] if _h_fcfy < 0.02 else T['text'])
                )
                _quality_pills += (
                    f'<span class="stat-pill">FCF Yield '
                    f'<b style="color:{_h_fcfy_color}">{_h_fcfy:.1%}</b></span>'
                )

    # Verdict pill (from scorecard JSON in ai_notes) — plain text, inherits pill style
    _sc_pills = ''
    _ai_notes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else None
    _sc_raw = (_ai_notes or {}).get('Scorecard', '') if _ai_notes else ''
    _sc_data = _parse_scorecard_json(_sc_raw) if _sc_raw else None
    if _sc_data:
        _verdict_str = (_sc_data.get('verdict') or '').lower()
        _verdict_label_map = {
            'deep_dive': 'Deep Dive',
            'revisit': 'Revisit',
            'pass': 'Pass',
        }
        _v_label = _verdict_label_map.get(_verdict_str)
        if _v_label:
            _sc_pills = f'<span class="stat-pill">Verdict <b>{_v_label}</b></span>'

    _hero_placeholder.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">{_prettify_company(cfg.get("company", ticker))}</p>'
        f'<div style="display:flex;align-items:center;justify-content:center;gap:12px">'
        + _logo_img(ticker, cfg.get("isin"), "",
                    "width:36px;height:36px;border-radius:50%;object-fit:cover")
        + 
        f'<p class="hero-value" style="font-size:2rem;margin:0">{ticker}</p>'
        f'</div>'
        f'<div class="stat-row">'
        f'<span class="stat-pill">Price <b>${live_price:.2f}</b></span>'
        f'{_ml_pills}'
        f'{_quality_pills}'
        f'{_sc_pills}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def run_analysis(ticker, peer_mode, manual_peers, margin_of_safety, terminal_growth):
    """Run the full DCF pipeline and return (excel_bytes, cfg, credit_rating)."""

    buf = io.StringIO()
    _cr = "N/A"

    with st.status("Analyzing " + ticker + "...", expanded=True) as status:
        pos = 0

        # ── Step 1: Company lookup ──
        status.write("\u23f3 Looking up company in SEC EDGAR...")
        with contextlib.redirect_stdout(buf):
            cik = get_cik(ticker)
            time.sleep(0.2)
            submissions = fetch_company_submissions(cik)
        company_name = submissions.get("name", ticker)
        sic_code = int(submissions.get("sic", 0))
        sic_desc = submissions.get("sicDescription", "")
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 **{company_name}** — {sic_desc}")

        # ── Step 2: Sector betas ──
        status.write("\u23f3 Determining sector & beta...")
        with contextlib.redirect_stdout(buf):
            sector_betas = resolve_sector_betas(sic_code, sic_desc)
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 Sector: {sector_betas[0][0]} (beta {sector_betas[0][1]:.2f})")

        # ── Step 3: Financials ──
        status.write("\u23f3 Fetching financial statements from EDGAR...")
        with contextlib.redirect_stdout(buf):
            facts = fetch_company_facts(cik)
            financials = parse_financials(facts, n_years=6, ticker=ticker)
            # ADR tickers: convert ordinary shares to ADR-equivalent so market
            # cap / per-share line up with the ADR price. getattr keeps this a
            # no-op if an older gather_data module is loaded (avoids hard fail).
            _apply_adr = getattr(gather_data, "apply_adr_share_ratio", None)
            if _apply_adr and financials.get("shares"):
                financials["shares"] = _apply_adr(financials["shares"], ticker)
        pos = _flush_clean(buf, pos, status)
        years = financials.get("years", [])
        if years:
            status.write(f"\u2705 {len(years)} years of data ({years[0]}–{years[-1]})")
        else:
            status.write(f"\u2705 Financial data loaded")

        # ── Step 4: Market data ──
        status.write("\u23f3 Fetching market data...")
        with contextlib.redirect_stdout(buf):
            stock_price, market_cap, shares_yahoo = fetch_stock_price(ticker)
            risk_free_rate = fetch_treasury_yield()
        pos = _flush_clean(buf, pos, status)
        if risk_free_rate is None:
            # Zichtbaar terugvallen: deze waarde gaat als discontovoet de
            # config in, dus de gebruiker moet weten dat hij niet gemeten is.
            risk_free_rate = RISK_FREE_RATE_DEFAULT
            status.write(f"\u26a0\ufe0f ${stock_price:.2f} per share — Treasury-feed onbereikbaar, "
                         f"aanname {risk_free_rate:.2%} gebruikt (RISK_FREE_RATE_DEFAULT)")
        else:
            status.write(f"\u2705 ${stock_price:.2f} per share — 10Y Treasury: {risk_free_rate:.2%}")

        # ── Step 5: Credit rating + sector margin + consensus ──
        status.write("\u23f3 Analyzing credit, margins & analyst estimates...")
        with contextlib.redirect_stdout(buf):
            oi_latest = financials["operating_income"][-1] if financials["operating_income"] else 0
            ie_latest = financials["interest_expense_latest"]
            credit_rating, credit_spread = synthetic_credit_rating(oi_latest, ie_latest)
            _cr = credit_rating

            if market_cap == 0 and stock_price > 0:
                edgar_shares = financials["shares"][-1] if financials["shares"] and financials["shares"][-1] and financials["shares"][-1] > 0 else 0
                if edgar_shares > 0:
                    market_cap = round(stock_price * edgar_shares, 0)

            sector_margin = None
            sector_name_for_margin = sector_betas[0][0] if sector_betas else ""
            if sector_name_for_margin:
                dam_margins = fetch_sector_margins()
                if dam_margins:
                    if sector_name_for_margin in dam_margins:
                        sector_margin = dam_margins[sector_name_for_margin]
                    else:
                        target_words = set(sector_name_for_margin.lower().replace("/", " ").split())
                        best_m, best_s = None, 0
                        for sec_name, sec_margin in dam_margins.items():
                            sec_words = set(sec_name.lower().replace("/", " ").split())
                            overlap = len(target_words & sec_words)
                            if overlap > best_s:
                                best_s = overlap
                                best_m = (sec_name, sec_margin)
                        if best_m and best_s > 0:
                            sector_margin = best_m[1]
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 Credit: {credit_rating} (spread {credit_spread:.2%})")

        # ── Step 6: Peers ──
        peers = []
        peer_tickers = []
        if peer_mode == "Auto-discover":
            # Peer auto-selection removed — no peers on a fresh analysis.
            pass
        elif peer_mode == "Manual" and manual_peers:
            peer_tickers = [t.strip().upper() for t in manual_peers.split(",") if t.strip()]

        if peer_tickers:
            status.write(f"\u23f3 Gathering data for {len(peer_tickers)} peers...")
            with contextlib.redirect_stdout(buf):
                peers = fetch_peer_data(peer_tickers)
            pos = _flush_clean(buf, pos, status)
            status.write(f"\u2705 {len(peers)} peer profiles loaded")
        elif peer_mode != "None":
            status.write("\u2705 No peers found")
        else:
            status.write("\u2705 Peer analysis skipped")

        # ── Step 7: Build config ──
        status.write("\u23f3 Building DCF model assumptions...")
        with contextlib.redirect_stdout(buf):
            cfg = build_config(
                ticker=ticker,
                financials=financials,
                stock_price=stock_price,
                market_cap=market_cap,
                shares_yahoo=shares_yahoo,
                risk_free_rate=risk_free_rate,
                sector_betas=sector_betas,
                credit_spread=credit_spread,
                credit_rating=credit_rating,
                peers=peers,
                company_name=company_name,
                margin_of_safety=margin_of_safety,
                terminal_growth=terminal_growth,
                sector_margin=sector_margin,
            )
        pos = _flush_clean(buf, pos, status)

        # Validate: enough data to build a meaningful DCF?
        base_rev = cfg.get("base_revenue", 0)
        base_year = cfg.get("base_year", 0)
        if base_rev <= 0:
            raise ValueError(
                f"{company_name} has no revenue data (or $0). "
                "A DCF model requires a company with revenue history."
            )
        if base_year < 2018:
            raise ValueError(
                f"{company_name}'s most recent filing is from {base_year}. "
                "The data is too old for a meaningful DCF analysis."
            )
        if market_cap <= 0 and stock_price <= 0:
            raise ValueError(
                f"Could not determine market cap or stock price for {ticker}. "
                "The company may be delisted or have no trading data."
            )

        status.write(f"\u2705 Configuration complete")

        # ── Step: the watchlist's EDGAR slice ──
        # A new ticker arrives here with no slice, so the watchlist would fall
        # back to a live 5 MB fetch for it on every cold load until the next
        # Refresh All. One deliberate fetch now costs a second inside an
        # operation that already takes several.
        #
        # Deliberately NOT reusing `financials` from step 3: that comes from
        # parse_financials, which returns six years and share counts in
        # millions, while the watchlist's arithmetic expects ten years and a
        # raw count. Mixing the two put every EDGAR-derived FCF yield at 0.0%
        # once already.
        status.write("\u23f3 Caching fundamentals for the watchlist...")
        try:
            with contextlib.redirect_stdout(buf):
                _slice = slim_fundamentals(fetch_fundamentals(ticker, n_years=10))
            if _slice:
                cfg["fund_slice"] = _slice
                status.write("\u2705 Fundamentals cached")
            else:
                status.write("\u26a0\ufe0f No EDGAR series to cache \u2014 the "
                             "watchlist will fetch this one live")
        except Exception as _e:
            # Never let this cost the analysis that just succeeded; the
            # watchlist simply falls back to fetching this ticker itself.
            logger.warning("Slice build failed for %s: %s", ticker, _e)
            status.write("\u26a0\ufe0f Could not cache fundamentals \u2014 the "
                         "watchlist will fetch this one live")
        _flush_clean(buf, pos, status)

        status.update(label=f"Analysis complete — {company_name} ({ticker})", state="complete", expanded=False)

    return cfg, _cr


# ══════════════════════════════════════════════════════
#  SIDEBAR — Navigation + page-specific settings
# ══════════════════════════════════════════════════════

# Eagerly load credentials into session_state so has_active_broker() works
_tt = _get_tt_token()
_ibkr = _get_ibkr_credentials()
logger.debug("Broker check: tt_token=%s ibkr_creds=%s", bool(_tt), bool(_ibkr))

with st.sidebar:
    st.toggle("Dark mode", key="dark_mode")

    def _on_nav_change():
        """Clear account page override when user clicks a main nav item."""
        st.session_state.pop("_account_page", None)

    _all_pages = ["Portfolio", "Holdings", "Results", "Watchlist", "Screener"]

    # CSS to add a visual separator after "Results" (3rd item)
    st.markdown(
        '<style>'
        '.st-key-nav_radio [role="radiogroup"] > label:nth-child(3) {'
        f'  border-bottom: 1px solid {T["separator"]};'
        '  padding-bottom: 8px;'
        '  margin-bottom: 4px;'
        '}'
        '</style>',
        unsafe_allow_html=True,
    )

    # Apply pending navigation from quick-link buttons (can't set widget key
    # after the widget is rendered, so we apply it before via default)
    if "_pending_nav" in st.session_state:
        st.session_state["nav_radio"] = st.session_state.pop("_pending_nav")

    _nav = st.radio(
        "Navigate",
        _all_pages,
        label_visibility="collapsed",
        key="nav_radio",
        on_change=_on_nav_change,
    )
    # ── Handle OAuth redirect (before page routing) ──
    _tt_connected = st.query_params.get("tt_connected")
    _tt_error = st.query_params.get("tt_error")
    if _tt_connected or _tt_error:
        st.session_state["_account_page"] = "Connect your Broker"
        # Persist OAuth result in session state so it survives the rerun
        # triggered by st.query_params.clear() (Streamlit ≥1.37)
        if _tt_connected:
            st.session_state["_tt_oauth_result"] = "success"
        else:
            st.session_state["_tt_oauth_result"] = _tt_error
        st.query_params.clear()

    page = st.session_state.get("_account_page") or _nav

    # ── Page view tracking ──
    if st.session_state.get("_last_page") != page:
        st.session_state["_last_page"] = page
        log_page_view(_sb_client, page)

    # ── Broker switcher (only if multiple brokers connected) ──
    _connected = []
    if st.session_state.get("tt_refresh_token"):
        _connected.append(("Tastytrade", "tastytrade"))
    if st.session_state.get("ibkr_credentials"):
        _connected.append(("Interactive Brokers", "ibkr"))
    if st.session_state.get("t212_credentials"):
        _connected.append(("Trading 212", "t212"))

    # No sidebar switcher any more: Portfolio, Holdings and Results each
    # carry their own Overview / per-broker tabs, and Watchlist and Cashflow
    # Champions touch no broker data at all. A second control for the same
    # thing only invites picking the one that doesn't apply.
    if len(_connected) == 1:
        st.session_state["active_broker"] = _connected[0][1]

    st.markdown("---")

    if page in ("Portfolio", "Holdings", "Results"):
        _broker_label = BROKER_NAMES.get(get_active_broker(), "Tastytrade")
        # The Portfolio page has its own broker view, so name what is actually
        # on screen — the sidebar reading "Tastytrade" above a combined table
        # would be a label that contradicts the page.
        if page in ("Portfolio", "Holdings", "Results") and len(_connected) > 1:
            # The widget's own key, not the copy the page writes afterwards:
            # the sidebar renders before the page body, so the copy would show
            # the previous selection for one interaction.
            _broker_label = (st.session_state.get(f"broker_view_{page}")
                             or st.session_state.get("_portfolio_view")
                             or "Overview")
        st.markdown(f"### {_broker_label}")
        if st.button("Refresh Data", width="stretch", type="primary"):
            st.session_state.pop("portfolio_data", None)
            st.session_state.pop("portfolio_account", None)
            st.session_state.pop("portfolio_prices", None)
            st.session_state.pop("net_liq_all", None)
            st.session_state.pop("yearly_transfers", None)
            st.session_state.pop("benchmark_returns", None)
            st.session_state.pop("portfolio_fetched_at", None)
            st.session_state.pop("_ibkr_flex_cache", None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()

        if st.button("Clear Session Data", width="stretch", type="primary"):
            _preserve = {"dark_mode", "nav_radio", "_account_page",
                         "supabase_client", "user", "_user_id", "tt_refresh_token",
                         "ibkr_credentials", "active_broker"}
            for key in [k for k in st.session_state if k not in _preserve]:
                del st.session_state[key]
            st.rerun()

    st.markdown("---")

    def _on_acct_change():
        """Map account radio selection to _account_page."""
        sel = st.session_state.get("_acct_radio")
        if sel == "Connect your Broker":
            st.session_state["_account_page"] = "Connect your Broker"
        elif sel == "Security & Privacy":
            st.session_state["_account_page"] = "🔒 Security & Privacy"
        elif sel == "Sign Out":
            logout()

    _acct_default = None
    _active_acct = st.session_state.get("_account_page", "")
    if _active_acct == "Connect your Broker":
        _acct_default = 0
    elif _active_acct == "🔒 Security & Privacy":
        _acct_default = 1

    st.radio(
        "Account",
        ["Connect your Broker", "Security & Privacy", "Sign Out"],
        index=_acct_default,
        label_visibility="collapsed",
        key="_acct_radio",
        on_change=_on_acct_change,
    )

    try:
        from assets.logo_footer_b64 import LOGO_FOOTER_B64
        _dark = st.session_state.get("dark_mode", False)
        _filter = "filter: invert(1) brightness(2);" if _dark else ""
        st.markdown(
            f'<style>'
            f'.lt-sidebar-footer {{'
            f'  position: fixed; bottom: 16px; text-align: center;'
            f'  width: var(--sidebar-width, 245px); left: 0; opacity: 0.5;'
            f'  pointer-events: none;'
            f'}}'
            f'</style>'
            f'<div class="lt-sidebar-footer">'
            f'<img src="data:image/png;base64,{LOGO_FOOTER_B64}" '
            f'style="width: 36px; {_filter}" />'
            f'</div>',
            unsafe_allow_html=True,
        )
    except ImportError:
        pass


# ══════════════════════════════════════════════════════
#  SHARED DATA LOADING FOR PORTFOLIO PAGES
# ══════════════════════════════════════════════════════

def _broker_view_control(page_key):
    """Render the Overview / per-broker picker and return the choice.

    Shared by the Portfolio and Holdings pages so the two never disagree
    about which account is on screen. It replaced the sidebar's Active Broker
    box entirely — two controls for one thing invites picking the one that
    doesn't apply.

    The widget key is per page — Streamlit drops widget state for a widget that
    isn't rendered — while `_portfolio_view` carries the choice between them so
    switching pages keeps the same account selected.
    """
    names = [BROKER_NAMES[b] for b in connected_brokers()]
    if len(names) < 2:
        return "Overview"
    view = st.segmented_control(
        "Broker view",
        ["Overview", *names],
        default=st.session_state.get("_portfolio_view", "Overview"),
        key=f"broker_view_{page_key}",
        label_visibility="collapsed",
    ) or "Overview"
    st.session_state["_portfolio_view"] = view
    if view != "Overview":
        # Anything still single-broker further down the page (margin, Greeks,
        # the option chain) reads the active broker, so point it at the account
        # the tab names.
        picked = next((k for k, v in BROKER_NAMES.items() if v == view), None)
        if picked and st.session_state.get("active_broker") != picked:
            st.session_state["active_broker"] = picked
    return view


def _portfolio_isins(rows):
    """{symbol: isin} voor de rijen waar de Xetra-bron zin heeft.

    Alleen niet-dollarlijnen met een broker-koers: de Amerikaanse namen kennen
    Tastytrade en Yahoo al, en een Xetra-notering van een US-aandeel zou een
    EUR-koers onder een dollarteken zetten. Zonder broker-koers is er niets om
    de dagbeweging op toe te passen, zie _row_quote. `symbol` en niet de
    sleutel: met twee brokers heet die "DECK (Trading 212)".
    """
    return {
        d.get("symbol", t): d["isin"] for t, d in rows.items()
        if d.get("isin") and d.get("broker_price")
        and (d.get("native_currency") or "USD").upper() != "USD"
    }


def _row_quote(price_data, data):
    """De quote voor een positierij, in de eenheid van de pagina (USD).

    Een quote met `venue` komt van Xetra en noteert in EUR, terwijl de rij
    van Trading 212 al een USD-koers draagt. Die blijft de koers; de beurs
    levert alleen de dagbeweging als verhouding vorige slot / laatste koers,
    en een verhouding heeft geen valuta. Tot 2026-09-21 kreeg zo'n rij de
    broker-koers als vorige slot mee en stond Day % op +0,00% -- een storing
    die als "onveranderd" las.

    Zonder quote van buiten valt de rij terug op de broker-koers zonder
    dagbeweging. Yahoo zoekt op de kale naam en kent "RMS" of "IEQU" niet, en
    een Europese ETF viel zo op $0 marktwaarde, met 100% van het gewicht voor
    de andere posities. Niets van beide: None, en de aanroeper zet 0.
    """
    broker_price = data.get("broker_price")
    if price_data and price_data.get("venue") and broker_price:
        last = price_data.get("price") or 0
        prev = price_data.get("previousClose")
        ratio = prev / last if prev and last else 1.0
        return {"price": broker_price, "previousClose": broker_price * ratio}
    if price_data:
        return price_data
    if broker_price:
        return {"price": broker_price, "previousClose": broker_price}
    return None


def _load_portfolio_data():
    """Fetch and enrich portfolio data (cached in session_state, auto-refreshes every 5 min)."""
    # Auto-refresh after 5 minutes
    fetched_at = st.session_state.get("portfolio_fetched_at", 0)
    if "portfolio_data" in st.session_state and time.time() - fetched_at > 300:
        for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                   "net_liq_all", "yearly_transfers", "benchmark_returns"]:
            st.session_state.pop(k, None)
        for k in [k for k in st.session_state if k.startswith("net_liq_")]:
            st.session_state.pop(k, None)

    if "portfolio_data" not in st.session_state:
        _active_broker = get_active_broker()
        _broker_names = [BROKER_NAMES[b] for b in connected_brokers()]
        _broker_name = " + ".join(_broker_names) or "your broker"
        with st.spinner(f"Fetching portfolio data from {_broker_name}..."):
            try:
                # Every connected broker, not just the active one: money moving
                # from Tastytrade to Trading 212 lives at both for a while, and
                # a portfolio that shows one of them is wrong in the only way
                # that matters.
                cost_basis, acct, _failures = fetch_all_portfolio_data()
                st.session_state.portfolio_data = cost_basis
                st.session_state.portfolio_account = acct
                st.session_state.portfolio_broker_failures = [
                    (n, str(e)) for n, e in _failures
                ]
                st.session_state.portfolio_fetched_at = time.time()
            except Exception as e:
                logger.error("Portfolio fetch failed: %s", e, exc_info=True)
                log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"broker": get_active_broker()})
                st.error(f"Failed to fetch portfolio data. Please try again. ({type(e).__name__})")
                st.stop()

        # An expired session no longer aborts the whole fetch — it arrives as
        # one broker's failure. Still clear that broker's token so the user
        # gets the reconnect prompt instead of a silently short portfolio.
        _cred_key = {"t212": "t212_credentials", "ibkr": "ibkr_credentials",
                     "tastytrade": "tt_refresh_token"}
        for _bname, _err in _failures:
            _bkey = next((k for k, v in BROKER_NAMES.items() if v == _bname), None)
            if _is_auth_error(_err):
                logger.warning("%s auth failed — clearing token so user can reconnect", _bname)
                log_error("AUTH_ERROR", "Broker session expired", page="Portfolio", metadata={"broker": _bkey})
                st.session_state.pop(_cred_key.get(_bkey, ""), None)
            else:
                logger.error("%s portfolio fetch failed: %s", _bname, _err)
                log_error_with_trace("PORTFOLIO_ERROR", _err, page="Portfolio", metadata={"broker": _bkey})

    # Re-rendered on every run, not just the fetching one: a missing broker
    # makes every total on this page a floor rather than the answer, and that
    # has to stay on screen for as long as it is true.
    for _bname, _msg in st.session_state.get("portfolio_broker_failures", []):
        st.warning(f"Could not reach {_bname} — its positions are missing here, "
                   f"so the totals below are incomplete. ({_msg})")

    cost_basis = st.session_state.portfolio_data
    acct = st.session_state.get("portfolio_account", "")

    if not cost_basis:
        st.info("No transactions found.")
        st.stop()

    if "portfolio_prices" not in st.session_state:
        # d["symbol"] rather than the dict key: with two brokers the key may
        # carry a broker suffix ("DECK (Trading 212)") and Yahoo has never
        # heard of that.
        active_tickers = sorted({
            d.get("symbol", t) for t, d in cost_basis.items()
            if d["shares_held"] > 0 or _has_open_options(d)
        })
        if active_tickers:
            with st.spinner("Fetching current prices..."):
                # Met de ISIN-kaart: zonder die valt de tweede koersbron voor
                # de Europese lijnen stil, en RMS en IEQU stonden op +0,00%.
                st.session_state.portfolio_prices = fetch_current_prices(
                    active_tickers, _portfolio_isins(cost_basis))
                st.session_state["portfolio_prices_at"] = time.time()
        else:
            st.session_state.portfolio_prices = {}

    prices = st.session_state.portfolio_prices

    for ticker, data in cost_basis.items():
        price_data = _row_quote(prices.get(data.get("symbol", ticker)), data)
        shares = data["shares_held"]

        if price_data and shares > 0:
            price = price_data["price"]
            data["current_price"] = price
            data["previous_close"] = price_data.get("previousClose") or price
            data["market_value"] = price * shares
            data["total_pl_real"] = data["total_pl"] + data["market_value"]
        elif price_data:
            # Options-only position — store underlying price for reference
            data["current_price"] = price_data["price"]
            data["previous_close"] = price_data.get("previousClose") or price_data["price"]
            data["market_value"] = 0.0
            data["total_pl_real"] = data["total_pl"]
        else:
            data["current_price"] = 0.0
            data["previous_close"] = 0.0
            data["market_value"] = 0.0
            data["total_pl_real"] = data["total_pl"]

    return cost_basis


def _to_time_col(values):
    """Parse a net-liq "time" column that may hold more than one format.

    Brokers stamp differently — Tastytrade an ISO timestamp, sometimes tz-aware,
    Trading 212 a bare date — and a merged or cached series can carry both.
    pandas infers one format from the first element and then raises on the
    rest, which took the whole Results page down. format="mixed" parses each
    element on its own; the timezone is dropped because everything downstream
    groups by day and year.
    """
    parsed = pd.to_datetime(values, format="mixed", utc=True)
    return parsed.dt.tz_localize(None)


def _verdict_card_html(content, title=""):
    """A summary card for a verdict-shaped pre-scan section, or None.

    None means "not in that shape" — sections written under the older, longer
    templates fall through to plain markdown rather than being squeezed into a
    card that would only half fill.
    """
    v = parse_verdict_section(content)
    if not v:
        return None

    # Colour reads the verdict, not the number, because the two scales run
    # opposite ways: a high moat score is good and a high risk rating is not.
    # band_tone owns the whole vocabulary — this used to keep its own small
    # lookup that knew wide/narrow/none and painted everything else green,
    # which is how "Mixed" came out as a pass.
    tone = band_tone(v["label"]) or T["text_muted"]

    # The dial. An arc rather than a bar because the score is a position on a
    # scale, not a quantity — and a word where there is no number, since Risk
    # has three levels and a 0-5 dial would invent precision it does not have.
    if v["score"] is not None:
        _r, _c = 46, 52
        _circ = 2 * 3.14159 * _r
        _fill = gauge_fraction(v["score"], v["out_of"]) * _circ * 0.75
        _score_txt = f'{v["score"]:g}'
        dial = (
            f'<svg viewBox="0 0 104 104" style="width:104px;height:104px">'
            f'<circle cx="{_c}" cy="{_c}" r="{_r}" fill="none" '
            f'stroke="rgba(255,255,255,0.16)" stroke-width="9" '
            f'stroke-dasharray="{_circ * 0.75:.1f} {_circ}" stroke-linecap="round" '
            f'transform="rotate(135 {_c} {_c})"/>'
            f'<circle cx="{_c}" cy="{_c}" r="{_r}" fill="none" stroke="{tone}" '
            f'stroke-width="9" stroke-dasharray="{_fill:.1f} {_circ}" '
            f'stroke-linecap="round" transform="rotate(135 {_c} {_c})"/>'
            f'<text x="{_c}" y="{_c + 10}" text-anchor="middle" fill="#fff" '
            f'font-size="30" font-weight="700">{_score_txt}</text></svg>'
        )
    else:
        dial = (f'<div style="font-size:1.6rem;font-weight:700;color:{tone};'
                f'padding:22px 0">{_html_escape(v["label"])}</div>')

    _cap = _html_escape(v["label"].upper() if v["score"] is not None
                        else " · ".join(v["qualifiers"]) or "")
    bullets = "".join(
        f'<li style="margin-bottom:10px;line-height:1.5">'
        f'<b>{_html_escape(b["label"])}</b>'
        f'{": " + _html_escape(b["text"]) if b["text"] else ""}</li>'
        for b in v["bullets"]
    )
    footer = ""
    if v["footer_text"]:
        footer = (
            f'<div style="margin-top:14px;padding-top:12px;'
            f'border-top:1px solid {T["divider"]};font-size:0.85rem;'
            f'color:{T["text_muted"]}">'
            + (f'<b style="color:{T["text"]}">{_html_escape(v["footer_label"])}:</b> '
               if v["footer_label"] else "")
            + _html_escape(v["footer_text"]) + '</div>'
        )
    quals = ""
    if v["qualifiers"] and v["score"] is not None:
        quals = (f'<span style="font-size:0.8rem;color:{T["text_muted"]};'
                 f'margin-left:8px">{_html_escape(" · ".join(v["qualifiers"]))}</span>')

    return (
        f'<div style="background:{T["bg_secondary"]};border-radius:16px;'
        f'padding:20px 22px">'
        f'<p style="margin:0 0 16px 0;font-size:1.02rem;line-height:1.55;'
        f'color:{T["text"]}">{_md_bold(v["summary"])}{quals}</p>'
        f'<div style="display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap">'
        f'<div style="background:#2b2b2f;border-radius:14px;padding:14px 18px;'
        f'text-align:center;min-width:132px">{dial}'
        f'<div style="color:rgba(255,255,255,0.72);font-size:0.7rem;'
        f'font-weight:700;letter-spacing:0.08em;margin-top:2px">{_cap}</div></div>'
        f'<ul style="flex:1;min-width:240px;margin:2px 0 0 0;padding-left:20px;'
        f'color:{T["text"]}">{bullets}</ul>'
        f'</div>{footer}</div>'
    )


def _html_escape(text):
    import html as _h
    return _h.escape(str(text or ""))


def _md_bold(text):
    """Render **bold** inside an otherwise escaped sentence."""
    import re as _re
    out = _html_escape(text)
    return _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)


# Which parqet logo URLs actually serve, cached a day. A module-level dict
# rather than @st.cache_data because the answers get filled in from worker
# threads, and because "does this public logo exist" is not user data — one
# container's answer is every session's answer.
_LOGO_OK_TTL = 86400.0
_LOGO_OK: dict = {}


def _logo_head(url):
    """Does parqet serve this logo?

    The earlier server-side check failed and was abandoned for the wrong
    reason: parqet answers 404 to anything without a browser User-Agent, so
    the check itself was broken, not the idea. With the UA it is reliable —
    and it has to be server-side, because Streamlit strips onerror handlers
    from rendered HTML, so a browser-side fallback never runs and a 404 shows
    a broken-image icon. That is how MNST looked wrong.

    On a network error assume it resolves: hiding every logo over one slow
    moment punishes the wrong thing.
    """
    import requests
    try:
        return requests.head(url, timeout=3, allow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"}
                             ).status_code == 200
    except Exception:
        return True


def _logo_resolves(url):
    """Cached single lookup. Falls back to a live check on a cache miss."""
    hit = _LOGO_OK.get(url)
    if hit and (time.time() - hit[0]) < _LOGO_OK_TTL:
        return hit[1]
    ok = _logo_head(url)
    _LOGO_OK[url] = (time.time(), ok)
    return ok


def prewarm_logos(symbols):
    """Resolve many logo URLs at once, before anything renders.

    One at a time these cost about 60ms each and nothing else is happening
    while they run: 117 of them was 7.1 of the Screener's 8.6 seconds, 81 was
    5.4 of the Watchlist's 13.7. The work is entirely waiting on someone
    else's server, so it belongs in threads.

    Symbols already answered stay answered — this only fills the gaps, so a
    second page load costs nothing.
    """
    todo = []
    now = time.time()
    for s in {s for s in symbols if s}:
        url = f"https://assets.parqet.com/logos/symbol/{s}"
        hit = _LOGO_OK.get(url)
        if not hit or (now - hit[0]) >= _LOGO_OK_TTL:
            todo.append(url)
    if not todo:
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(12, len(todo))) as pool:
        for url, ok in zip(todo, pool.map(_logo_head, todo)):
            _LOGO_OK[url] = (time.time(), ok)


def _logo_img(symbol, isin=None, css_class="pf-logo", style=""):
    """An <img> for a ticker's logo, or a monogram disc when none exists.

    Parqet indexes logos both ways, but the symbol index only resolves
    US-style tickers: the Amundi ETF WEBN 404s there while its ISIN serves the
    real Amundi mark. So the ISIN leads whenever a broker gave us one, the
    bare symbol is checked before use, and a name parqet does not know at all
    gets its initial in a disc — deliberate-looking, unlike the broken-image
    icon a stripped onerror leaves behind.
    """
    _t_logo = time.perf_counter()
    if isin:
        src = f"https://assets.parqet.com/logos/isin/{isin}"
    else:
        symbol_url = f"https://assets.parqet.com/logos/symbol/{symbol}"
        src = symbol_url if _logo_resolves(symbol_url) else None
    load_profiler.count_logo(time.perf_counter() - _t_logo)
    _cls = f'class="{css_class}" ' if css_class else ""
    _sty = f'style="{style}" ' if style else ""
    if src:
        return f'<img {_cls}{_sty}src="{src}">'
    initial = (str(symbol or "?")[:1]).upper()
    mono = ("display:inline-flex;align-items:center;justify-content:center;"
            "background:#d9e7dd;color:#2f6f4f;font-weight:700;"
            "font-size:0.62rem;")
    return f'<span {_cls}style="{mono}{style}">{initial}</span>'


def _color_val(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return f"color: {T['accent']}"
        elif val < 0:
            return f"color: {T['red']}"
    return ""


def _parse_option_symbol(symbol):
    """Extract strike, expiration, and type from OCC option symbol like MSFT  250321C00420000."""
    if not symbol:
        return None, None, None
    m = re.match(r'^(.+?)\s*(\d{6})([CP])(\d{8})$', symbol.strip())
    if not m:
        return None, None, None
    date_str, cp, strike_raw = m.group(2), m.group(3), m.group(4)
    strike = int(strike_raw) / 1000
    try:
        exp = datetime.strptime(date_str, "%y%m%d")
        return strike, exp.strftime("%d-%m-%Y"), cp
    except ValueError:
        return strike, None, cp


def _has_open_options(data):
    """Check if a ticker has any open option positions."""
    return bool(_find_open_options(data.get("trades", [])))


def _find_open_options(trades):
    """Find currently open option positions from a ticker's trade list.

    Returns list of dicts with keys: symbol, type (CSP/CC/Put/Call),
    strike, expiration, quantity, premium.
    """
    positions = {}  # keyed by symbol
    for t in trades:
        inst = t.get("instrument_type", "")
        if "Option" not in inst:
            continue
        symbol = t.get("symbol", "")
        action = t.get("action", "")
        label = t.get("label", "")
        qty = t["quantity"]
        net = t["net_value"]

        if symbol not in positions:
            positions[symbol] = {"qty": 0, "premium": 0.0, "label": "", "trades": []}

        pos = positions[symbol]
        if action == "Sell to Open":
            pos["qty"] += qty
            pos["premium"] += net
            pos["label"] = label  # CSP or CC
        elif action == "Buy to Open":
            pos["qty"] += qty
            pos["premium"] += net
            pos["label"] = label
        elif action in ("Buy to Close", "Sell to Close"):
            pos["qty"] -= qty
            pos["premium"] += net
        elif label in ("Expired", "Assignment"):
            pos["qty"] -= qty

    result = []
    for symbol, pos in positions.items():
        if pos["qty"] > 0:
            strike, exp, cp = _parse_option_symbol(symbol)
            opt_type = pos["label"] or ("Put" if cp == "P" else "Call" if cp == "C" else "Option")
            result.append({
                "symbol": symbol,
                "type": opt_type,
                "strike": strike,
                "expiration": exp,
                "cp": cp,
                "quantity": int(pos["qty"]),
                "premium": pos["premium"],
            })
    return result



# ══════════════════════════════════════════════════════
#  WATCHLIST PAGE — Track multiple DCF valuations
# ══════════════════════════════════════════════════════

GITHUB_REPO_URL = "https://github.com/lazytheta/stock-analysis"
CONTACT_EMAIL = "security@lazytheta.io"


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Log any unhandled exception to Supabase before Streamlit's default handler."""
    import traceback as _tb
    log_error(
        "UNHANDLED_ERROR",
        str(exc_value),
        page=st.session_state.get("nav_radio"),
        stack_trace="".join(_tb.format_exception(exc_type, exc_value, exc_tb)),
    )
    # Fall through to Streamlit's default handler
    _original_excepthook(exc_type, exc_value, exc_tb)


_original_excepthook = sys.excepthook
sys.excepthook = _global_exception_handler


# ── Monthly detail helpers ──

def _fmt_k(val):
    """Format dollar amount: $1,234 -> '$1.2K', $500 -> '$500'."""
    sign = "+" if val > 0 else "-" if val < 0 else ""
    av = abs(val)
    if av >= 1000:
        return f"{sign}${av / 1000:.1f}K"
    return f"{sign}${av:,.0f}"


def _report_yahoo_chart(ticker, data, query):
    """De Yahoo-chart voor een positie in het week-/maandrapport: (stamps, closes).

    Probeert de namen die t212_history voor de rij afleidt: de kale naam
    eerst, dan de beurs- en valutasuffixen. "RMS" en "IEQU" bestaan bij Yahoo
    niet, RMS.PA en IEQU.MI wel -- zonder dit stonden de Trading 212-lijnen
    elke week opnieuw als "koers niet opgehaald" in het rapport, vanaf elk IP,
    want dit was geen blokkade maar een verkeerde naam. Een 404 is een
    antwoord en gaat door naar de volgende kandidaat; wat anders faalt, faalt.

    `symbol` en niet de dict-sleutel: met een naam bij twee brokers heet die
    "DECK (Trading 212)", en daar heeft Yahoo nooit van gehoord.

    Werpt de laatste fout als geen enkele kandidaat antwoordt, zodat de
    aanroeper de rij als storing kan noemen in plaats van stil over te slaan.
    """
    import json as _json
    import ssl as _ssl
    import urllib.error as _urlerror
    import urllib.request as _urllib
    from t212_history import yahoo_candidates

    ctx = _ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = _ssl.CERT_NONE
    symbol = data.get("symbol") or ticker
    last_error = None
    for candidate in yahoo_candidates(symbol, data.get("exchange"),
                                      data.get("native_currency")):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{candidate}?{query}"
        req = _urllib.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with _urllib.urlopen(req, context=ctx, timeout=10) as resp:
                cdata = _json.loads(resp.read())
            result = cdata["chart"]["result"][0]
            return result["timestamp"], result["indicators"]["quote"][0]["close"]
        except _urlerror.HTTPError as e:
            last_error = e
            if e.code != 404:
                raise
    raise last_error or LookupError(f"geen Yahoo-notering gevonden voor {symbol}")


def _aggregate_month_trades(cost_basis, year, month):
    """Aggregate trade data for a specific month from cost_basis.

    Returns dict with:
        premium_total, premium_trades, leaders_premium, leaders_pl, laggards_pl
    """
    price_failures = []   # tickers waarvan de koers niet opgehaald kon worden
    from datetime import datetime

    ticker_data = defaultdict(lambda: {
        "cc": 0.0, "put": 0.0, "equity_pl": 0.0, "net_pl": 0.0,
        "premium": 0.0, "premium_trades": 0, "contracts": 0,
        "dte_sum": 0.0, "dte_count": 0, "collateral_sum": 0.0,
        "has_options": False, "has_equity": False,
    })

    def _ym(td):
        if hasattr(td, "year"):
            return td.year, td.month
        _dt = datetime.strptime(str(td)[:10], "%Y-%m-%d")
        return _dt.year, _dt.month

    for ticker, data in cost_basis.items():
        # ── Realized equity P/L, FIFO ──
        # The same lot relief the broker applies, so a month's figure can be
        # laid next to the statement. A running average made IBIT's August
        # sale -321.94 where Tastytrade booked -388.10.
        eq_trades = sorted(
            [t for t in data.get("trades", []) if t.get("instrument_type") == "Equity"],
            key=lambda t: t["date"],
        )
        _month_equity_pl = 0.0
        _had_equity_trade = False
        for _sale in fifo_realized(eq_trades):
            if _ym(_sale["date"]) == (year, month):
                _month_equity_pl += _sale["realized"]
                _had_equity_trade = True
        # A purchase makes the month an equity month too, even with no sale.
        for t in eq_trades:
            if (t.get("net_value", 0.0) < 0 and t.get("quantity")
                    and _ym(t["date"]) == (year, month)):
                _had_equity_trade = True

        if _had_equity_trade:
            ticker_data[ticker]["equity_pl"] += _month_equity_pl
            ticker_data[ticker]["net_pl"] += _month_equity_pl
            ticker_data[ticker]["has_equity"] = True

        # ── Then: process non-equity trades in target month ──
        for t in data.get("trades", []):
            if t.get("instrument_type") == "Equity":
                continue  # already handled above
            td = t["date"]
            if hasattr(td, "year"):
                t_year, t_month = td.year, td.month
            else:
                dt = datetime.strptime(str(td)[:10], "%Y-%m-%d")
                t_year, t_month = dt.year, dt.month
            if t_year != year or t_month != month:
                continue

            label = t.get("label", "")
            nv = t.get("net_value", 0.0)
            td_obj = ticker_data[ticker]
            td_obj["net_pl"] += nv

            if label in ("CC", "BTC CC"):
                td_obj["cc"] += nv
            elif label in ("CSP", "BTC CSP"):
                td_obj["put"] += nv

            if "Option" in (t.get("instrument_type") or ""):
                td_obj["has_options"] = True

            if label in ("CSP", "CC", "BTC CSP", "BTC CC"):
                td_obj["premium"] += nv
                td_obj["premium_trades"] += 1
                if label in ("CSP", "CC"):
                    td_obj["contracts"] += abs(int(t.get("quantity", 0)))
                if label in ("CSP", "CC"):
                    strike, exp_str, _cp = _parse_option_symbol(t.get("symbol"))
                    if exp_str and hasattr(td, "year"):
                        try:
                            exp_dt = datetime.strptime(exp_str, "%d-%m-%Y")
                            trade_dt = datetime(td.year, td.month, td.day) if hasattr(td, "day") else datetime.strptime(str(td)[:10], "%Y-%m-%d")
                            dte = (exp_dt - trade_dt).days
                            if dte > 0:
                                td_obj["dte_sum"] += dte
                                td_obj["dte_count"] += 1
                                qty = abs(int(t.get("quantity", 1))) or 1
                                if strike and strike > 0:
                                    td_obj["collateral_sum"] += strike * 100 * qty
                        except (ValueError, TypeError):
                            pass

    # ── Unrealized equity P/L for tickers with shares held and NO equity trades this month ──
    # Tickers that had equity (stock buy/sell) trades this month — already have realized P/L
    _equity_traded = {t for t, d in ticker_data.items() if d["has_equity"]}

    tickers_with_shares = {}
    for ticker, data in cost_basis.items():
        current_shares = data.get("shares_held", 0)
        if current_shares > 0 and ticker not in _equity_traded:
            tickers_with_shares[ticker] = current_shares

    if tickers_with_shares:
        for ticker, shares in tickers_with_shares.items():
            try:
                timestamps, closes = _report_yahoo_chart(
                    ticker, cost_basis[ticker], "range=5y&interval=1mo")
                month_prices = {}
                for ts, close in zip(timestamps, closes):
                    if close is None:
                        continue
                    dt = datetime.utcfromtimestamp(ts)
                    month_prices[(dt.year, dt.month)] = close
                prev_month = month - 1 if month > 1 else 12
                prev_year = year if month > 1 else year - 1
                price_start = month_prices.get((prev_year, prev_month))
                price_end = month_prices.get((year, month))
                if price_start and price_end:
                    # Yahoo noteert een Europese lijn in haar eigen valuta;
                    # het rapport telt in USD. De rij draagt de koers waarmee
                    # de positie al is omgerekend.
                    unrealized = (shares * (price_end - price_start)
                                  * (cost_basis[ticker].get("fx_rate") or 1.0))
                    if abs(unrealized) >= 1.0:
                        ticker_data[ticker]["equity_pl"] += unrealized
                        ticker_data[ticker]["net_pl"] += unrealized
                        ticker_data[ticker]["has_equity"] = True
            except Exception as _e:
                # Niet stil. equity_pl bleef 0 en has_equity False, en het
                # rapport rendert dan zonder de aandelenbeen -- de 'net P/L'
                # telt alleen optiepremie, zonder enig signaal. Wie hier
                # belandt hoort in het rapport genoemd te worden.
                price_failures.append(ticker)
                logger.warning("Koers voor %s niet opgehaald: %s", ticker, _e)

    premium_list = []
    for ticker, d in ticker_data.items():
        if d["premium"] <= 0:
            continue
        avg_dte = int(d["dte_sum"] / d["dte_count"]) if d["dte_count"] > 0 else 0
        est_roc = 0.0
        if d["collateral_sum"] > 0 and avg_dte > 0:
            est_roc = (d["premium"] / d["collateral_sum"]) * (365 / avg_dte) * 100
        premium_list.append({
            "ticker": ticker, "trades": d["premium_trades"],
            "contracts": d["contracts"], "avg_dte": avg_dte,
            "est_roc": round(est_roc, 1), "premiums": d["premium"],
        })
    premium_list.sort(key=lambda x: x["premiums"], reverse=True)

    # P/L list: only tickers where we hold shares or traded equity (not pure-option positions)
    pl_list = [{"ticker": t, "cc": d["cc"], "put": d["put"], "equity_pl": d["equity_pl"], "net_pl": d["net_pl"]}
               for t, d in ticker_data.items()
               if d["net_pl"] != 0 and d["has_equity"]]
    pl_list.sort(key=lambda x: x["net_pl"], reverse=True)

    total_premium = sum(d["premium"] for d in ticker_data.values())
    total_premium_trades = sum(d["premium_trades"] for d in ticker_data.values())

    return {
        "premium_total": total_premium,
        "price_failures": price_failures,
        "premium_trades": total_premium_trades,
        "leaders_premium": premium_list[:5],
        "leaders_pl": [x for x in pl_list[:5] if x["net_pl"] > 0],
        "laggards_pl": [x for x in pl_list[-5:] if x["net_pl"] < 0],
    }


def _aggregate_week_trades(cost_basis, wk_start, wk_end):
    """Aggregate trade data for a specific week from cost_basis.

    Returns dict with same structure as _aggregate_month_trades.
    """
    price_failures = []   # tickers waarvan de koers niet opgehaald kon worden
    from datetime import datetime

    ticker_data = defaultdict(lambda: {
        "cc": 0.0, "put": 0.0, "equity_pl": 0.0, "net_pl": 0.0,
        "premium": 0.0, "premium_trades": 0, "contracts": 0,
        "dte_sum": 0.0, "dte_count": 0, "collateral_sum": 0.0,
        "has_options": False, "has_equity": False,
    })

    wk_start_dt = wk_start if isinstance(wk_start, datetime) else datetime.combine(wk_start, datetime.min.time())
    wk_end_dt = wk_end if isinstance(wk_end, datetime) else datetime.combine(wk_end, datetime.max.time())
    # Normalize to date for comparison
    wk_start_d = wk_start_dt.date() if hasattr(wk_start_dt, 'date') else wk_start_dt
    wk_end_d = wk_end_dt.date() if hasattr(wk_end_dt, 'date') else wk_end_dt

    def _to_date(td):
        if hasattr(td, "date") and callable(td.date):
            return td.date()
        elif hasattr(td, "year"):
            return datetime(td.year, td.month, td.day).date()
        return datetime.strptime(str(td)[:10], "%Y-%m-%d").date()

    _traded_tickers = set()
    for ticker, data in cost_basis.items():
        # ── Realized equity P/L, FIFO (same engine as the monthly view) ──
        eq_trades = sorted(
            [t for t in data.get("trades", []) if t.get("instrument_type") == "Equity"],
            key=lambda t: t["date"],
        )
        _wk_equity_pl = 0.0
        _had_equity_trade = False
        for _sale in fifo_realized(eq_trades):
            if wk_start_d <= _to_date(_sale["date"]) <= wk_end_d:
                _wk_equity_pl += _sale["realized"]
                _had_equity_trade = True
        for t in eq_trades:
            if (t.get("net_value", 0.0) < 0 and t.get("quantity")
                    and wk_start_d <= _to_date(t["date"]) <= wk_end_d):
                _had_equity_trade = True

        if _had_equity_trade:
            ticker_data[ticker]["equity_pl"] += _wk_equity_pl
            ticker_data[ticker]["net_pl"] += _wk_equity_pl
            ticker_data[ticker]["has_equity"] = True
            _traded_tickers.add(ticker)

        # ── Non-equity trades in this week ──
        for t in data.get("trades", []):
            if t.get("instrument_type") == "Equity":
                continue
            t_date = _to_date(t["date"])
            if t_date < wk_start_d or t_date > wk_end_d:
                continue
            _traded_tickers.add(ticker)

            label = t.get("label", "")
            nv = t.get("net_value", 0.0)
            td_obj = ticker_data[ticker]
            td_obj["net_pl"] += nv

            if label in ("CC", "BTC CC"):
                td_obj["cc"] += nv
            elif label in ("CSP", "BTC CSP"):
                td_obj["put"] += nv

            if "Option" in (t.get("instrument_type") or ""):
                td_obj["has_options"] = True

            if label in ("CSP", "CC", "BTC CSP", "BTC CC"):
                td_obj["premium"] += nv
                td_obj["premium_trades"] += 1
                if label in ("CSP", "CC"):
                    td_obj["contracts"] += abs(int(t.get("quantity", 0)))
                if label in ("CSP", "CC"):
                    td = t["date"]
                    strike, exp_str, _cp = _parse_option_symbol(t.get("symbol"))
                    if exp_str and hasattr(td, "year"):
                        try:
                            exp_dt = datetime.strptime(exp_str, "%d-%m-%Y")
                            trade_dt = datetime(td.year, td.month, td.day) if hasattr(td, "day") else datetime.strptime(str(td)[:10], "%Y-%m-%d")
                            dte = (exp_dt - trade_dt).days
                            if dte > 0:
                                td_obj["dte_sum"] += dte
                                td_obj["dte_count"] += 1
                                qty = abs(int(t.get("quantity", 1))) or 1
                                if strike and strike > 0:
                                    td_obj["collateral_sum"] += strike * 100 * qty
                        except (ValueError, TypeError):
                            pass

    # ── Unrealized equity P/L for tickers where we hold shares ──
    # Only add unrealized for tickers with shares held and NO equity trades this week
    _equity_traded_wk = {t for t, d in ticker_data.items() if d["has_equity"]}

    tickers_with_shares = {}
    for ticker, data in cost_basis.items():
        current_shares = data.get("shares_held", 0)
        if current_shares > 0 and ticker not in _equity_traded_wk:
            tickers_with_shares[ticker] = current_shares

    if tickers_with_shares:
        _days_back = (datetime.now() - datetime(wk_start_d.year, wk_start_d.month, wk_start_d.day)).days + 14
        _range = "1mo" if _days_back < 25 else ("3mo" if _days_back < 80 else ("1y" if _days_back < 350 else "5y"))
        for ticker, shares in tickers_with_shares.items():
            try:
                timestamps, closes = _report_yahoo_chart(
                    ticker, cost_basis[ticker], f"range={_range}&interval=1d")
                daily_prices = []
                for ts, close in zip(timestamps, closes):
                    if close is None:
                        continue
                    dt = datetime.utcfromtimestamp(ts).date()
                    daily_prices.append((dt, close))
                daily_prices.sort()
                price_before = None
                price_end = None
                for dt, close in daily_prices:
                    if dt < wk_start_d:
                        price_before = close
                    if dt <= wk_end_d:
                        price_end = close
                if price_before and price_end:
                    # Zelfde omrekening als in het maandrapport hierboven.
                    unrealized = (shares * (price_end - price_before)
                                  * (cost_basis[ticker].get("fx_rate") or 1.0))
                    if abs(unrealized) >= 1.0:
                        ticker_data[ticker]["equity_pl"] += unrealized
                        ticker_data[ticker]["net_pl"] += unrealized
                        ticker_data[ticker]["has_equity"] = True
            except Exception as _e:
                # Niet stil. equity_pl bleef 0 en has_equity False, en het
                # rapport rendert dan zonder de aandelenbeen -- de 'net P/L'
                # telt alleen optiepremie, zonder enig signaal. Wie hier
                # belandt hoort in het rapport genoemd te worden.
                price_failures.append(ticker)
                logger.warning("Koers voor %s niet opgehaald: %s", ticker, _e)

    premium_list = []
    for ticker, d in ticker_data.items():
        if d["premium"] <= 0:
            continue
        avg_dte = int(d["dte_sum"] / d["dte_count"]) if d["dte_count"] > 0 else 0
        est_roc = 0.0
        if d["collateral_sum"] > 0 and avg_dte > 0:
            est_roc = (d["premium"] / d["collateral_sum"]) * (365 / avg_dte) * 100
        premium_list.append({
            "ticker": ticker, "trades": d["premium_trades"],
            "contracts": d["contracts"], "avg_dte": avg_dte,
            "est_roc": round(est_roc, 1), "premiums": d["premium"],
        })
    premium_list.sort(key=lambda x: x["premiums"], reverse=True)

    # P/L list: only tickers where we hold shares or traded equity (not pure-option positions)
    pl_list = [{"ticker": t, "cc": d["cc"], "put": d["put"], "equity_pl": d["equity_pl"], "net_pl": d["net_pl"]}
               for t, d in ticker_data.items()
               if d["net_pl"] != 0 and d["has_equity"]]
    pl_list.sort(key=lambda x: x["net_pl"], reverse=True)

    total_premium = sum(d["premium"] for d in ticker_data.values())
    total_premium_trades = sum(d["premium_trades"] for d in ticker_data.values())

    return {
        "premium_total": total_premium,
        "price_failures": price_failures,
        "premium_trades": total_premium_trades,
        "leaders_premium": premium_list[:5],
        "leaders_pl": [x for x in pl_list[:5] if x["net_pl"] > 0],
        "laggards_pl": [x for x in pl_list[-5:] if x["net_pl"] < 0],
    }


@st.dialog("Weekly Detail", width="large")
def _show_week_detail(year, iso_wk, wk_start, wk_end, cost_basis, nl_all, transfers, weekly_returns, T):
    """Render weekly detail modal — same format as monthly report."""
    import pandas as pd
    import base64 as _b64
    import streamlit.components.v1 as components

    wk_label = f"W{iso_wk} · {wk_start.strftime('%b %d')}–{wk_end.strftime('%b %d, %Y')}"

    agg = _aggregate_week_trades(cost_basis, wk_start, wk_end)
    if agg.get("price_failures"):
        st.caption("\u26a0\ufe0f Koersen niet opgehaald voor "
                   + ", ".join(agg["price_failures"])
                   + " \u2014 ongerealiseerde aandelen-P/L ontbreekt voor die posities.")

    # Weekly return %
    _wk_key = (year, wk_start.month)
    wk_ret_pct = 0.0
    for _wiso, _wret, _ws, _we in weekly_returns.get(_wk_key, []):
        if _wiso == iso_wk:
            wk_ret_pct = _wret
            break

    # Net P/L from net_liq
    net_pl_dollar = 0.0
    _period_capital = 0.0
    if nl_all:
        df = pd.DataFrame(nl_all)
        df["time"] = _to_time_col(df["time"])
        df = df.sort_values("time")
        # Normalize timezone: strip tz from df if wk_start is naive, or vice versa
        _wk_s = pd.Timestamp(wk_start)
        _wk_e = pd.Timestamp(wk_end) + pd.Timedelta(days=1)
        if df["time"].dt.tz is not None and _wk_s.tz is None:
            _wk_s = _wk_s.tz_localize(df["time"].dt.tz)
            _wk_e = _wk_e.tz_localize(df["time"].dt.tz)
        elif df["time"].dt.tz is None and _wk_s.tz is not None:
            _wk_s = _wk_s.tz_localize(None)
            _wk_e = _wk_e.tz_localize(None)
        wk_data = df[(df["time"] >= _wk_s) & (df["time"] <= _wk_e)]
        if not wk_data.empty:
            end_val = wk_data["close"].iloc[-1]
            prev = df[df["time"] < wk_data["time"].iloc[0]]
            start_val = prev["close"].iloc[-1] if not prev.empty else end_val
            _period_capital = start_val
            # Approximate deposits for this week
            _wk_yr, _wk_mo = wk_start.year, wk_start.month
            yr_tr = transfers.get(_wk_yr, {})
            mo_dep_total = yr_tr.get("months", {}).get(_wk_mo, 0) if isinstance(yr_tr, dict) else 0
            import calendar as _cal
            _days_in_mo = _cal.monthrange(_wk_yr, _wk_mo)[1]
            _wk_days = (wk_end - wk_start).days + 1
            wk_dep = mo_dep_total * (_wk_days / _days_in_mo) if _days_in_mo > 0 else 0
            net_pl_dollar = end_val - start_val - wk_dep

    # Premium ROC
    _prem_roc = (agg["premium_total"] / _period_capital * 100) if _period_capital > 0 else 0.0

    # ── Colors ──
    _green = T['accent']
    _red = T['red']
    _muted = T['text_muted']
    _card = T['card']
    _border = T['border']
    _text = T['text']
    _bg = T['bg']

    def _c(val):
        return _muted if val is None else (_green if val >= 0 else _red)

    def _pct(val):
        # Een ontbrekende meting toont een streep, geen +0.0%. Bij een
        # geblokkeerde Yahoo las een vlakke index als outperformance.
        return "\u2014" if val is None else f"{val:+.1f}%"

    # Premium table rows
    prem_rows = ""
    if agg["leaders_premium"]:
        for lp in agg["leaders_premium"]:
            dte_str = f'{lp["avg_dte"]}d' if lp["avg_dte"] > 0 else "—"
            prem_rows += (
                f'<tr><td class="tk">{lp["ticker"]}</td><td>{lp["trades"]}</td>'
                f'<td>{lp["contracts"]}</td><td>{dte_str}</td>'
                f'<td style="color:{_c(lp["est_roc"])}">{lp["est_roc"]:.1f}%</td>'
                f'<td style="color:{_green}">{_fmt_k(lp["premiums"])}</td></tr>'
            )

    # P/L table rows
    def _pl_html(items):
        if not items:
            return f'<tr><td colspan="5" style="text-align:center;color:{_muted};padding:20px">—</td></tr>'
        r = ""
        for it in items:
            r += (
                f'<tr><td class="tk">{it["ticker"]}</td>'
                f'<td style="color:{_c(it["cc"])}">{_fmt_k(it["cc"])}</td>'
                f'<td style="color:{_c(it["put"])}">{_fmt_k(it["put"])}</td>'
                f'<td style="color:{_c(it["equity_pl"])}">{_fmt_k(it["equity_pl"])}</td>'
                f'<td style="color:{_c(it["net_pl"])};font-weight:700">{_fmt_k(it["net_pl"])}</td></tr>'
            )
        return r

    w_rows = _pl_html(agg["leaders_pl"])
    l_rows = _pl_html(agg["laggards_pl"])

    # Logo
    with open("assets/logo_footer.png", "rb") as _lf:
        _logo_b64 = _b64.b64encode(_lf.read()).decode()

    has_premium = bool(agg["leaders_premium"])
    has_pl = bool(agg["leaders_pl"] or agg["laggards_pl"])

    premium_section = ""
    if has_premium:
        premium_section = f'''
        <div class="section">
            <div class="section-title">Winners — By Premium</div>
            <table>
                <tr><th class="left">Ticker</th><th>Trades</th><th>Contracts</th><th>Avg DTE</th><th>Ann. ROC</th><th>Net Premiums</th></tr>
                {prem_rows}
            </table>
        </div>'''

    pl_section = ""
    if has_pl:
        pl_section = f'''
        <div class="section">
            <div class="section-title">Winners &amp; Losers — By P/L</div>
            <div class="pl-grid">
                <div class="pl-half">
                    <div class="pl-label" style="color:{_green}">Winners</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {w_rows}
                    </table>
                </div>
                <div class="pl-divider"></div>
                <div class="pl-half">
                    <div class="pl-label" style="color:{_red}">Losers</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {l_rows}
                    </table>
                </div>
            </div>
        </div>'''

    _dl_name = f'lazytheta-W{iso_wk}-{year}'

    report_html = f'''<!DOCTYPE html>
<html><head>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif; background:{_bg}; color:{_text}; }}
#report {{ padding: 32px; max-width: 800px; margin: 0 auto; }}

.header {{ text-align:center; padding-bottom:20px; border-bottom:1px solid {_border}; margin-bottom:24px; }}
.header h1 {{ font-size:1.5rem; font-weight:700; letter-spacing:-0.01em; margin-bottom:2px; }}
.header .sub {{ font-size:0.82rem; color:{_muted}; }}

.heroes {{ display:flex; gap:12px; margin-bottom:24px; }}
.hero {{ flex:1; background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; display:flex; flex-direction:column; }}
.hero-label {{ font-size:0.7rem; color:{_muted}; text-transform:uppercase; letter-spacing:0.06em; font-weight:600; margin-bottom:8px; }}
.hero-val {{ font-size:1.7rem; font-weight:700; line-height:1.15; }}
.hero-detail {{ font-size:0.8rem; color:{_muted}; margin-top:4px; }}

.section {{ background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; margin-bottom:16px; }}
.section-title {{ font-size:0.82rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; padding-bottom:10px; margin-bottom:14px; border-bottom:1px solid {_border}; }}

table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
th {{ text-align:right; padding:8px 10px; color:{_muted}; font-weight:600; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.04em; border-bottom:2px solid {_border}; }}
th.left {{ text-align:left; }}
td {{ padding:10px 10px; border-bottom:1px solid {_border}; text-align:right; }}
td.tk {{ text-align:left; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}

.pl-grid {{ display:flex; gap:0; }}
.pl-half {{ flex:1; }}
.pl-divider {{ width:1px; background:{_border}; margin:0 16px; }}
.pl-label {{ font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px; }}

.footer {{ display:flex; align-items:center; justify-content:center; gap:10px; padding:20px 0 8px 0; border-top:1px solid {_border}; margin-top:8px; }}
.footer img {{ height:28px; opacity:0.7; }}
.footer span {{ font-size:0.8rem; color:{_muted}; letter-spacing:0.02em; }}

#dl-btn {{ background:{_green}; color:#fff; border:none; padding:12px 24px; border-radius:10px; cursor:pointer; font-size:0.85rem; font-weight:600; width:100%; margin-top:16px; letter-spacing:0.02em; }}
#dl-btn:hover {{ opacity:0.9; }}
</style></head><body>
<div id="report">
    <div class="header">
        <h1>Week {iso_wk}</h1>
        <div class="sub">{wk_start.strftime('%B %d')} – {wk_end.strftime('%B %d, %Y')} · Weekly Performance Report</div>
    </div>

    <div class="heroes">
        <div class="hero">
            <div class="hero-label">Net Premiums</div>
            <div class="hero-val" style="color:{_c(agg["premium_total"])}">{_fmt_k(agg["premium_total"])}</div>
            <div class="hero-detail"><span style="color:{_c(_prem_roc)};font-weight:600">{_prem_roc:+.1f}%</span> ROC</div>
        </div>
        <div class="hero">
            <div class="hero-label">Net P/L</div>
            <div class="hero-val" style="color:{_c(net_pl_dollar)}">{_fmt_k(net_pl_dollar)}</div>
            <div class="hero-detail"><span style="color:{_c(wk_ret_pct)};font-weight:600">{wk_ret_pct:+.1f}%</span> return</div>
        </div>
    </div>

    {premium_section}
    {pl_section}

    <div class="footer" id="logo-footer">
        <img src="data:image/png;base64,{_logo_b64}">
        <span>lazytheta.io</span>
    </div>

    <button id="dl-btn">Download as PNG</button>
</div>

<script>
document.getElementById('dl-btn').addEventListener('click', function() {{
    const btn = this;
    btn.textContent = 'Generating...';
    btn.style.opacity = '0.6';
    const report = document.getElementById('report');
    btn.style.display = 'none';

    html2canvas(report, {{
        backgroundColor: '{_bg}',
        scale: 2,
        useCORS: true,
        logging: false,
    }}).then(function(canvas) {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
        const link = document.createElement('a');
        link.download = '{_dl_name}.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    }}).catch(function() {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
    }});
}});
</script>
</body></html>'''

    _h = 350  # header + heroes
    if has_premium:
        _h += 60 + len(agg["leaders_premium"]) * 42
    if has_pl:
        _h += 80 + max(len(agg["leaders_pl"]), len(agg["laggards_pl"]), 1) * 42
    _h += 80

    components.html(report_html, height=_h, scrolling=True)



@st.dialog("Monthly Detail", width="large")
def _show_month_detail(year, month, cost_basis, nl_all, transfers, monthly_returns, T):
    """Render monthly detail modal — polished shareable report card."""
    import pandas as pd
    import base64 as _b64
    import streamlit.components.v1 as components

    MONTH_NAMES_FULL = ["", "January", "February", "March", "April", "May", "June",
                        "July", "August", "September", "October", "November", "December"]
    MONTH_NAMES_SHORT = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                         "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_label = f"{MONTH_NAMES_SHORT[month]} {year}"
    month_full = f"{MONTH_NAMES_FULL[month]} {year}"

    agg = _aggregate_month_trades(cost_basis, year, month)
    if agg.get("price_failures"):
        st.caption("\u26a0\ufe0f Koersen niet opgehaald voor "
                   + ", ".join(agg["price_failures"])
                   + " \u2014 ongerealiseerde aandelen-P/L ontbreekt voor die posities.")

    # Net P/L from net_liq. Geen 0.0 als default: een maand zonder meting is
    # geen maand met 0% rendement, en die twee lazen tot 2026-09-11 identiek.
    mo_ret_pct = monthly_returns.get(year, {}).get(month)
    net_pl_dollar = 0.0
    _period_capital = 0.0
    if nl_all:
        df = pd.DataFrame(nl_all)
        df["time"] = _to_time_col(df["time"])
        df = df.sort_values("time")
        mo_data = df[(df["time"].dt.year == year) & (df["time"].dt.month == month)]
        if not mo_data.empty:
            end_val = mo_data["close"].iloc[-1]
            prev = df[df["time"] < mo_data["time"].iloc[0]]
            start_val = prev["close"].iloc[-1] if not prev.empty else end_val
            _period_capital = start_val
            yr_transfers = transfers.get(year, {})
            mo_dep = yr_transfers.get("months", {}).get(month, 0) if isinstance(yr_transfers, dict) else 0
            net_pl_dollar = end_val - start_val - mo_dep

    # Premium ROC
    _prem_roc = (agg["premium_total"] / _period_capital * 100) if _period_capital > 0 else 0.0

    # Benchmark monthly returns (cached)
    if "benchmark_monthly" not in st.session_state:
        try:
            st.session_state["benchmark_monthly"] = fetch_benchmark_monthly_returns()
        except Exception:
            st.session_state["benchmark_monthly"] = {}
    bench = st.session_state["benchmark_monthly"]

    # ── Color helpers ──
    _green = T['accent']
    _red = T['red']
    _muted = T['text_muted']
    _card = T['card']
    _border = T['border']
    _text = T['text']
    _bg = T['bg']

    def _c(val):
        return _muted if val is None else (_green if val >= 0 else _red)

    def _pct(val):
        # Een ontbrekende meting toont een streep, geen +0.0%. Bij een
        # geblokkeerde Yahoo las een vlakke index als outperformance.
        return "\u2014" if val is None else f"{val:+.1f}%"

    # ── Build entire report as one HTML string ──
    # Benchmark rows
    bench_rows_html = (
        f'<div class="bench-row">'
        f'<span>Portfolio</span><span style="color:{_c(mo_ret_pct)}">{_pct(mo_ret_pct)}</span></div>'
    )
    for bname, bdata in bench.items():
        b_ret = bdata.get((year, month))
        bench_rows_html += (
            f'<div class="bench-row">'
            f'<span>{bname}</span><span style="color:{_c(b_ret)}">{_pct(b_ret)}</span></div>'
        )

    # Premium table rows
    prem_rows = ""
    if agg["leaders_premium"]:
        for lp in agg["leaders_premium"]:
            dte_str = f'{lp["avg_dte"]}d' if lp["avg_dte"] > 0 else "—"
            prem_rows += (
                f'<tr><td class="tk">{lp["ticker"]}</td><td>{lp["trades"]}</td>'
                f'<td>{lp["contracts"]}</td><td>{dte_str}</td>'
                f'<td style="color:{_c(lp["est_roc"])}">{lp["est_roc"]:.1f}%</td>'
                f'<td style="color:{_green}">{_fmt_k(lp["premiums"])}</td></tr>'
            )

    # P/L table rows
    def _pl_html(items):
        if not items:
            return f'<tr><td colspan="5" style="text-align:center;color:{_muted};padding:20px">—</td></tr>'
        r = ""
        for it in items:
            r += (
                f'<tr><td class="tk">{it["ticker"]}</td>'
                f'<td style="color:{_c(it["cc"])}">{_fmt_k(it["cc"])}</td>'
                f'<td style="color:{_c(it["put"])}">{_fmt_k(it["put"])}</td>'
                f'<td style="color:{_c(it["equity_pl"])}">{_fmt_k(it["equity_pl"])}</td>'
                f'<td style="color:{_c(it["net_pl"])};font-weight:700">{_fmt_k(it["net_pl"])}</td></tr>'
            )
        return r

    w_rows = _pl_html(agg["leaders_pl"])
    l_rows = _pl_html(agg["laggards_pl"])

    # Logo
    with open("assets/logo_footer.png", "rb") as _lf:
        _logo_b64 = _b64.b64encode(_lf.read()).decode()

    # Sections visibility
    has_premium = bool(agg["leaders_premium"])
    has_pl = bool(agg["leaders_pl"] or agg["laggards_pl"])

    premium_section = ""
    if has_premium:
        premium_section = f'''
        <div class="section">
            <div class="section-title">Winners — By Premium</div>
            <table>
                <tr><th class="left">Ticker</th><th>Trades</th><th>Contracts</th><th>Avg DTE</th><th>Ann. ROC</th><th>Net Premiums</th></tr>
                {prem_rows}
            </table>
        </div>'''

    pl_section = ""
    if has_pl:
        pl_section = f'''
        <div class="section">
            <div class="section-title">Winners &amp; Losers — By P/L</div>
            <div class="pl-grid">
                <div class="pl-half">
                    <div class="pl-label" style="color:{_green}">Winners</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {w_rows}
                    </table>
                </div>
                <div class="pl-divider"></div>
                <div class="pl-half">
                    <div class="pl-label" style="color:{_red}">Losers</div>
                    <table>
                        <tr><th class="left">Ticker</th><th>CC</th><th>PUT</th><th>Pos P/L</th><th>Net P/L</th></tr>
                        {l_rows}
                    </table>
                </div>
            </div>
        </div>'''

    report_html = f'''<!DOCTYPE html>
<html><head>
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&display=swap');
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', Arial, sans-serif; background:{_bg}; color:{_text}; }}
#report {{ padding: 32px; max-width: 800px; margin: 0 auto; }}

.header {{ text-align:center; padding-bottom:20px; border-bottom:1px solid {_border}; margin-bottom:24px; }}
.header h1 {{ font-size:1.5rem; font-weight:700; letter-spacing:-0.01em; margin-bottom:2px; }}
.header .sub {{ font-size:0.82rem; color:{_muted}; }}

.heroes {{ display:flex; gap:12px; margin-bottom:24px; }}
.hero {{ flex:1; background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; display:flex; flex-direction:column; }}
.hero-label {{ font-size:0.7rem; color:{_muted}; text-transform:uppercase; letter-spacing:0.06em; font-weight:600; margin-bottom:8px; }}
.hero-val {{ font-size:1.7rem; font-weight:700; line-height:1.15; }}
.hero-detail {{ font-size:0.8rem; color:{_muted}; margin-top:4px; }}

.bench-row {{ display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid {_border}; font-size:0.82rem; }}
.bench-row:last-child {{ border-bottom:none; }}
.bench-row span:last-child {{ font-weight:600; }}

.section {{ background:{_card}; border-radius:12px; padding:20px; border:1px solid {_border}; border-top:3px solid {_green}; margin-bottom:16px; }}
.section-title {{ font-size:0.82rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; padding-bottom:10px; margin-bottom:14px; border-bottom:1px solid {_border}; }}

table {{ width:100%; border-collapse:collapse; font-size:0.8rem; }}
th {{ text-align:right; padding:8px 10px; color:{_muted}; font-weight:600; font-size:0.7rem; text-transform:uppercase; letter-spacing:0.04em; border-bottom:2px solid {_border}; }}
th.left {{ text-align:left; }}
td {{ padding:10px 10px; border-bottom:1px solid {_border}; text-align:right; }}
td.tk {{ text-align:left; font-weight:600; }}
tr:last-child td {{ border-bottom:none; }}

.pl-grid {{ display:flex; gap:0; }}
.pl-half {{ flex:1; }}
.pl-divider {{ width:1px; background:{_border}; margin:0 16px; }}
.pl-label {{ font-size:0.72rem; font-weight:700; text-transform:uppercase; letter-spacing:0.05em; margin-bottom:10px; }}

.footer {{ display:flex; align-items:center; justify-content:center; gap:10px; padding:20px 0 8px 0; border-top:1px solid {_border}; margin-top:8px; }}
.footer img {{ height:28px; opacity:0.7; }}
.footer span {{ font-size:0.8rem; color:{_muted}; letter-spacing:0.02em; }}

#dl-btn {{ background:{_green}; color:#fff; border:none; padding:12px 24px; border-radius:10px; cursor:pointer; font-size:0.85rem; font-weight:600; width:100%; margin-top:16px; letter-spacing:0.02em; }}
#dl-btn:hover {{ opacity:0.9; }}
</style></head><body>
<div id="report">
    <div class="header">
        <h1>{month_full}</h1>
        <div class="sub">Monthly Performance Report</div>
    </div>

    <div class="heroes">
        <div class="hero">
            <div class="hero-label">Net Premiums</div>
            <div class="hero-val" style="color:{_c(agg["premium_total"])}">{_fmt_k(agg["premium_total"])}</div>
            <div class="hero-detail"><span style="color:{_c(_prem_roc)};font-weight:600">{_prem_roc:+.1f}%</span> ROC</div>
        </div>
        <div class="hero">
            <div class="hero-label">Net P/L</div>
            <div class="hero-val" style="color:{_c(net_pl_dollar)}">{_fmt_k(net_pl_dollar)}</div>
            <div class="hero-detail"><span style="color:{_c(mo_ret_pct)};font-weight:600">{mo_ret_pct:+.1f}%</span> return</div>
        </div>
        <div class="hero">
            <div class="hero-label">Benchmark</div>
            <div style="margin-top:4px">{bench_rows_html}</div>
        </div>
    </div>

    {premium_section}
    {pl_section}

    <div class="footer" id="logo-footer">
        <img src="data:image/png;base64,{_logo_b64}">
        <span>lazytheta.io</span>
    </div>

    <button id="dl-btn">Download as PNG</button>
</div>

<script>
document.getElementById('dl-btn').addEventListener('click', function() {{
    const btn = this;
    btn.textContent = 'Generating...';
    btn.style.opacity = '0.6';
    const report = document.getElementById('report');
    btn.style.display = 'none';

    html2canvas(report, {{
        backgroundColor: '{_bg}',
        scale: 2,
        useCORS: true,
        logging: false,
    }}).then(function(canvas) {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
        const link = document.createElement('a');
        link.download = 'lazytheta-{month_label.replace(" ", "-")}.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
    }}).catch(function() {{
        btn.style.display = 'block';
        btn.textContent = 'Download as PNG';
        btn.style.opacity = '1';
    }});
}});
</script>
</body></html>'''

    # Calculate approximate height based on content
    _h = 400  # header + heroes
    if has_premium:
        _h += 60 + len(agg["leaders_premium"]) * 42
    if has_pl:
        _h += 80 + max(len(agg["leaders_pl"]), len(agg["laggards_pl"]), 1) * 42
    _h += 80  # button + padding

    components.html(report_html, height=_h, scrolling=True)


if page == "Watchlist":
    st.markdown(
        "<style>.block-container { max-width: 1400px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Route: editor or overview ──
    edit_ticker = st.query_params.get("edit")
    if edit_ticker:
        _dcf_editor(edit_ticker.upper())
    else:
        _watchlist_overview()


# ══════════════════════════════════════════════════════
#  PORTFOLIO PAGE — Active positions overview
# ══════════════════════════════════════════════════════

elif page == "Portfolio":
    if not has_active_broker():
        _render_welcome_page()
        st.stop()

    st.markdown(
        "<style>.block-container { max-width: 1200px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )
    st.markdown("")
    cost_basis = _load_portfolio_data()

    held = {
        t: d for t, d in cost_basis.items()
        if d["shares_held"] > 0 or _has_open_options(d)
    }

    # ── Broker view ──
    # The combined picture answers "how am I doing"; the per-broker one answers
    # "does this match what my broker shows me", which is the only way to check
    # the combined figure is right. Both are worth keeping, so pick one rather
    # than replacing the old view with the sum.
    portfolio_view = _broker_view_control("Portfolio")
    if portfolio_view != "Overview":
        held = {t: d for t, d in held.items()
                if d.get("broker") == portfolio_view}
    else:
        # One holding, one row. The per-broker rows are still what the data
        # says and still what a broker tab shows — a position has to stay
        # checkable against the statement it came from.
        held = merge_by_symbol(held)

    if not held:
        if portfolio_view != "Overview":
            st.info(f"No active positions at {portfolio_view}.")
            st.stop()
        st.info("No active positions.")
        st.stop()

    # Symbols, not dict keys: the key carries a broker suffix once the same
    # ticker is held at two brokers, and no quote or profile API knows it.
    held_tickers = sorted({d.get("symbol", t) for t, d in held.items()})

    if "_target_pos_pct" not in st.session_state:
        st.session_state["_target_pos_pct"] = float(
            load_user_prefs(_sb_client).get("target_position_pct")
            or DEFAULT_TARGET_POS_PCT
        )

    # ── Margin / Buying Power (with integrated simulator) ──
    # These fetch whichever broker is active, so the active broker has to be
    # part of the cache key. Without it, switching broker kept serving the
    # previous one's margin and buying power for the rest of the TTL — the
    # numbers changed brokers on screen only after a minute of staleness.
    @st.cache_data(ttl=60, show_spinner=False)
    def _cached_account_balances(broker):
        return fetch_account_balances()

    @st.cache_data(ttl=60, show_spinner=False)
    def _cached_all_balances():
        return fetch_all_balances()

    @st.cache_data(ttl=120, show_spinner=False)
    def _cached_margin_requirements(broker):
        return fetch_margin_requirements()

    @st.cache_data(ttl=300, show_spinner=False)
    def _cached_valuations(user_id, tickers_tuple):
        """The watchlist keyed by ticker: fair-value band, buy price, updated.

        One query serves both the deployment card and the hold-or-sell column;
        a name absent from here has no valuation, which is left visible rather
        than assumed fine.
        """
        try:
            return {item["ticker"].upper(): item
                    for item in list_watchlist(_sb_client, user_id=user_id,
                                               tickers=list(tickers_tuple))}
        except Exception as e:
            logger.debug("Watchlist valuations unavailable: %s", e)
            return {}

    def _deployment_overview():
        """How much of the portfolio is committed, and what is left to buy with.

        Replaces the old Margin Overview. Buying power answered "how much could
        I borrow"; this answers "how much have I got left", which is the
        question that decides whether there is anything to add with when prices
        fall. The assignment-risk line survives from the old card: shares put to
        you consume exactly the cash this card is about.
        """
        st.markdown("")

        try:
            _bals, _ = _cached_all_balances()
        except Exception as e:
            logger.warning("Account balances fetch failed: %s", e)
            _bals = {}

        _view = st.session_state.get("_portfolio_view", "Overview")
        if _view != "Overview":
            _bals = {k: v for k, v in _bals.items() if k == _view}
        if not _bals:
            return

        net_liq = sum(b.get("net_liquidating_value") or 0.0 for b in _bals.values())
        cash = sum(b.get("cash_balance") or 0.0 for b in _bals.values())

        # Downstream cards (margin interest, beta-weighted delta) are still
        # single-broker, so they keep reading the active broker's figures.
        try:
            _abal = _cached_account_balances(get_active_broker())
            st.session_state["_margin_cash"] = _abal["cash_balance"]
            st.session_state["_net_liq"] = _abal["net_liquidating_value"]
        except Exception as e:
            logger.debug("Active-broker balances unavailable: %s", e)

        target_pct = float(st.session_state.get("_target_pos_pct", DEFAULT_TARGET_POS_PCT))

        _prices = st.session_state.get("portfolio_prices", {}) or {}
        dep = compute_deployment(
            held, net_liq, cash, target_pct,
            prices={t: (p or {}).get("price") for t, p in _prices.items()},
            buy_prices={t: v["buy_price"] for t, v in _cached_valuations(
                (st.session_state.get("user") or {}).get("id"),
                tuple(held_tickers),
            ).items() if v.get("buy_price")},
        )

        # ── Assignment exposure from open short puts and naked calls ──
        # Kept from the old card: an assignment turns into shares at the strike,
        # which is dry powder spent whether or not you meant to spend it.
        total_assignment = 0.0
        assignment_entries = []
        for ticker, data in held.items():
            for opt in _find_open_options(data.get("trades", [])):
                if opt["cp"] == "P":
                    exposure = opt["strike"] * opt["quantity"] * 100
                elif opt["cp"] == "C" and opt["type"] != "CC":
                    _cp = (_prices.get(data.get("symbol", ticker)) or {}).get("price", 0)
                    exposure = _cp * opt["quantity"] * 100
                else:
                    continue
                total_assignment += exposure
                assignment_entries.append(
                    f'{opt["quantity"]}x {data.get("symbol", ticker)} '
                    f'${opt["strike"]:.0f}{opt["cp"]}'
                )

        assign_note = ""
        if total_assignment > 0:
            _after = cash - total_assignment
            _after_txt = (
                f'leaves ${_after:,.0f}' if _after >= 0
                else f'${abs(_after):,.0f} more than the cash on hand'
            )
            assign_note = (
                f'<div style="margin-bottom:12px;padding:8px 12px;background:{T["info_bg"]};border-radius:8px;'
                f'border:1px dashed {T["border_medium"]};font-size:0.85rem">'
                f'<span style="color:{T["text_muted"]}">If assigned: </span>'
                f'<b style="color:{T["text"]}">{" | ".join(assignment_entries)}</b>'
                f'<span style="color:{T["text_muted"]}"> = ${total_assignment:,.0f} — {_after_txt}</span>'
                f'</div>'
            )

        # Colour follows the powder, not the deployment: this card exists to
        # say whether there is anything left to buy the next drawdown with.
        _dry_pct = dep["dry_powder_pct"]
        if _dry_pct >= 15:
            bar_color, status = T["accent"], "Dry powder"
        elif _dry_pct >= 7:
            bar_color, status = "#f2cc8f", "Getting thin"
        else:
            bar_color, status = T["red"], "Fully committed"

        _dep_w = min(max(dep["deployed_pct"], 0), 100)
        _full_w = min(max(dep["fully_deployed_pct"] - dep["deployed_pct"], 0), 100 - _dep_w)

        _n_held = len(held)
        _pos_line = f'{dep["full_count"]} of {_n_held} positions full'
        if dep["partial"]:
            _pos_line += f' · {len(dep["partial"])} with room (${dep["top_up_cost"]:,.0f} to fill)'

        if not dep["partial"]:
            _cash_line = (
                f'Nothing to top up — cash funds '
                f'{dep["new_positions_affordable"]:.1f} new full positions'
            )
        elif dep["cash_covers_top_ups"]:
            _cash_line = (
                f'Cash fills every partial and funds '
                f'{dep["new_positions_affordable"]:.1f} more full positions'
            )
        else:
            _cash_line = (
                f'Cash covers ${dep["dry_powder"]:,.0f} of the '
                f'${dep["top_up_cost"]:,.0f} needed to fill them'
            )

        _buy_line = ""
        if dep["below_buy"]:
            _buy_line = (
                f'<span class="stat-pill">Below buy price '
                f'<b>{len(dep["below_buy"])}</b> of {dep["valued_count"]} valued</span>'
            )

        st.markdown(
            f'<div class="hero-card">'
            f'<h4>Deployment</h4>'
            f'{assign_note}'
            f'<div style="margin:16px 0">'
            f'  <div style="display:flex;justify-content:space-between;margin-bottom:6px">'
            f'    <span style="font-size:0.85rem;color:{T["text_muted"]}">'
            f'Invested ${dep["invested"]:,.0f} / ${net_liq:,.0f}</span>'
            f'    <span style="font-size:0.85rem;font-weight:600;color:{bar_color}">'
            f'{status} ({dep["deployed_pct"]:.0f}% deployed)</span>'
            f'  </div>'
            f'  <div style="background:{T["grid"]};border-radius:8px;height:12px;overflow:hidden;display:flex">'
            f'    <div style="background:{bar_color};width:{_dep_w:.1f}%;height:100%"></div>'
            f'    <div style="background:{bar_color};opacity:0.35;width:{_full_w:.1f}%;height:100%"></div>'
            f'  </div>'
            f'  <div style="margin-top:6px;font-size:0.75rem;color:{T["text_muted"]}">'
            f'Solid = invested today · faded = where spending all cash would take you '
            f'({dep["fully_deployed_pct"]:.0f}%)</div>'
            f'</div>'
            f'<p style="margin:4px 0;font-size:0.9rem">{_pos_line}</p>'
            f'<p style="margin:4px 0;font-size:0.9rem;color:{T["text_muted"]}">{_cash_line}</p>'
            f'<div class="stat-row">'
            f'<span class="stat-pill">Dry powder <b>${dep["dry_powder"]:,.0f}</b> '
            f'({dep["dry_powder_pct"]:.0f}%)</span>'
            f'<span class="stat-pill">Full position <b>${dep["target"]:,.0f}</b> '
            f'({target_pct:.1f}%)</span>'
            f'{_buy_line}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if dep["partial"]:
            # A table, not markdown lines: Streamlit reads a pair of dollar
            # signs on one line as LaTeX, so "$355 of $1,440" came out as
            # italic maths. Amounts also line up when they share a column.
            _pt_head = f'padding:4px 8px;color:{T["text_muted"]};font-weight:600'
            _pt_num = f'padding:4px 8px;text-align:right;border-top:1px solid {T["divider"]}'
            _pt_txt = f'padding:4px 8px;border-top:1px solid {T["divider"]}'
            _pt_rows = "".join(
                f'<tr>'
                f'<td style="{_pt_txt}"><b>{_p["ticker"]}</b></td>'
                f'<td style="{_pt_num};color:{T["text_muted"]}">${_p["market_value"]:,.0f}</td>'
                f'<td style="{_pt_num};color:{T["text_muted"]}">${dep["target"]:,.0f}</td>'
                f'<td style="{_pt_num}"><b>${_p["gap"]:,.0f}</b></td>'
                f'</tr>'
                for _p in dep["partial"]
            )
            with st.expander(f'Positions with room ({len(dep["partial"])})'):
                st.markdown(
                    f'<table style="width:100%;border-collapse:collapse;font-size:0.9rem">'
                    f'<thead><tr>'
                    f'<th style="{_pt_head};text-align:left">Ticker</th>'
                    f'<th style="{_pt_head};text-align:right">Now</th>'
                    f'<th style="{_pt_head};text-align:right">Target</th>'
                    f'<th style="{_pt_head};text-align:right">To fill</th>'
                    f'</tr></thead><tbody>{_pt_rows}</tbody></table>',
                    unsafe_allow_html=True,
                )

        # ── Target size ──
        # Centred and narrow: it is a setting, not the point of the card. The
        # explanation lives in the label's help tooltip rather than beside it,
        # where a standing sentence competed with the figures above.
        _, _ts_mid, _ = st.columns([1.5, 1, 1.5])
        with _ts_mid:
            _new_pct = st.number_input(
                "Full position %",
                min_value=0.5, max_value=50.0, step=0.5, format="%.1f",
                value=target_pct, key="_target_pos_input",
            )
        if abs(_new_pct - target_pct) > 1e-9:
            st.session_state["_target_pos_pct"] = _new_pct
            _prefs_now = load_user_prefs(_sb_client)
            _prefs_now["target_position_pct"] = _new_pct
            save_user_prefs(_sb_client, _prefs_now)
            st.rerun()

    @st.fragment(run_every=timedelta(seconds=30))
    def _portfolio_cards():
        # Fetch fresh prices + account balances (every connected broker, since
        # the hero card's headline is their sum).
        #
        # On the run that just loaded the page, _load_portfolio_data has
        # already quoted these same tickers seconds ago; quoting them again
        # bought nothing and cost a second round trip on the slowest load
        # there is. Later runs are this fragment's own 30-second timer, which
        # is the whole point of it, so those still fetch.
        _fresh = time.time() - (st.session_state.get("portfolio_prices_at") or 0)
        if _fresh < 20 and st.session_state.get("portfolio_prices"):
            prices = st.session_state["portfolio_prices"]
        else:
            prices = fetch_current_prices(held_tickers, _portfolio_isins(held))
            st.session_state["portfolio_prices"] = prices
            st.session_state["portfolio_prices_at"] = time.time()

        for ticker, data in held.items():
            price_data = _row_quote(prices.get(data.get("symbol", ticker)), data)
            shares = data["shares_held"]
            if price_data and shares > 0:
                p = price_data["price"]
                data["current_price"] = p
                data["previous_close"] = price_data.get("previousClose") or p
                data["market_value"] = p * shares
            elif price_data:
                data["current_price"] = price_data["price"]
                data["previous_close"] = price_data.get("previousClose") or price_data["price"]

        # ── Hero card ──
        # The header says "Net Liquidating Value" with no broker qualifier, so
        # it has to be every broker's — one account's balance under that label
        # is simply the wrong number while money sits at two brokers.
        try:
            _bal_by_broker, _bal_failures = _cached_all_balances()
        except Exception as e:
            logger.warning("Account balances fetch failed: %s", e)
            _bal_by_broker, _bal_failures = {}, []
        # Read from session_state, not the closure: this fragment re-runs on its
        # own timer and must follow whichever broker view is on screen now.
        _view = st.session_state.get("_portfolio_view", "Overview")
        if _view != "Overview":
            _bal_by_broker = {k: v for k, v in _bal_by_broker.items() if k == _view}
            _bal_failures = [f for f in _bal_failures if f[0] == _view]
        if _bal_by_broker:
            net_liq = sum(b.get("net_liquidating_value") or 0.0
                          for b in _bal_by_broker.values())
            cash = sum(b.get("cash_balance") or 0.0
                       for b in _bal_by_broker.values())
        else:
            net_liq = sum(d["market_value"] for d in held.values())
            cash = 0.0

        total_value = sum(d["market_value"] for d in held.values())
        total_prev = sum(d.get("previous_close", 0) * d["shares_held"] for d in held.values())
        day_chg_pct = ((total_value - total_prev) / total_prev * 100) if total_prev else 0.0
        day_chg_cls = "hero-green" if day_chg_pct >= 0 else "hero-red"
        day_chg_sign = "+" if day_chg_pct >= 0 else ""

        day_chg_dollar = total_value - total_prev
        day_dollar_sign = "+" if day_chg_dollar >= 0 else ""
        nlv_cls = "hero-green" if net_liq >= 0 else "hero-red"

        # With two brokers the headline is a sum, and a sum with no breakdown
        # can't be checked against either account.
        _broker_pills = ""
        if len(_bal_by_broker) > 1:
            _broker_pills = "".join(
                f'<span class="stat-pill">{_bn} '
                f'<b>${(_bb.get("net_liquidating_value") or 0.0):,.0f}</b></span>'
                for _bn, _bb in _bal_by_broker.items()
            )

        st.markdown(
            f'<div class="hero-card">'
            f'<p class="hero-label">Net Liquidating Value</p>'
            f'<p class="hero-value {nlv_cls}">${net_liq:,.0f}</p>'
            f'<p class="hero-sub"><span class="{day_chg_cls}">{day_chg_sign}{day_chg_pct:.2f}% ({day_dollar_sign}${abs(day_chg_dollar):,.0f})</span> today &nbsp;·&nbsp; {len(held)} active positions</p>'
            f'<div class="stat-row">'
            f'<span class="stat-pill">Cash <b>${cash:,.0f}</b></span>'
            f'{_broker_pills}'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        if _bal_failures:
            st.caption(
                "Balance unavailable for "
                + ", ".join(n for n, _ in _bal_failures)
                + " — the value above excludes it."
            )

        # ── Column picker & Sort ──
        # Wheel-specific columns only mean something once a position has option
        # legs. A Trading 212 account is plain buy-and-hold, so Wheel Basis,
        # Premie, Days and Ann. % would sit there empty or — worse — show a
        # figure reconstructed from trades that don't exist. Offer them only
        # when there is a wheel to describe.
        # Option legs, not cycles. detect_wheels returns a cycle for any share
        # position — a plain buy sits in an "active" one — so testing for
        # cycles said every Tastytrade account wheels. NFLX is eight shares
        # bought once with no option ever written against them.
        _has_wheels = any(
            "Option" in (t.get("instrument_type") or "")
            for d in held.values() for t in d.get("trades", [])
        )
        _wheel_only = ["Break-even", "Ann. %", "Premie", "Days"]

        # "Avg Cost", not "Holdings": the figure is a price per share sitting
        # directly beside Current Price, and cost basis normally means the whole
        # position's acquisition value. The old label also read as though it had
        # something to do with the wheel — that one is "Break-even", and it only
        # appears where there are wheels.
        all_cols = ["Shares", "Avg Cost", "Break-even", "Current Price",
                    "Day %", "Mkt Value", "Unrealized P/L", "Return %", "Ann. %",
                    "Premie", "Days", "Weight", "Margin", "Margin %"]
        default_cols = ["Shares", "Avg Cost", "Current Price", "Day %",
                        "Mkt Value", "Unrealized P/L", "Return %", "Weight"]
        # No valuation columns here. Selling is not a call this table makes —
        # a fair value belongs to the decision to buy, and reading "37% above
        # fair value" beside a holding invites a trade the strategy does not
        # take. The watchlist is where valuation lives.
        if not _has_wheels:
            all_cols = [c for c in all_cols if c not in _wheel_only]
            default_cols = [c for c in default_cols if c not in _wheel_only]
        # With one broker every row would say the same thing; with two, the
        # same ticker can appear twice and the column is the only thing
        # telling the rows apart.
        _multi_broker = len({d.get("broker") for d in held.values() if d.get("broker")}) > 1
        if _multi_broker:
            all_cols.insert(0, "Broker")
            default_cols.insert(0, "Broker")
        sort_options = ["Ticker", "Weight", "Day %", "Return %", "Unrealized P/L", "Mkt Value", "Ann. %", "Margin %"]

        with st.container(key="toolbar_inline"):
            col_left, col_right = st.columns(2)
            with col_left:
                with st.popover("\u2699 Columns"):
                    selected = st.pills(
                        "Toggle columns",
                        all_cols,
                        default=default_cols,
                        selection_mode="multi",
                        label_visibility="collapsed",
                    )
            with col_right:
                with st.popover("\u2195 Sort"):
                    sort_by = st.pills(
                        "Sort by",
                        sort_options,
                        default="Ticker",
                        label_visibility="collapsed",
                    )

        # ── Build rows ──
        rows = []
        for ticker, data in held.items():
            _trades = data.get("trades", [])
            wheels = data.get("wheels", [])
            _cycle = wheels[-1] if wheels else None
            # A cycle is not a wheel. detect_wheels opens one for any share
            # position, so NFLX (eight shares, no option ever) and MSFT were
            # given an adjusted basis and a premium column for a trade that was
            # never made.
            last_wheel = _cycle if _cycle and has_option_legs(_trades) else None

            wheel_equity_cost = 0.0
            wheel_option_pl = 0.0

            # FIFO cost of the shares still held, from the whole history rather
            # than the current cycle's buys. Averaging every buy in the cycle
            # stopped being the purchase price as soon as part of the position
            # was sold: IBIT read 52.65 for 120 shares of which 20 were gone.
            _cost, _lot_shares = held_share_cost(_trades)
            # Only trust the lots when they account for the shares actually
            # held. A history that starts after the position did (a transfer
            # in, or an API returning only recent orders) would otherwise give
            # a confident wrong purchase price — worse than the broker's own
            # average.
            _lots_ok = lots_cover(_lot_shares, data["shares_held"]) and _lot_shares
            purchase_price = (_cost / _lot_shares) if _lots_ok else 0.0
            if not purchase_price:
                purchase_price = data.get("purchase_price") or display_basis(
                    data["cost_per_share"]
                )

            if last_wheel:
                for t in last_wheel["trades"]:
                    if t["instrument_type"] == "Equity":
                        # Dividends arrive as Equity rows too; they are income,
                        # not part of what the shares cost.
                        if (t.get("type") or "") != "Money Movement":
                            wheel_equity_cost += t["net_value"]
                    elif "Option" in t["instrument_type"]:
                        wheel_option_pl += t["net_value"]

                shares = data["shares_held"]
                wheel_cps = (wheel_equity_cost + wheel_option_pl) / shares if shares else 0.0
            else:
                wheel_cps = -purchase_price

            unrealized = data["market_value"] + wheel_equity_cost if last_wheel else data["market_value"] + data["equity_cost"]
            # The cycle start is the purchase date whether or not options were
            # written, so a plain holding keeps a real holding period.
            days_held = (date.today() - _cycle["start"]).days if _cycle else 0

            prev = data.get("previous_close", 0)
            cur = data["current_price"]
            day_change_pct = ((cur - prev) / prev * 100) if prev else 0.0

            initial_investment = abs(wheel_equity_cost) if last_wheel else abs(data["equity_cost"])
            return_pct = (unrealized / initial_investment * 100) if initial_investment else 0.0
            if days_held > 0 and initial_investment:
                ann_return = ((1 + unrealized / initial_investment) ** (365 / days_held) - 1) * 100
            else:
                ann_return = 0.0

            shares = data["shares_held"]
            break_even = display_basis(wheel_cps) if last_wheel and shares else purchase_price

            symbol = data.get("symbol", ticker)
            rows.append({
                # The position's key in `held`. Kept because "Ticker" is the
                # bare symbol and two brokers can supply the same one — looking
                # anything up by symbol would then hit the wrong position.
                "_key": ticker,
                "Logo": _logo_img(symbol, data.get("isin")),
                "Ticker": symbol,
                "Broker": data.get("broker", ""),
                "Shares": shares,
                "Avg Cost": purchase_price,
                "Break-even": break_even,
                "Current Price": cur,
                "Day %": day_change_pct,
                "Mkt Value": data["market_value"],
                "Unrealized P/L": unrealized,
                "Return %": return_pct,
                "Ann. %": ann_return,
                "Premie": wheel_option_pl if last_wheel else 0.0,
                "Days": days_held,
                # None where the band cannot be trusted, so the cell reads "—"
                # rather than a signal built on a broken DCF.
            })

        # ── Per-position margin requirements ──
        try:
            _margin_reqs = _cached_margin_requirements(get_active_broker())
        except Exception as e:
            logger.debug("Margin requirements fetch failed: %s", e)
            _margin_reqs = {}

        for row in rows:
            # Against net liq, not against the positions' own total. Cash is a
            # real allocation — 15% of it at the time of writing — and dividing
            # it out made every holding look larger than it is: PEP read 56.5%
            # of the portfolio where it is 47.8% of the money. It also puts
            # this column on the same denominator as the Deployment card, so
            # one screen stops giving two answers to "how big is this".
            #
            # Falls back to the invested total when balances are unavailable,
            # which is the old behaviour and better than a blank column.
            _weight_base = net_liq if net_liq > 0 else total_value
            row["Weight"] = row["Mkt Value"] / _weight_base * 100 if _weight_base else 0.0
            _mr = _margin_reqs.get(row["Ticker"], {})
            row["Margin"] = _mr.get("margin_requirement", 0)
            _mv = row["Mkt Value"]
            row["Margin %"] = (row["Margin"] / _mv * 100) if _mv > 0 else 0

        # ── Sort rows ──
        if sort_by == "Ticker":
            rows.sort(key=lambda r: r["Ticker"])
        else:
            # A row with no valuation sorts last rather than raising: None is
            # "we don't know", not the smallest number.
            rows.sort(key=lambda r: (r.get(sort_by) is not None, r.get(sort_by) or 0),
                      reverse=True)

        # ── Format helpers ──
        color_cols_set = {"Unrealized P/L", "Day %", "Return %", "Ann. %"}

        def _fmt_cell(col, val, row=None):
            cls = ""
            if val is None:
                return "—", ""
            if col in color_cols_set:
                cls = " pf-green" if val > 0 else " pf-red" if val < 0 else ""
            if col in ("Avg Cost", "Break-even", "Current Price"):
                return f"${val:,.2f}", cls
            if col == "Mkt Value":
                return f"${val:,.0f}", cls
            if col == "Unrealized P/L":
                return f"${val:+,.0f}", cls
            if col == "Premie":
                return f"${val:,.0f}", cls
            if col in ("Day %", "Return %", "Ann. %"):
                return f"{val:+.2f}%", cls
            if col == "Weight":
                return f"{val:.1f}%", cls
            if col == "Margin":
                return f"${val:,.0f}", cls
            if col == "Margin %":
                return f"{val:.0f}%", cls
            if col == "Shares":
                # Trading 212 handelt in fracties: 0,8156 Hermes las als
                # "0" naast een marktwaarde van $1.259.
                if float(val).is_integer():
                    return f"{val:,.0f}", cls
                return f"{val:,.4f}".rstrip("0"), cls
            if col == "Days":
                return f"{int(val)}", cls
            return f"{val}", cls

        # ── Detect open options per ticker ──
        opts_by_ticker = {}
        for ticker, data in held.items():
            open_opts = _find_open_options(data.get("trades", []))
            if open_opts:
                opts_by_ticker[ticker] = open_opts

        # ── Render cards ──
        # Two fixed tracks for the logo and the ticker, then one track per
        # selected column — sized in proportion to what that column actually
        # holds, not equally. Equal tracks gave "32" the same room as
        # "$1,796" and left the short ones swimming, which reads as a wide gap
        # between every pair of figures.
        #
        # Character counts of the rendered values, floored so a column never
        # collapses under its own heading. Digits are near enough to a fixed
        # width for this to hold; being a character or two out only shifts a
        # boundary, and every row shifts with it because they share the list.
        def _track_weight(col):
            widest = max((len(_fmt_cell(col, r[col], r)[0]) for r in rows),
                         default=0)
            # The label is set two-thirds the size of the value, so it costs
            # roughly 0.6 of a value character apiece. Floored on that so a
            # heading like "Unrealized P/L" keeps its own line — a wrapped
            # label makes its row taller than the ones around it.
            return max(widest, len(col) * 0.6, 4)

        # ch, not fr. Fractions share out whatever the row does not use, so a
        # full-width row spreads its slack between the figures however little
        # they need — which is the gap that kept looking too wide. Absolute
        # tracks take only what the content asks for and leave the remainder at
        # the ends, where it reads as margin instead of as distance.
        _tracks = " ".join(f"{_track_weight(c) + 1.5:.1f}ch" for c in selected)
        cards_html = (
            f'<div class="portfolio-cards pf-aligned" style="--pf-grid:'
            f'30px 52px {_tracks}">')
        for row in rows:
            cells = ""
            for col in selected:
                fval, cls = _fmt_cell(col, row[col], row)
                cells += (
                    f'<div class="pf-cell">'
                    f'<span class="pf-label">{col}</span>'
                    f'<span class="pf-val{cls}">{fval}</span>'
                    f'</div>'
                )
            card_inner = (
                f'<div class="portfolio-card">'
                f'{row["Logo"]}'
                f'<span class="pf-ticker">{row["Ticker"]}</span>'
                f'{cells}'
                f'</div>'
            )

            ticker = row["Ticker"]
            open_opts = opts_by_ticker.get(row["_key"])

            if open_opts:
                # Build option sub-cards
                opt_cards = ''
                for opt in open_opts:
                    strike_str = f"${opt['strike']:,.2f}" if opt["strike"] else "—"
                    exp_str = opt["expiration"] or "—"
                    prem_cls = " pf-green" if opt["premium"] > 0 else " pf-red" if opt["premium"] < 0 else ""
                    opt_cards += (
                        f'<div class="portfolio-card" style="border-style:dashed;margin-top:6px">'
                        f'<span class="pf-ticker" style="min-width:40px">{opt["type"]}</span>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Strike</span>'
                        f'<span class="pf-val">{strike_str}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Expiration</span>'
                        f'<span class="pf-val">{exp_str}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Qty</span>'
                        f'<span class="pf-val">{opt["quantity"]}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Premium</span>'
                        f'<span class="pf-val{prem_cls}">${opt["premium"]:+,.0f}</span>'
                        f'</div>'
                        f'</div>'
                    )
                cards_html += (
                    f'<details class="pf-details">'
                    f'<summary>{card_inner}</summary>'
                    f'{opt_cards}'
                    f'</details>'
                )
            else:
                cards_html += card_inner

        cards_html += '</div>'
        st.markdown(cards_html, unsafe_allow_html=True)

    prewarm_logos([(d.get("symbol") or t) for t, d in held.items()
                   if not d.get("isin")])
    _portfolio_cards()

    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(key="deployment_block"):
        _deployment_overview()

    # ── Contribution & Relative performance ──
    @st.cache_data(ttl=3600, show_spinner=False)
    def _cached_index_closes(symbol="SPY", years=5):
        return gather_data.fetch_daily_closes(symbol, years)

    _perf_rows = []
    _index_closes = {}
    try:
        _index_closes = _cached_index_closes()
    except Exception as e:
        logger.warning("Index history unavailable: %s", e)
    _today = date.today()
    for _tk, _d in held.items():
        _sym = _d.get("symbol", _tk)
        _mv = _d.get("market_value") or 0.0
        # Contribution is in dollars: a 40% gain on a 1% position moved nothing.
        _contrib = _mv + (_d.get("equity_cost") or 0.0) + (_d.get("option_pl") or 0.0)
        _rel = None
        _lots = open_lots(_d.get("trades") or [])
        # Same guard as the cost basis: measuring a position against the index
        # from lots that do not add up to it compares the wrong money over the
        # wrong window.
        if _index_closes and _lots and lots_cover(
            sum(lot["quantity"] for lot in _lots), _d.get("shares_held")
        ):
            _rel = relative_performance(
                _lots, _d.get("current_price") or 0.0, _index_closes, _today,
            )
        _perf_rows.append({
            "ticker": _sym, "broker": _d.get("broker", ""),
            "contribution": _contrib, "market_value": _mv, "rel": _rel,
        })

    _by_contrib = sorted(_perf_rows, key=lambda r: r["contribution"], reverse=True)
    # Net liq, matching the positions table and the Deployment card. Dividing
    # by the positions' own total instead would make this card disagree with
    # the one above it about how big the same holding is.
    try:
        _nl_by_broker, _ = _cached_all_balances()
        _nl_view = st.session_state.get("_portfolio_view", "Overview")
        if _nl_view != "Overview":
            _nl_by_broker = {k: v for k, v in _nl_by_broker.items() if k == _nl_view}
        _pf_value = sum(b.get("net_liquidating_value") or 0.0
                        for b in _nl_by_broker.values())
    except Exception as e:
        logger.debug("Net liq unavailable for contribution weights: %s", e)
        _pf_value = 0.0
    if not _pf_value:
        _pf_value = sum(r["market_value"] for r in _perf_rows)
    _rated = [r for r in _perf_rows if r["rel"] and r["rel"]["alpha"] is not None]
    _behind = sorted([r for r in _rated if r["rel"]["alpha"] < 0],
                     key=lambda r: r["rel"]["alpha"])

    # Cells of ONE grid, not a grid per row. A row of its own could only ever
    # align with itself: the 1fr column is resolved against that row's
    # container, so "146d" and "250d" sized their track differently from "70d"
    # and the last rows sat inset. Shared column tracks is what a grid is for.
    _cell = f'border-top:1px solid {T["divider"]};padding:5px 0'

    def _row_html(label, value, color, mid=""):
        return (
            f'<span style="{_cell};color:{T["text"]};overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap;text-align:left">{label}</span>'
            f'<span style="{_cell};text-align:right;font-size:0.75rem;'
            f'font-variant-numeric:tabular-nums;color:{T["text_muted"]}">{mid}</span>'
            f'<span style="{_cell};text-align:right;font-weight:600;'
            f'font-variant-numeric:tabular-nums;color:{color}">{value}</span>'
        )

    def _help_icon(text):
        """A "?" carrying its explanation in the native tooltip.

        st.help isn't reachable from inside raw card HTML, and a title
        attribute needs no script.
        """
        import html as _html
        _t = _html.escape(text, quote=True)
        # Zero-width wrapper: the icon renders beside the title but is not
        # counted when the title is centred, so "vs S&P 500" sits on the card's
        # centre line rather than pushed left by its own help icon.
        return (
            f'<span style="display:inline-block;width:0;overflow:visible;'
            f'white-space:nowrap">'
            f'<span title="{_t}" style="cursor:help;margin-left:6px;'
            f'font-size:0.7rem;font-weight:600;color:{T["text_muted"]};'
            f'border:1px solid {T["border_medium"]};border-radius:50%;'
            f'padding:0 5px;vertical-align:middle">?</span>'
            f'</span>'
        )

    def _rows_grid(cells):
        # Sized to its content and centred by the card's flexbox. Columns are
        # auto rather than 1fr so the table does not stretch: the ticker and
        # its figure belong next to each other, not on opposite edges.
        #
        # tabular-nums on the numeric columns keeps the digits from jittering
        # between rows once the tracks themselves stop moving.
        return (
            f'<div style="display:grid;grid-template-columns:auto auto auto;'
            f'column-gap:14px;align-items:baseline">{cells}</div>'
        )

    _card_htmls = []

    # Contribution: what actually moved the portfolio, in money. Every position,
    # ranked. A curated top and bottom three implied three names that mattered
    # and put RDDT's -23 beside PEP's -2,598; a complete ranked list makes the
    # same point without the implication, and the sizes speak for themselves.
    if _perf_rows:
        _total_contrib = sum(r["contribution"] for r in _perf_rows)
        _rows = "".join(
            _row_html(r["ticker"], f'${r["contribution"]:+,.0f}',
                      T["accent"] if r["contribution"] >= 0 else T["red"],
                      mid=f'{r["market_value"] / _pf_value * 100:.0f}%'
                          if _pf_value else "")
            for r in _by_contrib
        )
        _card_htmls.append(
            f'<div class="hero-card">'
            f'<h4>Contribution</h4>'
            f'<div style="text-align:center;margin-bottom:12px">'
            f'<span style="font-size:1.8rem;font-weight:700;'
            f'color:{T["accent"] if _total_contrib >= 0 else T["red"]}">'
            f'${_total_contrib:+,.0f}</span>'
            f'<div style="font-size:0.75rem;color:{T["text_muted"]}">'
            f'open positions, unrealized &nbsp;·&nbsp; middle column is weight</div></div>'
            f'{_rows_grid(_rows)}'
            f'</div>'
        )

    # Relative performance: is each name earning its place against the index?
    if _rated:
        _rows = "".join(
            # The holding period rather than a verdict on it: a reader can
            # discount three weeks of relative performance without being told
            # to, and "too early" was landing on five of seven positions.
            _row_html(
                r["ticker"],
                f'{r["rel"]["alpha"]:+.0f} pts',
                T["accent"] if r["rel"]["alpha"] >= 0 else T["red"],
                mid=f'{r["rel"]["days_held"]}d',
            )
            for r in sorted(_rated, key=lambda r: r["rel"]["alpha"], reverse=True)
        )
        _n_behind = len(_behind)
        _summary_color = T["red"] if _n_behind > len(_rated) / 2 else T["accent"]
        _uncovered = sum(1 for r in _perf_rows
                         if r["rel"] and r["rel"]["alpha"] is None)
        _no_dates = len(_perf_rows) - len(_rated) - _uncovered
        _missing = _uncovered + _no_dates
        # The caveats belong with the card, not under it: three lines of grey
        # type at the bottom made the two cards different heights and pushed
        # the reader past the numbers to reach them.
        _note = (
            "Price return since each purchase, against SPY over the same days. "
            "Dividends counted on neither side."
            + (f" {_missing} position(s) have no purchase date to measure from."
               if _missing else "")
        )
        _card_htmls.append(
            f'<div class="hero-card">'
            f'<h4>vs S&amp;P 500{_help_icon(_note)}</h4>'
            f'<div style="text-align:center;margin-bottom:12px">'
            f'<span style="font-size:1.8rem;font-weight:700;color:{_summary_color}">'
            f'{_n_behind} of {len(_rated)}</span>'
            f'<div style="font-size:0.75rem;color:{T["text_muted"]}">'
            f'behind the index since you bought</div></div>'
            f'{_rows_grid(_rows)}'
            f'</div>'
        )

    if _card_htmls:
        st.markdown(
            f'<div class="greeks-grid">{"".join(_card_htmls)}</div>',
            unsafe_allow_html=True,
        )
    # ── Portfolio Exposure (loads independently via fragment) ──
    @st.cache_data(ttl=86400, show_spinner=False)
    def _cached_ticker_profiles(tickers_tuple, _v=2):
        return fetch_ticker_profiles(list(tickers_tuple))

    @st.fragment
    def _portfolio_exposure():
        st.markdown("<h4 style='text-align:center'>Portfolio Allocation</h4>", unsafe_allow_html=True)
        try:
            with st.spinner("Loading sector & country data..."):
                profiles = _cached_ticker_profiles(tuple(held_tickers), _v=2)
            total_mv = sum(d["market_value"] for d in held.values())

            if total_mv > 0:
                sector_values = {}
                country_values = {}
                for ticker, data in held.items():
                    mv = data["market_value"]
                    profile = profiles.get(data.get("symbol", ticker), {})
                    sector = profile.get("sector", "Unknown")
                    country = profile.get("country", "Unknown")
                    sector_values[sector] = sector_values.get(sector, 0) + mv
                    country_values[country] = country_values.get(country, 0) + mv

                sector_sorted = sorted(sector_values.items(), key=lambda x: x[1], reverse=True)
                country_sorted = sorted(country_values.items(), key=lambda x: x[1], reverse=True)

                EXPOSURE_COLORS = [
                    '#81b29a', '#3d405b', '#e07a5f', '#f2cc8f', '#9b8ec4',
                    '#64b5f6', '#e57373', '#81c784', '#ffb74d', '#4dd0e1',
                    '#ba68c8', '#a1887f',
                ]

                def _donut_chart(labels, values):
                    fig = go.Figure(data=[go.Pie(
                        labels=labels,
                        values=values,
                        hole=0.55,
                        textinfo='label+percent',
                        textposition='outside',
                        marker=dict(colors=EXPOSURE_COLORS[:len(labels)]),
                        hovertemplate='%{label}<br>$%{value:,.0f}<br>%{percent}<extra></extra>',
                        pull=[0.02] * len(labels),
                    )])
                    fig.update_layout(
                        showlegend=True,
                        legend=dict(
                            orientation="h",
                            yanchor="top",
                            y=-0.12,
                            xanchor="center",
                            x=0.5,
                            font=dict(size=12, color=T['chart_font']),
                        ),
                        margin=dict(t=40, b=60, l=20, r=20),
                        height=520,
                        font=dict(
                            family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                            color=T['chart_font'],
                        ),
                        paper_bgcolor=T['chart_paper'],
                        plot_bgcolor=T['chart_plot'],
                    )
                    return fig

                tab_sector, tab_country = st.tabs(["By Sector", "By Country"])

                with tab_sector:
                    labels = [s[0] for s in sector_sorted]
                    values = [s[1] for s in sector_sorted]
                    st.plotly_chart(_donut_chart(labels, values), width="stretch", key="donut_sector")

                with tab_country:
                    labels = [c[0] for c in country_sorted]
                    values = [c[1] for c in country_sorted]
                    st.plotly_chart(_donut_chart(labels, values), width="stretch", key="donut_country")

        except Exception as e:
            st.warning(f"Could not load portfolio exposure: {e}")

    with st.container(key="allocation_block"):
        _portfolio_exposure()



# ══════════════════════════════════════════════════════
#  COST BASIS PAGE — Per-ticker cost basis and trade history
# ══════════════════════════════════════════════════════

elif page == "Holdings":
    if not has_active_broker():
        _render_connect_prompt()


    st.markdown("")
    cost_basis = _load_portfolio_data()

    # Same picker as the Portfolio page, and the same reason: a card here is
    # meant to be laid next to the broker's own screen, which only works if you
    # can narrow the page to that broker.
    _cb_view = _broker_view_control("Holdings")
    if _cb_view != "Overview":
        cost_basis = {t: d for t, d in cost_basis.items()
                      if d.get("broker") == _cb_view}
        if not cost_basis:
            st.info(f"No positions at {_cb_view}.")
            st.stop()
    # The track record is computed on these, per broker account, before the
    # symbol merge below: FIFO runs inside one account, and merging first let
    # a Tastytrade sale consume a Trading 212 lot.
    _cost_basis_by_broker = dict(cost_basis)
    if _cb_view == "Overview":
        cost_basis = merge_by_symbol(cost_basis)

    def _is_put(t):
        """Check if trade is put via OCC symbol, fallback to description."""
        _, _, cp = _parse_option_symbol(t.get("symbol"))
        if cp:
            return cp == "P"
        return "Put" in (t.get("description") or "")

    def _is_call(t):
        """Check if trade is call via OCC symbol, fallback to description."""
        _, _, cp = _parse_option_symbol(t.get("symbol"))
        if cp:
            return cp == "C"
        return "Call" in (t.get("description") or "")

    # ── Helper: detect if a ticker has an active position ──
    def _is_active(data):
        """Active = shares held or any open option positions."""
        if data["shares_held"] > 0:
            return True
        return _has_open_options(data)

    # ── Helper: categorize trades ──
    def _categorize(trades):
        csp = [t for t in trades if t.get("label") in ("CSP", "BTC CSP") or (t.get("label") == "Expired" and "Put" in (t.get("description") or ""))]
        cc = [t for t in trades if t.get("label") in ("CC", "BTC CC") or (t.get("label") == "Expired" and "Call" in (t.get("description") or ""))]
        sh = [t for t in trades if t.get("instrument_type") == "Equity" or t.get("label") == "Dividend"]
        return csp, cc, sh

    # ── Helper: render trade rows ──
    def _render_trades(trades):
        if not trades:
            st.caption("No trades.")
            return
        html = ""
        for t in reversed(trades):
            qty_val = int(t["quantity"]) if t["quantity"] == int(t["quantity"]) else t["quantity"]
            price_str = f'{t["price"]:,.2f}' if t["price"] else "—"
            net = t["net_value"]
            net_color = T['accent'] if net >= 0 else T['red']
            trade_date = t["date"].strftime("%d %b %Y") if hasattr(t["date"], "strftime") else t["date"]

            # Friendly labels for equity trades
            label_raw = t["label"]
            if t.get("instrument_type") == "Equity":
                if label_raw == "Assignment":
                    label_raw = "Buy Shares" if t["net_value"] < 0 else "Sell Shares"
                elif label_raw == "Stock Buy":
                    label_raw = "Buy Shares"
                elif label_raw == "Stock Sell":
                    label_raw = "Sell Shares"

            # Option info: strike + expiration
            strike, exp, _cp = _parse_option_symbol(t.get("symbol"))
            if strike is not None:
                label_str = f'{label_raw} @ {strike:,.2f}'
                date_str = f'{trade_date} &nbsp; exp {exp}' if exp else trade_date
            else:
                label_str = label_raw
                date_str = trade_date

            # One line per trade: what and when on the left, the cash on the
            # right with its sign. A share purchase is money moved into the
            # position, not a loss, so it stays uncoloured; colour is kept for
            # what was earned or paid away -- premium, dividend, tax.
            if label_raw == "Dividend" and net < 0:
                label_str = "Dividend tax"
            if t["quantity"] and t["price"]:
                label_str += (f' <span class="tr-detail">· {qty_val} \u00d7 '
                              f'{price_str}</span>')
            amt_sign = "+" if net >= 0 else "\u2212"
            amt_color = (T["text"] if t.get("instrument_type") == "Equity"
                         and label_raw != "Dividend" else net_color)
            html += (
                f'<div class="trade-row">'
                f'  <div class="tr-desc">'
                f'    <p class="tr-label">{label_str}</p>'
                f'    <p class="tr-date">{date_str}</p>'
                f'  </div>'
                f'  <p class="tr-amt" style="color:{amt_color}">'
                f'{amt_sign}${abs(net):,.2f}</p>'
                f'</div>'
            )
        st.markdown(html, unsafe_allow_html=True)

    # ── Helper: render tabs per trade category ──
    def _render_tabs(trades, key_suffix):
        csp, cc, sh = _categorize(trades)
        # Buy and hold: nothing was ever written, so CSP and CC would be two
        # empty tabs. Show the one list.
        if not has_option_legs(trades):
            _render_trades(sh)
            return
        tab_csp, tab_cc, tab_shares = st.tabs([
            f"CSP ({len(csp)})",
            f"CC ({len(cc)})",
            f"Shares ({len(sh)})",
        ])
        with tab_csp:
            _render_trades(csp)
        with tab_cc:
            _render_trades(cc)
        with tab_shares:
            _render_trades(sh)

    # ── Helper: render a ticker card ──
    def _render_ticker_card(ticker, data):
        pl = data["total_pl_real"]
        shares = data["shares_held"]
        wheels = data.get("wheels", [])
        cur_price = data["current_price"]
        prev_close = data.get("previous_close", cur_price)
        # _load_portfolio_data quotes only what is still held, so a closed
        # card arrived here with 0.00 under "Current Price" while the same
        # quote already sat in _closed_prices for the "If I'd held" line.
        if not cur_price and not _is_active(data):
            _q = _closed_prices.get(data.get("symbol", ticker)) or {}
            cur_price = _q.get("price") or 0.0
            prev_close = _q.get("previousClose") or cur_price

        # Buy price and adjusted cost. A cycle is not a wheel — detect_wheels
        # opens one for any share position — so a ticker that never had an
        # option written against it gets a purchase price and no adjusted
        # basis, rather than a "wheel" figure for a trade never made.
        _card_trades = data.get("trades", [])
        is_wheel = has_option_legs(_card_trades)
        last_wheel = wheels[-1] if wheels and is_wheel else None

        _cost, _lot_shares = held_share_cost(_card_trades)
        # Once the last share is sold there are no lots left, which is why a
        # closed card read "@ 0.00". Fall back to what was paid across every
        # purchase — that does not stop being a fact when you sell.
        buy_price = ((_cost / _lot_shares) if _lot_shares
                     else average_buy_price(_card_trades))
        adj_cost = data["cost_per_share"]
        wheel_equity = 0.0
        wheel_option = 0.0
        if last_wheel:
            for t in last_wheel["trades"]:
                if t["instrument_type"] == "Equity":
                    if (t.get("type") or "") != "Money Movement":
                        wheel_equity += t["net_value"]
                elif "Option" in t["instrument_type"]:
                    wheel_option += t["net_value"]
            if shares > 0:
                adj_cost = (wheel_equity + wheel_option) / shares

        # Day change
        day_chg = ((cur_price - prev_close) / prev_close * 100) if prev_close else 0.0
        day_color = T['accent'] if day_chg >= 0 else T['red']

        with st.container(key=f"wheel_card_{ticker}"):
            all_trades = data.get("trades", [])

            # The view switch is drawn above the transactions, below the
            # figures it also changes, so its value is read from session
            # state here.
            _view_key = f"wheel_view_{ticker}"
            per_wheel = (bool(all_trades) and is_wheel
                         and st.session_state.get(_view_key) == "Per wheel")

            # P/L: last wheel only when toggled, otherwise total
            if per_wheel and last_wheel:
                display_pl = last_wheel["pl"]
                if shares > 0:
                    display_pl += data["market_value"]
            else:
                display_pl = pl

            pl_badge = "pl-badge-green" if display_pl >= 0 else "pl-badge-red"
            pl_sign = "+$" if display_pl >= 0 else "-$"

            # The bare symbol, not the dict key: with two brokers connected the
            # key can carry a broker suffix, and no logo host knows
            # "DECK (Trading 212)". The ISIN fallback is what makes the Amundi
            # ETF show a logo at all.
            _card_symbol = data.get("symbol", ticker)
            _logo_tag = _logo_img(_card_symbol, data.get("isin"), "tk-logo")

            # The verdict against the index: what this name earned minus what
            # the same money in SPY would have, over each lot's own days,
            # premium and dividends counted.
            _tr = _tr_by_ticker.get(_card_symbol)
            _tag = ('<span class="tk-tag">new strategy</span>'
                    if _tr and _tr_new_share.get(_card_symbol, 0.0) >= 0.5 else "")

            # The basics as labelled rows: how many, bought at what and
            # when, where it is now, and how that compares to the S&P.
            def _fmt_day(d):
                if not d:
                    return ""
                if isinstance(d, str):
                    try:
                        d = datetime.fromisoformat(d[:10])
                    except ValueError:
                        return d[:10]
                return f"{d:%d %b %Y}"

            def _row(label, value):
                return (f'<div class="tk-row"><span class="tk-row-label">{label}</span>'
                        f'<span class="tk-row-value">{value}</span></div>')

            _muted = f'color:{T["text_muted"]}'
            _ccy = (data.get("currency") or "USD").upper()
            _cs = _CURRENCY_SYMBOL.get(_ccy, _ccy + " ")
            _is_open = shares > 0
            _start, _n_buys = position_start(_card_trades)
            _shares_txt = f"{shares:,.4f}".rstrip("0").rstrip(".") or "0"
            _rows = [_row("Position", f"{_shares_txt} shares" if _is_open
                          else '<span style="' + _muted + '">closed</span>')]

            _bought = f"{_cs}{buy_price:,.2f}"
            if _start:
                _bought += (f' <span style="{_muted}">on {_fmt_day(_start)}</span>'
                            if _n_buys <= 1 else
                            f' <span style="{_muted}">avg · since {_fmt_day(_start)}'
                            f' · {_n_buys} buys</span>')
            # The adjusted basis only where an option was actually written:
            # for an outright purchase it IS the purchase price, and printing
            # it twice implies a premium that was never collected.
            if is_wheel and _is_open:
                _bought += (f' <span style="{_muted}">· adjusted '
                            f'{_cs}{display_basis(adj_cost):,.2f}</span>')
            _rows.append(_row("Bought", _bought))

            if not _is_open:
                # Only the exit price and date are read here, which do not
                # depend on today's quote; hindsight() just refuses a zero.
                _hs_sold = hindsight(_card_trades, cur_price or 1.0)
                if _hs_sold:
                    _rows.append(_row(
                        "Sold",
                        f'{_cs}{_hs_sold["sale_price"]:,.2f}'
                        + (f' <span style="{_muted}">on {_fmt_day(_hs_sold["closed_on"])}</span>'
                           if _hs_sold.get("closed_on") else "")))

            # No quote is a dash, not 0.00: a closed name Yahoo will not
            # price from this IP is unpriced, not worthless.
            if cur_price:
                _rows.append(_row(
                    "Now", f'{_cs}{cur_price:,.2f} '
                           f'<span style="color:{day_color}">'
                           f'({day_chg:+.1f}% today)</span>'))
            else:
                _rows.append(_row("Now", "\u2014"))

            if _tr:
                _v = _tr["total_alpha_usd"]
                _prem = _tr["total_alpha_usd"] - _tr["alpha_usd"]
                _rows.append(_row(
                    "vs S&amp;P",
                    f'<span style="color:{T["accent"] if _v >= 0 else T["red"]};font-weight:600">'
                    f'${abs(_v):,.0f} {"ahead" if _v >= 0 else "behind"}'
                    f'</span> <span style="{_muted}">· {_tr["total_alpha"]:+.0f} pts'
                    f' · {_tr["days_held"]} days'
                    + (f' · premium {"+" if _prem >= 0 else "-"}${abs(_prem):,.0f}'
                       if abs(_prem) >= 1 else "")
                    + '</span>'))

            # The percentage on the same basis as the S&P line: every lot
            # over its own days, premium and dividends in. Not for a single
            # wheel, whose dollar figure has no matching base here.
            # Click the badge to switch between dollars and percent. A hidden
            # checkbox inside the label carries the state, so it flips in the
            # browser without a rerun.
            _usd_badge = (f'<span class="pl-badge {pl_badge} pl-usd">'
                          f'{pl_sign}{abs(display_pl):,.2f}</span>')
            if _tr and _tr.get("total_return") is not None and not per_wheel:
                _r = _tr["total_return"]
                _pl_html = (
                    f'<label class="pl-toggle" title="Click to switch $ / %">'
                    f'<input type="checkbox" class="pl-toggle-cb">{_usd_badge}'
                    f'<span class="pl-badge {"pl-badge-green" if _r >= 0 else "pl-badge-red"} pl-pct">'
                    f'{_r:+.1f}%</span></label>')
            else:
                _pl_html = _usd_badge

            st.markdown(
                f'<div class="card-header">'
                f'  <div class="card-left"><div class="tk-title">'
                f'    {_logo_tag}<p class="tk-name">{_card_symbol}</p>{_tag}'
                f'  </div></div>'
                f'  <div class="tk-pl">{_pl_html}</div>'
                f'</div>'
                f'<div class="tk-rows">{"".join(_rows)}</div>',
                unsafe_allow_html=True,
            )

            if not all_trades:
                return

            # "All | Per wheel" sits directly above the lists it switches,
            # on its own line, so the expanders keep the card's full width.
            # Without an option ever written there is no wheel to split by,
            # so no switch.
            if is_wheel:
                st.segmented_control("Transactions view", ["All", "Per wheel"],
                                     default="All", key=_view_key,
                                     label_visibility="collapsed")
            _tx_col = st.container()
            with _tx_col:
                if per_wheel:
                    for i, wheel in reversed(list(enumerate(wheels))):
                        status = wheel["status"]
                        w_pl = wheel["pl"]
                        w_pl_sign = "+$" if w_pl >= 0 else "-$"
                        w_start = wheel['start'].strftime("%d-%m-%Y") if hasattr(wheel['start'], 'strftime') else wheel['start']
                        w_end = wheel['end'].strftime("%d-%m-%Y") if hasattr(wheel['end'], 'strftime') else wheel['end']
                        if status == "completed":
                            label = f"Wheel {i + 1} — {w_start} \u2192 {w_end}"
                        elif status == "active":
                            label = f"Wheel {i + 1} (active) — {w_start} \u2192 now"
                        else:
                            label = f"CSP Income — {w_start} \u2192 {w_end}"
                        with st.expander(f"{label}  —  {w_pl_sign}{abs(w_pl):,.2f}"):
                            _render_tabs(wheel["trades"], f"{ticker}_w{i}")
                else:
                    n_total = len(all_trades)
                    with st.expander(f"Transactions ({n_total})"):
                        _render_tabs(all_trades, f"{ticker}_all")

                # ── What holding on would have been worth ──
                # Only for closed positions: the question a closed card exists to
                # answer is whether selling was right, and that needs today's price
                # against what you actually got.
                if shares == 0 and not _has_open_options(data):
                    _px = (_closed_prices.get(_card_symbol) or {}).get("price")
                    _hs = hindsight(_card_trades, _px or 0)
                    if _hs:
                        _d = _hs["delta"]
                        _c = T["red"] if _d > 0 else T["accent"]
                        _move = ((_hs["price_now"] / _hs["sale_price"] - 1) * 100
                                 if _hs["sale_price"] else 0.0)
                        # Percentage first, amount beside it. The percentage is
                        # what the price did since the sale, so its sign describes
                        # the stock rather than the outcome and stops fighting the
                        # colour: +222% in red is "it ran on without you". The
                        # amount stays because a percentage alone flatters small
                        # positions — BYND's -30% is $9 against GOOGL's +93% at
                        # $16,604.
                        # A decimal while it still tells you something, none once
                        # the number is big enough not to need one, and no sign at
                        # all on a move that rounds to nothing — TTD came out as
                        # "-0%", which reads as a rounding artefact rather than the
                        # flat result it is.
                        if abs(_move) < 0.05:
                            _pct = "0%"
                        elif abs(_move) < 10:
                            _pct = f"{_move:+.1f}%"
                        else:
                            _pct = f"{_move:+.0f}%"
                        _label = (f'If I\'d held  ·  :{"red" if _d > 0 else "green"}'
                                  f'[{_pct} (${abs(_d):,.0f})]')
                        with st.expander(_label):
                            if _hs.get("closed_on"):
                                st.caption(
                                    f'Sold {_hs["closed_on"]:%d %b %Y} · '
                                    f'{_hs["shares_sold"]:,.0f} shares'
                                )
                            _th = (f'padding:4px 8px;text-align:right;'
                                   f'color:{T["text_muted"]};font-weight:600')
                            _td = ('padding:4px 8px;text-align:right;'
                                   'font-variant-numeric:tabular-nums')
                            _bd = f'border-top:1px solid {T["divider"]}'
                            st.markdown(
                                f'<table style="width:100%;border-collapse:collapse;'
                                f'font-size:0.85rem">'
                                f'<thead><tr>'
                                f'<th style="{_th};text-align:left"></th>'
                                f'<th style="{_th}">Price</th>'
                                f'<th style="{_th}">Value</th>'
                                f'</tr></thead><tbody>'
                                f'<tr>'
                                f'<td style="{_td};{_bd};text-align:left">Sold at</td>'
                                f'<td style="{_td};{_bd}">${_hs["sale_price"]:,.2f}</td>'
                                f'<td style="{_td};{_bd}">${_hs["proceeds"]:,.0f}</td>'
                                f'</tr><tr>'
                                f'<td style="{_td};{_bd};text-align:left">Today</td>'
                                f'<td style="{_td};{_bd}">${_hs["price_now"]:,.2f}</td>'
                                f'<td style="{_td};{_bd}">${_hs["value_now"]:,.0f}</td>'
                                f'</tr><tr>'
                                f'<td style="{_td};{_bd};text-align:left;'
                                f'font-weight:600">Difference</td>'
                                f'<td style="{_td};{_bd};color:{_c};font-weight:600">'
                                f'{_move:+.1f}%</td>'
                                f'<td style="{_td};{_bd};color:{_c};font-weight:600">'
                                f'${_d:+,.0f}</td>'
                                f'</tr></tbody></table>',
                                unsafe_allow_html=True,
                            )

    # ── Two-column card layout ──
    st.markdown(
        "<style>.block-container { max-width: 1200px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Client-side live search (pure JS, no server roundtrip) ──
    st.markdown(
        f'<input type="text" id="ticker-live-search" placeholder="Search ticker..." '
        f'style="width:100%;padding:10px 14px;font-size:16px;border:1px solid #ddd;'
        f'border-radius:8px;margin-bottom:12px;outline:none;box-sizing:border-box;'
        f'background:{T["input_bg"]};" onfocus="this.style.borderColor=\'#4a90d9\'" '
        f'onblur="this.style.borderColor=\'#ddd\'">',
        unsafe_allow_html=True,
    )

    # ── Split tickers into active / closed ──
    active_tickers = {t: d for t, d in cost_basis.items() if _is_active(d)}
    closed_tickers = {t: d for t, d in cost_basis.items() if not _is_active(d)}

    # A closed position carries no price — _load_portfolio_data only quotes
    # what is still held — and the whole point of the closed cards is what
    # those shares would be worth today.
    @st.cache_data(ttl=300, show_spinner=False)
    def _cached_closed_prices(symbols):
        if not symbols:
            return {}
        try:
            return fetch_current_prices(list(symbols))
        except Exception as e:
            logger.debug("Prices for closed positions unavailable: %s", e)
            return {}

    _closed_prices = _cached_closed_prices(tuple(sorted(
        {(d.get("symbol") or t) for t, d in closed_tickers.items()}
    )))

    # ── Track record: every position ever held, against the index ──
    # The Portfolio page's "vs S&P 500" card stops at what is still held.
    # The sold positions are exactly where a wheel cycle or a change of mind
    # shows what it cost, so this one counts them too, each lot over its own
    # days, and adds a second column with the premium and dividends in.
    @st.cache_data(ttl=3600, show_spinner=False)
    def _cached_index_closes_h(symbol="SPY", years=5):
        return gather_data.fetch_daily_closes(symbol, years)

    _tr_index = {}
    try:
        _tr_index = _cached_index_closes_h()
    except Exception as e:
        logger.warning("Index history unavailable: %s", e)
    _tr_rows = _merge_track_rows(
        _track_record_rows(_cost_basis_by_broker, _tr_index, date.today()))

    # Into the cards, not a block of its own. The card is already where a
    # position is judged -- price, P/L, transactions, "If I'd held" -- so the
    # verdict against the index belongs on it as one line. The totals per
    # strategy are portfolio figures and live in the Results header.
    _tr_by_ticker = {r["ticker"]: r for r in _tr_rows}
    _tr_new_share = {}
    _strat_h = _strategy_start()
    if _tr_rows and _strat_h:
        for r in _merge_track_rows(_track_record_rows(
                _cost_basis_by_broker, _tr_index, date.today(), since=_strat_h)):
            _all_cost = _tr_by_ticker.get(r["ticker"], {}).get("cost") or r["cost"]
            _tr_new_share[r["ticker"]] = r["cost"] / _all_cost if _all_cost else 0.0

    def _render_grid(tickers):
        items = list(tickers.items())
        for i in range(0, len(items), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j < len(items):
                    with col:
                        _render_ticker_card(items[i + j][0], items[i + j][1])

    prewarm_logos([(d.get("symbol") or t) for t, d in cost_basis.items()
                   if not d.get("isin")])

    st.markdown(f"### Active ({len(active_tickers)})")
    if active_tickers:
        _render_grid(active_tickers)
    else:
        st.caption("No active positions.")

    st.markdown(f"### Closed ({len(closed_tickers)})")
    if closed_tickers:
        _render_grid(closed_tickers)
    else:
        st.caption("No closed positions.")

    # ── JS: instant client-side card filtering ──
    components.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const input = doc.getElementById("ticker-live-search");
            if (!input || input.dataset.bound) return;
            input.dataset.bound = "1";
            input.addEventListener("input", function() {
                const q = this.value.toUpperCase();
                // Find all ticker name elements
                const names = doc.querySelectorAll(".tk-name");
                names.forEach(function(el) {
                    // Extract ticker from "TICKER @ 123.45"
                    const ticker = el.textContent.split(" ")[0].toUpperCase();
                    // Traverse up to the stColumn container
                    let col = el.closest('[data-testid="stColumn"]');
                    if (col) {
                        col.style.display = (!q || ticker.includes(q)) ? "" : "none";
                    }
                });
                // Hide empty rows (both columns hidden)
                doc.querySelectorAll('[data-testid="stHorizontalBlock"]').forEach(function(row) {
                    const cols = row.querySelectorAll('[data-testid="stColumn"]');
                    if (cols.length === 0) return;
                    const anyVisible = Array.from(cols).some(c => c.style.display !== "none");
                    row.style.display = anyVisible ? "" : "none";
                });
            });
        })();
        </script>
        """,
        height=0,
    )

# ══════════════════════════════════════════════════════
#  RESULTS PAGE — P/L performance overview
# ══════════════════════════════════════════════════════

elif page == "Results":
    load_profiler.start("Results")

    st.markdown(
        "<style>.block-container { max-width: 1200px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    if not has_active_broker():
        _render_connect_prompt()

    st.markdown("")
    cost_basis = _load_portfolio_data()

    # Same picker as Portfolio and Holdings. The P/L totals and the performer
    # cards are built per position, so they narrow cleanly. Net liq history and
    # deposits do not: Trading 212 exposes neither, so those two blocks stay
    # single-broker and say which one they are showing.
    _res_view = _broker_view_control("Results")
    if _res_view != "Overview":
        cost_basis = {t: d for t, d in cost_basis.items()
                      if d.get("broker") == _res_view}
        if not cost_basis:
            st.info(f"No positions at {_res_view}.")
            st.stop()

    # ── Compute aggregates ──
    total_pl_real = sum(d["total_pl_real"] for d in cost_basis.values())
    total_option_pl = sum(d["option_pl"] for d in cost_basis.values())
    total_dividends = sum(d["dividends"] for d in cost_basis.values())
    active_positions = sum(
        1 for d in cost_basis.values()
        if d["shares_held"] > 0 or _has_open_options(d)
    )

    realized_pl = sum(
        w["pl"] for d in cost_basis.values()
        for w in d.get("wheels", []) if w["status"] == "completed"
    )
    unrealized_pl = sum(
        d["market_value"] + d["equity_cost"]
        for d in cost_basis.values() if d["shares_held"] > 0
    )

    pl_color_class = "hero-green" if total_pl_real >= 0 else "hero-red"
    pl_sign = "+" if total_pl_real >= 0 else ""

    # ── Compute CAGR from net liq history (deposit-adjusted) ──
    # Keyed by view: Overview adds every broker's curve, a broker tab shows
    # only its own. Without the key, switching tabs served the previous
    # account's history under the new tab's heading.
    _nl_key = f"net_liq_all::{_res_view}"
    _tr_key = f"yearly_transfers::{_res_view}"
    cagr_pill = ""
    if _nl_key not in st.session_state:
        try:
            with st.spinner("Loading full net liq history..."):
                with load_profiler.timed("net liq history (all)"):
                    st.session_state[_nl_key] = (
                        fetch_all_net_liq_history("all") if _res_view == "Overview"
                        else fetch_net_liq_history("all"))
        except Exception as e:
            if not _is_auth_error(e):
                logger.warning("Net liq history fetch failed: %s", e)
                log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"component": "net_liq_history"})
            st.session_state[_nl_key] = None
    if _tr_key not in st.session_state:
        try:
            with st.spinner("Loading cash transfer history..."):
                with load_profiler.timed("yearly transfers"):
                    st.session_state[_tr_key] = (
                        fetch_all_yearly_transfers() if _res_view == "Overview"
                        else fetch_yearly_transfers())
        except Exception as e:
            if not _is_auth_error(e):
                logger.warning("Yearly transfers fetch failed: %s", e)
                log_error_with_trace("PORTFOLIO_ERROR", e, page="Portfolio", metadata={"component": "yearly_transfers"})
            st.session_state[_tr_key] = {}

    # Names the rest of the page already reads.
    st.session_state["net_liq_all"] = st.session_state.get(_nl_key)
    st.session_state["yearly_transfers"] = st.session_state.get(_tr_key) or {}

    nl_all_early = st.session_state.get("net_liq_all")
    transfers_early = st.session_state.get("yearly_transfers", {})
    if nl_all_early:
        # Yearly Simple Dietz returns, then compound to CAGR
        df_cagr = pd.DataFrame(nl_all_early)
        df_cagr["time"] = _to_time_col(df_cagr["time"])
        df_cagr = df_cagr.sort_values("time")
        df_cagr["year"] = df_cagr["time"].dt.year
        yr_close = df_cagr.groupby("year")["close"].last()
        yr_list = sorted(yr_close.index)
        compound = 1.0
        for i in range(1, len(yr_list)):
            prev_yr, cur_yr = yr_list[i - 1], yr_list[i]
            start_v = yr_close[prev_yr]
            end_v = yr_close[cur_yr]
            yr_data = transfers_early.get(cur_yr, {})
            net_dep = yr_data["total"] if isinstance(yr_data, dict) and "total" in yr_data else 0.0
            denom = start_v + 0.5 * net_dep
            if denom > 0:
                compound *= (1 + (end_v - start_v - net_dep) / denom)
        days = (df_cagr["time"].iloc[-1] - df_cagr["time"].iloc[0]).days
        n_years = days / 365.25
        if n_years > 0:
            cagr = (compound ** (1 / n_years) - 1) * 100
            cagr_sign = "+" if cagr >= 0 else ""
            cagr_pill = f'<span class="stat-pill">CAGR <b>{cagr_sign}{cagr:.1f}%</b></span>'

    # ── Hero card ──
    portfolio_val_pill = ""
    total_dep_pill = ""
    ytd_pill = ""
    _strat = _strategy_start()
    try:
        _spy_closes_now = _cached_spy_closes()
        vs_spy_pill = _track_record_pill_html(
            _track_record_rows(cost_basis, _spy_closes_now, date.today()), T)
        if _strat:
            vs_spy_pill += _track_record_pill_html(
                _track_record_rows(cost_basis, _spy_closes_now, date.today(), since=_strat),
                T, label=f"vs SPY since {_strat:%b %d}")
    except Exception as e:
        logger.warning("Track record pill unavailable: %s", e)
        vs_spy_pill = ""
    if nl_all_early:
        pv = df_cagr["close"].iloc[-1]
        portfolio_val_pill = f'<span class="stat-pill">Portfolio Value <b>${pv:,.0f}</b></span>'

        total_dep = sum(v["total"] for v in transfers_early.values()) if transfers_early else 0
        total_dep_pill = f'<span class="stat-pill">Total Deposited <b>${total_dep:,.0f}</b></span>'

        true_pl = pv - total_dep
        true_pl_sign = "+" if true_pl >= 0 else ""
        true_pl_class = "hero-green" if true_pl >= 0 else "hero-red"

        # Compute monthly & yearly returns once (reused by Returns section below)
        from datetime import datetime as _dt_cls
        _cur_year = _dt_cls.now().year
        _df_mr = pd.DataFrame(nl_all_early)
        _df_mr["time"] = _to_time_col(_df_mr["time"])
        _df_mr = _df_mr.sort_values("time")
        _df_mr["year"] = _df_mr["time"].dt.year
        _df_mr["month"] = _df_mr["time"].dt.month
        _month_close = _df_mr.groupby(["year", "month"])["close"].last()
        _month_periods = list(_month_close.index)
        _monthly_rets = {}
        for _i in range(1, len(_month_periods)):
            _prev_yr, _prev_mo = _month_periods[_i - 1]
            _yr, _mo = _month_periods[_i]
            _sv = _month_close[(_prev_yr, _prev_mo)]
            _ev = _month_close[(_yr, _mo)]
            _yt = transfers_early.get(_yr, {})
            _md = _yt.get("months", {}).get(_mo, 0) if isinstance(_yt, dict) else 0
            _dn = _sv + 0.5 * _md
            if _dn > 0:
                _ret = (_ev - _sv - _md) / _dn * 100
            else:
                _ret = 0.0
            _monthly_rets.setdefault(_yr, {})[_mo] = _ret
        _yearly_rets = {}
        for _yr, _months in _monthly_rets.items():
            _factor = 1.0
            for _mo in sorted(_months):
                _factor *= (1 + _months[_mo] / 100)
            _yearly_rets[_yr] = round((_factor - 1) * 100, 1)
        # Store for reuse in Returns section
        st.session_state["_cached_monthly_returns"] = _monthly_rets
        st.session_state["_cached_yearly_returns"] = _yearly_rets
        # YTD pill from the cached yearly return
        ytd_ret = _yearly_rets.get(_cur_year)
        if ytd_ret is not None and abs(ytd_ret) > 0.01:
            ytd_sign = "+" if ytd_ret >= 0 else ""
            ytd_pill = f'<span class="stat-pill">YTD <b>{ytd_sign}{ytd_ret:.1f}%</b></span>'

    hero_pl = true_pl if nl_all_early else total_pl_real
    hero_pl_class = true_pl_class if nl_all_early else pl_color_class
    hero_pl_sign = true_pl_sign if nl_all_early else pl_sign

    with st.container(key="results_hero"):
      st.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">Total P/L</p>'
        f'<p class="hero-value {hero_pl_class}">{hero_pl_sign}${abs(hero_pl):,.0f}</p>'
        f'<p class="hero-sub">{active_positions} active positions</p>'
        f'<div class="stat-row">'
        f'{portfolio_val_pill}'
        f'{total_dep_pill}'
        f'{cagr_pill}'
        f'{ytd_pill}'
        f'{vs_spy_pill}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
      )

      # ── Net Liq History chart ──
      period_map = {"1M": "1m", "3M": "3m", "6M": "6m", "YTD": "ytd", "1Y": "1y", "All": "all"}
      if _strat:
          period_map[f"Since {_strat:%b %d}"] = "strategy"
      selected_period = st.pills(
          "Period", options=list(period_map.keys()), default="YTD",
      )
      time_back = period_map[selected_period]
      with st.expander("Strategy start", expanded=False):
          st.caption("The day your current strategy began. Sets the \"Since\" period "
                     "here and splits the track record on Holdings into new and old.")
          _new_start = st.date_input("Strategy start", value=_strat, key="_strategy_start_input")
          if _new_start != _strat:
              _set_strategy_start(_new_start)
              st.rerun()
      # YTD uses 1y data filtered client-side to Jan 1 of current year; the
      # strategy window is cut out of "all" the same way.
      api_time_back = ("1y" if time_back == "ytd"
                       else "all" if time_back == "strategy" else time_back)
      # Follow the tab, exactly like the figures above this chart do. This
      # drew a single broker's curve under an Overview headline: after money
      # moved from Tastytrade to Trading 212 the line fell off a cliff to
      # $7k while Portfolio Value beside it read $29,873, because the drop
      # was real for one account and the rise at the other was never plotted.
      #
      # The view belongs in the cache key too — without it, switching tabs
      # kept showing whichever broker was loaded first.
      cache_key = f"net_liq_{api_time_back}::{_res_view}"
      if cache_key not in st.session_state:
          # The full curve is already loaded above, under _nl_key, and "all"
          # contains every shorter period by definition. Cut the window out of
          # it instead of rebuilding from the same fills — that second build
          # was 3.65s of a 10.35s page. Only fall back to fetching when the
          # full curve is missing, which means its own load failed.
          _all_series = st.session_state.get(_nl_key)
          if _all_series:
              with load_profiler.timed(f"net liq window ({api_time_back}) uit 'all'"):
                  st.session_state[cache_key] = slice_net_liq(
                      _all_series, api_time_back)
          else:
              try:
                  with st.spinner("Loading net liq history..."):
                      with load_profiler.timed(f"net liq history ({api_time_back})"):
                          st.session_state[cache_key] = (
                              fetch_all_net_liq_history(api_time_back)
                              if _res_view == "Overview"
                              else fetch_net_liq_history(api_time_back))
              except Exception as e:
                  logger.warning("Net liq history fetch failed (%s): %s",
                                 api_time_back, e)
                  st.session_state[cache_key] = None

      net_liq_data = st.session_state[cache_key]
      if net_liq_data:
          df_liq = pd.DataFrame(net_liq_data)
          df_liq["time"] = _to_time_col(df_liq["time"])
          df_liq = df_liq.set_index("time")
          if time_back == "ytd":
              df_liq = df_liq[df_liq.index >= f"{pd.Timestamp.now().year}-01-01"]
          elif time_back == "strategy":
              df_liq = df_liq[df_liq.index >= pd.Timestamp(_strat)]
          first_close = df_liq["close"].iloc[0]
          last_close = df_liq["close"].iloc[-1]
          # For YTD, use the deposit-adjusted yearly return (matches hero & Returns)
          _cached_yr = st.session_state.get("_cached_yearly_returns", {})
          _bench_txt = ""
          if time_back == "ytd" and pd.Timestamp.now().year in _cached_yr:
              pct_change = _cached_yr[pd.Timestamp.now().year]
          elif time_back == "strategy":
              # Stortingsgecorrigeerd, want de overstap zelf ging gepaard
              # met geld dat van de ene broker naar de andere liep. En de
              # index over precies dezelfde dagen ernaast, anders is het
              # rendement een getal zonder maatstaf.
              pct_change = _dietz_return(net_liq_data, transfers_early, _strat)
              if pct_change is None:
                  pct_change = ((last_close - first_close) / first_close * 100) if first_close else 0
              try:
                  _spy = _cached_spy_closes()
                  _spy_start = next((_spy[d] for d in sorted(_spy) if d >= _strat), None)
                  if _spy and _spy_start:
                      _spy_pct = (_spy[max(_spy)] / _spy_start - 1) * 100
                      _days = (date.today() - _strat).days
                      _bench_txt = (f' · SPY {"+" if _spy_pct >= 0 else ""}{_spy_pct:.1f}% '
                                    f'over the same {_days} days'
                                    + (" · too early to judge" if _days < 180 else ""))
              except Exception as e:
                  logger.debug("SPY window unavailable: %s", e)
          else:
              pct_change = ((last_close - first_close) / first_close * 100) if first_close else 0
          pct_color = T['accent'] if pct_change >= 0 else T['red']
          pct_sign = "+" if pct_change >= 0 else ""
          st.markdown(
              f'<span style="font-size:1.3rem;font-weight:700;color:{pct_color}">'
              f'{pct_sign}{pct_change:.1f}%</span> '
              f'<span style="color:{T["text_muted"]};font-size:0.85rem">{selected_period}{_bench_txt}</span>',
              unsafe_allow_html=True,
          )
          fig_liq = go.Figure()
          fig_liq.add_trace(go.Scatter(
              x=df_liq.index,
              y=df_liq["close"],
              mode="lines",
              line=dict(color=T['accent'], width=2),
              fill="tozeroy",
              fillcolor=T['accent_fill'],
              hovertemplate="$%{y:,.0f}<extra></extra>",
          ))
          fig_liq.update_layout(
              margin=dict(t=10, b=20, l=40, r=20),
              height=300,
              font=dict(
                  family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                  color=T['chart_font'],
              ),
              paper_bgcolor=T['chart_paper'],
              plot_bgcolor=T['chart_plot'],
              xaxis=dict(gridcolor=T['chart_grid']),
              yaxis=dict(gridcolor=T['chart_grid']),
              showlegend=False,
          )
          st.plotly_chart(fig_liq, width="stretch")
          # Named, because this one cannot be combined: each broker's curve is
          # its own, and in the Overview tab that is not obvious from the tab.
          # Trading 212's is rebuilt from fills and cash movements rather than
          # fetched, so it is a reconstruction — worth saying out loud.
          _t212_on = "t212" in connected_brokers()
          st.caption(
              ("Every connected broker, added per day."
               if _res_view == "Overview"
               else f"{_res_view} only.")
              + (" Trading 212's curve is rebuilt from fills and cash "
                 "movements — it has no history endpoint."
                 if _t212_on else "")
          )
      else:
          st.info("Net liquidation history unavailable.")

    # ── Top / Bottom performers ──
    sorted_tickers = sorted(
        cost_basis.items(), key=lambda x: x[1]["total_pl_real"], reverse=True,
    )
    top5 = sorted_tickers[:5]
    bottom5 = sorted_tickers[-5:][::-1]

    def _performer_cards(items):
        cards = ''
        for ticker, data in items:
            logo = _logo_img(data.get("symbol", ticker), data.get("isin"),
                             "pf-logo")
            pl = data["total_pl_real"]
            pl_cls = " pf-green" if pl > 0 else " pf-red" if pl < 0 else ""
            opt = data["option_pl"]
            opt_cls = " pf-green" if opt > 0 else " pf-red" if opt < 0 else ""
            cards += (
                f'<div class="portfolio-card">'
                f'{logo}'
                f'<span class="pf-ticker">{ticker}</span>'
                f'<div class="pf-cell"><span class="pf-label">Total P/L</span>'
                f'<span class="pf-val{pl_cls}">${pl:+,.0f}</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Options P/L</span>'
                f'<span class="pf-val{opt_cls}">${opt:+,.0f}</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Dividends</span>'
                f'<span class="pf-val">${data["dividends"]:,.0f}</span></div>'
                f'</div>'
            )
        return cards

    st.markdown(
        f'<div class="performer-grid" style="margin:24px 0">'
        f'<div>'
        f'<div class="section-title-bar">Top Performers</div>'
        f'<div class="portfolio-cards">{_performer_cards(top5)}</div>'
        f'</div>'
        f'<div>'
        f'<div class="section-title-bar" style="border-left-color:{T["red"]}">Bottom Performers</div>'
        f'<div class="portfolio-cards">{_performer_cards(bottom5)}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.container(key="cumulative_block"):
        if "benchmark_returns" not in st.session_state:
            try:
                with st.spinner("Loading benchmark data..."):
                    st.session_state["benchmark_returns"] = fetch_benchmark_returns()
            except Exception as e:
                logger.warning("Benchmark returns fetch failed: %s", e)
                st.session_state["benchmark_returns"] = {}

        nl_all = st.session_state.get("net_liq_all")
        bench_returns = st.session_state["benchmark_returns"]
        transfers = st.session_state.get("yearly_transfers", {})

        if nl_all:
            df_nl = pd.DataFrame(nl_all)
            df_nl["time"] = _to_time_col(df_nl["time"])
            df_nl["year"] = df_nl["time"].dt.year
            # Last close per year
            year_close = df_nl.groupby("year")["close"].last()
            port_returns = {}
            years = sorted(year_close.index)
            for i in range(1, len(years)):
                prev_yr, cur_yr = years[i - 1], years[i]
                start_val = year_close[prev_yr]
                end_val = year_close[cur_yr]
                yr_data = transfers.get(cur_yr, {})
                net_dep = yr_data["total"] if isinstance(yr_data, dict) and "total" in yr_data else 0.0
                # Simple Dietz: adjust for deposits/withdrawals (assume mid-year)
                denominator = start_val + 0.5 * net_dep
                if denominator > 0:
                    port_returns[cur_yr] = (end_val - start_val - net_dep) / denominator * 100
                else:
                    port_returns[cur_yr] = 0.0

            # Collect all years across portfolio + all benchmarks
            all_years_set = set(port_returns.keys())
            for b_returns in bench_returns.values():
                all_years_set |= set(b_returns.keys())
            all_years = sorted(all_years_set)

            rows_yr = []
            for yr in all_years:
                if yr in port_returns:
                    row = {"year": str(yr), "portfolio": round(port_returns[yr], 1)}
                    for bench_name, b_returns in bench_returns.items():
                        row[bench_name] = round(b_returns.get(yr, 0), 1) if yr in b_returns else None
                    rows_yr.append(row)
            if rows_yr:
                # ── Cards ──
                cards_html = '<div class="portfolio-cards">'
                for row in reversed(rows_yr):
                    port_val = row["portfolio"]
                    port_cls = " pf-green" if port_val >= 0 else " pf-red"
                    cells = (
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Portfolio</span>'
                        f'<span class="pf-val{port_cls}">{port_val:+.1f}%</span>'
                        f'</div>'
                    )
                    for bench_name in bench_returns:
                        bval = row.get(bench_name)
                        if bval is not None:
                            b_cls = " pf-green" if bval >= port_val else " pf-red"
                            cells += (
                                f'<div class="pf-cell">'
                                f'<span class="pf-label">{bench_name}</span>'
                                f'<span class="pf-val{b_cls}">{bval:+.1f}%</span>'
                                f'</div>'
                            )
                        else:
                            cells += (
                                f'<div class="pf-cell">'
                                f'<span class="pf-label">{bench_name}</span>'
                                f'<span class="pf-val">—</span>'
                                f'</div>'
                            )
                    # Add info icon after last benchmark cell
                    cells += (
                        f'<div class="pf-cell" style="flex:0;min-width:auto;padding:0 4px">'
                        f'<span class="css-tip" data-tip="Green = beat your portfolio · Red = underperformed" '
                        f'style="font-size:0.75rem;color:{T["text_muted"]}">&#9432;</span>'
                        f'</div>'
                    )
                    cards_html += (
                        f'<div class="portfolio-card" style="justify-content:center;text-align:center">'
                        f'<span class="pf-ticker">{row["year"]}</span>'
                        f'{cells}'
                        f'</div>'
                    )
                cards_html += '</div>'

                # ── Total Profit line chart ──
                chart_years = [row["year"] for row in rows_yr]
                LINE_COLORS = ["#81b29a", "#86868b", "#e07a5f", "#f2cc8f"]

                def _cumulative(yearly_pcts):
                    """Compound yearly % returns into cumulative %."""
                    cum = []
                    factor = 1.0
                    for pct in yearly_pcts:
                        if pct is None:
                            cum.append(None)
                        else:
                            factor *= (1 + pct / 100)
                            cum.append(round((factor - 1) * 100, 1))
                    return cum

                cum_port = _cumulative([row["portfolio"] for row in rows_yr])

                fig_yr = go.Figure()
                fig_yr.add_trace(go.Scatter(
                    x=chart_years,
                    y=cum_port,
                    name="Portfolio",
                    mode="lines+markers",
                    line=dict(color=LINE_COLORS[0], width=3),
                    marker=dict(size=7),
                ))
                for idx, bench_name in enumerate(bench_returns):
                    cum_bench = _cumulative([row.get(bench_name, None) for row in rows_yr])
                    fig_yr.add_trace(go.Scatter(
                        x=chart_years,
                        y=cum_bench,
                        name=bench_name,
                        mode="lines+markers",
                        line=dict(color=LINE_COLORS[(idx + 1) % len(LINE_COLORS)], width=2),
                        marker=dict(size=5),
                    ))
                fig_yr.add_hline(y=0, line_dash="dot", line_color=T['chart_zero'], line_width=1)
                fig_yr.update_layout(
                    hovermode="x unified",
                    yaxis_title="Cumulative Return %",
                    yaxis_ticksuffix="%",
                    xaxis=dict(
                        type="category",
                        gridcolor=T['chart_grid'],
                    ),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=12, color=T['chart_font']),
                    ),
                    margin=dict(t=40, b=20, l=40, r=20),
                    height=380,
                    font=dict(
                        family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                        color=T['chart_font'],
                    ),
                    paper_bgcolor=T['chart_paper'],
                    plot_bgcolor=T['chart_plot'],
                    yaxis=dict(gridcolor=T['chart_grid'], zerolinecolor=T['chart_zero']),
                )

                st.markdown(
                    '<div class="performer-block">'
                    '<h4>Cumulative Returns vs Benchmarks</h4>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(fig_yr, width="stretch")
                st.markdown(cards_html, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.info("Not enough history for yearly returns.")
        else:
            st.info("Net liq history unavailable for yearly returns.")

    # ── Returns per year / month ──
    MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    if nl_all and transfers is not None:
        # Reuse cached monthly/yearly returns computed above (same data source)
        monthly_returns = st.session_state.get("_cached_monthly_returns")
        yearly_returns = st.session_state.get("_cached_yearly_returns")
        if monthly_returns is None or yearly_returns is None:
            # Fallback: compute if cache unavailable (e.g. nl_all_early was None)
            df_ret = pd.DataFrame(nl_all)
            df_ret["time"] = _to_time_col(df_ret["time"])
            df_ret["year"] = df_ret["time"].dt.year
            df_ret["month"] = df_ret["time"].dt.month
            df_ret = df_ret.sort_values("time")
            month_last_close = df_ret.groupby(["year", "month"])["close"].last()
            month_periods = list(month_last_close.index)
            monthly_returns = {}
            for i in range(1, len(month_periods)):
                prev_yr, prev_mo = month_periods[i - 1]
                yr, mo = month_periods[i]
                start_val = month_last_close[(prev_yr, prev_mo)]
                end_val = month_last_close[(yr, mo)]
                yr_transfers = transfers.get(yr, {})
                mo_dep = yr_transfers.get("months", {}).get(mo, 0) if isinstance(yr_transfers, dict) else 0
                denom = start_val + 0.5 * mo_dep
                if denom > 0:
                    ret = (end_val - start_val - mo_dep) / denom * 100
                else:
                    ret = 0.0
                monthly_returns.setdefault(yr, {})[mo] = ret
            yearly_returns = {}
            for yr, months in monthly_returns.items():
                factor = 1.0
                for mo in sorted(months):
                    factor *= (1 + months[mo] / 100)
                yearly_returns[yr] = round((factor - 1) * 100, 1)

        # Round monthly returns for display only (work on a copy)
        monthly_returns = {yr: dict(months) for yr, months in monthly_returns.items()}
        for yr in monthly_returns:
            for mo in monthly_returns[yr]:
                monthly_returns[yr][mo] = round(monthly_returns[yr][mo], 1)

        # ── Weekly returns (TWR, deposit-adjusted like monthly) ──
        _df_wk = pd.DataFrame(nl_all)
        _df_wk["time"] = _to_time_col(_df_wk["time"])
        _df_wk = _df_wk.sort_values("time")
        _df_wk["iso_yr"] = _df_wk["time"].dt.isocalendar().year.astype(int)
        _df_wk["iso_wk"] = _df_wk["time"].dt.isocalendar().week.astype(int)
        _wk_last = _df_wk.groupby(["iso_yr", "iso_wk"]).agg(
            close=("close", "last"),
        )
        _wk_periods = list(_wk_last.index)
        weekly_returns = {}  # {(year, month): [(iso_wk, ret, wk_start, wk_end), ...]}
        for i in range(1, len(_wk_periods)):
            prev_iso = _wk_periods[i - 1]
            cur_iso = _wk_periods[i]
            start_val = _wk_last.loc[prev_iso, "close"]
            end_val = _wk_last.loc[cur_iso, "close"]
            # Use actual ISO week boundaries (Monday–Sunday) instead of data points
            from datetime import datetime as _dt_cls, timedelta as _td_cls
            _iso_year, _iso_week = cur_iso
            wk_start = _dt_cls.strptime(f"{_iso_year}-W{_iso_week:02d}-1", "%G-W%V-%u")
            wk_end = wk_start + _td_cls(days=6)  # Sunday
            # Approximate weekly deposits from monthly data
            _wk_yr, _wk_mo = wk_start.year, wk_start.month
            yr_tr = transfers.get(_wk_yr, {})
            mo_dep_total = yr_tr.get("months", {}).get(_wk_mo, 0) if isinstance(yr_tr, dict) else 0
            # Spread monthly deposits evenly across ~4.3 weeks
            import calendar as _cal
            _days_in_mo = _cal.monthrange(_wk_yr, _wk_mo)[1]
            _wk_days = (wk_end - wk_start).days + 1
            wk_dep = mo_dep_total * (_wk_days / _days_in_mo) if _days_in_mo > 0 else 0
            denom = start_val + 0.5 * wk_dep
            if denom > 0:
                ret = (end_val - start_val - wk_dep) / denom * 100
            else:
                ret = 0.0
            key = (_wk_yr, _wk_mo)
            weekly_returns.setdefault(key, []).append((
                cur_iso[1], round(ret, 1), wk_start, wk_end
            ))

        # Compound annual returns — same method as benchmark lines in the chart
        _cum_factor = 1.0
        for yr in sorted(port_returns):
            _cum_factor *= (1 + port_returns[yr] / 100)
        total_return = round((_cum_factor - 1) * 100, 1)
        total_ret_cls = " pf-green" if total_return >= 0 else " pf-red"

        # ── Returns & Deposits side by side ──
        has_deposits = bool(transfers)
        sorted_transfers = sorted(transfers.items(), reverse=True) if has_deposits else []
        total_deposited = sum(v["total"] for v in transfers.values()) if has_deposits else 0
        total_dep_cls = " pf-green" if total_deposited >= 0 else " pf-red"

        col_ret, col_dep = st.columns(2)

        with col_ret:
            # Build month + week options for report picker
            _rpt_opts = []
            _rpt_map = {}  # label → ("month", yr, mo) or ("week", yr, mo, iso_wk, wk_start, wk_end)
            for _yr in sorted(yearly_returns, reverse=True):
                for _mo in range(12, 0, -1):
                    _mr = monthly_returns.get(_yr, {}).get(_mo)
                    if _mr is not None:
                        # Month entry (uppercase to distinguish)
                        _lbl = f"▸ {MONTH_NAMES[_mo]} {_yr}"
                        _rpt_opts.append(_lbl)
                        _rpt_map[_lbl] = ("month", _yr, _mo)
                        # Week entries under this month
                        _wks = weekly_returns.get((_yr, _mo), [])
                        for _iso_wk, _wk_ret, _ws, _we in sorted(_wks, key=lambda x: x[2], reverse=True):
                            _wk_lbl = f"    W{_iso_wk}: {_ws.strftime('%b %d')}–{_we.strftime('%d')}"
                            _rpt_opts.append(_wk_lbl)
                            _rpt_map[_wk_lbl] = ("week", _yr, _mo, _iso_wk, _ws, _we)

            # Returns header with inline report picker
            st.markdown(
                f'<div class="section-title-bar" style="margin-bottom:0">Returns &nbsp;<span style="font-weight:400;font-size:0.85rem;color:{T["text_muted"]}">'
                f'Cumulative: <span class="pf-val{total_ret_cls}" style="font-size:0.85rem">{total_return:+.1f}%</span>'
                f'</span></div>',
                unsafe_allow_html=True,
            )
            with st.container(key="ret_pick_wrap"):
                @st.fragment
                def _report_picker():
                    sel = st.selectbox(
                        "report", _rpt_opts, index=None,
                        placeholder="View report...",
                        label_visibility="collapsed",
                    )
                    if sel:
                        _entry = _rpt_map[sel]
                        if _entry[0] == "month":
                            _show_month_detail(_entry[1], _entry[2], cost_basis, nl_all, transfers, monthly_returns, T)
                        else:
                            _, _yr, _mo, _iso_wk, _ws, _we = _entry
                            _show_week_detail(_yr, _iso_wk, _ws, _we, cost_basis, nl_all, transfers, weekly_returns, T)
                _report_picker()

            # Returns: HTML <details> per year (identical to deposits)
            for yr in sorted(yearly_returns, reverse=True):
                yr_ret = yearly_returns[yr]
                yr_color = T['accent'] if yr_ret >= 0 else T['red']
                mo_html = ""
                for mo in range(1, 13):
                    mo_ret = monthly_returns.get(yr, {}).get(mo)
                    if mo_ret is None:
                        continue
                    mo_color = T['accent'] if mo_ret >= 0 else T['red']
                    mo_html += (
                        f'<div style="border-left:3px solid {mo_color};padding:6px 12px;margin-bottom:2px">'
                        f'<span style="font-weight:600;color:{T["text"]}">{MONTH_NAMES[mo]}</span> &nbsp; '
                        f'<span style="color:{mo_color};font-weight:600">{mo_ret:+.1f}%</span>'
                        f'</div>'
                    )
                st.markdown(
                    f'<details class="yr-details" style="background:{T["card"]};border:1px solid {T["border"]};border-left:3px solid {yr_color};'
                    f'border-radius:8px;padding:10px 14px;margin-bottom:6px">'
                    f'<summary style="font-weight:600;color:{T["text"]}">'
                    f'{yr} — <span style="color:{yr_color}">{yr_ret:+.1f}%</span></summary>'
                    f'<div style="margin-top:8px">{mo_html}</div>'
                    f'</details>',
                    unsafe_allow_html=True)


        with col_dep:
            if has_deposits:
                st.markdown(
                    f'<div class="section-title-bar dep-title-bar">Deposits &nbsp;<span style="font-weight:400;font-size:0.85rem;color:{T["text_muted"]}">'
                    f'Total: <span class="pf-val{total_dep_cls}" style="font-size:0.85rem">${total_deposited:+,.0f}</span>'
                    f'</span></div>',
                    unsafe_allow_html=True,
                )
                for yr, yr_data in sorted_transfers:
                    amount = yr_data["total"]
                    months = yr_data.get("months", {})
                    dep_color = T['accent'] if amount >= 0 else T['red']
                    mo_html = ""
                    for mo in range(1, 13):
                        mo_val = months.get(mo)
                        if mo_val is None:
                            continue
                        mo_color = T['accent'] if mo_val >= 0 else T['red']
                        mo_html += (
                            f'<div style="border-left:3px solid {mo_color};padding:6px 12px;margin-bottom:2px">'
                            f'<span style="font-weight:600;color:{T["text"]}">{MONTH_NAMES[mo]}</span> &nbsp; '
                            f'<span style="color:{mo_color};font-weight:600">${mo_val:+,.0f}</span>'
                            f'</div>'
                        )
                    st.markdown(
                        f'<details class="yr-details" style="background:{T["card"]};border:1px solid {T["border"]};border-left:3px solid {dep_color};'
                        f'border-radius:8px;padding:10px 14px;margin-bottom:6px">'
                        f'<summary style="font-weight:600;color:{T["text"]}">'
                        f'{yr} — <span style="color:{dep_color}">${amount:+,.0f}</span></summary>'
                        f'<div style="margin-top:8px">{mo_html}</div>'
                        f'</details>',
                        unsafe_allow_html=True)

    st.markdown("")

    # ── Per-ticker cards (sorted by Total P/L, best first) ──
    with st.expander(f"All Positions ({len(sorted_tickers)})"):
        def _fmt_result_cell(col, val):
            cls = ""
            if col in ("Options P/L", "Total P/L"):
                cls = " pf-green" if val > 0 else " pf-red" if val < 0 else ""
            if col in ("Options P/L", "Equity Cost", "Total P/L", "Dividends"):
                return f"${val:+,.0f}" if col in ("Options P/L", "Total P/L") else f"${val:,.0f}", cls
            if col == "Mkt Value":
                return f"${val:,.0f}", cls
            if col == "Wheels":
                return f"{val}", cls
            return f"{val}", cls

        result_cols = ["Wheels", "Options P/L", "Equity Cost", "Mkt Value", "Total P/L", "Dividends"]

        rows = []
        for ticker, data in sorted_tickers:
            wheels = data.get("wheels", [])
            completed = sum(1 for w in wheels if w["status"] == "completed")
            active = any(w["status"] == "active" for w in wheels)
            wheel_str = str(completed) + (" +1 active" if active else "")
            rows.append({
                "Logo": _logo_img(data.get("symbol", ticker), data.get("isin")),
                "Ticker": data.get("symbol", ticker),
                "Wheels": wheel_str,
                "Options P/L": data["option_pl"],
                "Equity Cost": data["equity_cost"],
                "Mkt Value": data["market_value"],
                "Total P/L": data["total_pl_real"],
                "Dividends": data["dividends"],
            })

        cards_html = (
            f'<div class="portfolio-cards pf-aligned" style="--pf-grid:'
            f'30px 52px repeat({len(result_cols)}, minmax(0, 1fr))">')
        for row in rows:
            cells = ""
            for col in result_cols:
                fval, cls = _fmt_result_cell(col, row[col])
                cells += (
                    f'<div class="pf-cell">'
                    f'<span class="pf-label">{col}</span>'
                    f'<span class="pf-val{cls}">{fval}</span>'
                    f'</div>'
                )
            cards_html += (
                f'<div class="portfolio-card">'
                f'{row["Logo"]}'
                f'<span class="pf-ticker">{row["Ticker"]}</span>'
                f'{cells}'
                f'</div>'
            )
        cards_html += '</div>'

        st.markdown(cards_html, unsafe_allow_html=True)

    load_profiler.panel()


# ══════════════════════════════════════════════════════
#  CASHFLOW CHAMPIONS — Liontrust two-ratio screen
# ══════════════════════════════════════════════════════

elif page == "Screener":
    st.markdown(
        "<style>.block-container { max-width: 1000px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # One snapshot, computed locally by scripts/run_screener.py — EDGAR is not
    # reliably reachable from Streamlit Cloud, and a 516-name batch does not
    # belong in a page load anyway.
    @st.cache_data(ttl=600, show_spinner=False)
    def _screener_snapshot():
        resp = (_sb_client.table("screener_snapshots")
                .select("computed_at, universe_as_of, summary, rows")
                .order("created_at", desc=True).limit(1).execute())
        return resp.data[0] if resp.data else None

    snap = _screener_snapshot()

    st.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">Screener</p>'
        f'<p class="hero-value" style="font-size:1.6rem">Quality, plainly defined</p>'
        f'<p class="hero-sub">Average ROCE of at least 20% over the last five to '
        f'ten reported years, and no net debt on the latest balance sheet. '
        f'Same ROCE as the watchlist — one definition, one answer.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if not snap:
        st.info("No screener snapshot yet. Run `python3 scripts/run_screener.py` "
                "locally to compute one.")
        st.stop()

    _rows = snap["rows"]
    _sum = snap["summary"]

    # Age up front: half the point of the last page's troubles was a stale
    # snapshot nobody could see was stale.
    _age_days = None
    try:
        from datetime import datetime, UTC as _UTC
        _ts = datetime.fromisoformat(snap["computed_at"].replace("Z", "+00:00"))
        _age_days = (datetime.now(_UTC) - _ts).days
    except (ValueError, KeyError, TypeError):
        pass
    _age_txt = (f"computed {_age_days} day(s) ago" if _age_days is not None
                else "computed at unknown time")
    st.caption(f"Universe of {snap.get('universe_as_of', '?')} · {_age_txt} · "
               f"{_sum['passes']} of {_sum['total']} names pass. "
               f"Refreshes on the 1st of each month.")
    if _age_days is not None and _age_days > 45:
        st.warning(f"This snapshot is {_age_days} days old — a new annual "
                   "report may have landed since. Refresh before acting on it.")

    # Order is the order of the buttons: large caps first, then the mid- and
    # small-cap benches where a 20% return on capital is rarer and less known.
    _INDEX_LABELS = {"sp500": "S&P 500", "nasdaq100": "Nasdaq 100",
                     "dow30": "Dow 30", "sp400": "S&P 400", "sp600": "S&P 600"}
    _screener_card = st.container(key="screener_block")
    with _screener_card:
        _pick = st.segmented_control(
            "Index", ["All", *(_INDEX_LABELS[k] for k in _INDEX_LABELS)],
            default="All", key="screener_index", label_visibility="collapsed",
        ) or "All"
        _pick_key = next((k for k, v in _INDEX_LABELS.items() if v == _pick), None)

        passes = [r for r in _rows if r.get("passes")
                  and (_pick_key is None or _pick_key in (r.get("indices") or []))]
        passes.sort(key=lambda r: -(r.get("avg_roce") or 0))

        # Names already on the watchlist get marked rather than filtered: seeing a
        # familiar name pass is confirmation, and its absence would read as a miss.
        _wl = set()
        try:
            _wl = {e["ticker"].upper() for e in
                   list_watchlist(_sb_client,
                                  user_id=(st.session_state.get("user") or {}).get("id"))}
        except Exception as _e:
            logger.debug("Watchlist unavailable for screener marks: %s", _e)

        if not passes:
            st.info("No names pass in this index.")
        else:
            # Rows of columns rather than an HTML table, for one reason: a
            # button. The add flow is the SAME one the watchlist's own "Add to
            # Watchlist" uses — run_analysis for the EDGAR facts, save_config
            # to store them. A second, different way of adding would be the
            # two-answers problem in write form.
            def _screener_add(t):
                try:
                    with st.spinner(f"Building {t} from EDGAR..."):
                        _cfg, _ = run_analysis(
                            t,
                            peer_mode="Auto-discover",
                            manual_peers="",
                            margin_of_safety=MARGIN_OF_SAFETY_DEFAULT,
                            terminal_growth=TERMINAL_GROWTH_DEFAULT,
                        )
                        save_config(_sb_client, t, _cfg)
                    st.cache_data.clear()
                    st.success(f"{t} added to watchlist — open it there to "
                               "author the DCF assumptions.")
                    st.rerun()
                except Exception as e:
                    logger.error("Screener add failed for %s: %s", t, e)
                    log_error_with_trace("WATCHLIST_ERROR", e, page="Screener",
                                         metadata={"ticker": t})
                    st.error(f"Could not add {t}: {type(e).__name__}")

            prewarm_logos([r["ticker"] for r in passes])
            _t_screener_rows = time.perf_counter()
            _w = [0.42, 1.1, 2.1, 1.5, 0.85, 0.6, 1.0]
            hdr = st.columns(_w, vertical_alignment="center")
            for _c, _label in zip(hdr, ["", "Ticker", "Company", "Sector",
                                        "Avg ROCE", "Years", "Net cash"]):
                if _label:
                    _c.markdown(f'<span style="color:{T["text_muted"]};'
                                f'font-weight:600;font-size:0.8rem">{_label}</span>',
                                unsafe_allow_html=True)

            for r in passes:
                t = r["ticker"]
                cols = st.columns(_w, vertical_alignment="center")
                with cols[0]:
                    # Same button either way, disabled once the name is on the
                    # watchlist: one shape in the column, and the grey state
                    # says "done" where a different icon said "look this up".
                    _on_wl = t.upper() in _wl
                    if st.button("", key=f"scr_add_{t}",
                                 icon=":material/check:" if _on_wl
                                 else ":material/add:",
                                 disabled=_on_wl,
                                 help="Already on your watchlist" if _on_wl
                                 else f"Add {t} to your watchlist"):
                        _screener_add(t)
                _logo = _logo_img(t, None, "",
                                  "width:20px;height:20px;border-radius:50%;"
                                  "object-fit:cover;vertical-align:middle;"
                                  "margin-right:7px")
                cols[1].markdown(f'{_logo}<b>{t}</b>', unsafe_allow_html=True)
                cols[2].markdown(f'<span style="color:{T["text_muted"]};'
                                 f'font-size:0.85rem">{r.get("name") or ""}</span>',
                                 unsafe_allow_html=True)
                cols[3].markdown(f'<span style="color:{T["text_muted"]};'
                                 f'font-size:0.85rem">{r.get("sector") or ""}</span>',
                                 unsafe_allow_html=True)
                cols[4].markdown(f'**{r["avg_roce"]:.0f}%**')
                cols[5].markdown(f'<span style="color:{T["text_muted"]};'
                                 f'font-size:0.85rem">{r.get("years_used", 0)}y</span>',
                                 unsafe_allow_html=True)
                cols[6].markdown(f'${-r.get("net_debt", 0):,.0f}M')
            st.caption(f"{len(passes)} name(s) · a grey check = already on your watchlist · "
                       "+ adds the EDGAR facts; assumptions stay yours to author")

        # What fell out and why — a screen that hides its rejects looks stricter
        # than it is.
        with st.expander("What was excluded"):
            _reason_labels = {
                "roce_below_gate": "Average ROCE under 20%",
                "net_debt": "Net debt on the latest balance sheet",
                "insufficient_history": "Too short a record — under five "
                                        "measurable years",
                "roce_not_measurable": "ROCE does not apply to this filer — "
                                       "banks, insurers and REITs report "
                                       "neither operating income nor current "
                                       "liabilities",
                "debt_tag_suspect": "Debt figure looks like a broken tag — "
                                    "excluded rather than trusted",
                "no_balance_sheet": "No cash figure to judge net debt with",
            }
            for k, n in sorted(_sum.get("reasons", {}).items(), key=lambda x: -x[1]):
                label = _reason_labels.get(k, k)
                if k.startswith("fetch"):
                    label = "EDGAR fetch failed"
                st.markdown(f"- **{n}** × {label}")
            st.caption(
                "Not measurable is mostly financials and real estate, where a "
                "return on capital would not mean what it means elsewhere: the "
                "balance sheet is the business. Too short a record also catches "
                "foreign filers whose IFRS statements don't parse — fewer "
                "usable years, not worse businesses."
            )


# ══════════════════════════════════════════════════════
#  SETTINGS PAGE — Per-user configuration
# ══════════════════════════════════════════════════════

elif page == "Connect your Broker":

    st.markdown(
        "<style>.block-container { max-width: 700px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )
    st.markdown("## Connect your Broker")

    # ── Tastytrade connection ──
    st.markdown("### Tastytrade")

    # Handle OAuth redirect results (read from session state — query params
    # were already captured and cleared in the nav section to avoid the rerun
    # race that st.query_params.clear() triggers in Streamlit ≥1.37).
    _oauth_result = st.session_state.pop("_tt_oauth_result", None)
    if _oauth_result == "success":
        log_page_view(_sb_client, "broker_connect:tastytrade:success")
        st.success("Tastytrade connected successfully!")
        st.session_state.pop("tt_refresh_token", None)  # force reload from DB
        st.session_state.pop("_account_page", None)
        st.rerun()
    elif _oauth_result == "access_denied":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:access_denied")
        st.error("Connection was cancelled. Click 'Connect with Tastytrade' to try again.")
    elif _oauth_result == "connection_failed":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:connection_failed")
        st.error("Could not connect to Tastytrade. Please try again.")
    elif _oauth_result == "session_expired":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:session_expired")
        st.error("Your login session timed out. Please try connecting again.")
    elif _oauth_result == "token_exchange_failed":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:token_exchange_failed")
        st.error("Tastytrade rejected the connection request. Please try again.")
    elif _oauth_result == "storage_failed":
        log_page_view(_sb_client, "broker_connect:tastytrade:error:storage_failed")
        st.error("Connected to Tastytrade but failed to save credentials. Please try again.")

    _existing_token = _get_tt_token()
    if _existing_token:
        st.success("Tastytrade account connected.")
        if st.button("Disconnect Tastytrade", type="primary"):
            delete_credential(_sb_client, "tastytrade_refresh_token")
            st.session_state.pop("tt_refresh_token", None)
            for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                       "net_liq_all", "yearly_transfers", "benchmark_returns",
                       "portfolio_fetched_at"]:
                st.session_state.pop(k, None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.info("Connect your Tastytrade account to view your portfolio, cost basis, and options data. "
                "Click the button below to log in securely via Tastytrade — we only request **read-only** access.")

        # OAuth connect button
        _oauth_url = _secret_or_env("OAUTH_SERVER_URL") or "http://localhost:8000"
        _user = st.session_state.get("user")
        _user_id = _user["id"] if _user and isinstance(_user, dict) else ""
        if _user_id:
            _connect_url = f"{_oauth_url}/auth/tastytrade/login?user_id={_user_id}"
            st.link_button("Connect with Tastytrade", _connect_url, type="primary")
            st.caption("You'll be redirected to Tastytrade to log in. We never see your password.")

        st.caption("Having trouble? Contact us at support@lazytheta.io")

    # ── Interactive Brokers connection ──
    st.markdown("---")
    st.markdown("### Interactive Brokers")
    _ibkr_creds = _get_ibkr_credentials()
    if _ibkr_creds:
        st.success("Interactive Brokers account connected.")
        if st.button("Disconnect Interactive Brokers", type="primary"):
            delete_ibkr_credentials(_sb_client)
            st.session_state.pop("ibkr_credentials", None)
            st.session_state.pop("_ibkr_flex_cache", None)
            if get_active_broker() == "ibkr":
                st.session_state.pop("active_broker", None)
            for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                       "net_liq_all", "yearly_transfers", "benchmark_returns",
                       "portfolio_fetched_at"]:
                st.session_state.pop(k, None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()
    else:
        st.info("Connect your Interactive Brokers account to view your portfolio, cost basis, and options data. "
                "We use **read-only** Flex Query access — this app cannot place trades or modify your account in any way.")
        with st.expander("How to set up your IBKR Flex Query", expanded=True):
            st.markdown(
                "**Step 1 — Create a Flex Query:**\n"
                "1. Log in to [Client Portal](https://www.interactivebrokers.com/portal)\n"
                "2. Go to **Performance & Reports → Flex Queries**\n"
                "3. Click **+ Create** under Activity Flex Queries\n"
                "4. Give it a name (e.g. *Lazy Theta*)\n"
                "5. In **Sections**, click each of these and select all fields:\n"
                "   - **Open Positions**\n"
                "   - **Trades**\n"
                "   - **Cash Transactions**\n"
                "   - **Net Asset Value (NAV) Summary in Base**\n"
                "   - **Change in NAV**\n"
                "   - **Account Information**\n"
                "6. Under **Delivery Configuration**, set the period to **Last 365 Calendar Days**\n"
                "7. Set format to **XML**\n"
                "8. Save the query — note the **Query ID** number\n\n"
                "**Step 2 — Enable the Flex Web Service:**\n"
                "1. Go to **Performance & Reports → Flex Queries**\n"
                "2. Click the **⚙ gear icon** next to Flex Web Service\n"
                "3. Toggle it **on** and copy the **token** shown\n\n"
                "**Step 3 — Paste both values below:**"
            )
        with st.form("ibkr_creds_form"):
            _ibkr_token = st.text_input("Flex Web Service Token", type="password",
                                        placeholder="Your Flex Web Service token")
            _ibkr_query_id = st.text_input("Flex Query ID",
                                           placeholder="e.g. 123456")
            _ibkr_submitted = st.form_submit_button("Save", type="primary")

        if _ibkr_submitted and _ibkr_token and _ibkr_query_id:
            if not _ibkr_query_id.strip().isdigit():
                log_page_view(_sb_client, "broker_connect:ibkr:error:invalid_query_id")
                st.error("Flex Query ID must be numeric (e.g. 123456).")
            else:
                _creds = {
                    "ibkr_flex_token": _ibkr_token.strip(),
                    "ibkr_flex_query_id": _ibkr_query_id.strip(),
                }
                save_ibkr_credentials(_sb_client, _creds)
                st.session_state["ibkr_credentials"] = _creds
                # Clear stale Flex cache so new credentials are used immediately
                st.session_state.pop("_ibkr_flex_cache", None)
                log_page_view(_sb_client, "broker_connect:ibkr:success")
                st.success("Interactive Brokers connected.")
                st.rerun()

    # ── Trading 212 connection ──
    st.markdown("---")
    st.markdown("#### Trading 212 (read-only)")
    _t212_creds_saved = st.session_state.get("t212_credentials")
    if _t212_creds_saved:
        st.success("Trading 212 connected.")
        if st.button("Disconnect Trading 212", type="primary"):
            delete_t212_credentials(_sb_client)
            st.session_state.pop("t212_credentials", None)
            if get_active_broker() == "t212":
                st.session_state.pop("active_broker", None)
            for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                       "net_liq_all", "yearly_transfers", "benchmark_returns",
                       "portfolio_fetched_at"]:
                st.session_state.pop(k, None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            log_page_view(_sb_client, "broker_connect:t212:disconnect")
            st.rerun()
    else:
        st.markdown(
            "1. In the Trading 212 app: **Settings → API (Beta) → Generate API key**\n"
            "2. Choose a **read-only** key. Copy the **key** and **secret** "
            "(the secret is shown only once).\n"
            "3. Paste both below:"
        )
        with st.form("t212_creds_form"):
            _t212_key = st.text_input("API Key", type="password",
                                      placeholder="Your Trading 212 API key")
            _t212_secret = st.text_input("API Secret", type="password",
                                         placeholder="Your Trading 212 API secret")
            _t212_submitted = st.form_submit_button("Save", type="primary")

        if _t212_submitted and _t212_key and _t212_secret:
            _t212_creds = {
                "t212_api_key": _t212_key.strip(),
                "t212_api_secret": _t212_secret.strip(),
            }
            save_t212_credentials(_sb_client, _t212_creds)
            st.session_state["t212_credentials"] = _t212_creds
            log_page_view(_sb_client, "broker_connect:t212:success")
            st.success("Trading 212 connected.")
            st.rerun()

# ══════════════════════════════════════════════════════
#  SECURITY & PRIVACY PAGE
# ══════════════════════════════════════════════════════

elif page == "🔒 Security & Privacy":

    st.markdown(
        f"""<style>
        .block-container {{ max-width: 800px; margin: auto; }}
        /* Force Streamlit columns to stretch to equal height */
        [data-testid="stHorizontalBlock"]:has(.sec-card) {{
            align-items: stretch;
        }}
        [data-testid="stHorizontalBlock"]:has(.sec-card) [data-testid="stColumn"] {{
            height: auto !important;
        }}
        [data-testid="stHorizontalBlock"]:has(.sec-card) [data-testid="stColumn"] div {{
            height: 100%;
        }}
        .sec-card {{
            background: {T['card']};
            border-radius: 18px;
            padding: 28px 24px;
            box-shadow: {T['shadow']};
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: flex-start;
            animation: fadeInUp 0.4s ease-out both;
        }}
        .sec-card h4 {{
            font-family: 'DM Serif Display', Georgia, serif;
            color: {T['text']};
            font-weight: 400;
            font-size: 1.1rem;
            margin: 12px 0 8px 0;
        }}
        .sec-card p {{
            color: {T['text_muted']};
            font-size: 0.88rem;
            line-height: 1.6;
            margin: 0;
            flex: 1;
        }}
        .sec-card a {{
            color: {T['accent']};
            text-decoration: none;
            font-weight: 500;
            font-size: 0.85rem;
        }}
        .sec-card a:hover {{ text-decoration: underline; }}
        .sec-icon {{
            font-size: 1.8rem;
            display: block;
        }}
        .sec-badge {{
            background: {T['card']};
            border: 1px solid {T['border_light']};
            border-radius: 980px;
            padding: 10px 0;
            text-align: center;
            font-size: 0.82rem;
            font-weight: 500;
            color: {T['text']};
        }}
        </style>""",
        unsafe_allow_html=True,
    )
    st.markdown("## 🔒 Security & Privacy")

    # ── Hero section ──
    st.markdown(
        f"""<div style="
            background: {T['card']};
            border-radius: 24px;
            border-top: 3px solid {T['accent']};
            padding: 36px 32px;
            box-shadow: {T['shadow']};
            text-align: center;
            margin-bottom: 24px;
            animation: fadeInUp 0.4s ease-out both;
        ">
            <p style="font-size: 1.6rem; margin: 0 0 8px 0;">🛡️</p>
            <p style="
                color: {T['text']};
                font-size: 1.05rem;
                font-weight: 500;
                margin: 0;
                line-height: 1.5;
            ">We never sell or share your data.</p>
            <p style="
                color: {T['text_muted']};
                font-size: 0.9rem;
                margin: 6px 0 0 0;
            ">Your account is isolated with Row Level Security, and we only store the minimum needed to run the app.</p>
        </div>""",
        unsafe_allow_html=True,
    )

    # ── Three columns ──
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            f"""<div class="sec-card" style="animation-delay: 0.05s;">
                <span class="sec-icon">🗄️</span>
                <h4>Minimal Data Storage</h4>
                <p>We store only what's needed: your watchlist configs, preferences, and
                broker connection tokens. Portfolio data and analysis results are fetched
                live each session — we don't keep copies of your financial data.</p>
            </div>""",
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""<div class="sec-card" style="animation-delay: 0.1s;">
                <span class="sec-icon">👁️‍🗨️</span>
                <h4>No Tracking</h4>
                <p>We run zero analytics, zero cookies, zero third-party tracking scripts.
                No Google Analytics, no Mixpanel, no pixel trackers.
                Your usage is your business.</p>
            </div>""",
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            f"""<div class="sec-card" style="animation-delay: 0.15s;">
                <span class="sec-icon">🔓</span>
                <h4>Open Source</h4>
                <p>Our entire codebase is publicly available on GitHub.
                Every line of code can be inspected, audited, and verified.
                Transparency is our default.</p>
                <a href="{GITHUB_REPO_URL}" target="_blank">View on GitHub →</a>
            </div>""",
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height: 12px"></div>', unsafe_allow_html=True)

    # ── Expander sections ──
    with st.expander("How your data flows"):
        st.markdown(
            "1. You sign in — your account is managed by Supabase with Row Level Security\n"
            "2. Watchlist configs and preferences are stored in Supabase, isolated per user\n"
            "3. Market data is fetched live from public APIs (SEC EDGAR, Yahoo Finance)\n"
            "4. Portfolio data is fetched from Tastytrade using your stored refresh token\n"
            "5. Calculations (DCF, Greeks, P&L) run server-side in your Streamlit session\n"
            "6. Results are displayed — raw portfolio data is not persisted\n"
            "7. When you close the tab, session-level data (fetched prices, calculations) is cleared"
        )

    with st.expander("What about the Tastytrade integration?"):
        st.markdown(
            "The Tastytrade integration uses **OAuth 2.0** — the same standard your bank uses. "
            "This means:\n\n"
            "- You authenticate directly with Tastytrade (we never see your password)\n"
            "- We store a **read-only** refresh token, encrypted in Supabase with per-user isolation\n"
            "- The token only grants read access — this app cannot place trades or modify your account\n"
            "- You can revoke access at any time from your Tastytrade account or disconnect in Connect your Broker\n\n"
            "We will never request write/trade permissions unless you explicitly enable this."
        )

    with st.expander("What we store"):
        st.markdown(
            "Stored **per-user** in Supabase (isolated via Row Level Security):\n\n"
            "- **Watchlist configs** — your saved DCF configurations per ticker\n"
            "- **User preferences** — display settings\n"
            "- **Tastytrade refresh token** — encrypted, read-only, revocable\n\n"
            "**Not** stored:\n\n"
            "- Portfolio positions, balances, or transaction history (fetched live each session)\n"
            "- Market data or stock prices\n"
            "- DCF calculation results\n"
            "- Your Tastytrade password"
        )

    with st.expander("HTTPS & Infrastructure"):
        st.markdown(
            "This app runs on **Streamlit Community Cloud** with enforced HTTPS/TLS encryption. "
            "All data in transit between your browser and the app is encrypted. "
            "The hosting infrastructure is managed by Streamlit (Snowflake) with SOC 2 compliance."
        )

    with st.expander("What we'd need to improve for production"):
        st.markdown(
            "We believe in transparency about what's not yet perfect:\n\n"
            "- **Custom security headers** (CSP, HSTS) — not configurable on Streamlit Cloud\n"
            "- **Rate limiting on API calls** — planned for future release\n"
            "- **Formal security audit** — planned before any paid tier launch"
        )

    st.markdown('<div style="height: 8px"></div>', unsafe_allow_html=True)

    # ── Trust badges ──
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        st.markdown('<div class="sec-badge">🔒 HTTPS Encrypted</div>', unsafe_allow_html=True)
    with b2:
        st.markdown('<div class="sec-badge">🚫 No Data Selling</div>', unsafe_allow_html=True)
    with b3:
        st.markdown('<div class="sec-badge">📖 Open Source</div>', unsafe_allow_html=True)
    with b4:
        st.markdown('<div class="sec-badge">🛡️ Per-User Isolation</div>', unsafe_allow_html=True)

    st.markdown('<div style="height: 4px"></div>', unsafe_allow_html=True)

    # ── Legal links ──
    _lc, _rc = st.columns(2)
    with _lc:
        if st.button("Privacy Policy", width="stretch", type="primary"):
            st.session_state["_account_page"] = "Privacy Policy"
            st.rerun()
    with _rc:
        if st.button("Terms of Service", width="stretch", type="primary"):
            st.session_state["_account_page"] = "Terms of Service"
            st.rerun()

    # ── Footer ──
    st.caption(
        f"Last updated: {date.today().strftime('%B %d, %Y')}. "
        f"Questions about our security practices? "
        f"[Open an issue on GitHub]({GITHUB_REPO_URL}/issues) "
        f"or reach out at {CONTACT_EMAIL}."
    )

elif page == "Privacy Policy":

    st.markdown(
        f"""<style>
        .legal-container {{ max-width: 800px; margin: auto; }}
        .legal-card {{
            background: {T['card']};
            border-radius: 18px;
            border-top: 3px solid {T['accent']};
            padding: 32px 28px;
            box-shadow: {T['shadow']};
            margin-bottom: 16px;
        }}
        .legal-card h4 {{
            font-family: 'DM Serif Display', Georgia, serif;
            color: {T['text']};
            font-weight: 400;
            font-size: 1.15rem;
            margin: 0 0 12px 0;
        }}
        .legal-card p, .legal-card li {{
            color: {T['text_muted']};
            font-size: 0.92rem;
            line-height: 1.6;
        }}
        .legal-card table {{
            width: 100%;
            font-size: 0.88rem;
            border-collapse: collapse;
        }}
        .legal-card th {{
            text-align: left;
            color: {T['text']};
            border-bottom: 1px solid {T['border']};
            padding: 6px 8px;
        }}
        .legal-card td {{
            color: {T['text_muted']};
            border-bottom: 1px solid {T['border']};
            padding: 6px 8px;
        }}
        .legal-card a {{ color: {T['accent']}; text-decoration: none; }}
        .legal-card a:hover {{ text-decoration: underline; }}
        </style>""",
        unsafe_allow_html=True,
    )

    st.markdown(f'<p style="font-family: \'DM Serif Display\', Georgia, serif; font-size: 2rem; color: {T["text"]}; margin-bottom: 4px;">Privacy Policy</p>', unsafe_allow_html=True)
    st.caption(f"Effective date: March 4, 2026 — Last updated: {date.today().strftime('%B %d, %Y')}")

    st.markdown(f"""<div class="legal-card">
<h4>1. Who we are</h4>
<p>Lazy Theta ("we", "us") operates the stock analysis platform at <a href="https://lazytheta.io">lazytheta.io</a>.<br>
Contact: <a href="mailto:info@lazytheta.io">info@lazytheta.io</a></p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>2. What we collect</h4>
<table>
<tr><th>Data</th><th>Purpose</th><th>Stored where</th></tr>
<tr><td>Email address</td><td>Account login</td><td>Supabase Auth</td></tr>
<tr><td>Name, title, date of birth, country</td><td>Account profile</td><td>Supabase Auth metadata</td></tr>
<tr><td>Password</td><td>Authentication (hashed, we never see it)</td><td>Supabase Auth</td></tr>
<tr><td>Watchlist configurations</td><td>Save your DCF valuations</td><td>Supabase database</td></tr>
<tr><td>Display preferences</td><td>Remember your settings</td><td>Supabase database</td></tr>
<tr><td>Tastytrade refresh token</td><td>Read-only portfolio access</td><td>Supabase database (encrypted at rest)</td></tr>
</table>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>3. What we do NOT collect</h4>
<ul>
<li>Portfolio positions, balances, or transaction history (fetched live, never stored)</li>
<li>Market data or stock prices</li>
<li>DCF calculation results</li>
<li>Your Tastytrade password</li>
<li>Analytics, cookies, or tracking data of any kind</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>4. How we protect your data</h4>
<ul>
<li>All data is isolated per user via <strong>Row Level Security</strong> (RLS)</li>
<li>All connections are <strong>HTTPS encrypted</strong></li>
<li>Passwords are hashed by Supabase Auth (bcrypt) &mdash; we never store or see plaintext passwords</li>
<li>Tastytrade tokens are <strong>read-only</strong> and revocable from your Tastytrade account at any time</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>5. Third-party services</h4>
<table>
<tr><th>Service</th><th>Purpose</th><th>Privacy policy</th></tr>
<tr><td>Supabase</td><td>Authentication &amp; database</td><td><a href="https://supabase.com/privacy">supabase.com/privacy</a></td></tr>
<tr><td>Streamlit Cloud</td><td>App hosting</td><td><a href="https://streamlit.io/privacy-policy">streamlit.io/privacy-policy</a></td></tr>
<tr><td>SEC EDGAR</td><td>Financial statements</td><td>Public government data</td></tr>
<tr><td>Tastytrade</td><td>Portfolio data (opt-in)</td><td><a href="https://tastytrade.com/privacy-policy">tastytrade.com/privacy-policy</a></td></tr>
</table>
<p>We do <strong>not</strong> use Google Analytics, Mixpanel, or any tracking service.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>6. Your rights</h4>
<p>You can at any time:</p>
<ul>
<li><strong>View</strong> your data in the app (Connect your Broker page)</li>
<li><strong>Delete</strong> your session data (Clear Session Data button)</li>
<li><strong>Revoke</strong> Tastytrade access from your Tastytrade account</li>
<li><strong>Request deletion</strong> of your account and all data by emailing <a href="mailto:info@lazytheta.io">info@lazytheta.io</a></li>
</ul>
<p>Under GDPR (EU) and similar regulations, you also have the right to data portability and to lodge a complaint with your local data protection authority.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>7. Data retention</h4>
<ul>
<li>Account data is retained as long as your account exists</li>
<li>Session data (portfolio, calculations) is destroyed when you close the browser tab</li>
<li>We do not keep backups of session data</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>8. Changes</h4>
<p>We may update this policy. Material changes will be communicated via the app. Continued use after changes constitutes acceptance.</p>
</div>""", unsafe_allow_html=True)

    if st.button("Back to Security & Privacy", type="primary"):
        st.session_state["_account_page"] = "🔒 Security & Privacy"
        st.rerun()

elif page == "Terms of Service":

    st.markdown(
        f"""<style>
        .legal-container {{ max-width: 800px; margin: auto; }}
        .legal-card {{
            background: {T['card']};
            border-radius: 18px;
            border-top: 3px solid {T['accent']};
            padding: 32px 28px;
            box-shadow: {T['shadow']};
            margin-bottom: 16px;
        }}
        .legal-card h4 {{
            font-family: 'DM Serif Display', Georgia, serif;
            color: {T['text']};
            font-weight: 400;
            font-size: 1.15rem;
            margin: 0 0 12px 0;
        }}
        .legal-card p, .legal-card li {{
            color: {T['text_muted']};
            font-size: 0.92rem;
            line-height: 1.6;
        }}
        .legal-card a {{ color: {T['accent']}; text-decoration: none; }}
        .legal-card a:hover {{ text-decoration: underline; }}
        </style>""",
        unsafe_allow_html=True,
    )

    st.markdown(f'<p style="font-family: \'DM Serif Display\', Georgia, serif; font-size: 2rem; color: {T["text"]}; margin-bottom: 4px;">Terms of Service</p>', unsafe_allow_html=True)
    st.caption(f"Effective date: March 4, 2026 — Last updated: {date.today().strftime('%B %d, %Y')}")

    st.markdown(f"""<div class="legal-card">
<h4>1. Acceptance</h4>
<p>By creating an account or using Lazy Theta ("the Service"), you agree to these terms.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>2. What the Service provides</h4>
<p>Lazy Theta is a stock analysis and portfolio management tool for personal, informational use. It provides:</p>
<ul>
<li>DCF valuation models based on public SEC filings</li>
<li>Portfolio overview via Tastytrade API integration</li>
<li>Wheel strategy cost basis tracking</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>3. Not financial advice</h4>
<p><strong>The Service does not provide financial, investment, tax, or legal advice.</strong> All valuations, calculations, and data are for informational purposes only. You are solely responsible for your investment decisions. We are not a registered investment adviser, broker-dealer, or financial planner.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>4. Your account</h4>
<ul>
<li>You must provide accurate information when creating an account</li>
<li>You are responsible for keeping your credentials secure</li>
<li>One account per person</li>
<li>We may suspend or terminate accounts that violate these terms</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>5. Acceptable use</h4>
<p>You agree not to:</p>
<ul>
<li>Use the Service for any illegal purpose</li>
<li>Attempt to access other users' data</li>
<li>Reverse-engineer, scrape, or overload the Service</li>
<li>Use automated tools to access the Service beyond normal use</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>6. Data accuracy</h4>
<ul>
<li>Financial data is sourced from SEC EDGAR, Yahoo Finance, and Tastytrade</li>
<li>We do not guarantee the accuracy, completeness, or timeliness of any data</li>
<li>DCF valuations are models with assumptions &mdash; they are not predictions of future stock prices</li>
</ul>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>7. Availability</h4>
<p>The Service is provided "as is" on Streamlit Cloud. We do not guarantee uptime or availability. We may modify or discontinue the Service at any time.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>8. Limitation of liability</h4>
<p>To the maximum extent permitted by law, Lazy Theta and its operators shall not be liable for any indirect, incidental, special, or consequential damages, including but not limited to financial losses from investment decisions made using the Service.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>9. Intellectual property</h4>
<p>The source code is available on <a href="{GITHUB_REPO_URL}">GitHub</a>. All rights reserved unless otherwise specified. You may not copy, modify, or redistribute the code without permission.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>10. Changes</h4>
<p>We may update these terms. Continued use after changes constitutes acceptance. Material changes will be communicated via the app.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>11. Governing law</h4>
<p>These terms are governed by the laws of the Netherlands.</p>
</div>""", unsafe_allow_html=True)

    st.markdown(f"""<div class="legal-card">
<h4>12. Contact</h4>
<p>Questions? Email <a href="mailto:info@lazytheta.io">info@lazytheta.io</a>.</p>
</div>""", unsafe_allow_html=True)

    if st.button("Back to Security & Privacy", type="primary"):
        st.session_state["_account_page"] = "🔒 Security & Privacy"
        st.rerun()

