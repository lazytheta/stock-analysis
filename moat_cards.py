"""Moat Cards: five question cards per ticker, from the Moat Analysis.

The questions and their answer scales live here, not in the model's output,
so every ticker's cards are comparable and a typo in a prompt cannot rename a
scale. The model only picks an answer, a direction and writes the text.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import json
import re

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

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

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

Output ONLY a fenced JSON block, nothing before or after:

```json
{"cards": [
  {"source": "switching_costs", "pick": 1, "direction": "stable",
   "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
]}
```
The array holds all five sources, in the order listed above.
"""


def parse_moat_cards(content):
    """The validated cards, or ValueError saying what is wrong."""
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        raise ValueError('expected an object {"cards": [...]}')
    keys = [s[0] for s in SOURCES]
    got = [c.get("source") if isinstance(c, dict) else None for c in cards]
    if got != keys:
        raise ValueError("cards must be the five sources in order: " + ", ".join(keys))

    out = []
    for c in cards:
        src = c["source"]
        pick = c.get("pick")
        if isinstance(pick, bool) or pick not in (0, 1, 2):
            raise ValueError(f"{src}: pick must be 0, 1 or 2")
        direction = str(c.get("direction") or "").strip().lower()
        if direction not in DIRECTIONS:
            raise ValueError(f"{src}: direction must be one of {', '.join(DIRECTIONS)}")
        summary = str(c.get("summary") or "").strip()
        if not summary:
            raise ValueError(f"{src}: summary is empty")
        points = c.get("points")
        ok = isinstance(points, list) and len(points) == 3 and all(
            isinstance(p, dict) and str(p.get("label") or "").strip()
            and str(p.get("text") or "").strip() for p in points)
        if not ok:
            raise ValueError(f"{src}: needs exactly three points, each with label and text")
        out.append({"source": src, "pick": pick, "direction": direction,
                    "summary": summary,
                    "points": [{"label": str(p["label"]).strip(),
                                "text": str(p["text"]).strip()} for p in points]})
    return {"cards": out}
