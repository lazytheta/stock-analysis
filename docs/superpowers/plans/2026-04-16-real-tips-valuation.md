# Real (TIPS) Valuation Mode — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `valuation_basis` parameter ("nominal" | "real") to the DCF tool so users can value stocks using TIPS-based real rates, eliminating inflation noise from valuations.

**Architecture:** Add `fetch_tips_yield()` to gather_data.py (FRED API), thread a `valuation_basis` parameter through `build_config()` → `_build_dcf_config_impl()` → MCP tool. When "real", use TIPS yield as Rf, deflate revenue growth by breakeven inflation, default terminal growth to 0.5%. Store reference fields (nominal Rf, breakeven) for context. No changes to dcf_calculator.py — the math is identical, only inputs differ.

**Tech Stack:** Python 3, FRED API (no key needed for DFII10 series), existing gather_data/dcf_calculator/mcp_server modules.

**Spec:** `docs/superpowers/specs/2026-04-16-real-tips-valuation-design.md`

---

## File Structure

```
gather_data.py       — Add fetch_tips_yield(), modify build_config() signature + logic
mcp_server.py        — Add valuation_basis parameter to _build_dcf_config_impl() and MCP tool
test_mcp_server.py   — Add tests for real valuation mode
```

No new files. No changes to `dcf_calculator.py` or `config_store.py` — the config dict gains new keys but the calculator reads only existing ones, and config_store serializes the full dict already.

---

## Task 1: Add `fetch_tips_yield()` to gather_data.py

**Files:**
- Modify: `gather_data.py` (after `fetch_treasury_yield()`, line ~1125)
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing test for fetch_tips_yield**

Add to `test_mcp_server.py`:

```python
def test_fetch_tips_yield_parses_fred_csv():
    """fetch_tips_yield() should parse FRED CSV and return the latest TIPS rate."""
    sample_csv = (
        b"observation_date,DFII10\n"
        b"2026-04-14,1.88\n"
        b"2026-04-15,1.90\n"
    )
    with patch("gather_data._http_get", return_value=sample_csv):
        rate = gather_data.fetch_tips_yield()
    assert rate == pytest.approx(0.019, abs=0.001)


def test_fetch_tips_yield_fallback_on_failure():
    """fetch_tips_yield() should return default 0.02 when FRED fetch fails."""
    with patch("gather_data._http_get", side_effect=Exception("network error")):
        rate = gather_data.fetch_tips_yield()
    assert rate == 0.02
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_fetch_tips_yield_parses_fred_csv test_mcp_server.py::test_fetch_tips_yield_fallback_on_failure -v`
Expected: FAIL — `AttributeError: module 'gather_data' has no attribute 'fetch_tips_yield'`

- [ ] **Step 3: Implement fetch_tips_yield**

Add to `gather_data.py` after `fetch_treasury_yield()` (after line 1124):

```python
TIPS_DEFAULT = 0.02  # 2% real rate fallback


def fetch_tips_yield():
    """Fetch current US 10-Year TIPS yield from FRED (series DFII10).

    Returns the real risk-free rate as a float (e.g. 0.019 for 1.9%).
    No API key required for this public CSV endpoint.
    """
    print("[TIPS] Fetching 10Y TIPS yield from FRED...")

    from datetime import datetime
    year = datetime.now().year
    url = (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id=DFII10&cosd={year}-01-01&fq=Daily"
    )

    try:
        data = _http_get(url, {"User-Agent": "StockAnalysis/1.0"})
        text = data.decode("utf-8").strip()
        lines = text.split("\n")

        # CSV format: observation_date,DFII10
        # Skip header, find last non-empty value (most recent)
        latest_rate = None
        for line in reversed(lines[1:]):
            parts = line.split(",")
            if len(parts) == 2 and parts[1].strip() and parts[1].strip() != ".":
                latest_rate = float(parts[1].strip()) / 100
                break

        if latest_rate is not None:
            print(f"  10Y TIPS: {latest_rate:.4f} ({latest_rate*100:.2f}%)")
            return latest_rate

        raise ValueError("No valid TIPS rate found in FRED CSV")

    except Exception as e:
        print(f"  WARNING: TIPS fetch failed: {e}. Using default: {TIPS_DEFAULT:.2%}")
        return TIPS_DEFAULT
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_fetch_tips_yield_parses_fred_csv test_mcp_server.py::test_fetch_tips_yield_fallback_on_failure -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add gather_data.py test_mcp_server.py
git commit -m "feat: add fetch_tips_yield() for TIPS-based real valuation"
```

