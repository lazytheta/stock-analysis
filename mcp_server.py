"""
LazyTheta DCF MCP Server
========================
Lets Claude Desktop fill out DCF configs in LazyTheta's Supabase.
Runs locally via stdio transport.

Required env vars:
    SUPABASE_URL          — Supabase project URL
    SUPABASE_SERVICE_KEY  — Service role key (bypasses RLS)
    LAZYTHETA_USER_ID     — Your Supabase user ID
"""

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment & Supabase client
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
USER_ID = os.environ.get("LAZYTHETA_USER_ID", "")

_client = None


def get_supabase_client():
    """Create or return cached Supabase client.

    Only requires SUPABASE_URL + SUPABASE_SERVICE_KEY to instantiate.
    For stdio MCP, USER_ID env var supplies the per-call default user;
    for Cloud Run, user_id is passed explicitly per JWT-authenticated
    request, so no module-level USER_ID is needed at client-creation time.
    """
    global _client
    if _client is not None:
        return _client

    if not SUPABASE_URL:
        raise ValueError("SUPABASE_URL environment variable is required")
    if not SUPABASE_SERVICE_KEY:
        raise ValueError("SUPABASE_SERVICE_KEY environment variable is required")

    from supabase import create_client
    _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "LazyTheta DCF",
    instructions="Fill out DCF valuations in LazyTheta's Streamlit app",
)


import gather_data
import dcf_calculator
import config_store
import quotes
import valuation_lenses
from scorecard_utils import compute_roce_metric, capital_employed
import notifications
import aspirant
import moat_cards
import risk_cards
import business_cards

# Structured pre-scan sections: a malformed block would render as a broken
# tab, so it is refused in _save_prescan_section_impl with the reason rather
# than stored.
_CARD_PARSERS = {moat_cards.TITLE: moat_cards.parse_moat_cards,
                  risk_cards.TITLE: risk_cards.parse_risk_cards,
                  business_cards.TITLE: business_cards.parse_business_cards}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_sector_betas(sic_code, sic_description=""):
    """Convert SIC code to sector_betas list of (name, beta, weight) tuples.

    Thin wrapper over gather_data.resolve_sector_betas, which prefers the live
    Damodaran beta over the hardcoded SIC_TO_SECTOR snapshot.
    """
    return gather_data.resolve_sector_betas(sic_code, sic_description)


# ---------------------------------------------------------------------------
# Tool implementations (testable without MCP decorator)
# ---------------------------------------------------------------------------

def _build_dcf_config_impl(ticker, financial_data, company_name,
                            sic_code=None, sic_description="",
                            margin_of_safety=None, terminal_growth=None,
                            sector_margin=None, consensus=None,
                            valuation_basis="nominal",
                            user_id: str | None = None):
    """Core logic for build_dcf_config."""
    # build_dcf_config doesn't touch Supabase directly, but for consistency
    # we accept user_id (unused here; future-proofs the signature).
    user_id = user_id or USER_ID
    ticker = ticker.upper()

    stock_price, _, _ = gather_data.fetch_stock_price(ticker)
    if stock_price <= 0:
        raise ValueError(f"Could not fetch stock price for {ticker}")

    # Baseline, not a live fetch — see gather_data.RISK_FREE_RATE_DEFAULT.
    # Fetching the day's 10Y per ticker is what pulled the watchlist off the
    # harmonised 4.65% again after July.
    risk_free_rate = gather_data.RISK_FREE_RATE_DEFAULT

    nominal_risk_free_rate = None
    if valuation_basis == "real":
        nominal_risk_free_rate = risk_free_rate
        risk_free_rate = gather_data.fetch_tips_yield()
        if risk_free_rate is None:
            logger.warning("TIPS-feed onbereikbaar; TIPS_DEFAULT %.2f%% gebruikt",
                           gather_data.TIPS_DEFAULT * 100)
            risk_free_rate = gather_data.TIPS_DEFAULT

    shares = financial_data.get("shares", [])
    shares_latest = shares[-1] if shares else 0

    # Equity market value is a deliberate input, not a live quantity. Under
    # CAPM it drives both the Hamada relevering (via D/E) and the WACC weights,
    # so re-deriving it from today's price makes the discount rate — and every
    # fair value — drift with the market, and drift the wrong way: price up →
    # D/E down → WACC down → fair value up. Rebuilding a ticker that is already
    # on the watchlist therefore carries the stored figure forward; only a
    # genuinely new ticker starts from the live price.
    market_cap = stock_price * shares_latest
    try:
        existing = config_store.load_config(get_supabase_client(), ticker,
                                            user_id=user_id)
    except Exception:                      # no DB reachable → fall back to live
        existing = None
    if existing and existing.get("equity_market_value"):
        market_cap = existing["equity_market_value"]

    oi_latest = financial_data.get("operating_income", [0])[-1] or 0
    ie_latest = financial_data.get("interest_expense_latest", 0) or 0
    credit_rating, credit_spread = gather_data.synthetic_credit_rating(oi_latest, ie_latest)

    sector_betas = _resolve_sector_betas(sic_code, sic_description)

    # Peer auto-selection removed — peers are authored via the MCP.
    peers = []

    cfg = gather_data.build_config(
        ticker=ticker,
        financials=financial_data,
        stock_price=stock_price,
        market_cap=market_cap,
        shares_yahoo=shares_latest,
        risk_free_rate=risk_free_rate,
        sector_betas=sector_betas,
        credit_spread=credit_spread,
        credit_rating=credit_rating,
        peers=peers,
        company_name=company_name,
        margin_of_safety=margin_of_safety,
        terminal_growth=terminal_growth,
        consensus=consensus,
        valuation_basis=valuation_basis,
        nominal_risk_free_rate=nominal_risk_free_rate,
    )

    return cfg


def _calculate_valuation_impl(cfg, user_id: str | None = None):
    """Core logic for calculate_valuation."""
    user_id = user_id or USER_ID  # unused but signature-consistent
    wacc = dcf_calculator.compute_wacc(cfg)
    valuation = dcf_calculator.compute_intrinsic_value(cfg, wacc)
    reverse = dcf_calculator.compute_reverse_dcf(cfg, wacc)

    result = {
        "wacc": round(wacc, 4),
        "intrinsic_value": round(valuation["intrinsic_value"], 2),
        "buy_price": round(valuation["buy_price"], 2),
        "enterprise_value": round(valuation["enterprise_value"], 2),
        "equity_value": round(valuation["equity_value"], 2),
        "tv_pct": round(valuation["tv_pct"], 4),
        "implied_growth": round(reverse["implied_growth"], 4),
        "implied_margin": round(reverse["implied_margin"], 4),
        "market_price": reverse["market_price"],
    }
    if reverse.get("closest"):
        result["closest_growth"] = round(reverse["closest"][0], 4)
        result["closest_margin"] = round(reverse["closest"][1], 4)

    # Include valuation basis metadata
    result["valuation_basis"] = cfg.get("valuation_basis", "nominal")
    if cfg.get("valuation_basis") == "real":
        result["nominal_risk_free_rate"] = cfg.get("nominal_risk_free_rate")
        result["breakeven_inflation"] = cfg.get("breakeven_inflation")

    return json.dumps(result)


def _calculate_multi_lens_valuation_impl(ticker, scenario_grid=False,
                                          user_id: str | None = None):
    """Core logic for calculate_multi_lens_valuation: load cfg, run all
    lenses, persist summary, return JSON."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})
    if aspirant.is_placeholder(cfg):
        return json.dumps({"error": f"{ticker.upper()} still has the placeholder "
                                    f"DCF (flat curves); fill it in and save first"})

    # Valuation uses only what the config already holds — no yfinance autofill.
    cfg.setdefault("ticker", ticker)

    summary = valuation_lenses.calculate_multi_lens_valuation(
        cfg, scenario_grid=scenario_grid
    )
    cfg["valuation_summary"] = summary
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps(summary, default=str)


def _refresh_all_valuations_impl(force: bool = False,
                                  user_id: str | None = None) -> str:
    """Run multi-lens fair value across all watchlist tickers in parallel.

    Stale = no valuation_summary OR calculated_at older than 7 days OR
    unparseable. Stale tickers get auto-fetched from yfinance + orchestrator
    + saved. Fresh tickers are skipped unless force=True.

    Returns JSON {computed: [...], errors: [...], skipped: [...]}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import UTC, datetime, timedelta

    user_id = user_id or USER_ID
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=user_id)
    tickers = [e["ticker"] for e in entries]

    threshold = datetime.now(UTC) - timedelta(days=7)

    def _is_stale(cfg: dict) -> bool:
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

    # Load configs in parallel and decide stale set
    def _load(t):
        return config_store.load_config(client, t, user_id=user_id)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(zip(tickers, pool.map(_load, tickers)))
    loaded, dropped = _load_watchlist_configs(tickers, results.get)

    loaded = {t: c for t, c in loaded.items() if not aspirant.is_placeholder(c)}
    targets = list(loaded.keys()) if force else [t for t, c in loaded.items() if _is_stale(c)]
    skipped = [t for t in loaded if t not in targets]

    computed: list[str] = []
    errors: list[str] = [f"{t}: config niet laadbaar" for t in dropped]

    def _refresh_one(ticker: str) -> str:
        cfg = dict(loaded[ticker])
        cfg.setdefault("ticker", ticker)
        summary = valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid=False)
        cfg["valuation_summary"] = summary
        config_store.save_config(client, ticker, cfg, user_id=user_id)
        return ticker

    if targets:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_refresh_one, t): t for t in targets}
            for future in as_completed(futures):
                t = futures[future]
                try:
                    future.result()
                    computed.append(t)
                except Exception as e:
                    logger.warning("Refresh failed for %s: %s", t, e)
                    errors.append(f"{t}: {e}")

    return json.dumps({"computed": computed, "errors": errors, "skipped": skipped})


