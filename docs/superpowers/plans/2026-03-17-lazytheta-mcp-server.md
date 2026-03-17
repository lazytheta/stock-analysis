# LazyTheta DCF MCP Server — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MCP server that lets Claude Desktop fill out DCF configs in LazyTheta's Supabase, producing configs identical to manual entry.

**Architecture:** Single `mcp_server.py` file using stdio transport. Imports existing `gather_data.py`, `dcf_calculator.py`, and `config_store.py` directly. Auth via Supabase service role key + hardcoded user_id env vars.

**Tech Stack:** Python 3, `mcp` package (MCP SDK), `supabase` client library, existing project modules.

**Spec:** `docs/superpowers/specs/2026-03-17-lazytheta-mcp-server-design.md`

---

## Chunk 1: config_store.py Refactor

### Task 1: Add `user_id` parameter to `save_config()`

**Files:**
- Modify: `config_store.py:50-66`
- Test: `test_mcp_server.py` (create)

- [ ] **Step 1: Write failing test for save_config with explicit user_id**

```python
# test_mcp_server.py
"""Tests for MCP server and config_store user_id parameter support."""
import pytest
from unittest.mock import MagicMock, patch


def test_save_config_with_explicit_user_id():
    """save_config() should use provided user_id instead of calling _get_user_id()."""
    from config_store import save_config

    mock_client = MagicMock()
    mock_client.table.return_value.upsert.return_value.execute.return_value = None

    cfg = {"company": "Test Corp", "stock_price": 100.0}
    save_config(mock_client, "TEST", cfg, user_id="explicit-uid-123")

    # Verify upsert was called with the explicit user_id
    call_args = mock_client.table.return_value.upsert.call_args[0][0]
    assert call_args["user_id"] == "explicit-uid-123"
    assert call_args["ticker"] == "TEST"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_mcp_server.py::test_save_config_with_explicit_user_id -v`
Expected: FAIL — `save_config()` does not accept `user_id` parameter

- [ ] **Step 3: Add user_id parameter to save_config**

In `config_store.py`, change `save_config`:

```python
def save_config(client, ticker, cfg, user_id=None):
    """Upsert a DCF config dict to Supabase."""
    from datetime import datetime, timezone

    ticker = ticker.upper()
    data = _prepare_for_json(cfg)
    if user_id is None:
        user_id = _get_user_id(client)

    row = {
        "user_id": user_id,
        "ticker": ticker,
        "company": cfg.get('company', ticker),
        "stock_price": cfg.get('stock_price', 0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "config": data,
    }
    client.table("watchlist_configs").upsert(row).execute()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_mcp_server.py::test_save_config_with_explicit_user_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config_store.py test_mcp_server.py
git commit -m "feat: add user_id parameter to save_config for MCP server support"
```

### Task 2: Add `user_id` parameter to `load_config()` and `list_watchlist()`

**Files:**
- Modify: `config_store.py:69-105`
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

```python
def test_load_config_with_explicit_user_id():
    """load_config() should filter by user_id when provided."""
    from config_store import load_config

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.data = {"config": {"company": "Test", "sector_betas": [], "debt_breakdown": []}}
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = mock_resp

    result = load_config(mock_client, "TEST", user_id="uid-456")

    # Should chain two .eq() calls: ticker AND user_id
    eq_calls = mock_client.table.return_value.select.return_value.eq.call_args_list
    assert any(call[0] == ("ticker", "TEST") for call in eq_calls)


def test_list_watchlist_with_explicit_user_id():
    """list_watchlist() should filter by user_id when provided."""
    from config_store import list_watchlist

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.data = [
        {"ticker": "MSFT", "company": "Microsoft", "stock_price": 400, "updated_at": "2026-01-01"}
    ]
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_resp

    result = list_watchlist(mock_client, user_id="uid-789")
    assert len(result) == 1
    assert result[0]["ticker"] == "MSFT"

    # Verify .eq("user_id", ...) was called
    mock_client.table.return_value.select.return_value.eq.assert_called_once_with("user_id", "uid-789")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_mcp_server.py::test_load_config_with_explicit_user_id test_mcp_server.py::test_list_watchlist_with_explicit_user_id -v`
Expected: FAIL

- [ ] **Step 3: Add user_id parameter to load_config and list_watchlist**

In `config_store.py`, update `load_config`:

```python
def load_config(client, ticker, user_id=None):
    """Load a DCF config dict. Returns dict or None."""
    ticker = ticker.upper()
    query = (
        client.table("watchlist_configs")
        .select("config")
        .eq("ticker", ticker)
    )
    if user_id is not None:
        query = query.eq("user_id", user_id)
    resp = query.single().execute()
    if resp and resp.data:
        return _restore_tuples(resp.data["config"])
    return None
```

Update `list_watchlist`:

```python
def list_watchlist(client, user_id=None):
    """Return list of dicts with ticker metadata.

    Each entry: {ticker, company, updated, stock_price}
    RLS automatically scopes to the current user when user_id is None.
    """
    query = (
        client.table("watchlist_configs")
        .select("ticker, company, stock_price, updated_at")
    )
    if user_id is not None:
        query = query.eq("user_id", user_id)
    resp = query.execute()
    if resp and resp.data:
        return [
            {
                'ticker': row['ticker'],
                'company': row.get('company', row['ticker']),
                'updated': row.get('updated_at', ''),
                'stock_price': row.get('stock_price', 0),
            }
            for row in resp.data
        ]
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_mcp_server.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Move `import streamlit` inside `_get_user_id()`**

In `config_store.py`, change the top-level import:

```python
# Remove: import streamlit as st  (line 12)
# Move it inside _get_user_id:
```

Update `_get_user_id`:

```python
def _get_user_id(client):
    """Get user_id, cached per-request via session_state."""
    import streamlit as st
    if "_user_id" not in st.session_state:
        st.session_state["_user_id"] = str(client.auth.get_user().user.id)
    return st.session_state["_user_id"]
```

This ensures `config_store.py` can be imported without streamlit installed (needed for MCP server). The Streamlit app still works — `_get_user_id()` is only called when `user_id=None`.

- [ ] **Step 6: Run existing test suite to verify no regressions**

Run: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v`
Expected: All 81 tests PASS

- [ ] **Step 7: Commit**

```bash
git add config_store.py test_mcp_server.py
git commit -m "feat: add user_id parameter to load_config and list_watchlist, move streamlit import"
```

---

## Chunk 2: MCP Server — Framework & Supabase Client

### Task 3: Create MCP server skeleton with env var validation

**Files:**
- Create: `mcp_server.py`
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing test for env var validation**

```python
def test_mcp_env_vars_missing(monkeypatch):
    """MCP server should raise clear error when env vars are missing."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("LAZYTHETA_USER_ID", raising=False)

    # Force re-import
    import importlib
    import mcp_server
    importlib.reload(mcp_server)

    with pytest.raises(ValueError, match="SUPABASE_URL"):
        mcp_server.get_supabase_client()


def test_mcp_env_vars_present(monkeypatch):
    """MCP server should create client when env vars are set."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")
    monkeypatch.setenv("LAZYTHETA_USER_ID", "test-uid")

    import importlib
    import mcp_server
    importlib.reload(mcp_server)

    assert mcp_server.USER_ID == "test-uid"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_mcp_server.py::test_mcp_env_vars_missing test_mcp_server.py::test_mcp_env_vars_present -v`
Expected: FAIL — `mcp_server` module does not exist

- [ ] **Step 3: Create mcp_server.py skeleton**

```python
"""
LazyTheta DCF MCP Server
========================
Lets Claude Desktop fill out DCF configs in LazyTheta's Supabase.
Runs locally via stdio transport.

Required env vars:
    SUPABASE_URL          — Supabase project URL
    SUPABASE_SERVICE_KEY  — Service role key (bypasses RLS)
    LAZYTHETA_USER_ID     — Your Supabase user ID
"""

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment & Supabase client
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
USER_ID = os.environ.get("LAZYTHETA_USER_ID", "")

_client = None


def get_supabase_client():
    """Create or return cached Supabase client."""
    global _client
    if _client is not None:
        return _client

    if not SUPABASE_URL:
        raise ValueError("SUPABASE_URL environment variable is required")
    if not SUPABASE_SERVICE_KEY:
        raise ValueError("SUPABASE_SERVICE_KEY environment variable is required")
    if not USER_ID:
        raise ValueError("LAZYTHETA_USER_ID environment variable is required")

    from supabase import create_client
    _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "LazyTheta DCF",
    description="Fill out DCF valuations in LazyTheta's Streamlit app",
)


# Tools will be added in subsequent tasks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Install mcp package**

Run: `pip install mcp`

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest test_mcp_server.py::test_mcp_env_vars_missing test_mcp_server.py::test_mcp_env_vars_present -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py test_mcp_server.py
git commit -m "feat: create MCP server skeleton with env var validation"
```

