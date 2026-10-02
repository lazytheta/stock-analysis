"""Quarterly revenue and diluted EPS from SEC companyfacts, for the Earnings tab.

Pure parsing over the companyfacts JSON gather_data already fetches; only
fetch_quarterly_results touches the network (through gather_data's throttled,
User-Agent-carrying EDGAR fetch). No Yahoo.

Rules:
- A quarter is a fact whose start→end spans 80–100 days (13- and 14-week
  quarters included); a fiscal year spans 300–380 days. Year-to-date
  six- and nine-month figures are neither and are ignored.
- Per end date the latest filing wins: a 10-Q's comparative column restates
  last year's quarter, and that restatement (e.g. after a split) is the one to
  keep.
- Q4 is rarely tagged on its own. When it isn't, Q4 = the fiscal-year value
  minus Q1–Q3 of that year, and only when all three are there.
- Revenue tags in gather_data.parse_financials' priority order; the tag with
  the most recent quarter leads and the others fill its gaps (gather_data's
  _try_tags rule, per quarter). EPS: diluted, else basic-and-diluted, else
  basic.
- Fiscal labels ("Q2 FY2026") come from the filer's own fiscal-year ends (the
  >300-day facts); a fiscal year that hasn't closed yet is extrapolated one
  year on from the last one. FY is named after the calendar year it ends in,
  as gather_data does. Without any fiscal-year fact: "Q ending 2026-06-30".
- Splits: a jump of 1.5x or more (or a fall to 2/3 or less) in the diluted
  weighted share count from one quarter to the next marks a split; EPS of
  every earlier quarter is divided by the split ratio and flagged
  split_adjusted, so per-share growth is not a split artefact.

Units: revenue in raw USD, EPS in USD per share, growth as fractions.
"""

from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

REVENUE_TAGS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "SalesRevenueNet",
)
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",
            "EarningsPerShareBasic")
SHARE_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding",
              "WeightedAverageNumberOfSharesOutstandingBasic")

N_QUARTERS = 12
QUARTER_DAYS = (80, 100)
ANNUAL_DAYS = (300, 380)          # a fiscal year; longer spans are cumulative totals
DAYS_PER_QUARTER = 91.3
FY_SLACK = timedelta(days=7)      # 52/53-week calendars drift a few days
YOY_DAYS = (351, 379)             # "same quarter a year earlier"
SPLIT_JUMP = 1.5
SPLIT_RATIOS = (1.5, 2, 3, 4, 5, 6, 7, 8, 10, 15, 20, 25, 30, 40, 50)


# ── reading facts ──────────────────────────────────────────────────────────

