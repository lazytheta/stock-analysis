"""Question Cards: the generic flip-card engine behind Moat Cards and (later)
Risk Cards.

Questions and their answer scales are defined in a `CardSet`, not in the
model's output, so every ticker's cards are comparable and a typo in a
prompt cannot rename a scale. The model only picks an answer, a direction
(when the card set has one) and writes the text.

No module-level import of prescan_render: mcp_server imports moat_cards
(which imports this module) for the parser, and the Cloud Run image does
not ship prescan_render.
"""

import json
import re
from dataclasses import dataclass
from html import escape as _html_escape

NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def esc(text):
    """HTML-escape, and turn $ into an entity: Streamlit's markdown reads a pair
    of dollar signs as LaTeX, so "$608M to $1.43B" became a formula."""
    return _html_escape(str(text)).replace("$", "&#36;")


def css(*blocks):
    """Style blocks on one line. A <style> block with newlines ends the HTML
    block early in Streamlit's markdown, and the cards after it fell apart."""
    return "".join(b.replace("\n", " ") for b in blocks)


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def bold(text):
    """Escape, then turn **x** back into <b>x</b>."""
    return _BOLD.sub(r"<b>\1</b>", esc(text or ""))


BANDS = ("red", "yellow", "green")
ARROW = {"widening": "↗", "stable": "→", "narrowing": "↘"}

STYLE = """<style>
.qc-section{background:var(--card);border-top:3px solid var(--accent);border-radius:24px;
  box-shadow:var(--shadow);padding:20px 24px 24px;margin:0 0 18px;
  --qc-inner:color-mix(in srgb, var(--text) 4%, var(--card))}
.qc-label{font-size:.72rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 14px}
.mc-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:16px}
@media (max-width:760px){.mc-grid{grid-template-columns:1fr}}
.mc-card{display:block;perspective:1200px;cursor:pointer;height:270px;margin:0}
.mc-flip{display:none}
.mc-inner{position:relative;width:100%;height:100%;transition:transform .5s ease;
  transform-style:preserve-3d}
.mc-flip:checked + .mc-inner{transform:rotateY(180deg)}
.mc-face{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden;
  background:var(--qc-inner, var(--bg-secondary));color:var(--text);border-radius:16px;
  padding:18px 22px;box-sizing:border-box;overflow:hidden;display:flex;flex-direction:column}
.mc-back{transform:rotateY(180deg);overflow:auto}
.mc-q{font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase}
.mc-tag{font-size:.66rem;font-weight:700;letter-spacing:.05em;padding:2px 8px;
  border-radius:9px;text-transform:uppercase;white-space:nowrap}
.mc-foot{margin-top:auto;font-size:.8rem;font-weight:600;text-align:center;padding-top:8px}
</style>"""

SUMMARY_STYLE = """<style>
.ms-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;
  align-items:stretch;margin-bottom:6px}
@media (max-width:760px){.ms-row{grid-template-columns:1fr}}
.ms-card{background:var(--qc-inner, var(--bg-secondary));color:var(--text);border-radius:16px;
  padding:18px 22px;display:flex;flex-direction:column;height:300px;box-sizing:border-box;
  overflow:hidden}
.ms-body{display:flex;gap:18px;align-items:flex-start;min-height:0;flex:1}
.ms-box{background:#2b2b2f;border-radius:14px;width:124px;height:124px;flex:none;
  display:flex;flex-direction:column;align-items:center;justify-content:center}
.ms-text{min-width:0;font-size:.86rem;line-height:1.45;overflow:hidden}
.ms-lead{margin:0 0 8px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;
  overflow:hidden}
.ms-pt{margin:0 0 6px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden}
.tp-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;
  align-items:stretch;margin-bottom:6px}
@media (max-width:760px){.tp-row{grid-template-columns:1fr}}
.tp-panel{background:var(--qc-inner, var(--bg-secondary));color:var(--text);border-radius:16px;
  padding:18px 22px;box-sizing:border-box}
.tp-title{font-size:.72rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 10px}
.tp-panel p{margin:0 0 10px;font-size:.86rem;line-height:1.5}
.tp-panel ul{margin:0;padding-left:1.1em}
.tp-panel li{font-size:.86rem;line-height:1.5;margin:0 0 6px}
</style>"""

