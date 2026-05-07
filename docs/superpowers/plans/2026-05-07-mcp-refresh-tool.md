# MCP Refresh Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `_auto_fill_*` helpers to a shared `auto_fetch.py` module, fix MCP `calculate_multi_lens_valuation` to call them, and add a new `refresh_all_valuations(force=False)` MCP tool.

**Architecture:** New `auto_fetch.py` module exposes `auto_fill_valuation_inputs(cfg)` and `auto_fill_peer_market_data(cfg)` (byte-for-byte the existing logic). `streamlit_app.py` re-exports them with the underscore-prefix names so existing call sites stay intact. `mcp_server.py` imports `auto_fetch` directly, calls the helpers before the orchestrator in the existing per-ticker tool, and exposes a new batch tool.

**Tech Stack:** Python 3.11, pytest, ruff, FastMCP, ThreadPoolExecutor (already in use).

**Spec:** `docs/superpowers/specs/2026-05-07-mcp-refresh-tool-design.md`

---

## File Map

| Path | Purpose | Action |
|------|---------|--------|
| `auto_fetch.py` | New module with two public helpers | Create |
| `streamlit_app.py` | Replace local helpers with re-exports from `auto_fetch` | Modify |
| `mcp_server.py` | Wire auto-fetch into per-ticker tool; add batch tool | Modify |
| `tests/test_market_data.py` | Move 9 helper tests to new patch target; add 4 new tests | Modify |
| `scripts/force_refresh_all.py` | (Optional Task 6) simplify to import auto_fetch | Modify |

---

### Task 1: Create `auto_fetch.py` module

**Files:**
- Create: `auto_fetch.py`

- [ ] **Step 1: Create the module with both helpers**

