---
name: dcf-excel-template
description: Generate a professional multi-tab DCF valuation Excel model for any public company. Fully automated — Claude gathers all data, calculates WACC, derives betas, sets growth assumptions, and builds the complete model with peer comparison, sensitivity analysis, and dynamic scenario modeling. User only needs to say "build a DCF for [company]". Uses Sales-to-Capital reinvestment methodology, mid-year discount convention, weighted sector betas from Damodaran, SBC deduction, buyback-adjusted shares, and margin of safety. Trigger when user asks to build a DCF, value a stock, or create a valuation model.
---

# DCF Excel Template

Generate a complete, multi-tab DCF valuation model in Excel for any public company.

## When to Use

- User asks to "build a DCF" or "value [company]"
- User wants a stock valuation spreadsheet
- User asks for an intrinsic value calculation
- User references this template or a previous DCF model we built

## Fully Automated Workflow

The user only needs to provide a company name (and optionally upload annual reports). Claude handles everything else:

### Step 1: Data Gathering (web search + annual reports)

1. **Damodaran sector betas** — fetch from https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html
   - Identify which sector(s) the company operates in
   - Get unlevered beta for each sector
   - Determine revenue weight per sector (e.g. PepsiCo: 55% food, 45% beverages)

2. **Company financials** (from 10-K/annual reports, ~6 years historical):
   - Balance sheet: Current Assets, Cash, ST Investments, Current Liabilities, ST Debt, ST Leases, Net PP&E, Goodwill & Intangibles
   - Income: Revenue, Cost of Revenue, Operating Income, Net Income
   - Cash flow: Stock-Based Compensation
   - Capital structure: Total debt breakdown, shares outstanding, credit rating
   - Share count: Diluted shares outstanding per year (for buyback/dilution tracking)

3. **Market data** (web search):
   - Current share price and market cap
   - US 10-Year Treasury yield (risk-free rate)
   - Credit rating → credit spread mapping

4. **SBC history**: Stock-Based Compensation from cash flow statements (3+ years to compute % of revenue)

5. **Peer data** (for Peer Comparison tab):
   - 6-8 comparable companies (same sector, similar size or direct competitors)
   - For each: EV/Revenue, EV/EBITDA, P/E, Operating Margin, Revenue Growth, ROIC
   - Source: web search for latest consensus estimates and multiples

### Step 2: Assumptions (Claude derives all of these)

Claude sets all assumptions based on data and business logic. Nothing is left blank:

- **WACC**: Fully calculated (risk-free rate, weighted sector betas, credit spread from rating, market D/E ratio)
- **Sales-to-Capital ratio**: Historical average from Invested Capital data
- **Revenue growth**: Based on historical growth, analyst consensus (web search), and business maturity
  - Mature/staples: low growth (2-3.5%), gradual taper
  - Growth companies: higher initial growth declining toward economic growth rate
  - Always smooth progression, never random zigzags
- **Operating margins**: Based on historical margins with realistic trend
  - Expanding if company shows operational leverage
  - Stable or mild compression for mature businesses
  - Always smooth, not erratic
- **SBC %**: Average of last 3 years as percentage of revenue
- **Buyback rate**: Derived from historical share count trend (see Buyback Modeling section)
- **Terminal growth**: Typically 2-2.5% (≤ long-term GDP growth)
- **Terminal margin**: Realistic steady-state margin for the industry
- **Margin of Safety**: Default 20% (standard value investing)

### Step 3: Model Generation

```python
exec(open('/mnt/skills/user/dcf-excel-template/dcf_template.py').read())
config = { ... }  # All gathered data and assumptions
build_dcf_model(config, output_path)
```

### Step 4: Recalculate & Verify

```bash
python /mnt/skills/public/xlsx/scripts/recalc.py <output_file>
```

Verify: 0 formula errors, spot-check WACC, FCFF, TV discounting, final price.

