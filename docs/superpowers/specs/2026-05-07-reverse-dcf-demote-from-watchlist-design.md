# Demote Reverse DCF from Watchlist Lens — Design

**Date:** 2026-05-07
**Author:** Arjan + Claude
**Status:** Approved

## Problem

Reverse DCF is currently treated as the 4th lens in the multi-lens fair value
system, with weight `0.10` in `DEFAULT_LENS_WEIGHTS`. This is methodologically
flawed:

- `compute_reverse_dcf_lens` returns `fv_low = fv_mid = fv_high = stock_price`
  by construction. The lens has no opinion on fair value — it is the current
  market price by definition.
- Including it in the weighted fair value pulls `weighted_fv_mid` 10% toward
  the current price, biasing the FV estimate toward "what the market already
  pays." That is circular: a fair value estimate that is partly anchored to
  the market price tells the user nothing new about whether the market is
  right.
- In the football field UI, the Reverse DCF "bar" is a single point that
  overlaps exactly with the Price marker — visually redundant and misleading
  (it suggests a 4th valuation methodology that happens to land on the price,
  rather than a definitional anchor).

The actual value of Reverse DCF is the implied growth and implied margin
metadata — *what assumptions does the market need to believe to justify the
current price?* — not a fair-value range.

## Goal

Remove Reverse DCF from the watchlist row UI and from the weighted FV
calculation, while keeping it intact on the per-ticker detail page and
keeping its lens entry available internally for future MCP / API use.

## Non-goals

- No changes to `compute_reverse_dcf` in `dcf_calculator.py` — that powers
  the dedicated Reverse DCF section on the detail page (line 4733+ of
  `streamlit_app.py`) and is unaffected.
- No changes to the detail page rendering of Reverse DCF.
- No data migration of existing Supabase `valuation_summary` rows — the
  next `Refresh all` recomputes everything; until then, stored summaries
  remain valid (lens entry still computed; UI just doesn't surface it).

## Design

### 1. `valuation_lenses.py` — weight to zero, lens stays computed

Change `DEFAULT_LENS_WEIGHTS`:

```python
DEFAULT_LENS_WEIGHTS = {
    "dcf": 0.30,
    "multiples": 0.30,
    "historical": 0.30,
    "reverse_dcf": 0.0,     # was 0.10 — anchors at current price, not a true valuation
    "dividend": 0.0,
}
```

The orchestrator `calculate_multi_lens_valuation` still calls
`compute_reverse_dcf_lens(cfg)` and stores the result in
`summary["lenses"]["reverse_dcf"]`. With weight 0.0 it contributes nothing
to `weighted_fv_low/mid/high`, but `details.implied_growth` and
`details.implied_margin` remain accessible via the summary dict for MCP
tools and future features.

The other three lenses renormalize naturally to ~0.333 each in the
weighted-average computation.

### 2. `streamlit_app.py` — watchlist row hides Reverse DCF

Two functions change. Both already iterate over an explicit `order` /
`lens_order` list of lens keys; we drop `"reverse_dcf"` from those lists.

**`_render_lens_dots`** (line 113):

```python
order = ["dcf", "multiples", "historical"]   # was: + "reverse_dcf"
```

Renders 3 dots max, label caps at "3 lenses".

**`_render_football_field`** (line 222):

```python
lens_order = [
    ("dcf", "DCF"),
    ("multiples", "Multiples"),
    ("historical", "Historical"),
    # "reverse_dcf" omitted — bar would overlap with Price marker
]
```

3 horizontal bars only. Price marker stays where it is.

### 3. `config_store.py:list_watchlist` — lens_count caps at 3

The current count includes any non-None lens, so reverse_dcf inflates it
to 4. Restrict to the three forward-looking lenses:

```python
_COUNTED_LENSES = ("dcf", "multiples", "historical")
lens_count = sum(1 for k in _COUNTED_LENSES if lenses.get(k) is not None)
```

This keeps `lens_count` aligned with what the watchlist row visualises.

### 4. Detail page — no changes

`streamlit_app.py:4733+` calls `dcf_calculator.compute_reverse_dcf`
directly with its own widget controls (growth range, margin range, WACC).
It does not consume `summary["lenses"]["reverse_dcf"]`. The "Market
implies" card, "Your base case" card, conclusion sentence, and
sensitivity matrix all stay exactly as they are.

### 5. Tests

**`tests/test_multi_lens.py`** — update assertions:

- `DEFAULT_LENS_WEIGHTS["reverse_dcf"] == 0.0` (was `0.10`)
- Any test that asserts `weighted_fv_mid` should not be biased toward
  `stock_price` by reverse DCF anymore.

**`tests/test_watchlist_ui.py`** — update assertions:

- Lens-dots renderer produces at most 3 dots, never 4
- Lens-dots label caps at "3 lenses"
- Football field renders at most 3 lens bars
- `list_watchlist` `lens_count` caps at 3

Run-all gate: `python3 -m pytest -v` must pass; in particular the test
suites listed in `CLAUDE.md` (`test_tastytrade_api.py test_ibkr_api.py`)
plus the multi-lens and watchlist UI test files.

## Components affected

| File | Change |
|------|--------|
| `valuation_lenses.py` | `DEFAULT_LENS_WEIGHTS["reverse_dcf"]` → 0.0 |
| `streamlit_app.py` | drop `reverse_dcf` from `_render_lens_dots` order |
| `streamlit_app.py` | drop `reverse_dcf` from `_render_football_field` lens_order |
| `config_store.py` | `list_watchlist` `lens_count` restricted to 3 forward lenses |
| `tests/test_multi_lens.py` | weight assertions updated |
| `tests/test_watchlist_ui.py` | lens-dots / football-field assertions updated |

## Migration / Rollout

- No DB migration. `valuation_summary` blobs in Supabase remain valid:
  the orchestrator still writes a `reverse_dcf` lens entry; the UI just
  doesn't render it.
- Existing `weighted_fv_mid` values in stored summaries will shift on the
  next `Refresh all` (~5-10% movement, since the 10% reverse-DCF anchor
  drops out and the other three lenses renormalise). This is the desired
  behavioural change, not a regression.
- One feature branch, 1-2 commits.

## Risks

- **None significant.** The change is purely additive (weight goes from
  0.10 to 0.0) and the lens data stays in the summary dict, so nothing
  downstream breaks.
- A user looking at a watchlist row before clicking "Refresh all" sees
  the new lens-dot count and football field immediately (UI rendering
  changes regardless of stored data), but the `weighted_fv_mid` value
  in the row keeps the old (slightly biased) figure until refresh. This
  is acceptable: the FV figure is meant to be refreshable, and stale
  data is a normal state.

## Open questions

None remaining. User confirmed:
- Keep computing `compute_reverse_dcf_lens` ✅
- Keep visible on detail page ✅
- Remove from watchlist row (lens-dots + football field) ✅
- Remove from weighted FV (weight 0.0) ✅
