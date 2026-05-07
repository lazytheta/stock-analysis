# Phase 2-D: Split Multiples Lens + Football Field UI — Design

**Status:** Draft for review
**Date:** 2026-05-07
**Author:** Arjan + Claude
**Predecessors:**
- `2026-05-05-phase2-watchlist-ui-design.md` (Phase 2-A)
- `2026-05-05-phase2b-auto-fetch-market-data-design.md` (Phase 2-B)
- `2026-05-07-phase2b2-historical-multiples-design.md` (Phase 2-B.2)
- `2026-05-07-mcp-refresh-tool-design.md` (MCP refresh tool)

## Goal

Two changes that resolve the methodological conflation in the current Multiples lens:

1. **Split the Multiples lens into two:** `multiples` (peer-relative only — sub-anchors B + C) and `historical` (own-history only — sub-anchors A.2 + D, plus manual A). This separates two distinct valuation epistemologies (cross-sectional vs time-series) that were being averaged together, hiding the source of disagreement when the range got wide.

2. **Add a "football field" popover UI:** click a 📊 icon next to the watchlist row's range-bar to see horizontal bars per methodology (DCF, Multiples, Historical, Reverse DCF), each with its own range, plus current-price and weighted-mid markers. Mirrors how investment banks present valuation. Lets the user see WHICH methodology drives a wide weighted range.

The split is a structural fix; the football field is the visual payoff that makes the split worthwhile to the end user.

## Non-Goals

