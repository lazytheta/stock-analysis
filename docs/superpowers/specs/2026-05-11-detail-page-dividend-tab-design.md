# Detail-page Dividend Tab — Design

**Date:** 2026-05-11
**Author:** Arjan + Claude
**Status:** Approved

## Problem

The Dividend lens (Phase 2-C, 2026-05-08) computes a fair-value range from
two anchors — a Two-stage DDM and a Yield Mean-Reversion calculation — but
has no per-ticker detail page presence. The user can only see the lens
output as a dot in the watchlist row or a bar in the football field tooltip;
they cannot inspect the DDM mechanics, the assumptions used, or explore
how the FV moves with growth-rate and discount-rate variations.

The other lens-style methods on the per-ticker detail page already have
dedicated tabs (`DCF`, `Reverse DCF`, `Peer Comparison`). The Dividend lens
is the odd one out.

## Goal

Add a dedicated `Dividend` tab to the per-ticker detail page that surfaces
the lens output, shows the inputs and assumptions, and provides an
exploratory sensitivity matrix — without changing the underlying lens
computation in `valuation_lenses.compute_dividend_lens`.

## Non-goals

- **No changes to `compute_dividend_lens`.** The lens API is stable; the
  tab just renders what the lens already produces (plus a small helper for
  the matrix).
- **No new MCP tools.** Pure Streamlit UI work. The existing
  `update_valuation_inputs` / `update_lens_weights` tools already cover
  persistent overrides.
- **No persistence from in-tab adjustments.** The "Adjust ranges" expander
  is exploration-only; users persist overrides via MCP tools.
- **No changes to the football field or watchlist row.** Those surfaces
  stay as they are.

## Placement

New 5th tab in `_dcf_editor`'s tab bar:

```
Pre-Scan | DCF | Reverse DCF | Peer Comparison | Dividend | Fundamentals
```

Tab is **always visible** for consistency across tickers — for non-payers
it renders an informative empty state rather than disappearing.

## Tab content — four blocks

### 1. Adjust ranges expander (collapsed by default)

Lets the user explore the sensitivity matrix without persisting changes.
Pattern mirrors the existing Reverse DCF tab's "Adjust ranges" expander.

```
▼ Adjust ranges
  Growth rate (g₁)        min 0%       max 12%       step 1%
  Cost of equity (ke)     min ke-2%    max ke+2%     step 0.5%
```

Default range bounds:
- **Growth (g₁)**: `[0%, 12%]` step `1%` → 13 rows. The cap matches the
  lens's 15% sanity cap from `gather_data.fetch_dividend_history`.
- **Cost of equity (ke)**: `[computed_ke − 2%, computed_ke + 2%]` step `0.5%`
  → 9 columns, centered on the actual ke computed via
  `dcf_calculator.compute_cost_of_equity(cfg)`.

