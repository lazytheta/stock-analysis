# Phase 2-B: Auto-Fetch Market Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-populate `valuation_inputs.{forward_eps, ttm_ebitda}` and per-peer `fwd_pe` + accurate `ev_ebitda` via yfinance, bundled into the existing Refresh-all flow. Respects user-set values via an `_auto_filled` metadata list.

**Architecture:** Two new pure fetchers in `gather_data.py` (`fetch_market_inputs`, `enrich_peer_with_market_data`). Two new private helpers in `streamlit_app.py` (`_auto_fill_valuation_inputs`, `_auto_fill_peer_market_data`) that wrap the fetchers with the user-override-respecting precedence rules. The existing `_refresh_one(ticker)` worker calls them before the orchestrator.

**Tech Stack:** Python 3.11, pytest, ruff, yfinance (already a dependency).

**Spec:** `docs/superpowers/specs/2026-05-05-phase2b-auto-fetch-market-data-design.md`

---

## File Map

| Path | Purpose | Action |
|------|---------|--------|
| `gather_data.py` | Add `fetch_market_inputs(ticker)` and `enrich_peer_with_market_data(peer)` near other yfinance helpers | Modify |
| `streamlit_app.py` | Add `_auto_fill_valuation_inputs(cfg)` and `_auto_fill_peer_market_data(cfg)`; call them from `_refresh_one` inside `_refresh_stale_valuations` | Modify |
| `tests/test_market_data.py` | New test file — 12 unit tests, all yfinance calls mocked | Create |

The fetchers are pure (mock yfinance and they're testable). The integration into `_refresh_stale_valuations` is a small extension to its inner `_refresh_one(ticker)` worker function.

---

## Test Fixtures

These helpers live at the **top** of `tests/test_market_data.py`. First task to need them creates them; later tasks just use them.

```python
"""Tests for Phase 2-B auto-fetch market data."""
from unittest.mock import MagicMock, patch

import pytest


def make_yf_info(**overrides):
    """Build a yfinance Ticker.info-like dict with sensible defaults.

    Pass kwargs to override or set fields, e.g. make_yf_info(forwardEps=5.48).
    Pass None as value to simulate field absence.
    """
    info = {
        "forwardEps": 5.48,
        "trailingEbitda": 11_800_000_000,   # in dollars
        "forwardPE": 21.0,
        "enterpriseValue": 200_000_000_000,
    }
    for k, v in overrides.items():
        if v is None:
            info.pop(k, None)
        else:
            info[k] = v
    return info


def patch_yfinance_info(info_dict):
    """Helper: returns a context manager that mocks `yfinance.Ticker(...).info`
    to return the given dict."""
    fake_ticker = MagicMock()
    fake_ticker.info = info_dict
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    return patch.dict("sys.modules", {"yfinance": fake_yf})
```

---

### Task 1: Test scaffolding

**Files:**
- Create: `tests/test_market_data.py`

- [ ] **Step 1: Create the test file with fixtures + one sanity test**

```python
"""Tests for Phase 2-B auto-fetch market data."""
from unittest.mock import MagicMock, patch

import pytest


def make_yf_info(**overrides):
    """Build a yfinance Ticker.info-like dict with sensible defaults.

    Pass kwargs to override or set fields, e.g. make_yf_info(forwardEps=5.48).
    Pass None as value to simulate field absence.
    """
    info = {
        "forwardEps": 5.48,
        "trailingEbitda": 11_800_000_000,
        "forwardPE": 21.0,
        "enterpriseValue": 200_000_000_000,
    }
    for k, v in overrides.items():
        if v is None:
            info.pop(k, None)
        else:
            info[k] = v
    return info


def patch_yfinance_info(info_dict):
    """Returns a context manager that mocks yfinance.Ticker(...).info."""
    fake_ticker = MagicMock()
    fake_ticker.info = info_dict
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    return patch.dict("sys.modules", {"yfinance": fake_yf})


def test_scaffold_present():
    """Sanity: the test file is discovered and runs."""
    assert True
```

- [ ] **Step 2: Verify pytest discovers it**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -v`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_market_data.py
git commit -m "test: scaffold tests/test_market_data.py for Phase 2-B"
```

---

### Task 2: `fetch_market_inputs` happy path + missing fields + zero/negative

**Files:**
- Modify: `gather_data.py` (add `fetch_market_inputs` near `fetch_consensus_estimates`, around line 1319)
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_market_data.py`:

```python
import gather_data


def test_fetch_market_inputs_happy_path():
    """Both fields populated; output uses $M for ttm_ebitda."""
    info = make_yf_info(forwardEps=5.48, trailingEbitda=11_800_000_000)
    with patch_yfinance_info(info):
        result = gather_data.fetch_market_inputs("ABT")
    assert result == {"forward_eps": 5.48, "ttm_ebitda": 11800.0}


def test_fetch_market_inputs_missing_fields():
    """Empty info → empty result, no crash."""
    with patch_yfinance_info({}):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}


