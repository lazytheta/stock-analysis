# MCP Refresh Tool — Design

**Status:** Draft for review
**Date:** 2026-05-07
**Author:** Arjan + Claude
**Predecessors:**
- `2026-05-05-phase2-watchlist-ui-design.md` (Phase 2-A)
- `2026-05-05-phase2b-auto-fetch-market-data-design.md` (Phase 2-B)
- `2026-05-07-phase2b2-historical-multiples-design.md` (Phase 2-B.2)

## Goal

Two changes that close a structural gap exposed by the NFLX peer-add incident:

1. **Fix the existing MCP tool `calculate_multi_lens_valuation`** so it auto-fetches yfinance data (valuation_inputs + peer multiples + historical multiples) BEFORE running the orchestrator — matching Streamlit's "↻ Refresh all" behavior. Until now, MCP-driven recomputes ran only the orchestrator on whatever data happened to be in the saved cfg, missing the auto-fetch step.

2. **Add a new MCP tool `refresh_all_valuations(force=False)`** that runs the full refresh flow across the entire watchlist in one call. Replaces the standalone `scripts/force_refresh_all.py` for everyday use; the script stays as a manual-creds fallback.

The change also removes duplication: `_auto_fill_valuation_inputs` and `_auto_fill_peer_market_data` currently live as private helpers in `streamlit_app.py`. We extract them to a shared `auto_fetch.py` module that both Streamlit and the MCP server import.

## Non-Goals

- Progress streaming during the batch refresh (Claude Desktop waits for the full response, no real-time progress). The Streamlit progress callback path remains.
- Per-ticker control beyond `force` (e.g. "refresh only these 3 tickers"). If you need a single ticker, call `calculate_multi_lens_valuation(ticker)` directly.
- New auto-fetch features. This PR only relocates and rewires existing helpers — no new yfinance fields, no new lens behavior.
- Removing `scripts/force_refresh_all.py` entirely. Optional simplification (import shared module) but the script is still useful when MCP credentials are inconvenient (e.g. troubleshooting).

## Architecture

```
auto_fetch.py                     ← NEW (~120 lines, pure-ish: imports gather_data + logger)
  ├ auto_fill_valuation_inputs(cfg) → None   (mutates cfg in place)
  └ auto_fill_peer_market_data(cfg) → None   (mutates cfg in place)

streamlit_app.py                  ← MODIFIED
  ├ _auto_fill_valuation_inputs   becomes thin re-export from auto_fetch
  └ _auto_fill_peer_market_data   becomes thin re-export from auto_fetch
  (Existing call sites in _refresh_one don't change.)

mcp_server.py                     ← MODIFIED
  ├ _calculate_multi_lens_valuation_impl: import auto_fetch, call helpers
  │   BEFORE the orchestrator (so MCP recomputes get the same auto-fetch
  │   treatment as Streamlit Refresh-all).
  ├ NEW _refresh_all_valuations_impl(force) — list watchlist, parallel
  │   refresh via ThreadPoolExecutor, return summary dict.
  └ NEW @mcp.tool() refresh_all_valuations(force) — JSON-encoding wrapper.

tests/test_market_data.py         ← MODIFIED
  ├ MOVE existing auto_fill_* tests' patches from streamlit_app to auto_fetch
  ├ NEW test for MCP single-ticker flow
  └ NEW 3 tests for refresh_all_valuations (force on/off, error isolation)

scripts/force_refresh_all.py      ← SIMPLIFIED (optional)
  └ Replace mirror of auto-fill logic with import from auto_fetch
```

`auto_fetch.py` is a small focused module: 2 public functions, both pure data transformations on a cfg dict. Imports `gather_data` and the standard library only. Single responsibility = "apply the auto-fill precedence rules to a cfg dict using yfinance fetch results."

## Data flow

### Single-ticker MCP refresh (existing tool, fixed)

```
calculate_multi_lens_valuation(ticker, scenario_grid=False)
   ↓
_calculate_multi_lens_valuation_impl(ticker, scenario_grid)
   ├ cfg = load_config(client, ticker, user_id)
   │   if cfg is None → return error JSON
   ├ auto_fetch.auto_fill_valuation_inputs(cfg)   ← NEW STEP
   ├ auto_fetch.auto_fill_peer_market_data(cfg)   ← NEW STEP
   ├ summary = valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid)
   ├ cfg["valuation_summary"] = summary
   └ save_config(client, ticker, cfg, user_id)
   ↓
return json.dumps(summary)
```

Two new lines. Existing return shape unchanged. Backward compatible — existing callers still get the same `valuation_summary` JSON.

### Batch MCP refresh (new tool)

