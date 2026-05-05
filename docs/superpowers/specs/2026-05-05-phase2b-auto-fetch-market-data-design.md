# Phase 2-B: Auto-Fetch Market Data — Design

**Status:** Draft for review
**Date:** 2026-05-05
**Author:** Arjan + Claude
**Predecessor:** `2026-05-05-phase2-watchlist-ui-design.md` (Phase 2-A)

## Goal

Automatically populate `valuation_inputs.forward_eps`, `valuation_inputs.ttm_ebitda`, peer `fwd_pe`, and replace the rough peer `ev_ebitda` approximation with real `trailingEbitda` from Yahoo Finance — bundled into the existing "↻ Refresh all" flow. Removes the friction of manually entering these per ticker via Claude Desktop and unblocks the Multiples lens for every watchlist ticker.

This is **Phase 2-B**, the second sub-project of Phase 2. UI surfacing already shipped in 2-A; dividend lens, sum-of-the-parts, real-basis switching, and historical-fwd-PE auto-fetch remain in their own future plans.

## Non-Goals

- `valuation_inputs.historical_fwd_pe` — there is no clean yfinance source for 5y/10y average forward P/E. Stays manual or empty. Could become a separate Phase 2-B.2 sub-project later.
- Dividend-related fields (`target_dividend_yield`, `current_dividend`, `expected_dividend_growth`) — gated by Phase 2-C dividend lens, fetched together with that.
- New data sources (FMP, Macrotrends, etc.) — `yfinance` is already a dependency and good enough.
- Editor-page UI for `valuation_inputs` — out of scope. With auto-fetch in place, manual entry stops being the primary path.
- Background scheduling. The fetch runs only when the user clicks Refresh all.
- A separate "Fetch market data" button. Folded into the existing Refresh-all UX per user preference.

## User Story

> "I add a new ticker. Click ↻ Refresh all. Yfinance fills in `forward_eps` and `ttm_ebitda` automatically, and adds `fwd_pe` + accurate `trailingEbitda` to each peer. The Multiples lens activates without me touching Claude Desktop. If a yfinance field is unavailable, the lens silently skips that sub-anchor — no error, no overwrite of any value I set manually before."

## Architecture Overview

```
gather_data.py          ← MODIFIED:
  ├── NEW: fetch_market_inputs(ticker) → dict
  │       Returns {forward_eps, ttm_ebitda} (None for missing fields)
  └── NEW: enrich_peer_with_market_data(peer_dict) → dict (mutates copy)
          Adds fwd_pe + replaces ev_ebitda with real trailingEbitda

streamlit_app.py        ← MODIFIED:
  └── _refresh_stale_valuations()
        ├── BEFORE running orchestrator per ticker:
        │     1. Call fetch_market_inputs(ticker), merge into cfg["valuation_inputs"]
        │        respecting `_auto_filled` list (don't overwrite user-set values)
        │     2. For each peer in cfg["peers"], call enrich_peer_with_market_data
        │        respecting `_auto_filled_peers[i]` lists per peer
        ├── THEN: run orchestrator (existing)
        └── THEN: save cfg back to Supabase (existing)

tests/test_market_data.py   ← NEW (~10 tests)
```

