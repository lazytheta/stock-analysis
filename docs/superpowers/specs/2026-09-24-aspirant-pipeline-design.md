# Aspirant-pijplijn: van Screener naar watchlist, wekelijks en zonder toezicht

Datum: 2026-09-24 · Status: ontwerp, goedgekeurd in chat, wacht op review van deze spec

## Doel

Een wekelijkse cloud-routine die namen die door de Screener komen automatisch
beoordeelt en in LazyTheta zet:

1. Elke nieuwe Screener-geslaagde naam komt als **aspirant** in de lijst, met de
   volledige pre-scan ingevuld door Claude.
2. Alleen bij een **Wide moat** mag hij door naar de gewone watchlist, met een
   **volledig door Claude ingevulde DCF**.
3. Een bestaande config wordt **nooit** overschreven.

De Screener zelf (ROCE ≥ 20% gemiddeld, geen netto schuld) verandert niet; hij
blijft maandelijks draaien als Cloud Run Job `screener`.

## Besluiten uit de chat

| Vraag | Besluit |
|---|---|
| Moat niet wide | Blijft aspirant, met analyse; niet verwijderen, niet opnieuw beoordelen |
| Aantal per run | Maximaal 5 nieuwe namen |
| Waar | Claude Code cloud-routine (`/schedule`), abonnement, geen API-tegoed |
| Hoe vaak | Wekelijks, maandag 07:00 Europe/Amsterdam |
| Doorzetten | Automatisch bij Wide moat; label "by Claude" zodat de gebruiker ziet wat hij nog niet zelf bekeek |
| Afwijzen | Knop **NO**: naam gaat de gewone watchlist in met verdict `pass` ("No — Pass"), wordt niet verwijderd |

## 1. Data: de status "aspirant"

- Een aspirant is een gewone rij in `watchlist_configs` met in de config
  `"watchlist_status": "aspirant"` en `"aspirant_added": "<ISO-datum>"`.
  Geen schemamigratie: `config` is al jsonb.
- Doorzetten verwijdert `watchlist_status` en zet
  `"promoted_by": "claude"`, `"promoted_at": "<ISO-datum>"`.
- Afwijzen zet `"watchlist_status": "rejected"` en `"rejected_at"`, en het
  Scorecard-verdict op `"pass"` (het bestaande "No — Pass", rood in de lijst).
  Als er een robustness-tabel is, wint die in `resolve_verdict`; bij afwijzen
  wordt daarom ook `robustness.verdict_mapped` op `"pass"` gezet.
- Afwezigheid van `watchlist_status` betekent een gewone watchlistnaam. Alle
  bestaande configs blijven dus ongewijzigd geldig.

### Aspiranten en afgewezen namen blijven buiten alles wat met reële waarde rekent

Een aspirant of afgewezen naam heeft een basisconfig met vlakke
placeholder-aannames. Een reële waarde daaruit is betekenisloos en mag nergens
als oordeel verschijnen.

- `rejected` staat wél in de gewone watchlist (met NO-verdict), maar met "—"
  voor fair value, buy price en upside, en zonder `calculate_multi_lens_valuation`.
- De uitsluitingen hieronder gelden voor beide statussen.

- `config_store.list_watchlist(..., include_aspirants=False)`: nieuwe parameter,
  standaard uit. Leest `config->watchlist_status` mee in de select.
- `config_store.load_all_configs(..., include_aspirants=False)`: idem; gebruikt
  door "Refresh all".
- `mcp_server._refresh_all_valuations_impl`: slaat aspiranten over.
- `supabase/functions/notify/index.ts`: filtert rijen met
  `config->>watchlist_status = 'aspirant'` weg (geen prijsmeldingen).
- Portfolio-pagina (fair-value-kolom) gebruikt `list_watchlist` met
  `tickers=`, dus valt vanzelf onder de standaard.
- De duplicate-check bij "Add to Watchlist" (`streamlit_app.py`, rond regel 4353)
  moet aspiranten **wel** meetellen: `include_aspirants=True`.