def _save_to_watchlist_impl(ticker, cfg, user_id: str | None = None):
    """Core logic for save_to_watchlist."""
    # equity_market_value is a deliberate input (see
    # dcf_calculator._equity_market_value). Without it the CAPM discount rate
    # falls back to stock_price × shares_outstanding and silently drifts with
    # the market — and drifts the wrong way, making a stock look cheaper the
    # more expensive it gets. Refuse rather than store a config that floats.
    if not (cfg or {}).get("equity_market_value") \
            or float(cfg["equity_market_value"]) <= 0:
        return (
            f"{ticker.upper()} not saved: config needs a positive "
            f"'equity_market_value' ($M). It anchors the CAPM discount rate "
            f"(D/E relevering + WACC weights); without it the rate follows the "
            f"day's price. Set it from the market cap you want to value at."
        )

    # sector_betas entries are (name, unlevered_beta, revenue_weight) and
    # beta_u is sum(beta × weight), so the weights have to sum to 1.0. Putting
    # the beta in the weight field squares it and quietly moves the discount
    # rate — DECK carried ["Shoe", 1.14, 1.14], giving beta_u 1.2996 and a WACC
    # of 10.39% where 1.14 alone gives 9.68%. Authored wrong five times now.
    betas = (cfg or {}).get("sector_betas")
    if betas:
        try:
            total = sum(float(entry[2]) for entry in betas)
        except (TypeError, IndexError, ValueError):
            return (f"{ticker.upper()} not saved: each 'sector_betas' entry must "
                    f"be [name, unlevered_beta, revenue_weight].")
        if abs(total - 1.0) > 0.001:
            return (
                f"{ticker.upper()} not saved: 'sector_betas' revenue weights sum "
                f"to {total:g}, not 1.0. Each entry is [name, unlevered_beta, "
                f"revenue_weight] and beta_u = sum(beta x weight) — a single "
                f"sector takes weight 1.0. A weight equal to the beta is the "
                f"usual cause: it squares the beta and shifts the WACC."
            )

    user_id = user_id or USER_ID
    client = get_supabase_client()
    stored = config_store.load_config(client, ticker, user_id=user_id) or {}

    # An Aspirant may only leave that category via promote_aspirant (Wide moat,
    # filled-in DCF) or set_category(ticker, "No") to reject it. A category
    # slipped into a save_to_watchlist payload would bypass that gate.
    if stored.get("category") == "Aspirant" \
            and "category" in cfg and cfg["category"] != "Aspirant":
        return (
            f"{ticker.upper()} not saved: it is an Aspirant and can only leave "
            f"that category via promote_aspirant (Wide moat, DCF filled in) or "
            f"set_category(ticker, \"No\") to reject it."
        )

    # A filled-in DCF lifts the aspirant's placeholder marker. Set to False
    # rather than popped: save_config merges, so an absent key would survive.
    # Flatness is judged on the merged curves — the payload's own
    # revenue_growth/op_margins where it sets them, else what's already
    # stored — so a save that fills in the curves but omits dcf_placeholder
    # doesn't leave a stale True behind.
    if stored.get("dcf_placeholder") or cfg.get("dcf_placeholder"):
        merged_curves = {
            key: cfg[key] if key in cfg else stored.get(key)
            for key in ("revenue_growth", "op_margins")
        }
        if not aspirant.curves_are_flat(merged_curves):
            cfg = {**cfg, "dcf_placeholder": False}

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return f"Saved {ticker.upper()} to watchlist."


def _get_config_impl(ticker, user_id: str | None = None):
    """Core logic for get_config."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})
    return json.dumps(cfg, default=str)


# Koersen kort onthouden over aanroepen heen. Zonder dit doet elke
# get_watchlist een ronde langs alle ~90 namen, en dat is exact het verkeer
# waarmee Streamlit Cloud bij Yahoo op de zwarte lijst kwam. Die blokkade is de
# reden dat er een tweede bron moest komen; hem vanaf Cloud Run herhalen zou
# ook het laatste werkende pad dichtgooien.
_QUOTE_CACHE = quotes.TtlCache(ttl_seconds=60)


def _live_quote(ticker: str):
    """Eén koers via fetch_stock_price (Nasdaq, dan Yahoo), in het koerscontract."""
    price, _, _ = gather_data.fetch_stock_price(ticker)
    return {"price": price} if price and price > 0 else None


def _merge_trailing_multiples(peer: dict, computed: dict) -> dict:
    """Peer-record bijwerken met vers berekende trailing multiples.

    Een multiple die nu niet berekenbaar is (None) wordt uit het record
    gehaald, niet met rust gelaten: anders bleef het oude getal in cfg.peers
    staan en telde het gewoon mee in de peer-mediaan, terwijl de
    toolbeschrijving belooft dat zo'n peer uit de trailing anchors valt en
    de respons `null` meldt. Respons en opgeslagen staat zeggen nu hetzelfde.
    """
    out = dict(peer)
    for key in ("trailing_pe", "ev_ebit"):
        if computed.get(key) is not None:
            out[key] = computed[key]
        else:
            out.pop(key, None)
    out["_trailing_source"] = "EDGAR compute_trailing_multiples"
    return out


def _load_watchlist_configs(tickers, load):
    """(loaded, dropped): configs per ticker, plus de tickers zonder config.

    Die laatste vielen tot 2026-09-13 stil uit `loaded` en kwamen daarna in
    géén van computed/errors/skipped terecht -- refresh_all_valuations gaf
    een compleet ogend antwoord terwijl een naam niet herrekend was.
    """
    loaded, dropped = {}, []
    for t in tickers:
        c = load(t)
        if c is None:
            dropped.append(t)
        else:
            loaded[t] = c
    return loaded, dropped


def _get_watchlist_impl(user_id: str | None = None, live_prices: bool = True):
    """Core logic for get_watchlist.

    De opgeslagen stock_price is de koers van het moment dat de config voor het
    laatst geschreven werd, niet die van nu -- op 2026-09-09 stond Hermes hier
    op 1613,50 terwijl de markt 1412 deed, en niets in het antwoord zei dat.
    Daarom wordt er een live koers overheen gelegd, met de herkomst en de
    ouderdom erbij zodat een oude koers zich niet als verse kan voordoen.

    Yahoo levert de VS-namen; Deutsche Boerse vult de Europese lijnen, die
    Yahoo niet geeft. Lukt geen van beide, dan blijft de opgeslagen koers staan
    met price_stale: True.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=user_id)
    if live_prices:
        quotes.apply_live_prices(
            entries,
            primary=lambda tickers: quotes.parallel_quotes(
                tickers, _live_quote, cache=_QUOTE_CACHE),
            frankfurt=quotes.fetch_frankfurt_quotes)
    return json.dumps(entries, default=str)


def _update_valuation_inputs_impl(ticker: str, fields: dict,
                                   user_id: str | None = None) -> str:
    """Core logic for update_valuation_inputs. Merges fields into
    cfg["valuation_inputs"] and removes them from _auto_filled so the
    user override survives the next refresh."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    for k, v in fields.items():
        inputs[k] = v
        if k in auto_filled:
            auto_filled.remove(k)
    inputs["_auto_filled"] = auto_filled

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps(inputs, default=str)


def _update_dcf_scenario_adjustments_impl(ticker: str, fields: dict,
                                            user_id: str | None = None) -> str:
    """Core logic for update_dcf_scenario_adjustments. Updates the bear/bull
    growth and margin adjustments which drive the DCF lens's fv_low/fv_high
    range when scenario_grid=True.

    Valid keys: bear_growth_adj, bear_margin_adj, bull_growth_adj, bull_margin_adj.
    All values must be floats. Typical magnitudes are small (±0.01 to ±0.05).
    Bear keys are usually negative, bull keys positive.

    These adjustments are added to revenue_growth and op_margins to build a
    4x4 scenario grid. The DCF lens takes min/max of all scenarios as
    fv_low/fv_high. Only effective when calling calculate_multi_lens_valuation
    with scenario_grid=True.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    valid_keys = {"bear_growth_adj", "bear_margin_adj", "bull_growth_adj", "bull_margin_adj"}
    unknown = sorted(k for k in fields if k not in valid_keys)
    if unknown:
        return json.dumps({
            "error": f"unknown adjustment key(s): {unknown}. "
                     f"Valid: {sorted(valid_keys)}",
        })

    for k, v in fields.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return json.dumps({
                "error": f"adjustment {k} must be a number, "
                         f"got {type(v).__name__}={v!r}",
            })

    for k, v in fields.items():
        cfg[k] = float(v)

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({
        "bear_growth_adj": cfg.get("bear_growth_adj"),
        "bear_margin_adj": cfg.get("bear_margin_adj"),
        "bull_growth_adj": cfg.get("bull_growth_adj"),
        "bull_margin_adj": cfg.get("bull_margin_adj"),
    }, default=str)


