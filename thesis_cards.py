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

import premortem
import prompt_style
import question_cards as qc

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
            "ignore": _strings(raw.get("ignore"))}


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


# ── into the pre-mortem ───────────────────────────────────────────────

def premortem_blocks(debates, flags) -> list:
    """The premortem blocks of the parsed sections that exist."""
    return [s["premortem"] for s in (debates, flags) if isinstance(s, dict)]


def _clean(text):
    """Whitespace and list bullets off, as premortem.normalize strips them on
    reload, so an item added once matches itself after a save."""
    return _s(text).strip(" -•\t").strip()


def _trim(text, limit):
    """`text` cut at a word to at most `limit` characters, with an ellipsis."""
    text = _clean(text)
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit - 1)
    cut = cut if cut > limit // 2 else limit - 1
    return text[:cut].rstrip(" ,;") + "…"


def _key(text):
    return " ".join(_clean(text).lower().split())


def merge_into_premortem(pm, blocks, today) -> tuple:
    """(new pre-mortem, what was added) after adding the blocks' triggers.

    Only sell/add triggers and ignore strings are added; a trigger whose
    label or condition is already on either list is skipped. Status, action,
    discipline and existing triggers are untouched. One log line records the
    addition when anything was added, so a second press adds nothing.
    """
    base = premortem.normalize(pm) or {
        "status": None, "status_note": "", "action": None, "sell": [], "add": [],
        "ignore": [], "discipline": [], "log": []}
    new = {k: list(v) if isinstance(v, list) else v for k, v in base.items()}
    seen = {_key(t[f]) for side in ("sell", "add") for t in new[side]
            for f in ("label", "when") if _key(t[f])}
    seen_ignore = {_key(i) for i in new["ignore"]}
    added = {"sell": [], "add": [], "ignore": []}
    for block in blocks or []:
        for side in ("sell", "add"):
            for t in block.get(side) or []:
                label = _trim(t.get("label"), premortem.MAX_LABEL)
                when = _trim(t.get("when"), premortem.MAX_WHEN)
                if not when or _key(label) in seen or _key(when) in seen:
                    continue
                trig = {"label": label, "when": when, "detail": "", "state": "watch",
                        "date": ""}
                new[side].append(trig)
                added[side].append(trig)
                seen.update({_key(label), _key(when)} - {""})
        for item in block.get("ignore") or []:
            item = _trim(item, premortem.MAX_ITEM)
            if item and _key(item) not in seen_ignore:
                new["ignore"].append(item)
                added["ignore"].append(item)
                seen_ignore.add(_key(item))
    n = sum(len(v) for v in added.values())
    if n:
        new["log"].insert(0, {"date": today, "text": f"{n} items from Thesis added"})
    return new, added


# ── page ──────────────────────────────────────────────────────────────

_GREEN, _AMBER, _RED, _GREY = "#2f8f4e", "#c79a3a", "#c0603f", "#8a8f98"
_STATUS_TONE = {"green": _GREEN, "yellow": _AMBER, "red": _RED, "n.a.": _GREY}
_LEAN_TONE = {"bull": _GREEN, "bear": _RED, "open": _AMBER}
_CEILING_TONE = {"full": _GREEN, "normal": _GREEN, "starter": _AMBER, "none": _RED}
_VERDICT_TONE = {"Clean": _GREEN, "Watch": _AMBER, "Red flag": _RED}
_ANSWERABLE = {"yes": "Data can settle it", "partly": "Partly answerable",
               "no": "Only time settles it"}

STYLE = """<style>
.th-top{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;margin-bottom:18px}
@media (min-width:800px){.th-top{grid-template-columns:repeat(2,minmax(0,1fr))}}
.th-card{background:var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)));border-radius:16px;padding:16px 18px;min-width:0}
.th-lbl{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--text-muted);margin:0 0 8px}
.th-big{display:flex;align-items:center;gap:8px;font-size:18px;font-weight:700;color:var(--text)}
.th-dot{width:11px;height:11px;border-radius:50%;flex:none}
.th-note{font-size:14px;color:var(--text);line-height:1.5;margin:6px 0 0}
.th-sec{margin-top:22px}
.th-h{font-size:12px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--text-muted);margin:0 0 10px}
.th-deb{background:var(--card);border-radius:14px;border-left:4px solid;padding:14px 16px;margin:0 0 12px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.th-q{display:flex;justify-content:space-between;gap:10px;align-items:baseline}
.th-q b{font-size:15.5px;color:var(--text)}
.th-pill{flex:none;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:7px;color:#fff}
.th-sub{font-size:12.5px;color:var(--text-muted);margin:4px 0 8px}
.th-sides{display:grid;grid-template-columns:minmax(0,1fr);gap:8px;margin:6px 0}
@media (min-width:800px){.th-sides{grid-template-columns:repeat(2,minmax(0,1fr))}}
.th-side{border-radius:10px;padding:8px 12px;font-size:13.5px;line-height:1.45;color:var(--text)}
.th-side i{font-style:normal;font-weight:700;font-size:11px;letter-spacing:.06em;text-transform:uppercase;display:block;margin-bottom:2px}
.th-row{font-size:13.5px;line-height:1.45;color:var(--text);margin:5px 0 0}
.th-row span{color:var(--text-muted)}
.th-chk{display:flex;gap:10px;align-items:flex-start;padding:9px 0;border-bottom:1px solid color-mix(in srgb, var(--text) 10%, transparent)}
.th-chk:last-child{border-bottom:none}
.th-chk .th-dot{margin-top:5px}
.th-chk b{font-size:14px;color:var(--text);font-weight:600}
.th-chk div{font-size:13.5px;color:var(--text);line-height:1.45}
.th-chk small{display:block;font-size:12px;color:var(--text-muted);margin-top:2px}
.th-chips{display:flex;flex-wrap:wrap;gap:6px}
.th-chip{padding:4px 10px;border-radius:8px;font-size:13px;color:var(--text-muted);background:color-mix(in srgb, var(--text) 6%, var(--card))}
.th-warn{font-size:13.5px;color:var(--text-muted);padding:10px 2px}
</style>"""