---

## Task 2: Add `valuation_basis` to `build_config()`

**Files:**
- Modify: `gather_data.py:1626-1982` (build_config function)
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing tests for real valuation mode in build_config**

Add to `test_mcp_server.py`:

```python
def _make_test_financials():
    """Helper: minimal financials dict for build_config tests."""
    return {
        "years": [2022, 2023, 2024, 2025],
        "revenue": [80000, 85000, 90000, 95000],
        "operating_income": [20000, 21000, 22000, 23000],
        "net_income": [16000, 17000, 18000, 19000],
        "cost_of_revenue": [40000, 42000, 44000, 46000],
        "sbc": [2000, 2100, 2200, 2300],
        "shares": [1000, 1000, 1000, 1000],
        "current_assets": [30000, 31000, 32000, 33000],
        "cash": [10000, 11000, 12000, 13000],
        "st_investments": [5000, 5000, 5000, 5000],
        "current_liabilities": [20000, 21000, 22000, 23000],
        "st_debt": [5000, 5000, 5000, 5000],
        "st_leases": [1000, 1000, 1000, 1000],
        "net_ppe": [15000, 16000, 17000, 18000],
        "goodwill_intang": [10000, 10000, 10000, 10000],
        "buyback": [0, 0, 0, 0],
        "tax_provision": [4000, 4250, 4500, 4750],
        "pretax_income": [20000, 21000, 22000, 23000],
        "lt_debt_latest": 20000,
        "lt_leases_latest": 3000,
        "st_debt_latest": 5000,
        "interest_expense_latest": 1000,
        "finance_leases_latest": 0,
        "minority_interest_latest": 0,
        "equity_investments_latest": 0,
        "unfunded_pension_latest": 0,
        "entity_public_float": 0,
    }


def test_build_config_nominal_default():
    """build_config with default valuation_basis should not set real-valuation fields."""
    financials = _make_test_financials()
    cfg = gather_data.build_config(
        ticker="TEST", financials=financials, stock_price=100.0,
        market_cap=100000, shares_yahoo=1000, risk_free_rate=0.04,
        sector_betas=[("Tech", 1.0, 1.0)], credit_spread=0.01,
        credit_rating="A", peers=[], company_name="Test Corp",
    )
    assert cfg["risk_free_rate"] == 0.04
    assert cfg.get("valuation_basis", "nominal") == "nominal"
    assert "breakeven_inflation" not in cfg


def test_build_config_real_mode():
    """build_config with valuation_basis='real' should store TIPS fields and deflate growth."""
    financials = _make_test_financials()
    cfg = gather_data.build_config(
        ticker="TEST", financials=financials, stock_price=100.0,
        market_cap=100000, shares_yahoo=1000, risk_free_rate=0.019,
        sector_betas=[("Tech", 1.0, 1.0)], credit_spread=0.01,
        credit_rating="A", peers=[], company_name="Test Corp",
        valuation_basis="real",
        nominal_risk_free_rate=0.0427,
    )
    assert cfg["valuation_basis"] == "real"
    assert cfg["risk_free_rate"] == 0.019
    assert cfg["nominal_risk_free_rate"] == 0.0427
    assert cfg["breakeven_inflation"] == pytest.approx(0.0237, abs=0.001)
    # Terminal growth should default to 0.005 for real mode
    assert cfg["terminal_growth"] == 0.005
    # Nominal revenue growth should be stored
    assert "nominal_revenue_growth" in cfg
    # Real revenue growth should be lower than nominal by ~breakeven
    for real_g, nom_g in zip(cfg["revenue_growth"], cfg["nominal_revenue_growth"]):
        assert real_g < nom_g or nom_g <= 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_build_config_nominal_default test_mcp_server.py::test_build_config_real_mode -v`