def _update_lens_weights_impl(ticker: str, weights: dict,
                              user_id: str | None = None) -> str:
    """Core logic for update_lens_weights. Merges weights into
    cfg["lens_weights"]; unspecified keys retain their current value
    (or fall back to DEFAULT_LENS_WEIGHTS via the orchestrator).

    Empty dict resets to defaults (cfg["lens_weights"] = {} → the
    orchestrator's `weights_cfg = cfg.get("lens_weights") or DEFAULT_LENS_WEIGHTS`
    falls back to defaults). The config_store guard treats empty dict as
    intentional user action (RESTORE_MISSING_ONLY for lens_weights, per
    2026-05-07 fix).

    The orchestrator renormalizes active lens weights to sum to 1.0 at
    compute time, so partial overrides like {"dcf": 0.6} are fine —
    unspecified keys retain DEFAULT_LENS_WEIGHTS, then everything gets
    renormalized.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    valid_keys = set(valuation_lenses.DEFAULT_LENS_WEIGHTS.keys())
    unknown = sorted(k for k in weights if k not in valid_keys)
    if unknown:
        return json.dumps({
            "error": f"unknown lens key(s): {unknown}. "
                     f"Valid: {sorted(valid_keys)}",
        })

    for k, v in weights.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            return json.dumps({
                "error": f"weight for {k} must be a non-negative number, "
                         f"got {type(v).__name__}={v!r}",
            })

    if not weights:
        cfg["lens_weights"] = {}
    else:
        existing = cfg.get("lens_weights") or {}
        cfg["lens_weights"] = {**existing, **weights}

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps(cfg["lens_weights"], default=str)


# ---------------------------------------------------------------------------
# Fundamentals (read + override) — surfaces EDGAR data and per-year manual
# overrides so Claude can analyse raw historicals or correct bad XBRL tags.
# ---------------------------------------------------------------------------


# Guards on incremental ROIC (ΔNOPAT / ΔCapitalEmployed). Both exist because
# the metric is a ratio of two differences, which behaves badly when the
# denominator is small or negative.
INCR_ROIC_MIN_CAPITAL_CHANGE = 0.05  # ΔCE must be ≥5% of the base year's CE
INCR_ROIC_CEILING = 100.0            # clamp, both directions


def _phase_gate_metrics(fund):
    """Extra metrics for the phase-aware ROCE gate (robustness engine, see
    specs/2026-06-16-phase-aware-roce-gate-design): Rule of 40 (3y revenue CAGR
    + FCF margin), incremental ROIC (3-delta, on the same capital-employed
    basis as the headline ROCE, so the two cannot disagree about what capital
    is; None when capital shrank or barely moved), and latest-year
    ROCE + rising trend on the same excess-liquidity-adjusted basis as the
    headline ROCE (scorecard_utils.roce_for_year — shared with
    compute_roce_metric so mean and latest/trend cannot diverge).
    Every key defaults to None when not computable."""
    out = {
        "revenue_cagr_3y_pct": None, "fcf_margin_pct": None, "rule_of_40_pct": None,
        "incremental_roic_pct": None, "roce_latest_pct": None, "roce_rising": None,
    }
    rev = fund.get("revenue") or []
    fcf = fund.get("fcf") or []
    oi = fund.get("operating_income") or []
    tax = fund.get("tax_provision") or []
    pretax = fund.get("pretax_income") or []
    ta = fund.get("total_assets") or []
    cl = fund.get("current_liabilities") or []
    n = len(fund.get("years") or [])

    # Revenue 3y CAGR over the last 4 usable revenue points (steadier than YoY)
    rev_pts = [(i, v) for i, v in enumerate(rev) if v is not None and v > 0]
    if len(rev_pts) >= 2:
        last_i, last_v = rev_pts[-1]
        base_i, base_v = rev_pts[max(0, len(rev_pts) - 4)]
        years = last_i - base_i
        if years > 0 and base_v > 0:
            out["revenue_cagr_3y_pct"] = ((last_v / base_v) ** (1 / years) - 1) * 100

    # FCF margin — latest year where both revenue and FCF exist
    for i in range(n - 1, -1, -1):
        rv = rev[i] if i < len(rev) else None
        fv = fcf[i] if i < len(fcf) else None
        if rv and fv is not None and rv > 0:
            out["fcf_margin_pct"] = fv / rv * 100
            break

    if out["revenue_cagr_3y_pct"] is not None and out["fcf_margin_pct"] is not None:
        out["rule_of_40_pct"] = out["revenue_cagr_3y_pct"] + out["fcf_margin_pct"]

    # Incremental ROIC = ΔNOPAT / ΔCapitalEmployed over the last 3 deltas.
    #
    # Capital employed is the shared definition (scorecard_utils), not the
    # debt + equity − cash proxy this used to carry. Book equity falls with
    # every buyback, so the proxy read BKNG's capital as 3,046 → −4,045 across
    # FY2022-FY2025 while EBIT grew 73%, then reported "capital shrank" for a
    # business whose capital had not shrunk. Same disease as the ROCE basis:
    # one concept, several calculations, drifting apart. There is now one.
    nopat, invcap = {}, {}
    for i in range(n):
        oi_v = oi[i] if i < len(oi) else None
        if oi_v is None:
            continue
        tr = 0.21  # fallback effective tax rate
        px = pretax[i] if i < len(pretax) else None
        tx = tax[i] if i < len(tax) else None
        if px and tx is not None and px != 0:
            _tr = tx / px
            if 0 <= _tr <= 0.35:
                tr = _tr
        nopat[i] = oi_v * (1 - tr)
        ta_v = ta[i] if i < len(ta) else None
        cl_v = cl[i] if i < len(cl) else None
        if ta_v is not None and cl_v is not None:
            invcap[i] = capital_employed(fund, i)
    common = sorted(set(nopat) & set(invcap))
    if len(common) >= 4:
        pts = common[-4:]
        d_nopat = nopat[pts[-1]] - nopat[pts[0]]
        d_inv = invcap[pts[-1]] - invcap[pts[0]]
        base = abs(invcap[pts[0]])
        # Two guards on a ratio of differences. Shrinking capital stays None:
        # the ratio is not wrong there, it is meaningless — a company earning
        # more on less capital is the best case, and a negative percentage
        # reads as the worst. And a capital base that barely moved divides by
        # almost nothing: CF moved 37 on a base of 8,016 and the metric read
        # −6,614%, a number about rounding, not about returns.
        if (d_inv > 0 and base > 0
                and d_inv >= INCR_ROIC_MIN_CAPITAL_CHANGE * base):
            out["incremental_roic_pct"] = max(
                -INCR_ROIC_CEILING, min(INCR_ROIC_CEILING, d_nopat / d_inv * 100))

    # Latest-year ROCE + rising trend, excess-liquidity-adjusted (shared helper
    # with compute_roce_metric so mean and latest/trend cannot diverge).
    from scorecard_utils import roce_for_year
    roce = {}
    any_capped = False
    for i in range(n):
        pct, capped = roce_for_year(fund, i)
        if pct is not None:
            roce[i] = pct
            any_capped = any_capped or capped
    rk = sorted(roce)
    if rk:
        out["roce_latest_pct"] = roce[rk[-1]]
        if len(rk) >= 2:
            out["roce_rising"] = roce[rk[-1]] > roce[rk[max(0, len(rk) - 4)]]
    out["roce_capped"] = any_capped
    return out


def _compute_fundamentals_headline(fund, cfg):
    """Compute the same headline metrics the watchlist + detail page show:
    avg ROCE (with ROE fallback for float businesses), current FCF Yield,
    current EBIT/EV, latest adjusted Net Debt + Net Debt/EBITDA.
    """
    yrs = list(fund.get("years") or [])
    n = len(yrs)
    headline = {
        "latest_year": yrs[-1] if yrs else None,
        "avg_roce_pct": None,
        "roce_metric": "ROCE",
        "current_fcf_yield_pct": None,
        "current_ebit_ev_pct": None,
        "latest_adjusted_net_debt_m": None,
        "latest_net_debt_ebitda": None,
    }
    if not n:
        return headline

    # avg_roce_pct — exactly what it computes, because the robustness
    # deal-breaker gate reads it and a quiet change moves verdicts across the
    # whole watchlist:
    #
    #   • an ARITHMETIC MEAN of the per-year percentages, giving every year the
    #     same weight, NOT a pooled sum(EBIT)/sum(CE) — which would weight the
    #     years by the size of their capital base.
    #   • over the trailing scorecard_utils.ROCE_WINDOW_YEARS years, NOT over
    #     however many years this call fetched. Before the window was pinned,
    #     get_fundamentals(n_years=11) answered 37.47% for BKE where n_years=10
    #     answered 36.27% — the same ticker, two headlines.
    #   • per year, EBIT / ((TA − CL) − cash − short-term investments),
    #     capped at ROCE_CEILING. Goodwill stays in the denominator; idle cash
    #     does not, because EBIT sits above the interest that cash earns.
    #   • ROE (Net Income / Total Equity) instead, on the same window, when the
    #     name is a float business or cfg['roce_metric_override'] says so.
    #
    # Single source of truth shared with the Streamlit watchlist and detail
    # page (scorecard_utils.compute_roce_metric).
    cash_w = fund.get("cash") or []  # used below for EV / net-debt
    _metric, _metric_val = compute_roce_metric(fund, cfg)
    headline["roce_metric"] = _metric
    headline["avg_roce_pct"] = round(_metric_val, 2) if _metric_val is not None else None

    # Current FCF Yield + EBIT/EV
    mcap_m = (cfg.get("equity_market_value") or 0) if isinstance(cfg, dict) else 0
    fcf_list = [v for v in (fund.get("fcf") or []) if v is not None]
    if fcf_list and mcap_m > 0:
        headline["current_fcf_yield_pct"] = round(fcf_list[-1] / mcap_m * 100, 2)
    oi_latest = next((v for v in reversed(fund.get("operating_income") or []) if v is not None), None)
    debt_latest = next((v for v in reversed(fund.get("total_debt") or []) if v is not None), None)
    cash_latest = next((v for v in reversed(cash_w) if v is not None), 0) or 0
    if oi_latest is not None and debt_latest is not None and mcap_m > 0:
        ev = mcap_m + debt_latest - cash_latest
        if ev > 0:
            headline["current_ebit_ev_pct"] = round(oi_latest / ev * 100, 2)

    # Latest adjusted Net Debt + Net Debt/EBITDA
    st_d = next((v for v in reversed(fund.get("short_term_debt") or []) if v is not None), 0) or 0
    op_l = next((v for v in reversed(fund.get("operating_lease_liabilities") or []) if v is not None), 0) or 0
    fn_l = next((v for v in reversed(fund.get("finance_lease_liabilities") or []) if v is not None), 0) or 0
    pen = next((v for v in reversed(fund.get("pension_liabilities") or []) if v is not None), 0) or 0
    if debt_latest is not None:
        adj_debt = debt_latest + st_d + op_l + fn_l + pen
        nd = adj_debt - cash_latest
        headline["latest_adjusted_net_debt_m"] = round(nd, 0)
        da_latest = next((v for v in reversed(fund.get("da") or []) if v is not None), 0) or 0
        if oi_latest is not None:
            ebitda = oi_latest + da_latest
            if ebitda > 0:
                headline["latest_net_debt_ebitda"] = round(nd / ebitda, 2)

    # Phase-aware ROCE-gate inputs (robustness engine reads these)
    headline.update(_phase_gate_metrics(fund))
    return headline


def _get_fundamentals_impl(ticker: str, n_years: int = 10,
                            user_id: str | None = None) -> str:
    """Return the per-year fundamentals arrays (with any stored overrides
    applied) plus computed headline metrics. Read-only — does not modify
    the cfg."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    try:
        fund_raw = gather_data.fetch_fundamentals(ticker, n_years=n_years)
    except Exception as e:
        return json.dumps({"error": f"fetch_fundamentals failed: {e}"})

    overrides = cfg.get("fundamentals_overrides") or {}
    fund = gather_data.apply_fundamentals_overrides(fund_raw, overrides)
    headline = _compute_fundamentals_headline(fund, cfg)

    return json.dumps({
        "ticker": ticker.upper(),
        "years": fund.get("years") or [],
        "raw": {k: v for k, v in fund.items() if k != "years"},
        "headline": headline,
        "overrides_applied": overrides,
    }, default=str)