_LABEL_STYLE = ('color:rgba(255,255,255,.72);font-size:.68rem;font-weight:700;'
               'letter-spacing:.08em;margin-top:2px')

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


@dataclass(frozen=True)
class CardSet:
    title: str
    items: tuple
    directions: tuple | None
    blocks: tuple = ()


def _item(cs, key):
    return next(i for i in cs.items if i[0] == key)


def _points(points, where, n=3):
    word = NUMBER_WORDS.get(n, str(n))
    ok = isinstance(points, list) and len(points) == n and all(
        isinstance(p, dict) and str(p.get("label") or "").strip()
        and str(p.get("text") or "").strip() for p in points)
    if not ok:
        raise ValueError(f"{where}: needs exactly {word} points, each with label and text")
    return [{"label": str(p["label"]).strip(), "text": str(p["text"]).strip()} for p in points]


def parse(cs, content):
    """The validated cards for card set `cs`, or ValueError saying what is wrong."""
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
    keys = [i[0] for i in cs.items]
    got = [c.get("source") if isinstance(c, dict) else None for c in cards]
    if got != keys:
        word = NUMBER_WORDS.get(len(keys), str(len(keys)))
        raise ValueError(f"cards must be the {word} sources in order: " + ", ".join(keys))

    out = []
    for c in cards:
        src = c["source"]
        pick = c.get("pick")
        if isinstance(pick, bool) or pick not in (0, 1, 2):
            raise ValueError(f"{src}: pick must be 0, 1 or 2")
        card = {"source": src, "pick": pick}
        if cs.directions is not None:
            direction = str(c.get("direction") or "").strip().lower()
            if direction not in cs.directions:
                raise ValueError(f"{src}: direction must be one of {', '.join(cs.directions)}")
            card["direction"] = direction
        summary = str(c.get("summary") or "").strip()
        if not summary:
            raise ValueError(f"{src}: summary is empty")
        card["summary"] = summary
        card["points"] = _points(c.get("points"), src)
        out.append(card)

    result = {"cards": out}
    # Named optional blocks (e.g. Moat's "trend"). Optional even when the card
    # set declares them, so cards saved before a block existed stay valid;
    # checked like a card when present. Blocks the card set doesn't declare
    # are ignored entirely.
    for name, n in cs.blocks:
        block = data.get(name)
        if block is not None:
            b_summary = str(block.get("summary") or "").strip() if isinstance(block, dict) else ""
            if not b_summary:
                raise ValueError(f"{name}: summary is empty")
            block = {"summary": b_summary, "points": _points(block.get("points"), name, n)}
        result[name] = block
    return result


def flip_card_html(cs, card, theme):
    from prescan_render import band_tone, three_state_html
    _key, name, question, options = _item(cs, card["source"])
    tone = band_tone(BANDS[card["pick"]])
    tag = ""
    if "direction" in card:
        direction_tone = band_tone({"widening": "green", "stable": "yellow",
                                    "narrowing": "red"}[card["direction"]])
        arrow = ARROW[card["direction"]]
        tag = (f'<span class="mc-tag" style="background:{direction_tone}22;color:{direction_tone}">'
               f'{esc(card["direction"])} {arrow}</span>')
    front = (
        f'<div class="mc-face">'
        f'<div style="display:flex;justify-content:space-between;gap:8px">'
        f'<span class="mc-q" style="color:{theme["text_muted"]}">{esc(question)}</span>{tag}</div>'
        f'<div style="margin:16px 0 12px">'
        f'{three_state_html(BANDS[card["pick"]], labels=options, size=40, theme=theme)}</div>'
        f'<div style="border-top:1px solid {theme["divider"]};padding-top:10px;'
        f'font-size:.86rem;line-height:1.45;text-align:center">{esc(card["summary"])}</div>'
        f'<div class="mc-foot" style="color:{theme["text"]}">Details →</div></div>')
    points = "".join(
        f'<div style="border-left:3px solid {tone};padding:1px 0 1px 10px;margin:0 0 9px;'
        f'font-size:.84rem;line-height:1.45"><b>{esc(p["label"])}</b>: {esc(p["text"])}</div>'
        for p in card["points"])
    back = (
        f'<div class="mc-face mc-back">'
        f'<div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:12px">'
        f'<span class="mc-q" style="color:{tone}">{esc(name)}</span>'
        f'<span class="mc-tag" style="background:{tone}33;color:{tone}">'
        f'{esc(options[card["pick"]])}</span></div>{points}'
        f'<div class="mc-foot" style="color:{theme["text_muted"]};text-align:left;'
        f'font-weight:500">Back to summary ↩</div></div>')
    return (f'<label class="mc-card"><input type="checkbox" class="mc-flip">'
            f'<div class="mc-inner">{front}{back}</div></label>')


