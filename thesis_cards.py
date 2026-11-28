"""Thesis tab: Key Debates and Red Flags (owner request 2026-11-28).

Between the prescan ("what is this business?") and the Pre-mortem ("when do I
act?"): what decides the investment, and what could blow it up. Both prompts
end with a `premortem` block in set_premortem's trigger shape, so the tab can
add their triggers to the Pre-mortem with one button (merge_into_premortem).

Pure module: no Streamlit, no Supabase. Named thesis_cards because thesis.py
is the DCF-vs-history grade.
"""

import json
import re

import prompt_style

DEBATES_TITLE = "Key Debates"
FLAGS_TITLE = "Red Flags"

CHECKS = (
    ("receivables_inventory", "Receivables and inventory"),
    ("cash_vs_profit", "Cash versus profit"),
    ("adjusted_vs_reported", "Adjusted versus reported"),
    ("changed_definitions", "Changed definitions"),
    ("off_balance_sheet", "Off the balance sheet"),
    ("uncheckable_numbers", "Numbers you cannot check"),
    ("capitalised_costs", "Capitalised costs"),
    ("governance", "Governance signals"),
    ("short_reports", "Short reports"),
    ("competitor_crosscheck", "Competitor cross-check"),
)

LEANS = ("bull", "bear", "open")
ANSWERABLE = ("yes", "partly", "no")
CEILINGS = ("full", "normal", "starter", "none")
VERDICTS = ("Clean", "Watch", "Red flag")
STATUSES = ("green", "yellow", "red", "n.a.")

_TRIGGER_RULE = ('Each trigger: label ≤40 characters, when ≤160 characters, ONE checkable\n'
                 'condition, state "watch".')

DEBATES_PROMPT = """# KEY DEBATES — {company} ({ticker})

Find the two or three questions this investment actually depends on, and judge
how far today's evidence answers them. This is not a summary of the prescan:
most facts in it do not move the decision. Find the few that do.

## PRIOR RESEARCH
Business: {prior:Business Analysis}
Moat: {prior:Moat Analysis}
Runway: {prior:Long-Term Potential}
Risk: {prior:Risk Analysis}
Sentiment: {prior:Price & Sentiment Analysis}
Red flags: {prior:Red Flags}

## WHERE THE DEBATES ARE
Look for what the market is arguing about, not what you find interesting:
- SEC connector: GetEarningsCallQuestions for the last four calls. The
  questions analysts keep asking are the debate. GetTranscriptPhraseTrend
  for a topic that management has started (or stopped) talking about.
- Short reports and bear write-ups on {company} from the last three years
  (web search). Treat a short report as the best free statement of the
  bear case; say whether its central claim holds against the filings.
- The bear case in the Sentiment section and the "biggest assumption" in
  the Runway section.
A debate qualifies only if its answer would move the fair value by a lot
(roughly a fifth or more) or decide whether the business survives. Revenue
growth of 8% versus 10% is not a debate; whether a competitor can close the
spread the company earns is.

## FOR EACH DEBATE
- question: one plain question that has a yes/no or a number as its answer.
- why_it_matters: what happens to earnings or the business if the bear is right.
- bull / bear: the strongest version of each side, each with the one fact
  that supports it (figure, period, source).
- lean: "bull", "bear" or "open". Judge the evidence; do not take the side the
  price implies.
- answerable: "yes" (filings or data can settle it), "partly", or "no" (no
  amount of extra research will settle it, only time). Be honest here: most
  long-term debates are "partly" or "no".
- settles_it: the observable metric and level that would decide it, and when
  it is reported (e.g. "take rate below 3% for two quarters, quarterly 10-Q").
- precedent: has this company faced the same kind of question before (a
  platform shift, a price war, a cycle)? What happened? One line, or
  "no precedent found". Use GetEarningsCallThemeHistory or old filings.
- next_research: the single cheapest piece of work that would move "lean"
  (a competitor's filing, customer reviews, an expert call, a footnote).
  Skip it if answerable is "no".

## DOWNSIDE TEST
Assume all the debates resolve bear-side. Does the investment still avoid a
permanent loss? Answer in one line, naming what is left (cash, the core
segment, assets). Compare with the DCF bear scenario if there is one; do not
compute a new fair value.

## NON-ISSUES
Up to three worries the market raises that you checked and found do NOT
matter for the outcome, each with the reason (e.g. "spending on the new
segment: even if it loses money forever, the core still earns X").

## CONVICTION CEILING
How much the evidence supports, independent of price:
- "full": every debate is answerable and leans bull, the red flags are clean,
  and the downside test passes.
- "normal": at most one debate is open or unanswerable, and the downside
  test passes.
- "starter": a debate that decides the outcome is unanswerable, or the
  downside test fails, or a red flag is yellow.
- "none": a red flag is red, or a debate leans bear and decides survival.
Name the one debate that caps the ceiling.

""" + prompt_style.HOW_TO_WRITE + """- If you find only two debates that qualify, give two. Never pad to three.

## PREMORTEM BLOCK
Turn the debates into the Pre-mortem tab's format:
- sell: for each debate, the bear-side outcome of settles_it as a trigger.
- add: for each debate where lean is bull, the outcome that would confirm it.
- ignore: the non-issues, as signals that are NOT a reason to act.
""" + _TRIGGER_RULE + """

Output ONLY a fenced JSON block, nothing before or after:

```json
{"debates": [
  {"question": "...", "why_it_matters": "...",
   "bull": "...", "bear": "...", "lean": "open", "answerable": "partly",
   "settles_it": "...", "precedent": "...", "next_research": "..."}
 ],
 "downside_test": {"passes": true, "note": "..."},
 "non_issues": [{"worry": "...", "why_not": "..."}],
 "conviction_ceiling": {"level": "starter", "capped_by": "..."},
 "premortem": {
   "sell": [{"label": "...", "when": "...", "state": "watch"}],
   "add":  [{"label": "...", "when": "...", "state": "watch"}],
   "ignore": ["..."]},
 "sources": ["..."]}
```
"""

