"""Management Cards: four question cards per ticker (skin in the game, pay,
stability, capital allocation) plus a validated "facts" object (insider ownership, net insider
trading, CEO pay, C-suite changes) for the Management tab. Answers run from
worst (0, red) to best (2, green).

The writer fills it with the SEC connector's executive and insider feeds.
Validated like the other card sections so a malformed block is refused
before it can break the tab.

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

TITLE = "Management Cards"
ITEMS = (
    ("skin_in_the_game", "Skin in the game", "Do they have skin in the game?",
     ("Little", "Some", "Plenty")),
    ("pay", "Pay", "Is the pay reasonable?", ("Excessive", "Okay", "Reasonable")),
    ("stability", "Stability", "Is the top team stable?",
     ("Unstable", "Some change", "Stable")),
    ("capital_allocation", "Capital allocation", "Do they spend the cash well?",
     ("Poor", "Mixed", "Good")),
)
MANAGEMENT = qc.CardSet(TITLE, ITEMS, directions=None, blocks=())

# Cards saved before 2026-10-08 have only the first three questions; they
# still render (parse_for_display) until re-run. A new save needs all four.
LEGACY_MANAGEMENT = qc.CardSet(TITLE, ITEMS[:3], directions=None, blocks=())

SOURCE_MAX = 160
NAME_MAX = 120
MAX_PAY_YEARS = 3
MAX_CHANGES = 5

PROMPT = """You are writing the Management Cards for **{company} ({ticker})**: four
question cards on the people running the company, plus the facts behind them.

{prior:Business Analysis}