def grid_html(cs, cards, theme):
    grid = "".join(flip_card_html(cs, c, theme) for c in cards)
    return f'{css(STYLE)}<div class="mc-grid">{grid}</div>'


def notice_html(title, theme):
    return (f'<div style="color:{theme["text_muted"]};font-size:.9rem;padding:14px 2px">'
            f'No {title} yet. Ask Claude via the MCP to fill the "{title}" '
            f'pre-scan section for this ticker.</div>')


def summary_card_html(title, box_html, lead_html, points, theme):
    pts = "".join(
        f'<p class="ms-pt">• <b>{esc(p["label"])}</b>: {esc(p["text"])}</p>' for p in points)
    return (f'<div class="ms-card">'
            f'<div class="mc-q" style="color:{theme["text_muted"]};margin-bottom:12px">{title}</div>'
            f'<div class="ms-body">{box_html}<div class="ms-text">'
            f'<p class="ms-lead">{lead_html}</p>{pts}</div></div></div>')


def dial_box_html(score, out_of, label, tone):
    if score is not None:
        from prescan_render import gauge_fraction
        r, c = 42, 50
        circ = 2 * 3.14159 * r
        fill = gauge_fraction(score, out_of) * circ * 0.75
        dial = (f'<svg viewBox="0 0 100 100" style="width:84px;height:84px">'
                f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="rgba(255,255,255,.16)" '
                f'stroke-width="8" stroke-dasharray="{circ * .75:.1f} {circ}" '
                f'stroke-linecap="round" transform="rotate(135 {c} {c})"/>'
                f'<circle cx="{c}" cy="{c}" r="{r}" fill="none" stroke="{tone}" '
                f'stroke-width="8" stroke-dasharray="{fill:.1f} {circ}" '
                f'stroke-linecap="round" transform="rotate(135 {c} {c})"/>'
                f'<text x="{c}" y="{c + 10}" text-anchor="middle" fill="#fff" font-size="28" '
                f'font-weight="700">{score:g}</text></svg>')
    else:
        dial = f'<div style="font-size:1.4rem;font-weight:700;color:{tone}">•</div>'
    return f'<div class="ms-box">{dial}<div style="{_LABEL_STYLE}">{esc(label.upper())}</div></div>'


def word_box_html(glyph, word, tone):
    return (f'<div class="ms-box"><div style="font-size:2.2rem;line-height:1;color:{tone}">'
            f'{glyph}</div><div style="{_LABEL_STYLE};margin-top:8px">'
            f'{esc(word.upper())}</div></div>')


def section_html(label, inner_html):
    """One white section in the site's card style with a small label, holding
    flat cards. Groups read as one block instead of a stack of floating boxes."""
    return (f'{css(STYLE, SUMMARY_STYLE)}<div class="qc-section">'
            f'<div class="qc-label">{esc(label)}</div>{inner_html}</div>')


def summary_row_html(left_html, right_html):
    return f'{css(STYLE, SUMMARY_STYLE)}<div class="ms-row">{left_html}{right_html}</div>'


_END_PUNCT = (".", ":", "!", "?")


def text_panel_html(title, lead_html, points):
    """A flat text panel: small-caps title, one lead paragraph, a bullet list
    of "label. text" items (no extra "." when the label already ends with
    punctuation, e.g. "Growing?" or "50/50:"). Not height-fixed;
    text_row_html stretches a pair of these to equal height."""
    items = []
    for p in points:
        raw_label = str(p["label"]).strip()
        suffix = "" if raw_label.endswith(_END_PUNCT) else "."
        items.append(f'<li><b>{esc(raw_label)}{suffix}</b> {esc(p["text"])}</li>')
    return (f'<div class="tp-panel"><div class="tp-title">{esc(title)}</div>'
            f'<p>{lead_html}</p><ul>{"".join(items)}</ul></div>')


def text_row_html(left_html, right_html):
    return f'{css(SUMMARY_STYLE)}<div class="tp-row">{left_html}{right_html}</div>'
