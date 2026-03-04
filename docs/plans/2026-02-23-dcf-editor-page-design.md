# DCF Editor Page Design

## Problem

The watchlist currently has a small inline editor (growth, margins, a few assumptions). Users want full control over all DCF parameters that appear in the Excel, editable directly in the app. The Excel should be generated from the app, not the other way around.

## Solution

Add a dedicated **DCF Editor page** accessible from the watchlist via query parameters. The watchlist overview stays lightweight; clicking a ticker navigates to a full editor.

## Navigation

- Watchlist overview table: click ticker row -> sets `st.query_params["edit"] = "AAPL"`
- Editor page detects `edit` param and renders the full editor
- Back button clears the param, returning to the overview
- Both views live within the existing "Watchlist" page (no new sidebar entry)

## Editor Page Sections

### 1. Header
- Back button ("< Watchlist")
- Ticker + company name (large)
- Stat pills: live price, intrinsic value, buy price, upside %, WACC
- Action buttons: Save (primary), Download Excel, Remove

### 2. Valuation Summary (read-only, live)
- Computed from current config via `compute_intrinsic_value()`
- Shows: intrinsic value, buy price, enterprise value, equity value, TV as % of EV
- Updates on Save

### 3. WACC Inputs (editable)
- `risk_free_rate`, `erp`, `credit_spread`, `tax_rate`
- `equity_market_value`, `debt_market_value`
- `sector_betas` table: name, unlevered beta, revenue weight
- Computed WACC shown as output

### 4. 10-Year Projections (editable)
- Table: year | revenue growth % | operating margin %
- One `number_input` per cell, 10 rows
- Base year + base revenue + base operating income shown as context

### 5. Terminal & Assumptions (editable)
- `terminal_growth`, `terminal_margin`
- `sales_to_capital`, `sbc_pct`, `buyback_rate`, `margin_of_safety`

### 6. Shares & Equity Bridge (editable)
- `shares_outstanding`, `cash_bridge`, `securities`

### 7. Scenario Adjustments (editable)
- `bull_growth_adj`, `bull_margin_adj`
- `bear_growth_adj`, `bear_margin_adj`

### 8. Peer Comparison (read-only)
- Table from `cfg['peers']` with columns: ticker, name, EV/Revenue, EV/EBITDA, P/E, op margin, rev growth, ROIC

### 9. Historical Data (read-only)
- 6-year table: revenue, operating income, net income, cost of revenue, SBC, shares
- 6-year balance sheet: current assets, cash, ST investments, operating cash, current liabilities, ST debt, ST leases, net PPE, goodwill/intangibles

## Data Flow

```
Watchlist overview
    -> click ticker
    -> load_config(ticker) from JSON
    -> render editor with all fields
    -> user edits values
    -> clicks Save
    -> save_config(ticker, updated_cfg)
    -> recompute intrinsic value
    -> rerun

Download Excel:
    -> _build_excel_bytes(cfg)
    -> st.download_button
```

## What's NOT included
- Calculations tab content (per user request)
- Excel upload/import flow
- Editable historical data or peer comparison
