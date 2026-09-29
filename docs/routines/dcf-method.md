# DCF method — how to fill in revenue growth, margins and reinvestment

Used by the Aspirant nightly run (step 2c) and by any one-off DCF fill-in.
The approach is the buy-side one: trust the near term to consensus and
guidance, and let the Moat verdict decide how fast growth and margins fade
back to ordinary. The company's own history is a check, not the anchor.

Fixed rules: nominal basis, the fixed hurdle rate as discount rate (default
9%, `discount_mode` "hurdle"; never set discount_mode or hurdle_rate), no SBC
adjustments, margin of safety 20%, no peers. sector_betas and
equity_market_value are still required by save_to_watchlist, but under the
hurdle they do not move the discount rate.

## 1. Gather the three anchors

- **History** — `get_fundamentals(ticker)`: revenue CAGR over 3, 5 and 10
  years (as far as the data goes), operating margin per year (last, 5- and
  10-year median, 10-year max), and the incremental sales-to-capital ratio
  over the last 5 years (Δrevenue ÷ Δinvested capital; invested capital =
  net PP&E + goodwill/intangibles + current assets − current liabilities −
  cash and short-term investments).
- **Consensus** — the SEC connector's `GetAnalystEstimates` (reuse the call
  made for Growth Cards if this run already made it). Per fiscal year not
  yet reported: consensus revenue growth vs the last reported year, and the
  implied operating or EBIT margin if an EBIT/operating-income estimate is
  given. Never estimate consensus numbers by hand; if the call returns
  nothing usable, write "no consensus".
- **Guidance** — the SEC connector's `GetGuidance(ticker)`, once. Revenue
  and margin guidance for the current and next fiscal year, as stated. If
  it returns nothing, write "no guidance".

## 2. Revenue growth, year by year (10 years)

- **Years 1–2**: consensus growth. Where guidance exists and differs, sit
  within or at the edge of the guidance range and say why; management
  guidance tends to be conservative, long-term consensus optimistic. No
  consensus and no guidance: the 3-year CAGR, haircut, and say so.
- **Years 3–10**: fade from the year-2 rate to `terminal_growth`. The Moat
  verdict sets the speed:
  - **Wide** — close to the year-2 rate through year 5, then linearly to
    terminal by year 10.
  - **Narrow** — linearly from year 3, reaching terminal by year 7.
  - **None** — at terminal by year 5.
- **Base-rate checks** (each one broken needs an explicit reason in the
  rationale): growth in year 3 or later above the company's own 10-year
  CAGR; double-digit growth after year 5; double-digit growth in any year
  for a company with more than $10B revenue. Sustained high growth is rare
  and larger companies grow slower.
- **`terminal_growth`**: nominal, at most the risk-free rate and at most
  3.5%; 2.5–3% for an ordinary business.

## 3. Operating margins, year by year

- **Years 1–2**: from `base_op_margin`, towards the consensus or guided
  margin where given.
- **Later years → `terminal_margin`**, by Moat verdict:
  - **Wide** — may hold or expand toward the 10-year max when operating
    leverage explains it.
  - **Narrow** — back toward the 5–10-year median.
  - **None** — back toward the 10-year median or lower.
- A margin above the 10-year max in any year needs an explicit reason.

## 4. Reinvestment

- `sales_to_capital`: the historical incremental ratio from step 1, clipped
  to a sane range (0.3–5). If it cannot be computed, pick a sector-typical
  value and say so.
- Check: implied terminal return on capital ≈ terminal_margin × (1 − tax
  rate) × sales_to_capital. Wide may stay well above the discount rate (the hurdle);
  Narrow modestly above; None close to it. If it does not fit the verdict,
  adjust sales_to_capital or the margin and say which.

## 5. Balance check — cash and investments in the equity bridge

The DCF adds `cash_bridge` + `securities` to enterprise value. The base config
fills both from EDGAR without judgement, so check them against the latest
10-K/10-Q balance sheet (the SEC connector's filing search).

- **Money that is not the shareholders'** — always when the config carries
  `balance_review` (SIC 6000–6499: banks, brokers, exchanges, insurers,
  insurance agents), and for anyone else holding client money (payment and
  payroll processors, marketplaces holding seller funds). Cash and
  investments that back claim reserves, deposits, client funds or clearing
  collateral do not belong to shareholders. Use only what the parent company
  can freely use: the parent-only balance sheet (10-K Schedule I, "condensed
  financial information of registrant") or the "parent company cash and
  investments" the company reports. If neither can be found, set
  both `cash_bridge` and `securities` to 0 (conservative) and say so.
- **Missing investments** — every company. If the filing shows short-term
  investments or marketable securities (current, and long-term if they are
  plainly liquid) that are missing from or smaller than `securities`, set
  `securities` to the filing's figure. EDGAR tags change over the years, so
  a null or stale value here is common.
- Report old and new values, the filing, its date and the line items used.

## 6. Save, scenarios, value

1. `get_config(ticker)`, set revenue_growth, op_margins, terminal_growth,
   terminal_margin, sales_to_capital, cash_bridge and securities (section 5),
   sector_betas as [name,
   unlevered_beta, revenue_weight] with weights summing to 1.0, and
   equity_market_value ($M). Save with `save_to_watchlist`.
2. `update_dcf_scenario_adjustments`: size bull and bear from the spread
   between the anchors (e.g. guidance range, consensus vs history); keep the
   defaults (+2% / −4% growth) when there is nothing to size them on.
3. `set_robustness`, `set_premortem`, then
   `calculate_multi_lens_valuation(ticker)`.
4. **Reverse-DCF check**: `calculate_valuation(config)` with the config you
   just saved (from `get_config`) returns `wacc` (the discount rate for the
   ROIC check in section 4), `implied_growth` and `implied_margin` — what
   the current price already assumes. Compare them with your years 1–5.

## 7. Rationale

Save with `save_prescan_section(ticker, "DCF Rationale", content)`:

```
**Anchors** — history: CAGR 3y x% · 5y x% · 10y x%; op margin last x%,
median 5y x% / 10y x%, max x%; incremental sales-to-capital x.
Consensus: FYxx +x% revenue, x% margin (n analysts) | no consensus.
Guidance: … | no guidance.

**Chosen** — growth y1–y10: …; terminal x%. Margins y1–y10: …; terminal x%.
Sales-to-capital x → implied terminal ROIC x% vs discount rate x%.
Fade: <Wide|Narrow|None> pattern.

**Checks** — each base-rate check broken, with the reason (or "none broken").
Reverse DCF: price implies x% growth vs our x% (y1–5) → <what that means>.

**Balance** — cash_bridge x → x, securities x → x (filing, date, lines) |
unchanged, checked against <filing>.

**Result** — fair value x, buy price x, vs price x.
```
