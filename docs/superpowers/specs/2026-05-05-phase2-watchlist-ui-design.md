# Phase 2-A: Watchlist UI for Multi-Lens Fair Value — Design

**Status:** Draft for review
**Date:** 2026-05-05
**Author:** Arjan + Claude
**Predecessor:** `2026-05-05-multi-lens-fair-value-design.md` (Phase 1)

## Goal

Surface the multi-lens fair value data (`valuation_summary`) that Phase 1 introduced into the Streamlit watchlist UI. End-users currently can't see any of the new computation; this PR closes that gap with a refreshed row layout and a top-level "Refresh all valuations" button.

This is the first sub-project of Phase 2. Auto-fetching market data (B), Dividend Discount lens (C), Sum-of-the-Parts (D), and other Phase-2 items remain in their own future plans.

## Non-Goals

- Auto-fetching `forward_eps`, peer `fwd_pe`, `ttm_ebitda` — separate sub-project (Phase 2-B).
- Dividend Discount lens implementation — separate sub-project (Phase 2-C).
- Editor-page changes — out of scope. The DCF editor (`_dcf_editor`) only changes if necessary to support the watchlist row data; we do not redesign it here.
- Mobile-first redesign. The existing watchlist is desktop-oriented (11 narrow columns); we keep that constraint.
- Showing business `phase` or scorecard `verdict` in the row. Both data points live in `ai_notes["Scorecard"]` and are surfaced in the editor; the user explicitly opted out of putting them in the overview.

## User Story

> "I open lazytheta.io. My watchlist shows fair-value ranges for every ticker (low–mid–high) with a price-position bar so I see at a glance which stocks are cheap, fair, or expensive. Tickers without a multi-lens summary fall back gracefully to the old DCF-only display, and a single 'Refresh all' button computes the missing summaries in parallel with a progress bar."

## Architecture Overview

```
streamlit_app.py
├── _watchlist_overview()
│     ├ NEW: top-bar refresh button (next to Add-to-Watchlist)
│     ├ NEW: status hint ("Last refreshed: X days ago · N of M tickers have summaries")
│     ├ NEW: handler for refresh-button click → parallel orchestrator + progress
│     │       (uses ThreadPoolExecutor, 6 workers, same pattern as _load_all_configs)
│     ├ row builder loop (existing)
│     │   └ uses cfg["valuation_summary"] when present, else falls back to DCF
│     └ render loop (existing per-category card layout)
│
├── _render_wl_header()  ← MODIFIED: column labels updated for new layout
└── _render_wl_row(row)  ← MODIFIED: new "Fair Value" cell with range + bar + lens-dots

valuation_lenses.py       ← UNCHANGED — pure orchestrator already exists
config_store.py           ← UNCHANGED — list_watchlist enrichment already done in Phase 1
```

The new code is contained to `streamlit_app.py`. No new modules, no schema changes. The orchestrator and persistence already exist.

## Row Layout

11 columns, identical width-ratios as today. The `Intrinsic` column is replaced with a richer **Fair Value** cell:

| # | Column | Width | Content |
|---|--------|-------|---------|
| 0 | (edit) | 0.3 | edit-icon button |
| 1 | Ticker | 1.0 | logo + bold ticker |
| 2 | Company | 1.6 | company name + optional notes preview (existing) |
| 3 | Price | 0.8 | live price |
| 4 | **Fair Value** | **1.5** | NEW (see below) |
| 5 | Buy | 0.8 | `valuation_summary.buy_price` (or DCF buy) |
| 6 | Upside | 0.7 | `(fv_mid / price - 1)` — green positive = cheap (unchanged convention) |
| 7 | P/E | 0.6 | existing |
| 8 | FCF Yield | 0.7 | existing |
| 9 | Earnings | 0.7 | existing earnings-date with urgency colors |
| 10 | (delete) | 0.3 | delete-icon button |

