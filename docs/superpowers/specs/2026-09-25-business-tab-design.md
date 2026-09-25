# Business-tab naar het inspiratievoorbeeld

Datum: 2026-09-25 · Status: goedgekeurd in chat ("akoord"). Voorbeeld:
`Desktop/Lazy Theta/3. inspirations/Screenshot 2026-09-02 at 20.48.31.png` (+ "Business quality"-kaarten).

## Indeling (tab tussen Pre-Scan en Moat)

1. **Sectie Business overview / Customer profile** — twee vlakke tekstpanelen naast elkaar,
   elk: één zin + vier punten (vet label + tekst). Geen meter.
   Bron: Business Cards-blokken `overview` en `profile`. Zonder Business Cards valt
   Business overview terug op de Business Analysis (zin + drie punten), Customer profile
   toont "Not filled yet".
2. **Sectie Revenue** — twee panelen:
   - **By segment**: totaal + 1-jaarsgroei rechtsboven, gestapelde balk met aandelen,
     tabel segment / share / revenue / 1-yr growth.
   - **By geography**: totaal, Plotly-wereldkaart (choropleth) met regio-kleuren, legenda
     met percentages.
   Streamlit-container in de witte sectiestijl (Plotly past niet in een HTML-blok).
   Zonder omzetdata: één regel uitleg.
3. **Sectie Business quality** — vier flip-kaarten (motor `question_cards`):

| key | vraag | opties (0 slecht → 2 goed) |
|---|---|---|
| predictability | How predictable is revenue? | Unpredictable / Modest / Predictable |
| pricing_power | Can the company raise prices? | No / Sometimes / Easily |
| recession | How recession-proof is it? | Weak / Okay / Strong |
| competitive_position | What is their competitive position? | Weak / Average / Dominant |

## Data: sectie "Business Cards"

```json
{"overview": {"summary": "...", "points": [4 × {"label","text"}]},
 "profile":  {"summary": "...", "points": [4 × ...]},
 "revenue":  {"period": "FY2026", "total_musd": 3195.0, "growth_pct": 16.3,
              "segments": [{"name": "...", "revenue_musd": 1430.0, "growth_pct": 21.0}],
              "regions":  [{"region": "North America", "label": "North America", "share_pct": 60.0}]},
 "cards": [4 × {"source","pick","summary","points": [3]}]}
```

- Omzetcijfers zijn door Claude uit de 10-K-segmentnoot overgenomen (EDGAR companyfacts
  bevat geen segmenten/regio's). Validatie: segmenten ≥ 1 en tellen op tot het totaal (±3%);
  regio-aandelen tellen op tot 95–105%; `region` uit de vaste lijst US, Canada,
  North America, Latin America, Europe, EMEA, Middle East & Africa, Asia Pacific, China,
  Japan, India, Rest of world; geen dubbele regio's. `revenue` en `growth_pct` mogen null.
- Motor: `CardSet` krijgt instelbare optionele blokken met een puntental
  (Moat: `trend`/3, Business: `overview`/4 en `profile`/4); Moat blijft identiek.
- Prompt direct na "Key Metrics" (context: Business Analysis, Moat Analysis, Key Metrics).
- MCP valideert bij opslaan; backfill vult drie sets (Moat, Risk, Business), samen 10/run;
  aspirant-routine neemt het mee.

## Kaart

Landen uit `plotly.express.data.gapminder()` (iso_alpha + continent) plus een kleine
aanvulling voor grote ontbrekende landen. Regio's van specifiek naar breed toegewezen
(US/Canada/China/Japan/India eerst, dan North America, Latin America, Europe,
Middle East & Africa, EMEA, Asia Pacific, Rest of world). Kleur per regio uit een vaste
palet in huisstijl-tinten.