If the user widens the range so it includes `ke ≤ g_term` cells, those
cells render `—` (DDM doesn't converge there).

### 2. Two FV cards (side-by-side)

Same border-left accent style as the existing Reverse DCF "Market implies"
/ "Your base case" cards.

**Left card — DDM Fair Value**
```
DDM FAIR VALUE
$169
5y growth 7.0% · ke 8.5% · terminal 2.5%
```
Value: `details.ddm_fv` from the lens output. Subtitle shows the three
inputs that drive the DDM math.

**Right card — Yield Mean-Reversion**
```
YIELD MEAN-REVERSION
$192
$5.20 TTM / 2.71% historic median yield
```
Value: `details.yield_mr_fv` when available. For tickers with <36 months
of dividend history (where `median_5y_yield is None`), the card content
becomes:
```
YIELD MEAN-REVERSION
Insufficient history
Needs ≥3y of dividend data
```

### 3. Conclusion sentence (centered, muted)

One short sentence comparing the lens midpoint (`(ddm_fv + yield_mr_fv) / 2`
when both available, else `ddm_fv`) to the current stock price. Three
variants based on the relative position:

| Condition | Wording |
|---|---|
| `lens_mid > price × 1.10` | "Lens midpoint $181 is 17% above current $155 — potential undervaluation signal." |
| `lens_mid < price × 0.90` | "Lens midpoint $181 is 11% below current $200 — overvaluation signal." |
| `0.90 × price ≤ lens_mid ≤ 1.10 × price` | "Lens midpoint $181 ≈ current $182 — fairly priced." |

Threshold confirmed at ±10% during brainstorm. Threshold lives as a
constant `_DIVIDEND_FAIR_THRESHOLD = 0.10` so it can be tuned later
without hunting through formatting code.

### 4. Sensitivity matrix

HTML table, dark-mode aware (uses the existing theme dict). Rows = growth
rate (g₁), columns = cost of equity (ke), each cell = DDM-derived FV at
that combination.

Cell formatting:
- Value formatted via the existing `_fmt_fv_dollar` helper (e.g. `$169`,
  `$1,245`)
- Cells where `ke ≤ g_term` render `—` (Gordon doesn't converge)
- The cell at the baseline (rounded g and ke that match the computed
  values) gets a highlighted border + bold weight to anchor the reader

Header rows use the same `T["card"]` background + `T["text_muted"]` color
as the Reverse DCF matrix for visual consistency.

## Edge cases

| Situation | Tab behavior |
|---|---|
| Non-payer (`ttm_dividend = 0` or lens returns None for that reason) | Empty state card: "{TICKER} doesn't pay dividends — Dividend lens not applicable. Use the `update_valuation_inputs` MCP tool to inject a target dividend if you want scenario analysis." No cards, no matrix. |
| Recent dividend initiator (<3y history, `median_5y_yield is None`) | DDM card renders normally; Yield-MR card shows "Insufficient history" empty content; matrix renders normally (DDM doesn't need yield history). |
| `ke ≤ g_term` at baseline (Gordon blow-up) | Warning banner: "Cost of equity ({ke:.2%}) ≤ terminal growth ({g_term:.2%}) — DDM formula doesn't converge for these assumptions. Adjust them in the DCF editor (Risk-Free Rate, ERP) or via Tools → Edit Config." Cards and matrix hidden. |
| No `valuation_summary` stored yet | Banner: "Run Refresh All on the watchlist (or call `calculate_multi_lens_valuation` via the MCP) to compute the lens for this ticker first." Cards/matrix hidden until refresh. |

## Architecture

- **Modify `streamlit_app.py`** — extend `_dcf_editor`'s `st.tabs(...)` call
  to add `"Dividend"` between `"Peer Comparison"` and `"Fundamentals"`.
  Insert the tab body block (~150-200 LOC) wherever fits the existing
  control flow.
- **No changes to `valuation_lenses.py`** — `compute_dividend_lens` already
  returns everything the tab needs in `details`:
  `ttm_dividend`, `growth_rate_stage1`, `terminal_growth`, `cost_of_equity`,
  `stage1_years`, `ddm_fv`, `yield_mr_fv`, `median_5y_yield`.
- **New helper functions** in `streamlit_app.py` (close to the existing
  `_render_football_field` family):
  - `_ddm_at(ttm, g, ke, g_term, stage1_years)` → float — reusable DDM
    valuation given explicit inputs (used by the sensitivity matrix to
    compute each cell without going through `compute_dividend_lens`'s
    skip-logic)
  - `_render_dividend_sensitivity_matrix(ttm, g_range, ke_range, g_term,
    stage1_years, baseline_g, baseline_ke, theme)` → HTML string
  - `_dividend_conclusion(lens_mid, price)` → str (one of the 3 wording
    variants)

The helpers are pure functions — testable in isolation, no Streamlit
imports needed.

## Tests

New file `tests/test_dividend_tab.py` (~10-15 tests):

- `test_ddm_at_matches_compute_dividend_lens_baseline` — `_ddm_at` at the
  baseline g and ke equals `compute_dividend_lens(cfg)["details"]["ddm_fv"]`
- `test_ddm_at_returns_inf_when_ke_le_g_term` — degenerate inputs return
  `float("inf")` (sensitivity matrix renders `—` for those cells)
- `test_dividend_conclusion_undervalued` — lens_mid 1.20× price → undervalued wording
- `test_dividend_conclusion_overvalued` — lens_mid 0.80× price → overvalued wording
- `test_dividend_conclusion_fair` — lens_mid 0.95× price → fairly-priced wording
- `test_dividend_conclusion_threshold_exactly_10pct` — boundary case, both sides
- `test_render_dividend_sensitivity_matrix_dimensions` — HTML has the right
  number of `<tr>` and `<td>` elements
- `test_render_dividend_sensitivity_matrix_baseline_highlighted` — cell
  at (baseline_g, baseline_ke) has the highlight class/style
- `test_render_dividend_sensitivity_matrix_skips_degenerate_cells` — cells
  where `ke ≤ g_term` render as `—`
- `test_render_dividend_sensitivity_matrix_uses_theme` — bg/text colors
  picked from the passed theme dict (not hardcoded)

UI rendering of the tab itself is not tested via Streamlit-runtime
(too complex to mock cleanly). Manual smoke after merge is the validation.

## Components affected

| File | Change |
|------|--------|
| `streamlit_app.py` | Add `Dividend` tab to `_dcf_editor`'s `st.tabs`; add tab body (~150-200 LOC); add 3 new helper functions (`_ddm_at`, `_render_dividend_sensitivity_matrix`, `_dividend_conclusion`) |
| `tests/test_dividend_tab.py` | NEW — ~10-15 tests covering the helpers |

## Migration / rollout

- **No DB migration.** Tab reads from the existing `valuation_summary` blob.
- **No backward compat concerns.** Tickers without a stored lens output
  show the "Run Refresh All" banner; everything else renders.
- **No Cloud Run redeploy needed** — Streamlit-only change. Push to main,
  Streamlit Cloud auto-redeploys.

## Risks

- **`_ddm_at` math drift from `compute_dividend_lens`.** The lens
  implementation has subtle details (growth cap at 15%, NaN guards, skip
  conditions). The sensitivity matrix reproduces the DDM math directly
  but skips the cap so the matrix can show user-explored values up to the
  bound the expander allows. Mitigated by the
  `test_ddm_at_matches_compute_dividend_lens_baseline` test that pins the
  baseline equivalence.
- **Matrix dimensions on small screens.** 13 rows × 9 columns is 117 cells.
  At default density that's ~600px wide. The container will scroll
  horizontally on phones — acceptable; the user mostly views this on
  desktop. No design tweak needed for MVP.
- **HTML escaping in conclusion sentence.** All inputs are numeric so
  no XSS risk. Sentence is rendered via `st.markdown(... unsafe_allow_html=True)`
  for consistent styling — same pattern as the Reverse DCF conclusion.

## Open questions

None remaining. User confirmed:
- Placement: new top-level Dividend tab ✅
- Layout: mirror Reverse DCF (2 cards + conclusion + matrix) ✅
- Matrix axes: growth (g₁) × cost of equity (ke), with adjust-ranges
  expander ✅
- "Fairly priced" threshold: ±10% ✅
