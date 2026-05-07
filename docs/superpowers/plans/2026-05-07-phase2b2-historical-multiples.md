# Phase 2-B.2: Historical Multiples Auto-Fetch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-fetch 4-year median historical trailing P/E and EV/EBITDA from yfinance into `valuation_inputs`, plus add two new "own historical" sub-anchors (A.2, D) to the multiples lens.

**Architecture:** New pure helper `fetch_historical_multiples(ticker)` in `gather_data.py` that builds monthly trailing-multiple time series from yfinance and returns medians. Existing `_auto_fill_valuation_inputs` in `streamlit_app.py` extends to call it. `compute_multiples_lens` gains two new sub-anchors that read the new fields.

**Tech Stack:** Python 3.11, pytest, ruff, yfinance (existing dependency).

**Spec:** `docs/superpowers/specs/2026-05-07-phase2b2-historical-multiples-design.md`

---

## File Map

| Path | Purpose | Action |
|------|---------|--------|
| `gather_data.py` | Add `fetch_historical_multiples(ticker)` near `fetch_market_inputs` (line ~1385) | Modify |
| `streamlit_app.py` | Extend `_auto_fill_valuation_inputs` to call the new fetcher | Modify |
| `valuation_lenses.py` | Add sub-anchors A.2 (historical trailing P/E) and D (historical EV/EBITDA) to `compute_multiples_lens` | Modify |
| `tests/test_market_data.py` | 6 new tests for fetcher + auto-fill integration | Modify |
| `tests/test_multi_lens.py` | 2 new tests for multiples lens sub-anchors | Modify |

---

## Test Fixtures

The fixture helper `make_yf_info` in `tests/test_market_data.py` (Phase 2-B) is reused. New helper for mocking yfinance's full historical interface — added in Task 1, used by Tasks 2-3.

```python
def make_yf_history(months=48, base_price=100.0, growth_pct=0.10):
    """Build a yfinance Ticker.history(period='4y', interval='1mo')-like
    DataFrame with `months` rows of monthly Close prices growing at
    growth_pct per year (linear)."""
    import pandas as pd
    import numpy as np
    dates = pd.date_range(end="2026-05-01", periods=months, freq="ME")
    monthly_growth = (1 + growth_pct) ** (1 / 12) - 1
    closes = [base_price * (1 + monthly_growth) ** i for i in range(months)]
    return pd.DataFrame({"Close": closes}, index=dates)


def make_yf_income_stmt(eps_per_year=None, ebitda_per_year=None):
    """Build a yfinance Ticker.income_stmt-like DataFrame.

    eps_per_year: dict {2022: 5.0, 2023: 6.0, 2024: 7.0, 2025: 8.0} (descending years)
    ebitda_per_year: dict {2022: 80e9, ...}
    """
    import pandas as pd
    if eps_per_year is None:
        eps_per_year = {2025: 8.0, 2024: 7.0, 2023: 6.0, 2022: 5.0}
    if ebitda_per_year is None:
        ebitda_per_year = {2025: 100e9, 2024: 90e9, 2023: 80e9, 2022: 70e9}
    columns = sorted(eps_per_year.keys(), reverse=True)
    cols = pd.DatetimeIndex([f"{y}-12-31" for y in columns])
    rows = {}
    rows["Diluted EPS"] = [eps_per_year[y] for y in columns]
    rows["EBITDA"] = [ebitda_per_year.get(y) for y in columns]
    return pd.DataFrame(rows, index=cols).T


def make_yf_quarterly_balance_sheet(debt_per_quarter=None, cash_per_quarter=None):
    """Build a yfinance Ticker.quarterly_balance_sheet-like DataFrame."""
    import pandas as pd
    if debt_per_quarter is None:
        # 16 quarters of $50B debt
        debt_per_quarter = [50e9] * 16
    if cash_per_quarter is None:
        cash_per_quarter = [80e9] * 16
    cols = pd.date_range(end="2026-03-31", periods=len(debt_per_quarter), freq="QE")
    rows = {
        "Total Debt": debt_per_quarter,
        "Cash And Cash Equivalents": cash_per_quarter,
    }
    return pd.DataFrame(rows, index=cols).T


def patch_yfinance_full(info=None, history=None, income_stmt=None, qbs=None):
    """Comprehensive yfinance mock for fetch_historical_multiples."""
    from unittest.mock import MagicMock, patch
    fake_ticker = MagicMock()
    fake_ticker.info = info or {}
    fake_ticker.history = MagicMock(return_value=history if history is not None else make_yf_history())
    fake_ticker.income_stmt = income_stmt if income_stmt is not None else make_yf_income_stmt()
    fake_ticker.quarterly_balance_sheet = qbs if qbs is not None else make_yf_quarterly_balance_sheet()
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    return patch.dict("sys.modules", {"yfinance": fake_yf})
```

