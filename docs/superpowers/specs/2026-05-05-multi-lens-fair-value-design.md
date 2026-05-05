# Multi-Lens Fair Value — Phase 1 Design

**Status:** Draft for review
**Date:** 2026-05-05
**Author:** Arjan + Claude

## Goal

Extend LazyTheta from a DCF-only valuation tool to a **multi-lens fair value triangulator**. A single DCF can be misleading (especially for mature wide-moat companies where terminal margin assumptions dominate). Combining DCF with trading multiples and reverse-DCF gives a more honest fair value range.

Phase 1 ships **DCF + Trading Multiples + Reverse DCF** as three first-class lenses, plus enriched `get_watchlist()` output that surfaces the multi-lens fair value range. Dividend-Discount and Sum-of-the-Parts are deferred to Phase 2.

## Non-Goals (Phase 2 / out of scope)

- Dividend Discount Model (Gordon Growth + yield mean-reversion) — placeholder stub only.
- Sum-of-the-Parts for diversified businesses.
- Auto-fetching forward EPS, peer multiples, EBITDA from Yahoo / FMP / etc.
- Promoting `valuation_summary` fields to dedicated Supabase columns (stays inside the existing `config` JSONB column).
- UI changes outside `get_watchlist`.
- Real-basis vs nominal-basis switching for the multiples lens.

## Architecture Overview

```
stock-analysis/
├── valuation_lenses.py        ← NEW: pure functions, no IO
├── scorecard_utils.py         ← NEW: shared scorecard parser
├── dcf_calculator.py          ← UNCHANGED
├── config_store.py            ← MODIFIED: guarded keys + enriched list_watchlist
├── mcp_server.py              ← MODIFIED: new tool, uses scorecard_utils
├── streamlit_app.py           ← MODIFIED: imports parser from scorecard_utils
└── tests/test_multi_lens.py   ← NEW: 5 acceptance tests
```

**Key boundaries:**

- `valuation_lenses.py` is **pure**: takes config dicts, returns dicts. No Supabase, no network, no `streamlit_app` imports. Trivially testable.
- `mcp_server.py` is the only place that combines `valuation_lenses` with persistence. The new MCP tool loads, computes, persists in one call.
- `scorecard_utils.py` is a small extraction of the existing `_parse_scorecard_json` helper from `streamlit_app.py`, so both Streamlit and MCP server share one implementation. Streamlit refactors to import this helper (no behavior change).

## Data Model

Three new top-level keys in the config dict, all optional. Existing configs without these keys keep working unchanged.

### `config["valuation_inputs"]` — user-supplied lens inputs

```python
{
    "forward_eps":              float | None,   # Next-FY consensus EPS
    "historical_fwd_pe":        float | None,   # Own 5y/10y avg forward P/E
    "ttm_ebitda":               float | None,   # TTM EBITDA in $M
    "target_dividend_yield":    float | None,   # Stored, unused in Phase 1
    "current_dividend":         float | None,   # Stored, unused in Phase 1
    "expected_dividend_growth": float | None,   # Stored, unused in Phase 1
}
```

All fields default to `None` when absent. Lenses that depend on missing inputs are silently skipped (logged at INFO, never raised).

### `config["lens_weights"]` — optional weight overrides

```python
{
    "dcf":         float,
    "multiples":   float,
    "reverse_dcf": float,
    "dividend":    float,
}
```

Defaults when absent (set in `valuation_lenses.DEFAULT_LENS_WEIGHTS`):

```python
DEFAULT_LENS_WEIGHTS = {
    "dcf":         0.30,
    "multiples":   0.40,
    "reverse_dcf": 0.10,
    "dividend":    0.00,   # Phase 2 placeholder, always 0 for now
}
```

Weights need not sum to 1.0 — they are renormalized across **active** lenses (i.e. lenses that produced a non-`None` result).

### `config["valuation_summary"]` — auto-computed output

