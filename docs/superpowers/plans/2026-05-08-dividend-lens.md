# Dividend Lens (Phase 2-C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a working `compute_dividend_lens` (Hybrid Two-stage DDM + Yield Mean-Reversion) that integrates with the existing watchlist UI, MCP, and refresh pipeline.

**Architecture:** Auto-fetch dividend history + monthly closes from yfinance into `cfg["valuation_inputs"]`; lens consumes those plus a new `compute_cost_of_equity` helper to discount future dividends. Lens skips cleanly for non-payers (TTM = 0), tickers with <3y history, or degenerate cost-of-equity vs terminal-growth setups. Generic `update_valuation_inputs` MCP tool lets the user override any auto-fetched field via Claude Desktop.

**Tech Stack:** Python 3.13, pandas (already in deps), yfinance (already in deps), pytest, MCP (FastMCP, already in deps).

**Spec:** `docs/superpowers/specs/2026-05-08-dividend-lens-design.md`

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `dcf_calculator.py` | Core DCF + cost-of-capital math | Add `compute_cost_of_equity(cfg)` helper |
| `gather_data.py` | yfinance / EDGAR data extraction | Add `fetch_dividend_history(ticker, n_years=5)` |
| `auto_fetch.py` | Shared auto-fetch helpers | Add `auto_fill_dividend_inputs(cfg)` |
| `streamlit_app.py` | Watchlist refresh + UI | Wire auto-fill into `_refresh_one`; re-add dividend to `_render_lens_dots` and `_render_football_field` |
| `mcp_server.py` | MCP tools | Wire auto-fill into `_calculate_multi_lens_valuation_impl`; add `update_valuation_inputs` tool |
| `valuation_lenses.py` | Multi-lens orchestrator | Replace `compute_dividend_lens` stub with hybrid implementation |
| `config_store.py` | Watchlist persistence | Re-add `dividend` to `_COUNTED_LENSES` |
| `scripts/force_refresh_all.py` | CLI batch tool | Mirror `_counted` extension |
| `tests/test_multi_lens.py` | Lens + orchestrator tests | New tests for dividend lens + cost_of_equity helper |
| `tests/test_market_data.py` | Auto-fetch tests | New tests for `fetch_dividend_history` and `auto_fill_dividend_inputs` |
| `tests/test_watchlist_ui.py` | UI tests | Update lens-dots / football-field counts back to 4 |
| `test_mcp_server.py` | MCP tool tests | New tests for `update_valuation_inputs` |

7 tasks across these files. Each task is a self-contained TDD cycle with one logical commit.

---

## Task 1: Extract `compute_cost_of_equity` helper

**Why first:** every later task that discounts dividends needs this. The existing `compute_wacc` returns a single float and inlines the levered-beta-times-ERP math, so we factor out a clean helper without touching `compute_wacc`'s output contract.