---

### Task 1: Test fixtures for historical multiples

**Files:**
- Modify: `tests/test_market_data.py` (add fixture helpers near the top)

- [ ] **Step 1: Append fixture helpers**

After the existing `patch_yfinance_info` helper (around line 30) in `tests/test_market_data.py`, append:

```python
def make_yf_history(months=48, base_price=100.0, growth_pct=0.10):
    """Build a yfinance Ticker.history(period='4y', interval='1mo')-like
    DataFrame with `months` rows of monthly Close prices growing at
    growth_pct per year (linear)."""
    import pandas as pd
    dates = pd.date_range(end="2026-05-01", periods=months, freq="ME")
    monthly_growth = (1 + growth_pct) ** (1 / 12) - 1
    closes = [base_price * (1 + monthly_growth) ** i for i in range(months)]
    return pd.DataFrame({"Close": closes}, index=dates)


def make_yf_income_stmt(eps_per_year=None, ebitda_per_year=None):
    """Build a yfinance Ticker.income_stmt-like DataFrame."""
    import pandas as pd
    if eps_per_year is None:
        eps_per_year = {2025: 8.0, 2024: 7.0, 2023: 6.0, 2022: 5.0}
    if ebitda_per_year is None:
        ebitda_per_year = {2025: 100e9, 2024: 90e9, 2023: 80e9, 2022: 70e9}
    columns = sorted(eps_per_year.keys(), reverse=True)
    cols = pd.DatetimeIndex([f"{y}-12-31" for y in columns])
    rows = {
        "Diluted EPS": [eps_per_year[y] for y in columns],
        "EBITDA": [ebitda_per_year.get(y) for y in columns],
    }
    return pd.DataFrame(rows, index=cols).T


def make_yf_quarterly_balance_sheet(debt_per_quarter=None, cash_per_quarter=None):
    """Build a yfinance Ticker.quarterly_balance_sheet-like DataFrame."""
    import pandas as pd
    if debt_per_quarter is None:
        debt_per_quarter = [50e9] * 16
    if cash_per_quarter is None:
        cash_per_quarter = [80e9] * 16
    cols = pd.date_range(end="2026-03-31", periods=len(debt_per_quarter), freq="QE")
    rows = {
        "Total Debt": debt_per_quarter,
        "Cash And Cash Equivalents": cash_per_quarter,
    }
    return pd.DataFrame(rows, index=cols).T


def patch_yfinance_full(info=None, history=None, income_stmt=None, qbs=None):
    """Comprehensive yfinance mock for fetch_historical_multiples."""
    from unittest.mock import MagicMock, patch
    fake_ticker = MagicMock()
    fake_ticker.info = info or {}
    fake_ticker.history = MagicMock(return_value=history if history is not None else make_yf_history())
    fake_ticker.income_stmt = income_stmt if income_stmt is not None else make_yf_income_stmt()
    fake_ticker.quarterly_balance_sheet = qbs if qbs is not None else make_yf_quarterly_balance_sheet()
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    return patch.dict("sys.modules", {"yfinance": fake_yf})
```

- [ ] **Step 2: Verify fixtures import correctly (sanity test)**

Append a one-line test:

```python
def test_yf_history_fixture_shape():
    df = make_yf_history(months=48)
    assert len(df) == 48
    assert "Close" in df.columns
```

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_yf_history_fixture_shape -v`
Expected: PASS.

- [ ] **Step 3: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check tests/test_market_data.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_market_data.py
git commit -m "test: yfinance historical-data fixtures for Phase 2-B.2"
```

---

### Task 2: `fetch_historical_multiples` happy path

**Files:**
- Modify: `gather_data.py` (add directly after `enrich_peer_with_market_data`, around line 1450)
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_market_data.py`:

```python
def test_fetch_historical_multiples_happy_path():
    """All inputs available → returns three keys with reasonable values."""
    info = {"trailingEps": 8.5}
    history = make_yf_history(months=48, base_price=200.0, growth_pct=0.05)
    income = make_yf_income_stmt(
        eps_per_year={2025: 8.0, 2024: 7.0, 2023: 6.0, 2022: 5.0},
        ebitda_per_year={2025: 100e9, 2024: 90e9, 2023: 80e9, 2022: 70e9},
    )
    qbs = make_yf_quarterly_balance_sheet()
    with patch_yfinance_full(info=info, history=history, income_stmt=income, qbs=qbs):
        result = gather_data.fetch_historical_multiples("MSFT")

    assert "historical_trailing_pe" in result
    assert result["historical_trailing_pe"] > 0
    assert result["historical_trailing_pe"] < 100  # sanity: P/E within reasonable range
    assert "historical_ev_ebitda" in result
    assert result["historical_ev_ebitda"] > 0
    assert result["ttm_eps"] == 8.5
```

- [ ] **Step 2: Run test — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_fetch_historical_multiples_happy_path -v`
Expected: `AttributeError: module 'gather_data' has no attribute 'fetch_historical_multiples'`.

- [ ] **Step 3: Implement the fetcher**

In `gather_data.py`, add directly after `enrich_peer_with_market_data` (locate with `grep -n "^def enrich_peer_with_market_data" gather_data.py`):

