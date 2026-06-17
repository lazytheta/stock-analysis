"""Lock the SBC convention (Option 2, 2026-06-17): op_margins are GAAP (SBC
already expensed in operating income), so the DCF must NOT subtract SBC again.
sbc_pct / terminal_sbc are kept in configs for display only and must not move
the valuation."""
from dcf_calculator import compute_intrinsic_value

_BASE = {
    "base_revenue": 1000.0,
    "revenue_growth": [0.10] * 5,
    "op_margins": [0.20] * 5,
    "terminal_growth": 0.03,
    "tax_rate": 0.21,
    "sales_to_capital": 2.0,
    "cash_bridge": 0.0,
    "debt_market_value": 0.0,
    "shares_outstanding": 100.0,
}


def test_sbc_does_not_affect_valuation():
    no_sbc = compute_intrinsic_value({**_BASE, "sbc_pct": 0.0, "terminal_sbc": 0.0},
                                     wacc=0.10)["intrinsic_value"]
    high_sbc = compute_intrinsic_value({**_BASE, "sbc_pct": 0.15, "terminal_sbc": 0.15,
                                        "sbc_per_year": [0.15] * 5}, wacc=0.10)["intrinsic_value"]
    assert no_sbc == high_sbc          # SBC is counted once in GAAP margins, not again
    assert no_sbc > 0


def test_gaap_vs_presbc_equivalence():
    """A GAAP config and the mathematically-equivalent pre-SBC config (margin +
    SBC, with the old separate deduction) now yield the same value — i.e. the
    conversion margin_gaap = margin_presbc - sbc is value-preserving."""
    gaap = compute_intrinsic_value({**_BASE, "op_margins": [0.20] * 5}, wacc=0.10)["intrinsic_value"]
    # pre-SBC margin 0.25 with sbc 0.05 == GAAP 0.20; engine ignores sbc now,
    # so the pre-SBC config must be entered as GAAP 0.20 to match.
    presbc_converted = compute_intrinsic_value(
        {**_BASE, "op_margins": [0.25 - 0.05] * 5, "sbc_pct": 0.05}, wacc=0.10)["intrinsic_value"]
    assert gaap == presbc_converted