Total ratio sum: 9.0 (was 8.3 — Fair Value column widened from 0.8 → 1.5 to fit the range + bar + lens-dots stack). Width is the only column-ratio change. The hero-card layout per category is unchanged.

### The Fair Value cell

When `valuation_summary` is present:

```
$352  ($290–$433)              ← bold mid + muted (low–high) inline
████░░░░░░░░░░░░░░░░░             ← gradient range-bar (green→amber→red)
                  │              ← white price-marker (red-tinted if past high)
●●●  3 lenses                    ← lens-dots (DCF, Multiples, Reverse_DCF)
```

- **Mid line:** `<strong>$mid</strong> <span muted>($low–$high)</span>`. Trailing dollar formatting; integers if value > $100, else 2-decimals.
- **Range-bar:** 6px tall, gradient `linear-gradient(90deg, green→amber→red)`. Width: fills cell minus margins. Marker position: `clamp((price - low) / (high - low), 0, 1) × 100%`. Marker is white (2px wide, 12px tall, slight shadow); becomes red-tinted when `price > high` and clamped to 99% so it stays visible.
- **Lens-dots:** three small 6px circles (DCF, Multiples, Reverse_DCF). Filled green when active (lens ≠ None), grey when skipped. Followed by inline label: "3 lenses" / "DCF + reverse" / etc. Dividend lens always omitted (Phase-2 stub).

When `valuation_summary` is missing (legacy fallback):

```
$95   single-lens               ← bold mid (DCF intrinsic) + muted badge
DCF intrinsic only · run "Refresh all" to compute multi-lens
```

- Single-line `mid` value from existing DCF intrinsic.
- Small "single-lens" badge (background-tint, no color).
- Tiny hint text below pointing to the refresh button.
- No range-bar, no lens-dots.

## Refresh All Valuations Button

Placed in the top action-row of `_watchlist_overview`, after the existing "Add to Watchlist" button:

```
[ Add to Watchlist ]  [ ↻ Refresh all valuations ]
Last refreshed: 3 days ago · 12 of 28 tickers have multi-lens summaries
```

The status line below the buttons summarizes:
- **Last refreshed:** the most recent `valuation_summary.calculated_at` across the user's tickers, formatted as relative time ("just now", "3 days ago", "never"). If no summaries exist yet, shows "never refreshed".
- **Coverage:** "N of M tickers have multi-lens summaries" — count of tickers with `valuation_summary != None`.

### Behavior on click

1. Determine the **stale set**:
   ```
   stale = [ticker for ticker, cfg in cfgs.items()
            if cfg.get("valuation_summary") is None
            or _is_older_than(cfg["valuation_summary"]["calculated_at"], days=7)]
   ```
2. If `stale` is empty: show `st.success("All valuations are fresh (refreshed within last 7 days)")` and return.
3. Otherwise, run a **ThreadPoolExecutor with 6 workers**:
   ```
   with st.progress(0.0) as bar:
       futures = {executor.submit(_refresh_one, ticker): ticker for ticker in stale}
       done = 0
       for future in as_completed(futures):
           done += 1
           bar.progress(done / len(stale), text=f"Computing {done}/{len(stale)}...")
   ```
4. `_refresh_one(ticker)` is wrapped in try/except. Errors are collected and reported after the loop ("3 tickers had errors: TTD, V, FOO — see logs"). One failure does not abort the rest.
5. After completion: `st.cache_data.clear()` to force the watchlist re-render, then `st.rerun()`.

### Force-refresh option

A small secondary link "↳ Force refresh all (ignore freshness)" rendered directly below the main refresh button. Same handler with `stale = list(cfgs.keys())`. Hold-shift-click is fragile across Streamlit reruns, so we use an explicit second control rather than a keyboard modifier.

## Data Flow

