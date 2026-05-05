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

    # Scenario grid path — implemented in next task
    raise NotImplementedError("scenario_grid implemented in Task 7")
