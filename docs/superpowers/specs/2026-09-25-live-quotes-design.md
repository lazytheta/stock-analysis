# Live koersen: één keten voor de hele app

Datum: 2026-09-25 · Status: goedgekeurd in chat ("yes"). Project 1 van 2; project 2 is de
Overview-tab (koersgrafiek, profiel, kerncijfers), die hierop voortbouwt.

## Probleem

Yahoo blokkeert verzoeken op user-agent en bron-IP (gemeten 2026-09-25: 429 vanaf Google
Cloud én lokaal voor een volledige Chrome-UA; `Mozilla/5.0` kaal komt nu door, maar dat is
geen afspraak). De watchlist en de portfolio gaan al eerst via de Tastytrade-feed
(`broker_adapter.fetch_current_prices`, sinds 2026-09-03), maar de rest vraagt alleen Yahoo:

- tickerpagina: `streamlit_app.py` `_price()` → `fetch_stock_price` (koers bovenaan, DCF, upside);
- `gather_data.fetch_stock_price`, en daarmee: ticker toevoegen/config bouwen
  (`streamlit_app.py` DCF-builder, `gather_data` build-paden), peers, en de MCP op Cloud Run
  (`build_dcf_config`, `_yahoo_quote`, `add_aspirant` — daarom geeft de routine nu een koers mee).

Wisselkoersen vallen erbuiten: die gaan sinds 2026-09-22 via de ECB (`fx.py`).

## Nieuwe bron: Nasdaq

`GET https://api.nasdaq.com/api/quote/{SYM}/info?assetclass=stocks|etf`, header
`Accept: application/json` en een korte user-agent. Geen sleutel; werkt vanaf Google Cloud
(getest via Cloud Build). Antwoord `data.primaryData`: `lastSalePrice` ("$71.6993"),
`netChange` ("-0.0207"), `lastTradeTimestamp` ("Sep 25, 2026 9:02 AM ET"), `isRealTime`.
`data` is `null` voor een onbekend symbool of verkeerde assetclass.

## Ontwerp

In `quotes.py` (geen Streamlit-import):

- `fetch_nasdaq_quotes(tickers) -> {ticker: quote | None}`
  - quote = `{"price", "previousClose", "asof", "venue": "Nasdaq"}`, met
    previousClose = price − netChange.
  - Eerst `assetclass=stocks`; is `data` null, dan `etf`.
  - Overslaan (→ None, geen verzoek): symbolen die niet voldoen aan `^[A-Z]{1,5}$`
    (dus `ENX.PA`, `BRK-B`, `EURUSD=X`, `^GSPC`).
  - Parallel met maximaal 8 tegelijk, timeout 5 s per verzoek; elke fout → None voor die ticker.
- `live_quotes(tickers, broker=None, isin_by_ticker=None) -> {ticker: quote | None}`
  - Keten: `broker(tickers)` (als meegegeven) → Nasdaq → Yahoo
    (`tastytrade_api.fetch_current_prices`) → Frankfurt (`fetch_frankfurt_quotes`,
    alleen tickers met ISIN).
  - Elke stap krijgt alleen wat de vorige miste. Een uitzondering in een stap wordt gelogd en
    telt als "niets gevonden".
  - Volgorde en lengte van de uitvoer = invoer (dubbele tickers één keer).

Gebruikers:

- `broker_adapter.fetch_current_prices(tickers, isin_by_ticker=None)` wordt een dunne laag:
  `live_quotes(tickers, broker=<Tastytrade-feed als ingelogd>, isin_by_ticker=...)`.
  Gedrag gelijk, plus Nasdaq als tweede bron.
- Tickerpagina: `_price()` gebruikt `fetch_current_prices([ticker], {ticker: isin})` (cache 30 s).
- `gather_data.fetch_stock_price(ticker)` houdt zijn signatuur `(price, 0, 0)`, maar haalt de
  koers via `live_quotes([ticker])` (zonder broker: Nasdaq → Yahoo → geen Frankfurt zonder
  ISIN). De FX-terugval die `fetch_stock_price("EURUSD=X")` aanroept, blijft werken: Nasdaq
  slaat dat symbool over, Yahoo pakt het.
- MCP: `add_aspirant(ticker, stock_price=0)` — bij 0 haalt hij de koers zelf op; de docstring
  en `docs/routines/aspirant-weekly.md` noemen het meegeven niet langer nodig.
  `_yahoo_quote` in de MCP-koerscache gaat via dezelfde keten.

Buiten scope: yfinance-aanroepen voor IBKR-posities (`ibkr_api.py`) en de yfinance-helpers in
`gather_data` voor fundamentele data.

## Faalgedrag

Mislukt de hele keten, dan blijft de bestaande terugval: de opgeslagen `stock_price`, gemarkeerd
als verouderd (`_resolve_watchlist_price`). Niets wordt als 0 getoond.

## Tests (offline, gemockt)

- Nasdaq-parsing: prijs/netChange met `$` en komma's; previousClose; `asof`.
- `data: null` bij stocks → tweede verzoek met etf.
- Overslaan van `ENX.PA`, `BRK-B`, `EURUSD=X` zonder netwerkverzoek.
- Keten: broker-treffer wordt niet opnieuw gevraagd; Nasdaq-uitzondering valt door naar Yahoo;
  Frankfurt alleen voor ISIN-tickers.
- `fetch_stock_price` gebruikt de keten; bestaande broker_adapter-tests blijven slagen.

## Uitrol

Push naar `main` → Streamlit Reboot (modulewijziging); Cloud Run-deploy van de MCP. Daarna
live controleren: tickerpagina toont een actuele koers en de MCP (`build_dcf_config`/
`get_watchlist`) geeft een koers zonder dat er een wordt meegegeven.