```
list_watchlist (Phase 1) → returns enriched dicts with fv_low/mid/high/buy_price/upside-equivalent
   │
   │ (optional: also load full configs in parallel for legacy fallback)
   ↓
_watchlist_overview row builder
   │
   ├── if cfg.has(valuation_summary): use summary fields directly
   │     - fv_low/mid/high → Fair Value cell
   │     - buy_price → Buy column
   │     - upside = fv_mid/price - 1 (consistent convention)
   │     - lens_count + which lenses → lens-dots
   │
   └── else (legacy): use existing _computed_intrinsic / compute_intrinsic_value
         - intrinsic → Fair Value mid (no low/high)
         - DCF buy_price → Buy column
         - upside = intrinsic/price - 1 (existing formula)
         - "single-lens" badge displayed
```

`list_watchlist` already returns the enriched fields (Phase 1, Task 11). The row builder no longer needs to call `compute_intrinsic_value` for tickers with summaries — but **we still need it for legacy fallback**, so the existing code path stays.

## Edge Cases

1. **First page-load after Phase 1 deploy** — every ticker is legacy fallback. User sees existing UX with "single-lens" badges everywhere and a hint about the refresh button. Hitting refresh populates summaries. After that, normal layout.
2. **Mixed state** — some tickers refreshed, some not. Both paths render correctly side-by-side; the lens-dots / single-lens badge tells you which you're looking at.
3. **Refresh failure for a ticker** — caught per-ticker; the rest continue. Row falls back to legacy display until next successful refresh.
4. **Stale-detection clock skew** — if `calculated_at` is in the future (clock skew, edited config), treat as fresh.
5. **Price > fv_high** — range-bar marker clamps to 99%, becomes red-tinted, signaling "above range".
6. **Price < fv_low** — marker clamps to 1%, no special color (the green gradient already signals cheap).
7. **fv_low == fv_high** (degenerate, e.g. only reverse_dcf active) — show single number, no bar. Actually this won't happen in practice because the orchestrator skips reverse-only — but the cell guards against it.
8. **No live price available** — Fair Value cell still renders; range-bar omitted (no marker possible). Upside shows "—".
9. **Missing peer fwd_pe everywhere** — multiples lens silently skipped; lens-dots show "DCF + reverse" with grey middle dot. Normal data flow.
10. **30+ tickers** — refresh-all parallelism (6 workers) → ~5–10s for 30 tickers; progress bar gives feedback.

## Concurrency / Caching

- `_load_all_configs` (existing, cached 30s): unchanged.
- `_fetch_prices_batch` (existing, cached 60s): unchanged.
- `_cached_fundamentals` (existing, cached 24h): unchanged.
- **NEW** during refresh: ThreadPoolExecutor blocking until all done. Progress reported via `st.progress`. After completion: `st.cache_data.clear()` invalidates the row builder caches so fresh summaries flow into the next render.

The orchestrator itself is pure CPU (no network), so 6 parallel workers is safe and fast.

## Dependencies & New Code

No new libraries. Reuses:
- `valuation_lenses.calculate_multi_lens_valuation` (Phase 1)
- `config_store.save_config` (Phase 1, with guarded keys protecting summary)
- `config_store.list_watchlist` (Phase 1, already returns enriched fields)
- `concurrent.futures.ThreadPoolExecutor` (already used in `_load_all_configs`)

New helpers (small, in `streamlit_app.py`):
- `_render_fv_cell(row, cfg)` — builds the multi-line HTML for the Fair Value cell.
- `_render_range_bar(price, low, mid, high)` — generates the inline gradient bar with marker.
- `_render_lens_dots(lenses_dict)` — generates the 3-dot indicator.
- `_refresh_all_handler(cfgs, force=False)` — orchestrates the parallel refresh loop.
- `_format_relative_time(iso_string)` — "3 days ago" formatting (could use `humanize` if available, otherwise inline simple formatter).

All five are private to `streamlit_app.py`. None warrant separate modules.

## Testing

`tests/test_watchlist_ui.py` — separate file from `tests/test_multi_lens.py` so UI helpers and lens math don't get tangled.

