"""Risk tab, the figures beside the AI's risk cards (owner request 2026-10-22).

Two blocks, all from reported numbers and stored prices, none written by
the AI:

  Balance sheet   net debt / EBITDA, years of free cash flow to repay it,
                  EBIT / interest, and the net debt itself. Net debt is the
                  Financials tab's definition (adjusted debt: long + short
                  debt, operating and finance leases, pension; minus cash), and
                  the net-debt bands are the Robustness axis's, so the three
                  places never disagree.
  Price risk      from ten years of daily closes: the deepest fall and how
                  long it took to recover, how far below its high it is now,
                  and the worst calendar year.

(A currency line was tried and dropped the same day at the owner's request.)

Pure HTML builders in the question_cards house style: every dynamic text
through qc.esc, one-line style blocks, CSS tooltips.
"""

from datetime import date

import overview_metrics as om
import question_cards as qc
from overview_page import CARD_STYLE, tip_box

DASH = om.DASH
GOOD, MID, BAD = "good", "mid", "bad"

RISK_STYLE = """<style>
.rk-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;align-items:stretch}
@media (min-width:800px){.rk-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.rk-tiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
.rk-val{font-size:22px;font-weight:700;color:var(--text);line-height:1.2;margin:2px 0;white-space:nowrap}
.rk-val.rk-good{color:var(--green, #2e7d32)}
.rk-val.rk-mid{color:#c79a3a}
.rk-val.rk-bad{color:var(--red, #c0603f)}
.rk-cap{font-size:12px;color:var(--text-muted);line-height:1.35}
.rk-note{margin-top:14px;font-size:12.5px;color:var(--text-muted);line-height:1.45}
</style>"""

HELP = {
    "Net debt / EBITDA": "Years of operating cash profit (EBITDA) the net debt equals. Up to 1x "
                         "is robust, 1-2x fine, above 2x a real burden; net cash is the "
                         "safest. Same bands as the Robustness table.",
    "Net debt / FCF": "Years of free cash flow it would take to pay the net debt off. Up to "
                      "3 years is comfortable, above 6 heavy.",
    "EBIT / Interest": "How many times operating profit covers the interest bill. Above 8x is "
                       "very safe; below 3x is tight.",
    "Net debt": "Adjusted debt (long and short debt, operating and finance leases, pension "
                "shortfall) minus cash, as on the Financials tab.",
    "Deepest fall": "The largest drop from a high to a later low in ten years of daily "
                    "closes, in the currency it trades in.",
    "Below its high": "How far today's price is under the highest close of the last ten "
                      "years, and under the high of the last twelve months.",
    "Worst year": "The weakest full calendar year, close to close.",
    "Recovery": "How long the deepest fall took to climb back to the old high.",
}


# ── balance sheet ─────────────────────────────────────────────────────

def _latest_index(fund):
    """Index of the latest year with operating income and debt data."""
    years = fund.get("years") or []
    for i in range(len(years) - 1, -1, -1):
        oi = (fund.get("operating_income") or [None] * len(years))[i]
        debt = (fund.get("total_debt") or [None] * len(years))[i]
        if oi is not None and debt is not None:
            return i
    return None


def _at(fund, key, i):
    seq = fund.get(key) or []
    return seq[i] if i is not None and i < len(seq) else None


