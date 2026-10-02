"""Capital Return tab: how much cash the company hands back, and how.

Fully computed from the EDGAR inputs the Overview and Phase tabs already
fetch (`fund` from gather_data.fetch_fundamentals, the cash-flow statement
from gather_data.fetch_cashflow_statement, the live price); no LLM text.

Pure functions return numbers/dicts and never touch the network. The HTML and
Plotly builders below them follow the house style (question_cards.section_html
white sections with flat inner cards, every dynamic text through
question_cards.esc, every <style> on one line, missing values as "—") and
never raise: bad input degrades to dashes.

Conventions reused rather than duplicated:
- fiscal year, `_at`, FCF fallback, percent/money formatting: overview_metrics
- "an untagged line in a fiscal year the statement covers is 0, not unknown",
  market cap and dividend-per-share from paid ÷ split-adjusted shares:
  phase_payouts (`_cf`, `_mcap`, `_dps_at`)

Units: money in $M; shares as a raw count. fetch_fundamentals has already
split-adjusted `shares` (a year-on-year jump > 1.5x scales all earlier
years), which is why per-share figures come from paid ÷ shares and never
from the tagged, unadjusted dividends_per_share.
"""

from __future__ import annotations

import plotly.graph_objects as go

import overview_metrics as om
import phase_payouts as pp
import question_cards as qc
from overview_page import CARD_STYLE, METRICS_STYLE
from phase_page import CHART_LAYOUT, PAYOUT_COLOUR, money_ticks

DASH = om.DASH
WINDOW = 10            # fiscal years in the charts and tables
RETURN_YEARS = 5       # horizon of "FCF returned" and the share-count CAGR
DIVIDEND_HORIZONS = (3, 5, 10)
FLAT_SHARES = 0.005    # |share CAGR| at or below this reads as "roughly flat"
RAISE_MIN = 0.005      # a DPS rise below 0.5% is noise from paid ÷ shares

NO_DATA = "Not enough cash-flow data to summarise capital return."
NO_DIVIDEND = "Pays no dividend."
NO_DIVIDEND_DATA = "Not enough cash-flow data to tell."

DEBT_COLOUR = "#9aa0a6"

_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"

CR_STYLE = f"""<style>
.cr-tiles{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}}
@media (max-width:900px){{.cr-tiles{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (max-width:480px){{.cr-tiles{{grid-template-columns:minmax(0,1fr)}}}}
.cr-tile{{background:{_INNER};border-radius:16px;padding:16px 18px;box-sizing:border-box;
  min-width:0}}
.cr-val{{font-size:24px;font-weight:700;color:var(--text);line-height:1.2;margin:2px 0 4px;
  white-space:nowrap}}
.cr-cap{{font-size:12px;color:var(--text-muted);line-height:1.35}}
.cr-sentence{{margin:16px 2px 0;font-size:15px;line-height:1.5;color:var(--text)}}
.cr-note{{margin:0;font-size:14px;color:var(--text-muted)}}
.cr-stats{{margin-top:10px}}
</style>"""


# ── small helpers ──────────────────────────────────────────────────────────

def _num(x):
    """A finite float, else None (bad data must not crash the page)."""
    if isinstance(x, bool):
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _abs(x):
    return None if x is None else abs(x)


def _covers(statement, year):
    return (year is not None and bool(statement)
            and year in (statement.get("years") or []))


def _fiscal_year(fund):
    try:
        return om._fiscal_year(fund or {})
    except Exception:
        return None


def _window(fy, n=WINDOW):
    return [] if fy is None else list(range(fy - n + 1, fy + 1))


def _fcf(fund, cashflow, year):
    """FCF ($M) from fundamentals (Overview's rule), else the cash-flow
    statement's own CFO + capex."""
    v = om._fcf_at(fund or {}, year)
    if v is None:
        v = om._at(cashflow, "fcf", year)
    return v


def _shares(fund, year):
    v = om._at(fund or {}, "shares", year)
    return v if v is not None and v > 0 else None


def _share_cagr(fund, fy, n=RETURN_YEARS):
    if fy is None:
        return None
    end, start = _shares(fund, fy), _shares(fund, fy - n)
    if end is None or start is None:
        return None
    return (end / start) ** (1.0 / n) - 1.0