Most of the changes are presentation HTML strings, which are hard to test meaningfully without browser-driving. We focus on the **data-shaping helpers** that have testable logic:

1. `test_fv_cell_has_summary` — given a row dict with multi-lens summary, returns the expected mid/range/lens-dots HTML fragments (assert via simple substring matching).
2. `test_fv_cell_legacy_fallback` — given a row dict without summary, returns the "single-lens" fallback fragment.
3. `test_range_bar_marker_position` — given (price, low, high), returns the correct CSS `left: X%` string. Cover: price < low (clamps to 1%), price > high (clamps to 99%, red-tinted), price == mid (~50% but NOT computed from mid — from low/high), edge cases.
4. `test_refresh_handler_filters_stale` — given a dict of cfgs with mixed `calculated_at` timestamps, the handler determines the correct stale set (None, older than 7 days). Mock the orchestrator + save_config so no real I/O.
5. `test_refresh_handler_resilient_to_per_ticker_errors` — one ticker raises during compute; the rest succeed; returned error-list contains exactly that one ticker.
6. `test_format_relative_time` — boundary cases: "just now" / "3 days ago" / "never" / future timestamp.

No browser/Selenium testing for v1. Visual review happens in PR.

## Migration & Backward Compatibility

- All changes are render-time. No DB schema changes.
- Existing tickers without `valuation_summary` keep working unchanged (legacy fallback path).
- The "Refresh all" button is purely additive.
- Existing pages outside the watchlist overview (DCF editor, prescan sections) are untouched.
- The Streamlit Cloud deploy is non-disruptive: refresh the browser, see the new layout. Tickers that already exist render in legacy mode until refreshed.

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Refresh-all takes >30s and Streamlit Cloud times out the session | 6-worker parallelism keeps total time ~5-15s for 30 tickers; user sees progress |
| `calculate_multi_lens_valuation` raises on a malformed legacy cfg | Per-ticker try/except; failures listed but don't abort the batch |
| `valuation_summary.calculated_at` parsing breaks on TZ-naive strings | Use `dateutil.parser` (or `datetime.fromisoformat` with offset handling) and treat parse failures as "stale" |
| Range-bar HTML broken on dark/light theme edge cases | Use theme tokens (`T["text"]`, `T["text_muted"]`, `T["accent"]`) for the marker and lens-dots; gradient colors hardcoded but semi-transparent so they work on both backgrounds |
| Streamlit re-renders during refresh kill the ThreadPool | Refresh runs synchronously inside the button-click handler; progress bar updates via `as_completed` are safe within a single Streamlit run |

## Acceptance Criteria

1. Watchlist overview renders the new Fair Value cell with range-bar + lens-dots for all tickers that have `valuation_summary`.
2. Tickers without `valuation_summary` render the legacy single-lens fallback with the badge + hint.
3. The "Refresh all valuations" button computes summaries for stale tickers in parallel, shows progress, and updates the page on completion.
4. A status line above the table shows `Last refreshed: <relative>` and `<N> of <M> tickers have summaries`.
5. The `Upside` column uses `(fv_mid / price - 1)` when summary is present, else the existing formula.
6. All 6 unit tests pass; full pytest suite stays green (no regressions).
7. `python3 -m ruff check .` clean.
8. Local Streamlit dev-server `streamlit run streamlit_app.py` renders the new layout without errors.

## Implementation Order

1. Helper functions (`_render_fv_cell`, `_render_range_bar`, `_render_lens_dots`, `_format_relative_time`).
2. Modify `_render_wl_header` and `_render_wl_row` to use the new cell.
3. Adjust the row builder to populate fv_low/mid/high/buy_price from summary when present.
4. Add the "Refresh all valuations" button + handler with parallel orchestrator + progress bar.
5. Add the status line ("Last refreshed: X · N of M").
6. Tests for the data-shaping helpers and refresh handler.
7. Local visual review on dev server.
8. Lint + regression suite.