SOURCES — use the SEC connector, at most 5 calls in total:
- GetExecutiveCompensation (CEO pay over the last years, share paid in stock)
- GetInsiderOwnership (shares held by current officers and directors)
- GetInsiderTransactions (the last 12 months)
- GetExecutiveChanges (C-suite arrivals and departures)
- GetGuidance (its "track record" line: how often results met or beat the
  company's own guidance)
Shares outstanding, returns on capital, buybacks and acquisitions come from
get_fundamentals, the ticker's config and the prior analysis.

CLEANUP RULES — the feeds are raw; clean them before you count anything:
- Merge duplicate executive names (e.g. "Timothy M. Archer" = "Timothy Archer").
- Count only current officers and directors: drop people whose last filing is
  older than 18 months or who have left per the changes feed.
- Ignore annual director re-elections; changes = C-suite (CEO, CFO, COO,
  President, and comparable) arrivals and departures only.
- Insider trading: sum the transaction values of open-market Buy and Sell
  transactions only; never use the balance-after field; exclude awards,
  option exercises and tax withholding.
- Ownership % = shares held ÷ shares outstanding from EDGAR; the feed gives no
  percentage.
- Pay: judge over several years (front-loaded hire grants make a single year
  misleading); compare it with how revenue and earnings moved (from the prior
  analysis or get_fundamentals).
- If a source returns nothing, set the field null and say so in the relevant
  card; never estimate.

For each question, in exactly this order, pick an answer (0 = worst, 2 = best):
- skin_in_the_game: Do they have skin in the game? pick 0 = Little, 1 = Some,
  2 = Plenty. Judge on what officers and directors own and on net open-market
  insider buying or selling.
- pay: Is the pay reasonable? pick 0 = Excessive, 1 = Okay, 2 = Reasonable.
  Judge the pay level against the company's size and results, and the share
  paid in stock.
- stability: Is the top team stable? pick 0 = Unstable, 1 = Some change,
  2 = Stable. Judge on recent C-suite turnover, abrupt exits and, where known,
  how long the leaders have been in their seats.
- capital_allocation: Do they spend the cash well? pick 0 = Poor, 1 = Mixed,
  2 = Good. Judge on where the cash went over the last five to ten years and
  what it earned: return on capital over time, acquisitions (paid off or
  written down), buybacks (shrinking the share count, bought at sensible
  prices), big new bets and how they turned out, debt taken on. Name the
  largest use of cash and its result.

summary: ONE sentence for the front of the card, with the fact that decides the pick.
points: EXACTLY three, each {"label": two to four words, "text": one line with the
plainest evidence}.

facts (null for anything a source did not give):
- ceo: {"name": ..., "since": year the CEO took the job, or null}
- insider_ownership_pct: current officers and directors together, % of shares
  outstanding (0-100)
- ceo_ownership_pct: the CEO alone, % of shares outstanding (0-100)
- ceo_shares: the number of shares the CEO owns, all share classes together,
  from the latest proxy's beneficial-ownership table (DEF 14A); the insider
  feed can understate it, so prefer the proxy
- ceo_bio: {"background": who the CEO is and how they got here — founder or
  hired, earlier roles, years in the job — at most 300 characters,
  "reputation": how investors and the press see the CEO, both the credit
  and the criticism, with the episode behind each, at most 300 characters}.
  Refer to the CEO by surname, not by pronoun. Null when there is too little
  to say.
- insider_net_12m_usd: open-market buys minus sells over the last 12 months,
  in US dollars (negative = net selling)
- buyers / sellers: how many insiders bought / sold on the open market in
  those 12 months
- planned_sell_pct: share of the sell value done under pre-arranged 10b5-1
  plans (0-100)
- ceo_pay: up to three fiscal years, newest last, each {"fy": "FY2025",
  "total_usd": total reported pay, "stock_pct": share paid in stock (0-100) or
  null}
- changes: up to five C-suite changes, newest first, each {"date":
  "YYYY-MM-DD", "person": ..., "role": ..., "action": e.g. "appointed",
  "resigned", "retired"}; an empty list when there were none
- guidance_record: {"met": how many times results met or beat the company's
  own guidance, "total": how many settled comparisons} from GetGuidance's
  track record line, or null when it gives none
- as_of: the date of your data, "YYYY-MM-DD"
- source: the feeds you used, in a few words (at most 160 characters)

""" + prompt_style.HOW_TO_WRITE + """
Output ONLY a fenced JSON block, nothing before or after:

```json
{"cards": [
  {"source": "skin_in_the_game", "pick": 1, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]},
  {"source": "pay", "pick": 1, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]},
  {"source": "stability", "pick": 2, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]},
  {"source": "capital_allocation", "pick": 1, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
 ],
 "facts": {
  "ceo": {"name": "Timothy Archer", "since": 2018},
  "insider_ownership_pct": 0.4, "ceo_ownership_pct": 0.1, "ceo_shares": 1300000,
  "ceo_bio": {"background": "...", "reputation": "..."},
  "insider_net_12m_usd": -28000000, "buyers": 0, "sellers": 6,
  "planned_sell_pct": 65,
  "ceo_pay": [{"fy": "FY2023", "total_usd": 17500000, "stock_pct": 72},
              {"fy": "FY2024", "total_usd": 19800000, "stock_pct": 74},
              {"fy": "FY2025", "total_usd": 24100000, "stock_pct": 78}],
  "changes": [{"date": "2025-02-10", "person": "Jane Doe", "role": "CFO",
               "action": "appointed"}],
  "guidance_record": {"met": 7, "total": 8},
  "as_of": "2026-09-30",
  "source": "SEC-MCP: compensation, insider ownership, Form 4 trades, executive changes"
 }}
```
The cards array holds all four questions, in the order listed above.
"""

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FY = re.compile(r"^FY\d{4}$")


def _load_json(content):
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None


def _text(value, name, required=True, max_len=NAME_MAX):
    """A stripped string with HTML entities decoded (a model sometimes writes
    "R&amp;D", which qc.esc would escape again)."""
    if not isinstance(value, str) or not html.unescape(value).strip():
        raise ValueError(f"{name} must be a non-empty string")
    out = html.unescape(value).strip()
    if len(out) > max_len:
        raise ValueError(f"{name} must be at most {max_len} characters")
    return out


def _number(value, name, lo=None, hi=None):
    """A finite number in [lo, hi], None for null, ValueError otherwise."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a number or null")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"{name} must be between {lo} and {hi}" if hi is not None
                         else f"{name} must be at least {lo}")
    return value


