# Reporting currency — step 3: Holdings + Portfolio "vs S&P 500"

Status: approved in conversation 2026-10-06 ("bouw het maar direct"). Steps 1 and 2:
`2026-10-06-reporting-currency-portfolio-design.md`, `...-results-design.md`.

## Holdings

- `€ | $` switch (shared preference).
- EUR mode: the per-broker rows go through `reporting_currency.to_eur_cost_basis` before
  `merge_by_symbol`, as on Results. Cards (Bought, Now, P/L badge, vs S&P, transactions,
  "If I'd held") then read euros; SPY closes and closed-name quotes convert to EUR.
- USD mode unchanged.

## "Currency" row

Per card: the part of the P/L that is the exchange rate,
`reporting_currency.fx_effect(usd_row, eur_row, ccy, usd_per_eur_now)`:

- EUR mode, share trading in USD: `P/L_eur − P/L_usd / rate_now`.
- USD mode, share trading in EUR: `P/L_usd − P/L_eur × rate_now`.
- Same currency: 0. Shown when |effect| ≥ 1, not in the per-wheel view.
- Computed per broker row and summed per symbol (NVDA at two brokers).
- For Trading 212 this is close to T212's own `fxImpact`; one formula for every position so it
  adds up with the P/L on the card. The euro copy is also built in USD mode (when a rate is
  known) because a euro-quoted holding needs its euro P/L.

## Portfolio "vs S&P 500" card

EUR mode: open lots, today's price and SPY from the euro copy; the "(USD)" label is gone.

## Tests

`tests/test_reporting_currency.py`: fx_effect for a dollar share in EUR mode, a euro share in
USD mode, and zero for the same currency.
