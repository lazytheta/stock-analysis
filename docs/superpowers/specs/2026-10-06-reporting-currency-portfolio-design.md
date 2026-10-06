# Reporting currency — step 1: preference + Portfolio page

Status: approved in conversation 2026-10-05/06. Step 1 of 3 (Portfolio → Results → Holdings);
steps 2 and 3 get their own spec.

## Why

The owner invests from euros. The Trading 212 account is in EUR; Tastytrade is a USD account.
The app computes everything in USD and shows a T212 position's return as the share's own price
move (META +33.6%), while the T212 app shows the return in EUR including the currency effect
(~38%). The owner judges results as a euro investor, so the app must be able to report in EUR
— and in EUR mode the Trading 212 figures must match the Trading 212 app.

## Success criteria

- A `€ | $` switch on the Portfolio page; the choice is stored per user and defaults to EUR.
- In EUR mode, a Trading 212 position's Return % equals T212's own
  `walletImpact.unrealizedProfitLoss / walletImpact.totalCost` within a few tenths of a percent
  (the only difference is the live quote vs T212's own snapshot and ECB vs T212's spot rate).
- In USD mode every number on the page is exactly what it is today.

## Out of scope (later steps)

- Results page (net-liq curve, deposits, monthly/yearly returns, benchmarks) — step 2.
- Holdings page and the Portfolio "vs S&P 500" card (track record vs SPY) — step 3.
  In step 1 that card stays in USD and gets a "(USD)" label in EUR mode.
- Day % including the currency's day move (needs yesterday's rate) — not planned.

## Preference

- `user_prefs.prefs["reporting_currency"]`: `"EUR"` (default when absent) or `"USD"`.
- Read once per session into `st.session_state`; the switch writes both session state and
  `config_store.save_user_prefs` (read-modify-write of the existing prefs dict, like
  `strategy_start`).
- The switch is a small segmented control (`€ | $`) at the top right of the Portfolio page,
  next to the broker-view tabs. Steps 2 and 3 reuse the same control and preference.

## Conversion rules (EUR mode)

`usd_per_eur_now` = current ECB EUR→USD rate; `usd_per_eur(d)` = ECB rate on day `d`, or the
last rate before `d` (weekends, holidays).

1. **Trading 212 cost** — T212's own `walletImpact.totalCost` (already stored as
   `account_cost`, account currency EUR). This is the rate T212 actually converted at.
2. **Cost built from trades (Tastytrade, and T212 without `account_cost`)** — every trade
   amount that makes up the row's equity cost is converted at `usd_per_eur(trade date)`:
   share purchases and sales for a plain holding; for a wheel position the trades of the last
   cycle that build `wheel_equity_cost`, plus option premiums converted on their own dates.
3. **Current value** — the live quote the page already uses (`current_price × shares`, USD)
   divided by `usd_per_eur_now`. Instruments that trade in EUR (IEQU, RMS) use their native
   EUR price × shares, no conversion.
4. **Cash / account value** — T212: its own EUR `cash.total`; Tastytrade: USD net liq divided
   by `usd_per_eur_now`.

Derived per row: `cost_eur`, `value_eur`, `pl_eur = value_eur − cost_eur`,
`return_pct_eur = pl_eur / cost_eur`, `avg_cost_eur = cost_eur / shares`,
`price_eur = value_eur / shares`.

**Positions at two brokers (e.g. NVDA "TT + T212")** — compute the EUR figures per broker row
first, then sum `cost_eur` and `value_eur` in the merge and derive the rest. `_merge_rows` today
keeps only the first row's `account_*` fields; the EUR fields must be summed instead.

## Components

- **`reporting_currency.py`** (new, pure: no Streamlit, no network)
  - `rate_on(series, d)` — the rate on `d` or the last one before it; `None` if none.
  - `row_eur(row, usd_per_eur_now, eur_history)` → `{cost_eur, value_eur, pl_eur,
    return_pct_eur, avg_cost_eur, price_eur, fx_note}`, applying rules 1–3.
  - `balance_eur(balance, usd_per_eur_now)` — rule 4 per broker.
  - `fmt_money(amount, ccy)` — `€`/`$` formatting used by the page.
- **`gather_data.fetch_fx_rate`** — the process-lifetime `_FX_CACHE` gets a one-hour expiry
  (it can be days old on Streamlit Cloud today). Shared helper; callers unchanged.
- **`portfolio_metrics._merge_rows`** — sums the EUR fields when present.
- **`streamlit_app.py` Portfolio page** — reads the preference, renders the switch, and in EUR
  mode feeds rows/balances through `reporting_currency` before building the cards. USD mode
  takes the existing code path untouched.

## Page changes (EUR mode)

| Element | EUR mode |
|---|---|
| Avg cost | `avg_cost_eur` |
| Current price | `price_eur` |
| Day % | unchanged (price move only) |
| Mkt value | `value_eur` |
| Unrealized P/L | `pl_eur` |
| Return % | `return_pct_eur` (includes the currency effect) |
| Weight | `value_eur / total_value_eur` |
| Net Liquidating Value, cash, broker pills | `balance_eur` per broker, summed |
| Day change | in €, from the price move |
| Deployment, Contribution cards | in €, same rules |
| vs S&P 500 card | unchanged, labelled "(USD)" |

All money on the page shows `€` instead of `$`.

## Missing data

- No current EUR/USD rate → the page falls back to USD mode with a small notice
  ("EUR rate unavailable — showing USD").
- No historical rate on a trade date → last known rate before it (`rate_on`).
- No history at all → today's rate, and the row's `fx_note` says the currency effect is
  missing for that position (shown as a footnote).
- T212 row without `account_cost` → rule 2 (convert its fills by date).

## Tests

- META with the T212 figures from 2026-10-05 (3 shares, `totalCost` €1,442.01, live value):
  `return_pct_eur` = `pl_eur / 1442.01`, and ≈ T212's own figure when the value matches.
- Tastytrade purchase at a different EUR/USD rate than today shows the currency effect.
- Wheel with premiums on different dates converts each on its own date.
- NVDA at two brokers: EUR cost and value are the sums of the per-broker figures.
- EUR-quoted instrument: no conversion.
- Fallbacks: no current rate, gap in history, no history, T212 row without `account_cost`.
- USD mode: page builders produce exactly today's output (regression).
- `fetch_fx_rate` cache expires after an hour.

## Deployment note

`reporting_currency.py`, `portfolio_metrics.py` and `gather_data.py` live outside
`streamlit_app.py`: after deploying, Streamlit Cloud needs *Manage app → Reboot*.
