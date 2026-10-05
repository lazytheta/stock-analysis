"""Company Explainer: what the company actually does, under four fixed headings,
for the Overview tab's "What the company does" section.

Built on the Business Analysis, Business Cards and Key Metrics prior sections
plus the latest 10-K's Business chapter. Validated like the card sections so a
malformed block is refused before it can break the Overview tab's rendering.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import html
import json
import re

import prompt_style
import question_cards as qc

TITLE = "Company Explainer"

# (key, heading) in display order. "offer" replaced the separate "sell" and
# "model" sections on 2026-10-05: for platform companies the product is the
# revenue model, so the two said the same thing (owner).
SECTIONS = (
    ("offer", "What they sell & how they earn"),
    ("customers", "Who their customers are"),
    ("drivers", "What drives revenue"),
    ("chain", "Where they sit"),
)
# Explainers saved before 2026-10-05 carry these two instead of "offer"; the
# parser joins them so they keep rendering until they are re-run.
LEGACY_OFFER = ("sell", "model")

LEAD_MAX = 400
SECTION_MIN, SECTION_MAX = 150, 700
OFFER_MAX = 900
SOURCE_MAX = 120

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

PROMPT = """You are writing the Company Explainer for **{company} ({ticker})**: the part
of the Overview tab that tells someone who has never heard of the company what
it actually does. After reading it they must know what it sells, who pays for
it, how it earns money, what moves its revenue and where it sits in its
industry.

Base it on the analyses below and on the latest 10-K's Item 1 "Business" and
its segment note (read them with the SEC connector if you have it). Every
number must come from a filing or from the analyses below; never estimate. If
the company does not disclose something, say so in plain words.

{prior:Business Analysis}

{prior:Business Cards}

{prior:Key Metrics}