def _day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _value(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if x == x and abs(x) != float("inf") else None


def _entries(facts, tag, unit):
    """Usable (start, end, value, filed) tuples for one tag and unit."""
    try:
        raw = facts["facts"]["us-gaap"][tag]["units"][unit]
    except (KeyError, TypeError):
        return []
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        start, end, val = _day(e.get("start")), _day(e.get("end")), _value(e.get("val"))
        if start is None or end is None or val is None or end <= start:
            continue
        out.append((start, end, val, str(e.get("filed") or "")))
    return out


def _latest_by_end(entries):
    """{end: (start, end, value)}, the latest filing winning per end date (a
    tie keeps the later entry)."""
    best = {}
    for start, end, val, filed in entries:
        if end not in best or filed >= best[end][1]:
            best[end] = ((start, end, val), filed)
    return {end: v for end, (v, _) in best.items()}


def _split(entries):
    """(quarters, years), each {end: (start, end, value)}."""
    lo, hi = QUARTER_DAYS
    quarters = _latest_by_end(e for e in entries if lo <= (e[1] - e[0]).days <= hi)
    years = _latest_by_end(e for e in entries
                           if ANNUAL_DAYS[0] < (e[1] - e[0]).days <= ANNUAL_DAYS[1])
    return quarters, years


def _with_q4(quarters, years, basis=None):
    """{end: {"value", "start", "derived"}}, adding Q4 = year − Q1..Q3 where
    Q4 isn't reported on its own and all three earlier quarters are.

    `basis(fy_end, quarter_ends)` returns the factor to divide Q1–Q3 by
    before subtracting (per-share series: a fiscal-year EPS restated for a
    later split next to quarters that were not)."""
    out = {end: {"start": s, "value": v, "derived": False}
           for end, (s, _e, v) in quarters.items()}
    for fy_end, (fy_start, _e, fy_val) in years.items():
        if any(abs((end - fy_end).days) <= FY_SLACK.days for end in quarters):
            continue                                   # Q4 reported standalone
        inside = sorted(
            (end, q) for end, q in quarters.items()
            if q[0] >= fy_start - FY_SLACK and end < fy_end - timedelta(days=30))
        if len(inside) != 3:
            continue
        k = basis(fy_end, [end for end, _q in inside]) if basis else 1.0
        q3_end = inside[-1][0]
        out[fy_end] = {"start": q3_end + timedelta(days=1),
                       "value": fy_val - sum(q[2] / k for _end, q in inside),
                       "derived": True}
    return out


def _share_basis(shares):
    """A `basis` for _with_q4 from the raw (quarters, years) share counts: the
    split ratio between the fiscal year's weighted share count and the mean
    of its Q1–Q3 counts, 1.0 when they agree or a count is missing."""
    quarters, years = shares

    def basis(fy_end, quarter_ends):
        year = years.get(fy_end)
        counts = [quarters[e][2] for e in quarter_ends if e in quarters]
        if not year or len(counts) != len(quarter_ends) or min(counts) <= 0 or year[2] <= 0:
            return 1.0
        r = year[2] / (sum(counts) / len(counts))
        if r >= SPLIT_JUMP:
            return _split_ratio(r)
        if r <= 1 / SPLIT_JUMP:
            return 1 / _split_ratio(1 / r)
        return 1.0
    return basis


def _raw_shares(facts):
    """(quarters, years) of the first share tag that has any."""
    for tag in SHARE_TAGS:
        quarters, years = _split(_entries(facts, tag, "shares"))
        if quarters or years:
            return quarters, years
    return {}, {}


def _series(facts, tags, unit, basis=None):
    """Quarter series for the first tag with the most recent quarter, gaps
    filled from the other tags in priority order."""
    per_tag = []
    for tag in tags:
        quarters, years = _split(_entries(facts, tag, unit))
        series = _with_q4(quarters, years, basis)
        if series:
            per_tag.append(series)
    if not per_tag:
        return {}
    lead = max(per_tag, key=lambda s: max(s))           # first max = priority order
    merged = dict(lead)
    for series in per_tag:
        for end, v in series.items():
            merged.setdefault(end, v)
    return merged


def _fiscal_year_ends(facts, tag_units):
    ends = set()
    for tags, unit in tag_units:
        for tag in tags:
            ends.update(_split(_entries(facts, tag, unit))[1])
    return sorted(ends)


# ── labels, splits, growth ─────────────────────────────────────────────────

def _shift_year(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:                                 # 29 February
        return d.replace(year=d.year + years, day=28)


def fiscal_label(end, fy_ends):
    """"Q2 FY2026" from the filer's fiscal-year ends, else "Q ending <end>"."""
    fallback = f"Q ending {end.isoformat()}"
    if not fy_ends:
        return fallback
    candidates = list(fy_ends) + [_shift_year(fy_ends[-1], k) for k in (1, 2)]
    fye = next((d for d in sorted(candidates) if d >= end - FY_SLACK), None)
    if fye is None or (fye - end).days > 370:
        return fallback
    k = round((fye - end).days / DAYS_PER_QUARTER)
    if not 0 <= k <= 3:
        return fallback
    return f"Q{4 - k} FY{fye.year}"


def _split_ratio(r):
    """The nearest common split ratio to a share-count jump `r` (> 1), or `r`
    itself when none is within 10%."""
    nearest = min(SPLIT_RATIOS, key=lambda s: abs(s - r) / s)
    return nearest if abs(nearest - r) / nearest <= 0.10 else r


def _split_factors(shares):
    """{end: factor} to divide each quarter's EPS by, from jumps in the share
    series (ascending ends; quarters without a count are skipped)."""
    ends = sorted(e for e, v in shares.items() if v["value"] and v["value"] > 0
                  and not v["derived"])
    factors, cumulative = {}, 1.0
    for prev, cur in reversed(list(pairwise(ends))):
        r = shares[cur]["value"] / shares[prev]["value"]
        if r >= SPLIT_JUMP:
            cumulative *= _split_ratio(r)
        elif r <= 1 / SPLIT_JUMP:
            cumulative /= _split_ratio(1 / r)
        if cumulative != 1.0:
            factors[prev] = cumulative
    return factors


def _factor_for(end, factors, share_ends):
    """The split factor for a quarter: that of the first quarter on or after
    it with a share count (so a quarter without its own count, e.g. a derived
    Q4, follows its neighbour)."""
    later = [e for e in share_ends if e >= end]
    if not later:
        return 1.0
    return factors.get(min(later), 1.0)


def _growth(cur, prev):
    if cur is None or prev is None or prev <= 0:
        return None
    return cur / prev - 1.0


def _year_before(end, ends):
    lo, hi = YOY_DAYS
    hits = [e for e in ends if lo <= (end - e).days <= hi]
    return min(hits, key=lambda e: abs((end - e).days - 365)) if hits else None


# ── public ─────────────────────────────────────────────────────────────────

def quarterly_results(facts, n: int = N_QUARTERS) -> list[dict]:
    """The last `n` quarters, oldest first: {end, fiscal_label, revenue,
    revenue_yoy, eps, eps_yoy, derived_q4, split_adjusted}. Missing values are
    None; bad input gives []. Never raises."""
    try:
        return _quarterly_results(facts, n)
    except Exception:
        return []


def _quarterly_results(facts, n):
    if not isinstance(facts, dict) or not isinstance(facts.get("facts"), dict):
        return []
    revenue = _series(facts, REVENUE_TAGS, "USD")
    eps = _series(facts, EPS_TAGS, "USD/shares", basis=_share_basis(_raw_shares(facts)))
    shares = _series(facts, SHARE_TAGS, "shares")
    fy_ends = _fiscal_year_ends(facts, ((REVENUE_TAGS, "USD"), (EPS_TAGS, "USD/shares")))

    factors = _split_factors(shares)
    share_ends = sorted(e for e, v in shares.items() if not v["derived"])
    eps_adj = {}
    for end, v in eps.items():
        k = _factor_for(end, factors, share_ends) if factors else 1.0
        eps_adj[end] = (v["value"] / k, k != 1.0)

    ends = sorted(set(revenue) | set(eps))
    rows = []
    for end in ends:
        rev = revenue.get(end, {}).get("value")
        e, adjusted = eps_adj.get(end, (None, False))
        prior = _year_before(end, ends)
        rows.append({
            "end": end.isoformat(),
            "fiscal_label": fiscal_label(end, fy_ends),
            "revenue": rev,
            "revenue_yoy": _growth(rev, revenue.get(prior, {}).get("value")) if prior else None,
            "eps": e,
            "eps_yoy": _growth(e, eps_adj.get(prior, (None, False))[0]) if prior else None,
            "derived_q4": bool(revenue.get(end, eps.get(end, {})).get("derived")),
            "split_adjusted": adjusted,
        })
    return rows[-max(int(n), 0):] if n else []


def fetch_quarterly_results(ticker, n: int = N_QUARTERS) -> list[dict]:
    """quarterly_results for a ticker from EDGAR companyfacts. A ticker SEC
    doesn't know (a foreign listing) gives []; network errors propagate so a
    caller's cache never stores an outage."""
    import gather_data
    try:
        cik = gather_data.get_cik(str(ticker).upper())
    except ValueError:
        return []
    return quarterly_results(gather_data.fetch_company_facts(cik), n)