```python
def fetch_historical_multiples(ticker: str) -> dict:
    """Compute 4-year median historical trailing P/E and EV/EBITDA from yfinance.

    Returns a dict with these keys (any may be absent when data insufficient):
        historical_trailing_pe:  float, 4-year monthly median price/ttm_eps
        historical_ev_ebitda:    float, 4-year monthly median EV/ttm_ebitda
        ttm_eps:                 float, current trailing EPS

    Skips months where the TTM denominator is <= 0.
    yfinance failures or insufficient data → returns empty dict, never raises.
    """
    import statistics

    try:
        import pandas as pd
        import yfinance as yf
        ticker_obj = yf.Ticker(ticker)
        info = ticker_obj.info
        history = ticker_obj.history(period="4y", interval="1mo")
        income = ticker_obj.income_stmt
        qbs = ticker_obj.quarterly_balance_sheet
    except ImportError:
        logger.warning("yfinance/pandas not installed; skipping historical multiples for %s", ticker)
        return {}
    except Exception as e:
        logger.warning("yfinance historical fetch failed for %s: %s", ticker, e)
        return {}

    out = {}

    # ttm_eps from current info (no computation)
    ttm_eps_current = info.get("trailingEps")
    if isinstance(ttm_eps_current, (int, float)) and ttm_eps_current > 0:
        out["ttm_eps"] = round(float(ttm_eps_current), 2)

    if history is None or len(history) < 24:
        logger.info("Historical multiples: %s has only %s months of price history; skipping",
                    ticker, 0 if history is None else len(history))
        return out

    months_dt = list(history.index)
    closes = [float(p) for p in history["Close"].values]

    # ---- Build monthly TTM-EPS series (linear interpolation between annual anchors) ----
    eps_series_monthly = _interpolate_yearly_to_monthly(income, "Diluted EPS", months_dt)
    if eps_series_monthly:
        pe_values = []
        for price, eps in zip(closes, eps_series_monthly):
            if eps is None or eps <= 0:
                continue
            pe_values.append(price / eps)
        if len(pe_values) >= 12:
            out["historical_trailing_pe"] = round(statistics.median(pe_values), 1)

    # ---- Build monthly TTM-EBITDA series + monthly EV → median EV/EBITDA ----
    ebitda_series_monthly = _interpolate_yearly_to_monthly(income, "EBITDA", months_dt)
    debt_series_monthly = _interpolate_quarterly_to_monthly(qbs, "Total Debt", months_dt)
    cash_series_monthly = _interpolate_quarterly_to_monthly(qbs, "Cash And Cash Equivalents", months_dt)

    shares = info.get("sharesOutstanding")
    if (ebitda_series_monthly and debt_series_monthly and cash_series_monthly
            and isinstance(shares, (int, float)) and shares > 0):
        ev_ebitda_values = []
        for price, ebitda, debt, cash in zip(closes, ebitda_series_monthly,
                                              debt_series_monthly, cash_series_monthly):
            if ebitda is None or ebitda <= 0:
                continue
            mkt_cap = price * shares
            ev = mkt_cap + (debt or 0) - (cash or 0)
            if ev <= 0:
                continue
            ev_ebitda_values.append(ev / ebitda)
        if len(ev_ebitda_values) >= 12:
            out["historical_ev_ebitda"] = round(statistics.median(ev_ebitda_values), 1)

    return out


def _interpolate_yearly_to_monthly(income_df, row_name, months_dt):
    """Linear-interpolate an annual income-statement row to a monthly series
    aligned to months_dt. Returns list of floats (or None for months that fall
    before the earliest annual anchor)."""
    if income_df is None or row_name not in income_df.index:
        return []
    annual_row = income_df.loc[row_name]
    points = []  # [(timestamp, value), ...] sorted ascending
    for col_dt, val in annual_row.items():
        if val is None:
            continue
        try:
            v = float(val)
            points.append((col_dt, v))
        except (TypeError, ValueError):
            continue
    if not points:
        return []
    points.sort(key=lambda p: p[0])

    series = []
    for m in months_dt:
        if m <= points[0][0]:
            series.append(points[0][1])
            continue
        if m >= points[-1][0]:
            series.append(points[-1][1])
            continue
        # interpolate between two adjacent annual anchors
        for i in range(len(points) - 1):
            a_dt, a_val = points[i]
            b_dt, b_val = points[i + 1]
            if a_dt <= m <= b_dt:
                span = (b_dt - a_dt).total_seconds()
                pos = (m - a_dt).total_seconds()
                t = pos / span if span > 0 else 0.0
                series.append(a_val + (b_val - a_val) * t)
                break
        else:
            series.append(None)
    return series


def _interpolate_quarterly_to_monthly(qbs_df, row_name, months_dt):
    """Linear-interpolate a quarterly balance-sheet row to a monthly series."""
    return _interpolate_yearly_to_monthly(qbs_df, row_name, months_dt)
```

Note: `_interpolate_quarterly_to_monthly` is a thin alias for now — same logic works since both annual and quarterly are timestamp-keyed columns. Kept as separate name for readability and future divergence.

- [ ] **Step 4: Run test — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_fetch_historical_multiples_happy_path -v`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check gather_data.py tests/test_market_data.py`
Expected: no NEW violations.

- [ ] **Step 6: Commit**

```bash
git add gather_data.py tests/test_market_data.py
git commit -m "feat: fetch_historical_multiples computes 4y median trailing P/E + EV/EBITDA"
```

---

### Task 3: `fetch_historical_multiples` edge cases

**Files:**
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Append failing tests**

