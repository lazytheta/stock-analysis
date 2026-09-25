# Moat-tab met vraagkaarten die omdraaien

Datum: 2026-09-25 · Status: goedgekeurd in chat (gebruiker: "BOUWEN", spec-review overgeslagen)

## Richting

De tickerpagina krijgt stap voor stap onderwerp-tabs (Moat, Business, Risk, Management, …)
naar inspiratie van een externe tool, in LazyTheta-huisstijl. Op termijn vervangen die
tabs de Pre-Scan-tab helemaal; voorlopig blijft Pre-Scan staan. Dit is deel 2 van drie
(vraagkaarten), eerst voor één tab: **Moat**.

## Data: sectie "Moat Cards"

- Nieuwe prompt "Moat Cards" in `DEFAULT_AI_PROMPTS`, direct na "Moat Analysis", met
  `{prior:Moat Analysis}` als context. Hij levert alleen een ```json-blok.
- Opgeslagen als pre-scan-sectie `Moat Cards` (zoals Scorecard JSON is).
- Vorm:
  `{"cards": [{"source", "pick", "direction", "summary", "points": [{"label","text"}×3]}]}`
- Vijf vaste bronnen, deze volgorde; vraag en opties staan in code (`moat_cards.SOURCES`),
  niet in de output:

| source | vraag | opties (0/1/2) |
|---|---|---|
| switching_costs | How hard is it to switch? | Easy / Moderate / Hard |
| network_effects | Does scale help customers? | No / Somewhat / Yes |
| intangible_assets | Does the brand or IP earn a premium? | No / Some / Strong |
| low_cost | Is there a cost advantage? | No / Some / Large |
| counter_positioning | Would copying hurt incumbents? | No / Partly / Yes |

- `pick` ∈ {0,1,2}: 0 = rood, 1 = geel, 2 = groen (hoger = sterkere moat).
- `direction` ∈ {widening, stable, narrowing}.
- Validatie in `moat_cards.parse_moat_cards`; `save_prescan_section` weigert ongeldige
  JSON voor titel "Moat Cards" met een leesbare fout.
- Eénmalig script voegt de prompt toe aan `user_prefs.ai_prompts` als hij ontbreekt,
  direct na "Moat Analysis"; bestaande prompts blijven ongemoeid.

## Weergave: tab "Moat"

- Nieuwe tab tussen Pre-Scan en Fundamentals.
- Boven: twee kaarten uit de bestaande Moat Analysis — **Moat size** (bestaande
  `_verdict_card_html`) en **Moat direction** (richting uit de verdictregel +
  "Weakest link"). Werkt voor alle namen zonder nieuwe data.
- Rij "Moat sources": één stip per bron in de kleur van `pick`.
- Vijf vraagkaarten in twee kolommen. Voorkant: vraag, richtinglabel, drie rondjes
  (`prescan_render.three_state_html`), één zin, "Details →". Achterkant (donker): drie
  punten, gekozen optie als label, "Back to summary ↩". Omdraaien met verborgen checkbox
  + CSS 3D-flip, geen JS, geen rerun; vaste hoogte; smal scherm onder elkaar; licht/donker.
- Zonder "Moat Cards": de twee bovenste kaarten + één uitlegregel.

## Vullen

- Nieuwe namen: de aspirant-routine vult alle prompts uit `get_prescan_prompts`, dus ook
  Moat Cards.
- Bestaande namen: MCP-tool `tickers_missing_section(title, limit)` (namen met Moat
  Analysis zonder Moat Cards) + tijdelijke routine "Moat Cards backfill", dagelijks
  07:30, 10 namen per run; gebruiker zet hem uit als hij leeg is.

## Testen

Parser (geldig/ongeldig), weigering bij opslaan, HTML van kaart en tab (gekozen optie,
richting, drie punten, unieke checkbox), tab zonder Moat Cards, `tickers_missing_section`,
Dockerfile-COPY (bestaande test), handmatig lokaal op één naam (flip, licht/donker, smal).