**Files:**
- Modify: `dcf_calculator.py:9-23` (add new helper above `compute_wacc`)
- Test: `tests/test_multi_lens.py` (extend existing test file with new test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_multi_lens.py` (anywhere among the existing tests, e.g. after the existing imports/helpers but before the dividend-lens tests we'll add later):

```python
def test_compute_cost_of_equity_basic():
    """Cost of equity = risk_free_rate + levered_beta × erp."""
    import dcf_calculator
    cfg = {
        "equity_market_value": 1000,
        "debt_market_value": 200,
        "sector_betas": [("Software", 1.10, 1.0)],
        "tax_rate": 0.21,
        "risk_free_rate": 0.04,
        "erp": 0.05,
    }
    # de_ratio = 200/1000 = 0.2
    # lev_beta = 1.10 * (1 + (1 - 0.21) * 0.2) = 1.10 * 1.158 = 1.2738
    # ke = 0.04 + 1.2738 * 0.05 = 0.10369
    ke = dcf_calculator.compute_cost_of_equity(cfg)
    assert ke == pytest.approx(0.10369, abs=1e-4)


def test_compute_cost_of_equity_matches_wacc_internals():
    """Cost of equity from the new helper must equal the ke that compute_wacc
    computes internally — they share the same formula and inputs.

    We verify this indirectly: build a config with debt = 0 so WACC == ke,
    then check the two functions agree."""
    import dcf_calculator
    cfg = {
        "equity_market_value": 1000,
        "debt_market_value": 0,           # no debt → WACC == ke
        "sector_betas": [("Software", 0.9, 1.0)],
        "tax_rate": 0.21,
        "risk_free_rate": 0.04,
        "erp": 0.055,
        "credit_spread": 0.01,            # ignored when debt = 0
    }
    ke = dcf_calculator.compute_cost_of_equity(cfg)
    wacc = dcf_calculator.compute_wacc(cfg)
    assert ke == pytest.approx(wacc, abs=1e-9)
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_multi_lens.py::test_compute_cost_of_equity_basic tests/test_multi_lens.py::test_compute_cost_of_equity_matches_wacc_internals -v`

Expected: FAIL — `AttributeError: module 'dcf_calculator' has no attribute 'compute_cost_of_equity'`

- [ ] **Step 3: Add the helper**

In `dcf_calculator.py`, add this function ABOVE `def compute_wacc(cfg):` (around line 9):

```python
def compute_cost_of_equity(cfg):
    """Compute the cost of equity (CAPM) from the config dict.

    ke = risk_free_rate + levered_beta × erp

    Levered beta uses the existing weighted-unlevered-beta + Hamada
    re-levering convention from compute_wacc, kept consistent so that
    when debt = 0, this function returns exactly compute_wacc(cfg).

    Returns the cost of equity as a float (e.g. 0.087 for 8.7%).
    """
    eq_val = cfg["equity_market_value"]
    debt_val = cfg["debt_market_value"]
    wu_beta = sum(ub * wt for _, ub, wt in cfg["sector_betas"])
    de_ratio = debt_val / eq_val if eq_val > 0 else 0
    lev_beta = wu_beta * (1 + (1 - cfg["tax_rate"]) * de_ratio)
    return cfg["risk_free_rate"] + lev_beta * cfg["erp"]
```

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `python3 -m pytest tests/test_multi_lens.py::test_compute_cost_of_equity_basic tests/test_multi_lens.py::test_compute_cost_of_equity_matches_wacc_internals -v`
Expected: PASS.

- [ ] **Step 5: Run the full multi-lens suite to confirm no regressions**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: All previously passing tests still pass; 2 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add dcf_calculator.py tests/test_multi_lens.py
git commit -m "$(cat <<'EOF'
refactor(dcf): extract compute_cost_of_equity helper

The existing compute_wacc returns a single WACC float and inlines the
levered-beta + ERP math. Extract a parallel helper that returns just
the cost-of-equity number. Used by the upcoming Dividend lens (DDM
discount rate); compute_wacc itself is untouched to avoid breaking
its return contract.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add `fetch_dividend_history` in gather_data.py

**Why next:** the lens needs `ttm_dividend`, `dividend_5y_cagr`, `median_5y_yield` from yfinance before `auto_fill_dividend_inputs` (Task 3) can wire them into the cfg.

**Files:**
- Modify: `gather_data.py` (add new function near existing `fetch_historical_multiples`, around line 1545)
- Test: `tests/test_market_data.py` (extend with new tests + a new dividend-history fixture builder)

- [ ] **Step 1: Add the test fixture builders**

Add at the top of `tests/test_market_data.py`, immediately AFTER the existing `make_yf_quarterly_balance_sheet` helper (around line 78):

```python
def make_yf_dividends(quarterly_amounts=None, n_years=5):
    """Build a yfinance Ticker.dividends-like pandas Series of quarterly
    payments. quarterly_amounts is a list of dollar amounts (length defaults
    to n_years*4). Index is quarterly ex-div dates ending around 2026-05-01.
    """
    import pandas as pd
    if quarterly_amounts is None:
        # Default: ~$0.50/quarter growing 5%/yr → realistic mature payer
        per_quarter = 0.50
        annual_growth = 0.05
        quarterly_amounts = []
        for q in range(n_years * 4):
            year_offset = q // 4
            quarterly_amounts.append(per_quarter * (1 + annual_growth) ** year_offset)
    dates = pd.date_range(end="2026-05-01", periods=len(quarterly_amounts), freq="QE")
    return pd.Series(quarterly_amounts, index=dates, name="Dividends")


def patch_yfinance_dividends(dividends_series, history_df=None):
    """Mock yf.Ticker(t).dividends + .history(...) for fetch_dividend_history."""
    fake_ticker = MagicMock()
    fake_ticker.dividends = dividends_series
    fake_ticker.history = MagicMock(
        return_value=history_df if history_df is not None else make_yf_history(months=60)
    )
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    return patch.dict("sys.modules", {"yfinance": fake_yf})
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_market_data.py` (anywhere after the existing `fetch_historical_multiples` tests, e.g. before the `auto_fill_*` tests):

```python
def test_fetch_dividend_history_full_5y_payer():
    """Mature payer with 5y of growing dividends → all three fields populated."""
    divs = make_yf_dividends(n_years=5)  # 20 quarterly dividends, growing
    with patch_yfinance_dividends(divs):
        result = gather_data.fetch_dividend_history("PEP")
    assert result["ttm_dividend"] > 0
    assert 0 < result["dividend_5y_cagr"] < 0.15
    assert result["median_5y_yield"] is not None
    assert result["median_5y_yield"] > 0
    assert result["n_years_available"] == pytest.approx(5, abs=0.5)


def test_fetch_dividend_history_non_payer_returns_zeros():
    """Empty dividends Series → ttm_dividend=0, growth=None, yield=None."""
    import pandas as pd
    empty = pd.Series([], dtype=float, name="Dividends")
    with patch_yfinance_dividends(empty):
        result = gather_data.fetch_dividend_history("ABNB")
    assert result["ttm_dividend"] == 0.0
    assert result["dividend_5y_cagr"] is None
    assert result["median_5y_yield"] is None
    assert result["n_years_available"] == 0


def test_fetch_dividend_history_short_history_no_yield():
    """Recent initiator (<3y of data) → median_5y_yield=None, others may
    still populate or be None depending on data sufficiency."""
    divs = make_yf_dividends(n_years=2)  # 8 quarterly dividends, ~2y
    with patch_yfinance_dividends(divs, history_df=make_yf_history(months=24)):
        result = gather_data.fetch_dividend_history("GOOG")
    assert result["ttm_dividend"] > 0
    assert result["median_5y_yield"] is None  # <36 months of data


def test_fetch_dividend_history_yfinance_error():
    """yfinance raises → returns dict with ttm_dividend=0 and all-None,
    not a crash and not an empty dict (consumers expect the keys)."""
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(side_effect=Exception("network down"))
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_dividend_history("XYZ")
    assert result == {
        "ttm_dividend": 0.0,
        "dividend_5y_cagr": None,
        "median_5y_yield": None,
        "n_years_available": 0,
    }


def test_fetch_dividend_history_caps_growth_at_15pct():
    """If raw 5y CAGR exceeds 15%, the function caps it for sanity
    (dividend growth above 15% per year sustained 5y is a red flag)."""
    # Build dividends growing 25%/yr — should be capped to 0.15
    quarterly = []
    base = 0.20
    for q in range(20):
        year_offset = q // 4
        quarterly.append(base * (1.25) ** year_offset)
    divs = make_yf_dividends(quarterly_amounts=quarterly)
    with patch_yfinance_dividends(divs):
        result = gather_data.fetch_dividend_history("HOTSTOCK")
    # Cap is applied — never above 0.15
    assert result["dividend_5y_cagr"] == pytest.approx(0.15, abs=1e-9)
```

- [ ] **Step 3: Run the failing tests to verify they fail**

Run: `python3 -m pytest tests/test_market_data.py -k "fetch_dividend_history" -v`
Expected: FAIL — `AttributeError: module 'gather_data' has no attribute 'fetch_dividend_history'`.

- [ ] **Step 4: Implement `fetch_dividend_history`**

Add to `gather_data.py`, immediately AFTER `fetch_historical_multiples` (around line 1545):

```python
def fetch_dividend_history(ticker: str, n_years: int = 5) -> dict:
    """Fetch dividend-related inputs for the Dividend lens from yfinance.

    Returns a dict with these keys (always present; values may be 0/None):
        ttm_dividend:          float, sum of ex-div dates in trailing 365 days
        dividend_5y_cagr:      float | None, capped at 0.15 for sanity
        median_5y_yield:       float | None, median of monthly TTM-div / close
                               across up to 60 months. None when <36 months
                               of usable observations.
        n_years_available:     int, span (rounded) of dividend history

    yfinance failure → returns the all-zero / all-None shape (never raises,
    never returns an empty dict — consumers rely on the keys existing).
    """
    import statistics
    from datetime import timedelta

    empty_result = {
        "ttm_dividend": 0.0,
        "dividend_5y_cagr": None,
        "median_5y_yield": None,
        "n_years_available": 0,
    }

    try:
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        divs = ticker_obj.dividends  # pandas Series indexed by ex-div date
        history = ticker_obj.history(period=f"{n_years}y", interval="1mo")
    except ImportError:
        logger.warning("yfinance not installed; skipping dividend history for %s", ticker)
        return empty_result
    except Exception as e:
        logger.warning("yfinance dividend fetch failed for %s: %s", ticker, e)
        return empty_result

    if divs is None or len(divs) == 0:
        return empty_result

    # Strip tz so we can compare with tz-naive arithmetic
    try:
        divs_idx = divs.index.tz_localize(None) if divs.index.tz is not None else divs.index
    except (AttributeError, TypeError):
        divs_idx = divs.index

    most_recent = divs_idx.max()
    # TTM = sum of dividends with ex-div in trailing 365 days from most recent
    ttm_window_start = most_recent - timedelta(days=365)
    ttm_mask = (divs_idx > ttm_window_start) & (divs_idx <= most_recent)
    ttm_dividend = float(divs.values[ttm_mask].sum())

    # Span of history available (years)
    earliest = divs_idx.min()
    n_years_available = round((most_recent - earliest).days / 365.0, 1)

    out = dict(empty_result)
    out["ttm_dividend"] = round(ttm_dividend, 4)
    out["n_years_available"] = n_years_available

    # 5y CAGR: TTM dividend now vs TTM dividend 5y ago
    five_years_ago = most_recent - timedelta(days=5 * 365)
    past_window_start = five_years_ago - timedelta(days=365)
    past_mask = (divs_idx > past_window_start) & (divs_idx <= five_years_ago)
    past_ttm = float(divs.values[past_mask].sum())
    if ttm_dividend > 0 and past_ttm > 0:
        cagr = (ttm_dividend / past_ttm) ** (1 / 5) - 1
        out["dividend_5y_cagr"] = min(round(cagr, 4), 0.15)

    # Median yield across monthly observations: rolling-TTM-div / close
    if history is not None and len(history) >= 36:
        try:
            hist_idx = history.index.tz_localize(None) if history.index.tz is not None else history.index
        except (AttributeError, TypeError):
            hist_idx = history.index
        closes = [float(p) for p in history["Close"].values]

        yields = []
        for month_dt, close in zip(hist_idx, closes):
            if close <= 0:
                continue
            window_start = month_dt - timedelta(days=365)
            mask = (divs_idx > window_start) & (divs_idx <= month_dt)
            rolling_ttm = float(divs.values[mask].sum())
            if rolling_ttm > 0:
                yields.append(rolling_ttm / close)
        if len(yields) >= 36:
            out["median_5y_yield"] = round(statistics.median(yields), 6)

    return out
```

- [ ] **Step 5: Run the dividend-history tests to verify they pass**

Run: `python3 -m pytest tests/test_market_data.py -k "fetch_dividend_history" -v`
Expected: All 5 dividend-history tests PASS.

- [ ] **Step 6: Run the full market-data suite**

Run: `python3 -m pytest tests/test_market_data.py -v`
Expected: previously passing tests still pass; 5 new tests pass.

- [ ] **Step 7: Commit**

```bash
git add gather_data.py tests/test_market_data.py
git commit -m "$(cat <<'EOF'
feat(gather_data): add fetch_dividend_history for Dividend lens

Pulls dividend amounts + monthly closes from yfinance, computes
ttm_dividend (rolling 365 days), dividend_5y_cagr (capped at 15%),
and median_5y_yield (medianised rolling-TTM-yield over up to 60
months, requires ≥36 observations). Best-effort: yfinance failure
returns the all-zero / all-None shape, never raises.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `auto_fill_dividend_inputs` + wire into refresh paths

**Files:**
- Modify: `auto_fetch.py` (add new helper + wire into existing flow)
- Modify: `streamlit_app.py:_refresh_one` (currently in `_refresh_stale_valuations`, around line 410-419)
- Modify: `mcp_server.py:_calculate_multi_lens_valuation_impl` (lines ~232-242) and `_refresh_all_valuations_impl._refresh_one` (lines ~293-301)
- Test: `tests/test_market_data.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_market_data.py` (after the existing `auto_fill_*` tests around line 308):

```python
def test_auto_fill_dividend_inputs_full(monkeypatch):
    """Full payer history → all three fields written and added to _auto_filled."""
    import auto_fetch
    cfg = {"ticker": "PEP", "valuation_inputs": {}}
    fake_history = {
        "ttm_dividend": 5.20,
        "dividend_5y_cagr": 0.062,
        "median_5y_yield": 0.027,
        "n_years_available": 5.2,
    }
    monkeypatch.setattr(gather_data, "fetch_dividend_history", lambda t, n_years=5: fake_history)
    auto_fetch.auto_fill_dividend_inputs(cfg)
    inputs = cfg["valuation_inputs"]
    assert inputs["ttm_dividend"] == 5.20
    assert inputs["dividend_5y_cagr"] == 0.062
    assert inputs["median_5y_yield"] == 0.027
    assert "ttm_dividend" in inputs["_auto_filled"]
    assert "dividend_5y_cagr" in inputs["_auto_filled"]
    assert "median_5y_yield" in inputs["_auto_filled"]
    assert "_fetched_at" in inputs


def test_auto_fill_dividend_inputs_respects_user_override(monkeypatch):
    """Field present in cfg but NOT in _auto_filled = user-set → preserved."""
    import auto_fetch
    cfg = {
        "ticker": "PEP",
        "valuation_inputs": {
            "dividend_5y_cagr": 0.10,           # user has a forward view
            "_auto_filled": ["ttm_dividend"],   # only ttm was auto-set previously
            "ttm_dividend": 4.50,
        },
    }
    fake_history = {
        "ttm_dividend": 5.20,
        "dividend_5y_cagr": 0.062,
        "median_5y_yield": 0.027,
        "n_years_available": 5.2,
    }
    monkeypatch.setattr(gather_data, "fetch_dividend_history", lambda t, n_years=5: fake_history)
    auto_fetch.auto_fill_dividend_inputs(cfg)
    inputs = cfg["valuation_inputs"]
    assert inputs["ttm_dividend"] == 5.20  # was in _auto_filled → overwritten
    assert inputs["dividend_5y_cagr"] == 0.10  # user-set → preserved
    assert inputs["median_5y_yield"] == 0.027  # was absent → written + auto_filled


def test_auto_fill_dividend_inputs_non_payer_writes_zeros(monkeypatch):
    """Non-payer (ABNB-like) → zeros/None still written and marked auto-filled,
    so a future actual dividend payment will overwrite them."""
    import auto_fetch
    cfg = {"ticker": "ABNB", "valuation_inputs": {}}
    monkeypatch.setattr(
        gather_data, "fetch_dividend_history",
        lambda t, n_years=5: {
            "ttm_dividend": 0.0, "dividend_5y_cagr": None,
            "median_5y_yield": None, "n_years_available": 0,
        },
    )
    auto_fetch.auto_fill_dividend_inputs(cfg)
    inputs = cfg["valuation_inputs"]
    assert inputs["ttm_dividend"] == 0.0
    assert inputs["dividend_5y_cagr"] is None
    assert inputs["median_5y_yield"] is None
    assert "ttm_dividend" in inputs["_auto_filled"]
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_market_data.py -k "auto_fill_dividend" -v`
Expected: FAIL — `AttributeError: module 'auto_fetch' has no attribute 'auto_fill_dividend_inputs'`.

- [ ] **Step 3: Add `auto_fill_dividend_inputs` to `auto_fetch.py`**

Add at the bottom of `auto_fetch.py`:

```python
def auto_fill_dividend_inputs(cfg: dict) -> None:
    """Auto-fill `cfg["valuation_inputs"]` with dividend-history fields.

    Writes ttm_dividend, dividend_5y_cagr, median_5y_yield from
    gather_data.fetch_dividend_history. Respects the same `_auto_filled`
    precedence as auto_fill_valuation_inputs: user-set values are
    preserved on subsequent refreshes. Updates `_fetched_at`.
    """
    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    fetched = gather_data.fetch_dividend_history(cfg.get("ticker", ""))

    for key, value in fetched.items():
        # n_years_available is diagnostic, not a valuation_inputs field
        if key == "n_years_available":
            continue
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
```

- [ ] **Step 4: Run the targeted tests**

Run: `python3 -m pytest tests/test_market_data.py -k "auto_fill_dividend" -v`
Expected: All 3 PASS.

- [ ] **Step 5: Wire into Streamlit's refresh path**

In `streamlit_app.py`, find `_refresh_one` inside `_refresh_stale_valuations` (around line 410). Currently:

```python
    def _refresh_one(ticker):
        cfg = dict(cfgs[ticker])
        cfg.setdefault("ticker", ticker)
        # Auto-fetch market inputs and peer multiples before computing the summary.
        # Both helpers are best-effort: yfinance failures don't block the orchestrator.
        _auto_fill_valuation_inputs(cfg)
        _auto_fill_peer_market_data(cfg)
        summary = calculate_multi_lens_valuation_remote(cfg)
```

Replace with (add the dividend auto-fill on a third line):

```python
    def _refresh_one(ticker):
        cfg = dict(cfgs[ticker])
        cfg.setdefault("ticker", ticker)
        # Auto-fetch market inputs, peer multiples, and dividend history
        # before computing the summary. All are best-effort: yfinance
        # failures don't block the orchestrator.
        _auto_fill_valuation_inputs(cfg)
        _auto_fill_peer_market_data(cfg)
        _auto_fill_dividend_inputs(cfg)
        summary = calculate_multi_lens_valuation_remote(cfg)
```

Then update the existing re-export at `streamlit_app.py:367-373`:

```python
# Auto-fill helpers live in auto_fetch (shared with mcp_server). Re-exported
# under their underscore-prefixed names so existing call sites in this file
# (and tests that monkey-patch streamlit_app._auto_fill_*) keep working.
from auto_fetch import (
    auto_fill_peer_market_data as _auto_fill_peer_market_data,
    auto_fill_valuation_inputs as _auto_fill_valuation_inputs,
)
```

Add `auto_fill_dividend_inputs` to the import group:

```python
# Auto-fill helpers live in auto_fetch (shared with mcp_server). Re-exported
# under their underscore-prefixed names so existing call sites in this file
# (and tests that monkey-patch streamlit_app._auto_fill_*) keep working.
from auto_fetch import (
    auto_fill_dividend_inputs as _auto_fill_dividend_inputs,
    auto_fill_peer_market_data as _auto_fill_peer_market_data,
    auto_fill_valuation_inputs as _auto_fill_valuation_inputs,
)
```

- [ ] **Step 6: Wire into MCP `_calculate_multi_lens_valuation_impl`**

In `mcp_server.py` around line 235:

```python
    cfg.setdefault("ticker", ticker)
    auto_fetch.auto_fill_valuation_inputs(cfg)
    auto_fetch.auto_fill_peer_market_data(cfg)
```

Add one line:

```python
    cfg.setdefault("ticker", ticker)
    auto_fetch.auto_fill_valuation_inputs(cfg)
    auto_fetch.auto_fill_peer_market_data(cfg)
    auto_fetch.auto_fill_dividend_inputs(cfg)
```

And in the same file's `_refresh_all_valuations_impl._refresh_one` around line 295:

```python
    def _refresh_one(ticker: str) -> str:
        cfg = dict(loaded[ticker])
        cfg.setdefault("ticker", ticker)
        auto_fetch.auto_fill_valuation_inputs(cfg)
        auto_fetch.auto_fill_peer_market_data(cfg)
```

Add the dividend line:

```python
    def _refresh_one(ticker: str) -> str:
        cfg = dict(loaded[ticker])
        cfg.setdefault("ticker", ticker)
        auto_fetch.auto_fill_valuation_inputs(cfg)
        auto_fetch.auto_fill_peer_market_data(cfg)
        auto_fetch.auto_fill_dividend_inputs(cfg)
```

- [ ] **Step 7: Run the affected test files**

Run: `python3 -m pytest tests/test_market_data.py tests/test_watchlist_ui.py -v`
Expected: All tests pass — the new dividend wiring uses fakes/mocks where needed; existing refresh tests don't assert on the dividend fields and won't break.

- [ ] **Step 8: Commit**

```bash
git add auto_fetch.py streamlit_app.py mcp_server.py tests/test_market_data.py
git commit -m "$(cat <<'EOF'
feat(auto_fetch): add auto_fill_dividend_inputs + wire into refresh

Mirrors auto_fill_valuation_inputs / auto_fill_peer_market_data pattern.
Pulls ttm_dividend, dividend_5y_cagr, median_5y_yield from
gather_data.fetch_dividend_history into cfg["valuation_inputs"],
respecting _auto_filled precedence. Wired into both Streamlit's
_refresh_one and MCP's calculate_multi_lens_valuation /
refresh_all_valuations paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Implement `compute_dividend_lens`

**Files:**
- Modify: `valuation_lenses.py:25-32` (replace stub)
- Test: `tests/test_multi_lens.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_multi_lens.py` (after the existing `test_compute_cost_of_equity_*` tests from Task 1):

```python
# Helpers for dividend-lens tests
_DIVIDEND_BASE_CFG = {
    "stock_price": 100.0,
    "equity_market_value": 1000,
    "debt_market_value": 100,
    "sector_betas": [("Software", 1.0, 1.0)],
    "tax_rate": 0.21,
    "risk_free_rate": 0.04,
    "erp": 0.05,
    "credit_spread": 0.01,
    "terminal_growth": 0.025,
    "valuation_inputs": {
        "ttm_dividend": 4.00,
        "dividend_5y_cagr": 0.06,
        "median_5y_yield": 0.030,
    },
}


def test_dividend_lens_skips_non_payer():
    """ttm_dividend = 0 → lens returns None."""
    cfg = dict(_DIVIDEND_BASE_CFG)
    cfg["valuation_inputs"] = {
        "ttm_dividend": 0.0,
        "dividend_5y_cagr": None,
        "median_5y_yield": None,
    }
    assert valuation_lenses.compute_dividend_lens(cfg) is None


def test_dividend_lens_skips_no_growth_history():
    """dividend_5y_cagr is None (insufficient history) → skip lens."""
    cfg = dict(_DIVIDEND_BASE_CFG)
    cfg["valuation_inputs"] = {
        "ttm_dividend": 1.50,
        "dividend_5y_cagr": None,        # no growth baseline
        "median_5y_yield": None,
    }
    assert valuation_lenses.compute_dividend_lens(cfg) is None


def test_dividend_lens_skips_when_ke_le_terminal():
    """cost_of_equity ≤ terminal_growth → Gordon perpetuity blows up → skip."""
    cfg = dict(_DIVIDEND_BASE_CFG)
    cfg["risk_free_rate"] = 0.01  # very low rf → low ke
    cfg["erp"] = 0.005             # very low erp
    cfg["terminal_growth"] = 0.05  # high terminal growth → ke < g
    lens = valuation_lenses.compute_dividend_lens(cfg)
    assert lens is None


def test_dividend_lens_active_with_both_anchors():
    """Full payer with median_5y_yield → range spans both DDM and yield-MR."""
    cfg = dict(_DIVIDEND_BASE_CFG)
    lens = valuation_lenses.compute_dividend_lens(cfg)
    assert lens is not None
    assert lens["fv_low"] < lens["fv_high"]
    assert lens["fv_low"] <= lens["fv_mid"] <= lens["fv_high"]
    details = lens["details"]
    assert details["ttm_dividend"] == 4.00
    assert details["growth_rate_stage1"] == 0.06
    assert details["terminal_growth"] == 0.025
    assert details["cost_of_equity"] > 0
    assert details["ddm_fv"] > 0
    assert details["yield_mr_fv"] > 0
    assert details["median_5y_yield"] == 0.030


def test_dividend_lens_active_anchor_a_only_when_no_yield():
    """No median_5y_yield → fv ±15% band on DDM result, not min/max."""
    cfg = dict(_DIVIDEND_BASE_CFG)
    cfg["valuation_inputs"] = {
        "ttm_dividend": 4.00,
        "dividend_5y_cagr": 0.06,
        "median_5y_yield": None,
    }
    lens = valuation_lenses.compute_dividend_lens(cfg)
    assert lens is not None
    ddm = lens["details"]["ddm_fv"]
    assert lens["fv_mid"] == pytest.approx(ddm, abs=0.01)
    assert lens["fv_low"] == pytest.approx(ddm * 0.85, abs=0.01)
    assert lens["fv_high"] == pytest.approx(ddm * 1.15, abs=0.01)
    assert lens["details"]["yield_mr_fv"] is None


def test_dividend_lens_caps_growth_at_15pct_in_details():
    """Even if upstream produced an uncapped CAGR somehow, the lens caps at 15%."""
    cfg = dict(_DIVIDEND_BASE_CFG)
    cfg["valuation_inputs"] = dict(_DIVIDEND_BASE_CFG["valuation_inputs"])
    cfg["valuation_inputs"]["dividend_5y_cagr"] = 0.25  # absurd
    lens = valuation_lenses.compute_dividend_lens(cfg)
    assert lens is not None
    assert lens["details"]["growth_rate_stage1"] == 0.15


def test_default_lens_weights_dividend_zero():
    """Dividend default weight stays 0.0 — opt-in per ticker."""
    assert valuation_lenses.DEFAULT_LENS_WEIGHTS["dividend"] == 0.0


def test_orchestrator_includes_dividend_when_payer():
    """Full dividend-paying cfg with all multi-lens inputs → 4 active lenses."""
    cfg = dict(_DIVIDEND_BASE_CFG)
    # We need the other lenses to be skipped or to also activate; easiest is
    # to provide enough inputs to keep DCF active and skip multiples/historical.
    cfg.update({
        "company": "Test",
        "ticker": "TEST",
        "shares_outstanding": 1000,
        "base_revenue": 50_000,
        "revenue_growth": [0.05] * 5,
        "op_margins": [0.20] * 5,
        "terminal_margin": 0.20,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.02,
        "margin_of_safety": 0.20,
        "cash_bridge": 1_000,
        "securities": 0,
    })
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)
    lenses = summary["lenses"]
    assert lenses["dcf"] is not None
    assert lenses["dividend"] is not None
    # Dividend has weight 0 by default → contributes nothing to weighted_fv
    assert lenses["dividend"]["weight_normalized"] == 0.0
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_multi_lens.py -k "dividend_lens or default_lens_weights_dividend or orchestrator_includes_dividend" -v`
Expected: most tests FAIL — `compute_dividend_lens` still returns None.

- [ ] **Step 3: Implement `compute_dividend_lens`**

In `valuation_lenses.py`, replace the existing stub at lines 25-32:

```python
def compute_dividend_lens(cfg):
    """Phase 2 placeholder.

    TODO Phase 2: Gordon Growth + yield mean-reversion using
    valuation_inputs.target_dividend_yield, current_dividend,
    expected_dividend_growth.
    """
    return None