def _count(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a whole number ≥ 0 or null")
    return value


def _iso_date(value, name):
    if not isinstance(value, str) or not _DATE.match(value.strip()):
        raise ValueError(f'{name} must be a date "YYYY-MM-DD"')
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        raise ValueError(f'{name} must be a real date "YYYY-MM-DD"') from None
    return value.strip()


def _list(value, name, max_n):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > max_n:
        raise ValueError(f"{name} must be a list of at most {max_n} entries")
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{name}[{i}] must be an object")
    return value


def _ceo(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('ceo must be {"name": ..., "since": year or null} or null')
    since = value.get("since")
    if since is not None and (isinstance(since, bool) or not isinstance(since, int)
                              or not 1900 <= since <= 2100):
        raise ValueError("ceo.since must be a year or null")
    return {"name": _text(value.get("name"), "ceo.name"), "since": since}


def _pay(value):
    out = []
    for i, p in enumerate(_list(value, "ceo_pay", MAX_PAY_YEARS)):
        fy = p.get("fy")
        if not isinstance(fy, str) or not _FY.match(fy.strip()):
            raise ValueError(f'ceo_pay[{i}].fy must look like "FY2025"')
        total = _number(p.get("total_usd"), f"ceo_pay[{i}].total_usd", 0)
        if total is None:
            raise ValueError(f"ceo_pay[{i}].total_usd is required")
        out.append({"fy": fy.strip(), "total_usd": total,
                    "stock_pct": _number(p.get("stock_pct"), f"ceo_pay[{i}].stock_pct", 0, 100)})
    return out


def _changes(value):
    return [{"date": _iso_date(c.get("date"), f"changes[{i}].date"),
             "person": _text(c.get("person"), f"changes[{i}].person"),
             "role": _text(c.get("role"), f"changes[{i}].role"),
             "action": _text(c.get("action"), f"changes[{i}].action")}
            for i, c in enumerate(_list(value, "changes", MAX_CHANGES))]


BIO_MAX = 300


def _ceo_bio(value):
    """{"background", "reputation"} or None (optional, added 2026-10-08)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('ceo_bio must be {"background": ..., "reputation": ...} or null')
    return {"background": _text(value.get("background"), "ceo_bio.background", max_len=BIO_MAX),
            "reputation": _text(value.get("reputation"), "ceo_bio.reputation", max_len=BIO_MAX)}


def _guidance_record(value):
    """{"met", "total"} or None (optional, added 2026-10-08)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('guidance_record must be {"met": n, "total": n} or null')
    met = _count(value.get("met"), "guidance_record.met")
    total = _count(value.get("total"), "guidance_record.total")
    if met is None or not total or met > total:
        raise ValueError("guidance_record needs 0 <= met <= total and total >= 1")
    return {"met": met, "total": total}


def parse_facts(facts):
    if not isinstance(facts, dict):
        raise ValueError("facts must be an object")
    return {
        "ceo": _ceo(facts.get("ceo")),
        "insider_ownership_pct": _number(facts.get("insider_ownership_pct"),
                                         "insider_ownership_pct", 0, 100),
        "ceo_ownership_pct": _number(facts.get("ceo_ownership_pct"), "ceo_ownership_pct", 0, 100),
        "ceo_shares": _count(facts.get("ceo_shares"), "ceo_shares"),
        "ceo_bio": _ceo_bio(facts.get("ceo_bio")),
        "insider_net_12m_usd": _number(facts.get("insider_net_12m_usd"), "insider_net_12m_usd"),
        "buyers": _count(facts.get("buyers"), "buyers"),
        "sellers": _count(facts.get("sellers"), "sellers"),
        "planned_sell_pct": _number(facts.get("planned_sell_pct"), "planned_sell_pct", 0, 100),
        "ceo_pay": _pay(facts.get("ceo_pay")),
        "changes": _changes(facts.get("changes")),
        "guidance_record": _guidance_record(facts.get("guidance_record")),
        "as_of": _iso_date(facts.get("as_of"), "facts.as_of"),
        "source": _text(facts.get("source"), "facts.source", max_len=SOURCE_MAX),
    }


def parse_management_cards(content, card_set=None):
    """{"cards", "facts"} validated, or ValueError saying what is wrong. With
    the default card set a save must hold all four questions."""
    result = qc.parse(card_set or MANAGEMENT, content)
    for card in result["cards"]:
        card["summary"] = html.unescape(card["summary"])
        card["points"] = [{"label": html.unescape(p["label"]), "text": html.unescape(p["text"])}
                          for p in card["points"]]
    data = _load_json(content)
    if data.get("facts") is None:
        raise ValueError("facts is required")
    result["facts"] = parse_facts(data["facts"])
    result["card_set"] = card_set or MANAGEMENT
    return result


def parse_for_display(content):
    """parse_management_cards, falling back to the three-question set of cards
    saved before 2026-10-08."""
    try:
        return parse_management_cards(content)
    except ValueError as current_error:
        try:
            return parse_management_cards(content, LEGACY_MANAGEMENT)
        except ValueError:
            raise current_error from None
