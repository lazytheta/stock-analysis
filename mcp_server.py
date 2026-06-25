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
import auto_fetch
import config_store
import valuation_lenses
from scorecard_utils import compute_roce_metric
import notifications


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_sector_betas(sic_code, sic_description=""):
    """Convert SIC code to sector_betas list of (name, beta, weight) tuples."""
    sic_int = int(sic_code) if sic_code else 0

    if sic_int in gather_data.SIC_TO_SECTOR:
        sector_name, sector_beta = gather_data.SIC_TO_SECTOR[sic_int]
        return [(sector_name, sector_beta, 1.0)]

    dam_betas = gather_data.fetch_sector_betas()
    if dam_betas and sic_description:
        sic_words = set(sic_description.lower().split())
        best_match, best_score = None, 0
        for sector, beta in dam_betas.items():
            sector_words = set(sector.lower().split())
            overlap = len(sic_words & sector_words)
            if overlap > best_score:
                best_score = overlap
                best_match = (sector, beta)
        if best_match and best_score > 0:
            return [(best_match[0], best_match[1], 1.0)]

    return [("Market", 1.0, 1.0)]


def _resolve_sector_margin(sector_betas):
    """Fetch sector median margin from Damodaran, matching on sector name."""
    sector_name = sector_betas[0][0] if sector_betas else ""
    if not sector_name:
        return None

    dam_margins = gather_data.fetch_sector_margins()
    if not dam_margins:
        return None

    if sector_name in dam_margins:
        return dam_margins[sector_name]

    target_words = set(sector_name.lower().replace("/", " ").split())
    best_match, best_score = None, 0
    for sec_name, sec_margin in dam_margins.items():
        sec_words = set(sec_name.lower().replace("/", " ").split())
        overlap = len(target_words & sec_words)
        if overlap > best_score:
            best_score = overlap
            best_match = (sec_name, sec_margin)
    if best_match and best_score > 0:
        return best_match[1]
    return None


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

    risk_free_rate = gather_data.fetch_treasury_yield()

    nominal_risk_free_rate = None
    if valuation_basis == "real":
        nominal_risk_free_rate = risk_free_rate
        risk_free_rate = gather_data.fetch_tips_yield()

    shares = financial_data.get("shares", [])
    shares_latest = shares[-1] if shares else 0
    market_cap = stock_price * shares_latest

    oi_latest = financial_data.get("operating_income", [0])[-1] or 0
    ie_latest = financial_data.get("interest_expense_latest", 0) or 0
    credit_rating, credit_spread = gather_data.synthetic_credit_rating(oi_latest, ie_latest)

    sector_betas = _resolve_sector_betas(sic_code, sic_description)

    if sector_margin is None:
        sector_margin = _resolve_sector_margin(sector_betas)

    peers = []
    if sic_code and market_cap > 0:
        try:
            peer_tickers = gather_data.find_peers(
                sic_code=int(sic_code),
                target_ticker=ticker,
                target_market_cap=market_cap,
            )
            peers = gather_data.fetch_peer_data(peer_tickers)
        except Exception as e:
            logger.warning("Peer lookup failed: %s", e)

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
        sector_margin=sector_margin,
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
    """Core logic for calculate_multi_lens_valuation: load cfg, auto-fetch
    yfinance market data + historical multiples, run all lenses, persist
    summary, return JSON."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})

    # Auto-fetch yfinance market data + historical multiples before the
    # orchestrator. Matches Streamlit's _refresh_one. Best-effort: yfinance
    # failures don't block the lens computation.
    cfg.setdefault("ticker", ticker)
    auto_fetch.auto_fill_valuation_inputs(cfg)
    auto_fetch.auto_fill_peer_market_data(cfg)
    auto_fetch.auto_fill_dividend_inputs(cfg)

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
        c = config_store.load_config(client, t, user_id=user_id)
        return (t, c) if c is not None else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        loaded = {r[0]: r[1] for r in pool.map(_load, tickers) if r}

    targets = list(loaded.keys()) if force else [t for t, c in loaded.items() if _is_stale(c)]
    skipped = [t for t in loaded if t not in targets]

    computed: list[str] = []
    errors: list[str] = []

    def _refresh_one(ticker: str) -> str:
        cfg = dict(loaded[ticker])
        cfg.setdefault("ticker", ticker)
        auto_fetch.auto_fill_valuation_inputs(cfg)
        auto_fetch.auto_fill_peer_market_data(cfg)
        auto_fetch.auto_fill_dividend_inputs(cfg)
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
    user_id = user_id or USER_ID
    client = get_supabase_client()
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


def _get_watchlist_impl(user_id: str | None = None):
    """Core logic for get_watchlist."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=user_id)
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