def _update_fundamentals_impl(ticker: str, overrides: dict,
                               user_id: str | None = None) -> str:
    """Merge per-field per-year overrides into cfg.fundamentals_overrides.

    Semantics:
    - Input `overrides` is shaped {field_name: {year: value}}
    - For each (field, year) pair: numeric value sets/replaces the
      override; null value removes that specific (field, year) override
      (reverting to the EDGAR-fetched value)
    - Fields not in OVERRIDABLE_FUNDAMENTALS_FIELDS are rejected
    - Other (field, year) overrides stay untouched (partial merge)
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    if not isinstance(overrides, dict) or not overrides:
        return json.dumps({"error": "overrides must be a non-empty dict"})

    allowed = set(gather_data.OVERRIDABLE_FUNDAMENTALS_FIELDS)
    unknown = sorted(k for k in overrides if k not in allowed)
    if unknown:
        return json.dumps({
            "error": f"unknown field(s): {unknown}. "
                     f"Allowed: {sorted(allowed)}",
        })

    existing = dict(cfg.get("fundamentals_overrides") or {})
    for field, year_map in overrides.items():
        if not isinstance(year_map, dict):
            return json.dumps({
                "error": f"field '{field}': value must be {{year: number_or_null}}",
            })
        field_existing = dict(existing.get(field) or {})
        for yr, val in year_map.items():
            try:
                yr_str = str(int(yr))
            except (TypeError, ValueError):
                return json.dumps({
                    "error": f"field '{field}': year '{yr}' must be an integer",
                })
            if val is None:
                field_existing.pop(yr_str, None)
            else:
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    return json.dumps({
                        "error": f"field '{field}' year {yr_str}: value must be a number or null",
                    })
                field_existing[yr_str] = float(val)
        if field_existing:
            existing[field] = field_existing
        else:
            existing.pop(field, None)

    cfg["fundamentals_overrides"] = existing
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({
        "ticker": ticker.upper(),
        "fundamentals_overrides": existing,
        "field_count": len(existing),
        "total_override_cells": sum(len(v) for v in existing.values()),
    }, default=str)


def _refresh_peer_multiples_impl(ticker: str, user_id: str | None = None) -> str:
    """Recompute trailing P/E + EV/EBIT for a ticker's peers and the ticker's
    own ttm_eps/ttm_ebit from EDGAR fundamentals + current price (via
    gather_data.compute_trailing_multiples) — no external multiples provider,
    no rate limit, self-refreshing. Populates cfg.peers[].trailing_pe/ev_ebit
    and cfg.valuation_inputs.ttm_eps/ttm_ebit, then saves. Peers that cannot be
    computed (foreign filer, negative earnings, missing tag) keep their existing
    fields and are naturally excluded from the trailing anchors.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})

    own = gather_data.compute_trailing_multiples(ticker)
    vi = dict(cfg.get("valuation_inputs") or {})
    if own.get("ttm_eps") is not None:
        vi["ttm_eps"] = own["ttm_eps"]
    if own.get("ttm_ebit") is not None:
        vi["ttm_ebit"] = own["ttm_ebit"]
    cfg["valuation_inputs"] = vi

    computed: dict = {}
    updated_peers = []
    for p in cfg.get("peers") or []:
        pt = p.get("ticker")
        if not pt:
            updated_peers.append(p)
            continue
        m = gather_data.compute_trailing_multiples(pt)
        np = _merge_trailing_multiples(p, m)
        updated_peers.append(np)
        computed[pt] = {"trailing_pe": m.get("trailing_pe"), "ev_ebit": m.get("ev_ebit")}
    cfg["peers"] = updated_peers

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({
        "ticker": ticker.upper(),
        "own_ttm_eps": vi.get("ttm_eps"),
        "own_ttm_ebit": vi.get("ttm_ebit"),
        "peers": computed,
    }, default=str)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def refresh_peer_multiples(ticker: str) -> str:
    """Recompute trailing P/E and EV/EBIT for a watchlist ticker's peer set —
    and the ticker's own ttm_eps/ttm_ebit — from EDGAR filings + current price,
    then save them into the config. No external multiples provider and no rate
    limit; safe to re-run any time to refresh. Peers that are foreign filers or
    lack computable earnings are left out of the trailing anchors.

    Args:
        ticker: Watchlist ticker whose peer multiples to refresh (e.g. "MSFT").

    Returns:
        JSON with the ticker's own ttm_eps/ttm_ebit and a per-peer map of the
        computed trailing_pe / ev_ebit (null where not computable).
    """
    try:
        return _refresh_peer_multiples_impl(ticker)
    except Exception as e:
        return json.dumps({"error": str(e)})