def _year_flows(fund, cashflow, year):
    """One fiscal year's cash use, or None when the cash-flow statement does
    not cover it (unknown is not zero)."""
    if not _covers(cashflow, year):
        return None
    repay = _abs(pp._cf(cashflow, "debt_repayment", year)) or 0.0
    debt_in = pp._cf(cashflow, "debt_issuance", year) or 0.0
    buybacks = _abs(pp._cf(cashflow, "stock_buybacks", year)) or 0.0
    issuance = _abs(pp._cf(cashflow, "stock_issuance", year)) or 0.0
    return {
        "year": year,
        "dividends": _abs(pp._cf(cashflow, "dividends_paid", year)) or 0.0,
        "buybacks": buybacks,
        "issuance": issuance,
        "net_buybacks": buybacks - issuance,
        "debt_paydown": max(0.0, repay - debt_in),
        "fcf": _fcf(fund, cashflow, year),
    }


# ── pure computations ──────────────────────────────────────────────────────

def annual_flows(fund, cashflow, years: int = WINDOW) -> list[dict]:
    """Per fiscal year over the last `years`: dividends, gross buybacks,
    stock issuance, net buybacks, net debt repayment (only when positive)
    and FCF, all $M and positive. Years the cash-flow statement lacks are
    left out, never zero-filled."""
    fy = _fiscal_year(fund)
    out = []
    for y in _window(fy, years):
        row = _year_flows(fund, cashflow, y)
        if row is not None:
            out.append(row)
    return out


def missing_years(fund, cashflow, years: int = WINDOW) -> list[int]:
    """Fiscal years in the window the cash-flow statement does not cover."""
    return [y for y in _window(_fiscal_year(fund), years) if not _covers(cashflow, y)]


def headline(fund, cashflow, price) -> dict:
    """The four headline figures (fractions; None when unknown):

    shareholder_yield: (dividends + repurchases - stock issuance) in the
        last fiscal year / market cap.
    dividend_yield: last fiscal year's dividends / market cap.
    share_cagr_5y: compound yearly change in shares over five years.
    fcf_returned_5y: (dividends + gross repurchases) / FCF, both summed over
        the last five fiscal years the cash-flow statement covers; None when
        that FCF sum is not positive.

    Market cap = price x shares at the fiscal year (the Overview's figure).
    Also returns fy, market_cap_m and the five-year dividend/buyback/FCF sums
    ($M) for the summary sentence.
    """
    fund = fund or {}
    fy = _fiscal_year(fund)
    out = {"fy": fy, "market_cap_m": None, "shareholder_yield": None,
           "dividend_yield": None, "share_cagr_5y": _share_cagr(fund, fy),
           "fcf_returned_5y": None, "dividends_5y": None, "buybacks_5y": None,
           "fcf_5y": None}
    price = _num(price)
    mcap = pp._mcap(fund, price) if price else None
    out["market_cap_m"] = mcap

    last = _year_flows(fund, cashflow, fy)
    if last is not None and mcap:
        out["dividend_yield"] = last["dividends"] / mcap
        out["shareholder_yield"] = (last["dividends"] + last["net_buybacks"]) / mcap

    rows = [r for r in (_year_flows(fund, cashflow, y) for y in _window(fy, RETURN_YEARS))
            if r is not None]
    if rows:
        out["dividends_5y"] = sum(r["dividends"] for r in rows)
        out["buybacks_5y"] = sum(r["buybacks"] for r in rows)
        fcfs = [r["fcf"] for r in rows if r["fcf"] is not None]
        if fcfs:
            out["fcf_5y"] = sum(fcfs)
            # Same years top and bottom: a year without FCF drops out of both.
            returned = sum(r["dividends"] + r["buybacks"] for r in rows if r["fcf"] is not None)
            if out["fcf_5y"] > 0:
                out["fcf_returned_5y"] = returned / out["fcf_5y"]
    return out


def _share_clause(cagr):
    if cagr is None:
        return ""
    if cagr > FLAT_SHARES:
        return f"shares are being diluted about {cagr * 100:.1f}% a year"
    if cagr < -FLAT_SHARES:
        return f"the share count falls about {abs(cagr) * 100:.1f}% a year"
    return "the share count is roughly flat"


