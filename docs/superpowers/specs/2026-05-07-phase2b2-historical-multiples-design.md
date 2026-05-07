# Phase 2-B.2: Historical Multiples Auto-Fetch — Design

**Status:** Draft for review
**Date:** 2026-05-07
**Author:** Arjan + Claude
**Predecessor:** `2026-05-05-phase2b-auto-fetch-market-data-design.md` (Phase 2-B)

## Goal

Auto-populate `valuation_inputs.historical_trailing_pe` and `valuation_inputs.historical_ev_ebitda` from a 4-year monthly yfinance history, plus add two new "own historical" sub-anchors to the multiples lens. This makes the multiples lens use a company's own valuation history (a more company-specific, less subjective signal than auto-discovered peer comparisons) without dropping the existing peer-based sub-anchors — both signals are available, with own-history typically dominating because it has 2 sub-anchors contributing.

## Non-Goals

- Forward P/E history (yfinance has no historical forward EPS estimates → no clean source). Keep `valuation_inputs.historical_fwd_pe` as a manual-only field; sub-anchor A.1 stays in place but rarely fires unless the user fills the value via Claude Desktop.
- Sector-relative or beta-adjusted historical multiples — out of scope, possibly Phase 2-B.3.
- 10-year history — yfinance caps annual income statement at ~4 years for most tickers. SEC EDGAR could supply 11 years but adds significant integration complexity. Stick with 4-year for v1.
- Per-sub-anchor weight control inside the multiples lens. Use lens-level `lens_weights["multiples"]` for now; finer-grained weights are a follow-up if needed.

## What "historical trailing P/E" means here

For each month in the 4-year window:
- `price_t = monthly_close_price` (from yfinance `Ticker.history(period="4y", interval="1mo")`)
- `ttm_eps_t = trailing-twelve-months EPS as of month t` (interpolated from annual yfinance income-statement EPS)
- `pe_t = price_t / ttm_eps_t` (skip months where `ttm_eps_t <= 0` to avoid division by zero / negative-earnings noise)

The historical multiple is `median(pe_t for t in window)`. Median is robust to outlier months (single-quarter losses, COVID dips, AI-bubble peaks).

Same idea for EV/EBITDA:
- `ev_t = market_cap_t + debt_t - cash_t` (debt + cash from quarterly balance sheet, interpolated)
- `ebitda_t = trailing-twelve-months EBITDA as of month t`
- `ev_ebitda_t = ev_t / ebitda_t`
- Result: `median(ev_ebitda_t)`

## Architecture

```
gather_data.py
└── NEW: fetch_historical_multiples(ticker) → dict
    Returns {historical_trailing_pe, historical_ev_ebitda, ttm_eps}
    Each may be absent if data is insufficient. Never raises.

streamlit_app.py
└── _auto_fill_valuation_inputs(cfg)
      ├─ existing call: fetch_market_inputs(ticker)
      └─ NEW: fetch_historical_multiples(ticker)
         Both feed into the same merge-with-_auto_filled-precedence loop.

valuation_lenses.py
└── compute_multiples_lens(cfg)
    Two new sub-anchors added (kept independently from existing ones):
      A.2 own historical trailing P/E × ttm_eps
      D   own historical EV/EBITDA × ttm_ebitda - net_debt → /shares
    The existing A.1, B, C remain unchanged.
```

## Data model

Three new optional keys in `valuation_inputs`. Type and semantics:

```python
config["valuation_inputs"] = {
    # Existing (Phase 1 + Phase 2-B):
    "forward_eps":        float | None,
    "historical_fwd_pe":  float | None,   # manual only — no auto-fetch
    "ttm_ebitda":         float | None,
    # NEW (Phase 2-B.2):
    "historical_trailing_pe":  float | None,   # 4-year monthly median
    "historical_ev_ebitda":    float | None,   # 4-year monthly median
    "ttm_eps":                 float | None,   # current TTM EPS (yfinance trailingEps)
    # Existing dividend stubs:
    "target_dividend_yield":   float | None,
    "current_dividend":        float | None,
    "expected_dividend_growth": float | None,
    # Existing meta:
    "_auto_filled": [...],   # extends with the new keys when auto-fetched
    "_fetched_at": str,
}
```

`_auto_filled` precedence (from Phase 2-B): if a new key is absent OR already in the list, the auto-fetcher writes; if present and not in `_auto_filled`, user-set value preserved.

## Fetcher

### `gather_data.fetch_historical_multiples(ticker) -> dict`