@mcp.tool()
def build_dcf_config(
    ticker: str,
    financial_data: dict,
    company_name: str,
    sic_code: str = "",
    sic_description: str = "",
    margin_of_safety: float = 0,
    terminal_growth: float = 0,
    sector_margin: float = 0,
    consensus: dict | None = None,
    valuation_basis: str = "nominal",
) -> str:
    """Build a complete DCF configuration from SEC financial data.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")
        financial_data: Parsed financials dict with keys: years, revenue,
            operating_income, net_income, cost_of_revenue, shares,
            current_assets, cash, st_investments, current_liabilities,
            st_debt, st_leases, net_ppe, goodwill_intang,
            tax_provision, pretax_income, lt_debt_latest, lt_leases_latest,
            st_debt_latest, interest_expense_latest, finance_leases_latest,
            minority_interest_latest, equity_investments_latest,
            unfunded_pension_latest
        company_name: Full company name (e.g. "Microsoft Corporation")
        sic_code: SIC code for sector beta + peer lookup (e.g. "7372")
        sic_description: SIC description for fuzzy sector matching
        margin_of_safety: Override default 20%% margin of safety (0 = use default)
        terminal_growth: Override default 2.5%% terminal growth (0 = use default)
        sector_margin: Override sector operating margin (0 = auto from Damodaran)
        consensus: Analyst estimates dict (optional)
        valuation_basis: "nominal" (default) or "real" (TIPS-based, inflation-adjusted)

    Returns:
        JSON string with the complete DCF config dict.
    """
    try:
        cfg = _build_dcf_config_impl(
            ticker=ticker,
            financial_data=financial_data,
            company_name=company_name,
            sic_code=sic_code or None,
            sic_description=sic_description,
            margin_of_safety=margin_of_safety or None,
            terminal_growth=terminal_growth or None,
            sector_margin=sector_margin or None,
            consensus=consensus,
            valuation_basis=valuation_basis,
        )
        return json.dumps(cfg, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def calculate_valuation(config: dict) -> str:
    """Calculate intrinsic value, WACC, and reverse DCF from a config.

    Args:
        config: Complete DCF config dict (from build_dcf_config or get_config).

    Returns:
        JSON with wacc, intrinsic_value, buy_price, enterprise_value,
        equity_value, tv_pct, implied_growth, implied_margin.
    """
    try:
        return _calculate_valuation_impl(config)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def calculate_multi_lens_valuation(ticker: str, scenario_grid: bool = False) -> str:
    """Run multi-lens fair value (DCF + Trading Multiples + Reverse DCF)
    for a watchlist ticker and persist the summary to Supabase.

    Use this after editing valuation_inputs or peers to refresh the
    fair value estimate. The result is also surfaced via get_watchlist().

    Args:
        ticker: Stock ticker symbol (e.g. "ABT")
        scenario_grid: If True, run a 4x4 bull/bear DCF scenario grid for
            the DCF lens fv_low/fv_high. Default False uses ±15% bands
            around the base intrinsic.

    Returns:
        JSON valuation_summary dict. See spec for schema.
    """
    try:
        return _calculate_multi_lens_valuation_impl(ticker, scenario_grid)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def refresh_all_valuations(force: bool = False) -> str:
    """Refresh multi-lens fair value for the entire watchlist in one call.

    Stale = no valuation_summary OR calculated_at older than 7 days OR
    unparseable. Stale tickers get auto-fetched from yfinance + orchestrator
    + saved. Fresh tickers are skipped unless force=True.

    Use this after editing peers/inputs across multiple tickers, or after
    a long period without refresh, to bring the watchlist's fair-value
    range back in sync with current yfinance data.

    Args:
        force: When True, recompute every ticker regardless of freshness.
            Default False uses the same 7-day stale-check as the Streamlit
            "↻ Refresh all" button.

    Returns:
        JSON with three keys:
            computed: list of tickers successfully refreshed
            errors: list of "TICKER: error" strings
            skipped: list of tickers that were fresh and not forced
    """
    try:
        return _refresh_all_valuations_impl(force)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def save_to_watchlist(ticker: str, config: dict) -> str:
    """Save a DCF config to the LazyTheta watchlist in Supabase.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")
        config: Complete DCF config dict (from build_dcf_config). Two fields are
            validated because both silently move the discount rate:
              • "equity_market_value" ($M), required and positive — it anchors
                the CAPM rate (D/E relevering + WACC weights). Without it the
                rate falls back to stock_price × shares_outstanding and drifts
                with the market.
              • "sector_betas" — a list of [name, unlevered_beta,
                revenue_weight]. beta_u = sum(beta × weight), so the weights
                must sum to 1.0; a single sector takes weight 1.0. Do NOT repeat
                the beta in the weight slot — that squares it.
            A config failing either check is refused, not stored.

    Peers (config["peers"]) drive the multiples lens — each peer is a dict with
    "ticker", "op_margin", "rev_growth", and the two multiples the lens reads:
      • "fwd_pe"    — forward P/E. The lens IGNORES peers without it (a bare
        "pe" does nothing). Leave it out and it auto-fills from yfinance's
        forward P/E on the next refresh — that's the recommended default.
      • "ev_ebitda" — EV/EBITDA. Auto-fills from yfinance as a *trailing*
        multiple, which can skew the peer median upward for high-growth peers.
        To pin a *forward* value with judgement, set "ev_ebitda" AND keep that
        key OUT of the peer's "_auto_filled" list — it then survives refreshes.
        (Any key listed in a peer's "_auto_filled" is refreshed from yfinance.)

    Returns:
        Confirmation message.
    """
    try:
        return _save_to_watchlist_impl(ticker, config)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_config(ticker: str) -> str:
    """Read an existing DCF config from the LazyTheta watchlist.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")

    Returns:
        JSON with the complete DCF config, or error if not found.
    """
    try:
        return _get_config_impl(ticker)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_watchlist() -> str:
    """List all tickers on the LazyTheta watchlist with multi-lens valuation summary.

    Each entry has these keys (always present; values may be None when no
    valuation_summary is stored — run calculate_multi_lens_valuation to populate):
        ticker, company, updated, stock_price,
        fv_low, fv_mid, fv_high, buy_price, current_vs_mid,
        lens_count, verdict, phase,
        price_source, price_asof, price_stale

    stock_price is live where a quote could be had: Yahoo for the US listings,
    Deutsche Boerse for the European ones. price_source names where it came
    from, price_asof when that venue last traded it, and price_stale is True
    when no quote was available and the stored snapshot is showing instead —
    that number can be weeks old, so do not read it as the market.

    Returns:
        JSON array of dicts with the schema above.
    """
    try:
        return _get_watchlist_impl()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def update_valuation_inputs(ticker: str, fields: dict) -> str:
    """Override one or more valuation_inputs fields for a watchlist ticker.

    Use this to inject your own view (e.g. expected dividend growth, forward
    EPS, own historical multiples) that should NOT be overwritten by the next
    yfinance auto-refresh. Each updated field is removed from `_auto_filled`
    so subsequent refreshes preserve the override.

    IMPORTANT: only the keys listed below are actually read by a lens. Any
    other key is silently stored but has no effect on valuation. If you want
    to activate a specific lens, set the keys for that lens.

    Args:
        ticker: Stock ticker (e.g. "PEP")
        fields: Dict of valuation_inputs keys to set. Valid keys, grouped by
            which lens consumes them:

            Dividend lens (compute_dividend_lens):
                ttm_dividend         (float, $/share)
                dividend_5y_cagr     (float, decimal, e.g. 0.08 = 8%)
                median_5y_yield      (float, decimal, e.g. 0.025 = 2.5%)

            Historical lens (compute_historical_lens) — own-history multiples:
                historical_fwd_pe       (float, own 5y median forward P/E)
                historical_trailing_pe  (float, own 5y median trailing P/E)
                historical_ev_ebitda    (float, own 5y median EV/EBITDA)
                forward_eps             (float, $/share, also used by Multiples)
                ttm_eps                 (float, $/share)
                ttm_ebitda              (float, $M, also used by Multiples)

            Multiples lens (compute_multiples_lens) — peer-relative:
                forward_eps          (float, $/share)
                ttm_ebitda           (float, $M)
                (peer multiples come from cfg["peers"], not from this tool)

            Examples:
                {"dividend_5y_cagr": 0.08}
                {"forward_eps": 6.50, "ttm_ebitda": 15000}
                {"historical_trailing_pe": 50.0,
                 "historical_ev_ebitda": 35.0,
                 "historical_fwd_pe": 35.0}

    Returns:
        JSON string with the updated valuation_inputs dict, or
        {"error": "..."} if ticker is not on the watchlist.
    """
    try:
        return _update_valuation_inputs_impl(ticker, fields)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def update_dcf_scenario_adjustments(ticker: str, fields: dict) -> str:
    """Adjust the DCF bear/bull scenario adjustments that drive the DCF lens's
    fv_low/fv_high range (and the football-field bar width).

    The DCF lens has two range modes:
      - scenario_grid=False (default): hardcoded ±15% around base intrinsic
      - scenario_grid=True: runs 4×4 grid of bear/bull scenarios using these
        adjustments and takes min/max as fv_low/fv_high

    This tool updates the per-ticker bear/bull adjustments. Call
    `calculate_multi_lens_valuation(ticker, scenario_grid=True)` afterwards
    to see the new range.

    Args:
        ticker: Stock ticker (e.g. "AMZN")
        fields: Dict with any of these keys (all optional, but must be numbers):
            bear_growth_adj   (typical: -0.04 — subtract 4pp from revenue growth)
            bear_margin_adj   (typical: -0.02 — subtract 2pp from op margin)
            bull_growth_adj   (typical:  0.02 — add 2pp to revenue growth)
            bull_margin_adj   (typical:  0.02 — add 2pp to op margin)
            Examples:
                {"bear_growth_adj": -0.06}                      # more pessimistic bear
                {"bull_growth_adj": 0.04, "bull_margin_adj": 0.03}  # more aggressive bull
                {"bear_growth_adj": -0.04, "bear_margin_adj": -0.02,
                 "bull_growth_adj":  0.02, "bull_margin_adj":  0.02}  # full reset

    Returns:
        JSON dict with the current values for all four adjustments after the
        update, or {"error": "..."} on validation failure.
    """
    try:
        return _update_dcf_scenario_adjustments_impl(ticker, fields)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def update_lens_weights(ticker: str, weights: dict) -> str:
    """Override one or more lens weights for a watchlist ticker.

    Controls how much each lens contributes to weighted_fv_mid. By default
    DCF=0.50, Peers=0.25, Historical=0.25, Dividend=0.0, Reverse DCF=0.0.
    Specified keys merge into cfg["lens_weights"]; unspecified keys retain
    their current value (or fall back to defaults). The orchestrator
    renormalizes active lens weights to sum to 1.0 at compute time, so
    partial overrides are fine.

    Args:
        ticker: Stock ticker (e.g. "PEP")
        weights: Dict mapping lens keys to non-negative floats. Valid
            keys: dcf, multiples, historical, reverse_dcf, dividend.
            Examples:
                {"dividend": 0.20}              # opt in dividend lens for PEP
                {"dcf": 0.60, "multiples": 0.20, "historical": 0.20}
                {}                              # reset to DEFAULT_LENS_WEIGHTS

    Returns:
        JSON string with the updated lens_weights dict, or
        {"error": "..."} if ticker is not on the watchlist, an unknown
        lens key is given, or a weight is negative.
    """
    try:
        return _update_lens_weights_impl(ticker, weights)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Pre-scan / AI Research Sections
# ---------------------------------------------------------------------------

def _fill_prompt_template(prompt: str, ticker: str, company: str, prior_results: dict) -> str:
    """Apply {ticker}, {company}, {prior:Section Title} substitutions.

    Mirrors streamlit_app.py's _fill_prompt so the prompt Claude sees here is
    identical to what ▶ Run would send via Groq/Gemini."""
    import re

    ticker = ticker.upper()

    def _sub_prior(m):
        title = m.group(1).strip()
        content = (prior_results.get(title) or "").strip()
        if not content:
            return f"(no prior '{title}' analysis available for this ticker)"
        return content

    filled = re.sub(r"\{prior:([^}]+)\}", _sub_prior, prompt)
    filled = filled.replace("{ticker}", ticker).replace("{company}", company)
    if "{ticker}" not in prompt and "{company}" not in prompt and "{prior:" not in prompt:
        filled = (
            f"**IMPORTANT OVERRIDE:** The company to analyze is "
            f"**{company} (ticker: {ticker})**. "
            f"Do NOT ask the user for a company — it is provided here. "
            f"Begin the analysis immediately using this company.\n\n"
            f"---\n\n{filled}"
        )
    return filled


def _get_prescan_prompts_impl(ticker, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}
    company = cfg.get("company", ticker.upper())

    prefs = config_store.load_user_prefs(client, user_id=user_id)
    library = prefs.get("ai_prompts") or []
    if not library:
        return {"error": "Prompt library is empty. Open a watchlist editor in the app once to seed defaults."}

    ai_notes = cfg.get("ai_notes") or {}
    if not isinstance(ai_notes, dict):
        ai_notes = {}

    out = []
    for entry in library:
        title = entry.get("title")
        prompt_template = entry.get("prompt", "")
        if not title:
            continue
        out.append({
            "title": title,
            "prompt": _fill_prompt_template(prompt_template, ticker, company, ai_notes),
        })
    return out


def _get_prescan_sections_impl(ticker, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}
    ai_notes = cfg.get("ai_notes") or {}
    if not isinstance(ai_notes, dict):
        ai_notes = {}
    return ai_notes


def _save_prescan_section_impl(ticker, title, content,
                                user_id: str | None = None):
    if not title.strip():
        return {"error": "title is required"}
    parser = _CARD_PARSERS.get(title.strip())
    if parser is not None:
        try:
            parser(content)
        except ValueError as e:
            return {"error": f"{title.strip()} not saved: {e}"}
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}

    ai_notes = cfg.get("ai_notes") or {}
    if not isinstance(ai_notes, dict):
        ai_notes = {}
    ai_notes[title] = content
    cfg["ai_notes"] = ai_notes

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return f"Saved {ticker.upper()} → '{title}' ({len(content)} chars)."


