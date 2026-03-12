# Monthly Detail Modal — Design Spec

## Overview

Add a monthly detail modal to the Results page. When a user expands a year in the Returns section and clicks on a month card, a `st.dialog` opens showing detailed performance metrics for that month.

## Navigation

- Month cards in the Returns section remain **vertically stacked** (current layout)
- Each month card becomes **clickable** via Streamlit button
- Click opens a **`@st.dialog`** modal with the month's detail view
- Use existing app styling (theme dict `T`, CSS classes like `pf-green`, `pf-red`, `portfolio-card`, etc.)

## Modal Content

### 1. Hero Cards (3 columns)

| Card | Data Source | Calculation |
|------|-----------|-------------|
| **Premium Collected** | `cost_basis[ticker]["trades"]` | Sum `net_value` for trades with label CSP or CC (Sell to Open) in the selected month. Show count of those trades. |
| **Net P/L** | `net_liq_history` | Dollar: `end_liq - start_liq - deposits`. Percent: Simple Dietz `(end - start - dep) / (start + 0.5*dep) * 100` |
| **Benchmark Comparison** | Yahoo Finance (`_fetch_yearly_returns` already fetches monthly intervals) | Compute month-over-month % change for S&P 500 and Nasdaq. Compare with portfolio monthly return. |

### 2. Leaders — By Premium (table)

Filter all trades in the month with label CSP or CC. Group by ticker.

| Column | Calculation |
|--------|-------------|
| Ticker | Underlying symbol |
| Trades | Count of CSP/CC trades for that ticker |
| Contracts | Sum of `abs(quantity)` for those trades |
| Avg DTE | Parse expiry from OCC option symbol, subtract trade date, average |
| Est. ROC | `(premium / collateral) * (365 / avg_dte) * 100`. CSP collateral = strike × 100. CC collateral = stock price at trade time × 100 |
| Premiums | Sum of `net_value` for those trades |

Sort descending by Premiums. Show top 5.

### 3. Leaders & Laggards — By P/L (two side-by-side tables)

For each ticker, compute total P/L in the month = sum of all `net_value` for all trades in that month.

Break down into:
- **CC**: sum of net_value where label is CC or BTC CC
- **PUT**: sum of net_value where label is CSP or BTC CSP
- **Net P/L**: total of all trades for that ticker in the month

**Leaders**: top 5 tickers by Net P/L (descending)
**Laggards**: bottom 5 tickers by Net P/L (ascending), only those with negative P/L

### 4. Monthly return formatting

Use `_fmt_k()` helper for amounts >= $1000 (e.g., `$42.2K`). Color: green for positive, red for negative. Match existing theme colors.

## Data Flow

1. `cost_basis` dict is already in `st.session_state["portfolio_data"]` — contains all trades with dates
2. `net_liq_history` is already cached in session state — has daily close values
3. Benchmark monthly data: add a new function `fetch_benchmark_monthly_returns()` that reuses the Yahoo Finance chart API (already using `interval=1mo`) but returns monthly returns instead of yearly
4. Filter trades by `year` and `month` from trade `date` field

## Implementation Notes

- Use `@st.dialog("Month Name Year")` decorator for the modal
- The dialog function receives `year`, `month`, `cost_basis`, `nl_all`, `transfers`, `T` (theme)
- Parse OCC option symbols for expiry: format is `TICKER  YYMMDDCSSSSSSSS` (ticker padded to 6 chars, C=call type, S=strike)
- Reuse existing CSS classes and theme dict for consistent styling
- Keep all new code in `streamlit_app.py` — no new files needed
