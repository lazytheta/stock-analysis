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
    "dcf": 0.30,
    "multiples": 0.40,
    "reverse_dcf": 0.10,
    "dividend": 0.00,
}


def compute_dividend_lens(cfg):
    """Phase 2 placeholder.

    TODO Phase 2: Gordon Growth + yield mean-reversion using
    valuation_inputs.target_dividend_yield, current_dividend,
    expected_dividend_growth.
    """
    return None


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


def compute_multiples_lens(cfg):
    """Trading-multiples lens with three independent sub-anchors:

    A) own historical forward P/E x forward_eps
    B) peer-set forward P/E (median, min, max) x forward_eps
    C) peer-set EV/EBITDA (median, min, max) x ttm_ebitda - net_debt -> /shares

    Sub-anchors silently skipped when their inputs are missing. Lens fully
    returns None when all three skip.
    """
    inputs = cfg.get("valuation_inputs") or {}
    peers = cfg.get("peers") or []

    fv_anchors = []
    details = {
        "fwd_pe_own": None,
        "fwd_pe_peer_median": None,
        "ev_ebitda_peer_median": None,
        "historical_trailing_pe_fv": None,    # NEW (Phase 2-B.2)
        "historical_ev_ebitda_fv": None,      # NEW (Phase 2-B.2)
        "closest_peer": None,
        "skipped": [],
    }

    forward_eps = inputs.get("forward_eps")
    historical_fwd_pe = inputs.get("historical_fwd_pe")
    ttm_ebitda = inputs.get("ttm_ebitda")
    historical_trailing_pe = inputs.get("historical_trailing_pe")    # NEW (Phase 2-B.2)
    ttm_eps = inputs.get("ttm_eps")                                  # NEW (Phase 2-B.2)

    # A) own historical forward P/E
    if forward_eps and historical_fwd_pe:
        own_fv = historical_fwd_pe * forward_eps
        fv_anchors.append(own_fv)
        details["fwd_pe_own"] = own_fv
    else:
        reason = "fwd_pe_own (forward_eps or historical_fwd_pe missing)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)

    # A.2) own historical trailing P/E × ttm_eps (Phase 2-B.2)
    if historical_trailing_pe and ttm_eps and ttm_eps > 0:
        own_trailing_fv = historical_trailing_pe * ttm_eps
        fv_anchors.append(own_trailing_fv)
        details["historical_trailing_pe_fv"] = own_trailing_fv
    else:
        reason = "historical_trailing_pe (no historical_trailing_pe or ttm_eps)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)

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
        # informational closest peer
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
        logger.info("Multiples lens fully skipped (no anchors)")
        return None

    return {
        "fv_low": min(fv_anchors),
        "fv_mid": sum(fv_anchors) / len(fv_anchors),
        "fv_high": max(fv_anchors),
        "details": details,
    }


def calculate_multi_lens_valuation(cfg, scenario_grid=False):
    """Run all lenses and return the valuation_summary dict.

    Pure function — does not mutate cfg, does not persist anywhere. Caller
    is responsible for storing the summary back to the config.
    """
    lenses = {
        "dcf":         compute_dcf_lens(cfg, scenario_grid=scenario_grid),
        "multiples":   compute_multiples_lens(cfg),
        "reverse_dcf": compute_reverse_dcf_lens(cfg),
        "dividend":    compute_dividend_lens(cfg),
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