FLAGS_PROMPT = """# RED FLAGS — {company} ({ticker})

Look for what would turn this investment into a disaster that the normal
analysis misses: accounting that flatters, obligations hidden in footnotes,
metrics whose definition moved, and management figures that cannot be
checked. Start from the assumption that something is wrong and look for
evidence against it. Most companies come back clean, and saying so is a
valid answer, but only after the checks are done.

## PRIOR RESEARCH
Business: {prior:Business Analysis}
Management: {prior:Management Cards}

## SOURCES
SEC filings first: the last three 10-Ks (including the notes), the latest
10-Q, 8-Ks. SEC connector: GetFinancialStatement, GetNonGaapBridge,
GetGoingConcernStatus, SearchDocument / SearchDocuments for footnote
wording, ListFilings for late filings and amendments. Web search only for
short reports and for how competitors define the same metric.

## THE CHECKS (rate each green / yellow / red / n.a.)
1. Receivables and inventory: are they growing clearly faster than revenue
   over three years? (Customers being sent more than they sell = channel
   stuffing.) Give days of receivables and inventory, then and now.
2. Cash versus profit: operating cash flow ÷ net income over three years.
   Below 80% without a clear reason (growth in working capital you can
   explain) = yellow.
3. Adjusted versus reported: how big is the gap between the company's
   adjusted and its GAAP earnings, and does it recur? Stock-based pay as a
   share of revenue. "One-off" charges in three or more of the last five
   years = yellow.
4. Changed definitions: did a key metric (users, GMV, bookings, same-store
   sales, segment split) change definition, or stop being reported, in the
   last five years? Did a restatement happen? A metric that changed exactly
   when it would have looked bad = red.
5. Off the balance sheet: guarantees, purchase commitments, factoring or
   supplier-finance programmes, entities the company controls but does not
   consolidate, large operating leases. Size each against equity or yearly
   free cash flow.
6. Numbers you cannot check: does management steer investors toward a figure
   the filings do not let you reconstruct (cumulative cash flow from
   acquisitions, "organic" growth without a bridge, adjusted returns)? Say
   which figure and what the checkable alternative shows.
7. Capitalised costs: are rising amounts of normal operating spending
   (software, customer acquisition, development) moved onto the balance
   sheet instead of expensed?
8. Governance signals: auditor change, material weakness, late or amended
   filings, going-concern language, large related-party transactions.
9. Short reports: any in the last three years? State the central claim in
   one line and whether the filings since then confirm or refute it.
10. Competitor cross-check: does a direct competitor report the same metric
    in a way that makes this company's figure look inflated or deflated?

## VERDICT
- "Clean": no red, at most one yellow with an innocent explanation.
- "Watch": yellows without a convincing explanation, or a check you could
  not do because of limited disclosure on something that matters.
- "Red flag": any red.
Name the one check that decides it.

""" + prompt_style.HOW_TO_WRITE + """- Every finding names its evidence: the figure, the period, the filing.
- "Limited disclosure" is a finding; never fill a gap with an estimate.
- Report only the checks that decide or nearly decide the verdict in
  full; give the rest one short line each.

## PREMORTEM BLOCK
- sell: every red finding, and every yellow finding that could turn red, as
  a trigger (e.g. "Receivables growth > revenue growth +10pp, two years").
- ignore: findings you checked and found innocent, as signals that are NOT
  a reason to act (e.g. "high adjusted-vs-GAAP gap: it is amortisation of
  one 2019 acquisition").
""" + _TRIGGER_RULE + """

Output ONLY a fenced JSON block, nothing before or after:

```json
{"verdict": {"level": "Watch", "decided_by": "...", "summary": "..."},
 "checks": [
   {"check": "receivables_inventory", "status": "green", "finding": "...", "evidence": "..."},
   {"check": "cash_vs_profit", "status": "green", "finding": "...", "evidence": "..."},
   {"check": "adjusted_vs_reported", "status": "yellow", "finding": "...", "evidence": "..."},
   {"check": "changed_definitions", "status": "green", "finding": "...", "evidence": "..."},
   {"check": "off_balance_sheet", "status": "green", "finding": "...", "evidence": "..."},
   {"check": "uncheckable_numbers", "status": "n.a.", "finding": "...", "evidence": "..."},
   {"check": "capitalised_costs", "status": "green", "finding": "...", "evidence": "..."},
   {"check": "governance", "status": "green", "finding": "...", "evidence": "..."},
   {"check": "short_reports", "status": "n.a.", "finding": "...", "evidence": "..."},
   {"check": "competitor_crosscheck", "status": "green", "finding": "...", "evidence": "..."}
 ],
 "could_not_check": ["..."],
 "premortem": {
   "sell": [{"label": "...", "when": "...", "state": "watch"}],
   "ignore": ["..."]},
 "sources": ["..."]}
```
"""


