# Fundamentals Tab — Design

**Date:** 2026-02-25
**Status:** Approved

## Goal

Add a fourth tab ("Fundamentals") to the DCF editor page showing historical financial health metrics as trend charts. Helps assess whether a company has a strong flywheel, creates value above its cost of capital, and converts earnings to cash.

## Data Source

- **Primary:** yfinance (`income_stmt`, `balance_sheet`, `cashflow`) — 4-5 years
- **Fallback:** EDGAR XBRL via existing `parse_financials()` — up to 10 years
- New function `fetch_fundamentals(ticker)` combines both, deduplicates by year, and returns a unified dict

## Tab Placement

```
DCF | Reverse DCF | Peer Comparison | Fundamentals
```

## Metrics

### 1. Operating Leverage (Flywheel)
- Revenue Growth % vs Operating Income Growth % as dual line chart
- DOL = OI Growth / Rev Growth shown as annotation per year
- DOL > 1 = business scales; highlight years where DOL > 1

### 2. Margins Trend
- Gross Margin, Operating Margin, FCF Margin — three lines in one chart
- Shows P&L compression/expansion from top to bottom
- No EBITDA anywhere

### 3. ROIC (Return on Invested Capital)
- NOPAT = Operating Income × (1 - Tax Rate)
- Invested Capital = Total Equity + Total Debt - Cash
- WACC as horizontal reference line (from DCF config if available)
- ROIC above WACC = value creation (shaded green zone)

### 4. FCF Conversion
- FCF / Net Income as line chart
- Above 100% = strong, below 70% = warning
- Reference lines at 100% and 70%

### 5. Revenue per Share Growth
- Revenue / Shares Outstanding per year
- Show Rev/Share Growth vs Revenue Growth to expose dilution
- Dual line chart

### 6. Debt/FCF
- Total Debt / Free Cash Flow per year
- Color zones: green < 3x, orange 3-5x, red > 5x
- Reference lines at 3x and 5x thresholds

### 7. FCF Yield
- FCF per Share / Current Share Price
- Prominent current value at top
- Historical trend chart below
- Also add as column to watchlist overview page

## Layout

```
Row 1: [Operating Leverage]  [Margins Trend]
Row 2: [ROIC]                [FCF Conversion]
Row 3: [Rev/Share Growth]    [Debt/FCF]
Row 4: [FCF Yield — full width]
```

Using `st.columns([1, 1])` for side-by-side, consistent with existing layout patterns.

## Chart Style

Consistent with existing Plotly charts in the app:
- Font: `-apple-system, BlinkMacSystemFont, 'Inter', sans-serif`
- Colors: `#81b29a` (primary/good), `#e07a5f` (secondary/warning), `#3d405b` (accent), `#f2cc8f` (tertiary)
- Transparent background, grid color `#f0f0f2`
- Height: ~280px per chart
- Hover templates with clear labels
- No chart titles (use st.markdown headers above each chart)

## Error Handling

- Show chart only if >= 3 years of data available for that metric
- If a metric can't be computed (missing fields), show subtle "Insufficient data" message
- Never crash the entire tab for a single missing metric

## Watchlist Overview Change

Add FCF Yield column after P/E in the watchlist overview table. Computed from the most recent year's FCF / shares / live price.