### Step 5: All Tabs Generated Automatically

The template now generates ALL tabs in one call when the config is complete:
1. **Summary** — auto-generated when `hist_operating_income` is provided
2. **Peer Comparison** — auto-generated when `peers` list is provided  
3. **Valuation** (main DCF) — always generated, includes Dynamic Bull/Bear/Base scenario blocks
4. **Calculations** — auto-generated when `hist_operating_income` is provided
5. **Sensitivity Analysis** — always generated (three 2D matrices)

No manual post-build steps needed. All tabs are formula-linked.

### Step 6: Present Results

Present file with summary table showing:
- Key inputs (WACC, growth range, margin range)
- Enterprise Value & TV as % of EV
- Share Price vs current market price
- Buy Price (with margin of safety)
- Scenario range (Bull/Base/Bear)
- Key Summary metrics: FCF Yield, SBC-adj margin, Rule of 40
- Brief commentary on valuation

### Step 7: Save Config for Reuse (Token Optimization)

After the first build, save the complete config dictionary as a standalone Python file
in `/mnt/skills/user/dcf-excel-template/configs/<ticker>_config.py`.

**On every DCF request, FIRST check if a config already exists:**
```python
import os
ticker = '<ticker>'.lower()
# Check both locations (configs subdir and skill root)
for path in [
    f'/mnt/skills/user/dcf-excel-template/configs/{ticker}_config.py',
    f'/mnt/skills/user/dcf-excel-template/{ticker}_config.py',
]:
    if os.path.exists(path):
        exec(open(path).read())  # loads 'cfg' dict
        break
```

This way:
- Future rebuilds only require loading the config file (no re-reading 10-K documents)
- Assumption changes are a single edit in the config
- The config file serves as documentation of all inputs
- Works across all projects and conversations

When the user says "rebuild the DCF" or "update the model" and a config file exists,
use it directly instead of re-extracting data from documents. Only re-extract if the
user explicitly asks to refresh the data or a new fiscal year is available.

## Model Structure (5 Tabs)

### Tab 1: Summary
Valuation ratios, historical margins/returns/growth, qualitative fields.

### Tab 2: Peer Comparison
Comparative analysis vs 6-8 peers on valuation multiples and quality metrics:

**Metrics per company:**
- EV/Revenue, EV/EBITDA, P/E Ratio
- Operating Margin, Net Margin
- Revenue Growth (YoY)
- ROIC
- Rule of 40 Score

**Analysis sections:**
- Peer ranking table with conditional formatting (green=cheap/high quality, red=expensive/low quality)
- Subject company position vs peer average and median
- Multiple-based valuation scenarios:
  - "At peer average EV/EBITDA → implied share price $X"
  - "At [closest peer] multiple → implied share price $Y"
  - "At [cheapest peer] multiple → implied share price $Z"
- Verdict: how DCF valuation compares to relative valuation

### Tab 3: [Ticker] Valuation
Full DCF model with:
- WACC calculation and Invested Capital history
- 10-year projections with equity bridge
- Dynamic Scenario Analysis (Bull/Base/Bear — see below)
- Reverse DCF matrix

### Tab 4: Sensitivity Analysis
Three 2D matrices with finer granularity than the reverse DCF:

**Matrix 1: Revenue CAGR × Operating Margin** (at base WACC)
- CAGR: 6-22% in 2% steps (9 rows)
- Margin: base ±4% in 1% steps (9 columns)
- Orange highlight = closest to market price

**Matrix 2: Revenue CAGR × WACC** (at base margin)
- CAGR: 6-22% in 2% steps
- WACC: base ±2% in 0.5% steps (9 columns)
- Shows WACC is typically the biggest lever

**Matrix 3: Operating Margin × WACC** (at base CAGR)
- Margin: base ±4% in 1% steps
- WACC: base ±2% in 0.5% steps
- Shows interaction between margin and discount rate

