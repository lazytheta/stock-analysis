"""Multi-lens fair value engine (Phase 1).

Pure functions: take a config dict, return a lens-result dict (or None).
No Supabase, no network, no streamlit imports — fully testable.
"""

import logging
import statistics  # noqa: F401 — used by Task 9 multiples lens
from datetime import datetime, timezone  # noqa: F401 — used by Task 10 orchestrator

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