def balance_metrics(fund, income=None) -> dict:
    """The balance-sheet figures for the latest complete fiscal year.

    Keys: fy, net_debt, adj_debt, cash, nd_ebitda, nd_fcf_years, ebit_interest.
    None where a figure is missing; nd_ebitda/nd_fcf_years are None for net
    cash (the tile says "net cash" instead of a negative multiple)."""
    fund = fund or {}
    i = _latest_index(fund)
    out = dict.fromkeys(("fy", "net_debt", "adj_debt", "cash", "nd_ebitda",
                         "nd_fcf_years", "ebit_interest"))
    if i is None:
        return out
    fy = (fund.get("years") or [])[i]
    adj = sum((_at(fund, k, i) or 0.0) for k in (
        "total_debt", "short_term_debt", "operating_lease_liabilities",
        "finance_lease_liabilities", "pension_liabilities"))
    cash = _at(fund, "cash", i)
    oi = _at(fund, "operating_income", i)
    da = _at(fund, "da", i)
    fcf = _at(fund, "fcf", i)
    out.update(fy=fy, adj_debt=adj, cash=cash)
    if cash is not None:
        nd = adj - cash
        out["net_debt"] = nd
        ebitda = (oi + (da or 0.0)) if oi is not None else None
        if nd > 0 and ebitda and ebitda > 0:
            out["nd_ebitda"] = nd / ebitda
        if nd > 0 and fcf and fcf > 0:
            out["nd_fcf_years"] = nd / fcf
    interest = None
    if income:
        years = income.get("years") or []
        if fy in years:
            interest = (income.get("interest_expense") or [None] * len(years))[years.index(fy)]
    if oi is not None and interest not in (None, 0):
        out["ebit_interest"] = oi / abs(interest)
    return out


def _tone_nd_ebitda(m):
    if m["net_debt"] is not None and m["net_debt"] <= 0:
        return GOOD
    v = m["nd_ebitda"]
    if v is None:
        return None
    return GOOD if v <= 1 else MID if v <= 2 else BAD


def _tone_nd_fcf(m):
    if m["net_debt"] is not None and m["net_debt"] <= 0:
        return GOOD
    v = m["nd_fcf_years"]
    if v is None:
        return None
    return GOOD if v <= 3 else MID if v <= 6 else BAD


def _tone_cover(v):
    if v is None:
        return None
    return GOOD if v >= 8 else MID if v >= 3 else BAD


def _help(label):
    text = HELP.get(label)
    return f'<span class="ov-help" tabindex="0">?{tip_box(text)}</span>' if text else ""


def _tile(label, value, caption, tone=None):
    cls = f"rk-val rk-{tone}" if tone else "rk-val"
    return (f'<div><div class="ov-lbl">{qc.esc(label.upper())}{_help(label)}</div>'
            f'<div class="{cls}">{qc.esc(value)}</div>'
            f'<div class="rk-cap">{qc.esc(caption)}</div></div>')


def _card(title, body):
    return f'<div class="ov-card"><div class="ov-ctitle">{qc.esc(title)}</div>{body}</div>'


def balance_card_html(m) -> str:
    net_cash = m["net_debt"] is not None and m["net_debt"] <= 0
    fy = f"FY{m['fy']}" if m["fy"] else "latest year"
    if m["net_debt"] is None:
        nd_ebitda = nd_fcf = DASH
    elif net_cash:
        nd_ebitda = nd_fcf = "Net cash"
    else:
        nd_ebitda = DASH if m["nd_ebitda"] is None else f"{m['nd_ebitda']:.1f}x"
        nd_fcf = DASH if m["nd_fcf_years"] is None else f"{m['nd_fcf_years']:.1f} yrs"
    cover = DASH if m["ebit_interest"] is None else f"{m['ebit_interest']:.0f}x"
    amount = DASH if m["net_debt"] is None else om.fmt_money_m(abs(m["net_debt"]))
    tiles = [
        _tile("Net debt / EBITDA", nd_ebitda, fy, _tone_nd_ebitda(m)),
        _tile("Net debt / FCF", nd_fcf, "years of free cash flow", _tone_nd_fcf(m)),
        _tile("EBIT / Interest", cover, "times the interest bill", _tone_cover(m["ebit_interest"])),
        _tile("Net cash" if net_cash else "Net debt", amount,
              "adjusted debt − cash", GOOD if net_cash else None),
    ]
    return _card("Balance sheet", f'<div class="rk-tiles">{"".join(tiles)}</div>')


# ── price risk ────────────────────────────────────────────────────────

