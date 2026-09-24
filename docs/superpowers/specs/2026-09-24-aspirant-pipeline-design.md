# Aspirant-pijplijn: van Screener naar watchlist, wekelijks en zonder toezicht

Datum: 2026-09-24 · Status: ontwerp, goedgekeurd in chat

## Doel

Een wekelijkse cloud-routine die namen die door de Screener komen automatisch
beoordeelt en in LazyTheta zet:

1. Elke nieuwe Screener-geslaagde naam komt in de watchlist-categorie
   **Aspirant**, met de volledige pre-scan ingevuld door Claude.
2. Alleen bij een **Wide moat** mag hij door naar **Uncategorized**, met een
   **volledig door Claude ingevulde DCF**.
3. Een bestaande config wordt **nooit** overschreven.
4. Afkeuren = categorie **No**, de bestaande groep in de watchlist.

De Screener zelf (ROCE ≥ 20% gemiddeld, geen netto schuld) verandert niet; hij
blijft maandelijks draaien als Cloud Run Job `screener`.

## Besluiten uit de chat

| Vraag | Besluit |
|---|---|
| Moat niet wide | Blijft Aspirant, met analyse; niet opnieuw beoordelen |
| Afkeuren | Categorie **No** (bestaande watchlistgroep); naam wordt niet verwijderd |
| Doorzetten (routine) | Categorie **Uncategorized**, label "by Claude" |
| Aantal per run | Maximaal 5 nieuwe namen |
| Waar | Claude Code cloud-routine (`/schedule`), abonnement, geen API-tegoed |
| Hoe vaak | Wekelijks, maandag 07:00 Europe/Amsterdam |
| Peers/multiples | Geen: de watchlist-fair-value is sinds 2026-07-30 puur de DCF |

## 1. Data

De watchlist kent al categorieën via `config["category"]`
(`Uncategorized`, `Yes`, `Maybe`, `Watch Later`, `No`), getoond als inklapbare
groepen en gezet met de Status-pills in de editor. Daar komt er één bij.

- **Nieuwe categorie `Aspirant`.** Toegevoegd aan de categorielijst van de
  watchlist-groepen én aan de Status-pills in de editor.
- **Nieuwe markering `dcf_placeholder: true`.** Zegt: deze config heeft nog
  geen echte DCF, alleen de basisconfig met vlakke placeholder-aannames van
  `build_config`. Gezet door `add_aspirant`; verwijderd door
  `save_to_watchlist` zodra de groei- of margecurve niet meer vlak is.
  Onafhankelijk van de categorie, zodat een afgekeurde naam in No ook geen
  nep-fair-value krijgt.
- Verder in de config: `aspirant_added` (ISO-datum), en bij doorzetten
  `promoted_by: "claude"` en `promoted_at`.
- Geen schemamigratie: `config` is al jsonb. Bestaande configs hebben geen
  `dcf_placeholder` en blijven ongewijzigd geldig.

### Geen fair value uit een placeholder-DCF

- `mcp_server._refresh_all_valuations_impl` en de "↻ Refresh all"-handler in
  `streamlit_app.py` slaan configs met `dcf_placeholder` over.
- `_calculate_multi_lens_valuation_impl` weigert bij `dcf_placeholder` met een
  duidelijke fout. Omdat `save_to_watchlist` de markering weghaalt zodra de DCF
  is ingevuld, werkt de waardering daarna gewoon.
- In de watchlistrij tonen fair value, buy price en upside "—" zolang er geen
  `valuation_summary` is. Dat gebeurt al; aspiranten krijgen er dus vanzelf
  geen.
- Prijsmeldingen (`supabase/functions/notify`) gaan al alleen uit voor
  `category === "Yes"`; geen wijziging nodig.

## 2. MCP-tools (`mcp_server.py`)

De beveiliging zit in de server, niet in de routine-prompt.

### `get_screener_candidates(limit: int = 5) -> str`

- Leest de nieuwste rij uit `screener_snapshots` (`rows`, elk met `ticker`,
  `avg_roce`, `net_debt`, `passes`).
- Neemt de rijen met `passes = true`.
- Haalt alle tickers weg die al in de lijst van de gebruiker staan, in welke
  categorie ook.
- Sorteert op `avg_roce` aflopend; geeft de eerste `limit` terug met ticker,
  bedrijf, `avg_roce`, `net_debt` en `computed_at` van de snapshot.

### `add_aspirant(ticker: str, stock_price: float = 0) -> str`

- **Weigert** als `load_config(ticker)` al iets teruggeeft.
- Bouwt de basisconfig via dezelfde EDGAR-route als `run_analysis` in de app,
  zonder Streamlit: `gather_data.build_base_config(ticker, stock_price=0)`.
  `run_analysis` blijft zoals hij is (hij toont voortgang per stap); de nieuwe
  functie herhaalt dezelfde stappen zonder UI.
- **Koers:** Yahoo is vanaf Cloud Run geblokkeerd. `stock_price > 0` wordt
  gebruikt; anders een poging via Yahoo; lukt dat niet, dan een fout
  ("geef stock_price mee"). De routine haalt de koers uit SEC-MCP
  `GetLiveQuote`.
- Slaat op met `category: "Aspirant"`, `dcf_placeholder: true`,
  `aspirant_added`.

### `promote_aspirant(ticker: str) -> str`