Each matrix includes:
- Green cells = price above market (undervalued)
- Red cells = price below market (overvalued)
- Orange cell = closest to current market price
- Synthesis verdict per matrix

### Tab 5: Calculations
FCF/Share analysis (auto) + weekly price history structure (manual/Google Finance).

## Dynamic Scenario Analysis (on Valuation Tab)

### Architecture
Three complete parallel DCF models linked by formulas:
- **Base Case** (rows 55-90): Full editable DCF — the master model
- **Bull Case** (rows ~98-120): Complete DCF referencing Base + adjustments
- **Bear Case** (rows ~123-145): Complete DCF referencing Base + adjustments

### Scenario Configuration (rows 92-95)
Two editable input cells per scenario:

| Adjustment | Bull (D94/D95) | Bear (E94/E95) |
|---|---|---|
| Revenue Growth Adj (pp) | +2.0% | -4.0% |
| Operating Margin Adj (pp) | +2.0% | -2.0% |

### How Cascading Works
- Each Bull/Bear row references the corresponding Base Case row + adjustment cell
- Example: Bull Revenue Growth Year 1 = `=MAX(D55+$D$94, 0)` → Base 13% + 2pp = 15%
- Shared inputs (no adjustment): WACC, Tax Rate, Sales-to-Capital, Shares, Buyback Rate, Debt, Cash
- Changing any Base Case input automatically updates Bull and Bear
- Changing adjustment cells (D94:E95) changes the scenario spread

### Formula Pattern for Scenario Blocks
```
Revenue Growth:    =MAX({col}55+{growth_adj_cell}, 0)     # Base growth + adj, floor 0
Revenue:           ={prev_col}{rev_row}*(1+{col}{rg_row}) # Chain from base year
Operating Margin:  ={col}57+{margin_adj_cell}              # Base margin + adj
Operating Income:  ={col}{rev}*{col}{om}                   # Revenue × Margin
Tax Rate:          =$C$14                                   # Shared with base
NOPAT:             ={col}{oi}*(1-{col}{tx})                # After-tax OI
Sales-to-Capital:  ={col}61                                 # Reference base STC
Reinvestment:      =({col}{rev}-{prev}{rev})/{col}{stc}    # ΔRevenue / STC
SBC:               ={col}{rev}*0.0425*(1-{col}{tx})        # Same % as base
FCFF:              ={col}{nop}-{col}{reinv}-{col}{sbc}     # Standard FCFF
TV:                =N{fcff}/(N{wacc}-N{rg})                # Gordon Growth
WACC:              =$C$20                                   # Shared with base
Discount Factor:   =1/(1+{col}{wacc})^{period}             # Mid-year convention
```

### Scenario Summary Table
Auto-updating table below Bear case showing all three scenarios:
- Share Price, Buy Price (incl MoS), vs Market %, Revenue CAGR, Avg Margin
- All cells are formulas referencing the scenario blocks

### Important: No Probability Weighting
Do NOT add probability-weighted averages. The scenarios serve as a range, not a point estimate. Weighting adds false precision since the probabilities are purely subjective. The value is in the bandwidth and the question "what must be true to justify the market price?"

## Methodology Notes

### SBC Treatment
- SBC is deducted as a real economic cost from FCFF (after-tax: Revenue × SBC% × (1-t))
- This is the Damodaran approach: SBC dilutes existing shareholders and is a real cost
- Default SBC%: average of last 3 years (SBC / Revenue)
- Since SBC is already deducted from cash flows, share count should reflect GROSS buyback activity, not net dilution

### Buyback Modeling
- **Critical distinction**: Net share change ≠ gross buyback rate
- Net dilution (e.g., -0.05%) = gross buybacks minus SBC-driven dilution
- Since SBC is already deducted from FCFF, shares should decline at the GROSS buyback rate
- Example: If company buys back $28B/year = ~77M shares on ~7.5B base → -1.0% gross rate
- Using net dilution rate (-0.05%) when SBC is separately deducted = double-counting the dilution
- Calculate gross buyback: annual buyback spending ÷ avg share price = shares retired → as % of total
- Set `buyback_rate` to gross buyback rate (positive number = annual reduction %)