Expected: FAIL — `build_config() got an unexpected keyword argument 'valuation_basis'`

- [ ] **Step 3: Implement valuation_basis in build_config**

Modify `gather_data.py`:

**A.** Add constant after line 50 (`MARGIN_OF_SAFETY_DEFAULT`):

```python
TERMINAL_GROWTH_REAL_DEFAULT = 0.005  # 0.5% real terminal growth
```

**B.** Update `build_config()` signature (line 1626) to add new parameters:

```python
def build_config(ticker, financials, stock_price, market_cap, shares_yahoo,
                 risk_free_rate, sector_betas, credit_spread, credit_rating,
                 peers, company_name, margin_of_safety=None, terminal_growth=None,
                 sector_margin=None, consensus=None,
                 valuation_basis="nominal", nominal_risk_free_rate=None):
```

**C.** After `term_growth = terminal_growth or TERMINAL_GROWTH_DEFAULT` (line 1671), add real-mode logic:

```python
    term_growth = terminal_growth or TERMINAL_GROWTH_DEFAULT
    consensus = consensus or {}

    # ── Real (TIPS) valuation mode ──
    breakeven_inflation = None
    nominal_revenue_growth = None

    if valuation_basis == "real":
        if nominal_risk_free_rate is None:
            nominal_risk_free_rate = risk_free_rate + 0.02  # rough estimate
        breakeven_inflation = nominal_risk_free_rate - risk_free_rate
        if not terminal_growth:
            term_growth = TERMINAL_GROWTH_REAL_DEFAULT
```

**D.** After the revenue_growth list is computed (after the exponential decay block, around line ~1735 where `revenue_growth` is finalized as a 10-element list), add deflation:

Find the line that sets `revenue_growth` to its final 10-element list. After it, add:

```python
    # Deflate revenue growth for real valuation
    if valuation_basis == "real" and breakeven_inflation is not None:
        nominal_revenue_growth = list(revenue_growth)
        revenue_growth = [max(g - breakeven_inflation, 0.0) for g in revenue_growth]
```

**E.** In the config dict (line ~1901), add the new fields after `"risk_free_rate": risk_free_rate,`:

After the existing `"risk_free_rate": risk_free_rate,` line, the cfg dict should include:

```python
        "risk_free_rate": risk_free_rate,
        "valuation_basis": valuation_basis,
```

And at the end of the cfg dict (before `"peers": peers,`), add:

```python
        "peers": peers,
    }

    # Add real-valuation reference fields
    if valuation_basis == "real":
        cfg["nominal_risk_free_rate"] = nominal_risk_free_rate
        cfg["breakeven_inflation"] = round(breakeven_inflation, 4)
        cfg["nominal_revenue_growth"] = nominal_revenue_growth
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_build_config_nominal_default test_mcp_server.py::test_build_config_real_mode -v`
Expected: PASS

- [ ] **Step 5: Run existing test suite to check for regressions**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -v`
Expected: All existing tests PASS (nominal mode is default, no behavior change)

- [ ] **Step 6: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add gather_data.py test_mcp_server.py
git commit -m "feat: add valuation_basis parameter to build_config for TIPS-based real valuation"
```

---

## Task 3: Thread `valuation_basis` through MCP server

**Files:**
- Modify: `mcp_server.py:123-282` (_build_dcf_config_impl and build_dcf_config tool)
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing test for real valuation mode via MCP**

Add to `test_mcp_server.py`:

```python
@patch("gather_data.fetch_treasury_yield", return_value=0.0427)
@patch("gather_data.fetch_tips_yield", return_value=0.019)
@patch("gather_data.fetch_stock_price", return_value=(150.0, 0, 0))
@patch("gather_data.fetch_sector_betas", return_value={"Tech": 1.0})
@patch("gather_data.fetch_sector_margins", return_value={"Tech": 0.25})
@patch("gather_data.find_peers", return_value=[])
@patch("gather_data.fetch_peer_data", return_value=[])
def test_build_dcf_config_impl_real_mode(
    mock_peers_data, mock_peers, mock_margins, mock_betas,
    mock_price, mock_tips, mock_treasury,
):
    """_build_dcf_config_impl with valuation_basis='real' should use TIPS yield."""
    from mcp_server import _build_dcf_config_impl

    financials = _make_test_financials()
    cfg = _build_dcf_config_impl(
        ticker="TEST",
        financial_data=financials,
        company_name="Test Corp",
        sic_code="7372",
        valuation_basis="real",
    )
    assert cfg["valuation_basis"] == "real"
    assert cfg["risk_free_rate"] == pytest.approx(0.019, abs=0.001)
    assert cfg["nominal_risk_free_rate"] == pytest.approx(0.0427, abs=0.001)
    assert "breakeven_inflation" in cfg
    mock_tips.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_build_dcf_config_impl_real_mode -v`
Expected: FAIL — `_build_dcf_config_impl() got an unexpected keyword argument 'valuation_basis'`

- [ ] **Step 3: Update _build_dcf_config_impl**

Modify `mcp_server.py`:

**A.** Update signature (line 123):

```python
def _build_dcf_config_impl(ticker, financial_data, company_name,
                            sic_code=None, sic_description="",
                            margin_of_safety=None, terminal_growth=None,
                            sector_margin=None, consensus=None,
                            valuation_basis="nominal"):
```

**B.** After `risk_free_rate = gather_data.fetch_treasury_yield()` (line 134), add TIPS logic:

```python
    risk_free_rate = gather_data.fetch_treasury_yield()

    nominal_risk_free_rate = None
    if valuation_basis == "real":
        nominal_risk_free_rate = risk_free_rate
        risk_free_rate = gather_data.fetch_tips_yield()
```

**C.** Add `valuation_basis` and `nominal_risk_free_rate` to the `build_config()` call (line 161):

```python
    cfg = gather_data.build_config(
        ticker=ticker,
        financials=financial_data,
        stock_price=stock_price,
        market_cap=market_cap,
        shares_yahoo=shares_latest,
        risk_free_rate=risk_free_rate,
        sector_betas=sector_betas,
        credit_spread=credit_spread,
        credit_rating=credit_rating,
        peers=peers,
        company_name=company_name,
        margin_of_safety=margin_of_safety,
        terminal_growth=terminal_growth,
        sector_margin=sector_margin,
        consensus=consensus,
        valuation_basis=valuation_basis,
        nominal_risk_free_rate=nominal_risk_free_rate,
    )
```

- [ ] **Step 4: Update the MCP tool signature**

Modify `build_dcf_config()` MCP tool (line ~234):

Add `valuation_basis` parameter:

```python
@mcp.tool()
def build_dcf_config(
    ticker: str,
    financial_data: dict,
    company_name: str,
    sic_code: str = "",
    sic_description: str = "",
    margin_of_safety: float = 0,
    terminal_growth: float = 0,
    sector_margin: float = 0,
    consensus: dict | None = None,
    valuation_basis: str = "nominal",
) -> str:
    """Build a complete DCF configuration from SEC financial data.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")
        financial_data: Parsed financials dict with keys: years, revenue,
            operating_income, net_income, cost_of_revenue, sbc, shares,
            current_assets, cash, st_investments, current_liabilities,
            st_debt, st_leases, net_ppe, goodwill_intang, buyback,
            tax_provision, pretax_income, lt_debt_latest, lt_leases_latest,
            st_debt_latest, interest_expense_latest, finance_leases_latest,
            minority_interest_latest, equity_investments_latest,
            unfunded_pension_latest
        company_name: Full company name (e.g. "Microsoft Corporation")
        sic_code: SIC code for sector beta + peer lookup (e.g. "7372")
        sic_description: SIC description for fuzzy sector matching
        margin_of_safety: Override default 20%% margin of safety (0 = use default)
        terminal_growth: Override default terminal growth (0 = use default: 2.5%% nominal, 0.5%% real)
        sector_margin: Override sector operating margin (0 = auto from Damodaran)
        consensus: Analyst estimates dict (optional)
        valuation_basis: "nominal" (default) or "real" (TIPS-based, inflation-adjusted)

    Returns:
        JSON string with the complete DCF config dict.
    """
    try:
        cfg = _build_dcf_config_impl(
            ticker=ticker,
            financial_data=financial_data,
            company_name=company_name,
            sic_code=sic_code or None,
            sic_description=sic_description,
            margin_of_safety=margin_of_safety or None,
            terminal_growth=terminal_growth or None,
            sector_margin=sector_margin or None,
            consensus=consensus,
            valuation_basis=valuation_basis,
        )
        return json.dumps(cfg, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -v`
Expected: All tests PASS including the new `test_build_dcf_config_impl_real_mode`

- [ ] **Step 6: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add mcp_server.py test_mcp_server.py
git commit -m "feat: thread valuation_basis through MCP server for real valuation mode"
```

---

## Task 4: Add `valuation_basis` to valuation output

**Files:**
- Modify: `mcp_server.py:182-203` (_calculate_valuation_impl)
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing test**

Add to `test_mcp_server.py`:

```python
def test_calculate_valuation_includes_valuation_basis():
    """_calculate_valuation_impl should include valuation_basis in output."""
    import json
    from mcp_server import _calculate_valuation_impl

    cfg = {
        "equity_market_value": 100000,
        "debt_market_value": 20000,
        "risk_free_rate": 0.019,
        "erp": 0.047,
        "credit_spread": 0.01,
        "tax_rate": 0.20,
        "sector_betas": [("Tech", 1.0, 1.0)],
        "base_revenue": 50000,
        "revenue_growth": [0.05] * 10,
        "op_margins": [0.25] * 10,
        "terminal_growth": 0.005,
        "terminal_margin": 0.20,
        "sales_to_capital": 0.5,
        "sbc_pct": 0.03,
        "shares_outstanding": 1000,
        "buyback_rate": 0,
        "margin_of_safety": 0.20,
        "stock_price": 100.0,
        "cash_bridge": 10000,
        "securities": 5000,
        "equity_investments": 0,
        "minority_interest": 0,
        "unfunded_pension": 0,
        "valuation_basis": "real",
        "nominal_risk_free_rate": 0.0427,
        "breakeven_inflation": 0.0237,
    }
    result = json.loads(_calculate_valuation_impl(cfg))
    assert result["valuation_basis"] == "real"
    assert result["nominal_risk_free_rate"] == 0.0427
    assert result["breakeven_inflation"] == 0.0237
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_calculate_valuation_includes_valuation_basis -v`
Expected: FAIL — `KeyError: 'valuation_basis'` (not in result dict)

- [ ] **Step 3: Update _calculate_valuation_impl**

Modify `mcp_server.py` `_calculate_valuation_impl()` (line ~182). Add after the `result` dict is built (after `result["closest_margin"]` block, around line 203):

```python
    # Include valuation basis metadata
    result["valuation_basis"] = cfg.get("valuation_basis", "nominal")
    if cfg.get("valuation_basis") == "real":
        result["nominal_risk_free_rate"] = cfg.get("nominal_risk_free_rate")
        result["breakeven_inflation"] = cfg.get("breakeven_inflation")

    return json.dumps(result)