def test_fetch_market_inputs_partial():
    """Only forwardEps available → only forward_eps in result."""
    info = make_yf_info(trailingEbitda=None)  # drop trailingEbitda
    with patch_yfinance_info(info):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {"forward_eps": 5.48}


def test_fetch_market_inputs_zero_or_negative_skipped():
    """Zero/negative values are not real data — skip them."""
    info = make_yf_info(forwardEps=0, trailingEbitda=-100)
    with patch_yfinance_info(info):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}
```

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k market_inputs -v`
Expected: FAIL with `AttributeError: module 'gather_data' has no attribute 'fetch_market_inputs'`.

- [ ] **Step 3: Implement the fetcher**

In `gather_data.py`, after `fetch_consensus_estimates` (around line 1380), add:

```python
def fetch_market_inputs(ticker: str) -> dict:
    """Fetch valuation_inputs fields from Yahoo Finance via yfinance.

    Returns a dict with these keys (any may be absent when unavailable):
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
        out["ttm_ebitda"] = round(float(ttm_ebitda_raw) / 1e6, 0)

    return out
```

Verify `gather_data.py` has a module-level `logger` — it does (defined near the top with `logger = logging.getLogger(__name__)`). If absent, add it. Use `grep -n "logger = logging" gather_data.py` to confirm.

- [ ] **Step 4: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k market_inputs -v`
Expected: 4 passed.

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check gather_data.py tests/test_market_data.py`
Expected: no NEW violations.

- [ ] **Step 6: Commit**

```bash
git add gather_data.py tests/test_market_data.py
git commit -m "feat: fetch_market_inputs from yfinance (forward_eps, ttm_ebitda)"
```

---

### Task 3: `fetch_market_inputs` error handling

**Files:**
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_market_data.py`:

```python
def test_fetch_market_inputs_yfinance_error():
    """yfinance.Ticker raises → fetcher returns {} (no crash, no propagation)."""
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(side_effect=Exception("network down"))
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}


def test_fetch_market_inputs_info_property_raises():
    """yf.Ticker(...).info access raises → fetcher returns {}."""
    fake_ticker = MagicMock()
    type(fake_ticker).info = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}
```

- [ ] **Step 2: Run tests — should pass directly**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k market_inputs -v`
Expected: 6 passed (4 + 2 new). The error-handling logic was added in Task 2, so these tests should pass now without code changes.

- [ ] **Step 3: Commit**

```bash
git add tests/test_market_data.py
git commit -m "test: pin error handling for fetch_market_inputs"
```

---

### Task 4: `enrich_peer_with_market_data` happy path + no-ticker

