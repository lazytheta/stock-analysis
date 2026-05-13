"""Multi-lens fair value engine (Phase 1).

Pure functions: take a config dict, return a lens-result dict (or None).
No Supabase, no network, no streamlit imports — fully testable.
"""

import logging
import statistics
from datetime import UTC, datetime

import dcf_calculator

logger = logging.getLogger(__name__)


DEFAULT_LENS_WEIGHTS = {
    "dcf":         0.50,    # primary anchor — intrinsic value via cash flows (Damodaran-style)
    "multiples":   0.25,    # peer-relative cross-check
    "historical":  0.25,    # own-history cross-check
    "reverse_dcf": 0.0,     # anchors at current price by definition; not a true valuation
    "dividend":    0.00,
    "sotp":        0.00,    # opt-in per ticker — only relevant for multi-segment businesses
}

# Canonical ordered list of forward-looking lenses surfaced in the watchlist
# UI (lens-dots row, football field tooltip) and counted in the "{N} lenses"
# label. Reverse DCF intentionally excluded — anchors at current price by
# construction (see 2026-05-07 reverse-dcf-demote spec); Dividend stub is
# excluded only when the user hasn't opted in via lens_weights, but the lens
# ITSELF is forward-looking and belongs in this list.
#
# Single source of truth for 4 consumers:
# - streamlit_app._render_lens_dots (order)
# - streamlit_app._render_football_field (lens_order, uses display labels)
# - config_store.list_watchlist (_COUNTED_LENSES — derives keys only)
# - scripts/force_refresh_all.py (_counted — derives keys only)
#
# Adding a new forward lens is now a one-line change here instead of 4-site
# lockstep updates.
FORWARD_LENSES: tuple[tuple[str, str], ...] = (
    ("dcf",        "DCF"),
    ("multiples",  "Peers"),
    ("historical", "Historical"),
    ("dividend",   "Dividend"),
    ("sotp",       "SOTP"),
)

# Convenience: keys-only tuple for consumers that don't need display labels.
FORWARD_LENS_KEYS: tuple[str, ...] = tuple(k for k, _ in FORWARD_LENSES)


