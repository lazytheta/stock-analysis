"""Risk Cards: four question cards per ticker, built on the Risk Analysis and
the SaaSpocalypse Resistance sections. Answers run from riskiest (0, red) to
safest (2, green). No module-level import of prescan_render."""

import question_cards as qc

TITLE = "Risk Cards"
ITEMS = (
    ("concentration", "Concentration", "How diversified are revenues?",
     ("Concentrated", "Moderate", "Diversified")),
    ("disruption", "Disruption", "Is disruption a threat?", ("Yes", "Some risk", "No")),
    ("outside_forces", "Outside forces", "How much is outside their control?",
     ("A lot", "Some", "Very little")),
    ("financial_health", "Financial health", "How healthy are the financials?",
     ("Weak", "Mixed", "Strong")),
)
RISK = qc.CardSet(TITLE, ITEMS, directions=None, trend=False)

PROMPT = """You are turning the existing risk work on **{company} ({ticker})** into four
question cards. Base every call on the analyses below and on reported numbers;
do not contradict them.

{prior:Risk Analysis}

{prior:SaaSpocalypse Resistance}

For each question, in exactly this order, pick an answer (0 = riskiest, 2 = safest):
- concentration: How diversified are revenues? pick 0 = Concentrated, 1 = Moderate, 2 = Diversified
  (customers, products and geographies; a customer at 10%+ of revenue counts against)
- disruption: Is disruption a threat? pick 0 = Yes, 1 = Some risk, 2 = No
  (technology, AI, new business models that could make the product obsolete)
- outside_forces: How much is outside their control? pick 0 = A lot, 1 = Some, 2 = Very little
  (regulation, government pricing, currencies, commodity inputs, rates and credit)
- financial_health: How healthy are the financials? pick 0 = Weak, 1 = Mixed, 2 = Strong
  (interest cover, debt versus cash flow, liquidity)

summary: ONE sentence for the front of the card, with the fact that decides the pick.
points: EXACTLY three, each {"label": two to four words, "text": one line with a number or
a fact from the filings}.

Output ONLY a fenced JSON block, nothing before or after:

```json
{"cards": [
  {"source": "concentration", "pick": 2, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
]}
```
The array holds all four questions, in the order listed above.
"""


def parse_risk_cards(content):
    """The validated cards, or ValueError saying what is wrong."""
    return qc.parse(RISK, content)


def cards_section_html(content, theme):
    try:
        cards = parse_risk_cards(content)["cards"] if content else None
    except ValueError:
        cards = None
    inner = qc.grid_html(RISK, cards, theme) if cards else qc.notice_html(TITLE, theme)
    return qc.section_html("Risk questions", inner)


def _verdict_card(title, v, band_tone, theme):
    """A summary card for one verdict-shaped section, or None when there isn't one."""
    if v is None:
        return None
    tone = band_tone(v["label"]) or theme["text_muted"]
    if v["score"] is not None:
        box = qc.dial_box_html(v["score"], v["out_of"], v["label"], tone)
    else:
        box = qc.word_box_html("●", v["label"], tone)
    return qc.summary_card_html(title, box, qc.bold(v["summary"]), v["bullets"][:3], theme)


def _missing_card(title, theme):
    return qc.summary_card_html(
        title, qc.word_box_html("–", "—", theme["text_muted"]),
        "Not in the verdict format yet.", [], theme)


def summary_row_html(risk_analysis, saas_text, theme):
    """EXECUTION RISK (Risk Analysis) and AI EXPOSURE (SaaSpocalypse Resistance)
    as two equal cards; None when neither is in verdict form."""
    from prescan_render import band_tone, parse_verdict_section
    left = _verdict_card("EXECUTION RISK", parse_verdict_section(risk_analysis or ""),
                          band_tone, theme)
    right = _verdict_card("AI EXPOSURE", parse_verdict_section(saas_text or ""),
                           band_tone, theme)
    if left is None and right is None:
        return None
    row = qc.summary_row_html(left or _missing_card("EXECUTION RISK", theme),
                              right or _missing_card("AI EXPOSURE", theme))
    return qc.section_html("Risk", row)
