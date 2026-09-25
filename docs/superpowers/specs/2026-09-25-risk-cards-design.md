# Risk-tab met vraagkaarten + gedeelde kaartenmotor

Datum: 2026-09-25 · Status: goedgekeurd in chat ("akkoord")

Vervolg op `2026-09-25-moat-cards-design.md`. Valuation-tab valt af (gebruiker).

## 1. Gedeelde motor `question_cards.py`

Uit `moat_cards.py` gaat alles wat niet Moat-specifiek is naar `question_cards.py`:
escape (incl. `$` → `&#36;`), CSS-op-één-regel, STYLE/SUMMARY_STYLE, `**bold**`,
een `CardSet` (titel, items `(key, name, question, options)`, toegestane richtingen of
`None`, trend-blok ja/nee), `parse(cardset, content)`, `flip_card_html(cardset, card, theme)`,
`grid_html(cardset, cards, theme)`, `notice_html(title, theme)`, `summary_card_html(...)`,
`dial_box_html(...)`, `word_box_html(...)`.
`moat_cards.py` houdt zijn publieke API (TITLE, SOURCES, DIRECTIONS, PROMPT,
parse_moat_cards, flip_card_html, sources_row_html, summary_row_html,
cards_section_html) en gedraagt zich identiek; alle bestaande Moat-tests blijven groen.
Geen module-level import van `prescan_render` (Cloud Run).

## 2. Risk Cards

- Sectie `Risk Cards`, prompt met `{prior:Risk Analysis}` en `{prior:SaaSpocalypse Resistance}`.
- Vier items, antwoorden van riskant (0, rood) naar veilig (2, groen):

| key | vraag | opties |
|---|---|---|
| concentration | How diversified are revenues? | Concentrated / Moderate / Diversified |
| disruption | Is disruption a threat? | Yes / Some risk / No |
| outside_forces | How much is outside their control? | A lot / Some / Very little |
| financial_health | How healthy are the financials? | Weak / Mixed / Strong |

- Geen richting per kaart, geen trend-blok. JSON: `{"cards": [{source, pick, summary, points×3}×4]}`.
- Validatie bij opslaan in de MCP (zoals Moat Cards).

## 3. Risk-tab

- Tabs: Pre-Scan · Moat · **Risk** · Fundamentals · DCF · …
- Bovenaan twee even hoge kaarten: **EXECUTION RISK** (uit Risk Analysis-verdict:
  Low/Medium/High, zonder score → woordvak in de band-kleur; zin + drie punten) en
  **AI EXPOSURE** (uit SaaSpocalypse Resistance-verdict, meter x/4; zin + drie punten).
  Ontbreekt een van beide in verdict-vorm: die kaart toont "Not in the verdict format yet.";
  ontbreken beide: caption.
- Daaronder de vier flip-kaarten (zelfde mechanisme als Moat, zonder richtinglabel), of de
  notice als "Risk Cards" ontbreekt.
- Pre-Scan-tab tekent "Risk Cards" als kaarten (zoals Moat Cards).

## 4. Vullen

- Prompt in `DEFAULT_AI_PROMPTS` direct na "Risk Analysis"; in de bibliotheek van de
  gebruiker gezet door de controller (SQL, zoals bij Moat Cards).
- Backfill-routine-doc: Moat Cards eerst, dan Risk Cards (`requires="Risk Analysis"`),
  samen maximaal 10 namen per run.
- Aspirant-routine neemt Risk Cards vanzelf mee (prompts uit de bibliotheek, re-fetch na
  secties waar latere prompts naar verwijzen — Risk Cards verwijst naar Risk Analysis en
  SaaSpocalypse Resistance, dus opnieuw ophalen na die twee).

## Testen

Bestaande Moat-tests ongewijzigd groen; parser/rendering Risk; flip zonder richtinglabel;
summary row (beide, één, geen verdict); MCP-validatie Risk Cards; tabvolgorde; Dockerfile-test
(dekt nieuwe modules); `$`/newline-veiligheid voor Risk-HTML; handmatig: proefpagina.