## 2. MCP-tools (`mcp_server.py`)

De beveiliging zit in de server, niet in de routine-prompt: een fout van de
routine kan geen bestaande config overschrijven en geen niet-wide naam doorzetten.

### `get_screener_candidates(limit: int = 5) -> str`

- Leest de nieuwste rij uit `screener_snapshots`.
- Neemt de rijen met `passes = true`.
- Haalt alle tickers uit de lijst van de gebruiker weg, **in welke status ook**
  (`list_watchlist(include_aspirants=True)`).
- Sorteert op gemiddeld ROCE, aflopend; geeft de eerste `limit` terug met
  ticker, bedrijf, ROCE, netto schuld en `computed_at` van de snapshot.

### `add_aspirant(ticker: str, stock_price: float = 0) -> str`

- **Weigert** met een fout als `load_config(ticker)` al iets teruggeeft.
- Bouwt de basisconfig via dezelfde EDGAR-route als `run_analysis` in de app
  (CIK, submissions, sector betas, `parse_financials`, `build_config`), maar
  zonder Streamlit. Die route wordt daarvoor uit `streamlit_app.py` gehaald naar
  een functie in `gather_data.py` die beide aanroepen.
- **Koers:** Yahoo is vanaf Cloud Run geblokkeerd (zie geheugennotitie
  `lazytheta-cloudrun-yfinance-blocked`). Daarom `stock_price` als parameter:
  als die > 0 is wordt hij gebruikt, anders een poging via Yahoo, en bij
  mislukking een duidelijke fout ("geef stock_price mee"). De routine haalt de
  koers uit een andere connector (SEC-MCP `GetLiveQuote`).
- Slaat op met `watchlist_status = "aspirant"` en `aspirant_added`.

### `promote_aspirant(ticker: str) -> str`

- Weigert als de config geen aspirant is.
- Weigert tenzij de pre-scan-sectie `Moat` een verdictregel heeft die als
  **Wide** parset. Parser: dezelfde vorm die `prescan_render.py` al leest
  (`**Moat: Wide 🛡️ · Stable ➡️ · 4/5**`).
- Weigert als de DCF niet is ingevuld. Minimumcontrole:
  - `equity_market_value` > 0 en `sector_betas`-gewichten tellen op tot 1,0;
  - groei- en margecurves zijn niet meer de vlakke placeholders van
    `build_config` (niet elk jaar gelijk aan terminal growth / laatste marge);
  - `valuation_summary` aanwezig (dus `calculate_multi_lens_valuation` gedraaid).
- Bij succes: status weg, `promoted_by` / `promoted_at` gezet.

### Bestaande tools

- `get_watchlist`: geeft voortaan ook `status` terug (`"aspirant"` of `null`),
  en toont aspiranten alleen met een nieuwe parameter `include_aspirants=True`.
- `save_to_watchlist` blijft zoals hij is (handmatig gebruik moet kunnen
  overschrijven). De routine-prompt verbiedt hem op namen die niet in deze run
  via `add_aspirant` zijn toegevoegd.

## 3. App (Watchlist-pagina)

- Onder de gewone lijst een blok **"Aspirants (n)"**, alleen als n > 0.
  Per rij: logo, ticker, bedrijf, ROCE, moat-verdict (uit de Moat-sectie, of
  "—" als die nog leeg is), datum toegevoegd.
- Per rij twee knoppen: **Promote** (handmatig doorzetten, zonder de
  Wide-controle: de gebruiker mag zelf afwijken) en **NO** (afwijzen: status
  `rejected`, verdict `pass`, naam verschijnt in de gewone lijst als NO).
- Een afgewezen naam komt terug in het normale leven zodra de gebruiker hem in
  de editor een DCF geeft en opslaat: dan verdwijnt `watchlist_status`.
- In de gewone lijst krijgen namen met `promoted_by = "claude"` een klein label
  "by Claude · 2 Oct". Het label verdwijnt zodra de gebruiker de config zelf
  opslaat in de editor.