### WACC: Current vs Normalized Risk-Free Rate
Two valid approaches:
1. **Current spot rate** (Damodaran standard): Use today's 10Y Treasury yield. Advantage: market-consistent, no subjective adjustment.
2. **Normalized rate** (investment bank practice): Use long-term average (~3.0-3.5%) for 10-year projections. Rationale: if you use forward-looking assumptions for growth/margins, using a snapshot rate for discounting is inconsistent.

Recommendation: Use current spot rate as default. If user asks, explain the normalized approach and offer it as alternative. A single constant WACC is standard — declining WACC over time adds complexity without defensible precision.

### Sales-to-Capital Ratio
- Measures capital efficiency: how much revenue per dollar of invested capital
- Higher = more capital-efficient (less reinvestment needed)
- Historical average is a starting point, but:
  - Heavy capex periods (AI infrastructure, cloud buildout) may depress the ratio temporarily
  - Normalizing upward (e.g., 0.65 → 1.0-1.5) is reasonable if capex is expected to moderate
  - Sensitivity: typically NOT the biggest driver of valuation; WACC and growth matter more
- Test impact: changing STC from 0.65 to 1.5 typically moves price ~15-20%, while WACC ±1% moves price ~$100+

## Reverse DCF (on Valuation Tab)

2D sensitivity matrix: revenue CAGR (rows) × operating margin (columns):
- Tests 10 growth rates (2%–25%) × 7 margin levels (centered on base ±6%)
- Each cell = implied share price at that combination
- Orange highlight = closest to market price (what the market is pricing in)
- Green cells = undervalued, red cells = overvalued at those assumptions
- Auto-verdict: compares market-implied growth/margin vs your base case

## Calculations Tab Details

**Auto-filled:**
- True FCF/Share (NOPAT − ΔIC / shares — treats SBC as real cost)
- Screener FCF/Share (True + SBC add-back — what Yahoo/screeners report, highlighted red)
- SBC/Share per year (the distortion between True and Screener)
- True FCF Yield, Growth Rate, CAGR, R² linearity
- Projected FCF/Share (10 years, using revenue growth assumptions)
- SBC Impact summary: SBC/Revenue, FCF inflation %, True vs Screener yield
- Regression stats (slope, intercept) on True FCF/Share
- TTM stats with both True and Screener FCF
- Price CAGR formulas (auto-calculate when prices filled)

**Manual input (yellow cells):**
- Price 5/10/20 years ago (or via Google Finance formula)
- Weekly close prices (520 rows = 10 years, paste from Google Finance/Yahoo)

## Beyond the Model: Qualitative Considerations

Remind the user that a DCF answers "if everything goes as expected, what's it worth?" but doesn't address:

1. **Competitive dynamics** — moat durability, disruption risk, winner-takes-most dynamics
2. **Management quality** — capital allocation track record, strategic vision
3. **Regulatory risk** — antitrust, industry regulation, international tax reform
4. **Macro environment** — Fed policy, recession risk, geopolitics
5. **Portfolio context** — position sizing, time horizon, opportunity cost
6. **Investor psychology** — ability to hold through 25-30% drawdowns

Suggest the user do a **pre-mortem**: "It's 3 years from now and this investment lost 40%. What happened?" This is the most powerful debiasing tool against confirmation bias.

## Config Dictionary Reference

