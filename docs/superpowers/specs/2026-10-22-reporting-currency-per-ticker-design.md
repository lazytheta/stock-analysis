# Rapportagevaluta per ticker + tickers zonder SEC-filings

Owner-verzoek 2026-10-22. Vervangt de keuze van 2026-10-10 (20-F-config in USD).

## Regel

Elke ticker rekent in de valuta waarin het bedrijf **rapporteert**: fundamentals,
ROCE, DCF, multi-lens, fv_low/mid/high, buy_price en de getoonde koers.
Alleen de **koers** wordt omgerekend wanneer de notering een andere munt heeft.

| Ticker | Notering | Rapporteert | Koers getoond in |
|---|---|---|---|
| ASML, SPOT | USD (Nasdaq/NYSE) | EUR | EUR |
| NVO | USD (ADR 1:1) | DKK | DKK |
| RMS.PA, EL.PA | EUR | EUR | EUR |
| 3064.T | JPY | JPY | JPY |
| RMV.L, AUTO.L | GBX (pence) | GBP | GBP (koers /100) |
| TSM | USD | USD (EDGAR levert alleen de dollarvertaling) | USD |

## Configvelden

- `reporting_currency` — munt van alle bedragen in de config.
- `listing_currency` — munt van de ruwe koers; `GBX` = pence.
- `adr_ratio` — gewone aandelen per genoteerd stuk (shares staan al in die eenheid).
- Legacy `currency` blijft gelezen als noteringsmunt (RMV.L: "GBP" + `.L` = pence).
- `source: "manual"` — geen EDGAR, fundamentals uit `fundamentals_overrides`.

Koers in rapportagemunt = ruwe koers × schaal (0,01 voor GBX) × FX(notering→rapportage),
FX via `gather_data.fetch_fx_rate` (ECB, Yahoo-fallback). Geen FX → geen live koers
(stale), nooit 1-op-1.

## Onderdelen

1. `ticker_currency.py` — pure regels hierboven.
2. Koersbron stockanalysis.com voor niet-VS tickers (getest vanaf Cloud Run);
   Frankfurt (altijd EUR) alleen nog voor EUR-noteringen.
3. `build_base_config` / `run_analysis`: geen omrekening van fundamentals meer,
   wel van de koers.
4. Watchlist, tickerpagina, `get_watchlist`: koers en bedragen in rapportagemunt,
   valutateken (€, ¥, £, kr) en `currency` in de MCP-uitvoer.
5. `add_manual_aspirant`: Aspirant zonder EDGAR; nachtelijke run en refreshes
   slaan EDGAR over bij `source: "manual"`.
6. Holdings: fair value omgerekend naar de munt van de positie voor de vergelijking.
7. Eenmalige omzetting ASML/SPOT/NVO naar rapportagemunt (+ RMV.L-koers in ponden),
   met backuptabel.

## Niet

Geen scraping van jaarverslagen; fundamentals komen via de tool.