---

## Chunk 3: MCP Tools — build_dcf_config

### Task 4: Implement `build_dcf_config` tool

**Files:**
- Modify: `mcp_server.py`
- Test: `test_mcp_server.py`

**Context:** This is the most complex tool. It takes `ticker`, `financial_data` (the dict from `parse_financials()`), `company_name`, and optional `sic_code`. It fetches live market data (stock price, treasury yield, credit rating, peers) then calls `gather_data.build_config()`.

The `financial_data` dict must match the structure returned by `parse_financials()`:
- Lists: `years`, `revenue`, `operating_income`, `net_income`, `cost_of_revenue`, `sbc`, `shares`, `current_assets`, `cash`, `st_investments`, `current_liabilities`, `st_debt`, `st_leases`, `net_ppe`, `goodwill_intang`, `buyback`, `tax_provision`, `pretax_income`
- Scalars: `lt_debt_latest`, `lt_leases_latest`, `st_debt_latest`, `interest_expense_latest`, `finance_leases_latest`, `minority_interest_latest`, `equity_investments_latest`, `unfunded_pension_latest`

- [ ] **Step 1: Write failing test**

```python
@patch("mcp_server.gather_data")
def test_build_dcf_config_tool(mock_gd):
    """build_dcf_config should resolve sector betas, fetch peers, and call build_config."""
    import importlib
    import mcp_server
    importlib.reload(mcp_server)

    # Mock the gather_data functions
    mock_gd.fetch_stock_price.return_value = (150.0, 0, 0)
    mock_gd.fetch_treasury_yield.return_value = 0.04
    mock_gd.synthetic_credit_rating.return_value = ("A+", 0.01)
    mock_gd.SIC_TO_SECTOR = {7372: ("Software (System & Application)", 1.23)}
    mock_gd.fetch_sector_margins.return_value = {"Software (System & Application)": 0.25}
    mock_gd.find_peers.return_value = ["AAPL", "GOOGL"]
    mock_gd.fetch_peer_data.return_value = [
        {"ticker": "AAPL", "name": "Apple", "ev_revenue": 9.5, "ev_ebitda": 26.0,
         "pe": 33.5, "op_margin": 0.315, "rev_growth": 0.05, "roic": 0.55},
    ]
    mock_gd.build_config.return_value = {"company": "Test Corp", "ticker": "TEST"}

    financial_data = {
        "years": [2023, 2024, 2025],
        "revenue": [100_000, 110_000, 120_000],
        "operating_income": [30_000, 33_000, 36_000],
        "shares": [1000, 1000, 1000],
        "interest_expense_latest": 500,
    }

    result = mcp_server._build_dcf_config_impl(
        ticker="TEST",
        financial_data=financial_data,
        company_name="Test Corp",
        sic_code="7372",
    )

    assert result["ticker"] == "TEST"
    mock_gd.build_config.assert_called_once()

    # Verify sector_betas was passed as tuples, not raw dict
    call_kwargs = mock_gd.build_config.call_args
    sector_betas_arg = call_kwargs.kwargs.get("sector_betas") or call_kwargs[1].get("sector_betas")
    assert isinstance(sector_betas_arg, list)
    assert isinstance(sector_betas_arg[0], tuple)
    assert sector_betas_arg[0][0] == "Software (System & Application)"

    # Verify fetch_peer_data was called (not just find_peers)
    mock_gd.fetch_peer_data.assert_called_once_with(["AAPL", "GOOGL"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_mcp_server.py::test_build_dcf_config_tool -v`
Expected: FAIL — `_build_dcf_config_impl` does not exist

- [ ] **Step 3: Implement build_dcf_config**

Add to `mcp_server.py`:

```python
import gather_data
import dcf_calculator
import config_store


def _resolve_sector_betas(sic_code, sic_description=""):
    """Convert SIC code to sector_betas list of (name, beta, weight) tuples.

    Mirrors the logic in gather_data.py main() lines 2520-2565.
    """
    sic_int = int(sic_code) if sic_code else 0

    # Try SIC lookup table first
    if sic_int in gather_data.SIC_TO_SECTOR:
        sector_name, sector_beta = gather_data.SIC_TO_SECTOR[sic_int]
        return [(sector_name, sector_beta, 1.0)]

    # Fallback: fetch Damodaran betas and fuzzy match on SIC description
    dam_betas = gather_data.fetch_sector_betas()
    if dam_betas and sic_description:
        sic_words = set(sic_description.lower().split())
        best_match, best_score = None, 0
        for sector, beta in dam_betas.items():
            sector_words = set(sector.lower().split())
            overlap = len(sic_words & sector_words)
            if overlap > best_score:
                best_score = overlap
                best_match = (sector, beta)
        if best_match and best_score > 0:
            return [(best_match[0], best_match[1], 1.0)]

    # Ultimate fallback: market beta
    return [("Market", 1.0, 1.0)]


def _resolve_sector_margin(sector_betas):
    """Fetch sector median margin from Damodaran, matching on sector name.

    Mirrors the logic in gather_data.py main() lines 2589-2610.
    """
    sector_name = sector_betas[0][0] if sector_betas else ""
    if not sector_name:
        return None

    dam_margins = gather_data.fetch_sector_margins()
    if not dam_margins:
        return None

    # Exact match
    if sector_name in dam_margins:
        return dam_margins[sector_name]

    # Fuzzy match
    target_words = set(sector_name.lower().replace("/", " ").split())
    best_match, best_score = None, 0
    for sec_name, sec_margin in dam_margins.items():
        sec_words = set(sec_name.lower().replace("/", " ").split())
        overlap = len(target_words & sec_words)
        if overlap > best_score:
            best_score = overlap
            best_match = (sec_name, sec_margin)
    if best_match and best_score > 0:
        return best_match[1]
    return None


def _build_dcf_config_impl(ticker, financial_data, company_name,
                            sic_code=None, sic_description="",
                            margin_of_safety=None, terminal_growth=None,
                            sector_margin=None, consensus=None):
    """Core logic for build_dcf_config (testable without MCP decorator)."""
    ticker = ticker.upper()

    # Fetch live market data
    stock_price, _, _ = gather_data.fetch_stock_price(ticker)
    if stock_price <= 0:
        raise ValueError(f"Could not fetch stock price for {ticker}")

    risk_free_rate = gather_data.fetch_treasury_yield()

    # Derive market cap from EDGAR shares
    shares = financial_data.get("shares", [])
    shares_latest = shares[-1] if shares else 0
    market_cap = stock_price * shares_latest  # both in $M context

    # Credit rating from latest financials
    oi_latest = financial_data.get("operating_income", [0])[-1] or 0
    ie_latest = financial_data.get("interest_expense_latest", 0) or 0
    credit_rating, credit_spread = gather_data.synthetic_credit_rating(oi_latest, ie_latest)

    # Sector betas — convert SIC to (name, beta, weight) tuples
    sector_betas = _resolve_sector_betas(sic_code, sic_description)

    # Sector margin from Damodaran (if not overridden)
    if sector_margin is None:
        sector_margin = _resolve_sector_margin(sector_betas)

    # Peers — find + fetch full data
    peers = []
    if sic_code and market_cap > 0:
        try:
            peer_tickers = gather_data.find_peers(
                sic_code=int(sic_code),
                target_ticker=ticker,
                target_market_cap=market_cap,
            )
            peers = gather_data.fetch_peer_data(peer_tickers)
        except Exception as e:
            logger.warning("Peer lookup failed: %s", e)

    # Build the config using the exact same function as the Streamlit app
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
    )

    return cfg


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
    consensus: dict = None,
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
        sic_description: SIC description for fuzzy sector matching (e.g. "Prepackaged Software")
        margin_of_safety: Override default 20% margin of safety (0 = use default)
        terminal_growth: Override default 2.5% terminal growth (0 = use default)
        sector_margin: Override sector operating margin (0 = auto from Damodaran)
        consensus: Analyst estimates dict (optional)

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
        )
        return json.dumps(cfg, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_mcp_server.py::test_build_dcf_config_tool -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py test_mcp_server.py
git commit -m "feat: implement build_dcf_config MCP tool"
```

---

## Chunk 4: MCP Tools — calculate_valuation, save, get, list

### Task 5: Implement `calculate_valuation` tool

**Files:**
- Modify: `mcp_server.py`
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing test**