- Per-sub-anchor weights inside any lens (kept the orchestrator's lens-level weighting model).
- Mobile-optimized football field. Desktop-first.
- Picking a "primary methodology per ticker" automatically. The user can still per-ticker override `cfg["lens_weights"]` to weight one methodology more, but the engine picks no primary.
- Historical-multiples-only mode (drop peers entirely). User explicitly chose to keep both signals separately rather than scrap one.
- Auto-migration of saved `valuation_summary` blobs. Force-refresh post-deploy regenerates them.
- Animations, transitions, dark/light mode tweaks beyond what theme tokens already provide.

## Architecture

```
valuation_lenses.py                ← MODIFIED
  ├ compute_multiples_lens(cfg)    ← STRIPPED to peer-only (B + C)
  ├ NEW: compute_historical_lens(cfg)  ← own-history only (A + A.2 + D)
  ├ DEFAULT_LENS_WEIGHTS            ← updated to 5 keys
  └ calculate_multi_lens_valuation  ← extra lens in the loop

streamlit_app.py                   ← MODIFIED
  ├ _render_lens_dots(...)         ← refactor to "N lenses" generic label
  ├ NEW: _render_football_field(summary, theme)
  └ Watchlist row builder          ← add popover trigger button

config_store.list_watchlist        ← UNCHANGED (lens_count formula already correct)
auto_fetch.py                      ← UNCHANGED (still feeds both lenses via valuation_inputs)
```

The split is purely in `valuation_lenses.py`. Auto-fetch keeps populating the same `valuation_inputs` fields (`historical_trailing_pe`, `historical_ev_ebitda`, `ttm_eps`, etc.); the new lens just reads them from a different consumer.

## Data Model

### Existing `valuation_summary.lenses` after Phase 2-B.2

```python
"lenses": {
    "dcf":         {fv_low/mid/high, weight, weight_normalized, details},
    "multiples":   {... details with own + peer sub-anchors mixed},
    "reverse_dcf": {...},
    "dividend":    None,
}
```

### New `valuation_summary.lenses` after Phase 2-D

```python
"lenses": {
    "dcf":         {fv_low/mid/high, weight, weight_normalized, details},
    "multiples":   {... details with PEER sub-anchors only},
    "historical":  {fv_low/mid/high, weight, weight_normalized, details},  # NEW
    "reverse_dcf": {...},
    "dividend":    None,
}
```

`details` schema per lens:

- `multiples.details`: `{fwd_pe_peer_median, ev_ebitda_peer_median, closest_peer, peer_fwd_pe_outliers_removed, peer_ev_ebitda_outliers_removed, skipped}`. Drops the old `fwd_pe_own`, `historical_trailing_pe_fv`, `historical_ev_ebitda_fv` keys (those move to `historical.details`).
- `historical.details`: `{fwd_pe_own, historical_trailing_pe_fv, historical_ev_ebitda_fv, skipped}`.

### Default lens weights

```python
DEFAULT_LENS_WEIGHTS = {
    "dcf":         0.30,
    "multiples":   0.30,
    "historical":  0.30,
    "reverse_dcf": 0.10,
    "dividend":    0.00,
}
```

Sum = 1.00. With dividend always None today, active sum = 1.00 → no renormalization triggers in the typical case. Per-ticker `cfg["lens_weights"]` override remains supported.

## `compute_historical_lens` — algorithm

```python
def compute_historical_lens(cfg) -> dict | None:
    """Time-series 'own history' lens. Three sub-anchors:
       A   own historical forward P/E × forward_eps        (manual-only)
       A.2 own historical trailing P/E × ttm_eps           (auto-fetched)
       D   own historical EV/EBITDA × ttm_ebitda - net_debt (auto-fetched)

    Returns None if all three skip. Otherwise lens-level fv_low/mid/high
    is min/mean/max of the (1-3) collected anchors.
    """
```

Sub-anchor logic mirrors the existing Phase 2-B.2 implementation, just relocated. `details` key is `historical_trailing_pe_fv` etc., same names — easier to keep then change naming AGAIN. Skips with INFO logs identical to the existing pattern.

## `compute_multiples_lens` — what gets removed

Keep:
- B: peer fwd P/E with Tukey filter → 3 anchors (low/median/high × forward_eps)
- C: peer EV/EBITDA with Tukey filter → 3 anchors

Remove:
- A: own forward P/E (moves to historical lens)
- A.2: own trailing P/E (moves)
- D: own EV/EBITDA (moves)
- `details["fwd_pe_own"]`, `details["historical_trailing_pe_fv"]`, `details["historical_ev_ebitda_fv"]` — gone from this lens

Returns None when both peer sub-anchors skip (no peers with multiples or no required input).

## Football field UI

### Trigger

Small button in the watchlist row, placed AFTER the range-bar in the Fair Value cell:

```html
<button class="ff-trigger" title="Open valuation breakdown">📊</button>
```

Uses `st.popover` (Streamlit ≥1.32 — verified 1.54 supports it). Streamlit's `st.popover` integrates as an inline widget that renders only when clicked.

### Content layout

A 600px-wide HTML block:

```
                          $401  ← current price (white dashed line)
                            |
                            v
DCF              ████░░░░░░░░░░░    $290 — $340
Multiples              ████████░░    $200 — $450
Historical                 ██████░   $380 — $480
Reverse DCF                  █       $401 (single anchor)
                            |  |
                            v  v
                          Mid Buy
                          $380 $304
```

Each bar:
- 100% width within a 600px container, height ~16px
- Background: `linear-gradient(90deg, #6cc07055, #d8a44855, #d96a5a55)` (cheap → fair → expensive)
- Inner `div` positioned absolute; `left: X%`, `width: Y%` showing the range
- Right-side label: `${low} — ${high}` (or `(skipped)` if lens is None)
- Skipped lenses: bar greyed out, no inner range div

Markers (vertical lines spanning all bars):
- Current price: white, 2px wide, `box-shadow` for visibility
- Weighted mid: sage-green (`T['accent']`), 2px wide, label "Mid" below
- Buy price: darker sage (`T['accent_hover']`), 2px wide, label "Buy" below

X-axis range: `[global_min, global_max]` where global_min/max span all lens fv_low / fv_high values plus current price (so price marker is always visible). Padding: 5% on each side.

Below the football field, a small caption: `last refreshed N days ago · 4 of 4 methodologies active`.

### Lens-dots refactor

Current `_render_lens_dots` has if-elif soup with explicit names per combination ("DCF only", "DCF + reverse", etc.). With 4 lenses the combinations explode (15 possible).

Refactor:
- Render N dots in fixed order (dcf, multiples, historical, reverse_dcf)
- Active lenses: green dot. Skipped: grey dot. Dividend stub omitted from display entirely (not rendered as a 5th dot).
- Label: simple `"{count} lenses"` for any count ≥ 1; `"no lenses"` if zero.

Drops the cleverness; simpler and scales.

## Migration

After deploy:

1. Existing `valuation_summary` rows in Supabase have the OLD shape (`multiples.details` with own-history sub-anchors mixed in, no `historical` key in `lenses`).
2. The watchlist UI continues to work — the new `_render_lens_dots` reads `lenses` dict robustly (missing keys = not active). Football field renders existing keys; the missing `historical` lens shows as "(refresh needed)" placeholder.
3. User runs `refresh_all_valuations(force=True)` once via Claude Desktop OR clicks Streamlit's "↻ Refresh all" twice (first click skips fresh ones, but the new code creates the new shape; subsequent visits use new data).
4. Force-refresh regenerates all summaries with the new shape.

This is the same migration pattern as previous phases. No code-level migration logic needed.

## Errors, skips, logging

- `compute_historical_lens` follows the same skip/log pattern as `compute_multiples_lens`: each sub-anchor skip appends to `details["skipped"]` and INFO-logs.
- Football field renders robustly when a lens is None: shows greyed-out bar, no inner range, label "(skipped)".
- Lens-dot count uses non-None membership (existing logic from Phase 2-A).
- New module-level logger lines reuse the existing `logger = logging.getLogger(__name__)` already in `valuation_lenses.py`.

## Tests

`tests/test_multi_lens.py` — additions and updates:

**New (~5 tests):**

1. `test_historical_lens_uses_all_three_subanchors_when_present` — config with manual `historical_fwd_pe`, `forward_eps`, `historical_trailing_pe`, `ttm_eps`, `historical_ev_ebitda`, `ttm_ebitda` → lens returns non-None with all three details populated and 3 anchors merged.
2. `test_historical_lens_returns_none_when_all_subanchors_skip` — empty `valuation_inputs` → returns None.
3. `test_multiples_lens_no_longer_uses_historical_keys` — config with all the historical inputs but no peers → multiples lens returns None (no peer anchors).
4. `test_orchestrator_includes_historical_lens` — full config → `summary["lenses"]["historical"]` is non-None.
5. `test_default_lens_weights_split` — assert `DEFAULT_LENS_WEIGHTS == {dcf: 0.30, multiples: 0.30, historical: 0.30, reverse_dcf: 0.10, dividend: 0.0}`.

**Updates (existing tests that asserted the OLD multiples-merged structure):**

- `test_multiples_lens_uses_historical_trailing_pe` (Phase 2-B.2) → MOVE to `test_historical_lens_uses_historical_trailing_pe` (rename + change function under test).
- `test_multiples_lens_uses_historical_ev_ebitda` (Phase 2-B.2) → MOVE to `test_historical_lens_uses_historical_ev_ebitda`.
- `test_all_lenses_active_weighted_in_range` (Phase 1) → adjust to expect 4 active lenses (was 3), `mid` between min/max of 4 lens mids.
- Any other test that asserts `details["historical_trailing_pe_fv"]` or similar — move the assertions to read from `summary["lenses"]["historical"]["details"]` instead of `summary["lenses"]["multiples"]["details"]`.

`tests/test_watchlist_ui.py` — additions:

6. `test_render_football_field_renders_all_active_lenses` — full summary → HTML contains 4 bar elements, current-price marker, mid marker, buy marker.
7. `test_render_football_field_handles_missing_lens` — summary with `historical: None` → bar greyed out / "(skipped)" label.
8. `test_render_lens_dots_4_lenses` — 4 active → "4 lenses" label, 4 ld-on dots.

Total tests after this PR: 178 → ~186.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Existing summaries in UI break during deploy-window | UI gracefully handles missing `historical` key (renders "(refresh needed)") — no crash. Force-refresh resolves. |
| Default weight sum != 1.0 | Verified 0.30+0.30+0.30+0.10+0.00 = 1.00. Test asserts this. |
| `_render_lens_dots` refactor changes existing test outputs | Update existing tests inline (3-4 tests, mostly assertion strings). |
| Football field popover doesn't render on Streamlit Cloud | `st.popover` is in 1.32+, Cloud runs 1.54 — verified working. |
| Per-ticker lens_weights override breaks because of new `historical` key | Existing override logic uses `weights_cfg.get(name, DEFAULT_LENS_WEIGHTS.get(name, 0.0))` per active lens — graceful for missing entries. New `historical` falls back to default 0.30 if user's override only has the old keys. |
| Tukey filter behavior unchanged but applied only to peer-multiples now | Yes — that's the intent. Own-history values aren't multi-peer aggregations, so no Tukey applies. |

## Acceptance criteria

1. `compute_historical_lens(cfg)` returns a non-None dict when at least one of A / A.2 / D has usable inputs; sub-anchor skip logic mirrors existing patterns.
2. `compute_multiples_lens(cfg)` no longer references `historical_*` keys in any sub-anchor or `details`.
3. `DEFAULT_LENS_WEIGHTS` has 5 keys totaling 1.00 with the values from §"Default lens weights".
4. `valuation_summary.lenses` has 5 keys after orchestration: dcf, multiples, historical, reverse_dcf, dividend.
5. Watchlist row's lens-dots show 4 dots (dividend stub omitted) with labels "{N} lenses" or "no lenses".
6. Click 📊 in a watchlist row → popover opens with horizontal football-field bars (one per non-None lens), current-price marker, mid marker, buy marker.
7. Force-refresh on the 21-ticker watchlist regenerates all summaries with the new shape; all tickers retain their existing peer/historical data.
8. Test counts: full pytest suite → ~186 passing.
9. `python3 -m ruff check .` clean for new code.

## Implementation order

1. Add `compute_historical_lens` (new) + ~3 tests for it
2. Strip own-history sub-anchors from `compute_multiples_lens` + update tests that asserted those details
3. Update `DEFAULT_LENS_WEIGHTS` + orchestrator + tests
4. Refactor `_render_lens_dots` to generic "{N} lenses" label + update test_render_lens_dots tests
5. Add `_render_football_field` helper + 2 tests
6. Wire popover trigger button into watchlist row builder
7. Force-refresh on production data + visual smoke check
8. Lint + full regression
