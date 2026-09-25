"""Moat Cards: five question cards per ticker, from the Moat Analysis.

The questions and their answer scales live here, not in the model's output,
so every ticker's cards are comparable and a typo in a prompt cannot rename a
scale. The model only picks an answer, a direction and writes the text.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import json
import re
from html import escape as _esc

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

_BANDS = ("red", "yellow", "green")
_ARROW = {"widening": "↗", "stable": "→", "narrowing": "↘"}
_BACK_BG = "#2b2b2f"
_BACK_TEXT = "#f2f0ea"

STYLE = """<style>
.mc-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:14px}
@media (max-width:760px){.mc-grid{grid-template-columns:1fr}}
.mc-card{display:block;perspective:1200px;cursor:pointer;height:270px;margin:0}
.mc-flip{display:none}
.mc-inner{position:relative;width:100%;height:100%;transition:transform .5s ease;
  transform-style:preserve-3d}
.mc-flip:checked + .mc-inner{transform:rotateY(180deg)}
.mc-face{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden;
  border-radius:16px;padding:16px 20px;box-sizing:border-box;overflow:hidden;
  display:flex;flex-direction:column}
.mc-back{transform:rotateY(180deg)}
.mc-q{font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase}
.mc-tag{font-size:.66rem;font-weight:700;letter-spacing:.05em;padding:2px 8px;
  border-radius:9px;text-transform:uppercase;white-space:nowrap}
.mc-foot{margin-top:auto;font-size:.8rem;font-weight:600;text-align:center;padding-top:8px}
</style>"""

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


def _source(key):
    return next(s for s in SOURCES if s[0] == key)


def flip_card_html(card, theme):
    from prescan_render import band_tone, three_state_html
    _key, _name, question, options = _source(card["source"])
    tone = band_tone(_BANDS[card["pick"]])
    arrow = _ARROW[card["direction"]]
    tag = (f'<span class="mc-tag" style="background:{tone}22;color:{tone}">'
           f'{_esc(card["direction"])} {arrow}</span>')
    front = (
        f'<div class="mc-face" style="background:{theme["bg_secondary"]};color:{theme["text"]}">'
        f'<div style="display:flex;justify-content:space-between;gap:8px">'
        f'<span class="mc-q" style="color:{theme["text_muted"]}">{_esc(question)}</span>{tag}</div>'
        f'<div style="margin:16px 0 12px">'
        f'{three_state_html(_BANDS[card["pick"]], labels=options, size=40, theme=theme)}</div>'
        f'<div style="border-top:1px solid {theme["divider"]};padding-top:10px;'
        f'font-size:.86rem;line-height:1.45;text-align:center">{_esc(card["summary"])}</div>'
        f'<div class="mc-foot" style="color:{theme["text"]}">Details →</div></div>')
    points = "".join(
        f'<div style="border-left:3px solid {tone};padding:1px 0 1px 10px;margin:0 0 9px;'
        f'font-size:.84rem;line-height:1.45"><b>{_esc(p["label"])}</b>: {_esc(p["text"])}</div>'
        for p in card["points"])
    back = (
        f'<div class="mc-face mc-back" style="background:{_BACK_BG};color:{_BACK_TEXT}">'
        f'<div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:12px">'
        f'<span class="mc-q" style="color:{tone}">{_esc(_name)}</span>'
        f'<span class="mc-tag" style="background:{tone}33;color:{tone}">'
        f'{_esc(options[card["pick"]])}</span></div>{points}'
        f'<div class="mc-foot" style="color:rgba(242,240,234,.6);text-align:left;'
        f'font-weight:500">Back to summary ↩</div></div>')
    return (f'<label class="mc-card"><input type="checkbox" class="mc-flip">'
            f'<div class="mc-inner">{front}{back}</div></label>')


def sources_row_html(cards, theme):
    from prescan_render import band_tone
    by_key = {c["source"]: c for c in cards}
    cells = []
    for key, name, *_ in SOURCES:
        c = by_key.get(key)
        tone = band_tone(_BANDS[c["pick"]]) if c else None
        dot = (f'background:{tone}' if c and c["pick"] > 0
               else f'border:1.5px solid {theme["text_muted"]}')
        cells.append(
            f'<div style="text-align:center;flex:1;min-width:90px">'
            f'<div style="width:12px;height:12px;border-radius:50%;margin:0 auto 6px;{dot}"></div>'
            f'<div class="mc-q" style="color:{theme["text_muted"]}">{_esc(name)}</div></div>')
    return (f'<div style="display:flex;flex-wrap:wrap;gap:8px;background:{theme["bg_secondary"]};'
            f'border-radius:14px;padding:14px 10px;margin-top:6px">{"".join(cells)}</div>')


def direction_card_html(moat_analysis, theme):
    """Direction from the Moat Analysis verdict line, with its weakest link."""
    from prescan_render import band_tone, parse_verdict_section
    v = parse_verdict_section(moat_analysis or "")
    if not v:
        return None
    word = next((q for q in v["qualifiers"]
                 if q.strip().lower() in ("widening", "stable", "narrowing")), "")
    if not word:
        return None
    tone = band_tone({"widening": "green", "stable": "yellow",
                      "narrowing": "red"}[word.strip().lower()])
    foot = ""
    if v["footer_text"]:
        foot = (f'<div style="margin-top:12px;font-size:.86rem;color:{theme["text_muted"]}">'
                f'<b style="color:{theme["text"]}">{_esc(v["footer_label"] or "Weakest link")}:</b> '
                f'{_esc(v["footer_text"])}</div>')
    return (f'<div style="background:{theme["bg_secondary"]};border-radius:16px;padding:20px 22px">'
            f'<div style="display:flex;gap:18px;align-items:center">'
            f'<div style="background:{_BACK_BG};border-radius:14px;padding:18px 20px;'
            f'text-align:center;min-width:132px">'
            f'<div style="font-size:2rem;color:{tone}">{_ARROW[word.strip().lower()]}</div>'
            f'<div style="color:rgba(255,255,255,.72);font-size:.7rem;font-weight:700;'
            f'letter-spacing:.08em">{_esc(word.upper())}</div></div>'
            f'<div style="font-size:.95rem;line-height:1.5;color:{theme["text"]}">'
            f'Is the moat getting stronger or weaker?</div></div>{foot}</div>')


def cards_section_html(content, theme):
    """Style + sources row + the five flip cards, or a one-line notice."""
    try:
        cards = parse_moat_cards(content)["cards"] if content else None
    except ValueError:
        cards = None
    if not cards:
        return (f'<div style="color:{theme["text_muted"]};font-size:.9rem;padding:14px 2px">'
                f'No Moat Cards yet. Ask Claude via the MCP to fill the "Moat Cards" '
                f'pre-scan section for this ticker.</div>')
    grid = "".join(flip_card_html(c, theme) for c in cards)
    return f'{STYLE}{sources_row_html(cards, theme)}<div class="mc-grid">{grid}</div>'