```python
def test_calculate_valuation_tool():
    """calculate_valuation should return WACC, intrinsic value, and reverse DCF."""
    import importlib
    import mcp_server
    importlib.reload(mcp_server)

    # Use a minimal but complete config
    cfg = {
        "equity_market_value": 2_000_000,
        "debt_market_value": 50_000,
        "tax_rate": 0.18,
        "sector_betas": [("Software", 1.05, 1.0)],
        "risk_free_rate": 0.04,
        "erp": 0.047,
        "credit_spread": 0.006,
        "base_revenue": 200_000,
        "revenue_growth": [0.10] * 10,
        "op_margins": [0.40] * 10,
        "terminal_growth": 0.03,
        "terminal_margin": 0.35,
        "sales_to_capital": 0.65,
        "sbc_pct": 0.04,
        "shares_outstanding": 7000,
        "buyback_rate": 0.01,
        "margin_of_safety": 0.20,
        "cash_bridge": 50_000,
        "stock_price": 300.0,
    }

    result = json.loads(mcp_server._calculate_valuation_impl(cfg))
    assert "wacc" in result
    assert "intrinsic_value" in result
    assert "buy_price" in result
    assert "implied_growth" in result
    assert "implied_margin" in result
    assert result["intrinsic_value"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest test_mcp_server.py::test_calculate_valuation_tool -v`
Expected: FAIL

- [ ] **Step 3: Implement calculate_valuation**

Add to `mcp_server.py`:

```python
def _calculate_valuation_impl(cfg):
    """Core logic for calculate_valuation (testable without MCP decorator)."""
    wacc = dcf_calculator.compute_wacc(cfg)
    valuation = dcf_calculator.compute_intrinsic_value(cfg, wacc)
    reverse = dcf_calculator.compute_reverse_dcf(cfg, wacc)

    result = {
        "wacc": round(wacc, 4),
        "intrinsic_value": round(valuation["intrinsic_value"], 2),
        "buy_price": round(valuation["buy_price"], 2),
        "enterprise_value": round(valuation["enterprise_value"], 2),
        "equity_value": round(valuation["equity_value"], 2),
        "tv_pct": round(valuation["tv_pct"], 4),
        "implied_growth": round(reverse["implied_growth"], 4),
        "implied_margin": round(reverse["implied_margin"], 4),
        "market_price": reverse["market_price"],
    }
    if reverse.get("closest"):
        result["closest_growth"] = round(reverse["closest"][0], 4)
        result["closest_margin"] = round(reverse["closest"][1], 4)

    return json.dumps(result)


@mcp.tool()
def calculate_valuation(config: dict) -> str:
    """Calculate intrinsic value, WACC, and reverse DCF from a config.

    Args:
        config: Complete DCF config dict (from build_dcf_config or get_config).

    Returns:
        JSON with wacc, intrinsic_value, buy_price, enterprise_value,
        equity_value, tv_pct, implied_growth, implied_margin.
    """
    try:
        return _calculate_valuation_impl(config)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest test_mcp_server.py::test_calculate_valuation_tool -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py test_mcp_server.py
git commit -m "feat: implement calculate_valuation MCP tool"
```

### Task 6: Implement `save_to_watchlist`, `get_config`, `get_watchlist` tools

**Files:**
- Modify: `mcp_server.py`
- Test: `test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

```python
@patch("mcp_server.get_supabase_client")
def test_save_to_watchlist_tool(mock_get_client):
    """save_to_watchlist should call config_store.save_config with user_id."""
    import importlib
    import mcp_server
    importlib.reload(mcp_server)
    mcp_server.USER_ID = "test-uid"

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.table.return_value.upsert.return_value.execute.return_value = None

    result = mcp_server._save_to_watchlist_impl("TEST", {"company": "Test", "stock_price": 100})
    assert "saved" in result.lower() or "success" in result.lower()


@patch("mcp_server.get_supabase_client")
def test_get_config_tool(mock_get_client):
    """get_config should return config from Supabase."""
    import importlib
    import mcp_server
    importlib.reload(mcp_server)
    mcp_server.USER_ID = "test-uid"

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.data = {"config": {"company": "Test", "ticker": "TEST", "sector_betas": [], "debt_breakdown": []}}
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = mock_resp

    result = json.loads(mcp_server._get_config_impl("TEST"))
    assert result["ticker"] == "TEST"