def _tickers_missing_section_impl(title, requires="", limit=10,
                                  user_id: str | None = None):
    """Watchlist tickers without pre-scan section `title` (and, when
    `requires` is given, that do have that section), alphabetical."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfgs = config_store.load_all_configs(client, user_id=user_id)
    missing = []
    for t, cfg in sorted(cfgs.items()):
        notes = cfg.get("ai_notes") if isinstance(cfg.get("ai_notes"), dict) else {}
        if str(notes.get(title) or "").strip():
            continue
        if requires and not str(notes.get(requires) or "").strip():
            continue
        missing.append(t)
    n = max(int(limit or 10), 0)
    return json.dumps({"tickers": missing[:n], "remaining": len(missing) - len(missing[:n])})


def _get_screener_candidates_impl(limit=5, user_id: str | None = None):
    """Passing names from the latest Screener run that are not on the list yet."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    resp = (client.table("screener_snapshots")
            .select("computed_at, rows")
            .order("created_at", desc=True).limit(1).execute())
    if not (resp and resp.data):
        return json.dumps({"candidates": [], "computed_at": None})
    snap = resp.data[0]
    listed = {e["ticker"].upper()
              for e in config_store.list_watchlist(client, user_id=user_id)}
    rows = [r for r in snap.get("rows") or []
            if r.get("passes") and (r.get("ticker") or "").upper() not in listed]
    rows.sort(key=lambda r: r.get("avg_roce") or 0, reverse=True)
    return json.dumps({
        "computed_at": snap.get("computed_at"),
        "candidates": [{"ticker": r["ticker"], "company": r.get("name"),
                        "sector": r.get("sector"), "avg_roce": r.get("avg_roce"),
                        "net_debt": r.get("net_debt")}
                       for r in rows[:max(int(limit or 5), 0)]],
    }, default=str)


def _add_aspirant_impl(ticker, stock_price=0, user_id: str | None = None):
    """Put a new name on the list as Aspirant; never touch an existing one."""
    from datetime import date
    user_id = user_id or USER_ID
    ticker = ticker.upper()
    client = get_supabase_client()
    if config_store.load_config(client, ticker, user_id=user_id) is not None:
        return f"{ticker} is already on the watchlist; not overwritten."
    try:
        cfg = gather_data.build_base_config(ticker, stock_price=stock_price or 0)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    cfg.update({"category": "Aspirant", "dcf_placeholder": True,
                "aspirant_added": date.today().isoformat()})
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return f"Added {ticker} as Aspirant."


def _promote_aspirant_impl(ticker, user_id: str | None = None):
    """Aspirant -> Uncategorized, only with a Wide moat and a filled-in DCF."""
    from datetime import date
    user_id = user_id or USER_ID
    ticker = ticker.upper()
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker} not on watchlist"})
    blockers = aspirant.promotion_blockers(cfg)
    if blockers:
        return json.dumps({"error": f"{ticker} not promoted: " + "; ".join(blockers)})
    config_store.save_config(client, ticker, {
        **cfg, "category": "Uncategorized", "promoted_by": "claude",
        "promoted_at": date.today().isoformat()}, user_id=user_id)
    return f"Promoted {ticker} to Uncategorized."


def _set_category_impl(ticker, category, user_id: str | None = None):
    """Move a name to one of the watchlist categories (e.g. No to reject)."""
    user_id = user_id or USER_ID
    ticker = ticker.upper()
    if category not in aspirant.CATEGORIES:
        return json.dumps({"error": f"Unknown category {category!r}; use one of "
                                    f"{', '.join(aspirant.CATEGORIES)}"})
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker} not on watchlist"})

    current = cfg.get("category")
    # An Aspirant leaves that category only via promote_aspirant (Wide moat,
    # filled-in DCF) or a reject to "No"; every other target is refused here.
    if current == "Aspirant" and category not in ("Aspirant", "No"):
        return json.dumps({
            "error": f"{ticker} is an Aspirant; use promote_aspirant to move it "
                     f"to Uncategorized, or set_category(ticker, \"No\") to reject it.",
        })
    # "Aspirant" is add_aspirant's category, not one set_category hands out.
    if category == "Aspirant" and current != "Aspirant":
        return json.dumps({"error": "Aspirant is only set by add_aspirant."})

    config_store.save_config(client, ticker, {**cfg, "category": category,
                                              "promoted_by": None}, user_id=user_id)
    return f"{ticker} → {category}."