```python
def test_fetch_historical_multiples_negative_eps_quarter_skipped():
    """A loss year doesn't crash the median; negative-eps months are excluded."""
    info = {"trailingEps": 8.0}
    income = make_yf_income_stmt(
        eps_per_year={2025: 8.0, 2024: 7.0, 2023: -1.0, 2022: 5.0},  # 2023 was a loss
    )
    with patch_yfinance_full(info=info, income_stmt=income):
        result = gather_data.fetch_historical_multiples("XYZ")

    assert "historical_trailing_pe" in result
    assert result["historical_trailing_pe"] > 0  # negative-eps months excluded; rest still positive


def test_fetch_historical_multiples_insufficient_history():
    """Too few months → returns empty (need ≥24 months)."""
    info = {"trailingEps": 5.0}
    short_history = make_yf_history(months=6)
    with patch_yfinance_full(info=info, history=short_history):
        result = gather_data.fetch_historical_multiples("RECENT_IPO")
    # ttm_eps still populated (info has it), but historical metrics absent
    assert "historical_trailing_pe" not in result
    assert "historical_ev_ebitda" not in result
    assert result.get("ttm_eps") == 5.0


def test_fetch_historical_multiples_yfinance_error():
    """yf.Ticker(...) raises → returns empty dict, no crash."""
    from unittest.mock import MagicMock, patch as _patch
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(side_effect=Exception("network down"))
    with _patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_historical_multiples("XYZ")
    assert result == {}


def test_fetch_historical_multiples_missing_ebitda():
    """No EBITDA data → trailing-PE still computed, EV/EBITDA absent."""
    info = {"trailingEps": 5.0}
    income = make_yf_income_stmt(
        eps_per_year={2025: 5.0, 2024: 4.5, 2023: 4.0, 2022: 3.5},
        ebitda_per_year={2025: None, 2024: None, 2023: None, 2022: None},
    )
    with patch_yfinance_full(info=info, income_stmt=income):
        result = gather_data.fetch_historical_multiples("XYZ")
    assert "historical_trailing_pe" in result
    assert "historical_ev_ebitda" not in result


def test_fetch_historical_multiples_no_shares_outstanding():
    """Missing sharesOutstanding → EV cannot be computed → ev_ebitda absent."""
    info = {"trailingEps": 5.0}  # no sharesOutstanding
    with patch_yfinance_full(info=info):
        result = gather_data.fetch_historical_multiples("XYZ")
    assert "historical_trailing_pe" in result
    assert "historical_ev_ebitda" not in result
```

- [ ] **Step 2: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -k historical_multiples -v`
Expected: 6 passed (1 happy path + 5 edge cases).

- [ ] **Step 3: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check tests/test_market_data.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_market_data.py
git commit -m "test: pin edge cases for fetch_historical_multiples"
```

---

### Task 4: Wire `fetch_historical_multiples` into `_auto_fill_valuation_inputs`

**Files:**
- Modify: `streamlit_app.py` — `_auto_fill_valuation_inputs` (locate with `grep -n "^def _auto_fill_valuation_inputs" streamlit_app.py`)
- Modify: `tests/test_market_data.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_market_data.py`:

```python
def test_auto_fill_inputs_includes_historical_multiples():
    """_auto_fill_valuation_inputs writes historical_trailing_pe, historical_ev_ebitda,
    and ttm_eps from fetch_historical_multiples in addition to forward_eps + ttm_ebitda."""
    cfg = {"ticker": "MSFT", "valuation_inputs": {}}
    info = {
        "forwardEps": 19.42, "trailingEbitda": 184e9, "trailingEps": 16.78,
        "sharesOutstanding": 7.43e9,
    }
    with patch_yfinance_full(info=info):
        streamlit_app._auto_fill_valuation_inputs(cfg)

    inputs = cfg["valuation_inputs"]
    # Phase 2-B fields:
    assert inputs.get("forward_eps") == 19.42
    assert inputs.get("ttm_ebitda") == round(184e9 / 1e6, 0)
    # Phase 2-B.2 fields:
    assert inputs.get("ttm_eps") == 16.78
    assert "historical_trailing_pe" in inputs
    assert "historical_ev_ebitda" in inputs
    # All three new keys in _auto_filled
    auto_filled = set(inputs.get("_auto_filled", []))
    assert {"ttm_eps", "historical_trailing_pe", "historical_ev_ebitda"}.issubset(auto_filled)
```

