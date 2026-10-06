# Reporting currency — step 2: Results page

Status: approved in conversation 2026-10-06. Step 2 of 3; step 1 (Portfolio) is
`2026-10-06-reporting-currency-portfolio-design.md`, step 3 is Holdings.

## Goal

In EUR mode the Results page shows what the owner earned as a euro investor: every amount
in €, every return a euro return (currency effect included), benchmarks measured in euros.
The Trading 212 tab's deposits are exactly the euro amounts and its curve is T212's own
euro value. USD mode is unchanged. Scope: everything on Results (owner chose "Alles op
Results"), including the vs SPY pills and the month/week detail views.

## Approach: a euro copy of the data (approach A)

Convert the inputs once, at their own dates, and run every existing Results computation
unchanged on the converted inputs.

### 1. Positions — `reporting_currency.to_eur_cost_basis(cost_basis, usd_per_eur_now, history)`

Returns a new dict (the session's cost_basis is not modified). Per row:

- Every trade is copied with `net_value` and `price` in EUR:
  - a Trading 212 fill with `wallet_net_value`: `net_value = wallet_net_value` (the euros the
    account actually moved), `price = |net_value| / quantity`;
  - any other trade: `net_value / rate_on(history, date)`, same for `price`
    (`usd_per_eur_now` when no history).
- Sums rebuilt from the converted trades with the same categories the brokers use:
  `equity_cost` (equity trades except money movements), `option_pl` (option trades),
  `dividends` (money-movement rows on the instrument), `total_pl` (all trades),
  `total_credits` / `total_debits`. A USD remainder the trades do not explain converts at
  today's rate. Trading 212 rows keep T212's own euro cost (`-account_cost`) as
  `equity_cost`, as on Portfolio.
- Live fields to EUR at today's rate (EUR-quoted lines keep their euros): `current_price`,
  `previous_close`, `broker_price`, `purchase_price`, `cost_per_share`; then
  `market_value = current_price × shares`, `total_pl_real = total_pl + market_value`.
- `wheels` re-detected from the converted trades; `currency = "EUR"`, `fx_rate = 1.0`;
  `_eur_history` attached for the month/week report (below).

### 2. Curve and deposits

- **Curve**: the merged USD curve divided by `rate_on(history, day)` per point. Exact: the
  T212 curve was converted to USD with the same ECB series, so dividing gives back T212's
  own euros; a Tastytrade point becomes its euro value that day.
- **Deposits**: converted at the source, per transfer date, because they are aggregated by
  month afterwards:
  - `t212_api.fetch_yearly_transfers(creds, currency)` — EUR: T212's own amounts.
  - `tastytrade_api.fetch_yearly_transfers(refresh_token, eur_history=None)` — with a history,
    each deposit/withdrawal is divided by its date's rate.
  - `broker_adapter.fetch_yearly_transfers(currency)` / `fetch_all_yearly_transfers(currency)`
    pass it on. IBKR (unused by the owner) converts its monthly totals at mid-month rates.
- Transfers between the owner's brokers still cancel, up to the real FX cost of the transfer.
- Session-state cache keys for curve and transfers include the currency.

### 3. Benchmarks

- SPY daily closes (Since-window comparison, vs SPY pills): divided by the day's rate.
- Yearly benchmark returns (S&P 500, NASDAQ 100, MSCI World):
  `tastytrade_api.fetch_benchmark_returns(eur_history=None)` — with a history, each year-end
  monthly close is divided by the rate on that month's last day before computing the return.

### 4. Month/week report

`_aggregate_month_trades` / `_aggregate_week_trades` value unrealized moves as
`shares × Δclose × fx_rate`. With a euro copy (row has `_eur_history`) and a non-EUR line:
`shares × (close_end / rate_end − close_start / rate_start)`. Everything else in the report
reads the converted trades.

### 5. Page

- The `€ | $` switch (shared preference with Portfolio) next to the broker tabs.
- In EUR mode: cost_basis → euro copy; curve and SPY closes divided by daily rates;
  transfers and benchmarks fetched in EUR. All money shows `€`: hero, pills, chart hover,
  performer cards, deposits column, month/week reports (`_fmt_k`), vs SPY pill.
- The money symbol for module-level formatters (`_fmt_k`, `_track_record_pill_html`) comes
  from `st.session_state["_money_sym"]`, reset to `$` at the start of every run.

## Missing data

As step 1: no current rate → USD with a notice; gaps → last rate before; T212 fill without
`wallet_net_value` → convert its USD amount at the fill date's rate.

## Tests

- MSFT example: bought $1,000 at 1.05, sold $1,100 at 1.12 → realized €30 in the euro copy
  (vs +$100), and the month report shows it.
- Euro copy: T212 fill uses wallet_net_value; T212 equity_cost = −account_cost; dividends
  and options categorised; live fields; EUR-quoted line untouched; original not modified.
- Curve conversion per day; T212 deposits in EUR are the native amounts; Tastytrade
  deposits per date; a TT→T212 transfer nets to its FX difference.
- Benchmark yearly return in EUR from closes and rates.
- Month report unrealized move in EUR.
- USD mode: no conversion anywhere (regression).

## Deployment

Modules outside streamlit_app.py change → Streamlit *Manage app → Reboot*.
