"""Growth Cards: a scored growth analysis, an optional analyst-consensus
block and two question cards per ticker, built on the Long-Term Potential,
Business Analysis and Key Metrics prior sections.

The generic flip-card engine (escaping, CSS, parsing, rendering) lives in
question_cards.py, shared with Moat/Risk/Business Cards. This module only
defines Growth's questions/prompt and the validation for its two extra
blocks -- "analysis" (mandatory: a 1-5 score) and "consensus" (optional: the
next-fiscal-year analyst numbers), neither of which fits question_cards'
generic summary+points block shape.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import json
import re

import prompt_style
import question_cards as qc

TITLE = "Growth Cards"

ITEMS = (
    ("industry", "Industry Growth", "Is the industry growing?",
     ("No", "Slowly", "Yes")),
    ("optionality", "Optionality", "Can new offerings drive growth?",
     ("Unlikely", "Possible", "Likely")),
)
GROWTH = qc.CardSet(TITLE, ITEMS, directions=None, blocks=())

SCORE_LABELS = {1: "Weak", 2: "Below average", 3: "Average", 4: "Strong", 5: "Exceptional"}

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

PROMPT = """You are turning the existing growth work on **{company} ({ticker})** into
Growth Cards: a scored growth analysis, an analyst-consensus block (when a
filings-grade source has one) and two question cards. Base every call on the
analyses below and on reported numbers; do not contradict them.

{prior:Long-Term Potential}

{prior:Business Analysis}

{prior:Key Metrics}

analysis: score the company's long-term growth potential from 1 (Weak) to 5
(Exceptional), ONE sentence summary with the fact that decides the score,
and EXACTLY three points, each {"label": two to four words, "text": one
line with the plainest evidence, no product names or acronyms}.

consensus: the next-fiscal-year revenue and EPS growth consensus, from a
filings-grade source only -- call the SEC connector's `GetAnalystEstimates`
for {ticker}. It returns LEVEL estimates (revenue and EPS, each with a
mean/median, an analyst count and an accounting basis), not growth rates, so
you must derive the growth figures yourself -- computing this ratio is
required; only inventing or estimating the consensus numbers themselves is
forbidden:
- fiscal_year: the label of the FIRST fiscal year not yet reported
  (e.g. "FY2027").
- revenue_growth_pct / eps_growth_pct: (consensus mean for that year /
  the last REPORTED fiscal-year actual - 1) x 100, one decimal. Use the
  actual on the same accounting basis as the estimate; if the EPS estimate
  is adjusted/non-GAAP and no same-basis actual is available, set
  eps_growth_pct to null.
- Use null for a growth figure when the change is outside −100% to 1000% or the prior-year value is ≤ 0.
- analysts: the estimate's analyst count.
- source: name the basis and the source plus the date you pulled it
  (e.g. "compiled consensus, adjusted EPS, Equibles 2026-09-24").
- year2: the SECOND fiscal year not yet reported, from the same call:
  {"fiscal_year": e.g. "FY2028", "revenue_growth_pct", "eps_growth_pct"},
  each growth = (consensus mean for that year / consensus mean for the first
  unreported year - 1) x 100, one decimal, same basis rules and null rules
  as above. Omit "year2" when the call has no estimate for that year.
If `GetAnalystEstimates` is unavailable, or returns nothing usable for the
first unreported fiscal year, OMIT the "consensus" key entirely -- do not
send it as null, and never estimate the consensus revenue or EPS by hand.

guidance: what the company itself said it expects, from the SEC connector's
`GetGuidance` for {ticker} or else the latest earnings release (8-K exhibit
99.1): {"items": up to four, each {"metric": e.g. "Revenue", "Total
expenses", "Capex" (at most 30 characters), "period": e.g. "Q3 2026" or
"FY2026" (at most 20), "value": the guided figure as stated, e.g.
"$61B-$64B" or "+8% to +10%" (at most 30)}, "source": the document and its
date}. Only current guidance (not for a period already reported), only
figures the company gave; omit the "guidance" key entirely when it gives
none.

For each of the two questions, in exactly this order, pick an answer
(0 = worst, 2 = best):
- industry: Is the industry growing? pick 0 = No, 1 = Slowly, 2 = Yes
- optionality: Can new offerings drive growth? pick 0 = Unlikely, 1 = Possible, 2 = Likely

summary: ONE sentence for the front of the card, with the fact that decides the pick.
points: EXACTLY three, each {"label": two to four words, "text": one line with the
plainest evidence, no product names or acronyms}.

""" + prompt_style.HOW_TO_WRITE + """
Output ONLY a fenced JSON block, nothing before or after:

```json
{"analysis":  {"score": 3, "summary": "...",
               "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
                          {"label": "...", "text": "..."}]},
 "consensus": {"fiscal_year": "FY2027", "revenue_growth_pct": 15.2, "eps_growth_pct": 12.0,
               "analysts": 23,
               "source": "compiled consensus, adjusted EPS, Equibles 2026-09-24",
               "year2": {"fiscal_year": "FY2028", "revenue_growth_pct": 12.1,
                         "eps_growth_pct": 14.0}},
 "guidance": {"items": [{"metric": "Revenue", "period": "Q3 2026", "value": "$47.5B-$50.5B"}],
              "source": "Q2 2026 earnings release, 2026-07-29"},
 "cards": [
  {"source": "industry", "pick": 2, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]},
  {"source": "optionality", "pick": 1, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
 ]}
```
The "cards" array holds both questions, in the order listed above. Omit the
"consensus" key entirely -- never send it as null -- when no filings-grade
consensus is available.
"""


def _is_number(x):
    """int/float only: booleans are ints in Python, and strings must never
    silently coerce."""
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_int(x):
    """int only: booleans are ints in Python and must never pass as a score
    or an analyst count, and a numeric string must never silently coerce."""
    return isinstance(x, int) and not isinstance(x, bool)


def _load_json(content):
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None


def _points3(points, where):
    ok = (isinstance(points, list) and len(points) == 3 and all(
        isinstance(p, dict) and str(p.get("label") or "").strip()
        and str(p.get("text") or "").strip() for p in points))
    if not ok:
        raise ValueError(f"{where}: needs exactly three points, each with label and text")
    return [{"label": str(p["label"]).strip(), "text": str(p["text"]).strip()} for p in points]


def _validate_analysis(analysis):
    if not isinstance(analysis, dict):
        raise ValueError("analysis: must be an object")

    score = analysis.get("score")
    if not _is_int(score) or not (1 <= score <= 5):
        raise ValueError("analysis: score must be an integer 1-5")

    summary = str(analysis.get("summary") or "").strip()
    if not summary:
        raise ValueError("analysis: summary is empty")

    points = _points3(analysis.get("points"), "analysis")
    return {"score": score, "summary": summary, "points": points}


def _validate_consensus(consensus):
    if not isinstance(consensus, dict):
        raise ValueError("consensus: must be an object or null")

    fiscal_year = str(consensus.get("fiscal_year") or "").strip()
    if not fiscal_year:
        raise ValueError("consensus: fiscal_year is empty")

    revenue_growth_pct = consensus.get("revenue_growth_pct")
    eps_growth_pct = consensus.get("eps_growth_pct")
    for name, value in (("revenue_growth_pct", revenue_growth_pct),
                        ("eps_growth_pct", eps_growth_pct)):
        if value is not None and (not _is_number(value) or not (-100 <= value <= 1000)):
            raise ValueError(f"consensus: {name} must be a number between -100 and 1000, or null")
    if revenue_growth_pct is None and eps_growth_pct is None:
        raise ValueError(
            "consensus: at least one of revenue_growth_pct or eps_growth_pct must be present")

    analysts = consensus.get("analysts")
    if analysts is not None and (not _is_int(analysts) or analysts <= 0):
        raise ValueError("consensus: analysts must be a positive integer, or null")

    source = str(consensus.get("source") or "").strip()
    if not source:
        raise ValueError("consensus: source is empty")

    out = {"fiscal_year": fiscal_year, "revenue_growth_pct": revenue_growth_pct,
           "eps_growth_pct": eps_growth_pct, "analysts": analysts, "source": source,
           "year2": None}
    year2 = consensus.get("year2")
    if year2 is not None:
        # Optional (added 2026-10-08): the second unreported year, growth
        # against the first year's consensus.
        if not isinstance(year2, dict):
            raise ValueError("consensus.year2: must be an object")
        fy2 = str(year2.get("fiscal_year") or "").strip()
        if not fy2:
            raise ValueError("consensus.year2: fiscal_year is empty")
        r2, e2 = year2.get("revenue_growth_pct"), year2.get("eps_growth_pct")
        for name, value in (("revenue_growth_pct", r2), ("eps_growth_pct", e2)):
            if value is not None and (not _is_number(value) or not (-100 <= value <= 1000)):
                raise ValueError(f"consensus.year2: {name} must be a number between -100 and "
                                 "1000, or null")
        if r2 is None and e2 is None:
            raise ValueError("consensus.year2: at least one growth figure must be present")
        out["year2"] = {"fiscal_year": fy2, "revenue_growth_pct": r2, "eps_growth_pct": e2}
    return out


GUIDANCE_TEXT_MAX = 140


GUIDANCE_MAX_ITEMS = 4


def _validate_guidance(guidance):
    """The company's own outlook (optional, added 2026-10-08) as rows:
    {"items": [{"metric", "period", "value"}], "source"}. The first form,
    {"period", "text", "source"}, is still read and becomes one row."""
    if not isinstance(guidance, dict):
        raise ValueError("guidance: must be an object")

    def _s(value, name, limit):
        value = str(value or "").strip()
        if not value or len(value) > limit:
            raise ValueError(f"guidance: {name} must be 1-{limit} characters")
        return value

    source = _s(guidance.get("source"), "source", 120)
    if "items" not in guidance and "text" in guidance:
        return {"items": [{"metric": "Guidance",
                           "period": _s(guidance.get("period"), "period", 40),
                           "value": _s(guidance.get("text"), "text", GUIDANCE_TEXT_MAX)}],
                "source": source}
    items = guidance.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= GUIDANCE_MAX_ITEMS:
        raise ValueError(f"guidance: items needs 1-{GUIDANCE_MAX_ITEMS} entries")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise ValueError(f"guidance: items[{i}] must be an object")
        out.append({"metric": _s(it.get("metric"), f"items[{i}].metric", 30),
                    "period": _s(it.get("period"), f"items[{i}].period", 20),
                    "value": _s(it.get("value"), f"items[{i}].value", 30)})
    return {"items": out, "source": source}


def parse_growth_cards(content):
    """The validated {"analysis", "consensus", "guidance", "cards"}, or
    ValueError saying what is wrong. "consensus" and "guidance" are None when
    absent; consensus["year2"] is None when absent."""
    result = qc.parse(GROWTH, content)
    data = _load_json(content)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")

    result["analysis"] = _validate_analysis(data.get("analysis"))

    consensus = data.get("consensus")
    result["consensus"] = _validate_consensus(consensus) if consensus is not None else None
    guidance = data.get("guidance")
    result["guidance"] = _validate_guidance(guidance) if guidance is not None else None
    return result


def _analysis_html(analysis):
    label = SCORE_LABELS.get(analysis["score"], str(analysis["score"]))
    items = "".join(
        f'<li><b>{qc.esc(p["label"])}</b>: {qc.esc(p["text"])}</li>' for p in analysis["points"])
    return (f'<div class="tp-panel"><div class="tp-title">GROWTH ANALYSIS</div>'
            f'<p><b>{analysis["score"]}/5 &middot; {qc.esc(label)}</b></p>'
            f'<p>{qc.bold(analysis["summary"])}</p><ul>{items}</ul></div>')


def _consensus_html(consensus):
    if not consensus:
        return ('<div class="tp-panel"><div class="tp-title">CONSENSUS</div>'
                '<p>No analyst consensus available.</p></div>')
    rev = f'{consensus["revenue_growth_pct"]:g}%' if consensus["revenue_growth_pct"] is not None \
        else "—"
    eps = f'{consensus["eps_growth_pct"]:g}%' if consensus["eps_growth_pct"] is not None else "—"
    analysts = consensus["analysts"] if consensus["analysts"] is not None else "—"
    return (f'<div class="tp-panel"><div class="tp-title">CONSENSUS</div>'
            f'<ul>'
            f'<li><b>Fiscal year</b>: {qc.esc(consensus["fiscal_year"])}</li>'
            f'<li><b>Revenue growth (next FY)</b>: {qc.esc(rev)}</li>'
            f'<li><b>EPS growth (next FY)</b>: {qc.esc(eps)}</li>'
            f'<li><b>Analysts</b>: {qc.esc(analysts)}</li>'
            f'</ul><p style="font-size:.78rem">{qc.esc(consensus["source"])}</p></div>')


def prescan_section_html(content, theme):
    """Pre-Scan tab preview: the growth analysis, the consensus block (or its
    "not available" line) and the two flip cards -- or a one-line notice.
    The Growth tab itself (Task 2) has its own, richer layout for this same
    data; this is just a plain preview, like company_profile.profile_list_html."""
    try:
        parsed = parse_growth_cards(content) if content else None
    except ValueError:
        parsed = None
    if not parsed:
        return qc.section_html(TITLE, qc.notice_html(TITLE, theme))

    top = qc.text_row_html(_analysis_html(parsed["analysis"]), _consensus_html(parsed["consensus"]))
    grid = qc.grid_html(GROWTH, parsed["cards"], theme)
    return qc.section_html(TITLE, f'{top}{grid}')