- [ ] **Step 2: Run test — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_auto_fill_inputs_includes_historical_multiples -v`
Expected: FAIL — historical keys not in inputs (auto_fill currently only calls fetch_market_inputs).

- [ ] **Step 3: Modify `_auto_fill_valuation_inputs`**

Locate the helper:

```bash
grep -n "^def _auto_fill_valuation_inputs" streamlit_app.py
```

Inside it, find the line:

```python
    fetched = gather_data.fetch_market_inputs(cfg.get("ticker", ""))
```

Replace with:

```python
    fetched = gather_data.fetch_market_inputs(cfg.get("ticker", ""))
    # Phase 2-B.2: also fetch historical multiples
    fetched.update(gather_data.fetch_historical_multiples(cfg.get("ticker", "")))
```

`fetched` is a plain dict; `dict.update` merges keys. `fetch_historical_multiples` returns disjoint keys (`historical_trailing_pe`, `historical_ev_ebitda`, `ttm_eps`), so no collision.

- [ ] **Step 4: Run test — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py::test_auto_fill_inputs_includes_historical_multiples -v`
Expected: PASS.

- [ ] **Step 5: Run full test_market_data.py suite**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_market_data.py -v 2>&1 | tail -10`
Expected: all market_data tests pass.

- [ ] **Step 6: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check streamlit_app.py tests/test_market_data.py`
Expected: no NEW violations.

- [ ] **Step 7: Commit**

```bash
git add streamlit_app.py tests/test_market_data.py
git commit -m "feat: _auto_fill_valuation_inputs now includes historical multiples"
```

---

### Task 5: Multiples lens sub-anchor A.2 (own historical trailing P/E)

**Files:**
- Modify: `valuation_lenses.py` — `compute_multiples_lens` (locate with `grep -n "^def compute_multiples_lens" valuation_lenses.py`)
- Modify: `tests/test_multi_lens.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_multi_lens.py`:

```python
def test_multiples_lens_uses_historical_trailing_pe():
    """Sub-anchor A.2: historical_trailing_pe × ttm_eps contributes to fv_anchors."""
    cfg = make_cfg(
        valuation_inputs={
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,
            # Other inputs missing → A.2 is the only sub-anchor that fires
        },
    )
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is not None
    # 25.0 * 4.0 = 100.0
    assert lens["details"]["historical_trailing_pe_fv"] == pytest.approx(100.0)
    # Single anchor → low/mid/high all equal
    assert lens["fv_low"] == pytest.approx(100.0)
    assert lens["fv_mid"] == pytest.approx(100.0)
    assert lens["fv_high"] == pytest.approx(100.0)
```

- [ ] **Step 2: Run test — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py::test_multiples_lens_uses_historical_trailing_pe -v`
Expected: FAIL — `details["historical_trailing_pe_fv"]` does not exist (and the lens may return None because no other sub-anchor fires).

- [ ] **Step 3: Add sub-anchor A.2 to `compute_multiples_lens`**

In `valuation_lenses.py`, locate `compute_multiples_lens`. Find the existing block:

```python
    inputs = cfg.get("valuation_inputs") or {}
    peers = cfg.get("peers") or []

    fv_anchors = []
    details = {
        "fwd_pe_own": None,
        "fwd_pe_peer_median": None,
        "ev_ebitda_peer_median": None,
        "closest_peer": None,
        "skipped": [],
    }

    forward_eps = inputs.get("forward_eps")
    historical_fwd_pe = inputs.get("historical_fwd_pe")
    ttm_ebitda = inputs.get("ttm_ebitda")
```

Replace with:

```python
    inputs = cfg.get("valuation_inputs") or {}
    peers = cfg.get("peers") or []

    fv_anchors = []
    details = {
        "fwd_pe_own": None,
        "fwd_pe_peer_median": None,
        "ev_ebitda_peer_median": None,
        "historical_trailing_pe_fv": None,    # NEW (Phase 2-B.2)
        "historical_ev_ebitda_fv": None,      # NEW (Phase 2-B.2)
        "closest_peer": None,
        "skipped": [],
    }

    forward_eps = inputs.get("forward_eps")
    historical_fwd_pe = inputs.get("historical_fwd_pe")
    ttm_ebitda = inputs.get("ttm_ebitda")
    historical_trailing_pe = inputs.get("historical_trailing_pe")    # NEW
    historical_ev_ebitda = inputs.get("historical_ev_ebitda")        # NEW
    ttm_eps = inputs.get("ttm_eps")                                  # NEW