Create `auto_fetch.py` with this content (byte-for-byte the existing helper bodies, just relocated and renamed without underscore prefix):

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
    Fields listed in `_auto_filled` or absent are written; user-set fields
    (present, not in _auto_filled) are preserved. Updates `_fetched_at`.
    """
    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    fetched = gather_data.fetch_market_inputs(cfg.get("ticker", ""))
    fetched.update(gather_data.fetch_historical_multiples(cfg.get("ticker", "")))

    for key, value in fetched.items():
        existing = inputs.get(key)
        if existing is None or key in auto_filled:
            inputs[key] = value
            if key not in auto_filled:
                auto_filled.append(key)
        else:
            logger.info(
                "Auto-fill skipped for %s.%s: user-set value preserved",
                cfg.get("ticker", "?"), key,
            )

    inputs["_auto_filled"] = auto_filled
    inputs["_fetched_at"] = datetime.now(UTC).isoformat()


def auto_fill_peer_market_data(cfg: dict) -> None:
    """Auto-fill yfinance fwd_pe and real ev_ebitda for each peer in cfg["peers"].

    fwd_pe: user-set values (present, not in _auto_filled) are preserved.
    ev_ebitda: ALWAYS overwritten when yfinance provides real data. This is an
    intentional Phase-2-B limitation: the existing values come from
    gather_data.fetch_peer_data's oi*1.3 approximation and are never marked
    as _auto_filled, so the standard precedence rule would treat them as
    user-set. To keep the workflow simple we always replace them with the real
    yfinance value.

    Updates peer["_fetched_at"]. Non-dict or ticker-less peers are skipped.
    """
    peers = cfg.get("peers") or []
    fetched_at = datetime.now(UTC).isoformat()

    for peer in peers:
        if not isinstance(peer, dict) or not peer.get("ticker"):
            continue

        auto_filled = list(peer.get("_auto_filled", []))
        enriched = gather_data.enrich_peer_with_market_data(peer)

        for key in ("fwd_pe", "ev_ebitda"):
            yfinance_value = enriched.get(key)
            if yfinance_value is None:
                continue
            original_value = peer.get(key)
            if key == "ev_ebitda" or original_value is None or key in auto_filled:
                peer[key] = yfinance_value
                if key not in auto_filled:
                    auto_filled.append(key)
            else:
                logger.info(
                    "Auto-fill skipped for %s peer %s.%s: user-set value preserved",
                    cfg.get("ticker", "?"), peer["ticker"], key,
                )

        peer["_auto_filled"] = auto_filled
        peer["_fetched_at"] = fetched_at
```

- [ ] **Step 2: Smoke-import**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -c "import auto_fetch; print(auto_fetch.auto_fill_valuation_inputs.__name__, auto_fetch.auto_fill_peer_market_data.__name__)"`
Expected: `auto_fill_valuation_inputs auto_fill_peer_market_data`.

- [ ] **Step 3: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check auto_fetch.py`
Expected: All checks passed.

- [ ] **Step 4: Commit**

```bash
git add auto_fetch.py
git commit -m "feat: extract auto_fill_* helpers into shared auto_fetch module"
```

---

### Task 2: Streamlit re-exports + verify watchlist tests still green

**Files:**
- Modify: `streamlit_app.py:222-310` (the two `_auto_fill_*` function definitions)

- [ ] **Step 1: Locate the existing definitions**

Run: `cd /Users/administrator/Documents/github/stock-analysis && grep -n "^def _auto_fill_valuation_inputs\|^def _auto_fill_peer_market_data" streamlit_app.py`
Expected: two line numbers (around 222 and 255).

- [ ] **Step 2: Replace both function definitions with imports**

In `streamlit_app.py`, find the block starting at `def _auto_fill_valuation_inputs(cfg: dict) -> None:` and ending at the closing of `_auto_fill_peer_market_data` (after the inner for-loop assigns `peer["_fetched_at"] = fetched_at`). Use `Read` first to confirm the exact range — the block ends just before the next top-level `def` (likely `_refresh_stale_valuations` or `calculate_multi_lens_valuation_remote`).

Replace the entire block (both function bodies) with:

```python
# Auto-fill helpers live in auto_fetch (shared with mcp_server). Re-exported
# under their underscore-prefixed names so existing call sites in this file
# (and tests that monkey-patch streamlit_app._auto_fill_*) keep working.
from auto_fetch import (
    auto_fill_peer_market_data as _auto_fill_peer_market_data,
    auto_fill_valuation_inputs as _auto_fill_valuation_inputs,
)
```

- [ ] **Step 3: Smoke-import**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -c "import streamlit_app; print(streamlit_app._auto_fill_valuation_inputs)"`
Expected: prints the function (any pre-existing `KeyError: supabase_client` is the long-standing import-side-effect, not new).

- [ ] **Step 4: Run watchlist UI tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -v 2>&1 | tail -10`
Expected: 29 passed (no regressions; tests patch `gather_data.*` not `streamlit_app._auto_fill_*` so the change is invisible to them).

- [ ] **Step 5: Run market_data tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -v 2>&1 | tail -10`
Expected: 31 passed. The 9 tests that call `streamlit_app._auto_fill_*` should still work because the re-exports are functionally identical to the originals.

- [ ] **Step 6: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check streamlit_app.py`
Expected: no new violations.

- [ ] **Step 7: Commit**

```bash
git add streamlit_app.py
git commit -m "refactor: streamlit_app re-exports auto_fill_* from auto_fetch"
```

---

### Task 3: Move auto_fill_* tests to patch `auto_fetch` directly

**Files:**
- Modify: `tests/test_market_data.py`

The 9 existing auto_fill_* tests currently call `streamlit_app._auto_fill_*(cfg)`. They keep working through the re-export but the patch target should ideally point at the new home so tests are decoupled from the Streamlit import path.

- [ ] **Step 1: Locate the affected tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && grep -n "streamlit_app._auto_fill_valuation_inputs\|streamlit_app._auto_fill_peer_market_data" tests/test_market_data.py`
Expected: ~9 occurrences across the test bodies.

- [ ] **Step 2: Add `import auto_fetch` near the existing test imports**

Find the `import streamlit_app` line in `tests/test_market_data.py` (likely near the top after the fixtures). Add directly below it:

```python
import auto_fetch
```

- [ ] **Step 3: Replace each `streamlit_app._auto_fill_*` call**

Use `Read` to inspect each test, then `Edit` to replace:

- `streamlit_app._auto_fill_valuation_inputs(cfg)` → `auto_fetch.auto_fill_valuation_inputs(cfg)`
- `streamlit_app._auto_fill_peer_market_data(cfg)` → `auto_fetch.auto_fill_peer_market_data(cfg)`

Tests that should be updated (each contains one of those calls):
- `test_auto_fill_inputs_populates_empty`
- `test_auto_fill_inputs_respects_user_set_value`
- `test_auto_fill_inputs_overwrites_previous_auto_value`
- `test_auto_fill_inputs_doesnt_overwrite_with_none`
- `test_auto_fill_inputs_fetched_at_always_set`
- `test_auto_fill_peer_populates_empty`
- `test_auto_fill_peer_respects_user_set_value`
- `test_auto_fill_peer_skips_invalid_entries`
- `test_auto_fill_inputs_includes_historical_multiples`

Also: any test bodies that have `with patch.object(streamlit_app, "save_config", ...)` for the round-trip refresh test stay unchanged — that test patches `_refresh_stale_valuations`'s save dependency, not the helpers themselves.

- [ ] **Step 4: Run market_data tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -v 2>&1 | tail -10`
Expected: 31 passed (no count change; just a different patch target).

- [ ] **Step 5: Run watchlist UI tests for sanity**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -v 2>&1 | tail -3`
Expected: 29 passed.

- [ ] **Step 6: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check tests/test_market_data.py`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add tests/test_market_data.py
git commit -m "test: route auto_fill_* test calls through auto_fetch module"
```

---

### Task 4: MCP per-ticker tool calls auto-fetch before orchestrator

**Files:**
- Modify: `mcp_server.py` — `_calculate_multi_lens_valuation_impl` (locate with `grep -n "^def _calculate_multi_lens_valuation_impl" mcp_server.py`)
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_market_data.py`:

```python
def test_mcp_calculate_multi_lens_valuation_does_auto_fetch():
    """The MCP per-ticker tool runs auto_fetch helpers before the orchestrator,
    so saved valuation_inputs include forward_eps, ttm_ebitda, and the
    historical multiples — fixing the NFLX-incident gap."""
    import json as _json
    import mcp_server
    from unittest.mock import patch as _patch, MagicMock as _Mock

    # Minimal config that compute_intrinsic_value can run on
    cfg_in = {
        "ticker": "NFLX",
        "company": "Netflix",
        "stock_price": 680.0,
        "equity_market_value": 300_000,
        "debt_market_value": 15_000,
        "risk_free_rate": 0.04,
        "erp": 0.05,
        "credit_spread": 0.01,
        "tax_rate": 0.21,
        "sector_betas": [("Internet", 1.1, 1.0)],
        "base_revenue": 35_000,
        "revenue_growth": [0.10] * 5,
        "op_margins": [0.22] * 5,
        "terminal_growth": 0.025,
        "terminal_margin": 0.20,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.03,
        "shares_outstanding": 430,
        "buyback_rate": 0.0,
        "margin_of_safety": 0.20,
        "cash_bridge": 8_000,
        "securities": 0,
        "bull_growth_adj": 0.02,
        "bear_growth_adj": -0.04,
        "bull_margin_adj": 0.02,
        "bear_margin_adj": -0.02,
        "peers": [],
    }
    saved_cfg_holder = {}
    yfinance_info = {
        "forwardEps": 3.84, "trailingEbitda": 14_000_000_000,
        "trailingEps": 3.10, "sharesOutstanding": 430_000_000,
    }

    def fake_save(client, ticker, cfg, user_id=None):
        saved_cfg_holder["cfg"] = dict(cfg)

    with _patch.object(mcp_server, "get_supabase_client", lambda: _Mock()), \
         _patch.object(mcp_server.config_store, "load_config", return_value=cfg_in), \
         _patch.object(mcp_server.config_store, "save_config", side_effect=fake_save), \
         _patch.object(mcp_server, "USER_ID", "u1"), \
         patch_yfinance_full(info=yfinance_info):
        result_json = mcp_server._calculate_multi_lens_valuation_impl(
            "NFLX", scenario_grid=False
        )

    summary = _json.loads(result_json)
    assert "weighted_fv_mid" in summary

    saved_inputs = saved_cfg_holder["cfg"]["valuation_inputs"]
    # Auto-fetch wrote forward_eps from yfinance.forwardEps
    assert saved_inputs.get("forward_eps") == 3.84
    # Auto-fetch wrote ttm_ebitda from yfinance.trailingEbitda (in $M)
    assert saved_inputs.get("ttm_ebitda") == 14_000.0
    # Phase 2-B.2 fields populated from fetch_historical_multiples
    assert saved_inputs.get("ttm_eps") == 3.10
```

- [ ] **Step 2: Run test — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_mcp_calculate_multi_lens_valuation_does_auto_fetch -v`
Expected: FAIL — `valuation_inputs` is empty in `saved_cfg` because the MCP impl doesn't call auto-fetch yet.

- [ ] **Step 3: Modify `_calculate_multi_lens_valuation_impl`**

Locate the existing impl with `grep -n "^def _calculate_multi_lens_valuation_impl" mcp_server.py`. Find this body:

```python
def _calculate_multi_lens_valuation_impl(ticker, scenario_grid=False):
    """Core logic for calculate_multi_lens_valuation: load cfg, run all
    lenses, persist summary, return JSON."""
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})

    summary = valuation_lenses.calculate_multi_lens_valuation(
        cfg, scenario_grid=scenario_grid
    )
    cfg["valuation_summary"] = summary
    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return json.dumps(summary, default=str)
```

Replace with (adds the two auto-fetch calls before the orchestrator):

```python
def _calculate_multi_lens_valuation_impl(ticker, scenario_grid=False):
    """Core logic for calculate_multi_lens_valuation: load cfg, auto-fetch
    yfinance market data + historical multiples, run all lenses, persist
    summary, return JSON."""
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})

    # Auto-fetch yfinance market data + historical multiples before the
    # orchestrator. Matches Streamlit's _refresh_one. Best-effort: yfinance
    # failures don't block the lens computation.
    cfg.setdefault("ticker", ticker)
    auto_fetch.auto_fill_valuation_inputs(cfg)
    auto_fetch.auto_fill_peer_market_data(cfg)

    summary = valuation_lenses.calculate_multi_lens_valuation(
        cfg, scenario_grid=scenario_grid
    )
    cfg["valuation_summary"] = summary
    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return json.dumps(summary, default=str)
```

Also add the import near the existing module-level imports in `mcp_server.py` (next to `import valuation_lenses`):

```python
import auto_fetch
```

Verify the existing imports with `grep -n "^import valuation_lenses\|^import config_store" mcp_server.py`.

- [ ] **Step 4: Run test — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_mcp_calculate_multi_lens_valuation_does_auto_fetch -v`
Expected: PASS.

- [ ] **Step 5: Run round-trip test from earlier phases**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py::test_round_trip_calculate_and_persist tests/test_market_data.py::test_refresh_one_calls_auto_fill_before_orchestrator -v 2>&1 | tail -10`
Expected: both pass — they exercise the multi-lens flow end-to-end.

- [ ] **Step 6: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check mcp_server.py tests/test_market_data.py`
Expected: no NEW violations.

- [ ] **Step 7: Commit**

```bash
git add mcp_server.py tests/test_market_data.py
git commit -m "feat: MCP calculate_multi_lens_valuation now auto-fetches yfinance data"
```

---

### Task 5: New `refresh_all_valuations` MCP tool

**Files:**
- Modify: `mcp_server.py`
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write 3 failing tests**

Append to `tests/test_market_data.py`:

```python
def test_refresh_all_valuations_force_true_processes_everything():
    """force=True processes every ticker including fresh ones."""
    import json as _json
    import mcp_server
    from unittest.mock import patch as _patch, MagicMock as _Mock
    from datetime import datetime as _dt, UTC as _UTC

    fresh_ts = _dt.now(_UTC).isoformat()
    cfgs = {
        "AAPL": {
            "ticker": "AAPL", "company": "Apple", "stock_price": 100.0,
            "equity_market_value": 100_000, "debt_market_value": 10_000,
            "risk_free_rate": 0.04, "erp": 0.05, "credit_spread": 0.01,
            "tax_rate": 0.21, "sector_betas": [("Tech", 1.1, 1.0)],
            "base_revenue": 50_000, "revenue_growth": [0.05] * 5,
            "op_margins": [0.20] * 5, "terminal_growth": 0.025,
            "terminal_margin": 0.18, "sales_to_capital": 1.5, "sbc_pct": 0.02,
            "shares_outstanding": 1_000, "buyback_rate": 0.0,
            "margin_of_safety": 0.20, "cash_bridge": 5_000, "securities": 0,
            "bull_growth_adj": 0.02, "bear_growth_adj": -0.04,
            "bull_margin_adj": 0.02, "bear_margin_adj": -0.02,
            "peers": [],
            "valuation_summary": {"calculated_at": fresh_ts, "weighted_fv_mid": 99.0},
        },
    }

    def fake_load(client, ticker, user_id=None):
        return dict(cfgs[ticker])

    def fake_save(client, ticker, cfg, user_id=None):
        cfgs[ticker] = dict(cfg)

    def fake_list(client, user_id=None):
        return [{"ticker": t} for t in cfgs]

    with _patch.object(mcp_server, "get_supabase_client", lambda: _Mock()), \
         _patch.object(mcp_server.config_store, "load_config", side_effect=fake_load), \
         _patch.object(mcp_server.config_store, "save_config", side_effect=fake_save), \
         _patch.object(mcp_server.config_store, "list_watchlist", side_effect=fake_list), \
         _patch.object(mcp_server, "USER_ID", "u1"), \
         patch_yfinance_full(info={"trailingEps": 5.0, "sharesOutstanding": 1_000_000_000}):
        result = _json.loads(mcp_server._refresh_all_valuations_impl(force=True))

    assert result["computed"] == ["AAPL"]
    assert result["skipped"] == []
    assert result["errors"] == []


def test_refresh_all_valuations_default_skips_fresh():
    """force=False (default) skips tickers whose summary is < 7 days old."""
    import json as _json
    import mcp_server
    from unittest.mock import patch as _patch, MagicMock as _Mock
    from datetime import datetime as _dt, UTC as _UTC

    fresh_ts = _dt.now(_UTC).isoformat()
    cfgs = {
        "FRESH": {
            "ticker": "FRESH",
            "valuation_summary": {"calculated_at": fresh_ts, "weighted_fv_mid": 50.0},
        },
        "EMPTY": {"ticker": "EMPTY"},
    }

    def fake_load(client, ticker, user_id=None):
        return dict(cfgs[ticker])

    def fake_save(client, ticker, cfg, user_id=None):
        cfgs[ticker] = dict(cfg)

    def fake_list(client, user_id=None):
        return [{"ticker": t} for t in cfgs]

    def fake_calc(cfg, scenario_grid=False):
        return {"calculated_at": _dt.now(_UTC).isoformat(), "weighted_fv_mid": 99.0,
                "stock_price": 100.0, "lenses": {}}

    with _patch.object(mcp_server, "get_supabase_client", lambda: _Mock()), \
         _patch.object(mcp_server.config_store, "load_config", side_effect=fake_load), \
         _patch.object(mcp_server.config_store, "save_config", side_effect=fake_save), \
         _patch.object(mcp_server.config_store, "list_watchlist", side_effect=fake_list), \
         _patch.object(mcp_server.valuation_lenses, "calculate_multi_lens_valuation", side_effect=fake_calc), \
         _patch.object(mcp_server, "USER_ID", "u1"), \
         _patch("auto_fetch.gather_data.fetch_market_inputs", return_value={}), \
         _patch("auto_fetch.gather_data.fetch_historical_multiples", return_value={}), \
         _patch("auto_fetch.gather_data.enrich_peer_with_market_data", side_effect=lambda p: dict(p)):
        result = _json.loads(mcp_server._refresh_all_valuations_impl(force=False))

    assert "EMPTY" in result["computed"]
    assert "FRESH" in result["skipped"]


def test_refresh_all_valuations_per_ticker_error_isolated():
    """One ticker raising during compute doesn't kill the others."""
    import json as _json
    import mcp_server
    from unittest.mock import patch as _patch, MagicMock as _Mock
    from datetime import datetime as _dt, UTC as _UTC

    cfgs = {"GOOD": {"ticker": "GOOD"}, "BAD": {"ticker": "BAD"}}

    def fake_load(client, ticker, user_id=None):
        return dict(cfgs[ticker])

    def fake_save(client, ticker, cfg, user_id=None):
        cfgs[ticker] = dict(cfg)

    def fake_list(client, user_id=None):
        return [{"ticker": t} for t in cfgs]

    def fake_calc(cfg, scenario_grid=False):
        if cfg.get("ticker") == "BAD":
            raise ValueError("boom")
        return {"calculated_at": _dt.now(_UTC).isoformat(), "weighted_fv_mid": 50.0,
                "stock_price": 100.0, "lenses": {}}

    with _patch.object(mcp_server, "get_supabase_client", lambda: _Mock()), \
         _patch.object(mcp_server.config_store, "load_config", side_effect=fake_load), \
         _patch.object(mcp_server.config_store, "save_config", side_effect=fake_save), \
         _patch.object(mcp_server.config_store, "list_watchlist", side_effect=fake_list), \
         _patch.object(mcp_server.valuation_lenses, "calculate_multi_lens_valuation", side_effect=fake_calc), \
         _patch.object(mcp_server, "USER_ID", "u1"), \
         _patch("auto_fetch.gather_data.fetch_market_inputs", return_value={}), \
         _patch("auto_fetch.gather_data.fetch_historical_multiples", return_value={}), \
         _patch("auto_fetch.gather_data.enrich_peer_with_market_data", side_effect=lambda p: dict(p)):
        result = _json.loads(mcp_server._refresh_all_valuations_impl(force=True))

    assert "GOOD" in result["computed"]
    assert any("BAD" in e for e in result["errors"])
