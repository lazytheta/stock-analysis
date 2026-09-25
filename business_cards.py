"""Business Cards: four question cards per ticker on business quality, plus
an "overview" text block, a "profile" text block and a validated revenue
breakdown, all built on the Business Analysis, Moat Analysis and Key Metrics
prior sections.

The generic flip-card engine (escaping, CSS, parsing, rendering) lives in
question_cards.py, shared with Moat Cards and Risk Cards. This module only
defines Business's questions/prompt, the revenue-block validation and the
overview/quality section HTML.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import json
import re

import question_cards as qc

TITLE = "Business Cards"

ITEMS = (
    ("predictability", "Revenue predictability", "How predictable is revenue?",
     ("Unpredictable", "Modest", "Predictable")),
    ("pricing_power", "Pricing power", "Can the company raise prices?",
     ("No", "Sometimes", "Easily")),
    ("recession", "Demand resilience", "How recession-proof is it?",
     ("Weak", "Okay", "Strong")),
    ("competitive_position", "Competitive position", "What is their competitive position?",
     ("Weak", "Average", "Dominant")),
)
BUSINESS = qc.CardSet(TITLE, ITEMS, directions=None, blocks=(("overview", 4), ("profile", 4)))

# Fixed region vocabulary: the model maps a company's own geography names
# (UCAN, APAC, International, ...) onto these before the shares are checked.
# "Americas" sits after "Latin America" and before "Europe": it exists for
# companies that report a single combined North+South America region, while
# the more specific "US"/"Canada"/"North America"/"Latin America" still take
# precedence when a company lists those separately.
REGIONS = ("US", "Canada", "North America", "Latin America", "Americas", "Europe",
           "EMEA", "Middle East & Africa", "Asia Pacific", "China", "Japan", "India",
           "Rest of world")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

PROMPT = """You are turning the existing business work on **{company} ({ticker})** into
Business Cards: a business overview, a customer profile, a revenue breakdown
and four question cards. Base every call on the analyses below and on
reported numbers; do not contradict them.

{prior:Business Analysis}

{prior:Moat Analysis}

{prior:Key Metrics}

overview: ONE sentence on what the company sells and how it earns money, plus
EXACTLY four points, each {"label": two to five words, "text": one line}, on:
what it sells (products or plans), how it charges (the revenue model), and
what makes that model distinctive.

profile: ONE sentence on who the buyer is and what budget they pay from, plus
EXACTLY four points on customer types: who they are, where they are, and why
they buy.

revenue: from the latest 10-K's segment and geography note, in USD millions.
If you cannot read the segment/geography note from the filing, omit the
"revenue" key entirely. Never estimate numbers.
- period: the fiscal period, e.g. "FY2026"
- total_musd: total revenue
- growth_pct: year-over-year growth versus the prior year, or null if not disclosed
- segments: EXACTLY the company's reporting segments, each {"name",
  "revenue_musd", "growth_pct"} (growth_pct null if not disclosed); must sum
  to total_musd. If intersegment eliminations or an unallocated corporate
  amount keep the segments from summing to total_musd, either add a positive
  "Other / eliminations" segment for the gap, or net a negative elimination
  into the largest segment, so the segments sum to total_musd within 3%.
- regions: the company's own geographic breakdown, each mapped onto the
  closest one of these fixed regions (e.g. UCAN -> "North America",
  APAC -> "Asia Pacific", "International" -> "Rest of world"):
  US, Canada, North America, Latin America, Americas, Europe, EMEA,
  Middle East & Africa, Asia Pacific, China, Japan, India, Rest of world.
  Each entry is {"region": one of the list above, "label": the company's own
  name for it, "share_pct": percent of total revenue}; shares must sum to
  100%. If several of the company's own regions map onto the same fixed
  region, combine them into ONE entry instead of listing it twice: sum their
  shares and join their labels (e.g. "Germany, UK & Other Europe"). If the
  company reports no geography split, use regions: [].

For each of the four questions, in exactly this order, pick an answer
(0 = worst, 2 = best):
- predictability: How predictable is revenue? pick 0 = Unpredictable, 1 = Modest, 2 = Predictable
- pricing_power: Can the company raise prices? pick 0 = No, 1 = Sometimes, 2 = Easily
- recession: How recession-proof is it? pick 0 = Weak, 1 = Okay, 2 = Strong
- competitive_position: What is their competitive position? pick 0 = Weak, 1 = Average, 2 = Dominant

summary: ONE sentence for the front of the card, with the fact that decides the pick.
points: EXACTLY three, each {"label": two to four words, "text": one line with a number or
a fact from the filings}.

Output ONLY a fenced JSON block, nothing before or after:

```json
{"overview": {"summary": "...",
              "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
                         {"label": "...", "text": "..."}, {"label": "...", "text": "..."}]},
 "profile":  {"summary": "...",
              "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
                         {"label": "...", "text": "..."}, {"label": "...", "text": "..."}]},
 "revenue":  {"period": "FY2026", "total_musd": 3195.0, "growth_pct": 16.3,
              "segments": [{"name": "...", "revenue_musd": 1430.0, "growth_pct": 21.0}],
              "regions":  [{"region": "North America", "label": "North America",
                            "share_pct": 60.0}]},
 "cards": [
  {"source": "predictability", "pick": 2, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
 ]}
```
The "cards" array holds all four questions, in the order listed above. The
example above shows only one segment and one region entry each — the real
"segments" must sum to total_musd and the real "regions" shares must sum to
100.
"""


def _is_number(x):
    """int/float only: booleans are ints in Python, and strings must never
    silently coerce."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _load_json(content):
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)


def _validate_revenue(rev):
    if not isinstance(rev, dict):
        raise ValueError("revenue: must be an object")

    period = str(rev.get("period") or "").strip()
    if not period:
        raise ValueError("revenue: period is empty")

    total = rev.get("total_musd")
    if not _is_number(total) or total <= 0:
        raise ValueError("revenue: total_musd must be a number > 0")

    growth = rev.get("growth_pct")
    if growth is not None and not _is_number(growth):
        raise ValueError("revenue: growth_pct must be a number or null")

    segments = rev.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError("revenue: segments needs at least one entry")
    out_segments, seg_sum = [], 0.0
    for s in segments:
        if not isinstance(s, dict):
            raise ValueError("revenue: each segment must be an object")
        name = str(s.get("name") or "").strip()
        if not name:
            raise ValueError("revenue: segment name is empty")
        rev_musd = s.get("revenue_musd")
        if not _is_number(rev_musd) or rev_musd < 0:
            raise ValueError("revenue: segment revenue_musd must be a number >= 0")
        s_growth = s.get("growth_pct")
        if s_growth is not None and not _is_number(s_growth):
            raise ValueError("revenue: segment growth_pct must be a number or null")
        seg_sum += rev_musd
        out_segments.append({"name": name, "revenue_musd": rev_musd, "growth_pct": s_growth})
    if abs(seg_sum - total) > total * 0.03:
        raise ValueError("revenue: segments must sum to total_musd (within 3%)")

    regions = rev.get("regions")
    if not isinstance(regions, list):
        raise ValueError("revenue: regions must be a list")
    out_regions, seen, share_sum = [], set(), 0.0
    for r in regions:
        if not isinstance(r, dict):
            raise ValueError("revenue: each region must be an object")
        region = r.get("region")
        if region not in REGIONS:
            raise ValueError(f"revenue: region must be one of {', '.join(REGIONS)}")
        if region in seen:
            raise ValueError(f"revenue: duplicate region {region}")
        seen.add(region)
        share = r.get("share_pct")
        if not _is_number(share) or share < 0:
            raise ValueError("revenue: region share_pct must be a number >= 0")
        label = str(r.get("label") or region).strip() or region
        share_sum += share
        out_regions.append({"region": region, "label": label, "share_pct": share})
    if out_regions and not (95 <= share_sum <= 105):
        raise ValueError("revenue: region share_pct must sum to 95-105")

    return {"period": period, "total_musd": total, "growth_pct": growth,
            "segments": out_segments, "regions": out_regions}


def parse_business_cards(content):
    """The validated cards/blocks/revenue, or ValueError saying what is wrong."""
    result = qc.parse(BUSINESS, content)
    data = _load_json(content)
    revenue = data.get("revenue") if isinstance(data, dict) else None
    result["revenue"] = _validate_revenue(revenue) if revenue is not None else None
    return result


def _panel(title, block, fallback_lead, fallback_points):
    if block:
        return qc.text_panel_html(title, qc.bold(block["summary"]), block["points"])
    return qc.text_panel_html(title, fallback_lead, fallback_points)


def overview_section_html(business_analysis, content, theme):
    """"Business" section: overview + customer-profile text panels side by side.

    Overview falls back to the Business Analysis verdict (summary + bullets)
    when there is no Business Cards "overview" block; profile has no such
    fallback source and just says it isn't filled in yet.
    """
    try:
        parsed = parse_business_cards(content) if content else None
    except ValueError:
        parsed = None
    overview_block = parsed.get("overview") if parsed else None
    profile_block = parsed.get("profile") if parsed else None

    if overview_block:
        overview_panel = _panel("BUSINESS OVERVIEW", overview_block, "", [])
    else:
        from prescan_render import parse_verdict_section
        v = parse_verdict_section(business_analysis or "")
        if v:
            overview_panel = qc.text_panel_html(
                "BUSINESS OVERVIEW", qc.bold(v["summary"]), v["bullets"])
        else:
            overview_panel = qc.text_panel_html("BUSINESS OVERVIEW", "Not filled yet.", [])

    profile_panel = _panel("CUSTOMER PROFILE", profile_block, "Not filled yet.", [])

    return qc.section_html("Business", qc.text_row_html(overview_panel, profile_panel))


def quality_section_html(content, theme):
    """"Business quality" section: the four flip cards, or a one-line notice."""
    try:
        cards = parse_business_cards(content)["cards"] if content else None
    except ValueError:
        cards = None
    inner = qc.grid_html(BUSINESS, cards, theme) if cards else qc.notice_html(TITLE, theme)
    return qc.section_html("Business quality", inner)
