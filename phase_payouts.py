"""Phase tab — Payouts: two computed flip cards (buybacks, dividend), plus
the Revenue & Operating Cash Flow series and its caption.

Unlike Moat/Risk cards, these are fully computed from EDGAR data, never an
LLM: this module builds the card dicts itself and hands them straight to
`question_cards.flip_card_html`, never through `question_cards.parse`.

Fiscal year and the "missing line item in a fiscal year that exists = 0"
rule are `overview_metrics`'s (reused here, not duplicated); money/percent
formatting is `overview_metrics.fmt_money_m` / `fmt_pct`.
"""

from __future__ import annotations

import overview_metrics as om
import question_cards as qc

TITLE = "Payouts"
ITEMS = (
    ("buybacks", "Buybacks", "Are they buying back stock?",
     ("No", "Yes & diluting", "Yes & shrinking")),
    ("dividend", "Dividend", "Do they pay a dividend?",
     ("No", "Yes & stable", "Yes & growing")),
)
PAYOUTS = qc.CardSet(TITLE, ITEMS, None)

BUYBACK_WINDOW = 5
DIVIDEND_GROWTH_YEARS = 3
DIVIDEND_GROWTH_MIN = 0.02
SHRINK_MIN = 0.01


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


def _fmt_shares(x):
    return om.DASH if x is None else f"{x / 1e6:.1f}M"


def _fmt_dollars(x):
    return om.DASH if x is None else f"${x:.2f}"


def _last_n_fiscal_years(fy, n):
    return [] if fy is None else [fy - i for i in range(n - 1, -1, -1)]


def _dps_at(fund, cashflow, year):
    """Dividend per share at `year`: the tagged figure, else the aggregate
    dividend paid (cash-flow statement, $M) divided by shares outstanding."""
    if year is None:
        return None
    dps = om._at(fund, "dividends_per_share", year)
    if dps is not None:
        return dps
    paid = _cf(cashflow, "dividends_paid", year)
    shares = om._at(fund, "shares", year)
    if paid is not None and shares:
        return abs(paid) * 1e6 / shares
    return None


def buyback_card(fund, cashflow, price) -> dict:
    fund = fund or {}
    cashflow = cashflow or {}
    fy = om._fiscal_year(fund)
    years = _last_n_fiscal_years(fy, BUYBACK_WINDOW)
    amounts = [_cf(cashflow, "stock_buybacks", y) for y in years]
    total = sum(abs(a) for a in amounts if a is not None)
    last = amounts[-1] if amounts else None
    last_abs = 0.0 if last is None else abs(last)

    start = om._at(fund, "shares", fy - BUYBACK_WINDOW) if fy is not None else None
    end = om._at(fund, "shares", fy) if fy is not None else None
    pct = (end - start) / start if (end is not None and start not in (None, 0)) else None

    if total <= 0:
        pick = 0
        summary = "No buybacks in the last five years."
    elif pct is not None and pct <= -SHRINK_MIN:
        pick = 2
        summary = (f"Shrinking the share count: {abs(pct) * 100:.1f}% "
                    f"fewer shares than FY{fy - BUYBACK_WINDOW}.")
    elif pct is not None and pct > 0:
        pick = 1
        summary = (f"Buying back, but issuing faster: {pct * 100:.1f}% "
                    f"more shares than FY{fy - BUYBACK_WINDOW}.")
    else:
        pick = 1
        summary = "Buying back stock; share count roughly flat."

    mcap = _mcap(fund, price)
    buyback_yield = last_abs / mcap if mcap else None

    share_text = om.DASH
    if fy is not None:
        share_text = (f"{_fmt_shares(start)} in FY{fy - BUYBACK_WINDOW} → "
                       f"{_fmt_shares(end)} in FY{fy} ({om.fmt_pct(pct, signed=True)})")

    return {
        "source": "buybacks",
        "pick": pick,
        "summary": summary,
        "points": [
            {"label": "Bought back",
             "text": (f"{om.fmt_money_m(total)} over the last 5 years, "
                       f"{om.fmt_money_m(last_abs)} in FY{fy}." if fy is not None else om.DASH)},
            {"label": "Share count", "text": share_text},
            {"label": "Buyback yield",
             "text": f"{om.fmt_pct(buyback_yield)} of market cap." if mcap else om.DASH},
        ],
    }