```

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k refresh_all_valuations -v`
Expected: 3 FAIL — `AttributeError: module 'mcp_server' has no attribute '_refresh_all_valuations_impl'`.

- [ ] **Step 3: Add the impl + decorated tool to `mcp_server.py`**

After `_calculate_multi_lens_valuation_impl` (around line 240 after Task 4's modifications), add:

```python
def _refresh_all_valuations_impl(force: bool = False) -> str:
    """Run multi-lens fair value across all watchlist tickers in parallel.

    Stale = no valuation_summary OR calculated_at older than 7 days OR
    unparseable. Stale tickers get auto-fetched from yfinance + orchestrator
    + saved. Fresh tickers are skipped unless force=True.

    Returns JSON {computed: [...], errors: [...], skipped: [...]}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import UTC, datetime, timedelta

    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=USER_ID)
    tickers = [e["ticker"] for e in entries]

    threshold = datetime.now(UTC) - timedelta(days=7)

    def _is_stale(cfg: dict) -> bool:
        s = cfg.get("valuation_summary") if isinstance(cfg, dict) else None
        if not s:
            return True
        ts_str = s.get("calculated_at")
        if not ts_str:
            return True
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
        except (ValueError, AttributeError):
            return True
        return ts < threshold

    # Load configs in parallel and decide stale set
    def _load(t):
        c = config_store.load_config(client, t, user_id=USER_ID)
        return (t, c) if c is not None else None

    with ThreadPoolExecutor(max_workers=6) as pool:
        loaded = {r[0]: r[1] for r in pool.map(_load, tickers) if r}

    targets = list(loaded.keys()) if force else [t for t, c in loaded.items() if _is_stale(c)]
    skipped = [t for t in loaded if t not in targets]

    computed: list[str] = []
    errors: list[str] = []

    def _refresh_one(ticker: str) -> str:
        cfg = dict(loaded[ticker])
        cfg.setdefault("ticker", ticker)
        auto_fetch.auto_fill_valuation_inputs(cfg)
        auto_fetch.auto_fill_peer_market_data(cfg)
        summary = valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid=False)
        cfg["valuation_summary"] = summary
        config_store.save_config(client, ticker, cfg, user_id=USER_ID)
        return ticker

    if targets:
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_refresh_one, t): t for t in targets}
            for future in as_completed(futures):
                t = futures[future]
                try:
                    future.result()
                    computed.append(t)
                except Exception as e:
                    logger.warning("Refresh failed for %s: %s", t, e)
                    errors.append(f"{t}: {e}")

    return json.dumps({"computed": computed, "errors": errors, "skipped": skipped})
```

Then add the decorated tool below `calculate_multi_lens_valuation` (around line 360 after Task 4):

```python
@mcp.tool()
def refresh_all_valuations(force: bool = False) -> str:
    """Refresh multi-lens fair value for the entire watchlist in one call.

    Stale = no valuation_summary OR calculated_at older than 7 days OR
    unparseable. Stale tickers get auto-fetched from yfinance + orchestrator
    + saved. Fresh tickers are skipped unless force=True.

    Use this after editing peers/inputs across multiple tickers, or after
    a long period without refresh, to bring the watchlist's fair-value
    range back in sync with current yfinance data.

    Args:
        force: When True, recompute every ticker regardless of freshness.
            Default False uses the same 7-day stale-check as the Streamlit
            "↻ Refresh all" button.

    Returns:
        JSON with three keys:
            computed: list of tickers successfully refreshed
            errors: list of "TICKER: error" strings
            skipped: list of tickers that were fresh and not forced
    """
    try:
        return _refresh_all_valuations_impl(force)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k refresh_all_valuations -v`
Expected: 3 passed.

- [ ] **Step 5: Run full test_market_data.py suite**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -v 2>&1 | tail -10`
Expected: 35 passed (31 + 1 from Task 4 + 3 new).

- [ ] **Step 6: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check mcp_server.py tests/test_market_data.py`
Expected: no new violations.

- [ ] **Step 7: Commit**

```bash
git add mcp_server.py tests/test_market_data.py
git commit -m "feat: MCP refresh_all_valuations(force=False) batch tool"
```

---

### Task 6: (Optional) Simplify `scripts/force_refresh_all.py` to import auto_fetch

**Files:**
- Modify: `scripts/force_refresh_all.py`

This task is optional — the script keeps working without changes. It's a cleanup that drops ~30 lines of mirrored helper code by importing the shared module.

- [ ] **Step 1: Locate the mirror functions**

Run: `cd /Users/administrator/Documents/github/stock-analysis && grep -n "^def auto_fill_valuation_inputs\|^def auto_fill_peer_market_data" scripts/force_refresh_all.py`
Expected: two line numbers.

- [ ] **Step 2: Replace mirror functions with import**

Find the two function definitions (`auto_fill_valuation_inputs` and `auto_fill_peer_market_data`) inside the script. Use `Read` to identify the start and end of each block (each is ~20 lines).

Replace both function blocks with a single import line right after the `import valuation_lenses` import near the top:

```python
from auto_fetch import auto_fill_valuation_inputs, auto_fill_peer_market_data
```

Inside the `refresh_one(ticker)` function, the call sites already use `auto_fill_valuation_inputs(cfg)` / `auto_fill_peer_market_data(cfg)` (no `_` prefix), so no body changes required.

- [ ] **Step 3: Run the script as a smoke check**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 scripts/force_refresh_all.py 2>&1 | tail -5`
Expected: same output shape as before — 21 tickers, ~12-15s wall-clock, 0 errors.

(If you don't want to hit yfinance/Supabase from this task, skip the smoke check; the next force-refresh whenever you do one will validate.)

- [ ] **Step 4: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check scripts/force_refresh_all.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add scripts/force_refresh_all.py
git commit -m "refactor: force_refresh_all script imports auto_fetch instead of mirroring logic"
```

---

### Task 7: Lint + full regression suite

**Files:**
- None (verification only)

- [ ] **Step 1: Lint over the whole repo**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check .`
Expected: All checks pass on `auto_fetch.py`, `mcp_server.py` changes, `streamlit_app.py` changes, `tests/test_market_data.py`. Pre-existing violations elsewhere are not this PR's responsibility.

If new violations were introduced, fix them inline. Re-run until clean (modulo pre-existing).

- [ ] **Step 2: Full regression suite**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m pytest test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py tests/test_watchlist_ui.py tests/test_market_data.py 2>&1 | tail -3
```
Expected: 41 + 40 + 32 + 29 + 35 = **177 tests passed**.

- [ ] **Step 3: Smoke test the MCP from CLI**

Run a one-shot Python check that imports mcp_server (without launching the stdio loop) and invokes the impl directly to verify no import errors:

```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -c "
import os, json
env = json.load(open('/Users/administrator/Library/Application Support/Claude/claude_desktop_config.json'))['mcpServers']['lazytheta-dcf']['env']
for k, v in env.items(): os.environ[k] = v
import mcp_server
print('mcp_server imports OK')
print('Has _refresh_all_valuations_impl:', hasattr(mcp_server, '_refresh_all_valuations_impl'))
"
```

Expected: `mcp_server imports OK` and `Has _refresh_all_valuations_impl: True`. Don't actually call the impl in this smoke step — that hits yfinance/Supabase live and would do a real refresh.

- [ ] **Step 4: Optional — invoke the new MCP tool via Claude Desktop after merge**

After this branch is merged and Claude Desktop's MCP server reloaded (cmd+Q + reopen Claude Desktop), test in Claude Desktop:

> "Use the refresh_all_valuations tool with force=True"

Expected: ~12-15s response containing `{"computed": [...21 tickers], "errors": [], "skipped": []}`.

- [ ] **Step 5: Commit (only if you fixed any lint issues in Step 1)**

If Step 1 required edits, commit them:
```bash
git add auto_fetch.py mcp_server.py streamlit_app.py tests/test_market_data.py
git commit -m "style: lint fixes for MCP refresh tool"
```

If no edits were needed, skip this step (no commit).

---

## Summary of Commits (target sequence)

1. `feat: extract auto_fill_* helpers into shared auto_fetch module`
2. `refactor: streamlit_app re-exports auto_fill_* from auto_fetch`
3. `test: route auto_fill_* test calls through auto_fetch module`
4. `feat: MCP calculate_multi_lens_valuation now auto-fetches yfinance data`
5. `feat: MCP refresh_all_valuations(force=False) batch tool`
6. (optional) `refactor: force_refresh_all script imports auto_fetch instead of mirroring logic`
7. (optional) `style: lint fixes for MCP refresh tool`

5-7 commits. Implementation should take ~45 minutes via subagents — most tasks are mechanical code-relocation + small additions.