def compute_dividend_lens(cfg):
    """Hybrid Two-stage DDM + Yield Mean-Reversion lens.

    Sub-anchor A (DDM): 5y explicit dividend growth + Gordon terminal,
    discounted at cost of equity.
    Sub-anchor B (yield mean-reversion): TTM dividend / median 5y yield.
    Active only when ≥3y history (median_5y_yield available).

    Returns None when:
      - TTM dividend = 0 (non-payer)
      - dividend_5y_cagr is None (insufficient growth history)
      - cost_of_equity ≤ terminal_growth (Gordon would blow up)
      - any input is non-finite (NaN guard)
    """
    ticker = cfg.get("ticker", "?")
    inputs = cfg.get("valuation_inputs") or {}
    ttm = inputs.get("ttm_dividend") or 0.0
    raw_g = inputs.get("dividend_5y_cagr")
    median_yield = inputs.get("median_5y_yield")

    if ttm <= 0:
        logger.info("Dividend lens: skipping %s (ttm_dividend=0, non-payer)", ticker)
        return None
    if raw_g is None:
        logger.info(
            "Dividend lens: skipping %s (no dividend_5y_cagr, insufficient history)",
            ticker,
        )
        return None

    # Cap growth at 15% (defense in depth — gather_data already caps,
    # but a user override via update_valuation_inputs could be higher).
    g = min(raw_g, 0.15)
    g_term = cfg.get("terminal_growth", 0.025)

    try:
        ke = dcf_calculator.compute_cost_of_equity(cfg)
    except (KeyError, ZeroDivisionError, TypeError) as e:
        logger.info(
            "Dividend lens: skipping %s (compute_cost_of_equity failed: %s)",
            ticker, e,
        )
        return None

    # NaN / non-finite guards
    for v in (ttm, g, g_term, ke):
        if v != v or v in (float("inf"), float("-inf")):
            logger.info(
                "Dividend lens: skipping %s (non-finite input: "
                "ttm=%s g=%s g_term=%s ke=%s)",
                ticker, ttm, g, g_term, ke,
            )
            return None

    if ke <= g_term:
        logger.info(
            "Dividend lens: skipping %s (ke=%.4f <= g_term=%.4f, "
            "Gordon perpetuity would blow up)",
            ticker, ke, g_term,
        )
        return None

    # ── Sub-anchor A: Two-stage DDM ─────────────────────────────
    stage1_years = 5
    pv_stage1 = 0.0
    d = ttm
    for n in range(1, stage1_years + 1):
        d = d * (1 + g)
        pv_stage1 += d / ((1 + ke) ** n)

    d_terminal = d  # D_5
    terminal_value = d_terminal * (1 + g_term) / (ke - g_term)
    pv_terminal = terminal_value / ((1 + ke) ** stage1_years)
    ddm_fv = pv_stage1 + pv_terminal

    # ── Sub-anchor B: Yield Mean-Reversion ──────────────────────
    yield_mr_fv = None
    if median_yield is not None and median_yield > 0:
        yield_mr_fv = ttm / median_yield

    # ── Range derivation ───────────────────────────────────────
    if yield_mr_fv is not None:
        fv_low = min(ddm_fv, yield_mr_fv)
        fv_high = max(ddm_fv, yield_mr_fv)
        fv_mid = (ddm_fv + yield_mr_fv) / 2.0
    else:
        fv_low = ddm_fv * 0.85
        fv_mid = ddm_fv
        fv_high = ddm_fv * 1.15

    return {
        "fv_low": fv_low,
        "fv_mid": fv_mid,
        "fv_high": fv_high,
        "details": {
            "ttm_dividend": ttm,
            "growth_rate_stage1": g,
            "terminal_growth": g_term,
            "cost_of_equity": ke,
            "stage1_years": stage1_years,
            "ddm_fv": ddm_fv,
            "yield_mr_fv": yield_mr_fv,
            "median_5y_yield": median_yield,
        },
    }


def compute_dcf_lens(cfg, scenario_grid=False):
    """DCF lens. Always returns a result — never None."""
    wacc = dcf_calculator.compute_wacc(cfg)
    base = dcf_calculator.compute_intrinsic_value(cfg, wacc=wacc)
    base_intrinsic = base["intrinsic_value"]

    if not scenario_grid:
        return {
            "fv_low": base_intrinsic * 0.85,
            "fv_mid": base_intrinsic,
            "fv_high": base_intrinsic * 1.15,
            "details": {
                "wacc": wacc,
                "base_intrinsic": base_intrinsic,
                "scenarios": None,
            },
        }

    bull_g = cfg.get("bull_growth_adj", 0.02)
    bear_g = cfg.get("bear_growth_adj", -0.04)
    bull_m = cfg.get("bull_margin_adj", 0.02)
    bear_m = cfg.get("bear_margin_adj", -0.02)

    growth_offsets = [bear_g, bear_g / 2, bull_g / 2, bull_g]
    margin_offsets = [bear_m, bear_m / 2, bull_m / 2, bull_m]

    scenarios = []
    base_growth = list(cfg["revenue_growth"])
    base_margins = list(cfg["op_margins"])
    base_terminal_margin = cfg.get("terminal_margin", base_margins[-1])

    for g_off in growth_offsets:
        for m_off in margin_offsets:
            scen_cfg = dict(cfg)
            scen_cfg["revenue_growth"] = [g + g_off for g in base_growth]
            scen_cfg["op_margins"] = [m + m_off for m in base_margins]
            scen_cfg["terminal_margin"] = base_terminal_margin + m_off
            try:
                scen_wacc = dcf_calculator.compute_wacc(scen_cfg)
                price = dcf_calculator.compute_intrinsic_value(
                    scen_cfg, wacc=scen_wacc
                )["intrinsic_value"]
                scenarios.append(price)
            except Exception as e:
                logger.info(
                    "DCF scenario grid: skipping (g_off=%.3f, m_off=%.3f): %s",
                    g_off, m_off, e,
                )

    if not scenarios:
        scenarios = [base_intrinsic]

    return {
        "fv_low": min(scenarios),
        "fv_mid": base_intrinsic,
        "fv_high": max(scenarios),
        "details": {
            "wacc": wacc,
            "base_intrinsic": base_intrinsic,
            "scenarios": scenarios,
        },
    }