```python
def fetch_historical_multiples(ticker: str) -> dict:
    """Compute 4-year median historical trailing P/E and EV/EBITDA from yfinance.

    Returns a dict with these keys (any may be absent when data insufficient):
        historical_trailing_pe:  float, 4-year monthly median price/ttm_eps
        historical_ev_ebitda:    float, 4-year monthly median EV/ttm_ebitda
        ttm_eps:                 float, current trailing EPS

    Skips months where TTM denominator is <= 0 (negative earnings windows
    don't carry P/E meaning).

    yfinance failures or insufficient data → returns empty dict, logs warning.
    Never raises.
    """
```

Algorithm:
1. Fetch 4-year monthly price history (`Ticker.history(period="4y", interval="1mo")`)
2. Fetch annual income statement (`Ticker.income_stmt`) — yields ~4 annual data points for `Diluted EPS` and `EBITDA`
3. Fetch `info["trailingEps"]` for current TTM EPS
4. Build a monthly TTM-EPS series by linear-interpolating annual EPS to month-ends. Each annual datum becomes a year-end anchor; intermediate months get linear interpolation between adjacent annual anchors. Last partial year extrapolates from the most recent annual to today using monthly slope.
5. For each month `t`: compute `pe_t = price_t / ttm_eps_t` if `ttm_eps_t > 0`. Discard otherwise.
6. `historical_trailing_pe = statistics.median(pe_t for t in months)`. If fewer than 12 valid months, return empty (insufficient signal).
7. Same flow for EV/EBITDA, but the denominator uses interpolated EBITDA. The numerator (EV) needs market_cap (price × shares_t) + debt_t - cash_t. Shares + debt + cash interpolated from quarterly balance sheet (`Ticker.quarterly_balance_sheet`).
8. `ttm_eps = info.get("trailingEps")` (no computation, just pass-through).

Total yfinance calls per ticker: 1 history + 1 income_stmt + 1 quarterly_balance_sheet + 1 info = 4. Adds ~0.5-1s to existing per-ticker refresh.

### Insufficient-data handling

| Condition | Behavior |
|-----------|----------|
| `Ticker.history` returns < 24 months | Skip historical_trailing_pe AND historical_ev_ebitda |
| `Diluted EPS` missing from income_stmt | Skip historical_trailing_pe (P/E uncomputable) |
| `EBITDA` missing or all NaN | Skip historical_ev_ebitda |
| Quarterly balance sheet missing `Total Debt` or `Cash` | Skip historical_ev_ebitda |
| Fewer than 12 months with positive denominator | Skip that specific metric |
| All else fails | Return `{}` |

Each missing field is silently dropped — same pattern as `fetch_market_inputs`.

## Multiples lens changes

`compute_multiples_lens` gains two new sub-anchor branches. The existing A.1 (own forward P/E), B (peer fwd P/E), C (peer EV/EBITDA) are untouched.

```python
# A.2 own historical trailing P/E × ttm_eps (NEW)
if historical_trailing_pe and ttm_eps and ttm_eps > 0:
    own_trailing_fv = historical_trailing_pe * ttm_eps
    fv_anchors.append(own_trailing_fv)
    details["historical_trailing_pe_fv"] = own_trailing_fv
else:
    details["skipped"].append("historical_trailing_pe (no historical_trailing_pe or ttm_eps)")

# D own historical EV/EBITDA × ttm_ebitda - net_debt (NEW)
if historical_ev_ebitda and ttm_ebitda:
    net_debt = (cfg.get("debt_market_value", 0.0)
                - cfg.get("cash_bridge", 0.0)
                - cfg.get("securities", 0.0))
    shares = cfg.get("shares_outstanding") or 1.0
    own_evebitda_fv = (historical_ev_ebitda * ttm_ebitda - net_debt) / shares
    fv_anchors.append(own_evebitda_fv)
    details["historical_ev_ebitda_fv"] = own_evebitda_fv
else:
    details["skipped"].append("historical_ev_ebitda (no historical_ev_ebitda or ttm_ebitda)")
```

The lens-level `fv_low/fv_mid/fv_high` continues to be `min/mean/max` of the full `fv_anchors` list. With the new anchors:
- 2 own-history anchors (1 each from A.2 and D)
- 3 peer fwd-PE anchors (low/mid/high if peers present)
- 3 peer EV/EBITDA anchors (low/mid/high if peers present)
- 1 own forward P/E anchor (only if user manually filled `historical_fwd_pe`)

Total typical anchor count: 8-9 when own-history available, vs 6-7 before. Own-history accounts for ~25% of anchor weight in the mean — meaningful but not dominant. If you want own-history to weigh more, that's a follow-up tuning task (per-sub-anchor weights) — not this PR.

## Auto-fill orchestration

Existing `streamlit_app._auto_fill_valuation_inputs(cfg)` (Phase 2-B):

