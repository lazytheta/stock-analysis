"""Tier 2: stille fouten in de rekenkern.

Elke test hier beschrijft een pad waarop een ontbrekende of onmogelijke
input tot 2026-09-13 een plausibel ogend getal opleverde in plaats van een
uitgeschakelde lens of een foutmelding.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dcf_calculator
import valuation_lenses


def _cfg(**over):
    cfg = {
        "discount_mode": "capm", "risk_free_rate": 0.046, "erp": 0.045,
        "tax_rate": 0.23, "equity_market_value": 10000, "debt_market_value": 0,
        "credit_spread": 0.004, "sector_betas": [("S", 1.0, 1.0)],
        "base_revenue": 1000, "revenue_growth": [0.05] * 5,
        "op_margins": [0.20] * 5, "terminal_growth": 0.025,
        "terminal_margin": 0.20,
        "stc_per_year": [2.0] * 5, "terminal_stc": 2.0,
        "tax_per_year": [0.23] * 5, "terminal_tax": 0.23,
        "cash_bridge": 100, "shares_outstanding": 100, "margin_of_safety": 0.2,
        "stock_price": 50.0,
    }
    cfg.update(over)
    return cfg


# ── #9 shares_outstanding ─────────────────────────────────────────────────

def _multiples_inputs(**over):
    return _cfg(
        valuation_inputs={"ttm_ebitda": 2000.0},
        peers=[{"ticker": "P1", "ev_ebitda": 15.0}, {"ticker": "P2", "ev_ebitda": 17.0},
               {"ticker": "P3", "ev_ebitda": 16.0}],
        **over)


def test_multiples_lens_skips_the_ev_anchor_without_a_share_count():
    """`or 1.0` rekende de totale equity in $M als koers per aandeel:
    ttm_ebitda 2000 × 15 = 30.000 'per aandeel'. De DCF zei 0 voor dezelfde
    ontbrekende input. Drie gedragingen voor één gat, geen enkele schakelde
    de lens uit."""
    cfg = _multiples_inputs()
    del cfg["shares_outstanding"]
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is None or "ev" not in " ".join(
        k for k in lens["details"] if "_fv" in k and "ev" in k)


def test_historical_lens_skips_the_ev_anchor_without_a_share_count():
    cfg = _cfg(valuation_inputs={"historical_ev_ebitda": 15.0, "ttm_ebitda": 2000.0})
    del cfg["shares_outstanding"]
    assert valuation_lenses.compute_historical_lens(cfg) is None


def test_a_share_count_of_zero_counts_as_missing():
    cfg = _cfg(valuation_inputs={"historical_ev_ebitda": 15.0, "ttm_ebitda": 2000.0},
               shares_outstanding=0)
    assert valuation_lenses.compute_historical_lens(cfg) is None


# ── #10 DCF-guards ────────────────────────────────────────────────────────

def test_terminal_growth_at_or_above_the_discount_rate_is_refused():
    """tv = tv_fcff / (wacc - tg) klapt om bij tg >= wacc: intrinsic -105,94
    zonder foutmelding. De dividendlens bewaakt precies dit (ke <= g_term);
    de DCF deed dat niet."""
    with pytest.raises(ValueError, match="terminal_growth"):
        dcf_calculator.compute_intrinsic_value(_cfg(terminal_growth=0.30))


def test_a_sales_to_capital_of_zero_is_refused_not_a_zero_division():
    with pytest.raises(ValueError, match=r"sales_to_capital|stc"):
        dcf_calculator.compute_intrinsic_value(_cfg(stc_per_year=[0.0] * 5))


def test_a_terminal_stc_of_zero_is_refused():
    with pytest.raises(ValueError, match=r"sales_to_capital|stc"):
        dcf_calculator.compute_intrinsic_value(_cfg(terminal_stc=0.0))


def test_a_sane_config_still_computes():
    out = dcf_calculator.compute_intrinsic_value(_cfg())
    assert out["intrinsic_value"] > 0


def test_the_dcf_band_never_comes_out_inverted():
    """Bij een negatieve basiswaarde gaf ×0.85/×1.15 fv_low > fv_high."""
    lens = valuation_lenses.compute_dcf_lens(_cfg(debt_market_value=10_000_000))
    assert lens["fv_low"] <= lens["fv_mid"] <= lens["fv_high"]


# ── #11 gewichten ─────────────────────────────────────────────────────────

def test_all_weights_zero_does_not_produce_a_fair_value_of_zero():
    """`total = sum(raw.values()) or 1.0` maakte van gewicht-som 0 alle
    genormaliseerde gewichten 0, en de watchlist toonde fv_mid = 0.0 als
    geldige waardering."""
    cfg = _cfg(lens_weights={"dcf": 0.0, "multiples": 0.0, "historical": 0.0,
                             "dividend": 0.0, "reverse_dcf": 0.0})
    out = valuation_lenses.calculate_multi_lens_valuation(cfg)
    assert out["weighted_fv_mid"] > 0


# ── #12 één equity-bridge ─────────────────────────────────────────────────

def test_the_lenses_bridge_matches_the_dcf_bridge():
    """De DCF trekt minority en pension af en telt equity_investments op;
    de multiples- en historical-lens deden dat niet. Bij een naam met een
    materieel minderheidsbelang overschatten die lenzen FV/aandeel."""
    cfg = _cfg(minority_interest=500, unfunded_pension=300, equity_investments=200,
               securities=50, debt_market_value=1000, cash_bridge=100)
    assert valuation_lenses._net_debt_bridge(cfg) == pytest.approx(
        1000 - 100 - 50 - 200 + 500 + 300)


def test_the_bridge_treats_missing_items_as_zero():
    assert valuation_lenses._net_debt_bridge({}) == 0.0


# ── #13 stale peer-waarde ─────────────────────────────────────────────────

def test_a_peer_that_lost_its_multiple_does_not_keep_the_old_one():
    """refresh_peer_multiples schreef alleen bij `is not None`, dus een peer
    zonder berekenbare trailing P/E hield zijn oude getal in cfg.peers --
    terwijl de toolbeschrijving belooft dat hij eruit valt."""
    import mcp_server
    peer = {"ticker": "P", "trailing_pe": 22.0, "ev_ebit": 14.0}
    merged = mcp_server._merge_trailing_multiples(peer, {"trailing_pe": None, "ev_ebit": 15.0})
    assert "trailing_pe" not in merged
    assert merged["ev_ebit"] == 15.0


# ── #14 niet-laadbare tickers ─────────────────────────────────────────────

def test_a_ticker_that_fails_to_load_is_reported_not_dropped():
    """_load gaf None voor een ticker zonder config; die viel uit `loaded`
    en kwam in géén van computed/errors/skipped terecht."""
    import mcp_server
    loaded, dropped = mcp_server._load_watchlist_configs(
        ["A", "B", "C"], lambda t: {"ok": t} if t != "B" else None)
    assert set(loaded) == {"A", "C"}
    assert dropped == ["B"]


# ── #15 Retry-After begrensd ──────────────────────────────────────────────

def test_retry_after_is_capped():
    """`Retry-After: 3600` was letterlijk een uur time.sleep in een
    Streamlit-run. tastytrade_api had de cap al; hier ontbrak hij."""
    import gather_data
    assert gather_data.bounded_retry_after("3600", fallback=1.0) <= gather_data.RETRY_AFTER_CAP


def test_retry_after_in_http_date_format_does_not_crash():
    import gather_data
    assert gather_data.bounded_retry_after("Wed, 21 Oct 2026 07:28:00 GMT", fallback=1.5) == 1.5


def test_retry_after_below_the_cap_is_honoured():
    import gather_data
    assert gather_data.bounded_retry_after("2", fallback=1.0) == 2.0