The new fetchers are pure (mock yfinance and they're testable). The integration into `_refresh_stale_valuations` is a small extension to its inner `_refresh_one(ticker)` worker function.

## Data Model — `_auto_filled` metadata

Track which fields were auto-populated so subsequent refreshes don't overwrite manual edits.

```python
config["valuation_inputs"] = {
    # User-set or auto-set values
    "forward_eps": float | None,
    "historical_fwd_pe": float | None,
    "ttm_ebitda": float | None,
    "target_dividend_yield": float | None,
    "current_dividend": float | None,
    "expected_dividend_growth": float | None,

    # NEW: auto-fetch metadata
    "_auto_filled": ["forward_eps", "ttm_ebitda"],   # which keys were auto-populated
    "_fetched_at": "2026-05-05T20:30:00+00:00",      # last yfinance refresh, ISO 8601 UTC
}
```

For peers, mirror the metadata at the peer dict level:

```python
config["peers"] = [
    {
        "ticker": "AAPL",
        "name": "Apple",
        "ev_revenue": 9.5,
        "ev_ebitda": 24.3,           # now from real trailingEbitda when auto-fetched
        "pe": 33.5,                  # trailing (unchanged, existing field)
        "fwd_pe": 30.5,              # NEW
        "op_margin": 0.315,
        "rev_growth": 0.05,
        "roic": 0.55,
        "_auto_filled": ["fwd_pe", "ev_ebitda"],   # which keys this auto-fetch wrote
        "_fetched_at": "2026-05-05T20:30:00+00:00",
    },
    ...
]
```

### Auto-fill rule (precedence)

For every field the auto-fetcher wants to write:

1. If the field is **not present** in the dict (or value is `None`) → write the new value, append the key to `_auto_filled`.
2. If the field **is present** AND the key is in `_auto_filled` → overwrite (it was a previous auto-fetch result).
3. If the field **is present** AND the key is NOT in `_auto_filled` → it's a user-set value. **Do not overwrite.** Log INFO ("Skipping auto-fill of forward_eps for ABT: user-set value preserved").
4. If yfinance returns `None` for a field → never overwrite a non-None value with `None`. Drop from `_auto_filled` only if the field stays None after merge.

`_fetched_at` is updated on every successful fetch attempt (even if zero fields wrote — we still know we tried).

### Backward compat

- Configs without `_auto_filled` → treat all existing fields as user-set (don't overwrite).
- Configs without `_fetched_at` → first refresh fills it.
- `_AI_NOTES_GUARDED_KEYS` already protects `valuation_inputs` and `peers` from silent wipes (Phase 1, Task 4). No change needed.

## Fetchers

### `gather_data.fetch_market_inputs(ticker) -> dict`

```python
def fetch_market_inputs(ticker: str) -> dict:
    """Fetch valuation_inputs fields from Yahoo Finance.

    Returns a dict with keys (any may be None when unavailable):
        forward_eps:  Ticker.info["forwardEps"]
        ttm_ebitda:   Ticker.info["trailingEbitda"] / 1e6  (convert $ to $M)

    Network failure / yfinance import failure → returns empty dict and logs warning.
    Never raises.
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except ImportError:
        logger.warning("yfinance not installed; skipping market input fetch for %s", ticker)
        return {}
    except Exception as e:
        logger.warning("yfinance fetch failed for %s: %s", ticker, e)
        return {}

    out = {}
    fwd_eps = info.get("forwardEps")
    if isinstance(fwd_eps, (int, float)) and fwd_eps > 0:
        out["forward_eps"] = round(float(fwd_eps), 2)

    ttm_ebitda_raw = info.get("trailingEbitda")
    if isinstance(ttm_ebitda_raw, (int, float)) and ttm_ebitda_raw > 0:
        out["ttm_ebitda"] = round(float(ttm_ebitda_raw) / 1e6, 0)  # $ → $M

    return out
```

Returns `{}` if neither field is fetchable. The integration code applies the auto-fill precedence rules.

### `gather_data.enrich_peer_with_market_data(peer: dict) -> dict`

```python
def enrich_peer_with_market_data(peer: dict) -> dict:
    """Return a copy of `peer` enriched with yfinance fwd_pe and a real EV/EBITDA
    multiple computed from trailingEbitda (replacing the oi*1.3 approximation).

    Returns the same dict shape as input, with `fwd_pe` added and `ev_ebitda`
    potentially replaced. The original is not mutated.

    Yfinance unavailable / errors → returns peer unchanged.
    """
    out = dict(peer)
    ticker = peer.get("ticker", "")
    if not ticker:
        return out

    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info
    except ImportError:
        logger.warning("yfinance not installed; peer enrich skipped for %s", ticker)
        return out
    except Exception as e:
        logger.warning("yfinance peer enrich failed for %s: %s", ticker, e)
        return out

    fwd_pe = info.get("forwardPE")
    if isinstance(fwd_pe, (int, float)) and fwd_pe > 0:
        out["fwd_pe"] = round(float(fwd_pe), 1)

    # Real EV/EBITDA: enterprise_value / trailingEbitda, both from yfinance
    ev = info.get("enterpriseValue")
    ttm_ebitda = info.get("trailingEbitda")
    if (isinstance(ev, (int, float)) and ev > 0
            and isinstance(ttm_ebitda, (int, float)) and ttm_ebitda > 0):
        out["ev_ebitda"] = round(ev / ttm_ebitda, 1)

    return out
```

`fwd_pe` and `ev_ebitda` are tracked in the peer's `_auto_filled` list by the integration layer (next section). The enricher itself is pure.

## Integration into `_refresh_stale_valuations`

Modify the existing `_refresh_one(ticker)` worker (introduced in Phase 2-A Task 8) to call the fetchers BEFORE the orchestrator runs.

```python
def _refresh_one(ticker):
    cfg = dict(cfgs[ticker])
    cfg.setdefault("ticker", ticker)

    # NEW: auto-fetch market inputs (respect user-set values, track via _auto_filled)
    _auto_fill_valuation_inputs(cfg)
    _auto_fill_peer_market_data(cfg)

    # Existing: run orchestrator and save
    summary = calculate_multi_lens_valuation_remote(cfg)
    cfg["valuation_summary"] = summary
    save_config(client, ticker, cfg, user_id=user_id)
    return ticker
```

Two new private helpers in `streamlit_app.py` (or a small new `auto_fetch.py` module — see "File layout" below):

```python
def _auto_fill_valuation_inputs(cfg: dict) -> None:
    """Mutates cfg['valuation_inputs'] in place. Respects user-set values
    (anything not in _auto_filled). Updates _fetched_at timestamp.
    """
    from datetime import datetime, timezone
    import gather_data

    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    fetched = gather_data.fetch_market_inputs(cfg["ticker"])

    for key, value in fetched.items():
        existing = inputs.get(key)
        if existing is None or key in auto_filled:
            inputs[key] = value
            if key not in auto_filled:
                auto_filled.append(key)
        else:
            logger.info("Auto-fill skipped for %s.%s: user-set value preserved",
                        cfg["ticker"], key)

    inputs["_auto_filled"] = auto_filled
    inputs["_fetched_at"] = datetime.now(timezone.utc).isoformat()


def _auto_fill_peer_market_data(cfg: dict) -> None:
    """For each peer in cfg['peers'], enrich with yfinance fwd_pe + ev_ebitda.
    Respects user-set values per peer (anything not in peer['_auto_filled']).
    """
    from datetime import datetime, timezone
    import gather_data

    peers = cfg.get("peers") or []
    fetched_at = datetime.now(timezone.utc).isoformat()

    for i, peer in enumerate(peers):
        if not isinstance(peer, dict) or not peer.get("ticker"):
            continue

        auto_filled = list(peer.get("_auto_filled", []))
        enriched = gather_data.enrich_peer_with_market_data(peer)

        for key in ("fwd_pe", "ev_ebitda"):
            if key not in enriched:
                continue
            existing = peer.get(key)
            if existing is None or key in auto_filled:
                peer[key] = enriched[key]
                if key not in auto_filled:
                    auto_filled.append(key)
            else:
                logger.info("Auto-fill skipped for %s peer %s.%s: user-set value preserved",
                            cfg["ticker"], peer["ticker"], key)

        peer["_auto_filled"] = auto_filled
        peer["_fetched_at"] = fetched_at
```

## File Layout

Two options:

**A. Inline in `streamlit_app.py`** — `_auto_fill_*` helpers live next to `_refresh_stale_valuations`. The pure fetchers go to `gather_data.py`. **Recommended.** Keeps Streamlit-specific orchestration together; the testable fetchers stay pure.

**B. New `auto_fetch.py` module** — extract the orchestration helpers from `streamlit_app.py` into a dedicated module. Cleaner separation. Slight overhead for a ~80-line addition.

Going with A. The `_auto_fill_*` helpers are inherently coupled to the refresh-flow context (logging, config dict shape) and only get called from one place. New module only pays off if Phase 2-C+ reuses them.

## Concurrency

`_refresh_stale_valuations` already uses `ThreadPoolExecutor(max_workers=6)`. Each worker now does ~2-7 yfinance calls (1 for ticker + 1 per peer, peers usually 6). With a 21-ticker watchlist that's ~150 yfinance calls per refresh. yfinance does not enforce a hard rate limit; in practice 6 parallel workers is safe.

For safety against partial throttling, peers within a ticker are fetched **sequentially** (the for-loop in `_auto_fill_peer_market_data`). Tickers across the watchlist are parallel via the existing executor. Total wall-clock estimate: ~10-25s for a full 21-ticker refresh.

## Errors, Skips, Logging

- **yfinance import error** → log warning once, return empty dict. Refresh continues without auto-fill.
- **Network failure / generic yfinance exception** → caught per call, log warning with ticker, return empty dict.
- **Field returns `None` or invalid type** → skip silently (don't add to result dict).
- **Per-ticker error in `_refresh_one`** → already wrapped by the existing `as_completed` loop's try/except. The auto-fill helpers should not raise; if they do, the ticker is added to the existing `errors` list.
- **Logger** → reuse the module-level `logger` already defined in `streamlit_app.py` and `gather_data.py`.

## Tests

`tests/test_market_data.py` — new file, all yfinance calls mocked.

1. `test_fetch_market_inputs_happy_path` — mock `yf.Ticker(...).info` returns `{"forwardEps": 5.48, "trailingEbitda": 11_800_000_000}`; assert `{"forward_eps": 5.48, "ttm_ebitda": 11800.0}`.
2. `test_fetch_market_inputs_missing_fields` — mock returns `{}` → fetcher returns `{}`.
3. `test_fetch_market_inputs_partial` — mock returns only `{"forwardEps": 5.48}` → fetcher returns `{"forward_eps": 5.48}` only.
4. `test_fetch_market_inputs_zero_or_negative_skipped` — mock returns `{"forwardEps": 0, "trailingEbitda": -100}` → fetcher returns `{}`.
5. `test_fetch_market_inputs_yfinance_error` — mock raises Exception → fetcher returns `{}` and logs warning.
6. `test_enrich_peer_happy_path` — mock returns `{"forwardPE": 30.5, "enterpriseValue": 3.5e12, "trailingEbitda": 145e9}` → peer gets `fwd_pe=30.5`, `ev_ebitda=24.1`.
7. `test_enrich_peer_no_ticker_returns_unchanged` — peer dict without `ticker` → returns copy unchanged.
8. `test_auto_fill_respects_user_set_values` — `valuation_inputs = {"forward_eps": 5.48}` (not in `_auto_filled`) + yfinance returns `5.50` → final value stays `5.48`, log message recorded.
9. `test_auto_fill_overwrites_previous_auto_value` — `valuation_inputs = {"forward_eps": 5.40, "_auto_filled": ["forward_eps"]}` + yfinance returns `5.50` → final value is `5.50`.
10. `test_auto_fill_doesnt_overwrite_with_none` — existing `forward_eps = 5.48` (in `_auto_filled`) + yfinance returns no `forward_eps` field → existing `5.48` preserved.
11. `test_auto_fill_peer_respects_user_set_values` — same logic but at the peer level.
12. `test_round_trip_in_refresh` — mock yfinance + run `_refresh_stale_valuations` on a 1-ticker fixture; assert `valuation_inputs` and peers are filled, summary is computed, `_fetched_at` set.

Run: `python3 -m pytest tests/test_market_data.py -v`.

## Migration & Backward Compatibility

- All changes additive. No DB schema changes (everything in the existing `config` JSONB).
- Configs without `_auto_filled` keys: first refresh treats existing values as user-set (does not overwrite).
- Configs from before this PR keep working unchanged.
- The fetchers gracefully degrade if `yfinance` is unavailable.

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| yfinance API breaks / changes field names | All field accesses use `.get()`; failure path returns empty. The system stays functional with the user manually entering values via Claude Desktop. |
| Yahoo rate-limits during a 150-call refresh | yfinance has been used in this project for months without rate-limit issues. If it ever happens, errors surface in the Refresh-all summary; the user can re-click after a few seconds. |
| Auto-fetch overwrites legitimate user override | The `_auto_filled` list explicitly protects user-set values. Tested in `test_auto_fill_respects_user_set_values`. |
| `enrichPeer` rounds `ev_ebitda` differently from existing values, surprising old configs | The first refresh after this PR replaces existing peer `ev_ebitda` (which is the `oi*1.3` approximation, never user-set) with the real one. This is a deliberate quality improvement, documented as "real EV/EBITDA replaces the approximation on first refresh." |
| Performance regression on Refresh all | +1 yfinance call per ticker + 1 per peer × 6 peers = ~7 calls/ticker. With 6-worker parallelism and 21 tickers: total wall time goes from ~3-8s (Phase 2-A) to ~10-25s. Acceptable for a once-per-week action. |

## Acceptance Criteria

1. Clicking ↻ Refresh all on a watchlist ticker that has no `valuation_inputs` populates `forward_eps` + `ttm_ebitda` from yfinance and adds `_auto_filled = ["forward_eps", "ttm_ebitda"]` to that ticker's config.
2. Each peer in that ticker's config gets a `fwd_pe` value (when available) and an updated `ev_ebitda` (real, from yfinance) with `_auto_filled = ["fwd_pe", "ev_ebitda"]` (or subset).
3. After refresh, the Multiples lens activates: lens-dots show 3 active lenses, range-bar shows a wider FV range than DCF-only.
4. If a user manually edits `valuation_inputs.forward_eps` to a custom value (without it being in `_auto_filled`), the next Refresh all preserves that custom value.
5. If yfinance is unavailable for a ticker, `valuation_summary` still computes (DCF + reverse_dcf), with a warning logged. No crash.
6. All 12 tests in `tests/test_market_data.py` pass. Full pytest suite stays green (no regressions).
7. `python3 -m ruff check .` clean for new files.

## Implementation Order

1. Add `fetch_market_inputs` and `enrich_peer_with_market_data` to `gather_data.py`. Tests 1-7.
2. Add `_auto_fill_valuation_inputs` and `_auto_fill_peer_market_data` to `streamlit_app.py`. Tests 8-11.
3. Wire both into `_refresh_one(ticker)` inside `_refresh_stale_valuations`. Test 12 (round-trip).
4. Lint + full regression suite.
5. Local visual review on dev server with one or two real tickers.