```python
config = {
    "company": "PepsiCo",
    "ticker": "PEP",
    
    # WACC inputs
    "equity_market_value": 201698,      # shares × price
    "debt_market_value": 54000,         # from 10-K
    "risk_free_rate": 0.0423,           # US 10Y Treasury
    "erp": 0.055,                       # Damodaran implied ERP
    "credit_spread": 0.008,             # based on credit rating
    "tax_rate": 0.21,                   # statutory or effective
    
    # Sector betas — list of (name, unlevered_beta, revenue_weight)
    "sector_betas": [
        ("Food Processing", 0.46, 0.55),
        ("Beverage (Soft)", 0.56, 0.45),
    ],
    
    # Debt breakdown — list of (label, value)
    "debt_breakdown": [
        ("Short-Term Debt", 7082),
        ("Long-Term Debt", 37224),
        ("Other Liabilities", 9052),
    ],
    
    # Historical data (oldest→newest, ~6 years, all in millions)
    "ic_years": [2018, 2019, 2020, 2021, 2022, 2023],
    "current_assets":      [21893, 17645, 23001, 21783, 21539, 26950],
    "cash":                [8721, 5509, 8185, 5596, 4954, 9711],
    "st_investments":      [272, 229, 1366, 392, 394, 292],
    "operating_cash":      [1293, 1343, 1407, 1589, 1728, 1829],
    "current_liabilities": [22138, 20461, 23372, 26220, 26785, 31647],
    "st_debt":             [4026, 2920, 3780, 4308, 3414, 6510],
    "st_leases":           [0, 442, 460, 446, 483, 556],
    "net_ppe":             [17589, 20853, 23039, 24427, 26664, 29944],
    "goodwill_intang":     [30633, 31544, 38072, 37046, 33788, 32657],
    "hist_revenue":        [64661, 67161, 70372, 79474, 86392, 91471],
    
    # Summary tab data (same years as ic_years)
    "hist_operating_income": [10110, 10291, 10080, 11160, 10837, 12860],
    "hist_net_income":       [12515, 7314, 7120, 7618, 8910, 9625],
    "hist_sbc_values":       [256, 268, 264, 311, 357, 392],
    "hist_shares":           [1424, 1396, 1391, 1386, 1381, 1378],
    "stock_price": 147.50,  # current market price
    
    # Peer comparison data
    "peers": [
        {"ticker": "KO", "name": "Coca-Cola", "ev_revenue": 6.8, "ev_ebitda": 20.5, 
         "pe": 24.1, "op_margin": 0.30, "rev_growth": 0.02, "roic": 0.15},
        # ... more peers
    ],
    
    # DCF Projections (10 years)
    "base_year": 2024,
    "base_revenue": 91854,
    "base_oi": 12860,
    "revenue_growth": [0.02, 0.03, 0.035, 0.035, 0.03, 0.03, 0.03, 0.025, 0.025, 0.025],
    "op_margins": [0.138, 0.138, 0.136, 0.136, 0.134, 0.134, 0.132, 0.132, 0.130, 0.130],
    "base_op_margin": 0.14,
    "sales_to_capital": 1.4,
    "sbc_pct": 0.004,
    "terminal_growth": 0.02,
    "terminal_margin": 0.13,
    
    # Equity bridge
    "cash_bridge": 9711,
    "securities": 292,
    "shares_outstanding": 1378,
    "buyback_rate": 0.003,         # GROSS buyback rate (see Buyback Modeling section)
    "margin_of_safety": 0.20,
    "valuation_date": "12-02-2026",
    
    # Scenario adjustments (for dynamic Bull/Bear)
    "bull_growth_adj": 0.02,       # pp added to each year's growth
    "bull_margin_adj": 0.02,       # pp added to each year's margin
    "bear_growth_adj": -0.04,      # pp subtracted from growth
    "bear_margin_adj": -0.02,      # pp subtracted from margin
}
```

## Summary Tab — Auto-Generated Metrics

When `hist_operating_income` is provided in the config, a **Summary** tab is automatically added as the first sheet. It contains:

### Valuation Snapshot (current market data)
- P/E Ratio, EV/Revenue, EV/NOPAT
- FCF Yield (vs risk-free rate benchmark)
- Net Cash per Share, SBC / Market Cap
- Buyback Yield, PEG Ratio (manual input)

