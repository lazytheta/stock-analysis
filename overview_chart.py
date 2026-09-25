"""Price-vs-S&P 500 chart: maths and Plotly figure for the Overview tab.

Pure functions only — no Streamlit import. The caller (streamlit_app.py)
owns `price_history.load_series(...)` results, the live quote and the
theme dict; this module turns those into percent-change series, a figure
and a header line.
"""

from __future__ import annotations

from datetime import date

import plotly.graph_objects as go

import question_cards

RANGES = ("1M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y")
DEFAULT_RANGE = "5Y"

_MONTHS = {"1M": 1, "6M": 6}
_YEARS = {"1Y": 1, "3Y": 3, "5Y": 5, "10Y": 10}


def with_live_point(series: list, price, today: date) -> list:
    """Append (today, price) when there's a live price newer than the last close.

    Returns a new list; never mutates the input or replaces an existing point.
    """
    if not price or price <= 0:
        return series
    if series and series[-1][0] >= today.isoformat():
        return series
    return [*series, (today.isoformat(), price)]


def _months_before(d: date, months: int) -> date:
    total = d.month - 1 - months
    year = d.year + total // 12
    month = total % 12 + 1
    day = d.day
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1


def range_start(rng: str, last_day: date) -> date:
    """Start of the window for a range button, ending at `last_day`."""
    if rng in _MONTHS:
        return _months_before(last_day, _MONTHS[rng])
    if rng == "YTD":
        return date(last_day.year, 1, 1)
    years = _YEARS.get(rng, _YEARS[DEFAULT_RANGE])
    try:
        return last_day.replace(year=last_day.year - years)
    except ValueError:
        # last_day is Feb 29 and last_day.year - years isn't a leap year.
        return last_day.replace(month=2, day=28, year=last_day.year - years)


def aligned_pct(stock: list, bench: list, start: date):
    """Inner-join stock and bench on day (>= start), each as percent change
    vs its first close in the window. Fewer than 2 common points -> ([], [], [])."""
    start_iso = start.isoformat()
    stock_map = {d: c for d, c in stock if d >= start_iso}
    bench_map = {d: c for d, c in bench if d >= start_iso}
    common = sorted(set(stock_map) & set(bench_map))
    if len(common) < 2:
        return [], [], []
    stock_base = stock_map[common[0]]
    bench_base = bench_map[common[0]]
    stock_pcts = [stock_map[d] / stock_base - 1 for d in common]
    bench_pcts = [bench_map[d] / bench_base - 1 for d in common]
    return common, stock_pcts, bench_pcts


def total_and_cagr(pcts: list, days: list, min_years: float = 1.0):
    """Total return (last pct) and CAGR (None when the span is under min_years)."""
    if not pcts:
        return None, None
    total = pcts[-1]
    span_days = (date.fromisoformat(days[-1]) - date.fromisoformat(days[0])).days
    if span_days < min_years * 365:
        return total, None
    cagr = (1 + total) ** (365.25 / span_days) - 1
    return total, cagr


def _hex_to_fill(hex_color: str, alpha: float = 0.08):
    if not isinstance(hex_color, str) or not hex_color.startswith("#") or len(hex_color) != 7:
        return None
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except ValueError:
        return None
    return f"rgba({r},{g},{b},{alpha})"


def figure(dates, stock_pcts, bench_pcts, ticker: str, theme: dict) -> go.Figure:
    """Two-line chart: stock (with light fill to zero) vs S&P 500, as percent change."""
    accent = theme.get("accent", "#81b29a")
    bench_color = theme.get("bench", "#999999")
    fill_color = _hex_to_fill(accent)

    fig = go.Figure()
    stock_trace = dict(
        x=dates, y=stock_pcts, mode="lines", name=ticker,
        line=dict(color=accent, width=2.5),
        fill="tozeroy",
    )
    if fill_color:
        stock_trace["fillcolor"] = fill_color
    fig.add_trace(go.Scatter(**stock_trace))
    fig.add_trace(go.Scatter(
        x=dates, y=bench_pcts, mode="lines", name="S&P 500",
        line=dict(color=bench_color, width=2),
    ))
    fig.update_layout(
        height=360,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_yaxes(tickformat=".0%", showgrid=True, gridwidth=1,
                      gridcolor="rgba(128,128,128,0.15)", zeroline=False)
    fig.update_xaxes(showgrid=False)
    return fig


def _color_for(pct):
    return "var(--green, #2e7d32)" if pct >= 0 else "var(--red, #c62828)"


def _colored(text, pct):
    return f'<span style="color:{_color_for(pct)}">{text}</span>'


def _line(label, total, cagr):
    esc = question_cards.esc
    if total is None:
        dash = '<span style="color:var(--text-muted)">—</span>'
        parts = [esc(label), f"<b>{dash}</b>"]
        return " ".join(parts)
    parts = [esc(label), f"<b>{_colored(f'{total:+.1%}', total)}</b>"]
    if cagr is not None:
        parts.append(_colored(f"CAGR {cagr:+.1%}", cagr))
    return " ".join(parts)


def header_html(ticker: str, rng: str, stock_total, stock_cagr,
                 bench_total, bench_cagr) -> str:
    """Two lines: `{TICKER} · {rng} <b>+40.0%</b> CAGR +7.0%` and the S&P line."""
    stock_label = f"{ticker} · {rng}"
    lines = [
        f'<div>{_line(stock_label, stock_total, stock_cagr)}</div>',
        f'<div>{_line("S&P 500", bench_total, bench_cagr)}</div>',
    ]
    return "".join(lines)