@patch("mcp_server.get_supabase_client")
def test_get_watchlist_tool(mock_get_client):
    """get_watchlist should return list of tickers."""
    import importlib
    import mcp_server
    importlib.reload(mcp_server)
    mcp_server.USER_ID = "test-uid"

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_resp = MagicMock()
    mock_resp.data = [
        {"ticker": "MSFT", "company": "Microsoft", "stock_price": 400, "updated_at": "2026-01-01"},
        {"ticker": "AAPL", "company": "Apple", "stock_price": 230, "updated_at": "2026-01-01"},
    ]
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_resp

    result = json.loads(mcp_server._get_watchlist_impl())
    assert len(result) == 2
    assert result[0]["ticker"] == "MSFT"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest test_mcp_server.py::test_save_to_watchlist_tool test_mcp_server.py::test_get_config_tool test_mcp_server.py::test_get_watchlist_tool -v`
Expected: FAIL

- [ ] **Step 3: Implement the three tools**

Add to `mcp_server.py`:

```python
def _save_to_watchlist_impl(ticker, cfg):
    """Core logic for save_to_watchlist."""
    client = get_supabase_client()
    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return f"Saved {ticker.upper()} to watchlist."


def _get_config_impl(ticker):
    """Core logic for get_config."""
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})
    return json.dumps(cfg, default=str)


def _get_watchlist_impl():
    """Core logic for get_watchlist."""
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=USER_ID)
    return json.dumps(entries, default=str)


@mcp.tool()
def save_to_watchlist(ticker: str, config: dict) -> str:
    """Save a DCF config to the LazyTheta watchlist in Supabase.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")
        config: Complete DCF config dict (from build_dcf_config).

    Returns:
        Confirmation message.
    """
    try:
        return _save_to_watchlist_impl(ticker, config)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_config(ticker: str) -> str:
    """Read an existing DCF config from the LazyTheta watchlist.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")

    Returns:
        JSON with the complete DCF config, or error if not found.
    """
    try:
        return _get_config_impl(ticker)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_watchlist() -> str:
    """List all tickers on the LazyTheta watchlist.

    Returns:
        JSON array of {ticker, company, updated, stock_price} entries.
    """
    try:
        return _get_watchlist_impl()
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest test_mcp_server.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite for regressions**

Run: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v`
Expected: All 81 tests PASS

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py test_mcp_server.py
git commit -m "feat: implement save/get/list watchlist MCP tools"
```

---

## Chunk 5: Integration Test & Claude Desktop Config

### Task 7: Smoke test the MCP server locally

**Files:**
- None (manual verification)

- [ ] **Step 1: Verify MCP server starts without errors**

Run: `echo '{}' | SUPABASE_URL=test SUPABASE_SERVICE_KEY=test LAZYTHETA_USER_ID=test python3 mcp_server.py`
Expected: Server starts and waits for MCP messages on stdin (no crash, no import errors)

- [ ] **Step 2: Run the MCP inspector for interactive testing**

Run: `SUPABASE_URL=$SUPABASE_URL SUPABASE_SERVICE_KEY=$SUPABASE_SERVICE_KEY LAZYTHETA_USER_ID=$LAZYTHETA_USER_ID npx @modelcontextprotocol/inspector python3 mcp_server.py`
Expected: MCP inspector shows 5 tools: `build_dcf_config`, `calculate_valuation`, `save_to_watchlist`, `get_config`, `get_watchlist`

- [ ] **Step 3: Test get_watchlist via inspector**

In inspector, call `get_watchlist` with no args.
Expected: Returns JSON array of current watchlist entries.

- [ ] **Step 4: Commit any fixes from smoke testing**

### Task 8: Configure Claude Desktop

**Files:**
- Modify: `~/Library/Application Support/Claude/claude_desktop_config.json`

- [ ] **Step 1: Add lazytheta MCP server entry**

Add to the `mcpServers` section:

```json
{
  "lazytheta": {
    "command": "python3",
    "args": ["/Users/administrator/Documents/GitHub/stock-analysis/mcp_server.py"],
    "env": {
      "SUPABASE_URL": "<your-supabase-url>",
      "SUPABASE_SERVICE_KEY": "<your-service-role-key>",
      "LAZYTHETA_USER_ID": "<your-user-id>"
    }
  }
}
```

- [ ] **Step 2: Restart Claude Desktop**

Quit and reopen Claude Desktop. Verify "LazyTheta DCF" appears in the MCP tools list (hammer icon).

- [ ] **Step 3: End-to-end test**

In Claude Desktop, say: "Show me my LazyTheta watchlist"
Expected: Claude calls `get_watchlist` and shows your current tickers.

- [ ] **Step 4: Full pipeline test**

In Claude Desktop, say: "Analyze MSFT and add it to my LazyTheta watchlist"
Expected: Claude uses SEC MCP → `build_dcf_config` → `calculate_valuation` → `save_to_watchlist`. Open LazyTheta Streamlit app and verify MSFT appears with identical config structure.

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "feat: complete LazyTheta DCF MCP server"
```
