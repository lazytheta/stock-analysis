# LazyTheta DCF MCP Server — Design Spec

## Overview

A local MCP (Model Context Protocol) server that lets Claude Desktop fill out DCF (Discounted Cash Flow) configurations in the LazyTheta Streamlit app. Configs saved via this MCP are identical to manually-created ones — same structure, same fields, same types.

## Problem

Currently, filling out a DCF in LazyTheta requires manual data entry in the Streamlit UI. The user already has an MCP for SEC filings (financial data) and Tastytrade (portfolio). This MCP bridges the gap: Claude can take SEC data it already has and write a complete DCF config to LazyTheta's Supabase backend, where it appears in the Streamlit app.

## Architecture

- **Single file:** `mcp_server.py` in the stock-analysis project root
- **Transport:** stdio (standard MCP pattern for local tools)
- **Dependencies:** Imports directly from existing modules — `gather_data.py`, `dcf_calculator.py`, `config_store.py`
- **Auth:** Supabase service role key + hardcoded user_id (env vars). Bypasses RLS. Single-user only.
- **New dependency:** `mcp` Python package for the MCP protocol. All business logic reuses existing code.

## Integration Challenges

### 1. Streamlit dependency in `config_store.py`
`config_store.py` imports `streamlit` at the top level and uses `st.session_state` in `_get_user_id()`. This crashes outside Streamlit.

**Solution:** Add an optional `user_id` parameter to `save_config()`, `load_config()`, and `list_watchlist()`. When provided, skip the `_get_user_id()` call and use the passed value directly. The Streamlit app continues to work unchanged (parameter defaults to `None`, falling back to the existing `_get_user_id()` path).

### 2. `build_config()` requires many parameters beyond financial data
The actual signature is:
```python
def build_config(ticker, financials, stock_price, market_cap, shares_yahoo,
                 risk_free_rate, sector_betas, credit_spread, credit_rating,
                 peers, company_name, margin_of_safety=None, terminal_growth=None,
                 sector_margin=None, consensus=None)
```
The MCP tool `build_dcf_config` must gather the non-financial inputs itself:
- `stock_price`, `market_cap`, `shares_yahoo` → from `fetch_stock_price()` (returns `(price, 0, 0)` — use first element) and Yahoo chart API (market cap, shares from chart response)
- `risk_free_rate` → from `fetch_treasury_yield()`
- `credit_spread`, `credit_rating` → from `synthetic_credit_rating(operating_income, interest_expense)`
- `sector_betas` → from `fetch_sector_betas()` or passed by Claude
- `peers` → from `find_peers(sic_code, ticker, market_cap)` — `sic_code` must be provided by Claude (from SEC MCP)
- `company_name` → provided by Claude or extracted from SEC data

The `build_dcf_config` input schema reflects this: Claude provides `ticker`, `financial_data` (the parsed financials dict), `company_name`, and `sic_code`. The tool fetches the rest (stock price, treasury yield, credit rating, peers) internally.

### 3. User scoping with RLS bypassed
With service role key, `load_config()` and `list_watchlist()` see all users' data.

**Solution:** Add `.eq("user_id", user_id)` filter to queries in `config_store.py` when a `user_id` parameter is provided. The MCP server always passes the configured `LAZYTHETA_USER_ID`.

## Tools

### 1. `build_dcf_config`
- **Description:** Build a complete DCF configuration from financial data
- **Input:**
  - `ticker` (string, required)
  - `financial_data` (dict, required) — parsed financials from SEC: revenue, ebit, depreciation, capex, nwc, tax_rate, shares, debt, cash, interest_expense, operating_income per year
  - `company_name` (string, required)
  - `sic_code` (string, optional) — for peer lookup; skipped if not provided
  - `margin_of_safety` (float, optional) — override default
  - `terminal_growth` (float, optional) — override default
  - `sector_margin` (float, optional) — override default
  - `consensus` (dict, optional) — analyst estimates
- **Output:** Complete DCF config dict (same format as `configs/msft_config.py`)
- **Implementation:** Fetches stock price via `fetch_stock_price()`, treasury yield via `fetch_treasury_yield()`, derives credit rating via `synthetic_credit_rating()`, finds peers via `find_peers()` (if sic_code provided). Then calls `gather_data.build_config()` with all required parameters.
- **Config compatibility:** Uses the exact same `build_config()` function. No reimplementation.

