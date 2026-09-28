# Growth-tab op de tickerpagina

Datum: 2026-09-28 · Status: goedgekeurd in chat ("yes"). Voorbeeld:
`Desktop/Lazy Theta/3. inspirations/Screenshot 2026-09-02 at 20.51.19.png`, `20.51.25`, `20.51.34`.

## Plaats
Tabvolgorde: `Overview · Pre-Scan · Business · Phase · Moat · Growth · Risk · Fundamentals · DCF · …`.
Huisstijl zoals Business/Phase/Moat/Risk.

## Secties
1. **Growth** — twee vlakke kaarten naast elkaar (gelijke hoogte):
   - **Growth Analysis**: meter met score 1–5 en label (1 Weak · 2 Below average · 3 Average ·
     4 Strong · 5 Exceptional; kleur rood ≤ 2, geel 3, groen ≥ 4), één samenvattende zin en
     precies 3 punten met vet label. Bron: blok `analysis` van sectie "Growth Cards".
   - **Consensus**: vier label/waarde-paren — Revenue growth next FY, EPS growth next FY,
     Analysts, Fiscal year — plus een kleine bronregel. Bron: blok `consensus`; ontbreekt het,
     dan de regel `No analyst consensus available.`
   Zonder sectie → notitie `No growth analysis yet. It comes with the "Growth Cards" section.`
2. **Revenue & Earnings** — Plotly-lijngrafiek (omzet en nettowinst, $M → leesbare as zoals de
   Phase-grafiek), boven elk punt het jaar-op-jaar-percentage (niet voor het eerste punt, niet
   als het vorige jaar ≤ 0), knoppen `5Y` / `10Y` (standaard 5Y). Daaronder een tabelletje
   Revenue / Earnings × 3Y / 5Y / 10Y CAGR (hergebruik de CAGR-regel van `overview_metrics`:
   alleen als begin en eind > 0; anders `—`). Geen data → `No revenue history available.`
3. **Growth questions** — twee omdraaikaarten (motor `question_cards`):

| key | naam (achterkant) | vraag | opties (0 → 2) |
|---|---|---|---|
| industry | Industry Growth | Is the industry growing? | No / Slowly / Yes |
| optionality | Optionality | Can new offerings drive growth? | Unlikely / Possible / Likely |

## Data: sectie "Growth Cards"
```json
{"analysis":  {"score": 3, "summary": "...", "points": [3 × {"label","text"}]},
 "consensus": {"fiscal_year": "FY2027", "revenue_growth_pct": 15.2, "eps_growth_pct": 12.0,
               "analysts": 23, "source": "Equibles compiled consensus, 2026-09-24"},
 "cards": [2 × {"source","pick","summary","points": [3]}]}
```
Validatie: `analysis` verplicht (score int 1–5, bool geweigerd; summary niet leeg; 3 punten);
`consensus` optioneel (null of object): fiscal_year niet-lege string; revenue_growth_pct en
eps_growth_pct getal of null, tussen −100 en 1000; analysts int > 0 of null; source niet-lege
string; minstens één van de twee groeicijfers niet null. Kaarten zoals de andere sets.
Prompt (priors Long-Term Potential, Business Analysis, Key Metrics) staat in de bibliotheek
direct na "Company Profile". Consensus komt uit de SEC-koppeling (`GetAnalystEstimates`);
lukt dat niet, dan het `consensus`-blok weglaten — nooit schatten. MCP valideert bij opslaan;
backfill-routine vult "Growth Cards" als vijfde set (zelfde budget van 10), aspirant-routine
vult hem mee.

## Buiten scope
LTM-kolom, 7Y-knop, eigen consensus-bron in de app.

## Uitrol
Prompt via guarded SQL na "Company Profile" vóór de reboot; push; MCP-deploy; VEEV's
Growth Cards invullen; Streamlit Reboot.