""" + prompt_style.HOW_TO_WRITE + """This matters more than completeness. Write as if explaining the company to a
friend; an everyday comparison is welcome ("like the printing press of a chip
factory"). Never list product names.

Bad (a catalogue, unreadable for an outsider):
"Lam sells wafer-fab tools: etch systems (Kiyo, Flex, Vantex, Akara),
deposition systems (ALTUS, SABRE, VECTOR, Striker) and clean tools (EOS,
DV-Prime)."
Good (says what it does):
"Lam makes the machines chip factories use to build chips layer by layer.
Some lay down ultra-thin layers of material, others cut microscopic patterns
into those layers, and others clean the wafer between steps."

lead: TWO or THREE short sentences, at most 400 characters, shown at the top
of the Overview tab: what the company makes or does, how that turns into
money, and who buys it. Plain words only, no product names; someone who has
never heard of the company must understand it.

sections: EXACTLY these four keys, each a short paragraph. Every fact
belongs in one section only — never repeat the revenue model outside "offer".
- offer (3 to 5 sentences, 150 to 900 characters): what they sell and how
  they earn — what the products and services do for the customer in everyday
  words, then the revenue model (one-off sale, subscription, usage,
  commission, spread, premiums) and the pricing unit, and which segment
  brings in how much of revenue and operating profit, with the fiscal year.
- customers (2 to 4 sentences, 150 to 700 characters): who the customers
  are — which customers, out of which budget, and how concentrated (largest
  customers' share, main regions) where disclosed.
- drivers (2 to 4 sentences, 150 to 700 characters): what drives revenue —
  the two or three levers that move it (e.g. units shipped, members, price
  per ad, take rate) and the latest figures the company itself reports for
  them. Do not explain the revenue model again.
- chain (2 to 4 sentences, 150 to 700 characters): where they sit — whom
  they buy from, whom they sell to, and who they compete with at that step
  of the chain.

source: the filing you relied on, e.g. "10-K FY2026, filed 2026-08-07".

Output ONLY a fenced JSON block, nothing before or after:

```json
{"lead": "…",
 "sections": {"offer": "…", "customers": "…", "drivers": "…",
              "chain": "…"},
 "source": "10-K FY2026, filed 2026-08-07"}
```
"""


def _text(value, name):
    """A stripped string with HTML entities decoded (a model sometimes writes
    "G&amp;A", which qc.esc would escape again); None/empty passes through
    as "", non-strings raise."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return html.unescape(value).strip()


def _load_json(content):
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None


def parse_company_explainer(content):
    """{"lead", "sections" (ordered like SECTIONS), "source"}, or ValueError
    saying what is wrong."""
    data = _load_json(content)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")

    lead = _text(data.get("lead"), "lead")
    if not lead or len(lead) > LEAD_MAX:
        raise ValueError(f"lead must be 1-{LEAD_MAX} characters")

    sections = data.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("sections must be an object")
    legacy = "offer" not in sections and any(k in sections for k in LEGACY_OFFER)
    known = {key for key, _ in SECTIONS} - ({"offer"} if legacy else set())
    if legacy:
        known |= set(LEGACY_OFFER)
    unknown = sorted(set(sections) - known)
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(unknown)}")

    def _section(key, max_len):
        text = _text(sections.get(key), f"sections.{key}")
        if not (SECTION_MIN <= len(text) <= max_len):
            raise ValueError(f"sections.{key} must be {SECTION_MIN}-{max_len} "
                             f"characters (has {len(text)})")
        return text

    out_sections = {}
    for key, _ in SECTIONS:
        if key == "offer" and legacy:
            out_sections[key] = " ".join(_section(k, SECTION_MAX) for k in LEGACY_OFFER)
        else:
            out_sections[key] = _section(key, OFFER_MAX if key == "offer" else SECTION_MAX)

    source = _text(data.get("source"), "source")
    if not source or len(source) > SOURCE_MAX:
        raise ValueError(f"source must be 1-{SOURCE_MAX} characters")

    return {"lead": lead, "sections": out_sections, "source": source}


_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"

STYLE = f"""<style>
.ce-lead{{margin:0 0 16px;font-size:17px;font-weight:600;color:var(--text);line-height:1.5}}
.ce-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}
@media (max-width:800px){{.ce-grid{{grid-template-columns:minmax(0,1fr)}}}}
.ce-panel{{background:{_INNER};border-radius:16px;padding:16px 18px;min-width:0}}
.ce-head{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 6px}}
.ce-panel p{{margin:0;font-size:14px;color:var(--text);line-height:1.6}}
.ce-source{{margin-top:12px;font-size:12px;color:var(--text-muted)}}
.ce-empty{{font-size:13px;color:var(--text-muted);line-height:1.45;margin-top:8px}}
</style>"""

_LABEL = "What the company does"
_EMPTY_NOTE = ('Company Explainer not filled yet. Ask Claude via the MCP to fill the '
               '"Company Explainer" pre-scan section for this ticker.')


def _panel(heading, text):
    return (f'<div class="ce-panel"><div class="ce-head">{qc.esc(heading)}</div>'
            f'<p>{qc.esc(text)}</p></div>')


def lead_text(content):
    """The explainer's lead (2-3 sentences) for the Overview's Profile card,
    or None when there is no valid explainer."""
    try:
        return parse_company_explainer(content)["lead"] if content else None
    except ValueError:
        return None


def explainer_section_html(content, theme=None):
    """The white "What the company does" section: four panels in a 2x2 grid
    and the source. The lead is not repeated here; the Overview shows
    it in the Profile card. Without a valid explainer: a muted note."""
    try:
        data = parse_company_explainer(content) if content else None
    except ValueError:
        data = None

    if not data:
        inner = f'<div class="ce-empty">{qc.esc(_EMPTY_NOTE)}</div>'
        return qc.css(STYLE) + qc.section_html(_LABEL, inner)

    panels = "".join(_panel(heading, data["sections"][key]) for key, heading in SECTIONS)
    inner = (f'<div class="ce-grid">{panels}</div>'
             f'<div class="ce-source">Source: {qc.esc(data["source"])}</div>')
    return qc.css(STYLE) + qc.section_html(_LABEL, inner)
