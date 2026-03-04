# Reverse DCF Tab — Design

## Overview
Add a "Reverse DCF" tab to the DCF editor in the watchlist overview page. Shows what growth and margin assumptions the market is pricing into the current stock price.

## Layout

### Tab placement
Third tab in editor: `st.tabs(["DCF", "Reverse DCF", "Peer Comparison"])`

### Section 1: Implied Metrics (top)
Two metric cards side-by-side via `st.columns(2)`:

- **Implied Revenue CAGR**: Binary search for the uniform growth rate that produces the current market price, holding margin at base case. Shows: implied value, base case comparison, one-line conclusion.
- **Implied Operating Margin**: Binary search for the uniform margin that produces the current market price, holding growth at base case. Shows: implied value, base case comparison, one-line conclusion.

### Section 2: Sensitivity Matrix
Styled `st.dataframe` (Pandas Styler):
- Rows: Revenue CAGR levels
- Columns: Operating Margin levels
- Cells: Implied share price at each combo
- Colors: Green = above market (undervalued), Red = below market (overvalued), Orange = closest to market price
- Ranges auto-centered on implied values

### Section 3: Adjustable Ranges (expander)
`st.expander("Adjust ranges")`:
- Growth: min, max, step (defaults: implied center ±8%, step 2%)
- Margin: min, max, step (defaults: implied center ±8%, step 2%)

## Calculation
- Reuse `compute_intrinsic_value()` from `dcf_calculator.py`
- Binary search: modify config with uniform growth/margin, compute price, bisect until match
- Matrix: iterate all growth/margin combos, compute price for each
- No Excel generation — pure Python computation to Streamlit output

## Data Dependencies
From config: `stock_price`, `base_revenue`, `revenue_growth`, `op_margins`, `terminal_growth`, `terminal_margin`, `sales_to_capital`, `sbc_pct`, `buyback_rate`, `shares_outstanding`, `cash_bridge`, `securities`, `debt_market_value`, WACC inputs.