def _update_sotp_segments_impl(ticker: str, segments: list,
                                user_id: str | None = None) -> str:
    """Core logic for update_sotp_segments. Upsert-by-name with partial merge.

    For each input segment, match `name` against existing cfg.sotp.segments
    using case-insensitive trim. Match found → merge supplied (non-None)
    fields into the existing segment. No match → append as new segment
    (requires ev_mid > 0).

    Initialises cfg["sotp"] = {} if not yet present, then sets/updates "segments".
    Other top-level sotp keys (e.g. corporate_overhead_ev_adjustment) are untouched.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    if not isinstance(segments, list) or not segments:
        return json.dumps({
            "error": "segments must be a non-empty list",
        })

    allowed_fields = {
        "name", "ev_mid", "ev_low", "ev_high",
        "revenue", "operating_margin", "implied_multiple_mid", "rationale",
    }
    numeric_fields = {
        "ev_mid", "ev_low", "ev_high", "revenue",
        "operating_margin", "implied_multiple_mid",
    }
    nonneg_fields = {"ev_mid", "ev_low", "ev_high"}

    sotp = cfg.setdefault("sotp", {})
    existing = list(sotp.get("segments") or [])

    def _norm(n):
        return (n or "").strip().lower()

    for idx, inp in enumerate(segments):
        if not isinstance(inp, dict):
            return json.dumps({
                "error": f"segment[{idx}] must be an object",
            })
        name = (inp.get("name") or "").strip()
        if not name:
            return json.dumps({
                "error": f"segment[{idx}] missing required 'name'",
            })
        for k, v in inp.items():
            if k not in allowed_fields:
                return json.dumps({
                    "error": f"segment '{name}': unknown field '{k}'. "
                             f"Allowed: {sorted(allowed_fields)}",
                })
            if k in numeric_fields and v is not None:
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    return json.dumps({
                        "error": f"segment '{name}': field '{k}' must be "
                                 f"a number, got {type(v).__name__}={v!r}",
                    })
                if k in nonneg_fields and v < 0:
                    return json.dumps({
                        "error": f"segment '{name}': field '{k}' must be "
                                 f">= 0, got {v}",
                    })

        match_idx = next(
            (i for i, s in enumerate(existing)
             if _norm(s.get("name")) == _norm(name)),
            None,
        )
        if match_idx is None:
            ev_mid = inp.get("ev_mid")
            if not isinstance(ev_mid, (int, float)) or isinstance(ev_mid, bool) \
                    or ev_mid <= 0:
                return json.dumps({
                    "error": f"new segment '{name}' requires ev_mid > 0",
                })
            new_seg = {k: v for k, v in inp.items()
                       if k in allowed_fields and v is not None}
            new_seg["name"] = name  # use trimmed name
            existing.append(new_seg)
        else:
            merged = dict(existing[match_idx])
            for k, v in inp.items():
                if k == "name":
                    continue  # preserve original stored name; don't overwrite with input casing
                if k in allowed_fields and v is not None:
                    merged[k] = v
            existing[match_idx] = merged

    sotp["segments"] = existing

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({
        "ticker": ticker.upper(),
        "sotp": sotp,
        "segment_count": len(existing),
    }, default=str)


def _remove_sotp_segment_impl(ticker: str, name: str,
                               user_id: str | None = None) -> str:
    """Core logic for remove_sotp_segment. Case-insensitive name match.
    Idempotent — removing a non-existent name is a no-op, not an error.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    target = (name or "").strip().lower()
    if not target:
        return json.dumps({"error": "name must be a non-empty string"})

    sotp = cfg.get("sotp") or {}
    segments = list(sotp.get("segments") or [])
    new_segments = [s for s in segments
                    if (s.get("name") or "").strip().lower() != target]

    if len(new_segments) != len(segments):
        sotp["segments"] = new_segments
        cfg["sotp"] = sotp
        config_store.save_config(client, ticker, cfg, user_id=user_id)

    return json.dumps({
        "ticker": ticker.upper(),
        "sotp": cfg.get("sotp") or {"segments": []},
        "segment_count": len(new_segments),
        "removed": len(segments) - len(new_segments),
    }, default=str)