def summary_sentence(h) -> str:
    """One rule-based sentence for the headline, e.g. "Gives back about 90% of
    free cash flow, mostly through buybacks; the share count falls about
    2.1% a year." Never raises."""
    try:
        h = h or {}
        div5, buy5 = _num(h.get("dividends_5y")), _num(h.get("buybacks_5y"))
        ratio, fcf5 = _num(h.get("fcf_returned_5y")), _num(h.get("fcf_5y"))
        shares = _share_clause(_num(h.get("share_cagr_5y")))
        if div5 is None or buy5 is None:
            return f"{shares[:1].upper()}{shares[1:]}; cash-flow data is missing." if shares else NO_DATA

        if div5 <= 0 and buy5 <= 0:
            lead = "Returns no cash to shareholders: pays no dividend and does not buy back shares"
        else:
            if div5 <= 0:
                channel = "all through buybacks (it pays no dividend)"
            elif buy5 <= 0:
                channel = "all through dividends (it does not buy back shares)"
            elif buy5 >= div5:
                channel = "mostly through buybacks"
            else:
                channel = "mostly through dividends"
            if ratio is None:
                if fcf5 is not None and fcf5 <= 0:
                    lead = ("Gives back more than it earns: free cash flow over five years "
                            f"was not positive, yet it paid out, {channel}")
                else:
                    lead = f"Gives back cash, {channel}"
            elif round(ratio * 100) > 100:
                # On the rounded figure, so 100.4% never reads "more than it
                # earns: about 100%".
                lead = (f"Gives back more than it earns: about {round(ratio * 100)}% "
                        f"of free cash flow, {channel}")
            else:
                lead = f"Gives back about {round(ratio * 100)}% of free cash flow, {channel}"
        return f"{lead}; {shares}." if shares else f"{lead}."
    except Exception:
        return NO_DATA


def dividend_stats(fund, cashflow) -> dict:
    """Dividend per share over the last ten fiscal years (paid ÷ split-
    adjusted shares; None where the cash-flow statement or the share count
    is missing), its 3/5/10-year CAGR, the run of consecutive yearly raises
    ending at the fiscal year, and the payout as a share of net income and
    of FCF in the fiscal year.

    pays: True / False, or None when the fiscal year's cash flow is unknown.
    """
    fund = fund or {}
    cashflow = cashflow or {}
    fy = _fiscal_year(fund)
    out = {"fy": fy, "pays": None, "years": [], "dps": [],
           "growth": {n: None for n in DIVIDEND_HORIZONS},
           "consecutive_increases": 0, "payout_net_income": None, "payout_fcf": None}
    if fy is None:
        return out

    def dps(y):
        return pp._dps_at(fund, cashflow, y) if _covers(cashflow, y) else None

    pairs = [(y, dps(y)) for y in _window(fy)]
    pairs = [(y, v) for y, v in pairs if v is not None]
    out["years"] = [y for y, _ in pairs]
    out["dps"] = [v for _, v in pairs]

    if not _covers(cashflow, fy):
        return out
    paid = _abs(pp._cf(cashflow, "dividends_paid", fy)) or 0.0
    out["pays"] = paid > 0
    if not out["pays"]:
        return out

    now = dps(fy)
    for n in DIVIDEND_HORIZONS:
        then = dps(fy - n)
        if now and then and now > 0 and then > 0:
            out["growth"][n] = (now / then) ** (1.0 / n) - 1.0

    streak, y = 0, fy
    while True:
        cur, prev = dps(y), dps(y - 1)
        if cur is None or prev is None or prev <= 0 or cur <= prev * (1 + RAISE_MIN):
            break
        streak += 1
        y -= 1
    out["consecutive_increases"] = streak

    ni = om._at(fund, "net_income", fy)
    fcf = _fcf(fund, cashflow, fy)
    out["payout_net_income"] = paid / ni if ni is not None and ni > 0 else None
    out["payout_fcf"] = paid / fcf if fcf is not None and fcf > 0 else None
    return out


def share_count_series(fund, years: int = WINDOW):
    """(fiscal years, split-adjusted shares) over the last `years`, skipping
    years without a share count."""
    fund = fund or {}
    pairs = [(y, _shares(fund, y)) for y in _window(_fiscal_year(fund), years)]
    pairs = [(y, s) for y, s in pairs if s is not None]
    return [y for y, _ in pairs], [s for _, s in pairs]


def share_count_rows(fund, cashflow, years: int = WINDOW) -> list[dict]:
    """Per fiscal year: gross buybacks and stock issuance ($M; None when the
    cash-flow statement lacks the year) next to the year-on-year change in
    shares (fraction; None without both counts). Years with neither are
    left out."""
    fund = fund or {}
    out = []
    for y in _window(_fiscal_year(fund), years):
        flows = _year_flows(fund, cashflow, y)
        cur, prev = _shares(fund, y), _shares(fund, y - 1)
        change = cur / prev - 1.0 if cur is not None and prev is not None else None
        if flows is None and cur is None:
            continue
        out.append({"year": y,
                    "buybacks": None if flows is None else flows["buybacks"],
                    "issuance": None if flows is None else flows["issuance"],
                    "share_change": change})
    return out


