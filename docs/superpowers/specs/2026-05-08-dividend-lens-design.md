# Dividend Lens (Phase 2-C) — Design

**Date:** 2026-05-08
**Author:** Arjan + Claude
**Status:** Approved
**Phase:** 2-C of the multi-lens fair value system (the previously-stubbed Dividend lens)

## Problem

`valuation_lenses.compute_dividend_lens(cfg)` is a stub returning `None` since Phase 1.
The watchlist surfaces 3 active lenses (DCF, Peers, Historical) but no opinion on
fair value from a dividend-discount perspective. For mature dividend payers (PEP,
MSFT, V), this leaves a useful methodology unused.

## Goal

Implement a working `compute_dividend_lens` that produces a meaningful fair-value
range for dividend-paying tickers, skips cleanly for non-payers, and integrates
with the existing UI / MCP / refresh pipeline.

## Non-goals

- No new dedicated detail-page Dividend section. The lens shows up in lens-dots,
  football field, and `summary["lenses"]["dividend"]` for MCP consumption — that's
  enough for now. A separate editor card can be added later if needed.
- No analyst-consensus integration (forward-looking dividend estimates from
  third-party providers). Trailing CAGR + user override via MCP is sufficient.
- No mid-stage transitional growth (H-Model). Two-stage (5y explicit + Gordon
  terminal) is the right complexity tier here.

## Methodology

**Hybrid: Two-stage Dividend Discount Model + Yield Mean-Reversion cross-check.**

### Sub-anchor A — Two-stage DDM (active when lens activates)

```
PV stage 1 = Σ [D₀ × (1+g₁)ⁿ / (1+r)ⁿ] for n = 1..5
Terminal value = D₅ × (1 + g_term) / (r − g_term) / (1+r)⁵
fv_a = PV stage 1 + Terminal value
```

| Symbol | Source |
|--------|--------|
| `D₀` (TTM dividend) | yfinance `Ticker(t).dividends` time-series, sum of all ex-div dates in the last 365 days (handles quarterly, semi-annual, annual schedules uniformly) |
| `g₁` (stage 1 growth) | 5-year CAGR computed from yfinance dividend history; capped at 15% for sanity |
| `g_term` (terminal growth) | `cfg["terminal_growth"]` (reused from DCF, typically 2.0–2.5%) |
| `r` (discount rate) | New helper `dcf_calculator.compute_cost_of_equity(cfg)` returning `ke = risk_free_rate + lev_beta × erp` (extracted from existing `compute_wacc` logic; same inputs, separate concern) |
| Stage 1 length | 5 years (matches the CAGR window; not configurable in this phase) |

### Sub-anchor B — Yield Mean-Reversion (active when ≥3y of dividend history)

```
fv_b = D₀ / median_5y_yield
```

`median_5y_yield` = median of (rolling-TTM dividend / monthly close) across the
available monthly observations, target window 60 months. Field name is `median_5y_yield`
for stable nomenclature even when the actual window is shorter (3–5y).

If fewer than 36 monthly observations are available (e.g., GOOG started paying
in 2024 — only ~12 months of history), this anchor is skipped and the lens
falls back to sub-anchor A only with a ±15% band.

### Range derivation

| Active sub-anchors | fv_low | fv_mid | fv_high |
|---|---|---|---|
| A and B | `min(fv_a, fv_b)` | `mean(fv_a, fv_b)` | `max(fv_a, fv_b)` |
| A only | `fv_a × 0.85` | `fv_a` | `fv_a × 1.15` |

The ±15% band on the A-only path mirrors the DCF lens single-result fallback
(see `compute_dcf_lens` for precedent).

## Skip logic

The lens returns `None` (skipped) under any of the following conditions, with
`details.skipped` listing the reason:

- **TTM dividend = 0** — non-payer (ABNB et al). The lens-dot greys out, the
  football field shows no Dividend bar, and the weighted FV redistributes weight
  to the remaining active lenses.
- **<3 years of dividend history** — can't compute a meaningful CAGR for stage 1,
  and the yield mean-reversion median needs ≥36 monthly observations anyway.
  Captures recent dividend initiators (GOOG started 2024) where pricing the
  lens would be guesswork. Sub-anchor A needs a growth-rate baseline; without
  enough history we don't have one we trust.
- **Cost of equity ≤ terminal growth** — would make the Gordon perpetuity blow
  up or go negative. Cost of equity comes from `compute_wacc(cfg)`; if a config
  has a degenerate beta or sector setup that produces this, skip cleanly.
- **Any input is NaN / non-finite** — defensive guard against pandas NaN bleeding
  through from yfinance.

## Default lens weight

`DEFAULT_LENS_WEIGHTS["dividend"]` stays `0.0` (opt-in per ticker via
`cfg["lens_weights"]`).