def _set_sotp_corporate_overhead_impl(ticker: str, value: float,
                                       user_id: str | None = None) -> str:
    """Core logic for set_sotp_corporate_overhead. Scalar setter for
    cfg["sotp"]["corporate_overhead_ev_adjustment"]. Initialises cfg["sotp"]
    with segments: [] if not yet present.

    Typical magnitudes are negative ($M, e.g. -5000 for $5B of unallocated
    corporate overhead capitalized into the bridge).
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return json.dumps({
            "error": f"value must be a number, got {type(value).__name__}={value!r}",
        })

    sotp = cfg.get("sotp")
    if not isinstance(sotp, dict):
        sotp = {"segments": []}
    sotp["corporate_overhead_ev_adjustment"] = float(value)
    cfg["sotp"] = sotp

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({
        "ticker": ticker.upper(),
        "sotp": sotp,
    }, default=str)


# ---------------------------------------------------------------------------
# Fundamentals (read + override) — surfaces EDGAR data and per-year manual
# overrides so Claude can analyse raw historicals or correct bad XBRL tags.
# ---------------------------------------------------------------------------


def _phase_gate_metrics(fund):
    """Extra metrics for the phase-aware ROCE gate (robustness engine, see
    specs/2026-06-16-phase-aware-roce-gate-design): Rule of 40 (3y revenue CAGR
    + FCF margin), incremental ROIC (3-delta, best-effort), and latest-year
    ROCE + rising trend on the same EBIT/(TA−CL) basis as the headline ROCE.
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
    debt = fund.get("total_debt") or []
    eq = fund.get("total_equity") or []
    cash = fund.get("cash") or []
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

    # Incremental ROIC = ΔNOPAT / ΔInvestedCapital over the last 3 deltas
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
        d = debt[i] if i < len(debt) else None
        e = eq[i] if i < len(eq) else None
        c = (cash[i] if i < len(cash) else 0) or 0
        if d is not None and e is not None:
            invcap[i] = d + e - c
    common = sorted(set(nopat) & set(invcap))
    if len(common) >= 4:
        pts = common[-4:]
        d_nopat = nopat[pts[-1]] - nopat[pts[0]]
        d_inv = invcap[pts[-1]] - invcap[pts[0]]
        if d_inv > 0:  # guard: shrinking capital → sign-flipped artifact
            out["incremental_roic_pct"] = d_nopat / d_inv * 100

    # Latest-year ROCE + rising trend, EBIT/(TA−CL) (cash kept, like headline)
    roce = {}
    for i in range(n):
        oi_v = oi[i] if i < len(oi) else None
        ta_v = ta[i] if i < len(ta) else None
        cl_v = cl[i] if i < len(cl) else None
        if oi_v is not None and ta_v and cl_v is not None and (ta_v - cl_v) > 0:
            roce[i] = oi_v / (ta_v - cl_v) * 100
    rk = sorted(roce)
    if rk:
        out["roce_latest_pct"] = roce[rk[-1]]
        if len(rk) >= 2:
            out["roce_rising"] = roce[rk[-1]] > roce[rk[max(0, len(rk) - 4)]]
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

    # Avg ROCE (EBIT/(TA−CL)) with float-business ROE fallback + manual
    # override — single source of truth shared with the Streamlit watchlist
    # and detail page (scorecard_utils.compute_roce_metric).
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


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

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
            operating_income, net_income, cost_of_revenue, sbc, shares,
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
        config: Complete DCF config dict (from build_dcf_config).

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
        lens_count, verdict, phase

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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