def cash_use_caption(fund, cashflow) -> str:
    gaps = missing_years(fund, cashflow)
    base = ("Bars: dividends, buybacks (gross repurchases) and net debt repayment. "
            "Line: free cash flow.")
    if not gaps:
        return base
    listed = ", ".join(f"FY{y}" for y in gaps)
    return f"{base} No cash-flow data for {listed}; those years are left out, not shown as zero."


# ── figures ────────────────────────────────────────────────────────────────

def _money_axis(fig, values):
    tickvals, ticktext = money_ticks(values)
    fig.update_yaxes(rangemode="tozero", tickmode="array", tickvals=tickvals,
                     ticktext=ticktext, showgrid=True, gridwidth=1,
                     gridcolor="rgba(128,128,128,0.15)", zeroline=True,
                     zerolinecolor="rgba(128,128,128,0.35)")
    fig.update_xaxes(type="category", showgrid=False)


def cash_use_figure(rows, theme) -> go.Figure:
    """Stacked bars per fiscal year (dividends, buybacks, debt paydown) with
    free cash flow as a line, $M."""
    theme = theme or {}
    rows = rows or []
    xs = [f"FY{r['year']}" for r in rows]
    fig = go.Figure()
    for name, key, colour in (("Dividends", "dividends", theme.get("accent", "#81b29a")),
                              ("Buybacks", "buybacks", PAYOUT_COLOUR),
                              ("Debt paydown", "debt_paydown", DEBT_COLOUR)):
        ys = [r[key] for r in rows]
        fig.add_trace(go.Bar(
            x=xs, y=ys, name=name, marker_color=colour,
            customdata=[om.fmt_money_m(v) for v in ys],
            hovertemplate=f"%{{customdata}}<extra>{name}</extra>"))
    fcf = [r["fcf"] for r in rows]
    line = theme.get("text", "#444")
    fig.add_trace(go.Scatter(
        x=xs, y=fcf, mode="lines+markers", name="Free cash flow",
        line=dict(color=line, width=2.5), marker=dict(size=7, color=line),
        customdata=[om.fmt_money_m(v) for v in fcf],
        hovertemplate="%{customdata}<extra>Free cash flow</extra>"))
    fig.update_layout(**CHART_LAYOUT)
    fig.update_layout(barmode="relative", bargap=0.35)
    stacks = [r["dividends"] + r["buybacks"] + r["debt_paydown"] for r in rows]
    _money_axis(fig, stacks + fcf)
    return fig


def dps_figure(years, dps, theme) -> go.Figure:
    """Dividend per share per fiscal year, small bar chart."""
    theme = theme or {}
    xs = [f"FY{y}" for y in years or []]
    ys = list(dps or [])
    colour = theme.get("accent", "#81b29a")
    fig = go.Figure(go.Bar(
        x=xs, y=ys, name="Dividend per share", marker_color=colour,
        customdata=[DASH if v is None else f"${v:.2f}" for v in ys],
        hovertemplate="%{customdata}<extra>Dividend per share</extra>"))
    fig.update_layout(**{**CHART_LAYOUT, "height": 220, "showlegend": False})
    fig.update_yaxes(rangemode="tozero", tickprefix="$", showgrid=True, gridwidth=1,
                     gridcolor="rgba(128,128,128,0.15)")
    fig.update_xaxes(type="category", showgrid=False)
    return fig


def shares_figure(years, shares, theme) -> go.Figure:
    """Shares outstanding (millions, split-adjusted) per fiscal year."""
    theme = theme or {}
    xs = [f"FY{y}" for y in years or []]
    ys = [None if s is None else s / 1e6 for s in shares or []]
    colour = theme.get("accent", "#81b29a")
    fig = go.Figure(go.Scatter(
        x=xs, y=ys, mode="lines+markers", name="Shares outstanding",
        line=dict(color=colour, width=2.5), marker=dict(size=7, color=colour),
        customdata=[DASH if v is None else f"{v:,.1f}M" for v in ys],
        hovertemplate="%{customdata}<extra>Shares</extra>"))
    fig.update_layout(**{**CHART_LAYOUT, "height": 260, "showlegend": False})
    fig.update_yaxes(ticksuffix="M", showgrid=True, gridwidth=1,
                     gridcolor="rgba(128,128,128,0.15)")
    fig.update_xaxes(type="category", showgrid=False)
    return fig


