"""Company Explainer: what the company actually does, under five fixed headings,
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

import question_cards as qc

TITLE = "Company Explainer"

# (key, heading) in display order.
SECTIONS = (
    ("sell", "What they sell"),
    ("customers", "Who pays"),
    ("model", "How they make money"),
    ("drivers", "What drives revenue"),
    ("chain", "Where they sit"),
)

LEAD_MAX = 240
SECTION_MIN, SECTION_MAX = 150, 700
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

Write in plain English. Name real products, brands and customer types. When a
term of art is unavoidable, explain it in a short clause ("etch — carving
patterns into the silicon").

lead: ONE sentence, at most 240 characters, on what the company is at its
core — what it makes or does and how that turns into money.

sections: EXACTLY these five keys, each a short paragraph of 2 to 4 sentences
(150 to 700 characters):
- sell: what they sell — the concrete products and services by name, and what
  the customer uses them for.
- customers: who pays — which customers, out of which budget, and how
  concentrated (largest customers' share, main regions) where disclosed.
- model: how they make money — the revenue model (one-off sale, subscription,
  usage, commission, spread, premiums), the pricing unit, and which segment
  brings in how much of revenue and operating profit, with the fiscal year.
- drivers: what drives revenue — the two or three levers that move it (volume
  times price, e.g. units shipped, members, take rate) and the KPIs the
  company itself reports.
- chain: where they sit — whom they buy from, whom they sell to, and who they
  compete with at that step of the chain.

source: the filing you relied on, e.g. "10-K FY2026, filed 2026-08-07".

Output ONLY a fenced JSON block, nothing before or after:

```json
{"lead": "…",
 "sections": {"sell": "…", "customers": "…", "model": "…",
              "drivers": "…", "chain": "…"},
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
    known = {key for key, _ in SECTIONS}
    unknown = sorted(set(sections) - known)
    if unknown:
        raise ValueError(f"unknown section(s): {', '.join(unknown)}")
    out_sections = {}
    for key, _ in SECTIONS:
        text = _text(sections.get(key), f"sections.{key}")
        if not (SECTION_MIN <= len(text) <= SECTION_MAX):
            raise ValueError(f"sections.{key} must be {SECTION_MIN}-{SECTION_MAX} "
                             f"characters (has {len(text)})")
        out_sections[key] = text

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
.ce-panel.ce-wide{{grid-column:1 / -1}}
.ce-head{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 6px}}
.ce-panel p{{margin:0;font-size:14px;color:var(--text);line-height:1.6}}
.ce-source{{margin-top:12px;font-size:12px;color:var(--text-muted)}}
.ce-empty{{font-size:13px;color:var(--text-muted);line-height:1.45;margin-top:8px}}
</style>"""

_LABEL = "What the company does"
_EMPTY_NOTE = ('Company Explainer not filled yet. Ask Claude via the MCP to fill the '
               '"Company Explainer" pre-scan section for this ticker.')


def _panel(heading, text, wide=False):
    cls = "ce-panel ce-wide" if wide else "ce-panel"
    return (f'<div class="{cls}"><div class="ce-head">{qc.esc(heading)}</div>'
            f'<p>{qc.esc(text)}</p></div>')


def explainer_section_html(content, mission="", theme=None):
    """The white "What the company does" section. With a valid explainer: the
    lead, five panels (the last full width) and the source. Without one (or an
    invalid one): the Company Profile mission, if any, and a muted note."""
    try:
        data = parse_company_explainer(content) if content else None
    except ValueError:
        data = None

    if not data:
        lead = f'<p class="ce-lead">{qc.esc(mission)}</p>' if mission else ""
        inner = f'{lead}<div class="ce-empty">{qc.esc(_EMPTY_NOTE)}</div>'
        return qc.css(STYLE) + qc.section_html(_LABEL, inner)

    last = len(SECTIONS) - 1
    panels = "".join(_panel(heading, data["sections"][key], wide=(i == last))
                     for i, (key, heading) in enumerate(SECTIONS))
    inner = (f'<p class="ce-lead">{qc.esc(data["lead"])}</p>'
             f'<div class="ce-grid">{panels}</div>'
             f'<div class="ce-source">Source: {qc.esc(data["source"])}</div>')
    return qc.css(STYLE) + qc.section_html(_LABEL, inner)