def _day(d):
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def drawdown_stats(closes) -> dict | None:
    """From [(day, close), ...] ascending: the deepest fall (pct, peak, trough,
    recovered day or None, recovery_days), the current fall from the window's
    high and from the 12-month high, and the worst full calendar year.
    None with fewer than ~a year of closes."""
    pts = [(_day(d), float(c)) for d, c in closes or [] if c and float(c) > 0]
    if len(pts) < 200:
        return None
    peak_day, peak = pts[0]
    best = (0.0, None, None)                       # (fall, peak_day, trough_day)
    for d, c in pts:
        if c > peak:
            peak_day, peak = d, c
        fall = c / peak - 1
        if fall < best[0]:
            best = (fall, peak_day, d)
    max_dd, dd_peak, dd_trough = best
    recovered = None
    if dd_peak is not None:
        old_high = dict(pts)[dd_peak]
        recovered = next((d for d, c in pts if d > dd_trough and c >= old_high), None)

    last_day, last = pts[-1]
    high = max(c for _, c in pts)
    year_ago = date(last_day.year - 1, last_day.month, min(last_day.day, 28))
    high_12m = max(c for d, c in pts if d >= year_ago)

    year_end = {}
    for d, c in pts:
        year_end[d.year] = c
    years = sorted(year_end)
    full = [y for y in years[1:] if y < last_day.year]
    worst = min(((year_end[y] / year_end[y - 1] - 1, y) for y in full
                 if y - 1 in year_end), default=None)
    return {
        "max_dd": max_dd, "peak_day": dd_peak, "trough_day": dd_trough,
        "recovered_day": recovered,
        "recovery_days": (recovered - dd_trough).days if recovered else None,
        "from_high": last / high - 1, "from_12m_high": last / high_12m - 1,
        "worst_year": worst[1] if worst else None,
        "worst_year_ret": worst[0] if worst else None,
        "since": pts[0][0],
    }


def _tone_fall(v, bad=-0.5, mid=-0.3):
    if v is None:
        return None
    return BAD if v <= bad else MID if v <= mid else GOOD


def _pct(v):
    return DASH if v is None else f"{v * 100:+.0f}%".replace("+0%", "0%")


def _months(days):
    if days is None:
        return None
    m = round(days / 30.44)
    return f"{m // 12} yr {m % 12} mo" if m >= 12 else f"{m} mo"


def price_card_html(s) -> str:
    if not s:
        body = '<div class="rk-note">Not enough price history yet.</div>'
        return _card("Price risk", body)
    span = f"{s['peak_day']:%b %Y} → {s['trough_day']:%b %Y}" if s["peak_day"] else ""
    if s["recovered_day"]:
        rec, rec_cap, rec_tone = (_months(s["recovery_days"]),
                                  f"back at the old high {s['recovered_day']:%b %Y}", None)
    else:
        rec, rec_cap, rec_tone = "Not yet", "still below the old high", MID
    tiles = [
        _tile("Deepest fall", _pct(s["max_dd"]), span, _tone_fall(s["max_dd"])),
        _tile("Recovery", rec, rec_cap, rec_tone),
        _tile("Below its high", _pct(s["from_high"]),
              f"{_pct(s['from_12m_high'])} vs 12-month high",
              _tone_fall(s["from_high"], bad=-0.4, mid=-0.2)),
        _tile("Worst year", _pct(s["worst_year_ret"]),
              str(s["worst_year"]) if s["worst_year"] else "no full year yet",
              _tone_fall(s["worst_year_ret"], bad=-0.4, mid=-0.2)),
    ]
    note = (f'<div class="rk-note">Daily closes since {s["since"]:%b %Y}, '
            f'in the currency it trades in.</div>')
    return _card("Price risk", f'<div class="rk-tiles">{"".join(tiles)}</div>{note}')


def risk_figures_section_html(fund, income, closes) -> str:
    cards = [balance_card_html(balance_metrics(fund, income)),
             price_card_html(drawdown_stats(closes))]
    grid = f'<div class="rk-grid">{"".join(cards)}</div>'
    return qc.css(CARD_STYLE, RISK_STYLE) + qc.section_html("In numbers", grid)
