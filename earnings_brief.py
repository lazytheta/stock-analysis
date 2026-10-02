"""Earnings Brief: what the latest earnings call said, for the Earnings tab's
"Latest call" section, plus the consensus for the next quarter.

Written by Claude from two SEC-connector calls (the latest call's tone and
themes, and the analyst estimates) and validated like the card sections, so a
malformed block is refused at save time instead of breaking the tab.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import html
import json
import math
import re
from datetime import date

import prompt_style
import question_cards as qc

TITLE = "Earnings Brief"
TONES = ("Positive", "Neutral", "Cautious")

SUMMARY_MAX = 240
SOURCE_MAX = 160
LABEL_MAX = 40
PERIOD_MAX = 60
POINT_TEXT_MAX = 240
POINT_WORDS = (2, 5)

PROMPT = """You are writing the Earnings Brief for **{company} ({ticker})**: what the
company said on its latest earnings call, and what analysts expect for the
next quarter. It fills the "Latest call" section of the Earnings tab, next to
the reported numbers (those come from the filings; don't repeat them).

{prior:Business Analysis}

SOURCES — use the SEC connector, at most 2 calls in total:
- GetEarningsCallToneAndThemes for the latest earnings call.
- GetAnalystEstimates for the consensus of the next quarter not yet reported.

RULES:
- quarter = the most recently REPORTED fiscal quarter (the one the latest call
  discussed), not the one in progress. fiscal_qtr_end is that quarter's last
  day as the company's filing states it; label is the company's own name for
  it, e.g. "Q4 FY2026".
- tone = management's own outlook on the call: "Positive", "Neutral" or
  "Cautious". It is about what management said and expects, not about how
  the share price reacted.
- next: the first fiscal quarter not yet reported. period names it, e.g.
  "Q1 FY2027" or "Q ending 2026-09-30". eps_consensus (per share, in dollars)
  and revenue_consensus_usd (in dollars, not millions) are the level
  estimates GetAnalystEstimates returns for that quarter; analysts is its
  analyst count. Copy them, never estimate them by hand: null for anything
  the call returns nothing usable for.
- If a source returns nothing, say so in the summary and never estimate.

summary: ONE sentence (at most 240 characters) on how the quarter went and
how management sounded.
points: EXACTLY three, in this order, each {"label": two to five words,
"text": one line}:
1. what went well,
2. what worries,
3. what management expects.
as_of: the date of your data, "YYYY-MM-DD".
source: the feeds you used, in a few words (at most 160 characters).

""" + prompt_style.HOW_TO_WRITE + """
Output ONLY a fenced JSON block, nothing before or after:

```json
{"quarter": {"fiscal_qtr_end": "2026-06-30", "label": "Q4 FY2026"},
 "tone": "Positive",
 "summary": "Demand for its cloud services kept growing faster than expected and management sounded confident.",
 "points": [
  {"label": "What went well", "text": "Cloud revenue grew about 40%, ahead of guidance."},
  {"label": "What worries", "text": "Spending on new data centres keeps rising faster than sales."},
  {"label": "Management expects", "text": "Growth to stay above 30% next quarter as capacity comes online."}
 ],
 "next": {"period": "Q1 FY2027", "eps_consensus": 3.85,
          "revenue_consensus_usd": 80500000000, "analysts": 34},
 "as_of": "2026-07-30",
 "source": "SEC-MCP: earnings call tone and themes, analyst estimates"}
```
"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_json(content):
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None


def _text(value, name, max_len):
    """A stripped string with HTML entities decoded (a model sometimes writes
    "R&amp;D", which qc.esc would escape again)."""
    if not isinstance(value, str) or not html.unescape(value).strip():
        raise ValueError(f"{name} must be a non-empty string")
    out = html.unescape(value).strip()
    if len(out) > max_len:
        raise ValueError(f"{name} must be at most {max_len} characters (has {len(out)})")
    return out


def _iso_date(value, name):
    if not isinstance(value, str) or not _DATE.match(value.strip()):
        raise ValueError(f'{name} must be a date "YYYY-MM-DD"')
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f'{name} must be a real date "YYYY-MM-DD"') from None
    return value.strip()


def _number(value, name, lo=None):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number or null")
    if lo is not None and value < lo:
        raise ValueError(f"{name} must be at least {lo} or null")
    return value


def _count(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a whole number ≥ 0 or null")
    return value


def _quarter(value):
    if not isinstance(value, dict):
        raise ValueError('quarter must be {"fiscal_qtr_end": "YYYY-MM-DD", "label": ...}')
    return {"fiscal_qtr_end": _iso_date(value.get("fiscal_qtr_end"), "quarter.fiscal_qtr_end"),
            "label": _text(value.get("label"), "quarter.label", LABEL_MAX)}


def _tone(value):
    word = str(value or "").strip().capitalize()
    if word not in TONES:
        raise ValueError(f"tone must be one of {', '.join(TONES)}")
    return word


def _points(value):
    try:
        points = qc._points(value, "points")
    except ValueError:
        raise ValueError('points: needs exactly three points, each with label and text '
                         '(what went well, what worries, what management expects)') from None
    out = []
    lo, hi = POINT_WORDS
    for i, p in enumerate(points):
        label = html.unescape(p["label"]).strip()
        text = html.unescape(p["text"]).strip()
        if not lo <= len(label.split()) <= hi:
            raise ValueError(f"points[{i}].label must be {lo}-{hi} words (has "
                             f"{len(label.split())}: {label!r})")
        if "\n" in text or len(text) > POINT_TEXT_MAX:
            raise ValueError(f"points[{i}].text must be one line of at most "
                             f"{POINT_TEXT_MAX} characters")
        out.append({"label": label, "text": text})
    return out


def _next(value):
    if not isinstance(value, dict):
        raise ValueError('next must be {"period": ..., "eps_consensus": number or null, '
                         '"revenue_consensus_usd": number or null, "analysts": int or null}')
    return {"period": _text(value.get("period"), "next.period", PERIOD_MAX),
            "eps_consensus": _number(value.get("eps_consensus"), "next.eps_consensus"),
            "revenue_consensus_usd": _number(value.get("revenue_consensus_usd"),
                                             "next.revenue_consensus_usd", 0),
            "analysts": _count(value.get("analysts"), "next.analysts")}


def parse_earnings_brief(content):
    """{quarter, tone, summary, points, next, as_of, source} validated, or
    ValueError saying what is wrong."""
    data = _load_json(content)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return {
        "quarter": _quarter(data.get("quarter")),
        "tone": _tone(data.get("tone")),
        "summary": _text(data.get("summary"), "summary", SUMMARY_MAX),
        "points": _points(data.get("points")),
        "next": _next(data.get("next")),
        "as_of": _iso_date(data.get("as_of"), "as_of"),
        "source": _text(data.get("source"), "source", SOURCE_MAX),
    }