Reasoning: dividend discipline varies wildly across companies. A 25% default
weight for an active payer is neither always too low (for high-yield mature
utilities where DDM is the cleanest method) nor always too high (for growth
companies whose dividend is symbolic). Forcing the user to consciously enable
the dividend lens per-ticker matches the methodological judgment they should
already be making.

The lens still computes and stores its result — it just doesn't contribute to
`weighted_fv_mid` until the user sets a non-zero weight in `cfg["lens_weights"]`.

## Data flow

### Auto-fetch helper (new)

`auto_fetch.auto_fill_dividend_inputs(cfg)` writes the following to
`cfg["valuation_inputs"]`:

- `ttm_dividend` (float, USD per share)
- `dividend_5y_cagr` (float, 0.0–0.15)
- `median_5y_yield` (float or None when <5y history)

Respects the `_auto_filled` precedence rule (existing pattern from Phase 2-B):
fields the user has set via MCP override are preserved on subsequent refreshes.
Updates `cfg["valuation_inputs"]["_fetched_at"]` ISO timestamp on every call,
matching `auto_fill_valuation_inputs` and `auto_fill_peer_market_data`.

Called from:
- Streamlit's `_refresh_one` (in `streamlit_app.py:_refresh_stale_valuations`)
- MCP's `_calculate_multi_lens_valuation_impl` and `_refresh_all_valuations_impl`

Same call site as `auto_fill_valuation_inputs` and `auto_fill_peer_market_data`
to keep the refresh pipeline coherent.

### gather_data fetcher (new)

`gather_data.fetch_dividend_history(ticker, n_years=5)` returns:

```python
{
    "ttm_dividend": float,           # sum of last 4 quarterly divs
    "dividend_5y_cagr": float | None,
    "median_5y_yield": float | None, # None when insufficient history
    "n_years_available": int,        # for diagnostics
}
```

Implementation:
- `yf.Ticker(t).dividends` → pandas Series indexed by ex-div date
- TTM = sum of dividend amounts with ex-div date in the trailing 365 days from
  the most recent payment (not "today", to handle stale data correctly)
- 5y CAGR = `(div_TTM / div_5y_ago)^(1/5) − 1`, where `div_5y_ago` = sum of
  dividends in the 365-day window ending 5 years before `div_TTM`'s window.
  Requires both endpoints non-zero; returns `None` otherwise.
- For median yield: pull monthly closes from `Ticker(t).history(period='5y', interval='1mo')`,
  for each month compute `(rolling_TTM_dividend_at_that_month / close)`,
  take the median across the resulting 60 monthly observations. Skip the anchor
  if fewer than 36 observations are available (≥3y of data needed for any
  median to be meaningful; the 5y label is the target, not the floor).

Best-effort: yfinance failures return all-None values; the lens then skips gracefully.

## MCP tool (new, generic)

`update_valuation_inputs(ticker: str, **fields) -> str` (returns JSON of updated
`valuation_inputs`):

- Loads `cfg` via `config_store.load_config`
- Merges `fields` into `cfg["valuation_inputs"]`
- Removes each updated field name from `cfg["valuation_inputs"]["_auto_filled"]`
  (so future auto-refresh respects the user override)
- Saves cfg via `config_store.save_config`
- Returns the updated `valuation_inputs` dict serialized to JSON

Generic by design: works for any valuation_inputs field, not just dividend
overrides. Useful for `forward_eps`, `historical_trailing_pe`, `ttm_ebitda`, etc.
in addition to the dividend lens fields.

The Dividend lens uses three overrideable inputs:
- `ttm_dividend` (rare to override; auto-fetch is reliable)
- `dividend_5y_cagr` (likely override target — user has a forward view)
- `median_5y_yield` (rare; only override if user wants to anchor at a specific
  long-term yield they consider "fair")

## UI integration (`streamlit_app.py`)

- `_render_lens_dots`: extend `order` to `["dcf", "multiples", "historical", "dividend"]`
  → 4 dots max, label scales to "4 lenses"
- `_render_football_field`: extend `lens_order` with `("dividend", "Dividend")`
  → 4 horizontal bars when all active
- `config_store.list_watchlist`: extend `_COUNTED_LENSES` to
  `("dcf", "multiples", "historical", "dividend")`
- `scripts/force_refresh_all.py`: same `_counted` extension for the CLI log

The 3-lens hardcoding from the 2026-05-07 reverse-DCF demotion is the only thing
that needs adjusting; the underlying rendering logic already iterates the order
list dynamically.

## Tests

### `tests/test_multi_lens.py` — new tests for the lens and orchestrator integration

- `test_dividend_lens_skips_non_payer` — `cfg` with `ttm_dividend = 0` → `compute_dividend_lens` returns `None`
- `test_dividend_lens_full_history_both_anchors` — full inputs → fv_low/mid/high spans both anchors
- `test_dividend_lens_no_yield_history_anchor_a_only` — `median_5y_yield = None`, lens active with ±15% band on fv_a
- `test_dividend_lens_blocks_negative_perpetuity` — cost_of_equity ≤ terminal_growth → returns `None` with skipped reason
- `test_dividend_lens_growth_capped_at_15pct` — input CAGR of 25% gets capped to 15% in `details.growth_rate_stage1`
- `test_orchestrator_dividend_active_when_payer` — full dividend-paying cfg → 4 lenses active in summary
- `test_default_lens_weights_dividend_zero` — default weight stays `0.0`