```python
{
    "calculated_at": str,       # ISO-8601 UTC timestamp
    "stock_price":   float,     # snapshot at calc time
    "scenario_grid": bool,      # which DCF mode was used
    "lenses": {
        "dcf": {
            "fv_low": float, "fv_mid": float, "fv_high": float,
            "weight": float,             # raw, from cfg or default
            "weight_normalized": float,  # renormalized across actives
            "details": {
                "wacc": float,
                "base_intrinsic": float,
                "scenarios": list[float] | None,  # 16 prices if scenario_grid
            },
        },
        "multiples": {
            "fv_low": float, "fv_mid": float, "fv_high": float,
            "weight": float,
            "weight_normalized": float,
            "details": {
                "fwd_pe_own":            float | None,
                "fwd_pe_peer_median":    float | None,
                "ev_ebitda_peer_median": float | None,
                "closest_peer":          str | None,    # ticker, informational
                "skipped":               list[str],     # which sub-anchors skipped + reason
            },
        } | None,                                 # None if all anchors skipped
        "reverse_dcf": {
            "fv_low": float, "fv_mid": float, "fv_high": float,
            "weight": float, "weight_normalized": float,
            "details": {
                "implied_growth": float,
                "implied_margin": float,
            },
        },
        "dividend": None,   # Phase 2 placeholder
    },
    "weighted_fv_low":  float,
    "weighted_fv_mid":  float,
    "weighted_fv_high": float,
    "current_vs_mid":   float,   # signed: (price - fv_mid) / fv_mid
    "buy_price":        float,   # weighted_fv_mid * (1 - margin_of_safety)
}
```

### Peer schema extension

Each entry in `config["peers"]` accepts an optional `fwd_pe: float | None` field. Existing peer dicts without `fwd_pe` keep working — the multiples lens treats missing values as "skip this peer for the fwd-PE component."

### Migration safety

- No Supabase migrations. All new keys live in the existing `config` JSONB column.
- `_AI_NOTES_GUARDED_KEYS` in `config_store.py` extends to `("ai_notes", "peers", "valuation_inputs", "valuation_summary", "lens_weights")` so that partial saves never silently wipe these keys.
- The existing `cfg["buy_price"]` (from DCF) is left untouched. The new `valuation_summary.buy_price` is the weighted version. `get_watchlist()` exposes the weighted version (consistent with `fv_mid`).

## Lens Logic

### Lens 1 — DCF (`compute_dcf_lens`)

Always available. Takes a `scenario_grid` flag.

**`scenario_grid=False`** (default, fast):
- Run `dcf_calculator.compute_intrinsic_value(cfg)` for the base case.
- `fv_mid = base_intrinsic`, `fv_low = base * 0.85`, `fv_high = base * 1.15`.

**`scenario_grid=True`** (4×4 grid):
- Pull `bull_growth_adj`, `bear_growth_adj`, `bull_margin_adj`, `bear_margin_adj` from cfg (defaults: +0.02 / -0.04 / +0.02 / -0.02 — same defaults already in `dcf_template.py`).
- Build offsets: `growth_offsets = [bear, bear/2, bull/2, bull]` and similar for margin.
- Compute 16 intrinsic values by uniformly shifting `revenue_growth` and `op_margins` (and `terminal_margin`) by each `(g_off, m_off)` pair.
- `fv_low = min(prices)`, `fv_mid = base_intrinsic`, `fv_high = max(prices)`.

`details.scenarios` holds the 16 prices when grid mode is on, else `None`.

### Lens 2 — Trading Multiples (`compute_multiples_lens`)

Three independent sub-anchors. Each contributes to a list of fv anchors; lens-level `fv_low/mid/high = min/mean/max` of all collected anchors.

**Sub-anchor A — own historical fwd P/E:**
- Requires `valuation_inputs.forward_eps` AND `valuation_inputs.historical_fwd_pe`.
- `fv = historical_fwd_pe * forward_eps`.

**Sub-anchor B — peer fwd P/E (median + range):**
- Requires `valuation_inputs.forward_eps` AND ≥1 peer with `fwd_pe`.
- Collect all `peers[i].fwd_pe` that are not None.
- `fv_mid = median(peer_fwd_pes) * forward_eps`.
- `fv_low = min(peer_fwd_pes) * forward_eps`, `fv_high = max(...) * forward_eps`.
- `closest_peer` = ticker of peer with smallest weighted Euclidean distance on `(op_margin, rev_growth)` to the target — informational only, not used for math.