```

Then find the existing block for sub-anchor A (own forward P/E):

```python
    # A) own historical forward P/E
    if forward_eps and historical_fwd_pe:
        own_fv = historical_fwd_pe * forward_eps
        fv_anchors.append(own_fv)
        details["fwd_pe_own"] = own_fv
    else:
        reason = "fwd_pe_own (forward_eps or historical_fwd_pe missing)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)
```

Append directly after it:

```python
    # A.2) own historical trailing P/E × ttm_eps (Phase 2-B.2)
    if historical_trailing_pe and ttm_eps and ttm_eps > 0:
        own_trailing_fv = historical_trailing_pe * ttm_eps
        fv_anchors.append(own_trailing_fv)
        details["historical_trailing_pe_fv"] = own_trailing_fv
    else:
        reason = "historical_trailing_pe (no historical_trailing_pe or ttm_eps)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)
```

- [ ] **Step 4: Run test — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py::test_multiples_lens_uses_historical_trailing_pe -v`
Expected: PASS.

- [ ] **Step 5: Run full test_multi_lens.py suite to ensure no regression**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py -v 2>&1 | tail -10`
Expected: all 30+ tests pass.

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: multiples lens sub-anchor A.2 (own historical trailing P/E × ttm_eps)"
```

---

### Task 6: Multiples lens sub-anchor D (own historical EV/EBITDA)

**Files:**
- Modify: `valuation_lenses.py`
- Modify: `tests/test_multi_lens.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_multi_lens.py`:

```python
def test_multiples_lens_uses_historical_ev_ebitda():
    """Sub-anchor D: historical_ev_ebitda × ttm_ebitda - net_debt → /shares."""
    cfg = make_cfg(
        valuation_inputs={
            "historical_ev_ebitda": 15.0,
            "ttm_ebitda": 10_000.0,  # in $M
            # ttm_eps missing → A.2 doesn't fire
        },
    )
    # net_debt = debt(10_000) - cash(5_000) - securities(0) = 5_000
    # ev = 15.0 * 10_000 = 150_000  (in $M)
    # equity = ev - net_debt = 145_000  (in $M)
    # per share = 145_000 / 1_000 shares_outstanding = 145.0 (per share, not $M!)
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is not None
    assert lens["details"]["historical_ev_ebitda_fv"] == pytest.approx(145.0)
```

- [ ] **Step 2: Run test — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py::test_multiples_lens_uses_historical_ev_ebitda -v`
Expected: FAIL — `historical_ev_ebitda_fv` not populated.

- [ ] **Step 3: Add sub-anchor D to `compute_multiples_lens`**

In `valuation_lenses.py`, find the existing sub-anchor C block (peer EV/EBITDA):

```python
    # C) peer EV/EBITDA
    peer_ev_ebitda_pairs = [(p["ticker"], p["ev_ebitda"]) for p in peers if p.get("ev_ebitda")]
    ...
    if peer_ev_ebitdas and ttm_ebitda:
        ...
        fv_anchors.extend([fv_low_e, fv_mid_e, fv_high_e])
        details["ev_ebitda_peer_median"] = fv_mid_e
    else:
        reason = "ev_ebitda_peer (no peers with ev_ebitda or no ttm_ebitda)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)
```

Append directly after it:

```python
    # D) own historical EV/EBITDA × ttm_ebitda - net_debt → /shares (Phase 2-B.2)
    if historical_ev_ebitda and ttm_ebitda:
        net_debt_d = (
            cfg.get("debt_market_value", 0.0)
            - cfg.get("cash_bridge", 0.0)
            - cfg.get("securities", 0.0)
        )
        shares_d = cfg.get("shares_outstanding") or 1.0
        own_evebitda_fv = (historical_ev_ebitda * ttm_ebitda - net_debt_d) / shares_d
        fv_anchors.append(own_evebitda_fv)
        details["historical_ev_ebitda_fv"] = own_evebitda_fv
    else:
        reason = "historical_ev_ebitda (no historical_ev_ebitda or ttm_ebitda)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)