```

- [ ] **Step 4: Run all tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add mcp_server.py test_mcp_server.py
git commit -m "feat: include valuation_basis metadata in valuation output"
```

---

## Task 5: Add `convert_to_real()` utility

**Files:**
- Modify: `gather_data.py` (after build_config, line ~1983)
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing test**

Add to `test_mcp_server.py`:

```python
def test_convert_to_real():
    """convert_to_real should transform a nominal config to real basis."""
    nominal_cfg = {
        "risk_free_rate": 0.0427,
        "revenue_growth": [0.08, 0.07, 0.06, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.025],
        "terminal_growth": 0.025,
        "valuation_basis": "nominal",
    }
    real_cfg = gather_data.convert_to_real(nominal_cfg, tips_yield=0.019, breakeven=0.0237)

    assert real_cfg["valuation_basis"] == "real"
    assert real_cfg["risk_free_rate"] == 0.019
    assert real_cfg["nominal_risk_free_rate"] == 0.0427
    assert real_cfg["breakeven_inflation"] == 0.0237
    assert real_cfg["terminal_growth"] == pytest.approx(0.0013, abs=0.001)
    assert real_cfg["nominal_revenue_growth"] == nominal_cfg["revenue_growth"]
    # Each real growth = nominal - breakeven, floored at 0
    for real_g, nom_g in zip(real_cfg["revenue_growth"], nominal_cfg["nominal_revenue_growth"]):
        expected = max(nom_g - 0.0237, 0.0)
        assert real_g == pytest.approx(expected, abs=0.0001)


def test_convert_to_real_floors_at_zero():
    """convert_to_real should floor growth rates at 0%."""
    nominal_cfg = {
        "risk_free_rate": 0.04,
        "revenue_growth": [0.01, 0.005],
        "terminal_growth": 0.025,
        "valuation_basis": "nominal",
    }
    real_cfg = gather_data.convert_to_real(nominal_cfg, tips_yield=0.019, breakeven=0.021)
    # 0.01 - 0.021 = -0.011 → floored to 0.0
    assert real_cfg["revenue_growth"][0] == 0.0
    # 0.005 - 0.021 = -0.016 → floored to 0.0
    assert real_cfg["revenue_growth"][1] == 0.0
    # terminal: 0.025 - 0.021 = 0.004
    assert real_cfg["terminal_growth"] == pytest.approx(0.004, abs=0.001)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_convert_to_real test_mcp_server.py::test_convert_to_real_floors_at_zero -v`
Expected: FAIL — `AttributeError: module 'gather_data' has no attribute 'convert_to_real'`

- [ ] **Step 3: Implement convert_to_real**

Add to `gather_data.py` after `build_config()` (after line ~1982):

```python
def convert_to_real(cfg, tips_yield, breakeven):
    """Convert a nominal DCF config to real (TIPS-based) valuation basis.

    Args:
        cfg: Existing nominal config dict.
        tips_yield: Current 10-year TIPS yield (e.g. 0.019).
        breakeven: Breakeven inflation rate (nominal Rf - TIPS yield).

    Returns:
        New config dict with real valuation basis applied.
    """
    real_cfg = dict(cfg)
    real_cfg["valuation_basis"] = "real"
    real_cfg["nominal_risk_free_rate"] = cfg["risk_free_rate"]
    real_cfg["risk_free_rate"] = tips_yield
    real_cfg["breakeven_inflation"] = breakeven
    real_cfg["nominal_revenue_growth"] = list(cfg["revenue_growth"])
    real_cfg["revenue_growth"] = [max(g - breakeven, 0.0) for g in cfg["revenue_growth"]]
    real_cfg["terminal_growth"] = max(cfg["terminal_growth"] - breakeven, 0.0)
    return real_cfg
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full regression test suite**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_tastytrade_api.py test_ibkr_api.py test_mcp_server.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add gather_data.py test_mcp_server.py
git commit -m "feat: add convert_to_real() utility for nominal-to-real config conversion"
```