**Sub-anchor C — EV/EBITDA (peer median + range):**
- Requires `valuation_inputs.ttm_ebitda` AND ≥1 peer with `ev_ebitda`.
- `net_debt = cfg.debt_market_value - cfg.cash_bridge - cfg.securities`.
- `fv = (peer_ev_ebitda * ttm_ebitda - net_debt) / shares_outstanding`, applied to median/min/max of peer set.

**Skip behavior:**
- A sub-anchor that lacks its inputs is appended to `details.skipped` with a one-line reason and INFO-logged.
- If **all three** sub-anchors are skipped, the entire lens returns `None` and is excluded from the weighted FV.

### Lens 3 — Reverse DCF (`compute_reverse_dcf_lens`)

- Calls `dcf_calculator.compute_reverse_dcf(cfg)` (existing).
- Output: `fv_low = fv_mid = fv_high = stock_price`. The lens is an "anchor at current price" — it tells you what is priced in, not what the company is worth.
- `details = {implied_growth, implied_margin}`.
- Default weight `0.10`, deliberately low — it should not dominate but is useful as a directional check.

### Lens 4 — Dividend Discount (stub)

```python
def compute_dividend_lens(cfg):
    # TODO Phase 2: Gordon Growth + yield mean-reversion
    return None
```

Always returns `None` in Phase 1. Excluded from weighted FV.

### Orchestrator (`calculate_multi_lens_valuation`)

```python
def calculate_multi_lens_valuation(cfg, scenario_grid=False):
    lenses = {
        "dcf":         compute_dcf_lens(cfg, scenario_grid=scenario_grid),
        "multiples":   compute_multiples_lens(cfg),
        "reverse_dcf": compute_reverse_dcf_lens(cfg),
        "dividend":    compute_dividend_lens(cfg),
    }
    active = {n: l for n, l in lenses.items() if l is not None}
    weights = cfg.get("lens_weights", DEFAULT_LENS_WEIGHTS)
    raw = {n: weights.get(n, 0.0) for n in active}
    total = sum(raw.values()) or 1.0
    norm = {n: w / total for n, w in raw.items()}

    for n, lens in active.items():
        lens["weight"] = raw[n]
        lens["weight_normalized"] = norm[n]

    weighted_low  = sum(active[n]["fv_low"]  * norm[n] for n in active)
    weighted_mid  = sum(active[n]["fv_mid"]  * norm[n] for n in active)
    weighted_high = sum(active[n]["fv_high"] * norm[n] for n in active)

    mos = cfg.get("margin_of_safety", 0.20)
    price = cfg["stock_price"]

    return {
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "stock_price": price,
        "scenario_grid": scenario_grid,
        "lenses": lenses,
        "weighted_fv_low":  round(weighted_low, 2),
        "weighted_fv_mid":  round(weighted_mid, 2),
        "weighted_fv_high": round(weighted_high, 2),
        "current_vs_mid":   round((price - weighted_mid) / weighted_mid, 4) if weighted_mid else 0.0,
        "buy_price":        round(weighted_mid * (1 - mos), 2),
    }
```

## MCP Tool Surface

### New tool

```python
@mcp.tool()
def calculate_multi_lens_valuation(ticker: str, scenario_grid: bool = False) -> str:
    """Recompute multi-lens fair value for a watchlist ticker and persist
    the summary back to Supabase.

    Loads the existing config, runs DCF + Multiples + Reverse-DCF lenses,
    stores the result in config['valuation_summary'], and saves.

    Args:
        ticker: Stock ticker symbol (e.g. "ABT")
        scenario_grid: If True, run a 4x4 bull/bear DCF scenario grid for
            DCF lens fv_low/fv_high. Default False uses ±15% bands.

    Returns:
        JSON valuation_summary dict.
    """
```

Internally:
1. `cfg = config_store.load_config(client, ticker, user_id=USER_ID)`
2. `summary = valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid)`
3. `cfg["valuation_summary"] = summary`
4. `config_store.save_config(client, ticker, cfg, user_id=USER_ID)`
5. Return `json.dumps(summary, default=str)`

### Existing tools

- `calculate_valuation` — **unchanged** (DCF-only, backward compat).
- `build_dcf_config` — unchanged (peers still get `fwd_pe = None` by default; user fills it in if available).
- `save_to_watchlist`, `get_config` — unchanged.
- `get_watchlist` — output **shape changes** (see below).