```

with:

```python
def compute_dividend_lens(cfg):
    """Hybrid Two-stage DDM + Yield Mean-Reversion lens.

    Sub-anchor A (DDM): 5y explicit dividend growth + Gordon terminal,
    discounted at cost of equity.
    Sub-anchor B (yield mean-reversion): TTM dividend / median 5y yield.
    Active only when ≥3y history (median_5y_yield available).

    Returns None when:
      - TTM dividend = 0 (non-payer)
      - dividend_5y_cagr is None (insufficient growth history)
      - cost_of_equity ≤ terminal_growth (Gordon would blow up)
      - any input is non-finite (NaN guard)
    """
    inputs = cfg.get("valuation_inputs") or {}
    ttm = inputs.get("ttm_dividend") or 0.0
    raw_g = inputs.get("dividend_5y_cagr")
    median_yield = inputs.get("median_5y_yield")

    if ttm <= 0:
        return None
    if raw_g is None:
        return None

    # Cap growth at 15% (defense in depth — gather_data already caps,
    # but a user override via update_valuation_inputs could be higher).
    g = min(raw_g, 0.15)
    g_term = cfg.get("terminal_growth", 0.025)

    try:
        ke = dcf_calculator.compute_cost_of_equity(cfg)
    except (KeyError, ZeroDivisionError, TypeError):
        return None

    # NaN / non-finite guards
    for v in (ttm, g, g_term, ke):
        if v != v or v in (float("inf"), float("-inf")):
            return None

    if ke <= g_term:
        return None

    # ── Sub-anchor A: Two-stage DDM ─────────────────────────────
    stage1_years = 5
    pv_stage1 = 0.0
    d = ttm
    for n in range(1, stage1_years + 1):
        d = d * (1 + g)
        pv_stage1 += d / ((1 + ke) ** n)

    d_terminal = d  # D_5
    terminal_value = d_terminal * (1 + g_term) / (ke - g_term)
    pv_terminal = terminal_value / ((1 + ke) ** stage1_years)
    ddm_fv = pv_stage1 + pv_terminal

    # ── Sub-anchor B: Yield Mean-Reversion ──────────────────────
    yield_mr_fv = None
    if median_yield is not None and median_yield > 0:
        yield_mr_fv = ttm / median_yield

    # ── Range derivation ───────────────────────────────────────
    if yield_mr_fv is not None:
        fv_low = min(ddm_fv, yield_mr_fv)
        fv_high = max(ddm_fv, yield_mr_fv)
        fv_mid = (ddm_fv + yield_mr_fv) / 2.0
    else:
        fv_low = ddm_fv * 0.85
        fv_mid = ddm_fv
        fv_high = ddm_fv * 1.15

    return {
        "fv_low": fv_low,
        "fv_mid": fv_mid,
        "fv_high": fv_high,
        "details": {
            "ttm_dividend": ttm,
            "growth_rate_stage1": g,
            "terminal_growth": g_term,
            "cost_of_equity": ke,
            "stage1_years": stage1_years,
            "ddm_fv": ddm_fv,
            "yield_mr_fv": yield_mr_fv,
            "median_5y_yield": median_yield,
        },
    }