```python
def _auto_fill_valuation_inputs(cfg: dict) -> None:
    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    fetched = gather_data.fetch_market_inputs(cfg.get("ticker", ""))   # existing
    # NEW: also fetch historical multiples
    fetched.update(gather_data.fetch_historical_multiples(cfg.get("ticker", "")))
    # Merge with precedence (unchanged)
    for key, value in fetched.items():
        existing = inputs.get(key)
        if existing is None or key in auto_filled:
            inputs[key] = value
            ...
```

`fetch_market_inputs` returns `{forward_eps, ttm_ebitda}`; `fetch_historical_multiples` returns `{historical_trailing_pe, historical_ev_ebitda, ttm_eps}`. No key overlap; `dict.update` is safe.

## Errors, skips, logging

- yfinance ImportError or generic error → return `{}`, log warning. Same pattern as `fetch_market_inputs`.
- Insufficient months / missing fundamentals → silently skip the affected key. INFO log per skip.
- Module-level `logger = logging.getLogger(__name__)` already exists (added in Phase 2-B).

## Tests

`tests/test_market_data.py` — extended with ~7 new tests:

1. `test_fetch_historical_multiples_happy_path` — mock yfinance to return 60 monthly prices + 4y annual income_stmt with diluted EPS + EBITDA + quarterly balance sheet. Verify `historical_trailing_pe`, `historical_ev_ebitda`, `ttm_eps` all populated and within reasonable ranges.

2. `test_fetch_historical_multiples_negative_eps_quarter_skipped` — mock with one annual EPS = -1.0 (loss year). Median should still produce a positive P/E from non-loss months; loss months excluded.

3. `test_fetch_historical_multiples_insufficient_history` — mock 6 months only. Returns `{}` (need ≥ 24 months for the metrics).

4. `test_fetch_historical_multiples_yfinance_error` — `yf.Ticker(...)` raises. Returns `{}`, no propagation.

5. `test_fetch_historical_multiples_missing_ebitda` — income_stmt has no EBITDA row. Returns dict with `historical_trailing_pe` and `ttm_eps` only (EV/EBITDA skipped silently).

6. `test_auto_fill_inputs_writes_historical_keys` — verify `_auto_fill_valuation_inputs` actually writes the three new keys to `cfg["valuation_inputs"]` and adds them to `_auto_filled`. Mock both fetchers via patch.

7. `test_multiples_lens_uses_historical_trailing_pe` — config with `historical_trailing_pe = 25.0` and `ttm_eps = 4.0` → A.2 contributes 100.0 to `fv_anchors`. Verify `details["historical_trailing_pe_fv"] == 100.0`.

8. `test_multiples_lens_uses_historical_ev_ebitda` — symmetric test for sub-anchor D.

## Migration & backward compatibility

- All new keys optional. Configs without them keep working.
- `_AI_NOTES_GUARDED_KEYS` already protects `valuation_inputs` from silent wipes (Phase 1).
- Existing tickers' first refresh after this PR auto-populates the three new keys.
- Tickers without enough yfinance history (recent IPOs) silently skip; no errors.

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Linear interpolation of annual EPS to monthly is crude (assumes smooth growth across the year) | Acceptable for a 4-year median — the monthly noise washes out. Documented behavior in helper docstring. |
| Quarterly balance sheet missing for some tickers (small caps) | EV/EBITDA simply skipped; trailing P/E still works |
| Median of 48 monthly P/Es can still include extreme months | If needed, swap median for trimmed mean (drop top/bottom 10%) in v2. Median is good enough for v1. |
| yfinance occasionally returns empty income_stmt for valid tickers | Existing fallback chain (`trailingEbitda` → `ebitda` from Phase 2-B fix) doesn't apply here — different fields. Helper returns `{}` and the user sees the multiples lens with only peer sub-anchors. Same recovery as today. |

## Acceptance criteria

1. After Refresh-all, every watchlist ticker with ≥4 years of yfinance history has `historical_trailing_pe` + `historical_ev_ebitda` + `ttm_eps` populated in `valuation_inputs`.
2. Tickers with <24 months of yfinance data (e.g. recent IPOs) keep working without errors; just no own-history sub-anchors fire.
3. The multiples lens fv range narrows for tickers where own-history disagrees with peer outliers — visible in the watchlist range-bar.
4. `_auto_filled` list includes the three new keys for tickers where they auto-filled.
5. User can override any of the three values via Claude Desktop edit; subsequent refresh preserves the override.
6. All ~8 new tests pass; full pytest suite stays green (currently 163 → 171 tests after this PR).
7. `python3 -m ruff check .` clean.

## Implementation order

1. `gather_data.fetch_historical_multiples` + 5 unit tests (yfinance mocked).
2. Extend `_auto_fill_valuation_inputs` to call the new fetcher + 1 integration test.
3. Add sub-anchors A.2 and D to `compute_multiples_lens` + 2 lens tests.
4. Run force-refresh on 21-ticker watchlist; verify range narrowing on a few tickers.
5. Lint + full regression.