### 2. `calculate_valuation`
- **Description:** Calculate intrinsic value from a DCF config
- **Input:** `config` (dict — complete DCF config)
- **Output:** Dict with `wacc`, `intrinsic_value`, `buy_price`, `enterprise_value`, `equity_value`, `tv_pct`, plus reverse DCF `closest` result (implied growth and margin). The full sensitivity matrix is omitted to keep output concise.
- **Implementation:** Calls `dcf_calculator.compute_wacc()`, `dcf_calculator.compute_intrinsic_value()`, and `dcf_calculator.compute_reverse_dcf()`.

### 3. `save_to_watchlist`
- **Description:** Save a DCF config to the LazyTheta watchlist in Supabase
- **Input:** `ticker` (string), `config` (dict — complete DCF config)
- **Output:** Confirmation message
- **Implementation:** Creates a Supabase client with service role key, calls `config_store.save_config(client, ticker, config, user_id=LAZYTHETA_USER_ID)`.

### 4. `get_watchlist`
- **Description:** List all tickers currently on the LazyTheta watchlist
- **Input:** None
- **Output:** List of watchlist entries (ticker, last updated, etc.)
- **Implementation:** Calls `config_store.list_watchlist(client, user_id=LAZYTHETA_USER_ID)`.

### 5. `get_config`
- **Description:** Read an existing DCF config from the watchlist
- **Input:** `ticker` (string)
- **Output:** Complete DCF config dict, or error if not found
- **Implementation:** Calls `config_store.load_config(client, ticker, user_id=LAZYTHETA_USER_ID)`.

## Authentication

- Environment variables: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `LAZYTHETA_USER_ID`
- Service role key bypasses Row Level Security
- User ID is injected into Supabase operations so configs appear in the correct user's watchlist
- Single-user design — only the configured user_id's watchlist is affected

## Config Compatibility Guarantee

The entire point of this MCP is that configs are indistinguishable from manual entry:

1. **Same builder:** `build_config()` from `gather_data.py` — not a reimplementation
2. **Same saver:** `save_config()` from `config_store.py` — handles tuple<>list conversion
3. **Same loader:** `load_config()` from `config_store.py` — restores tuples
4. **Same calculator:** `compute_wacc()` + `compute_intrinsic_value()` from `dcf_calculator.py`

If it works in the Streamlit app today, it works via MCP.

## Claude Desktop Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lazytheta": {
      "command": "python3",
      "args": ["/Users/administrator/Documents/GitHub/stock-analysis/mcp_server.py"],
      "env": {
        "SUPABASE_URL": "your-supabase-url",
        "SUPABASE_SERVICE_KEY": "your-service-role-key",
        "LAZYTHETA_USER_ID": "your-user-id"
      }
    }
  }
}
```

## Example Flow

1. User: "Analyze MSFT"
2. Claude gathers SEC financials via existing SEC MCP
3. Claude calls `build_dcf_config(ticker="MSFT", financial_data={...})` -> gets complete config
4. Claude calls `calculate_valuation(config={...})` -> sees intrinsic value, WACC, etc.
5. Claude reviews numbers, optionally discusses with user
6. Claude calls `save_to_watchlist(ticker="MSFT", config={...})` -> saved to Supabase
7. User opens LazyTheta Streamlit app -> MSFT appears on watchlist with full DCF

## Required Changes to Existing Modules

### `config_store.py`
- Add optional `user_id` parameter to `save_config()`, `load_config()`, `list_watchlist()`
- When `user_id` is provided: use it directly instead of calling `_get_user_id()`
- When `user_id` is provided: add `.eq("user_id", user_id)` to read queries
- Streamlit app continues working unchanged (parameter defaults to `None`)

### `gather_data.py`
- No changes needed. All functions are standalone and importable without Streamlit.
- Guard against `import streamlit` if present (verify at implementation time).

### `dcf_calculator.py`
- No changes needed. Pure Python, zero dependencies.

## Error Handling

- Missing env vars → clear error message on startup
- Supabase connection failure → tool returns error string (no crash)
- Invalid financial data → `build_config()` raises with descriptive message
- Ticker not found on watchlist → `get_config` returns "not found" message
- Yahoo Finance API failure (stale/zero price) → tool returns warning + partial result
- Network timeouts (peer lookup, treasury yield) → graceful degradation with defaults
- Each tool wrapped in try/except returning structured error messages (no server crash)

## File Structure

```
stock-analysis/
├── mcp_server.py          <- NEW: MCP server (single file)
├── gather_data.py         <- existing (imported)
├── dcf_calculator.py      <- existing (imported)
├── config_store.py        <- existing (imported)
└── configs/
    └── msft_config.py     <- reference config format
```

## Out of Scope

- Multi-user support
- Web/HTTP transport
- SEC data gathering (handled by separate MCP)
- Tastytrade integration (handled by separate MCP)
- Streamlit UI changes
- Config editing UI in Claude Desktop