def dividend_card(fund, cashflow, income_or_none, price, net_cash_m) -> dict:
    fund = fund or {}
    cashflow = cashflow or {}
    income_or_none = income_or_none or {}
    fy = om._fiscal_year(fund)

    last_paid = _cf(cashflow, "dividends_paid", fy) if fy is not None else None
    last_paid_abs = 0.0 if last_paid is None else abs(last_paid)
    pays = fy is not None and last_paid_abs > 0

    dps_now = _dps_at(fund, cashflow, fy) if fy is not None else None
    dps_then = _dps_at(fund, cashflow, fy - DIVIDEND_GROWTH_YEARS) if fy is not None else None
    rate = None
    if dps_now is not None and dps_then is not None and dps_now > 0 and dps_then > 0:
        rate = (dps_now / dps_then) ** (1.0 / DIVIDEND_GROWTH_YEARS) - 1.0

    if not pays:
        pick = 0
        summary = "No dividend: all cash returned through buybacks or reinvested."
    elif rate is not None and rate >= DIVIDEND_GROWTH_MIN:
        pick = 2
        summary = f"Dividend growing {rate * 100:.1f}% a year over 3 years."
    else:
        pick = 1
        summary = "Dividend paid, roughly flat."

    mcap = _mcap(fund, price)
    div_yield = last_paid_abs / mcap if (mcap and pays) else None

    net_income = om._at(fund, "net_income", fy) if fy is not None else None
    if net_income is None:
        net_income = om._at(income_or_none, "net_income", fy)
    payout_ratio = (last_paid_abs / net_income
                     if (pays and net_income is not None and net_income > 0) else None)

    if pays:
        points = [
            {"label": "Dividend per share", "text": f"{_fmt_dollars(dps_now)} in FY{fy}."},
            {"label": "Yield",
             "text": f"{om.fmt_pct(div_yield)} at the current price." if mcap else om.DASH},
            {"label": "Payout ratio", "text": f"{om.fmt_pct(payout_ratio)} of net income."},
        ]
    else:
        buyback_last = _cf(cashflow, "stock_buybacks", fy) if fy is not None else None
        buyback_last_abs = None if buyback_last is None else abs(buyback_last)
        capex_last = _cf(cashflow, "capex", fy) if fy is not None else None
        capex_last_abs = None if capex_last is None else abs(capex_last)
        points = [
            {"label": "Cash used instead",
             "text": (f"{om.fmt_money_m(buyback_last_abs)} in buybacks in FY{fy}."
                       if fy is not None else om.DASH)},
            {"label": "Reinvestment",
             "text": (f"{om.fmt_money_m(capex_last_abs)} in capex in FY{fy}."
                       if fy is not None else om.DASH)},
            {"label": "Net cash", "text": om.fmt_money_m(net_cash_m)},
        ]

    return {"source": "dividend", "pick": pick, "summary": summary, "points": points}


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
    """`Revenue grew {cagr} a year over {n} years; operating cash flow was
    positive in {k} of {len(years)} years.` n = len(years) - 1 (the
    year-over-year steps spanning the series, used only by the growth
    clause, which is dropped when the first or last revenue isn't
    positive). `k` counts positive cfo over ALL shown years."""
    n = len(years) - 1
    if n < 1:
        return ""

    first_rev, last_rev = revenue[0], revenue[-1]
    growth_ok = first_rev is not None and first_rev > 0 and last_rev is not None and last_rev > 0

    total = len(years)
    k = sum(1 for c in cfo if c is not None and c > 0)
    ocf_clause = f"operating cash flow was positive in {k} of {total} years."

    if not growth_ok:
        return ocf_clause[0].upper() + ocf_clause[1:]

    cagr = (last_rev / first_rev) ** (1.0 / n) - 1.0
    return f"Revenue grew {om.fmt_pct(cagr)} a year over {n} years; {ocf_clause}"