```
refresh_all_valuations(force=False)
   ↓
_refresh_all_valuations_impl(force)
   ├ entries = list_watchlist(client, user_id)
   ├ load configs in parallel (6 workers, same as Streamlit Refresh-all)
   ├ for each ticker — determine stale (no summary OR calculated_at > 7d OR unparseable);
   │   if force=True, treat all as stale
   ├ parallel ThreadPoolExecutor over stale set:
   │   for each ticker: auto_fill_* → orchestrator → save_config
   ├ collect computed/errors/skipped
   └ return result dict
   ↓
return json.dumps({
    "computed": [...],   # tickers successfully refreshed
    "errors":   [...],   # ["TICKER: <reason>", ...]
    "skipped":  [...],   # tickers that were fresh and not forced
})
```

Stale-check criteria identical to existing `_refresh_stale_valuations` in `streamlit_app.py`:
- No `valuation_summary` → stale
- `calculated_at` parses but is >7 days old → stale
- `calculated_at` unparseable → stale
- Otherwise fresh

Default `force=False`; pass `force=True` to recompute everything.

Wall-clock: ~10-15s for a 21-ticker watchlist (yfinance bound). Claude Desktop's MCP stdio transport has no hard timeout for tool responses, so the full result returns in one message.

## Module API: `auto_fetch.py`

```python
"""Shared auto-fetch helpers for valuation_inputs and peer market data.

Used by both the Streamlit refresh flow and the MCP server. Mutates the
config dict in place. Each helper respects the `_auto_filled` precedence
rule: fields in that list (or absent) get overwritten with yfinance values;
user-set fields (present, not in `_auto_filled`) are preserved.
"""

import logging
from datetime import UTC, datetime

import gather_data

logger = logging.getLogger(__name__)


def auto_fill_valuation_inputs(cfg: dict) -> None:
    """Auto-fill `cfg["valuation_inputs"]` from yfinance.

    Combines results from gather_data.fetch_market_inputs (Phase 2-B:
    forward_eps, ttm_ebitda) and gather_data.fetch_historical_multiples
    (Phase 2-B.2: historical_trailing_pe, historical_ev_ebitda, ttm_eps).
    Updates `_auto_filled` list and `_fetched_at` timestamp.
    """
    # ...same body as streamlit_app._auto_fill_valuation_inputs


def auto_fill_peer_market_data(cfg: dict) -> None:
    """Auto-fill yfinance fwd_pe + real ev_ebitda for each peer in cfg["peers"].

    fwd_pe respects user-set values (precedence via peer["_auto_filled"]).
    ev_ebitda is always overwritten — see Phase 2-B docs for the
    "always-overwrite" policy on this field.
    """
    # ...same body as streamlit_app._auto_fill_peer_market_data
```

Both functions are byte-for-byte the same logic as today's private versions in `streamlit_app.py`. The relocation is the only change; behavior is preserved.

## Streamlit re-export

`streamlit_app.py` keeps the underscore-prefixed names as thin aliases so existing call sites and tests stay intact:

```python
from auto_fetch import (
    auto_fill_valuation_inputs as _auto_fill_valuation_inputs,
    auto_fill_peer_market_data as _auto_fill_peer_market_data,
)
```

Mirrors the `scorecard_utils` pattern from Phase 1 — proven safe and minimal disruption.

## MCP tool surface

### Existing (modified): `calculate_multi_lens_valuation`

Signature unchanged: `(ticker: str, scenario_grid: bool = False) -> str (JSON)`. Body adds two `auto_fetch` calls before the orchestrator. Docstring updated to mention auto-fetch behavior.

### New: `refresh_all_valuations`

```python
@mcp.tool()
def refresh_all_valuations(force: bool = False) -> str:
    """Run multi-lens fair value across the entire watchlist.

    Stale = no valuation_summary OR calculated_at older than 7 days OR
    unparseable. Stale tickers get auto-fetched from yfinance + orchestrator
    + saved. Fresh tickers are skipped unless force=True.

    Args:
        force: When True, recompute every ticker regardless of freshness.

    Returns:
        JSON with three keys:
            computed: list of tickers successfully refreshed
            errors: list of "TICKER: error" strings
            skipped: list of tickers that were fresh and not forced
    """
```

## Errors, skips, logging

- **Per-ticker errors** in the batch tool: caught at `as_completed`, appended to `errors` list, other tickers continue. Same pattern as `_refresh_stale_valuations`.
- **MCP-level errors** (e.g., Supabase auth fails before any ticker runs): caught in the `@mcp.tool()` wrapper, returned as `{"error": "..."}` JSON. Same pattern as other MCP tools.
- **Logger**: re-uses `logger = logging.getLogger(__name__)` already in `auto_fetch` and `mcp_server` modules.

## Tests

`tests/test_market_data.py` — modifications + additions:

**Move (no logic change, just patch target):**
- `test_auto_fill_inputs_populates_empty`
- `test_auto_fill_inputs_respects_user_set_value`
- `test_auto_fill_inputs_overwrites_previous_auto_value`
- `test_auto_fill_inputs_doesnt_overwrite_with_none`
- `test_auto_fill_inputs_fetched_at_always_set`
- `test_auto_fill_peer_populates_empty`
- `test_auto_fill_peer_respects_user_set_value`
- `test_auto_fill_peer_skips_invalid_entries`
- `test_auto_fill_inputs_includes_historical_multiples`

These tests currently call `streamlit_app._auto_fill_valuation_inputs(cfg)`. Replace with `auto_fetch.auto_fill_valuation_inputs(cfg)`. Same patches against `gather_data.fetch_market_inputs` etc. Same assertions.

**Add:**

1. `test_mcp_calculate_multi_lens_valuation_does_auto_fetch` — verify `_calculate_multi_lens_valuation_impl` calls auto-fetch before orchestrator. Mock load_config + save_config + yfinance; assert saved cfg has populated `valuation_inputs.forward_eps` (was the NFLX bug).

2. `test_refresh_all_valuations_force_true_processes_everything` — mock list_watchlist returns 3 tickers (one fresh, two stale); call with `force=True`; assert all 3 in `computed`.

3. `test_refresh_all_valuations_default_skips_fresh` — same setup; call with `force=False`; assert fresh ticker in `skipped`, stale ones in `computed`.

4. `test_refresh_all_valuations_per_ticker_error_isolated` — mock orchestrator to raise for one ticker; verify others still complete and the failing one appears in `errors`.

Total tests after this PR: 173 → 177.

`tests/test_watchlist_ui.py` keeps existing patches (`gather_data.fetch_market_inputs`, etc.) unchanged — they patch the underlying `gather_data` module, not `streamlit_app.*`, so the relocation is invisible to them.

## Migration & backward compatibility

- Streamlit `_refresh_stale_valuations` and `_refresh_one` continue to work — they call the underscore-aliased re-exports. No call-site changes.
- `tests/test_watchlist_ui.py` patches `gather_data.*` not `streamlit_app._auto_fill_*`, so test changes are zero.
- MCP existing tool `calculate_multi_lens_valuation` keeps the same signature and JSON return shape. Only adds new behavior (auto-fetch step). Callers don't break.
- `scripts/force_refresh_all.py` works unchanged. Optional cleanup: import `auto_fetch` to drop ~30 lines of mirror code (drop-in replacement).

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Relocating helpers breaks Streamlit imports at module-load | Use the `from auto_fetch import X as _X` pattern; tests verify the re-export by running the existing watchlist-UI tests against the new arrangement. |
| MCP batch tool runs 150 yfinance calls and Claude Desktop times out | Local stdio MCP has no hard timeout; ~12s is well within tool-response norms. If we ever hit limits, we'd add a `tickers: list[str]` param to scope the batch. |
| Force flag semantics drift between Streamlit and MCP | Both call into the same `auto_fetch.py` module; the stale-check is itself a small util that can be lifted to `auto_fetch` later if needed. For now it lives in both `streamlit_app._refresh_stale_valuations` and `mcp_server._refresh_all_valuations_impl` — same algorithm, different code (acceptable; <20 lines each). |

## Acceptance criteria

1. Calling `calculate_multi_lens_valuation("NFLX")` via Claude Desktop now populates `valuation_inputs.forward_eps`/`historical_trailing_pe`/etc. (the NFLX bug is fixed).
2. Calling `refresh_all_valuations()` via Claude Desktop returns a JSON with computed/errors/skipped covering the full watchlist.
3. Calling `refresh_all_valuations(force=True)` recomputes every ticker (no skipped).
4. Streamlit `↻ Refresh all` still works identically — same 21-ticker behavior, no test regressions.
5. All 9 moved tests + 4 new tests pass. Full pytest suite: 173 → 177.
6. `python3 -m ruff check .` clean for new code.

## Implementation order

1. Create `auto_fetch.py` with the two helpers (copy from streamlit_app, rename without underscore prefix).
2. Update `streamlit_app.py` to import them as underscore-prefixed re-exports. Verify all existing tests still pass.
3. Move the 9 auto_fill_* tests to patch `auto_fetch.*` instead of `streamlit_app.*`. Verify they pass.
4. Update `mcp_server._calculate_multi_lens_valuation_impl` to call `auto_fetch` helpers before orchestrator. Add 1 test.
5. Add `_refresh_all_valuations_impl` + `@mcp.tool() refresh_all_valuations` to `mcp_server.py`. Add 3 tests.
6. (Optional) Simplify `scripts/force_refresh_all.py` to import from `auto_fetch`.
7. Lint + full regression suite.