def _set_robustness_impl(ticker, axes, user_id: str | None = None):
    """Store the 4 qualitative robustness axes (band + note each) as the
    'Robustness' ai_notes section, recompute the data axes (ROCE/net debt) from
    fundamentals, and persist the merged table + weakest-link verdict to
    cfg['robustness']. Existing user overrides are preserved."""
    import json as _json
    from datetime import UTC, datetime

    import robustness

    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}

    ai_notes = cfg.get("ai_notes") if isinstance(cfg.get("ai_notes"), dict) else {}
    ai_notes["Robustness"] = "```json\n" + _json.dumps({"axes": axes}, ensure_ascii=False) + "\n```"

    try:
        fund_raw = gather_data.fetch_fundamentals(ticker, n_years=10)
        fund = gather_data.apply_fundamentals_overrides(
            fund_raw, cfg.get("fundamentals_overrides") or {})
        headline = _compute_fundamentals_headline(fund, cfg)
    except Exception as e:
        return {"error": f"fundamentals fetch failed: {e}"}

    overrides = (cfg.get("robustness") or {}).get("overrides") or {}
    table = robustness.build_table(headline, ai_notes, overrides)
    table["computed_at"] = datetime.now(UTC).isoformat()

    cfg["ai_notes"] = ai_notes
    cfg["robustness"] = table
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return (f"Saved {ticker.upper()} robustness → {table['verdict']} "
            f"({table['verdict_mapped']}): {table['verdict_reason']}.")