- Weigert als `category != "Aspirant"`.
- Weigert tenzij de pre-scan-sectie `Moat` een verdictregel heeft die als
  **Wide** parset (via `prescan_render.parse_verdict_section`, label
  `Wide`; vorm: `**Moat: Wide 🛡️ · Stable ➡️ · 4/5**`).
- Weigert als de DCF niet is ingevuld:
  - `dcf_placeholder` is weg;
  - `equity_market_value` > 0 en `sector_betas`-gewichten tellen op tot 1,0;
  - `valuation_summary` aanwezig.
- Bij succes: `category: "Uncategorized"`, `promoted_by: "claude"`,
  `promoted_at`.

### `set_category(ticker: str, category: str) -> str`

- Zet één van de categorieën (`Uncategorized`, `Yes`, `Maybe`,
  `Watch Later`, `No`, `Aspirant`). Weigert onbekende. Zo kan de gebruiker ook
  via Claude afkeuren ("zet XYZ op No").

### Bestaande tools

- `save_to_watchlist`: haalt `dcf_placeholder` weg zodra `revenue_growth` of
  `op_margins` niet meer vlak is (niet elk jaar gelijk). Overschrijven blijft
  mogelijk voor handmatig gebruik; de routine-prompt verbiedt hem op namen die
  niet in deze run via `add_aspirant` zijn toegevoegd.
- `get_watchlist` / `config_store.list_watchlist`: geven ook `category` terug.

## 3. App (Watchlist-pagina)

- `Aspirant` als groep in de watchlist, na `Yes` (standaard ingeklapt), en als
  optie in de Status-pills van de editor.
- In de rijen van de Aspirant-groep een kleine knop **NO**: zet `category` op
  `No`. Doorzetten doet de gebruiker met de bestaande Status-pills.
- Namen met `promoted_by = "claude"` in categorie Uncategorized krijgen een
  klein label "by Claude · 2 Oct" in de rij. Het label verdwijnt zodra de
  gebruiker de categorie zelf wijzigt (de pills wissen `promoted_by`).
- De "↻ Refresh all"-handler slaat `dcf_placeholder`-configs over.

## 4. De routine

- Aangemaakt met `/schedule`, repo `lazytheta/stock-analysis`, wekelijks
  maandag 07:00 Europe/Amsterdam, connectors: LazyTheta Remote MCP en SEC-MCP.
- Prompt vastgelegd in `docs/routines/aspirant-weekly.md` (versiebeheerd):
  1. `get_screener_candidates(5)`. Leeg → stoppen, geen reminder.
  2. Per naam: koers via `GetLiveQuote`, dan `add_aspirant(ticker, stock_price)`.
     Faalt die → naam overslaan, reden noteren.
  3. `get_prescan_prompts` → elke sectie beantwoorden →
     `save_prescan_section`. De Moat-sectie moet de vaste verdictregel bevatten.
  4. Moat niet Wide → klaar met deze naam (blijft Aspirant).
  5. Moat Wide → DCF volledig invullen: groei- en margecurves met onderbouwing,
     `sector_betas` met gewichten die optellen tot 1,0, `equity_market_value`,
     scenario-aanpassingen, `set_robustness`, `set_premortem`. Opslaan via
     `save_to_watchlist`, dan `calculate_multi_lens_valuation`, dan
     `promote_aspirant`. Weigert die → reden noteren, naam blijft Aspirant.
  6. Eén `add_reminder` met de samenvatting: toegevoegd, doorgezet,
     overgeslagen met reden.
- DCF-regels: nominal basis, CAPM/WACC, geen SBC, margin of safety 20%, geen
  peers.

## Foutafhandeling

- Elke naam is onafhankelijk: een fout bij naam 2 stopt naam 3 niet.
- Een half ingevulde aspirant uit een mislukte run wordt de week erna niet
  opnieuw opgepakt (hij staat al in de lijst) en blijft zichtbaar in de groep
  Aspirant met een lege of halve pre-scan.

## Testen

- `get_screener_candidates`: filtert bestaande tickers (elke categorie),
  respecteert `limit`, sorteert op ROCE, geen snapshot → lege lijst.
- `add_aspirant`: weigert bij bestaande config, gebruikt meegegeven
  `stock_price`, zet categorie, markering en datum.
- `promote_aspirant`: weigert bij andere categorie, bij Narrow/None/geen
  verdict, bij `dcf_placeholder`, bij ontbrekende `valuation_summary`; slaagt
  bij volledige config en zet Uncategorized.
- `save_to_watchlist`: haalt `dcf_placeholder` weg bij niet-vlakke curves,
  laat hem staan bij vlakke.
- Refresh-all slaat `dcf_placeholder` over; calculate weigert erop.
- `set_category`: weigert onbekende categorie.
- Alles offline met mocks, zoals de bestaande suites.

## Uitrol

1. App → `main` (Streamlit Cloud). `gather_data.py`/`config_store.py` zijn
   modules buiten `streamlit_app.py`: na de push **Manage app → Reboot**.
2. MCP → `gcloud run deploy lazytheta-mcp --source .` vanaf de repo-root.
3. Routine aanmaken, één keer handmatig laten draaien en het resultaat samen
   nakijken vóór de eerste geplande run.

## Buiten scope

- De Screener wekelijks laten draaien.
- Een andere koersbron voor de hele app.
- Financials/REITs in de Screener (eerder gemeten en verworpen).
