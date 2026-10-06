"""The business phase (1-6) for the Business tab's Profile card, and the
Revenue & Operating Cash Flow figure on the Financials tab. The Phase tab
itself was folded into those two places on 2026-10-06.

Pure builders, no Streamlit import; the tab in streamlit_app.py fetches the
data, wraps every render in a try and draws the chart in a keyed container.
Sections use question_cards.section_html with flat inner cards, every dynamic
text goes through question_cards.esc (a bare `$` pair renders as LaTeX), and
every <style> block is emitted on one line.
"""

from __future__ import annotations

import math

import plotly.graph_objects as go

import overview_metrics as om

PHASES = ((1, "Startup"), (2, "Hypergrowth"), (3, "Self Funding"),
          (4, "Operating Leverage"), (5, "Capital Return"), (6, "Decline"))
_NAMES = dict(PHASES)

PAYOUT_COLOUR = "#5b6cff"


# Per phase: what it looks like, which valuation fits and what moves it on —
# condensed from the "Business Phase Analysis" prompt's phase definitions and
# decision tree (streamlit_app.DEFAULT_AI_PROMPTS).
PHASE_NOTE_LABELS = (("looks_like", "Looks like"), ("valuation", "Valuation fits"),
                     ("moves_on", "Moves on when"))
PHASE_NOTES = {
    1: {"looks_like": "Losses expanding, finding product-market fit",
        "valuation": "Forward price to sales, total addressable market (TAM)",
        "moves_on": "Losses start shrinking (→ Hypergrowth)"},
    2: {"looks_like": "Losses improving, proving viability",
        "valuation": "Forward price to sales, price to gross profit",
        "moves_on": "Losses near breakeven (→ Self Funding)"},
    3: {"looks_like": "Near breakeven, validating the model",
        "valuation": "Price to sales, price to gross profit",
        "moves_on": "Operating income turns positive (→ Operating Leverage)"},
    4: {"looks_like": "Profitable, maximizing margins",
        "valuation": "Forward P/E, forward price to free cash flow",
        "moves_on": "Payouts start (→ Capital Return) or revenue falls (→ Decline)"},
    5: {"looks_like": "Mature, rewarding shareholders",
        "valuation": "Trailing P/E, trailing price to free cash flow, reverse DCF",
        "moves_on": "Payouts stop (→ Operating Leverage, or Decline if revenue falls)"},
    6: {"looks_like": "Revenue falling, business deteriorating",
        "valuation": "Price to book, liquidation value, asset-based valuation",
        "moves_on": "Revenue grows again (→ Operating Leverage)"},
}

# ── phase number ────────────────────────────────────────────────────────────

def _from_analysis(analysis_text):
    from prescan_render import parse_verdict_section
    v = parse_verdict_section(analysis_text) if isinstance(analysis_text, str) else None
    if v and v.get("out_of") == 6 and v.get("score") is not None:
        score = v["score"]
        if score == int(score) and 1 <= score <= 6:
            return int(score)
    return None


def _from_scorecard(scorecard_text):
    from scorecard_utils import parse_scorecard
    if not isinstance(scorecard_text, str):
        return None
    n = parse_scorecard({"Scorecard": scorecard_text}).get("phase")
    return n if isinstance(n, int) and 1 <= n <= 6 else None


def phase_number(analysis_text, scorecard_text):
    """The current phase 1–6: the analysis score when it is out of 6, else the
    Scorecard's phase.number, else None. Never raises."""
    for source, text in ((_from_analysis, analysis_text), (_from_scorecard, scorecard_text)):
        try:
            n = source(text)
        except Exception:
            n = None
        if n is not None:
            return n
    return None


# ── phase section ───────────────────────────────────────────────────────────

def _verdict(analysis_text):
    from prescan_render import parse_verdict_section
    try:
        return parse_verdict_section(analysis_text) if isinstance(analysis_text, str) else None
    except Exception:
        return None


def _plain(text):
    """Markdown bold off: the summary is shown as plain text in a tooltip."""
    return (text or "").replace("**", "").strip()


def phase_info(analysis_text, scorecard_text):
    """The phase for the Business tab's Profile card, or None when unknown:
    {"number", "name", "summary", "tooltip"}. summary is the analysis's own
    lead sentence (may be empty); tooltip adds what the phase looks like,
    which valuation fits it and what moves it on."""
    number = phase_number(analysis_text, scorecard_text)
    if not number:
        return None
    v = _verdict(analysis_text)
    summary = _plain(v.get("summary")) if v else ""
    note = PHASE_NOTES.get(number) or {}
    lines = [f"Phase {number} of 6: {_NAMES[number]}"]
    if summary:
        lines.append(summary)
    lines += [f"{label}: {note[key]}" for key, label in PHASE_NOTE_LABELS if key in note]
    return {"number": number, "name": _NAMES[number], "summary": summary,
            "tooltip": "\n".join(lines)}


# ── revenue & operating cash flow ───────────────────────────────────────────

def _tick_label(t):
    if t == 0:
        return "$0"
    return ("-" if t < 0 else "") + om.fmt_money_m(abs(t))


def money_ticks(values, n=5):
    """Round y-axis ticks ($M) from min(0, lowest) to the highest value, about
    `n` steps of 1/2/5 x 10^k, each labelled like fmt_money_m ("$2.4B",
    "$510M"). Empty when there is no data."""
    vals = [v for v in values if v is not None]
    if not vals:
        return [], []
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    span = hi - lo
    if span <= 0:
        return [0.0], ["$0"]
    raw = span / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first, last = math.floor(lo / step), math.ceil(hi / step)
    ticks = [k * step for k in range(first, last + 1)]
    return ticks, [_tick_label(t) for t in ticks]


# Shared by the Phase and Growth line charts: compact top margin and the
# legend tucked just above the plot's top-left corner.
CHART_LAYOUT = dict(
    height=300,
    margin=dict(l=10, r=10, t=28, b=10),
    hovermode="x unified",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    legend=dict(orientation="h", x=0, xanchor="left", y=1.0, yanchor="bottom",
                bgcolor="rgba(0,0,0,0)"),
)


def revenue_ocf_figure(years, revenue, cfo, theme) -> go.Figure:
    """Revenue and operating cash flow ($M) per fiscal year, two lines."""
    xs = [f"FY{y}" for y in years]
    fig = go.Figure()
    for name, ys, colour in (("Revenue", revenue, theme.get("accent", "#81b29a")),
                             ("Operating cash flow", cfo, PAYOUT_COLOUR)):
        fig.add_trace(go.Scatter(
            x=xs, y=list(ys), mode="lines+markers", name=name,
            line=dict(color=colour, width=2.5), marker=dict(size=7, color=colour),
            customdata=[om.fmt_money_m(v) for v in ys],
            hovertemplate=f"%{{customdata}}<extra>{name}</extra>",
        ))
    fig.update_layout(**CHART_LAYOUT)
    tickvals, ticktext = money_ticks(list(revenue) + list(cfo))
    fig.update_yaxes(rangemode="tozero", tickmode="array", tickvals=tickvals,
                     ticktext=ticktext, showgrid=True,
                     gridwidth=1, gridcolor="rgba(128,128,128,0.15)", zeroline=True,
                     zerolinecolor="rgba(128,128,128,0.35)")
    fig.update_xaxes(type="category", showgrid=False)
    return fig
