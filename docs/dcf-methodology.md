# DCF Methodology & Config Reference

The watchlist DCF engine is `dcf_calculator.py` (`compute_intrinsic_value`), fed by
configs in `watchlist_configs` (Supabase) / `configs/*.py` examples. The Excel-export
template (`dcf_template.py`) and its skill were removed 2026-06-17 — the app values
in-engine only.

## Core conventions
- **Reinvestment:** Sales-to-Capital (reinvestment = ΔRevenue / sales_to_capital).
- **Discounting:** mid-year convention (period = 0.5 + i).
- **WACC:** weighted sector betas (Damodaran), ERP + risk-free + credit spread (`compute_wacc`).
- **Terminal value:** Gordon growth on terminal FCFF; `terminal_*` overrides.
- **Margin of safety:** `buy_price = intrinsic × (1 − margin_of_safety)` (default 20%).
- **Per share:** equity / current `shares_outstanding` (no future share projection).

## SBC treatment (Option 2 — decided 2026-06-17)
- `op_margins` are **GAAP** (SBC already expensed in operating income), so SBC is
  counted **once** via NOPAT. The engine does **not** subtract a separate SBC line
  from FCFF (that double-counted it and understated value — ≈18% for MSFT at 4.4%
  SBC, far more for high-SBC names).
- `sbc_pct` / `sbc_per_year` are retained in configs for **display only**
  (SBC-adjusted margin, Rule of 40) and must not move the valuation
  (`tests/test_dcf_sbc.py` locks this).
- This is the Damodaran/GAAP convention: SBC is a real expense, kept in EBIT;
  dilution is captured through the lower GAAP NOPAT.
- **Pre-SBC margins must not be used.** Convert any to GAAP value-preservingly:
  `margin_gaap = margin_presbc − sbc%` (applied across all projection years +
  terminal). Only NET ever used pre-SBC; it was converted on 2026-06-17.

## Config field reference
See `configs/msft_config.py` for the full field layout (revenue_growth, op_margins,
sales_to_capital, tax_rate, terminal_*, cash_bridge, debt_market_value,
shares_outstanding, peers, valuation_inputs, etc.).

## Multi-lens valuation
The watchlist fair value blends DCF + multiples + historical + dividend lenses; see
`docs/superpowers/specs/2026-05-05-multi-lens-fair-value-design.md` and
`valuation_lenses.py`.