### `tests/test_market_data.py` — auto-fetch tests with yfinance mocked

- `test_auto_fill_dividend_inputs_full` — mock yfinance with 5y of monthly dividends + closes → all 3 fields populated, marked as `_auto_filled`
- `test_auto_fill_dividend_inputs_short_history_under_3y` — only ~24 months of dividends available → `median_5y_yield = None`, `ttm_dividend` and `dividend_5y_cagr` still populated
- `test_auto_fill_dividend_inputs_non_payer` — empty dividends Series → all fields = 0 / None
- `test_auto_fill_dividend_respects_user_override` — field present in cfg but NOT in `_auto_filled` → preserved

### `tests/test_watchlist_ui.py` — UI integration

- Update `test_render_lens_dots_all_active` from 3 → 4 dots, label "4 lenses"
- Update `test_render_lens_dots_empty_dict` from 3 → 4 grey dots
- Update `test_render_football_field_renders_all_active_lenses` to expect 4 ff-bars and a "Dividend" label
- New: `test_render_lens_dots_dividend_skipped` — non-payer cfg → 3 active dots + 1 grey

### `test_mcp_server.py` — MCP tool

- `test_update_valuation_inputs_writes_field` — call sets a field, save_config receives merged cfg
- `test_update_valuation_inputs_removes_from_auto_filled` — overridden field is removed from `_auto_filled` list
- `test_update_valuation_inputs_preserves_other_fields` — only specified fields change

## Components affected

| File | Responsibility | Change |
|------|----------------|--------|
| `valuation_lenses.py` | Lens implementations | Replace `compute_dividend_lens` stub with hybrid DDM + yield mean-reversion |
| `gather_data.py` | Data extraction | Add `fetch_dividend_history(ticker, n_years=5)` |
| `auto_fetch.py` | Shared auto-fetch helpers | Add `auto_fill_dividend_inputs(cfg)` |
| `mcp_server.py` | MCP tools | Add generic `update_valuation_inputs(ticker, **fields)` tool |
| `streamlit_app.py` | Watchlist UI | Re-add `dividend` to lens-dots and football-field order lists |
| `config_store.py` | Watchlist persistence | Re-add `dividend` to `_COUNTED_LENSES` |
| `scripts/force_refresh_all.py` | CLI batch tool | Mirror the `_counted` extension |
| `tests/test_multi_lens.py` | Tests | Lens + orchestrator + default-weight tests |
| `tests/test_market_data.py` | Tests | Auto-fetch tests (yfinance mocked) |
| `tests/test_watchlist_ui.py` | Tests | UI count tests updated |
| `test_mcp_server.py` | Tests | MCP tool tests |

## Migration / Rollout

- **No DB migration.** Existing `valuation_summary` blobs in Supabase remain
  valid; on next "Refresh all" the orchestrator writes the new `dividend` lens
  entry alongside the existing four. UI rendering picks up the 4-dot layout
  immediately from frontend code, regardless of stored data freshness.
- Existing `weighted_fv_mid` values stay the same (since default dividend weight
  is 0.0 — the lens result doesn't enter the weighted average until the user
  enables it via per-config `lens_weights`).
- One feature branch, ~4 commits (lens core + auto-fetch + MCP + UI/tests).

## Risks

- **yfinance dividend data quality:** for some non-US ADRs and small-caps,
  yfinance returns sparse or wrong dividend histories. The lens skips gracefully
  in those cases (returns `None`), so worst case is "lens not active" rather
  than "wrong fair value". Tests include the empty-history case.
- **Cost of equity instability:** if a config has a very high beta or unusual
  sector, cost_of_equity can be 12–15%, making the DDM dominate (long
  discounting, small TV contribution). Cap at 15% on g₁ helps but doesn't fully
  prevent the issue. User can override via `lens_weights` per-ticker.
- **Stage 1 length is hardcoded at 5 years.** No big issue today — for the
  watchlist's mature payers, 5 years is the right horizon. If we later need
  10y for slow-growth utilities, add `stage1_years` as an overrideable field
  via the MCP tool. Forward-compatible: the lens reads
  `cfg["valuation_inputs"].get("stage1_years", 5)`.

## Open questions

None remaining. User confirmed:
- Methodology: Hybrid (Two-stage DDM + Yield Mean-Reversion) ✅
- Skip non-payers (return None) ✅
- Auto-fetch from yfinance ✅
- MCP override capability via generic tool ✅
- Default weight stays 0.0 ✅
- 4-lens UI integration ✅