# ── HTML ───────────────────────────────────────────────────────────────────

def _pct(x, signed=False):
    return om.fmt_pct(_num(x), signed=signed)


def _tile(label, value, caption):
    return (f'<div class="cr-tile"><div class="ov-lbl">{qc.esc(label.upper())}</div>'
            f'<div class="cr-val">{qc.esc(value)}</div>'
            f'<div class="cr-cap">{qc.esc(caption)}</div></div>')


def headline_section_html(h) -> str:
    """White "Capital return" section: four tiles and one summary sentence."""
    try:
        h = h if isinstance(h, dict) else {}
        fy = h.get("fy")
        fy_txt = f"FY{fy}" if isinstance(fy, int) else "last fiscal year"
        tiles = [
            _tile("Shareholder yield", _pct(h.get("shareholder_yield")),
                  f"dividends + net buybacks, {fy_txt}, / market cap"),
            _tile("Dividend yield", _pct(h.get("dividend_yield")),
                  f"dividends paid in {fy_txt} / market cap"),
            _tile("Share count", _pct(h.get("share_cagr_5y"), signed=True),
                  "change per year over 5 years"),
            _tile("FCF returned", _pct(h.get("fcf_returned_5y")),
                  "dividends + buybacks / free cash flow, 5 years"),
        ]
        sentence = summary_sentence(h)
    except Exception:
        tiles, sentence = [_tile(n, DASH, "") for n in
                           ("Shareholder yield", "Dividend yield", "Share count",
                            "FCF returned")], NO_DATA
    inner = (f'{qc.css(CARD_STYLE, CR_STYLE)}<div class="cr-tiles">{"".join(tiles)}</div>'
             f'<p class="cr-sentence">{qc.esc(sentence)}</p>')
    return qc.section_html("Capital return", inner)


def _row(label, value):
    return (f'<div class="ov-row"><span>{qc.esc(label)}</span>'
            f'<b>{qc.esc(value)}</b></div>')


def dividend_section_body_html(d) -> str:
    """The figures under the DPS chart; the non-payer / no-data note when
    there is no dividend to describe."""
    style = qc.css(CARD_STYLE, METRICS_STYLE, CR_STYLE)
    try:
        d = d if isinstance(d, dict) else {}
        if d.get("pays") is not True:
            note = NO_DIVIDEND if d.get("pays") is False else NO_DIVIDEND_DATA
            return f'{style}<p class="cr-note">{qc.esc(note)}</p>'
        growth = d.get("growth") or {}
        streak = d.get("consecutive_increases")
        fy = d.get("fy")
        fy_txt = f" (FY{fy})" if isinstance(fy, int) else ""
        rows = [_row(f"DPS growth {n}Y", _pct(growth.get(n), signed=True))
                for n in DIVIDEND_HORIZONS]
        rows.append(_row("Consecutive yearly raises",
                         DASH if not isinstance(streak, int) else str(streak)))
        rows.append(_row(f"Payout of net income{fy_txt}", _pct(d.get("payout_net_income"))))
        rows.append(_row(f"Payout of free cash flow{fy_txt}", _pct(d.get("payout_fcf"))))
        return f'{style}<div class="cr-stats">{"".join(rows)}</div>'
    except Exception:
        return f'{style}<p class="cr-note">{qc.esc(NO_DIVIDEND_DATA)}</p>'


def share_table_html(rows) -> str:
    """Gross buybacks ($) vs stock issued ($) vs net change in shares (%)
    per fiscal year, newest first."""
    style = qc.css(CARD_STYLE, METRICS_STYLE)
    try:
        rows = [r for r in (rows or []) if isinstance(r, dict)]
        if not rows:
            return f'{style}<p class="cr-note" style="margin:0">{qc.esc(NO_DATA)}</p>'
        head = ("<tr><th>Fiscal year</th><th>Bought back</th><th>Stock issued</th>"
                "<th>Shares, change</th></tr>")
        cells = []
        for r in reversed(rows):
            values = (f"FY{r.get('year')}",
                      om.fmt_money_m(_num(r.get("buybacks"))),
                      om.fmt_money_m(_num(r.get("issuance"))),
                      _pct(r.get("share_change"), signed=True))
            cells.append("<tr>" + "".join(f"<td>{qc.esc(v)}</td>" for v in values) + "</tr>")
        body = "".join(cells)
        return f'{style}<table class="ov-gtab">{head}{body}</table>'
    except Exception:
        return f'{style}<p class="cr-note">{qc.esc(NO_DATA)}</p>'
