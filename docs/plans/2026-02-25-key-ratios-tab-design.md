# Key Ratios Tab — Design

**Date:** 2026-02-25
**Status:** Approved

## Goal

Add a fifth tab ("Key Ratios") to the DCF editor page showing comprehensive historical financial ratios as HTML tables (Morningstar-style). Pure numbers, no charts.

## Data Source

- **Primary:** yfinance (`income_stmt`, `balance_sheet`, `cashflow`) — 4-5 years
- **Fallback:** EDGAR XBRL via `fetch_company_facts()` — up to 10 years
- Extend existing `fetch_fundamentals()` with additional fields
- **New:** Historical year-end stock prices via Yahoo Finance chart API for valuation metrics

### New fields for fetch_fundamentals()

Add to yfinance extraction:
- `total_assets` — from balance_sheet "Total Assets"
- `current_liabilities` — from balance_sheet "Current Liabilities"
- `goodwill` — from balance_sheet "Goodwill"
- `intangibles` — from balance_sheet "Intangible Assets" (excl goodwill)
- `ppe` — from balance_sheet "Net PPE"
- `da` — from cashflow "Depreciation And Amortization"
- `gross_profit` — from income_stmt "Gross Profit"
- `eps` — from income_stmt "Diluted EPS"
- `dividends_per_share` — from EDGAR `CommonStockDividendsPerShareDeclared`

EDGAR XBRL fallback tags:
- `total_assets`: `Assets`
- `current_liabilities`: `LiabilitiesCurrent`
- `goodwill`: `Goodwill`
- `intangibles`: `IntangibleAssetsNetExcludingGoodwill`
- `ppe`: `PropertyPlantAndEquipmentNet`
- `da`: `DepreciationDepletionAndAmortization`
- `gross_profit`: `GrossProfit`
- `eps`: `EarningsPerShareDiluted` (unit_key="USD/shares")
- `dividends_per_share`: `CommonStockDividendsPerShareDeclared` (unit_key="USD/shares")

### Historical year-end prices

New function `fetch_historical_prices(ticker, years)`:
- Use Yahoo Finance chart API: `query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=12y&interval=1mo`
- Extract closing price for December (or last available month) of each year
- Returns dict: `{2016: 123.45, 2017: 145.67, ...}`

## Tab Placement

```
DCF | Reverse DCF | Peer Comparison | Fundamentals | Key Ratios
```

## Sections

### 1. Returns

| Metric | Formula |
|--------|---------|
| Return on Assets | Net Income / Total Assets |
| Return on Equity | Net Income / Total Equity |
| Return on Invested Capital | NOPAT / (Equity + Debt - Cash) |
| Return on Capital Employed | OI / (Total Assets - Current Liabilities) |
| Return on Tangible Capital | OI / (Total Assets - CurLiab - Goodwill - Intangibles) |

Format: percentages with 1 decimal. Green when improving YoY, no special coloring otherwise.

### 2. Margins as % of Revenue

| Metric | Formula |
|--------|---------|
| Gross Margin | Gross Profit / Revenue |
| EBITDA Margin | (OI + D&A) / Revenue |
| Operating Margin | OI / Revenue |
| Pretax Margin | Pretax Income / Revenue |
| Net Margin | Net Income / Revenue |
| FCF Margin | FCF / Revenue |

Format: percentages with 1 decimal.

### 3. Capital Structure

| Metric | Formula |
|--------|---------|
| Assets to Equity | Total Assets / Total Equity |
| Equity to Assets | Total Equity / Total Assets |
| Debt to Equity | Total Debt / Total Equity |
| Debt to Assets | Total Debt / Total Assets |

Format: ratios with 1 decimal.

### 4. Year-Over-Year Growth

Rows:
- Revenue, Gross Profit, EBITDA, Operating Income, Pretax Income, Net Income, Diluted EPS
- (blank separator row)
- Diluted Shares
- (blank separator row)
- PP&E, Total Assets, Equity
- (blank separator row)
- Cash from Operations, Capital Expenditures, Free Cash Flow

Format: percentages with 1 decimal. Green for positive, red for negative.

### 5. Valuation Metrics

| Metric | Formula |
|--------|---------|
| Market Capitalization | Year-end Price × Shares |
| Price-to-Earnings | Year-end Price / EPS |
| Price-to-Book | Year-end Price / (Equity / Shares) |
| Price-to-Sales | Year-end Price / (Revenue / Shares) |

Format: Market Cap as formatted number, ratios with 2 decimals.

### 6. Dividends

| Metric | Formula |
|--------|---------|
| Dividends per Share | Direct from EDGAR |
| Payout Ratio | DPS / EPS |

Format: DPS as dollar amount with 2 decimals, Payout Ratio as percentage.
Show "—" for companies that don't pay dividends.

### 7. Per-Share Items

| Metric | Formula |
|--------|---------|
| Revenue | Revenue / Shares |
| EBITDA | (OI + D&A) / Shares |
| Operating Income | OI / Shares |
| Diluted EPS | Direct from EDGAR or Net Income / Shares |
| Free Cash Flow | FCF / Shares |
| Book Value | Equity / Shares |
| Tangible Book Value | (Equity - Goodwill - Intangibles) / Shares |

Format: dollar amounts with 2 decimals.

### 8. Supplementary Items

| Metric | Source |
|--------|-------|
| Free Cash Flow | Absolute value in millions |
| Book Value | Total Equity in millions |
| Tangible Book Value | (Equity - Goodwill - Intangibles) in millions |

Format: formatted numbers with commas, no decimals.

## Table Style

Consistent with existing Operating Leverage table:
- Cell: `text-align:right;padding:5px 10px;font-size:0.85rem`
- Header: `text-align:right;padding:5px 10px;font-size:0.85rem;color:#86868b`
- Label: `text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap`
- Section headers as bold `st.markdown` above each table
- Avg column with `border-left:2px solid #d2d2d7`
- Row separators: `border-top:1px solid #f0f0f2`
- Blank separator rows between logical groups (in Growth section)

## Error Handling

- Show section only if >= 2 years of data available for its metrics
- If a metric can't be computed (missing fields), show "—"
- Never crash the entire tab for a single missing metric
- Valuation section requires historical prices; show "Insufficient data" if unavailable