```

- [ ] **Step 4: Run test — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py::test_multiples_lens_uses_historical_ev_ebitda -v`
Expected: PASS.

- [ ] **Step 5: Run full test_multi_lens.py + test_market_data.py suites**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m pytest tests/test_multi_lens.py tests/test_market_data.py -v 2>&1 | tail -10
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: multiples lens sub-anchor D (own historical EV/EBITDA)"
```

---

### Task 7: Lint + full regression suite

**Files:**
- None (verification only)

- [ ] **Step 1: Lint over the whole repo**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check .`
Expected: All checks pass on new code in `gather_data.py`, `streamlit_app.py`, `valuation_lenses.py`, `tests/test_market_data.py`, `tests/test_multi_lens.py`. Pre-existing violations elsewhere are not Phase-2-B.2's responsibility.

If new violations were introduced, fix them inline. Re-run until clean (modulo pre-existing).

- [ ] **Step 2: Full regression suite**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m pytest test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py tests/test_watchlist_ui.py tests/test_market_data.py 2>&1 | tail -3
```
Expected: 41 + 40 + 32 (was 30 + 2 new) + 29 + 31 (was 23 + 8 new) = 173 tests passed.

- [ ] **Step 3: Force-refresh on production data**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 scripts/force_refresh_all.py 2>&1 | tail -25
```

Expected: all 21 tickers refresh successfully; lens_count remains 3 for the same set as before (the new sub-anchors join the multiples lens, they don't enable it for tickers it was off for).

Spot-check a few `valuation_summary.lenses.multiples.details` for tickers like MSFT or ABT to confirm `historical_trailing_pe_fv` and `historical_ev_ebitda_fv` are populated:

```bash
python3 -c "
import os, json
env = json.load(open('/Users/administrator/Library/Application Support/Claude/claude_desktop_config.json'))['mcpServers']['lazytheta-dcf']['env']
for k, v in env.items(): os.environ[k] = v
import config_store
from supabase import create_client
client = create_client(env['SUPABASE_URL'], env['SUPABASE_SERVICE_KEY'])
cfg = config_store.load_config(client, 'MSFT', user_id=env['LAZYTHETA_USER_ID'])
mult = (cfg.get('valuation_summary') or {}).get('lenses', {}).get('multiples') or {}
print(json.dumps(mult.get('details') or {}, indent=2))
"
```

Expected: `historical_trailing_pe_fv` and `historical_ev_ebitda_fv` show numeric values; `historical_trailing_pe` and `historical_ev_ebitda` and `ttm_eps` populated in `valuation_inputs`.

- [ ] **Step 4: Commit (only if you fixed any lint issues in Step 1)**

If Step 1 required edits, commit them:
```bash
git add gather_data.py streamlit_app.py valuation_lenses.py tests/test_market_data.py tests/test_multi_lens.py
git commit -m "style: lint fixes for Phase 2-B.2"
```

If no edits were needed, skip this step (no commit).

---

## Summary of Commits (target sequence)

1. `test: yfinance historical-data fixtures for Phase 2-B.2`
2. `feat: fetch_historical_multiples computes 4y median trailing P/E + EV/EBITDA`
3. `test: pin edge cases for fetch_historical_multiples`
4. `feat: _auto_fill_valuation_inputs now includes historical multiples`
5. `feat: multiples lens sub-anchor A.2 (own historical trailing P/E × ttm_eps)`
6. `feat: multiples lens sub-anchor D (own historical EV/EBITDA)`
7. (optional) `style: lint fixes for Phase 2-B.2`

6-7 commits. Implementation should take ~1h via subagents — Tasks 1-3 are mechanical (yfinance mocking + helper math), Tasks 4-6 are wiring + lens extension.