### Historical Metrics (5-6 years + average + trend)
**Margins:** Operating, Net, SBC-adjusted Operating, FCF Margin
**Returns:** ROIC, ROIIC (3yr rolling), FCF Return on Capital
**Growth:** Revenue, Cost of Revenue, Operating Expenses, Operating Income, Net Income, Share Count
**Health:** Current Ratio, Net Debt / NOPAT
**Composite:** Rule of 40 Score

### Asset-Light Company Handling
- Metrics show **N/A** when Invested Capital ≤ 0 (ROIC, FCF ROC)
- ROIIC uses 3-year rolling window to smooth volatility
- Growth from negative base shows N/A instead of misleading percentages

### Qualitative Section
Empty fields for manual input: Business Case, Competitive Moat, Management & Insiders

### Required Config Keys for Summary
| Key | Type | Description |
|-----|------|-------------|
| `hist_cost_of_revenue` | list | Cost of revenue/COGS per year (enables COGS & OpEx growth) |
| `hist_operating_income` | list | Operating income per year (triggers Summary tab) |
| `hist_net_income` | list | Net income per year |
| `hist_sbc_values` | list | Stock-based compensation per year |
| `hist_shares` | list | Diluted share count per year |
| `stock_price` | float | Current market price (or derived from equity_market_value/shares) |

## Credit Spread Reference

| Rating | Spread |
|--------|--------|
| AAA    | 0.40%  |
| AA+/AA | 0.55%  |
| A+/A   | 0.80%  |
| A-     | 1.00%  |
| BBB+   | 1.25%  |
| BBB    | 1.50%  |
| BBB-   | 1.75%  |
| BB+    | 2.50%  |
| BB     | 3.00%  |

## Common Pitfalls to Avoid

1. **Terminal Value must be discounted** — Add PV(TV), not undiscounted TV, to Enterprise Value
2. **WACC formula** — Double-check cell references; E/(D+E) × Ke + D/(D+E) × Kd(1-t)
3. **Revenue growth patterns** — Smooth progressions, not random zigzags
4. **Operating margins** — Should reflect realistic trends, not arbitrary swings
5. **Credit spread** — Match to actual credit rating (see table above)
6. **Margin of safety** — Use 20-25% for standard value investing, not 10%
7. **Single-sector beta trap** — Many companies span multiple Damodaran sectors; always weight by revenue
8. **SBC + Buyback double-counting** — If SBC deducted from FCFF, use GROSS buyback rate for shares, not net dilution
9. **Hardcoded scenario values** — Never hardcode Bull/Bear prices; use formula-linked DCF blocks that auto-update
10. **Sensitivity analysis completeness** — One matrix is not enough; test all three pairwise combinations (growth×margin, growth×WACC, margin×WACC)

## Formatting Rules

- Blue font: All hardcoded inputs (user can change)
- Black font: All formulas
- Green font: Notes and sources
- Light blue fill: Key calculated outputs
- Green fill (light): Bull case highlights
- Red/orange fill (light): Bear case highlights
- Orange fill: Market-implied cells in sensitivity analysis
- All numbers use Excel formulas, never Python-computed hardcodes

## Data Gathering Tips

### Effective Project Knowledge Queries
When financial data is available in project knowledge (10-K filings, annual reports):
- Search by specific line items + fiscal year: "revenue operating income FY2023 FY2024"
- Search for balance sheet items: "total debt long-term short-term borrowings"
- Search for cash flow items: "stock-based compensation capital expenditures"
- Multiple focused queries beat one broad query

### Damodaran Beta Lookup
- URL: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html
- Look for unlevered beta, NOT the levered beta column
- For diversified companies, identify 2-3 sectors and weight by revenue contribution
- Software sector beta (~1.15 unlevered) is commonly used for cloud/SaaS companies