@mcp.tool()
def get_prescan_prompts(ticker: str) -> str:
    """Return the user's pre-scan prompts with {ticker}/{company}/{prior:...}
    placeholders already substituted, ready to send to an LLM.

    Use this to fill in the AI Research Sections in the LazyTheta watchlist
    editor. Each entry has the section title and the filled prompt — generate
    a markdown answer per section, then call save_prescan_section to persist.

    Args:
        ticker: Stock ticker symbol (e.g. "NFLX")

    Returns:
        JSON array of {title, prompt} objects, in the order they appear in
        the user's prompt library. Or {"error": "..."} on failure.
    """
    try:
        return json.dumps(_get_prescan_prompts_impl(ticker), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_prescan_sections(ticker: str) -> str:
    """List the existing pre-scan section content for a ticker.

    Useful to see what's already filled in (so Claude knows what to skip
    or update). For the Scorecard section, the content is a fenced JSON
    block; for other sections it's free-form Markdown.

    Args:
        ticker: Stock ticker symbol (e.g. "NFLX")

    Returns:
        JSON object {title: content_string} for every existing section.
    """
    try:
        return json.dumps(_get_prescan_sections_impl(ticker), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def save_prescan_section(ticker: str, title: str, content: str) -> str:
    """Save Markdown content (or a fenced JSON block, for the Scorecard) to
    one pre-scan section of a ticker. Other sections are preserved.

    Args:
        ticker: Stock ticker symbol (e.g. "NFLX")
        title: Section title — must match one of the user's prompt library
            entries (e.g. "Business Description", "Moat", "Scorecard").
        content: Markdown body. For the Scorecard section, format as a
            ```json fenced block to be parsed by the visual renderer.

    Returns:
        Confirmation string or {"error": "..."} JSON.
    """
    try:
        result = _save_prescan_section_impl(ticker, title, content)
        if isinstance(result, dict):
            return json.dumps(result)
        return result
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def tickers_missing_section(title: str, requires: str = "", limit: int = 10) -> str:
    """Watchlist tickers that lack pre-scan section `title` — e.g. "Moat Cards"
    with requires="Moat Analysis" for a backfill. Returns JSON
    {tickers: [...], remaining: n}.
    """
    try:
        return _tickers_missing_section_impl(title, requires, limit)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_screener_candidates(limit: int = 5) -> str:
    """Names that pass the latest Screener run and are not on the watchlist
    yet (in any category), highest average ROCE first.

    Returns JSON {computed_at, candidates: [{ticker, company, sector,
    avg_roce, net_debt}]}.
    """
    try:
        return _get_screener_candidates_impl(limit)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def add_aspirant(ticker: str, stock_price: float = 0) -> str:
    """Add a NEW name to the watchlist in category Aspirant, with a facts-only
    base config (EDGAR) marked dcf_placeholder. Refuses if the ticker already
    has a config — it never overwrites. stock_price is optional: when omitted
    the server fetches it (Nasdaq, then Yahoo).
    """
    try:
        return _add_aspirant_impl(ticker, stock_price)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def promote_aspirant(ticker: str) -> str:
    """Move an Aspirant to Uncategorized. Refused unless the Moat section's
    verdict is Wide, the DCF is filled in (no placeholder, equity_market_value,
    sector_betas weights sum to 1.0) and a valuation_summary exists.
    """
    try:
        return _promote_aspirant_impl(ticker)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def set_category(ticker: str, category: str) -> str:
    """Set a watchlist name's category: Yes, Maybe, Watch Later, No or
    Uncategorized. "No" is how an Aspirant is rejected. An Aspirant may only
    leave that category via promote_aspirant or a set_category(ticker, "No")
    reject — any other target category is refused here. "Aspirant" itself is
    only set by add_aspirant, not by this tool.
    """
    try:
        return _set_category_impl(ticker, category)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def set_robustness(ticker: str, axes: dict) -> str:
    """Set the 4 qualitative robustness axes for a watchlist ticker and
    recompute the Prasad robustness verdict.

    Args:
        ticker: Stock ticker (e.g. "META").
        axes: dict of the four qualitative axes, each {"band": "robust|mid|
            fragile", "note": "..."}. Keys: customers, barriers, management,
            industry. ROCE and net-debt axes are computed from data — omit them.

    Returns:
        A status string with the derived verdict (robust/borderline/fragile)
        and reason. ROCE/net-debt axes + verdict are computed server-side.
    """
    try:
        result = _set_robustness_impl(ticker, axes)
        if isinstance(result, dict):
            return json.dumps(result)
        return result
    except Exception as e:
        return json.dumps({"error": str(e)})


# Fixed pre-mortem schema — same sections for every ticker.
PREMORTEM_SECTIONS = [
    ("current", "Current view"),
    ("sell", "Sell triggers"),
    ("add", "Add triggers"),
    ("ignore", "Not a sell reason"),
    ("discipline", "Discipline"),
]


def _premortem_dict(current="", sell=None, add=None, ignore=None, discipline=None):
    """Build the structured pre-mortem dict from the fixed fields. List fields
    accept a list or a newline-separated string; each item is bullet-cleaned."""
    def _cl(x):
        if isinstance(x, str):
            x = x.split("\n")
        return [str(i).strip(" -•\t") for i in (x or []) if str(i).strip(" -•\t")]
    return {
        "current": (current or "").strip(),
        "sell": _cl(sell), "add": _cl(add),
        "ignore": _cl(ignore), "discipline": _cl(discipline),
    }


def _set_premortem_impl(ticker, current="", sell=None, add=None, ignore=None,
                        discipline=None, user_id: str | None = None):
    """Set the structured pre-mortem (cfg['premortem'] = fixed-section dict)."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}
    cfg["premortem"] = _premortem_dict(current, sell, add, ignore, discipline)
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return f"Saved pre-mortem for {ticker.upper()}."


@mcp.tool()
def set_premortem(ticker: str, current: str = "",
                  sell: list[str] | None = None, add: list[str] | None = None,
                  ignore: list[str] | None = None,
                  discipline: list[str] | None = None) -> str:
    """Set the structured pre-mortem / action-triggers for a watchlist ticker.

    Shown atop the Pre-Scan tab with the SAME fixed sections for every ticker
    (stored as cfg['premortem']). Overwrites; read back via get_config
    (the 'premortem' object). Keep each list item to one short condition.

    Args:
        ticker: Stock ticker (e.g. "PEP").
        current: One-line current view (spot / cost basis / fair value / buy price).
        sell: Sell / thesis-breaker triggers (list).
        add: Add / buy-more triggers (list).
        ignore: Signals that are NOT a reason to sell — noise to ignore (list).
        discipline: Decision-discipline rules (list).
    """
    try:
        return _set_premortem_impl(ticker, current=current, sell=sell, add=add,
                                   ignore=ignore, discipline=discipline)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Notifications (custom reminders + per-ticker alert opt-in)
# ---------------------------------------------------------------------------

def _add_reminder_impl(text, fire_date, ticker=None, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    notifications.add_custom_reminder(client, fire_date, text, ticker=ticker, user_id=user_id)
    return f"Reminder set for {fire_date}" + (f" · {ticker.upper()}" if ticker else "") + "."


def _list_reminders_impl(user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    return notifications.list_custom_reminders(client, user_id=user_id)


def _delete_reminder_impl(reminder_id, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    notifications.delete_custom_reminder(client, reminder_id, user_id=user_id)
    return "Reminder deleted."


def _set_ticker_alert_impl(ticker, enabled, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    notifications.set_ticker_alert(client, ticker, bool(enabled), user_id=user_id)
    return f"Price/earnings alerts {'enabled' if enabled else 'disabled'} for {ticker.upper()}."


@mcp.tool()
def add_reminder(text: str, fire_date: str, ticker: str = "") -> str:
    """Schedule a custom reminder. Fires on the date via Telegram (if linked) +
    the in-app notifications feed.

    Args:
        text: The reminder text.
        fire_date: Date to fire, 'YYYY-MM-DD'.
        ticker: Optional ticker to tag the reminder with (e.g. "MSFT").
    """
    try:
        return _add_reminder_impl(text, fire_date, ticker=(ticker or None))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_reminders() -> str:
    """List the user's pending custom reminders (id, fire_date, text, ticker)."""
    try:
        return json.dumps(_list_reminders_impl(), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_reminder(reminder_id: str) -> str:
    """Delete a pending custom reminder by its id (from list_reminders)."""
    try:
        return _delete_reminder_impl(reminder_id)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def set_ticker_alert(ticker: str, enabled: bool) -> str:
    """Turn buy-price + earnings alerts on/off for a watchlist ticker. Note: alerts
    only fire for 'Yes'-category tickers; this is the per-ticker opt-in within that.

    Args:
        ticker: Stock ticker (e.g. "MSFT").
        enabled: True to enable alerts, False to disable.
    """
    try:
        return _set_ticker_alert_impl(ticker, enabled)
    except Exception as e:
        return json.dumps({"error": str(e)})


def _add_price_alert_impl(ticker, target, direction=None, note=None, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    if direction not in ("above", "below"):
        cfg = config_store.load_config(client, ticker, user_id=user_id) or {}
        px = cfg.get("stock_price") or 0
        direction = "below" if (px and float(target) < float(px)) else "above"
    notifications.add_price_alert(client, ticker, target, direction, note=note, user_id=user_id)
    arrow = "≥" if direction == "above" else "≤"
    return f"Price alert set: {ticker.upper()} {arrow} {target}."


def _list_price_alerts_impl(user_id: str | None = None):
    user_id = user_id or USER_ID
    return notifications.list_price_alerts(get_supabase_client(), user_id=user_id)


def _delete_price_alert_impl(alert_id, user_id: str | None = None):
    user_id = user_id or USER_ID
    notifications.delete_price_alert(get_supabase_client(), alert_id, user_id=user_id)
    return "Price alert deleted."


@mcp.tool()
def add_price_alert(ticker: str, target: float, direction: str = "", note: str = "") -> str:
    """Set a one-shot price-target alert (standalone — NOT the buy-price auto alert).
    Fires once via Telegram + in-app when the price crosses the target, then
    deactivates.

    Args:
        ticker: Stock ticker (e.g. "META").
        target: Target price (e.g. 540).
        direction: 'above' or 'below'. If omitted, inferred from the last known
            price (target below price → 'below', else 'above').
        note: Optional note included in the alert.
    """
    try:
        return _add_price_alert_impl(ticker, target, direction=(direction or None),
                                     note=(note or None))
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def list_price_alerts() -> str:
    """List the user's active price-target alerts (id, ticker, direction, target)."""
    try:
        return json.dumps(_list_price_alerts_impl(), default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def delete_price_alert(alert_id: str) -> str:
    """Delete a price-target alert by its id (from list_price_alerts)."""
    try:
        return _delete_price_alert_impl(alert_id)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")


# ---------------------------------------------------------------------------
# Trading 212 — read-only broker tools
# ---------------------------------------------------------------------------
# T212 has no OAuth: authentication is an API key + secret the user generates
# in the app and stores through Lazy Theta's Broker Connections screen. So
# these tools authenticate the *caller* (Supabase Auth, via the MCP's JWT) and
# then look up that caller's own key. Read-only throughout — no order
# endpoints are touched.


def _t212_creds(user_id: str | None):
    """The caller's own T212 credentials, or None.

    user_id is mandatory in spirit even though the signature allows None for
    the stdio path: this server runs with the service-role key, which bypasses
    RLS, so an unscoped read would return whichever row came back first — a
    different user's brokerage account.
    """
    user_id = user_id or USER_ID
    return config_store.load_t212_credentials(get_supabase_client(),
                                              user_id=user_id)


_T212_NOT_CONNECTED = (
    "Trading 212 is not connected for this account. Add an API key under "
    "Account → Broker Connections in Lazy Theta first."
)


def _t212_positions_impl(user_id: str | None = None) -> str:
    """Core logic for t212_positions."""
    import t212_api

    creds = _t212_creds(user_id)
    if not creds:
        return _T212_NOT_CONNECTED
    positions, account_id = t212_api.fetch_portfolio_data(creds)

    rows = []
    for symbol, d in sorted(positions.items()):
        # Closed names ride along so the Cost Basis page can show a card for
        # them; a positions tool that listed them would report holdings the
        # user does not have.
        if not d.get("shares_held"):
            continue
        # Computed, not read: t212_api does not set market_value — the
        # Streamlit layer works it out as price x shares on the way to the
        # table. Reading the key gave every position a value of zero.
        shares = d["shares_held"]
        price = d.get("broker_price") or 0.0
        rows.append({
            "ticker": symbol,
            "shares": round(shares, 4),
            "cost_per_share": round(d.get("purchase_price") or 0.0, 2),
            "price": round(price, 2),
            "market_value": round(shares * price, 2),
            "unrealized_pl": round(d.get("total_pl") or 0.0, 2),
            "currency": d.get("currency", "USD"),
            "isin": d.get("isin", ""),
        })
    return json.dumps({
        "account_id": account_id,
        "currency_note": "All figures converted to USD.",
        "positions": rows,
    }, default=str)


def _t212_balance_impl(user_id: str | None = None) -> str:
    """Core logic for t212_balance."""
    import t212_api

    creds = _t212_creds(user_id)
    if not creds:
        return _T212_NOT_CONNECTED
    b = t212_api.fetch_account_balances(creds)
    return json.dumps({
        "net_liquidating_value": round(b.get("net_liquidating_value") or 0.0, 2),
        "cash_balance": round(b.get("cash_balance") or 0.0, 2),
        "currency": b.get("currency", "USD"),
        # The account's own currency and the rate applied, so the figures can
        # be reconciled against what Trading 212 itself shows.
        "account_currency": b.get("native_currency", ""),
        "fx_rate_used": b.get("fx_rate", 1.0),
    }, default=str)


def _t212_transactions_impl(ticker: str | None = None,
                            start_date: str | None = None,
                            end_date: str | None = None,
                            user_id: str | None = None) -> str:
    """Core logic for t212_transactions."""
    import t212_api

    creds = _t212_creds(user_id)
    if not creds:
        return _T212_NOT_CONNECTED
    positions, _ = t212_api.fetch_portfolio_data(creds)

    want = ticker.upper() if ticker else None
    fills = []
    for symbol, d in positions.items():
        if want and symbol.upper() != want:
            continue
        for t in d.get("trades") or []:
            day = str(t.get("date") or "")
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            fills.append({
                "ticker": symbol,
                "date": day,
                "action": t.get("action", ""),
                "quantity": t.get("quantity"),
                "price": round(t.get("price") or 0.0, 4),
                "net_value": round(t.get("net_value") or 0.0, 2),
            })
    # Oldest first: the order a position was built in is what makes a cost
    # basis readable, and the broker's own ordering is not to be trusted.
    fills.sort(key=lambda f: (f["date"], f["ticker"]))
    return json.dumps({
        "currency_note": "Prices converted to USD.",
        "count": len(fills),
        "fills": fills,
    }, default=str)


@mcp.tool()
def t212_positions() -> str:
    """Open Trading 212 positions, converted to USD.

    Read-only. Requires a Trading 212 API key connected in Lazy Theta
    (Account → Broker Connections).

    Returns:
        JSON with account_id and a list of positions: ticker, shares,
        cost_per_share (FIFO), price, market_value, unrealized_pl,
        currency, isin.
    """
    try:
        return _t212_positions_impl()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def t212_balance() -> str:
    """Trading 212 account value and free cash, converted to USD.

    Also returns the account's own currency and the FX rate applied, so the
    figures can be reconciled against Trading 212's own screen.
    """
    try:
        return _t212_balance_impl()
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def t212_transactions(ticker: str | None = None,
                      start_date: str | None = None,
                      end_date: str | None = None) -> str:
    """Trading 212 fill history, oldest first, prices converted to USD.

    Args:
        ticker: only this ticker, e.g. "RDDT".
        start_date: YYYY-MM-DD, inclusive.
        end_date: YYYY-MM-DD, inclusive.

    Returns:
        JSON with count and a list of fills: ticker, date, action, quantity,
        price, net_value.
    """
    try:
        return _t212_transactions_impl(ticker=ticker, start_date=start_date,
                                       end_date=end_date)
    except Exception as e:
        return json.dumps({"error": str(e)})
