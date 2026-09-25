# Overview-tab op de tickerpagina

Datum: 2026-09-25 · Status: goedgekeurd in chat ("akoord"). Project 2 van 2; project 1 (live
koersketen, `quotes.live_quotes`) staat live. Voorbeeld:
`Desktop/Lazy Theta/3. inspirations/Screenshot 2026-09-02 at 20.47.18.png` (Netflix).

## Indeling

Overview wordt de eerste tab en opent standaard:
`Overview · Pre-Scan · Business · Moat · Risk · Fundamentals · DCF · …`. Eén witte sectie
(`.qc-section`-stijl, key `qc_overview_section`) met:

1. **Profiel (linkerkolom)** — label/waarde-paren: Sector, Industry, Market cap, Capital type,
   Difficulty (chip), Founded, Employees; daaronder tags als chips. Bron: sectie
   "Company Profile"; Market cap = live koers × laatste aandelenaantal (EDGAR).
   Zonder profiel: alleen Market cap + regel
   `Company profile not filled yet. It comes with the "Company Profile" section.`
2. **Koersgrafiek (rechterkolom)** — procentuele verandering van het aandeel en van SPY
   ("S&P 500") vanaf het begin van de gekozen periode; knoppen `1M 6M YTD 1Y 3Y 5Y 10Y`
   (standaard 5Y). Boven de grafiek: `{TICKER} · 5Y +40.0% CAGR +7.0%` en
   `S&P 500 +68.2% CAGR +11.0%` (CAGR alleen bij periodes ≥ 1 jaar). Laatste punt = live koers.
   Zonder historie: `No price history for this listing yet.`
3. **Missie** — één cursieve regel over de volle breedte (uit het profiel).
4. **Kerncijfers** — vier kolommen, tabelrijen label/waarde:
   - Profitability (label `LATEST FISCAL YEAR (FY2026)`): Gross margin, Operating margin,
     Net margin, FCF margin. Financial Health: Cash & investments, Total debt, Debt / Equity,
     EBIT / Interest.
   - Growth (`COMPOUND ANNUAL GROWTH`): Revenue, EPS, FCF — elk 3Y / 5Y / 10Y.
   - Valuation (`AT CURRENT PRICE`): P/S, P/E, P/B, P/FCF. Shareholder Returns: Dividend
     yield, Buyback yield, Debt paydown yield, Total shareholder yield.
   Niet te berekenen → `—`.

## Data

### Kerncijfers (bestaande EDGAR-fetchers, geen wijziging aan gather_data)
- `fetch_fundamentals(ticker, n_years=11)` ($M; shares raw): revenue, gross_profit,
  operating_income, net_income, fcf, cash, short_term_investments, total_debt, total_equity,
  shares, eps.
- `fetch_income_statement(ticker, n_years=11)`: interest_expense ($M).
- `fetch_cashflow_statement(ticker, n_years=11)` ($M, uitstroom negatief): dividends_paid,
  stock_buybacks, debt_repayment, debt_issuance.
- Peiljaar = laatste jaar in fundamentals met omzet; cash-flow/income op datzelfde jaar.
- Definities: marges = x / revenue; cash & investments = cash + short_term_investments;
  D/E = total_debt / total_equity (alleen equity > 0); EBIT/interest = operating_income /
  |interest_expense| (alleen interest ≠ 0); CAGR = (eind/begin)^(1/n) − 1 alleen als begin
  en eind > 0; market cap = price × shares / 1e6; P/S, P/B (equity > 0), P/FCF (fcf > 0),
  P/E = price / eps (eps > 0); dividend-/buyback-yield = |bedrag| / market cap;
  debt paydown = (|debt_repayment| − debt_issuance) / market cap (mag negatief);
  total = som van de drie.
- `apply_fundamentals_overrides(fund, cfg['fundamentals_overrides'])` zoals de
  Fundamentals-tab.

### Koershistorie
- Tabel `price_history(ticker text, day date, close numeric, primary key (ticker, day))`,
  RLS aan, policy `price_history_read`: SELECT voor `authenticated`, `true`. Schrijven alleen
  met de service key.
- Cloud Run Job `price-history` (patroon van de screener-job): tickers = alle distinct
  tickers uit `watchlist_configs` die `quotes._nasdaq_symbol_ok` halen, plus `SPY`. Per
  ticker: laatste `day` in de tabel; geen → vanaf vandaag − 10 jaar; anders vanaf dag + 1.
  Bron `https://api.nasdaq.com/api/quote/{SYM}/historical?assetclass={stocks|etf}&fromdate=
  YYYY-MM-DD&todate=YYYY-MM-DD&limit=9999` (rows: `date` "MM/DD/YYYY", `close` "$71.72").
  Upsert in blokken van 500. 0,3 s pauze tussen tickers. Fout bij één ticker → loggen,
  doorgaan. Schema `30 23 * * 1-5` Europe/Amsterdam.
- De app leest per ticker + SPY (cache 1 uur) en voegt de live koers toe als punt van vandaag
  als dat na de laatste dag ligt.

### Profiel: sectie "Company Profile"
```json
{"sector": "Communication Services", "industry": "Entertainment",
 "capital_type": "Asset-light", "difficulty": "Moderate", "founded": 1997,
 "employees": 16000, "tags": ["Subscription", "Ad-based"],
 "mission": "To entertain the world."}
```
Validatie: sector/industry niet leeg (≤ 60 tekens); capital_type ∈ {Asset-light,
Asset-heavy}; difficulty ∈ {Easy, Moderate, Hard}; founded int 1600..huidig jaar of null;
employees int > 0 of null; tags 1–4 unieke niet-lege strings ≤ 24 tekens; mission niet leeg,
≤ 200 tekens. Prompt (priors Business Analysis, Moat Analysis) staat in de bibliotheek direct
na "Business Cards"; MCP valideert bij opslaan (`_CARD_PARSERS`); backfill-routine en
aspirant-routine vullen hem mee. Difficulty = hoe lastig het bedrijf te doorgronden is
(Easy = één product, simpel verdienmodel; Hard = conglomeraat, financials, biotech-pijplijn).

## Buiten scope
Europese tickers krijgen geen grafiek (Nasdaq kent ze niet). Geen TTM uit kwartaalcijfers.
Geen Technical Analysis/Download/Advanced. Pre-Scan blijft.

## Uitrol
Tabel via Supabase-migratie; job deployen en één keer handmatig draaien (backfill);
prompt via guarded SQL na "Business Cards" vóór de reboot; push; MCP-deploy; Streamlit Reboot;
Company Profile voor VEEV invullen; live controleren.