```

- [ ] **Step 4: Run the targeted tests**

Run: `python3 -m pytest tests/test_multi_lens.py -k "dividend_lens or default_lens_weights_dividend or orchestrator_includes_dividend" -v`
Expected: All 8 tests PASS.

- [ ] **Step 5: Run the full multi-lens suite to confirm no regressions**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: previously passing tests still pass; 8 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "$(cat <<'EOF'
feat(lenses): implement Dividend lens (Phase 2-C)

Hybrid Two-stage DDM + Yield Mean-Reversion. Skips non-payers,
recent initiators (no CAGR baseline), and degenerate ke ≤ g_term
configs. Default weight stays 0.0 in DEFAULT_LENS_WEIGHTS — opt-in
per ticker via cfg.lens_weights. The lens still computes and stores
its result so MCP / detail-page consumers can inspect it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Add `update_valuation_inputs` MCP tool

**Files:**
- Modify: `mcp_server.py` (add new `_update_valuation_inputs_impl` + `@mcp.tool` wrapper)
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_mcp_server.py` (find a sensible location — e.g. near the end of the file, after existing MCP-tool tests):

```python
def test_update_valuation_inputs_writes_field(monkeypatch):
    """Calling the tool writes the field into cfg.valuation_inputs and saves."""
    import json as _json
    import mcp_server

    storage = {
        "TEST": {
            "company": "Test",
            "ticker": "TEST",
            "valuation_inputs": {
                "_auto_filled": ["dividend_5y_cagr"],
                "dividend_5y_cagr": 0.05,
            },
        },
    }

    def fake_load(client, ticker, user_id=None):
        return dict(storage[ticker.upper()])

    def fake_save(client, ticker, cfg, user_id=None):
        storage[ticker.upper()] = dict(cfg)

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_valuation_inputs_impl(
        "TEST", {"dividend_5y_cagr": 0.10}
    )
    result = _json.loads(result_json)

    assert result["dividend_5y_cagr"] == 0.10
    saved = storage["TEST"]
    assert saved["valuation_inputs"]["dividend_5y_cagr"] == 0.10


def test_update_valuation_inputs_removes_from_auto_filled(monkeypatch):
    """Overriding a field removes it from _auto_filled so future refresh
    won't overwrite the user value."""
    import json as _json
    import mcp_server

    storage = {
        "TEST": {
            "ticker": "TEST",
            "valuation_inputs": {
                "_auto_filled": ["dividend_5y_cagr", "ttm_dividend"],
                "dividend_5y_cagr": 0.05,
                "ttm_dividend": 4.00,
            },
        },
    }

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    mcp_server._update_valuation_inputs_impl("TEST", {"dividend_5y_cagr": 0.10})
    saved = storage["TEST"]
    assert "dividend_5y_cagr" not in saved["valuation_inputs"]["_auto_filled"]
    # Other auto-filled fields untouched
    assert "ttm_dividend" in saved["valuation_inputs"]["_auto_filled"]


def test_update_valuation_inputs_preserves_other_fields(monkeypatch):
    """Updating one field doesn't disturb others."""
    import json as _json
    import mcp_server

    storage = {
        "TEST": {
            "ticker": "TEST",
            "valuation_inputs": {
                "_auto_filled": ["dividend_5y_cagr", "ttm_dividend", "forward_eps"],
                "dividend_5y_cagr": 0.05,
                "ttm_dividend": 4.00,
                "forward_eps": 8.00,
            },
        },
    }

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    mcp_server._update_valuation_inputs_impl("TEST", {"dividend_5y_cagr": 0.10})
    saved = storage["TEST"]
    assert saved["valuation_inputs"]["ttm_dividend"] == 4.00
    assert saved["valuation_inputs"]["forward_eps"] == 8.00


def test_update_valuation_inputs_unknown_ticker_returns_error(monkeypatch):
    """If the ticker isn't on the watchlist, return a JSON error string."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_valuation_inputs_impl(
        "UNKNOWN", {"dividend_5y_cagr": 0.10}
    )
    assert "error" in _json.loads(result_json)
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest test_mcp_server.py -k "update_valuation_inputs" -v`
Expected: FAIL — `_update_valuation_inputs_impl` doesn't exist yet.

