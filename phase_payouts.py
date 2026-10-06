"""Cash-flow helpers shared by the Capital Return tab (_cf, _mcap, _dps_at)
and the Revenue & Operating Cash Flow series and caption (Financials tab).

The Phase tab's payout cards that this module used to build were removed on
2026-10-06: Capital Return tells buybacks and dividend in full.

Fiscal year and the "missing line item in a fiscal year that exists = 0"
rule are `overview_metrics`'s (reused here, not duplicated); money/percent
formatting is `overview_metrics.fmt_money_m` / `fmt_pct`.
"""

from __future__ import annotations

import overview_metrics as om

def _cf(statement, key, year):
    """`overview_metrics.compute`'s cf() rule, generalised to any statement:
    an untagged line in a fiscal year the statement otherwise covers is 0,
    not unknown."""
    if year is None:
        return None
    amount = om._at(statement, key, year)
    has_year = bool(statement) and year in (statement.get("years") or [])
    return 0.0 if amount is None and has_year else amount


def _mcap(fund, price):
    if not price or price <= 0:
        return None
    shares = om.shares_at_fiscal_year(fund)
    if not shares or shares <= 0:
        return None
    return price * shares / 1e6


def _dps_at(fund, cashflow, year):
    """Dividend per share at `year`: total dividends paid (cash-flow
    statement, $M) over the split-adjusted share count. The tagged
    dividends_per_share is not split-adjusted, so it is never used for
    growth."""
    if year is None:
        return None
    paid = _cf(cashflow, "dividends_paid", year)
    shares = om._at(fund, "shares", year)
    if paid is not None and shares:
        return abs(paid) * 1e6 / shares
    return None


def revenue_ocf_series(fund, years: int):
    """The last `years` fiscal years that have revenue, with revenue and
    cfo ($M). Years with no revenue reported are skipped entirely, so a
    gap in coverage never shows as a $0 bar."""
    fund = fund or {}
    all_years = fund.get("years") or []
    revenue = fund.get("revenue") or []
    pairs = sorted((y, r) for y, r in zip(all_years, revenue) if r is not None)
    pairs = pairs[-years:] if years > 0 else []
    out_years = [y for y, _ in pairs]
    out_revenue = [r for _, r in pairs]
    out_cfo = [om._at(fund, "cfo", y) for y in out_years]
    return out_years, out_revenue, out_cfo


def revenue_ocf_caption(years, revenue, cfo) -> str:
    """`Revenue grew {cagr} a year over {span} years; operating cash flow was
    positive in {k} of {n} years.` span is the calendar span
    years[-1] - years[0], so a gap in coverage does not inflate the CAGR.
    The growth clause is dropped when the first or last revenue isn't
    positive. k counts positive cfo among years where cfo is known; when
    some years lack cfo the clause says "of {m} years with data"."""
    if len(years) < 2:
        return ""
    span = years[-1] - years[0]

    first_rev, last_rev = revenue[0], revenue[-1]
    growth_ok = (span >= 1 and first_rev is not None and first_rev > 0
                 and last_rev is not None and last_rev > 0)

    known = [c for c in cfo if c is not None]
    k = sum(1 for c in known if c > 0)
    if not known:
        ocf_clause = ""
    elif len(known) < len(years):
        ocf_clause = f"operating cash flow was positive in {k} of {len(known)} years with data."
    else:
        ocf_clause = f"operating cash flow was positive in {k} of {len(years)} years."

    if not growth_ok:
        return ocf_clause[:1].upper() + ocf_clause[1:]

    growth = f"Revenue grew {om.fmt_pct((last_rev / first_rev) ** (1.0 / span) - 1.0)} a year over {span} years"
    return f"{growth}; {ocf_clause}" if ocf_clause else f"{growth}."