## 4. De routine

- Aangemaakt met `/schedule`, repo `lazytheta/stock-analysis`, wekelijks
  maandag 07:00 Europe/Amsterdam, connectors: LazyTheta Remote MCP en SEC-MCP.
- Prompt (vastgelegd in `docs/routines/aspirant-weekly.md` in de repo, zodat
  hij versiebeheerd is):
  1. `get_screener_candidates(5)`. Leeg → `add_reminder` overslaan en stoppen.
  2. Per naam: koers via `GetLiveQuote`, dan `add_aspirant(ticker, stock_price)`.
     Faalt die → naam overslaan, reden noteren.
  3. `get_prescan_prompts` → elke sectie beantwoorden →
     `save_prescan_section`. De Moat-sectie moet de vaste verdictregel bevatten.
  4. Moat niet Wide → klaar met deze naam (blijft aspirant).
  5. Moat Wide → DCF volledig invullen: groei- en margecurves met onderbouwing,
     `sector_betas` met gewichten die optellen tot 1,0, `equity_market_value`,
     scenario-aanpassingen, `set_robustness`, `set_premortem`. Opslaan via
     `save_to_watchlist`, dan `calculate_multi_lens_valuation`, dan
     `promote_aspirant`. Weigert die → reden noteren, naam blijft aspirant.
  6. Eén `add_reminder` met de samenvatting: toegevoegd, doorgezet,
     overgeslagen met reden.
- Afspraken voor de DCF volgen de bestaande regels: nominal basis, CAPM/WACC,
  geen SBC, marge of safety 20%.
- Geen peers of multiples: sinds 2026-07-30 is de watchlist-fair-value puur de
  DCF (multiples-lenzen op gewicht 0, alleen nog referentie op de tickerpagina).
  De routine vult geen peers in.

## Foutafhandeling

- Elke stap per naam is onafhankelijk: een fout bij naam 2 stopt naam 3 niet.
- `add_aspirant` is idempotent in de zin dat een tweede aanroep weigert in
  plaats van te overschrijven; een half ingevulde aspirant uit een mislukte run
  wordt de week erna niet opnieuw opgepakt (hij staat al in de lijst). Hij blijft
  zichtbaar met een lege of halve pre-scan, zodat de gebruiker het ziet.

## Testen

- `get_screener_candidates`: filtert bestaande tickers (gewoon én aspirant),
  respecteert `limit`, sorteert op ROCE, lege snapshot → lege lijst.
- `add_aspirant`: weigert bij bestaande config (ook aspirant), gebruikt
  meegegeven `stock_price`, zet status en datum.
- `promote_aspirant`: weigert bij Narrow/None/geen verdict, weigert bij
  placeholder-curves of ontbrekende summary, slaagt bij volledige config.
- `list_watchlist` / `load_all_configs`: aspiranten standaard weg, met vlag erbij;
  `rejected` wel in de lijst, zonder fair value.
- Afwijzen: zet status, verdict `pass` (ook via robustness), en de routine pakt
  de naam niet opnieuw op.
- Refresh-all en `notify` slaan aspiranten over.
- Alles offline met mocks, zoals de bestaande suites.

## Uitrol

1. App en `config_store` → `main` (Streamlit Cloud; `config_store` is een
   module buiten `streamlit_app.py`, dus na de push **Manage app → Reboot**).
2. MCP → `gcloud run deploy lazytheta-mcp --source .` vanaf de repo-root.
3. `notify` edge function herdeployen.
4. Routine aanmaken, één keer handmatig laten draaien en het resultaat samen
   nakijken vóór de eerste geplande run.

## Buiten scope

- De Screener wekelijks laten draaien (jaarcijfers veranderen per kwartaal).
- Een andere koersbron voor de hele app (de Yahoo-blokkade in het algemeen).
- Financials/REITs in de Screener (eerder gemeten en verworpen).
