"""Moat Cards: five question cards per ticker, from the Moat Analysis.

The questions and their answer scales live here, not in the model's output,
so every ticker's cards are comparable and a typo in a prompt cannot rename a
scale. The model only picks an answer, a direction and writes the text.

The generic flip-card engine (escaping, CSS, parsing, rendering) lives in
question_cards.py, shared with other card sets (e.g. Risk Cards). This
module only defines Moat's questions/prompt and the Moat-specific bits
(sources_row_html has no equivalent in other card sets).

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import question_cards

TITLE = "Moat Cards"

# (key, source name, question, answers from weakest to strongest)
SOURCES = (
    ("switching_costs", "Switching costs", "How hard is it to switch?",
     ("Easy", "Moderate", "Hard")),
    ("network_effects", "Network effects", "Does scale help customers?",
     ("No", "Somewhat", "Yes")),
    ("intangible_assets", "Intangible assets", "Does the brand or IP earn a premium?",
     ("No", "Some", "Strong")),
    ("low_cost", "Low-cost production", "Is there a cost advantage?",
     ("No", "Some", "Large")),
    ("counter_positioning", "Counter-positioning", "Would copying hurt incumbents?",
     ("No", "Partly", "Yes")),
)
DIRECTIONS = ("widening", "stable", "narrowing")

MOAT = question_cards.CardSet(TITLE, SOURCES, DIRECTIONS, trend=True)

STYLE = question_cards.STYLE
SUMMARY_STYLE = question_cards.SUMMARY_STYLE

PROMPT = """You are turning an existing moat analysis of **{company} ({ticker})** into five
question cards. Base every call on the analysis below and on reported numbers;
do not contradict its verdict.

{prior:Moat Analysis}

For each of the five moat sources, in exactly this order, decide:
- switching_costs: How hard is it to switch? pick 0 = Easy, 1 = Moderate, 2 = Hard
- network_effects: Does scale help customers? pick 0 = No, 1 = Somewhat, 2 = Yes
- intangible_assets: Does the brand or IP earn a premium? pick 0 = No, 1 = Some, 2 = Strong
- low_cost: Is there a cost advantage? pick 0 = No, 1 = Some, 2 = Large
- counter_positioning: Would copying the model hurt incumbents? pick 0 = No, 1 = Partly, 2 = Yes
  (only when incumbents would harm themselves by copying; being different is not enough)

direction: "widening", "stable" or "narrowing" - is this source getting stronger or weaker.
summary: ONE sentence for the front of the card, with the fact that decides the pick.
points: EXACTLY three, each {"label": two to four words, "text": one line with a number or
a fact from the filings}. For an absent source, say what you looked for and why it is absent.

Then the moat as a whole, in "trend":
summary: ONE sentence on whether the moat is widening, stable or narrowing, and why
(agree with the direction in the analysis's verdict line).
points: EXACTLY three, same shape as above, each a measurable sign of that direction
(returns versus peers over time, costs versus sales, a new mechanism, a rival gaining).

Output ONLY a fenced JSON block, nothing before or after:

```json
{"cards": [
  {"source": "switching_costs", "pick": 1, "direction": "stable",
   "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
],
 "trend": {"summary": "...",
           "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
                      {"label": "...", "text": "..."}]}}
```
The "cards" array holds all five sources, in the order listed above.
"""


def parse_moat_cards(content):
    """The validated cards, or ValueError saying what is wrong."""
    return question_cards.parse(MOAT, content)


def flip_card_html(card, theme):
    return question_cards.flip_card_html(MOAT, card, theme)


def sources_row_html(cards, theme):
    from prescan_render import band_tone
    by_key = {c["source"]: c for c in cards}
    cells = []
    for key, name, *_ in SOURCES:
        c = by_key.get(key)
        tone = band_tone(question_cards.BANDS[c["pick"]]) if c else None
        dot = (f'background:{tone}' if c and c["pick"] > 0
               else f'border:1.5px solid {theme["text_muted"]}')
        cells.append(
            f'<div style="text-align:center;flex:1;min-width:90px">'
            f'<div style="width:12px;height:12px;border-radius:50%;margin:0 auto 6px;{dot}"></div>'
            f'<div class="mc-q" style="color:{theme["text_muted"]}">{question_cards.esc(name)}</div></div>')
    # Same card as the rest of the site: white, 24px, accent top, soft shadow.
    return ('<div style="display:flex;flex-wrap:wrap;gap:8px;background:var(--card);'
            'border-top:3px solid var(--accent);border-radius:24px;box-shadow:var(--shadow);'
            f'padding:16px 12px;margin-top:6px">{"".join(cells)}</div>')


def summary_row_html(moat_analysis, cards_content, theme):
    """Moat size and moat direction as two equal cards, or None without a verdict.

    Size comes from the Moat Analysis (verdict, sentence, three bullets). The
    direction card uses the Moat Cards "trend" block when there is one; before
    that it falls back to the verdict's direction and its weakest link.
    """
    from prescan_render import band_tone, parse_verdict_section
    v = parse_verdict_section(moat_analysis or "")
    if not v:
        return None
    size_tone = band_tone(v["label"]) or theme["text_muted"]
    size_box = question_cards.dial_box_html(v["score"], v["out_of"], v["label"], size_tone)
    size = question_cards.summary_card_html(
        "MOAT SIZE", size_box, question_cards.bold(v["summary"]), v["bullets"][:3], theme)

    word = next((q.strip().lower() for q in v["qualifiers"]
                 if q.strip().lower() in DIRECTIONS), "stable")
    dir_tone = band_tone({"widening": "green", "stable": "yellow", "narrowing": "red"}[word])
    dir_box = question_cards.word_box_html(question_cards.ARROW[word], word, dir_tone)
    try:
        trend = parse_moat_cards(cards_content)["trend"] if cards_content else None
    except ValueError:
        trend = None
    if trend:
        lead, points = question_cards.esc(trend["summary"]), trend["points"]
    else:
        lead = "Is the moat getting stronger or weaker?"
        points = ([{"label": v["footer_label"] or "Weakest link", "text": v["footer_text"]}]
                  if v["footer_text"] else [])
    direction = question_cards.summary_card_html("MOAT DIRECTION", dir_box, lead, points, theme)
    return question_cards.summary_row_html(size, direction)


def cards_section_html(content, theme):
    """Sources row + the five flip cards (with style), or a one-line notice."""
    try:
        cards = parse_moat_cards(content)["cards"] if content else None
    except ValueError:
        cards = None
    if not cards:
        return question_cards.notice_html(TITLE, theme)
    return f'{sources_row_html(cards, theme)}{question_cards.grid_html(MOAT, cards, theme)}'