**Files:**
- Modify: `gather_data.py`
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_market_data.py`:

```python
def test_enrich_peer_happy_path():
    """fwd_pe is added; ev_ebitda is replaced with real (EV / trailingEbitda)."""
    peer = {"ticker": "AAPL", "name": "Apple", "ev_ebitda": 99.9, "pe": 33.5}
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        out = gather_data.enrich_peer_with_market_data(peer)

    assert out["fwd_pe"] == 30.5
    # 3.5T / 145B = 24.137... → round 1 decimal
    assert out["ev_ebitda"] == pytest.approx(24.1, rel=1e-3)
    # original dict not mutated
    assert peer["ev_ebitda"] == 99.9
    assert "fwd_pe" not in peer


def test_enrich_peer_no_ticker_returns_unchanged_copy():
    """Peer without ticker → returns copy unchanged, no yfinance call."""
    peer = {"name": "no-ticker", "ev_ebitda": 12.0}
    out = gather_data.enrich_peer_with_market_data(peer)
    assert out == peer
    assert out is not peer  # is a copy
```

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k enrich_peer -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the enricher**

In `gather_data.py`, directly after `fetch_market_inputs`, add:

```python
def enrich_peer_with_market_data(peer: dict) -> dict:
    """Return a copy of `peer` enriched with yfinance fwd_pe and a real
    EV/EBITDA multiple (EV / trailingEbitda), replacing any prior approximation.

    Returns the same dict shape as input, with `fwd_pe` added when available
    and `ev_ebitda` updated when both EV and TTM EBITDA are available. The
    original is never mutated. yfinance unavailable / errors → returns peer
    copy unchanged.
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

    ev = info.get("enterpriseValue")
    ttm_ebitda = info.get("trailingEbitda")
    if (isinstance(ev, (int, float)) and ev > 0
            and isinstance(ttm_ebitda, (int, float)) and ttm_ebitda > 0):
        out["ev_ebitda"] = round(ev / ttm_ebitda, 1)

    return out
```

- [ ] **Step 4: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k enrich_peer -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add gather_data.py tests/test_market_data.py
git commit -m "feat: enrich_peer_with_market_data adds fwd_pe and real ev_ebitda"
```

---

### Task 5: `enrich_peer_with_market_data` partial / error / partial-replace

**Files:**
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_market_data.py`:

```python
def test_enrich_peer_only_fwd_pe_available():
    """Only forwardPE available → fwd_pe added, ev_ebitda left unchanged."""
    peer = {"ticker": "X", "ev_ebitda": 99.9}
    info = make_yf_info(forwardPE=22.0, enterpriseValue=None, trailingEbitda=None)
    with patch_yfinance_info(info):
        out = gather_data.enrich_peer_with_market_data(peer)
    assert out["fwd_pe"] == 22.0
    assert out["ev_ebitda"] == 99.9


def test_enrich_peer_yfinance_error_returns_unchanged():
    """yfinance raises → original peer fields preserved."""
    peer = {"ticker": "X", "ev_ebitda": 99.9, "pe": 20.0}
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(side_effect=Exception("boom"))
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        out = gather_data.enrich_peer_with_market_data(peer)
    assert out == peer
    assert "fwd_pe" not in out


def test_enrich_peer_zero_ev_skipped():
    """EV is 0 (anomaly) → don't compute a junk multiple."""
    peer = {"ticker": "X", "ev_ebitda": 99.9}
    info = make_yf_info(forwardPE=22.0, enterpriseValue=0, trailingEbitda=10_000_000_000)
    with patch_yfinance_info(info):
        out = gather_data.enrich_peer_with_market_data(peer)
    assert out["fwd_pe"] == 22.0
    assert out["ev_ebitda"] == 99.9  # unchanged
```

- [ ] **Step 2: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k enrich_peer -v`
Expected: 5 passed (2 + 3 new).

- [ ] **Step 3: Commit**

```bash
git add tests/test_market_data.py
git commit -m "test: pin enrich_peer error/partial paths"
```

---

### Task 6: `_auto_fill_valuation_inputs` — happy path + user-respect

**Files:**
- Modify: `streamlit_app.py` (add helper near the existing `_refresh_stale_valuations`; use `grep -n "^def _refresh_stale_valuations" streamlit_app.py` to locate)
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_market_data.py`:

```python
import streamlit_app