def compute_reverse_dcf_lens(cfg):
    """Reverse DCF. Single anchor at current price — answers 'what's priced in'.

    Always returns a result given a valid config (stock_price > 0). The lens
    isn't a fair-value estimate; its low weight reflects that.
    """
    reverse = dcf_calculator.compute_reverse_dcf(cfg)
    fv = cfg["stock_price"]
    return {
        "fv_low": fv,
        "fv_mid": fv,
        "fv_high": fv,
        "details": {
            "implied_growth": reverse["implied_growth"],
            "implied_margin": reverse["implied_margin"],
        },
    }


def _tukey_filter(values, k=1.5):
    """Damodaran-style outlier removal for peer multiples.

    Drops values outside [Q1 - k*IQR, Q3 + k*IQR]. Returns (kept_values,
    removed_indices). Falls back to (values, []) when:
      - len(values) < 4 (not enough data for meaningful quartiles)
      - filtering would leave fewer than 2 values

    Default k=1.5 follows Tukey's fence convention used by Damodaran for
    sector multiples in his published valuation data.
    """
    n = len(values)
    if n < 4:
        return list(values), []
    sorted_vals = sorted(values)
    q1 = statistics.median(sorted_vals[: n // 2])
    q3 = statistics.median(sorted_vals[(n + 1) // 2:])
    iqr = q3 - q1
    lo, hi = q1 - k * iqr, q3 + k * iqr
    kept = []
    removed_idx = []
    for i, v in enumerate(values):
        if lo <= v <= hi:
            kept.append(v)
        else:
            removed_idx.append(i)
    if len(kept) < 2:
        return list(values), []
    return kept, removed_idx


def _closest_peer_ticker(peers, target_op_margin, target_rev_growth):
    """Return the ticker of the peer with smallest weighted Euclidean
    distance on (op_margin, rev_growth). Informational only.
    """
    best_ticker, best_score = None, float("inf")
    for p in peers:
        om = p.get("op_margin")
        rg = p.get("rev_growth")
        if om is None or rg is None:
            continue
        score = (om - target_op_margin) ** 2 + (rg - target_rev_growth) ** 2
        if score < best_score:
            best_score = score
            best_ticker = p.get("ticker")
    return best_ticker


def compute_historical_lens(cfg):
    """Time-series 'own history' lens. Three sub-anchors:

    A   own historical forward P/E × forward_eps        (manual: requires historical_fwd_pe)
    A.2 own historical trailing P/E × ttm_eps           (auto-fetched in Phase 2-B.2)
    D   own historical EV/EBITDA × ttm_ebitda - net_debt (auto-fetched in Phase 2-B.2)

    Sub-anchors silently skipped when their inputs are missing. Lens fully
    returns None when all three skip.
    """
    inputs = cfg.get("valuation_inputs") or {}

    forward_eps = inputs.get("forward_eps")
    historical_fwd_pe = inputs.get("historical_fwd_pe")
    historical_trailing_pe = inputs.get("historical_trailing_pe")
    historical_ev_ebitda = inputs.get("historical_ev_ebitda")
    ttm_eps = inputs.get("ttm_eps")
    ttm_ebitda = inputs.get("ttm_ebitda")

    fv_anchors = []
    details = {
        "fwd_pe_own": None,
        "historical_trailing_pe_fv": None,
        "historical_ev_ebitda_fv": None,
        "skipped": [],
    }

    # A) own forward P/E (manual)
    if forward_eps and historical_fwd_pe:
        own_fv = historical_fwd_pe * forward_eps
        fv_anchors.append(own_fv)
        details["fwd_pe_own"] = own_fv
    else:
        reason = "fwd_pe_own (forward_eps or historical_fwd_pe missing)"
        details["skipped"].append(reason)
        logger.info("Historical lens: skipping %s", reason)

    # A.2) own historical trailing P/E × ttm_eps
    if historical_trailing_pe and ttm_eps and ttm_eps > 0:
        own_trailing_fv = historical_trailing_pe * ttm_eps
        fv_anchors.append(own_trailing_fv)
        details["historical_trailing_pe_fv"] = own_trailing_fv
    else:
        reason = "historical_trailing_pe (no historical_trailing_pe or ttm_eps)"
        details["skipped"].append(reason)
        logger.info("Historical lens: skipping %s", reason)

    # D) own historical EV/EBITDA × ttm_ebitda - net_debt → /shares
    if historical_ev_ebitda and ttm_ebitda:
        net_debt = (
            cfg.get("debt_market_value", 0.0)
            - cfg.get("cash_bridge", 0.0)
            - cfg.get("securities", 0.0)
        )
        shares = cfg.get("shares_outstanding") or 1.0
        own_evebitda_fv = (historical_ev_ebitda * ttm_ebitda - net_debt) / shares
        fv_anchors.append(own_evebitda_fv)
        details["historical_ev_ebitda_fv"] = own_evebitda_fv
    else:
        reason = "historical_ev_ebitda (no historical_ev_ebitda or ttm_ebitda)"
        details["skipped"].append(reason)
        logger.info("Historical lens: skipping %s", reason)

    if not fv_anchors:
        logger.info("Historical lens fully skipped (no anchors)")
        return None

    return {
        "fv_low": min(fv_anchors),
        "fv_mid": sum(fv_anchors) / len(fv_anchors),
        "fv_high": max(fv_anchors),
        "details": details,
    }


def compute_multiples_lens(cfg):
    """Peer-relative multiples lens. Two sub-anchors:

    B) peer-set forward P/E (median, min, max, Tukey-filtered) × forward_eps
    C) peer-set EV/EBITDA (median, min, max, Tukey-filtered) × ttm_ebitda - net_debt → /shares

    Own-history sub-anchors (A, A.2, D) live in compute_historical_lens.
    Sub-anchors silently skipped when their inputs are missing. Lens returns
    None when both peer sub-anchors skip.
    """
    inputs = cfg.get("valuation_inputs") or {}
    peers = cfg.get("peers") or []

    forward_eps = inputs.get("forward_eps")
    ttm_ebitda = inputs.get("ttm_ebitda")

    fv_anchors = []
    details = {
        "fwd_pe_peer_median": None,
        "ev_ebitda_peer_median": None,
        "closest_peer": None,
        "skipped": [],
    }

    # B) peer fwd P/E
    peer_fwd_pe_pairs = [(p["ticker"], p["fwd_pe"]) for p in peers if p.get("fwd_pe")]
    peer_fwd_pes_raw = [v for _, v in peer_fwd_pe_pairs]
    peer_fwd_pes, removed_idx = _tukey_filter(peer_fwd_pes_raw)
    details["peer_fwd_pe_outliers_removed"] = [peer_fwd_pe_pairs[i][0] for i in removed_idx]
    if peer_fwd_pes and forward_eps:
        median_pe = statistics.median(peer_fwd_pes)
        fv_low_p = min(peer_fwd_pes) * forward_eps
        fv_mid_p = median_pe * forward_eps
        fv_high_p = max(peer_fwd_pes) * forward_eps
        fv_anchors.extend([fv_low_p, fv_mid_p, fv_high_p])
        details["fwd_pe_peer_median"] = fv_mid_p
        avg_growth = sum(cfg.get("revenue_growth", [0.0])) / max(
            len(cfg.get("revenue_growth", [0.0])), 1
        )
        avg_margin = sum(cfg.get("op_margins", [0.0])) / max(
            len(cfg.get("op_margins", [0.0])), 1
        )
        details["closest_peer"] = _closest_peer_ticker(peers, avg_margin, avg_growth)
    else:
        reason = "fwd_pe_peer (no peers with fwd_pe or no forward_eps)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)

    # C) peer EV/EBITDA
    peer_ev_ebitda_pairs = [(p["ticker"], p["ev_ebitda"]) for p in peers if p.get("ev_ebitda")]
    peer_ev_ebitdas_raw = [v for _, v in peer_ev_ebitda_pairs]
    peer_ev_ebitdas, removed_idx_ev = _tukey_filter(peer_ev_ebitdas_raw)
    details["peer_ev_ebitda_outliers_removed"] = [peer_ev_ebitda_pairs[i][0] for i in removed_idx_ev]
    if peer_ev_ebitdas and ttm_ebitda:
        net_debt = (
            cfg.get("debt_market_value", 0.0)
            - cfg.get("cash_bridge", 0.0)
            - cfg.get("securities", 0.0)
        )
        shares = cfg.get("shares_outstanding") or 1.0
        median_ev = statistics.median(peer_ev_ebitdas)
        fv_low_e = (min(peer_ev_ebitdas) * ttm_ebitda - net_debt) / shares
        fv_mid_e = (median_ev * ttm_ebitda - net_debt) / shares
        fv_high_e = (max(peer_ev_ebitdas) * ttm_ebitda - net_debt) / shares
        fv_anchors.extend([fv_low_e, fv_mid_e, fv_high_e])
        details["ev_ebitda_peer_median"] = fv_mid_e
    else:
        reason = "ev_ebitda_peer (no peers with ev_ebitda or no ttm_ebitda)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)

    if not fv_anchors:
        logger.info("Multiples lens fully skipped (no peer anchors)")
        return None

    return {
        "fv_low": min(fv_anchors),
        "fv_mid": sum(fv_anchors) / len(fv_anchors),
        "fv_high": max(fv_anchors),
        "details": details,
    }


def compute_sotp_lens(cfg):
    """Sum-of-the-Parts lens. Aggregates per-segment Enterprise Values
    (with low/mid/high) into total equity value via the standard bridge.

    Returns None when no segments defined (lens is opt-in per ticker).

    Bridge:
      EV_total = SUM(segment_EVs) + equity_investments + corp_overhead_adj
      Equity   = EV_total + cash + securities - debt - minority - pension
      FV/share = Equity / shares_outstanding

    Range: aggregates from per-segment low/mid/high. Bridge items are point
    estimates (known accounting values), so the range is driven entirely by
    segment-EV uncertainty — which is where it belongs.

    Edge cases:
      - no segments → None
      - missing ev_low / ev_high → fall back to ev_mid (range collapses)
      - shares_outstanding = 0 → fallback to 1 (defensive)
    """
    ticker = cfg.get("ticker", "?")
    sotp = cfg.get("sotp") or {}
    segments = sotp.get("segments") or []

    if not segments:
        logger.info("SOTP lens: skipping %s (no sotp.segments defined)", ticker)
        return None

    def _seg(s, key):
        # Fall back to ev_mid when low/high not supplied
        v = s.get(key)
        if v is None:
            v = s.get("ev_mid", 0)
        return float(v or 0)

    total_ev_low = sum(_seg(s, "ev_low") for s in segments)
    total_ev_mid = sum(_seg(s, "ev_mid") for s in segments)
    total_ev_high = sum(_seg(s, "ev_high") for s in segments)

    corp_adj = float(sotp.get("corporate_overhead_ev_adjustment", 0) or 0)
    equity_inv = float(cfg.get("equity_investments", 0) or 0)

    # Bridge items — point estimates (latest year for cash/securities)
    cash_list = cfg.get("cash") or [0]
    cash_latest = float(cash_list[-1] if cash_list else 0)
    sec_list = cfg.get("st_investments") or [0]
    sec_latest = float(sec_list[-1] if sec_list else 0)
    debt = float(cfg.get("debt_market_value", 0) or 0)
    minority = float(cfg.get("minority_interest", 0) or 0)
    pension = float(cfg.get("unfunded_pension", 0) or 0)

    bridge_delta = equity_inv + corp_adj + cash_latest + sec_latest - debt - minority - pension

    shares = float(cfg.get("shares_outstanding", 1) or 1) or 1

    fv_low = (total_ev_low + bridge_delta) / shares
    fv_mid = (total_ev_mid + bridge_delta) / shares
    fv_high = (total_ev_high + bridge_delta) / shares

    # Defensive ordering: if user supplied low > high (typo), still sort sensibly
    if fv_low > fv_high:
        fv_low, fv_high = fv_high, fv_low

    return {
        "fv_low": fv_low,
        "fv_mid": fv_mid,
        "fv_high": fv_high,
        "details": {
            "total_ev_low": total_ev_low,
            "total_ev_mid": total_ev_mid,
            "total_ev_high": total_ev_high,
            "segment_count": len(segments),
            "segments": [
                {
                    "name": s.get("name", "?"),
                    "ev_low": _seg(s, "ev_low"),
                    "ev_mid": _seg(s, "ev_mid"),
                    "ev_high": _seg(s, "ev_high"),
                    "pct_of_total_mid": (
                        _seg(s, "ev_mid") / total_ev_mid if total_ev_mid else 0
                    ),
                    "rationale": s.get("rationale", ""),
                }
                for s in segments
            ],
            "bridge_delta": bridge_delta,
            "equity_value_mid": total_ev_mid + bridge_delta,
            "shares": shares,
            "corporate_overhead_ev_adjustment": corp_adj,
            "equity_investments": equity_inv,
        },
    }


def calculate_multi_lens_valuation(cfg, scenario_grid=False):
    """Run all lenses and return the valuation_summary dict.

    Pure function — does not mutate cfg, does not persist anywhere. Caller
    is responsible for storing the summary back to the config.
    """
    lenses = {
        "dcf":         compute_dcf_lens(cfg, scenario_grid=scenario_grid),
        "multiples":   compute_multiples_lens(cfg),
        "historical":  compute_historical_lens(cfg),
        "reverse_dcf": compute_reverse_dcf_lens(cfg),
        "dividend":    compute_dividend_lens(cfg),
        "sotp":        compute_sotp_lens(cfg),
    }

    weights_cfg = cfg.get("lens_weights") or DEFAULT_LENS_WEIGHTS
    active_names = [n for n, l in lenses.items() if l is not None]
    raw = {n: weights_cfg.get(n, DEFAULT_LENS_WEIGHTS.get(n, 0.0)) for n in active_names}
    total = sum(raw.values()) or 1.0
    norm = {n: w / total for n, w in raw.items()}

    for n in active_names:
        lenses[n]["weight"] = raw[n]
        lenses[n]["weight_normalized"] = norm[n]

    weighted_low = sum(lenses[n]["fv_low"] * norm[n] for n in active_names)
    weighted_mid = sum(lenses[n]["fv_mid"] * norm[n] for n in active_names)
    weighted_high = sum(lenses[n]["fv_high"] * norm[n] for n in active_names)

    mos = cfg.get("margin_of_safety", 0.20)
    price = cfg["stock_price"]
    fv_mid_rounded = round(weighted_mid, 2)
    cvm = ((price - fv_mid_rounded) / fv_mid_rounded) if fv_mid_rounded else 0.0

    return {
        "calculated_at": datetime.now(UTC).isoformat(),
        "stock_price": price,
        "scenario_grid": scenario_grid,
        "lenses": lenses,
        "weighted_fv_low":  round(weighted_low, 2),
        "weighted_fv_mid":  fv_mid_rounded,
        "weighted_fv_high": round(weighted_high, 2),
        "current_vs_mid":   round(cvm, 4),
        "buy_price":        round(fv_mid_rounded * (1 - mos), 2),
    }