### `get_watchlist` enriched output

```python
[
    {
        "ticker", "company", "updated", "stock_price",   # existing
        "fv_low", "fv_mid", "fv_high",                   # from valuation_summary, or None
        "buy_price",                                      # weighted, or None
        "current_vs_mid",                                 # or None
        "lens_count": int,                                # 0 if no summary
        "verdict",                                        # from Scorecard, or None
        "phase":   int | None,                            # from Scorecard, or None
    },
    ...
]
```

`config_store.list_watchlist` selects the `config` column too and inlines the parsing. Configs without `valuation_summary` get `None` for all new numeric fields. `verdict` and `phase` come from `scorecard_utils.parse_scorecard(cfg.get("ai_notes", {}))`.

Docstring note: "Configs without `valuation_summary` show only base fields; run `calculate_multi_lens_valuation` to populate."

## Errors, Skips, Logging

- **No exceptions for missing inputs.** Every missing `valuation_inputs` field or peer-level field results in an INFO log and a skip — never a raise.
- **Component skip:** logs `Multiples lens: skipping fwd_pe_own (forward_eps missing)`.
- **Full lens skip:** logs `Multiples lens fully skipped (no anchors)`. Lens excluded from weighted FV.
- **Worst case:** only DCF active → `weight_normalized = 1.0` → weighted FV = DCF FV.
- **DCF errors** (e.g. `equity_market_value=0`): bubble up as today. We do not catch these; an unusable DCF means an unusable valuation.
- **Logger:** standard `logging.getLogger(__name__)` in `valuation_lenses.py`.

## Tests

`tests/test_multi_lens.py` — 5 tests, all offline (no Supabase, no network). Fixtures inline.

1. `test_dcf_only_fallback` — config without `valuation_inputs` → summary has only DCF lens, `weight_normalized = 1.0`, `weighted_fv_mid` equals DCF base intrinsic.
2. `test_all_lenses_active` — full ABT-style config → 3 active lenses (DCF, Multiples, Reverse), `min(mids) <= weighted_fv_mid <= max(mids)`.
3. `test_watchlist_enriched_shape` — mocked Supabase response → `list_watchlist` returns dicts whose **keys** include all new fields; values are `None` (not absent) when summary missing.
4. `test_round_trip_persistence` — compute summary → mock save_config → mock list_watchlist returns same `fv_mid`.
5. `test_no_regression_calculate_valuation` — existing `_calculate_valuation_impl` returns same shape and same numeric output as before this change (golden-fixture comparison).

Run: `python3 -m pytest tests/test_multi_lens.py -v`.

## Implementation Order

1. **Schema + utils:** `scorecard_utils.py` (extract from streamlit_app.py), guarded keys in `config_store.py`.
2. **Lens module:** `valuation_lenses.py` with all four lens functions and orchestrator.
3. **Watchlist enrichment:** modify `config_store.list_watchlist`.
4. **MCP tool wiring:** new `calculate_multi_lens_valuation` tool in `mcp_server.py`.
5. **Streamlit cleanup:** swap `_parse_scorecard_json` for the shared util import.
6. **Tests:** write `tests/test_multi_lens.py`, iterate until green.
7. **Lint + tests:** `python3 -m ruff check .` and the regression suite (`test_tastytrade_api.py test_ibkr_api.py`).

## Acceptance Criteria

All five tests in `tests/test_multi_lens.py` pass. Existing test suite (`test_tastytrade_api.py test_ibkr_api.py`, 81 tests) still passes. `ruff check .` clean.

## Reference: ABT Sanity Check

```python
abt_valuation_inputs = {
    "forward_eps": 5.48,
    "historical_fwd_pe": 21.0,
    "target_dividend_yield": 0.020,
    "ttm_ebitda": 11800.0,
    "current_dividend": 2.36,
    "expected_dividend_growth": 0.075,
}

# Expected ballpark (precision differences are fine):
# lenses.dcf.fv_mid       ~ 48
# lenses.multiples.fv_mid ~ 100
# lenses.reverse_dcf.fv_mid ~ 88 (current price)
# weighted_fv_mid ~ 79
# current_vs_mid ~ +0.11 (slightly above weighted FV)
```
