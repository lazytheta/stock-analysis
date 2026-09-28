# Phase-tab op de tickerpagina

Datum: 2026-09-28 · Status: goedgekeurd in chat ("yes klopt"). Voorbeeld:
`Desktop/Lazy Theta/3. inspirations/Screenshot 2026-09-02 at 20.49.13.png` en `20.49.20.png`.

## Plaats
Tabvolgorde: `Overview · Pre-Scan · Business · Phase · Moat · Risk · Fundamentals · DCF · …`.
Huisstijl: witte `.qc-section`-secties met vlakke binnenkaarten, zoals Business/Moat/Risk.

## Fasen
De app houdt **6 fasen** aan (niet de 5 van het voorbeeld), gelijk aan de prompt
"Business Phase Analysis" en het Scorecard: 1 Startup, 2 Hypergrowth, 3 Self Funding,
4 Operating Leverage, 5 Capital Return, 6 Decline.

## Secties
1. **Phase** — twee vlakke kaarten naast elkaar (gelijke hoogte):
   - **Phase Analysis**: badge met het fasenummer en de naam, de openingszin en de punten uit
     de bestaande sectie "Business Phase Analysis" (via `prescan_render.parse_verdict_section`:
     label, score/out_of, summary, bullets). Fasenummer = score; valt terug op
     Scorecard `phase.number` als de analyse geen score heeft. Geen analyse → notitie
     `No phase analysis yet. It comes with the "Business Phase Analysis" section.`
   - **Growth cycle**: statisch SVG-diagram, 6 even brede kolommen met nummer + naam,
     drie gestileerde curves (Revenue stijgt en zakt in fase 6; Profits onder nul in 1–2,
     break-even rond 3, piek 4–5, daalt in 6; Payouts nul tot 4, stijgt in 5, zakt in 6),
     de huidige fase als gemarkeerde kolom (lichte accentband + badge). Zonder fase: geen band.
2. **Revenue & Operating Cash Flow** — Plotly-lijngrafiek (twee lijnen, $), boekjaren uit
   `fetch_fundamentals` (`revenue`, `cfo`), knoppen `5Y` / `10Y` (standaard 5Y). Eronder één
   berekende zin: `Revenue grew {cagr} a year over {n} years; operating cash flow was positive in
   {k} of {n} years.` (CAGR alleen als begin en eind > 0; anders de omzetzin weglaten.)
   Geen data → `No revenue history available.`
3. **Payouts** — twee omdraaikaarten (motor `question_cards.flip_card_html`), volledig berekend
   uit EDGAR (geen Claude):
   - **Are they buying back stock?** opties `No / Yes & diluting / Yes & shrinking`.
     Inkoop = som |stock_buybacks| over de laatste 5 boekjaren (cash-flow statement).
     0 → No; > 0 en aandelenaantal (fund `shares`) over 5 jaar gedaald met ≥ 1% → shrinking;
     anders diluting. Voorkant-zin: bv. `Shrinking the share count: 4.1% fewer shares than
     FY2021.` / `Buying back, but issuing faster: 2.3% more shares than FY2021.` /
     `No buybacks in the last five years.` Achterkant, 3 punten: `Bought back` (totaal 5 jaar +
     laatste jaar), `Share count` (toen → nu, %), `Buyback yield` (laatste jaar / market cap).
   - **Do they pay a dividend?** opties `No / Yes & stable / Yes & growing`.
     Dividend = |dividends_paid| laatste boekjaar > 0. Growing als dividend per aandeel
     (fund `dividends_per_share`, anders dividends_paid) over 3 jaar met ≥ 2% per jaar steeg;
     anders stable. Voorkant-zin: `No dividend: all cash returned through buybacks or
     reinvested.` / `Dividend growing {x}% a year over 3 years.` / `Dividend paid, roughly flat.`
     Achterkant, 3 punten: `Dividend per share` (laatste jaar), `Yield` (bij huidige koers),
     `Payout ratio` (dividends / net income) — bij No: `Cash used instead` (inkoop laatste jaar),
     `Reinvestment` (capex laatste jaar), `Net cash` (uit overview_metrics.glance).
   Ontbrekende regels in een bestaand boekjaar = 0 (zelfde regel als de Overview).

## Buiten scope
De fasekiezer ("In your opinion, which phase…"), LTM-kolom, 7Y-knop.

## Tests / uitrol
Pure modules met offline tests; tabvolgorde-asserts bijwerken; push naar `main`; Streamlit
Reboot. Geen MCP- of Supabase-wijziging.