# ── parsing ───────────────────────────────────────────────────────────

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _load(content):
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def _s(v):
    return str(v or "").strip()


def _enum(value, allowed, where):
    if value not in allowed:
        raise ValueError(f"{where} must be one of {', '.join(allowed)}, got {value!r}")
    return value


def _premortem_block(raw):
    raw = raw if isinstance(raw, dict) else {}

    def triggers(key):
        return [{"label": _s(t.get("label")), "when": _s(t.get("when"))}
                for t in (raw.get(key) or [])
                if isinstance(t, dict) and (_s(t.get("label")) or _s(t.get("when")))]

    return {"sell": triggers("sell"), "add": triggers("add"),
            "ignore": [_s(i) for i in (raw.get("ignore") or []) if _s(i)]}


def _strings(raw):
    return [_s(i) for i in (raw or []) if _s(i)] if isinstance(raw, list) else []


def parse_debates(content) -> dict:
    """The validated Key Debates section, or ValueError saying what is wrong."""
    data = _load(content)
    debates = data.get("debates")
    if not isinstance(debates, list) or not 1 <= len(debates) <= 3:
        raise ValueError("debates: needs one to three debates")
    out = []
    for i, d in enumerate(debates):
        if not isinstance(d, dict) or not _s(d.get("question")):
            raise ValueError(f"debates[{i}]: needs a question")
        out.append({
            "question": _s(d["question"]),
            **{k: _s(d.get(k)) for k in ("why_it_matters", "bull", "bear", "settles_it",
                                         "precedent", "next_research")},
            "lean": _enum(_s(d.get("lean")).lower(), LEANS, f"debates[{i}].lean"),
            "answerable": _enum(_s(d.get("answerable")).lower(), ANSWERABLE,
                                f"debates[{i}].answerable"),
        })
    dt = data.get("downside_test") if isinstance(data.get("downside_test"), dict) else {}
    cc = data.get("conviction_ceiling") if isinstance(data.get("conviction_ceiling"), dict) else {}
    return {
        "debates": out,
        "downside_test": {"passes": dt.get("passes") if isinstance(dt.get("passes"), bool) else None,
                          "note": _s(dt.get("note"))},
        "non_issues": [{"worry": _s(n.get("worry")), "why_not": _s(n.get("why_not"))}
                       for n in (data.get("non_issues") or [])
                       if isinstance(n, dict) and _s(n.get("worry"))][:3],
        "conviction_ceiling": {
            "level": _enum(_s(cc.get("level")).lower(), CEILINGS, "conviction_ceiling.level"),
            "capped_by": _s(cc.get("capped_by"))},
        "premortem": _premortem_block(data.get("premortem")),
        "sources": _strings(data.get("sources")),
    }


def parse_red_flags(content) -> dict:
    """The validated Red Flags section, or ValueError saying what is wrong."""
    data = _load(content)
    v = data.get("verdict") if isinstance(data.get("verdict"), dict) else {}
    level = next((x for x in VERDICTS if x.lower() == _s(v.get("level")).lower()), None)
    if level is None:
        raise ValueError(f"verdict.level must be one of {', '.join(VERDICTS)}")
    checks = data.get("checks")
    if not isinstance(checks, list):
        raise ValueError("checks: expected a list")
    known = dict(CHECKS)
    by_key = {}
    for i, c in enumerate(checks):
        key = _s(c.get("check")) if isinstance(c, dict) else ""
        if key not in known:
            raise ValueError(f"checks[{i}]: unknown check {key!r}")
        by_key[key] = {"check": key, "label": known[key],
                       "status": _enum(_s(c.get("status")).lower(), STATUSES,
                                       f"checks[{i}].status"),
                       "finding": _s(c.get("finding")), "evidence": _s(c.get("evidence"))}
    return {
        "verdict": {"level": level, "decided_by": _s(v.get("decided_by")),
                    "summary": _s(v.get("summary"))},
        "checks": [by_key[k] for k, _ in CHECKS if k in by_key],
        "could_not_check": _strings(data.get("could_not_check")),
        "premortem": _premortem_block(data.get("premortem")),
        "sources": _strings(data.get("sources")),
    }
