"""Company Profile: sector/industry, business shape and a handful of quick
facts for the Overview tab's profile panel.

Built on the Business Analysis and Moat Analysis prior sections. Validated
like Moat/Risk/Business Cards so a malformed block is refused before it can
break the Overview tab's rendering.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import json
import re
from datetime import date

import question_cards as qc

TITLE = "Company Profile"

CAPITAL_TYPES = ("Asset-light", "Asset-heavy")
DIFFICULTIES = ("Easy", "Moderate", "Hard")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

PROMPT = """You are turning the existing business work on **{company} ({ticker})** into
a Company Profile: the facts that anchor the Overview tab. Base every field on
the analyses below and on the latest 10-K; never estimate a value — use null
where the field allows it and no reported figure exists.

{prior:Business Analysis}

{prior:Moat Analysis}

sector: the company's GICS sector, e.g. "Communication Services".
industry: the company's GICS industry (or sub-industry), e.g. "Entertainment".
capital_type: "Asset-light" when the business needs little PP&E or inventory
to grow (software, platforms, brands), else "Asset-heavy".
difficulty: how hard the business is to understand. "Easy" = one product and
a simple revenue model. "Moderate" = several segments or a less obvious
model. "Hard" = conglomerates, banks/insurers, biotech pipelines, or heavy
accounting judgement.
founded: the company's founding year, or null if not stated.
employees: full-time employees from the latest 10-K, or null if not stated.
tags: 2 to 4 short business-model tags, e.g. "Subscription", "Marketplace",
"Ad-based", "Recurring revenue".
mission: the company's own mission statement if it has one, else one plain
sentence describing what it does, at most 200 characters.

Output ONLY a fenced JSON block, nothing before or after:

```json
{"sector": "Communication Services", "industry": "Entertainment",
 "capital_type": "Asset-light", "difficulty": "Moderate", "founded": 1997,
 "employees": 16000, "tags": ["Subscription", "Ad-based"],
 "mission": "To entertain the world."}
```
"""


def _is_int(x):
    """int only: booleans are ints in Python and must never pass as a year
    or a headcount, and a numeric string must never silently coerce."""
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


def parse_company_profile(content):
    """The validated profile dict (exactly the eight keys), or ValueError
    saying what is wrong."""
    data = _load_json(content)
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")

    sector = str(data.get("sector") or "").strip()
    if not sector or len(sector) > 60:
        raise ValueError("sector must be 1-60 characters")

    industry = str(data.get("industry") or "").strip()
    if not industry or len(industry) > 60:
        raise ValueError("industry must be 1-60 characters")

    capital_type = data.get("capital_type")
    if capital_type not in CAPITAL_TYPES:
        raise ValueError(f"capital_type must be one of {', '.join(CAPITAL_TYPES)}")

    difficulty = data.get("difficulty")
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")

    current_year = date.today().year
    founded = data.get("founded")
    if founded is not None and (not _is_int(founded) or not (1600 <= founded <= current_year)):
        raise ValueError(f"founded must be an integer 1600-{current_year}, or null")

    employees = data.get("employees")
    if employees is not None and (not _is_int(employees) or employees <= 0):
        raise ValueError("employees must be a positive integer, or null")

    tags = data.get("tags")
    if not isinstance(tags, list) or not (1 <= len(tags) <= 4):
        raise ValueError("tags needs 1-4 entries")
    out_tags, seen = [], set()
    for t in tags:
        tag = str(t or "").strip()
        if not tag or len(tag) > 24:
            raise ValueError("each tag must be 1-24 characters")
        key = tag.lower()
        if key in seen:
            raise ValueError(f"duplicate tag: {tag}")
        seen.add(key)
        out_tags.append(tag)

    mission = str(data.get("mission") or "").strip()
    if not mission or len(mission) > 200:
        raise ValueError("mission must be 1-200 characters")

    return {"sector": sector, "industry": industry, "capital_type": capital_type,
            "difficulty": difficulty, "founded": founded, "employees": employees,
            "tags": out_tags, "mission": mission}


def profile_list_html(content, theme):
    """A readable label/value list for the Pre-Scan tab -- shown as raw JSON
    it reads as a code dump. The Overview tab's own profile panel renders
    the same data with its own layout; this is just a plain preview."""
    try:
        data = parse_company_profile(content) if content else None
    except ValueError:
        data = None
    if not data:
        return qc.section_html(TITLE, qc.notice_html(TITLE, theme))
    rows = [
        ("Sector", data["sector"]),
        ("Industry", data["industry"]),
        ("Capital type", data["capital_type"]),
        ("Difficulty", data["difficulty"]),
        ("Founded", data["founded"] if data["founded"] is not None else "—"),
        ("Employees", data["employees"] if data["employees"] is not None else "—"),
        ("Tags", ", ".join(data["tags"])),
        ("Mission", data["mission"]),
    ]
    items = "".join(f"<li><b>{qc.esc(label)}</b>: {qc.esc(value)}</li>" for label, value in rows)
    return qc.section_html(TITLE, f"<ul>{items}</ul>")