def _pill(text, tone):
    return f'<span class="th-pill" style="background:{tone}">{qc.esc(text)}</span>'


def _top_card(label, word, tone, note):
    note = f'<p class="th-note">{qc.esc(note)}</p>' if note else ""
    return (f'<div class="th-card"><p class="th-lbl">{qc.esc(label)}</p>'
            f'<div class="th-big"><span class="th-dot" style="background:{tone}"></span>'
            f'{qc.esc(word)}</div>{note}</div>')


def _debate_html(d):
    esc = qc.esc
    tone = _LEAN_TONE[d["lean"]]

    def row(label, text):
        return f'<p class="th-row"><span>{label}:</span> {esc(text)}</p>' if text else ""

    sides = ""
    if d["bull"] or d["bear"]:
        sides = (f'<div class="th-sides">'
                 f'<div class="th-side" style="background:color-mix(in srgb, {_GREEN} 9%, transparent)">'
                 f'<i style="color:{_GREEN}">Bull</i>{esc(d["bull"])}</div>'
                 f'<div class="th-side" style="background:color-mix(in srgb, {_RED} 9%, transparent)">'
                 f'<i style="color:{_RED}">Bear</i>{esc(d["bear"])}</div></div>')
    return (f'<div class="th-deb" style="border-left-color:{tone}">'
            f'<div class="th-q"><b>{esc(d["question"])}</b>{_pill("Leans " + d["lean"], tone)}</div>'
            f'<div class="th-sub">{_ANSWERABLE[d["answerable"]]}</div>'
            + row("Why it matters", d["why_it_matters"]) + sides
            + row("Settles it", d["settles_it"]) + row("Precedent", d["precedent"])
            + (row("Next research", d["next_research"]) if d["answerable"] != "no" else "")
            + "</div>")


def _check_html(c):
    tone = _STATUS_TONE[c["status"]]
    status = c["status"].replace(".", "")
    ev = f'<small>{qc.esc(c["evidence"])}</small>' if c["evidence"] else ""
    return (f'<div class="th-chk th-{status}"><span class="th-dot" style="background:{tone}"></span>'
            f'<div><b>{qc.esc(c["label"])}</b><br>{qc.esc(c["finding"])}{ev}</div></div>')


def _section(title, inner):
    return f'<div class="th-sec"><p class="th-h">{qc.esc(title)}</p>{inner}</div>'


def _parsed(raw, parser):
    """(parsed, problem): problem is None when it parsed or nothing is stored."""
    if not (raw or "").strip():
        return None, None
    try:
        return parser(raw), None
    except ValueError as e:
        return None, str(e)


def tab_html(debates_raw, flags_raw, theme) -> str:
    """The Thesis tab: ceiling and verdict, the debates, the red-flag checks.

    Each half renders on its own; a section that is missing or unreadable
    becomes a one-line notice instead of hiding the other half.
    """
    deb, deb_err = _parsed(debates_raw, parse_debates)
    flg, flg_err = _parsed(flags_raw, parse_red_flags)
    top = []
    if deb:
        cc = deb["conviction_ceiling"]
        top.append(_top_card("Conviction ceiling", cc["level"].capitalize(),
                             _CEILING_TONE[cc["level"]],
                             f"Capped by: {cc['capped_by']}" if cc["capped_by"] else ""))
    if flg:
        v = flg["verdict"]
        decided = dict(CHECKS).get(v["decided_by"], v["decided_by"])
        note = " ".join(x for x in (v["summary"], f"Decided by: {decided}." if decided else "") if x)
        top.append(_top_card("Red flags", v["level"], _VERDICT_TONE[v["level"]], note))
    parts = [STYLE]
    if top:
        parts.append(f'<div class="th-top">{"".join(top)}</div>')

    def problem(title, err):
        if err:
            return f'<div class="th-warn">{title} could not be read ({qc.esc(err)}).</div>'
        return qc.notice_html(title, theme)

    if deb:
        parts.append(_section("Key debates", "".join(_debate_html(d) for d in deb["debates"])))
        dt = deb["downside_test"]
        if dt["note"] or dt["passes"] is not None:
            word = {True: "Passes", False: "Fails", None: "Unclear"}[dt["passes"]]
            tone = {True: _GREEN, False: _RED, None: _AMBER}[dt["passes"]]
            parts.append(_section("Downside test", _top_card(
                "If every debate goes bear-side", word, tone, dt["note"])))
        if deb["non_issues"]:
            rows = "".join(f'<p class="th-row"><b>{qc.esc(n["worry"])}</b> '
                           f'<span>— {qc.esc(n["why_not"])}</span></p>' for n in deb["non_issues"])
            parts.append(_section("Not a reason to worry", f'<div class="th-card">{rows}</div>'))
    else:
        parts.append(problem(DEBATES_TITLE, deb_err))
    if flg:
        checks = "".join(_check_html(c) for c in flg["checks"])
        parts.append(_section("Red-flag checks", f'<div class="th-card">{checks}</div>'))
        if flg["could_not_check"]:
            chips = "".join(f'<span class="th-chip">{qc.esc(x)}</span>' for x in flg["could_not_check"])
            parts.append(_section("Could not check", f'<div class="th-chips">{chips}</div>'))
    else:
        parts.append(problem(FLAGS_TITLE, flg_err))
    return "".join(parts)