- [ ] **Step 3: Add the impl + tool**

In `mcp_server.py`, immediately AFTER `_get_watchlist_impl` (around line 339, just before the `# MCP Tools` comment), add:

```python
def _update_valuation_inputs_impl(ticker: str, fields: dict) -> str:
    """Core logic for update_valuation_inputs. Merges fields into
    cfg["valuation_inputs"] and removes them from _auto_filled so the
    user override survives the next refresh."""
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    for k, v in fields.items():
        inputs[k] = v
        if k in auto_filled:
            auto_filled.remove(k)
    inputs["_auto_filled"] = auto_filled

    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return json.dumps(inputs, default=str)
```

Then in the `# MCP Tools` section, find an appropriate spot (e.g. after `save_to_watchlist`) and add the tool wrapper:

```python
@mcp.tool()
def update_valuation_inputs(ticker: str, fields: dict) -> str:
    """Override one or more valuation_inputs fields for a watchlist ticker.

    Use this to inject your own view (e.g. expected dividend growth, forward
    EPS) that should NOT be overwritten by the next yfinance auto-refresh.
    Each updated field is removed from `_auto_filled` so subsequent refreshes
    preserve the override.

    Args:
        ticker: Stock ticker (e.g. "PEP")
        fields: Dict of valuation_inputs keys to set. Examples:
            {"dividend_5y_cagr": 0.08}
            {"forward_eps": 6.50, "ttm_ebitda": 15000}
            {"median_5y_yield": 0.025}

    Returns:
        JSON string with the updated valuation_inputs dict, or
        {"error": "..."} if ticker is not on the watchlist.
    """
    try:
        return _update_valuation_inputs_impl(ticker, fields)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run the targeted tests**

Run: `python3 -m pytest test_mcp_server.py -k "update_valuation_inputs" -v`
Expected: All 4 PASS.

- [ ] **Step 5: Run the full mcp_server test file**

Run: `python3 -m pytest test_mcp_server.py -v`
Expected: previously passing tests still pass; 4 new tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py test_mcp_server.py
git commit -m "$(cat <<'EOF'
feat(mcp): add update_valuation_inputs tool

Generic per-field override for cfg.valuation_inputs. Each updated
field is removed from _auto_filled so the user override survives
subsequent yfinance refreshes. Useful for the Dividend lens
(expected dividend_5y_cagr, target median_5y_yield) but also for
forward_eps, ttm_ebitda, historical_trailing_pe, etc.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: 4-lens UI integration

**Files:**
- Modify: `streamlit_app.py:_render_lens_dots` (lines 113-142, the `order` list)
- Modify: `streamlit_app.py:_render_football_field` `lens_order` (around line 244)
- Modify: `config_store.py:_COUNTED_LENSES` (around line 167)
- Modify: `scripts/force_refresh_all.py:_counted` (around line 81)
- Test: `tests/test_watchlist_ui.py` and `tests/test_multi_lens.py`

- [ ] **Step 1: Update the failing tests**

In `tests/test_watchlist_ui.py`, replace `test_render_lens_dots_all_active`:

```python
def test_render_lens_dots_all_active():
    """All four forward-looking lenses active → 4 filled dots, '4 lenses' label.
    (reverse_dcf intentionally not rendered — it anchors at price.)"""
    lenses = {
        "dcf": {}, "multiples": {}, "historical": {}, "dividend": {},
        "reverse_dcf": {}, "dividend_stub": None,
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 4
    assert 'class="ld-off"' not in html
    assert "4 lenses" in html
```

Replace `test_render_lens_dots_dcf_only`:

```python
def test_render_lens_dots_dcf_only():
    """Only DCF active → 1 filled dot, 3 grey dots, '1 lens' label."""
    lenses = {"dcf": {}, "multiples": None, "historical": None, "dividend": None, "reverse_dcf": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1
    assert html.count('class="ld-off"') == 3
    assert "1 lens" in html
```

Replace `test_render_lens_dots_empty_dict`:

```python
def test_render_lens_dots_empty_dict():
    """No lenses at all → 'no lenses' label, all 4 dots grey."""
    html = streamlit_app._render_lens_dots({}, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert html.count('class="ld-off"') == 4
    assert "no lenses" in html
```

Replace `test_render_lens_dots_3_active_after_demote` (or whatever it was renamed to in the previous Reverse-DCF demote work):

```python
def test_render_lens_dots_dividend_skipped_for_non_payer():
    """Non-payer with 3 forward lenses active + dividend skipped → 3 dots on, 1 off."""
    lenses = {
        "dcf": {}, "multiples": {}, "historical": {},
        "dividend": None, "reverse_dcf": {},
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 3
    assert html.count('class="ld-off"') == 1
    assert "3 lenses" in html
```

Replace `test_render_football_field_renders_all_active_lenses`:

```python
def test_render_football_field_renders_all_active_lenses():
    """Full summary → HTML contains 4 forward-lens bars (DCF, Peers,
    Historical, Dividend) + price marker. Reverse DCF intentionally absent."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 80.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 120.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":         {"fv_low": 90.0,  "fv_mid": 100.0, "fv_high": 110.0},
            "multiples":   {"fv_low": 70.0,  "fv_mid": 95.0,  "fv_high": 130.0},
            "historical":  {"fv_low": 95.0,  "fv_mid": 105.0, "fv_high": 115.0},
            "dividend":    {"fv_low": 85.0,  "fv_mid": 95.0,  "fv_high": 105.0},
            "reverse_dcf": {"fv_low": 100.0, "fv_mid": 100.0, "fv_high": 100.0},
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    assert "DCF" in html
    assert "Peers" in html
    assert "Historical" in html
    assert "Dividend" in html
    assert "Reverse DCF" not in html
    assert "$100" in html or "100.00" in html
    assert html.count('class="ff-bar"') == 4
```

In `tests/test_multi_lens.py`, update `test_list_watchlist_enriched_shape` so the `WITH` row's lenses dict includes `dividend` and the assertion now expects `lens_count == 4`. Find the fixture summary (search for `"historical": {}` — it was added in the Reverse-DCF demote work):

```python
        "lenses": {"dcf": {}, "multiples": {}, "historical": {}, "reverse_dcf": {}, "dividend": None},
```

Replace with:

```python
        "lenses": {"dcf": {}, "multiples": {}, "historical": {}, "dividend": {}, "reverse_dcf": {}},
```

And the assertion:

```python
    assert with_row["lens_count"] == 3  # dcf + multiples + historical (reverse_dcf and dividend excluded from count)
```

Replace with:

```python
    assert with_row["lens_count"] == 4  # dcf + multiples + historical + dividend (reverse_dcf excluded)
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_watchlist_ui.py tests/test_multi_lens.py -k "lens_dots or football_field or list_watchlist_enriched_shape" -v`
Expected: many of these FAIL — the production code still iterates over the 3-lens lists from the Reverse-DCF demote work.

- [ ] **Step 3: Update `_render_lens_dots`**

In `streamlit_app.py` around lines 113-142, find:

```python
    order = ["dcf", "multiples", "historical"]
```

Replace with:

```python
    order = ["dcf", "multiples", "historical", "dividend"]
```

Update the docstring above it to mention dividend:

```python
    """Render N dots showing which forward-looking lenses are active + a count label.

    Order: dcf · multiples · historical · dividend. Reverse DCF is intentionally not
    rendered — it anchors at current price by definition (see
    docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md).

    Each lens key maps to a non-None lens dict (active, green dot) or None
    (skipped, grey dot). Label: "{N} lens" or "{N} lenses" or "no lenses".
    """
```

- [ ] **Step 4: Update `_render_football_field`**

In `streamlit_app.py` around line 244, find:

```python
    lens_order = [
        ("dcf", "DCF"),
        ("multiples", "Peers"),
        ("historical", "Historical"),
        # "reverse_dcf" intentionally omitted — its bar would overlap the
        # Price marker (lens always returns fv = stock_price). See
        # docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md.
    ]
```

Replace with:

```python
    lens_order = [
        ("dcf", "DCF"),
        ("multiples", "Peers"),
        ("historical", "Historical"),
        ("dividend", "Dividend"),
        # "reverse_dcf" intentionally omitted — its bar would overlap the
        # Price marker (lens always returns fv = stock_price). See
        # docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md.
    ]
```

- [ ] **Step 5: Update `config_store._COUNTED_LENSES`**

In `config_store.py` around line 167, find:

```python
    _COUNTED_LENSES = ("dcf", "multiples", "historical")
```

Replace with:

```python
    _COUNTED_LENSES = ("dcf", "multiples", "historical", "dividend")
```

(Update the surrounding comment to mention `dividend` as well so future readers see this is intentional.)

- [ ] **Step 6: Update `scripts/force_refresh_all.py`**

In `scripts/force_refresh_all.py` around line 83, find:

```python
                _counted = ("dcf", "multiples", "historical")
```

Replace with:

```python
                _counted = ("dcf", "multiples", "historical", "dividend")
```

- [ ] **Step 7: Run the targeted tests**

Run: `python3 -m pytest tests/test_watchlist_ui.py tests/test_multi_lens.py -k "lens_dots or football_field or list_watchlist_enriched_shape" -v`
Expected: All updated tests PASS.

- [ ] **Step 8: Run the full test suites for both files**

Run: `python3 -m pytest tests/test_watchlist_ui.py tests/test_multi_lens.py -v`
Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add streamlit_app.py config_store.py scripts/force_refresh_all.py tests/test_watchlist_ui.py tests/test_multi_lens.py
git commit -m "$(cat <<'EOF'
ui(watchlist): re-add dividend to 4-lens display

Lens-dots and football field both now render 4 forward-looking
lenses (DCF · Peers · Historical · Dividend). config_store and
the CLI batch script's _COUNTED_LENSES extend in lockstep.
Reverse DCF stays out of the watchlist UI per the 2026-05-07
demote — only the four forward-looking lenses are surfaced.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final verification — ruff + pytest + smoke

**Why:** All five logic tasks plus the UI task have shipped. Sanity check the full test suite, ruff, and a manual streamlit smoke before merging.

**Files:** none new — verification only.

- [ ] **Step 1: Ruff lint**

Run: `python3 -m ruff check streamlit_app.py mcp_server.py auto_fetch.py valuation_lenses.py gather_data.py config_store.py dcf_calculator.py scripts/force_refresh_all.py tests/test_multi_lens.py tests/test_market_data.py tests/test_watchlist_ui.py test_mcp_server.py`
Expected: same baseline error count as before this branch (no new errors introduced).

- [ ] **Step 2: Full pytest suite**

Run: `python3 -m pytest tests/ test_mcp_server.py test_tastytrade_api.py test_ibkr_api.py -v`
Expected: all tests PASS. Multi-lens, market-data, watchlist-ui, mcp-server suites should all be green.

- [ ] **Step 3: Manual smoke — Streamlit watchlist row**

Run: `streamlit run streamlit_app.py`. Open the watchlist tab. For at least one ticker that pays dividends (e.g. PEP, MSFT, V) and one that doesn't (ABNB):

**Dividend payer:**
- Lens-dots row shows **4 dots active** (DCF · Peers · Historical · Dividend), label "4 lenses"
- Hover "details ›" → tooltip's football field shows **4 horizontal bars** + Price marker
- The Dividend bar is in a sensible range (typically near or below current price for mature payers)

**Non-payer (ABNB):**
- Lens-dots row shows **3 dots active + 1 grey** (Dividend dot is greyed out), label "3 lenses"
- Hover "details ›" → tooltip's football field shows **3 horizontal bars** (no Dividend bar)

**Refresh All:** click the button. Watch the progress bar; should complete in 30-90s for the typical watchlist. After completion, dividend lens entries are populated for payers (visible by hovering the details).

If any of these checks fail, stop and report which one failed before considering the branch ready.

- [ ] **Step 4: Manual smoke — MCP `update_valuation_inputs`**

In Claude Desktop (after Cmd+Q + reopen so the MCP picks up the new tool):
- Ask Claude: "Use update_valuation_inputs to set PEP's expected dividend growth to 8%"
- Expected: Claude calls the tool, reports the updated valuation_inputs dict
- Then ask: "calculate_multi_lens_valuation for PEP" — expected: dividend lens uses the 8% growth, not the trailing CAGR; subsequent Refresh All preserves the 8%

If the MCP tool isn't visible in Claude Desktop, restart Claude Desktop fully (Cmd+Q + reopen, not just window close).

- [ ] **Step 5: No commit — verification only**

Tasks 1-6 are already committed. If smoke passes, the branch is ready to finish via the `superpowers:finishing-a-development-branch` skill.

---

## Notes for the implementer

- **Branch handling:** start with a fresh feature branch off main: `git checkout -b feature/dividend-lens`. Tasks land sequentially with one commit each (~7 commits total).
- **No DB migration:** existing `valuation_summary` blobs in Supabase remain valid; the next "↻ Refresh all" populates the new `dividend` lens entry alongside the existing four. The dividend lens has weight 0.0 by default so existing `weighted_fv_mid` numbers don't change.
- **Spec's `compute_wacc(cfg)["cost_of_equity"]` was a misstatement** — `compute_wacc` returns a float, not a dict. Task 1 introduces the `compute_cost_of_equity(cfg)` helper the spec needs; the spec was updated inline (search for `compute_cost_of_equity` in the spec).
- **MCP restart required after merge:** the new `update_valuation_inputs` tool only becomes available in Claude Desktop after a full restart (Cmd+Q + reopen). The Streamlit Cloud deploy of the rest happens automatically on push to main.
- **Cap on dividend growth (15%):** applied in two places defensively — at the gather_data level (Task 2) and at the lens level (Task 4). The lens-level cap is belt-and-suspenders for cases where a user passes a higher value via `update_valuation_inputs`.