def test_auto_fill_inputs_populates_empty():
    """Empty valuation_inputs → both keys filled, both in _auto_filled."""
    cfg = {"ticker": "ABT", "valuation_inputs": {}}
    info = make_yf_info(forwardEps=5.48, trailingEbitda=11_800_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_valuation_inputs(cfg)

    inputs = cfg["valuation_inputs"]
    assert inputs["forward_eps"] == 5.48
    assert inputs["ttm_ebitda"] == 11800.0
    assert set(inputs["_auto_filled"]) == {"forward_eps", "ttm_ebitda"}
    assert "_fetched_at" in inputs


def test_auto_fill_inputs_respects_user_set_value():
    """forward_eps set by user (not in _auto_filled) → not overwritten."""
    cfg = {
        "ticker": "ABT",
        "valuation_inputs": {"forward_eps": 5.48},  # no _auto_filled key
    }
    info = make_yf_info(forwardEps=5.50, trailingEbitda=11_800_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_valuation_inputs(cfg)

    inputs = cfg["valuation_inputs"]
    assert inputs["forward_eps"] == 5.48                # preserved
    assert inputs["ttm_ebitda"] == 11800.0              # newly filled
    assert "ttm_ebitda" in inputs["_auto_filled"]
    assert "forward_eps" not in inputs["_auto_filled"]  # user value, not auto


def test_auto_fill_inputs_overwrites_previous_auto_value():
    """forward_eps in _auto_filled list → overwritten with new yfinance value."""
    cfg = {
        "ticker": "ABT",
        "valuation_inputs": {
            "forward_eps": 5.40,
            "_auto_filled": ["forward_eps"],
        },
    }
    info = make_yf_info(forwardEps=5.55)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_valuation_inputs(cfg)

    assert cfg["valuation_inputs"]["forward_eps"] == 5.55
```

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k auto_fill_inputs -v`
Expected: FAIL with `AttributeError: module 'streamlit_app' has no attribute '_auto_fill_valuation_inputs'`.

- [ ] **Step 3: Implement the helper**

Locate `_refresh_stale_valuations` in `streamlit_app.py` with `grep -n "^def _refresh_stale_valuations" streamlit_app.py`. Add the new helper directly **above** it:

```python
def _auto_fill_valuation_inputs(cfg: dict) -> None:
    """Auto-fill valuation_inputs from yfinance, respecting user-set values.

    Mutates cfg["valuation_inputs"] in place. Fields listed in `_auto_filled`
    or absent are written; user-set fields (present, not in _auto_filled) are
    preserved. Updates `_fetched_at` ISO timestamp.
    """
    from datetime import datetime, timezone
    import gather_data

    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    fetched = gather_data.fetch_market_inputs(cfg.get("ticker", ""))

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
    inputs["_fetched_at"] = datetime.now(timezone.utc).isoformat()
```

`logger` is already defined at the top of `streamlit_app.py` (verify with `grep -n "logger = logging" streamlit_app.py`).

- [ ] **Step 4: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k auto_fill_inputs -v`
Expected: 3 passed.

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check streamlit_app.py tests/test_market_data.py`
Expected: no NEW violations.

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py tests/test_market_data.py
git commit -m "feat: _auto_fill_valuation_inputs respects user-set values"
```

---

### Task 7: `_auto_fill_valuation_inputs` — None-protect + fetched_at

**Files:**
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_market_data.py`:

```python
def test_auto_fill_inputs_doesnt_overwrite_with_none():
    """Existing auto-filled value preserved when yfinance returns None."""
    cfg = {
        "ticker": "ABT",
        "valuation_inputs": {
            "forward_eps": 5.48,
            "_auto_filled": ["forward_eps"],
        },
    }
    # yfinance returns nothing (e.g. error path or empty info)
    with patch_yfinance_info({}):
        streamlit_app._auto_fill_valuation_inputs(cfg)
    assert cfg["valuation_inputs"]["forward_eps"] == 5.48


def test_auto_fill_inputs_fetched_at_always_set():
    """_fetched_at is updated even when no fields wrote."""
    cfg = {"ticker": "ABT", "valuation_inputs": {}}
    with patch_yfinance_info({}):
        streamlit_app._auto_fill_valuation_inputs(cfg)
    assert "_fetched_at" in cfg["valuation_inputs"]
```

- [ ] **Step 2: Run tests — should pass directly**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k auto_fill_inputs -v`
Expected: 5 passed (3 + 2 new).

- [ ] **Step 3: Commit**

```bash
git add tests/test_market_data.py
git commit -m "test: pin None-protect and _fetched_at behavior"
```

---

### Task 8: `_auto_fill_peer_market_data` — happy + user-respect

**Files:**
- Modify: `streamlit_app.py`
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_market_data.py`:

```python
def test_auto_fill_peer_populates_empty():
    """All peer fields auto-filled, _auto_filled lists tracked per peer."""
    cfg = {
        "ticker": "ABT",
        "peers": [
            {"ticker": "AAPL", "name": "Apple", "ev_ebitda": 99.9, "pe": 33.5},
        ],
    }
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_peer_market_data(cfg)

    peer = cfg["peers"][0]
    assert peer["fwd_pe"] == 30.5
    assert peer["ev_ebitda"] == pytest.approx(24.1, rel=1e-3)
    assert set(peer["_auto_filled"]) == {"fwd_pe", "ev_ebitda"}
    assert "_fetched_at" in peer


def test_auto_fill_peer_respects_user_set_value():
    """User-set fwd_pe (not in _auto_filled) → preserved."""
    cfg = {
        "ticker": "ABT",
        "peers": [
            {"ticker": "AAPL", "fwd_pe": 28.0, "ev_ebitda": 99.9},  # no _auto_filled
        ],
    }
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_peer_market_data(cfg)

    peer = cfg["peers"][0]
    assert peer["fwd_pe"] == 28.0   # preserved
    assert peer["ev_ebitda"] == pytest.approx(24.1, rel=1e-3)  # was overwritten
    assert "ev_ebitda" in peer["_auto_filled"]
    assert "fwd_pe" not in peer["_auto_filled"]


def test_auto_fill_peer_skips_invalid_entries():
    """Non-dict and ticker-less peers are skipped without raising."""
    cfg = {
        "ticker": "ABT",
        "peers": [
            "not a dict",                       # garbage
            {"name": "no-ticker"},              # no ticker
            {"ticker": "AAPL", "ev_ebitda": 99.9},
        ],
    }
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_peer_market_data(cfg)

    # only the valid peer is enriched
    assert cfg["peers"][0] == "not a dict"
    assert "fwd_pe" not in cfg["peers"][1]
    assert cfg["peers"][2]["fwd_pe"] == 30.5
```

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k auto_fill_peer -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the helper**

In `streamlit_app.py`, directly after `_auto_fill_valuation_inputs` (added in Task 6), add:

```python
def _auto_fill_peer_market_data(cfg: dict) -> None:
    """Auto-fill yfinance fwd_pe and real ev_ebitda for each peer in cfg["peers"].

    For each peer dict: fields listed in peer["_auto_filled"] or absent are
    written; user-set fields are preserved. Updates peer["_fetched_at"].
    Non-dict or ticker-less peers are skipped without raising.
    """
    from datetime import datetime, timezone
    import gather_data

    peers = cfg.get("peers") or []
    fetched_at = datetime.now(timezone.utc).isoformat()

    for peer in peers:
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
                logger.info(
                    "Auto-fill skipped for %s peer %s.%s: user-set value preserved",
                    cfg.get("ticker", "?"), peer["ticker"], key,
                )

        peer["_auto_filled"] = auto_filled
        peer["_fetched_at"] = fetched_at
```

- [ ] **Step 4: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k auto_fill_peer -v`
Expected: 3 passed.

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check streamlit_app.py tests/test_market_data.py`
Expected: no NEW violations.

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py tests/test_market_data.py
git commit -m "feat: _auto_fill_peer_market_data with user-respect + skip invalid"
```

---

### Task 9: Wire auto-fill into `_refresh_one`

**Files:**
- Modify: `streamlit_app.py` — the `_refresh_one(ticker)` inner function inside `_refresh_stale_valuations`

- [ ] **Step 1: Write the round-trip test (failing because not yet wired)**

Append to `tests/test_market_data.py`:

```python
def test_refresh_one_calls_auto_fill_before_orchestrator():
    """End-to-end: refresh on a stale ticker fills market data, then runs orchestrator."""
    cfg_in = {
        "ticker": "ABT",
        "company": "Abbott",
        "stock_price": 88.0,
        "equity_market_value": 152_000,
        "debt_market_value": 60_000,
        "risk_free_rate": 0.04,
        "erp": 0.05,
        "credit_spread": 0.01,
        "tax_rate": 0.21,
        "sector_betas": [("Healthcare", 0.9, 1.0)],
        "base_revenue": 41_000,
        "revenue_growth": [0.04] * 5,
        "op_margins": [0.20] * 5,
        "terminal_growth": 0.025,
        "terminal_margin": 0.18,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.02,
        "shares_outstanding": 1_750,
        "buyback_rate": 0.0,
        "margin_of_safety": 0.20,
        "cash_bridge": 8_000,
        "securities": 0,
        "bull_growth_adj": 0.02,
        "bear_growth_adj": -0.04,
        "bull_margin_adj": 0.02,
        "bear_margin_adj": -0.02,
        "peers": [
            {"ticker": "JNJ", "ev_ebitda": 99.9, "pe": 18.0,
             "op_margin": 0.25, "rev_growth": 0.03, "roic": 0.20},
        ],
    }
    storage = {"ABT": cfg_in}
    info = make_yf_info(
        forwardEps=5.48, trailingEbitda=11_800_000_000,
        forwardPE=22.0, enterpriseValue=420_000_000_000,
    )

    fake_save_called_with = []

    def fake_save(client, ticker, cfg, user_id=None):
        fake_save_called_with.append((ticker, dict(cfg)))

    with patch_yfinance_info(info), \
         patch.object(streamlit_app, "save_config", side_effect=fake_save):
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=storage, user_id="u1", force=True,
        )

    assert "ABT" in result["computed"]
    saved_ticker, saved_cfg = fake_save_called_with[0]
    assert saved_ticker == "ABT"
    # Auto-fill set valuation_inputs
    assert saved_cfg["valuation_inputs"]["forward_eps"] == 5.48
    assert saved_cfg["valuation_inputs"]["ttm_ebitda"] == 11800.0
    # Auto-fill enriched the peer
    peer = saved_cfg["peers"][0]
    assert peer["fwd_pe"] == 22.0
    # Orchestrator ran (summary present)
    assert "valuation_summary" in saved_cfg
    assert saved_cfg["valuation_summary"]["weighted_fv_mid"] > 0
```

- [ ] **Step 2: Run test — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_refresh_one_calls_auto_fill_before_orchestrator -v`
Expected: FAIL — `valuation_inputs` is empty in `saved_cfg` because the auto-fill helpers aren't called yet.

- [ ] **Step 3: Wire the helpers into `_refresh_one`**

In `streamlit_app.py`, locate `_refresh_one(ticker)` inside `_refresh_stale_valuations` (use `grep -n "def _refresh_one" streamlit_app.py`). It currently looks like:

```python
    def _refresh_one(ticker):
        cfg = dict(cfgs[ticker])
        cfg.setdefault("ticker", ticker)
        summary = calculate_multi_lens_valuation_remote(cfg)
        cfg["valuation_summary"] = summary
        save_config(client, ticker, cfg, user_id=user_id)
        return ticker
```

Replace with:

```python
    def _refresh_one(ticker):
        cfg = dict(cfgs[ticker])
        cfg.setdefault("ticker", ticker)
        # Auto-fetch market inputs and peer multiples before computing the summary.
        # Both helpers are best-effort: yfinance failures don't block the orchestrator.
        _auto_fill_valuation_inputs(cfg)
        _auto_fill_peer_market_data(cfg)
        summary = calculate_multi_lens_valuation_remote(cfg)
        cfg["valuation_summary"] = summary
        save_config(client, ticker, cfg, user_id=user_id)
        return ticker
```

- [ ] **Step 4: Run test — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_refresh_one_calls_auto_fill_before_orchestrator -v`
Expected: PASS.

- [ ] **Step 5: Run full test_market_data.py suite**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -v`
Expected: 14 passed (1 scaffold + 4 fetch_market_inputs + 2 fetch error + 5 enrich_peer + 5 auto_fill_inputs + 3 auto_fill_peer + 1 round-trip = wait, let me recount: 1 + 4 + 2 + 2 + 3 + 3 + 2 + 3 + 1 = 21).

Actually, target count by task:
- Task 1: 1 test
- Task 2: +4 = 5
- Task 3: +2 = 7
- Task 4: +2 = 9
- Task 5: +3 = 12
- Task 6: +3 = 15
- Task 7: +2 = 17
- Task 8: +3 = 20
- Task 9: +1 = 21

Expected: 21 passed.

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py tests/test_market_data.py
git commit -m "feat: wire auto-fill helpers into _refresh_one"
```

---

### Task 10: Lint + full regression suite

**Files:**
- None (verification only)

- [ ] **Step 1: Lint over the whole repo**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check .`
Expected: All checks pass on new files (`tests/test_market_data.py`) and on modifications to `gather_data.py` / `streamlit_app.py`. Pre-existing violations elsewhere are not Phase-2-B's responsibility — leave them.

If new violations were introduced by this branch, fix them. Re-run until clean (modulo pre-existing).

- [ ] **Step 2: Full regression suite**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m pytest test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py tests/test_watchlist_ui.py tests/test_market_data.py -v 2>&1 | tail -3
```
Expected: 41 + 40 + 25 + 29 + 21 = 156 tests passed.

- [ ] **Step 3: Local visual smoke test (optional)**

If credentials are available locally, start the dev server, visit Watchlist, click "↻ Refresh all" on a non-empty watchlist. Confirm:
- Refresh completes without errors
- After refresh, at least one ticker shows lens-dots = 3 active (Multiples now lit up because forward_eps + ttm_ebitda were auto-fetched)
- Range-bar shows wider FV span than DCF-only

Stop the server when done. If credentials aren't available locally, skip — production smoke happens after merge.

- [ ] **Step 4: Commit (only if you fixed any lint issues in Step 1)**

If Step 1 required edits, commit them:
```bash
git add gather_data.py streamlit_app.py tests/test_market_data.py
git commit -m "style: lint fixes for Phase 2-B"
```

If no edits were needed, skip this step (no commit).

---

## Summary of Commits (target sequence)

1. `test: scaffold tests/test_market_data.py for Phase 2-B`
2. `feat: fetch_market_inputs from yfinance (forward_eps, ttm_ebitda)`
3. `test: pin error handling for fetch_market_inputs`
4. `feat: enrich_peer_with_market_data adds fwd_pe and real ev_ebitda`
5. `test: pin enrich_peer error/partial paths`
6. `feat: _auto_fill_valuation_inputs respects user-set values`
7. `test: pin None-protect and _fetched_at behavior`
8. `feat: _auto_fill_peer_market_data with user-respect + skip invalid`
9. `feat: wire auto-fill helpers into _refresh_one`
10. (optional) `style: lint fixes for Phase 2-B`

9-10 commits. Implementation should take ~1h via subagents — most tasks are mechanical, only Tasks 6/8/9 touch existing code paths.
