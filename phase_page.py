"""HTML and the chart for the Phase tab: the "Phase" section (Phase Analysis
card + growth-cycle diagram) and the Revenue & Operating Cash Flow figure.

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
import question_cards as qc

PHASES = ((1, "Startup"), (2, "Hypergrowth"), (3, "Self Funding"),
          (4, "Operating Leverage"), (5, "Capital Return"), (6, "Decline"))
_NAMES = dict(PHASES)

PAYOUT_COLOUR = "#5b6cff"

NOTICE = 'No phase analysis yet. It comes with the "Business Phase Analysis" section.'

_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"
_HAIRLINE = "color-mix(in srgb, var(--text) 10%, transparent)"

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

PHASE_STYLE = f"""<style>
.ph-row{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;
  align-items:stretch}}
@media (max-width:800px){{.ph-row{{grid-template-columns:minmax(0,1fr)}}}}
.ph-card{{background:{_INNER};border-radius:16px;padding:16px 18px;box-sizing:border-box;
  min-width:0;height:100%;color:var(--text)}}
.ph-title{{font-size:15px;font-weight:700;color:var(--text);margin:0 0 12px}}
.ph-badge{{display:flex;align-items:center;gap:14px;margin:0 0 14px}}
.ph-num{{width:52px;height:52px;border-radius:50%;background:var(--accent);color:#fff;
  display:flex;align-items:center;justify-content:center;font-size:26px;font-weight:700;
  flex:none}}
.ph-kicker{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted)}}
.ph-name{{font-size:20px;font-weight:700;line-height:1.2}}
.ph-lead{{margin:0 0 10px;font-size:13px;line-height:1.45}}
.ph-pt{{margin:0 0 7px;font-size:13px;line-height:1.45}}
.ph-note{{margin:0;font-size:13px;color:var(--text-muted);line-height:1.45}}
.ph-svg{{display:block;width:100%;height:auto;max-width:900px;margin:0 auto}}
.ph-cycle{{display:flex;flex-direction:column}}
.ph-cycle-fig{{margin:auto 0}}
.ph-pn{{margin-top:14px;padding-top:12px;border-top:1px solid {_HAIRLINE}}}
.ph-pn-row{{display:flex;gap:10px;margin:0 0 5px;font-size:12.5px;line-height:1.4;
  color:var(--text-muted)}}
.ph-pn-row:last-child{{margin-bottom:0}}
.ph-pn-row b{{flex:none;width:104px;font-weight:600}}
</style>"""


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


# ── growth-cycle diagram ────────────────────────────────────────────────────

_COL = 150          # six equal columns across a 900-wide viewBox
_W = 6 * _COL
_ZERO = 130         # y of the zero baseline
_H = 198            # viewBox height: chart, then the column labels

# Stylised curves, one point per column centre plus both edges.
_XS = (0, 75, 225, 375, 525, 675, 825, 900)
_CURVES = (
    # Revenue climbs through 1–5 and sags in Decline.
    ("Revenue", "var(--accent)", (128, 124, 108, 90, 72, 62, 74, 82)),
    # Profits: losses in 1–2, break-even around 3, peak in 4–5, falling in 6.
    ("Profits", "var(--text-muted)", (132, 146, 154, 130, 96, 86, 112, 122)),
    # Payouts: nothing until 4, rise in 5, shrink in 6.
    ("Payouts", PAYOUT_COLOUR, (130, 130, 130, 130, 129, 104, 118, 124)),
)


def _smooth_path(xs, ys):
    """A Catmull-Rom spline through the points, as cubic Béziers."""
    pts = list(zip(xs, ys))
    d = f"M{pts[0][0]},{pts[0][1]}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]},{p2[1]}"
    return d


def _label_lines(n, name):
    """One line per column; at 10px a 150-unit column fits ~24 characters,
    longer names would wrap before their last word."""
    text = f"{n} {name}"
    return [text] if len(text) <= 24 else text.rsplit(" ", 1)


def growth_cycle_svg(current):
    """The six-phase growth cycle with Revenue / Profits / Payouts curves; the
    current phase (1–6) gets an accent band and a numbered badge."""
    current = current if isinstance(current, int) and 1 <= current <= 6 else None
    parts = []
    if current:
        x0 = (current - 1) * _COL
        cx = x0 + _COL // 2
        parts.append(f'<rect class="gc-band" x="{x0}" y="24" width="{_COL}" height="{_H - 24}" '
                     f'rx="8" fill="var(--accent)" fill-opacity="0.12"/>')
        parts.append(f'<circle cx="{cx}" cy="36" r="9.5" fill="var(--accent)"/>'
                     f'<text x="{cx}" y="39.5" text-anchor="middle" font-size="10" '
                     f'font-weight="700" fill="#fff">{current}</text>')
    for i in range(1, 6):
        parts.append(f'<line x1="{i * _COL}" y1="28" x2="{i * _COL}" y2="176" '
                     f'stroke="var(--text-muted)" stroke-opacity="0.18"/>')
    parts.append(f'<line x1="0" y1="{_ZERO}" x2="{_W}" y2="{_ZERO}" stroke="var(--text-muted)" '
                 f'stroke-opacity="0.55" stroke-dasharray="3 3"/>'
                 f'<text x="{_W - 4}" y="{_ZERO - 4}" text-anchor="end" font-size="8" '
                 f'fill="var(--text-muted)">0</text>')
    for name, colour, ys in _CURVES:
        parts.append(f'<path d="{_smooth_path(_XS, ys)}" fill="none" stroke="{colour}" '
                     f'stroke-width="2.5" stroke-linecap="round"><title>{name}</title></path>')
    # Legend: three short swatches across the top row.
    for i, (name, colour, _ys) in enumerate(_CURVES):
        x = 341 + i * 75            # centred on the 900-wide row
        parts.append(f'<line x1="{x}" y1="11" x2="{x + 16}" y2="11" stroke="{colour}" '
                     f'stroke-width="2.5" stroke-linecap="round"/>'
                     f'<text x="{x + 20}" y="14" font-size="9" '
                     f'fill="var(--text-muted)">{name}</text>')
    for n, name in PHASES:
        cx = (n - 1) * _COL + _COL // 2
        on = n == current
        fill = "var(--text)" if on else "var(--text-muted)"
        weight = ' font-weight="700"' if on else ""
        lines = _label_lines(n, name)
        y0 = 190 if len(lines) == 1 else 185
        spans = "".join(f'<tspan x="{cx}" y="{y0 + 11 * j}">{qc.esc(line)}</tspan>'
                        for j, line in enumerate(lines))
        parts.append(f'<text text-anchor="middle" font-size="10" fill="{fill}"{weight}>'
                     f'{spans}</text>')
    return (f'<svg class="ph-svg" viewBox="0 0 {_W} {_H}" role="img" '
            f'aria-label="Growth cycle: six phases with revenue, profits and payouts">'
            f'{"".join(parts)}</svg>')


# ── phase section ───────────────────────────────────────────────────────────

def _verdict(analysis_text):
    from prescan_render import parse_verdict_section
    try:
        return parse_verdict_section(analysis_text) if isinstance(analysis_text, str) else None
    except Exception:
        return None


def _analysis_body(number, v):
    badge = ""
    if number:
        badge = (f'<div class="ph-badge"><div class="ph-num">{number}</div><div>'
                 f'<div class="ph-kicker">Phase {number} of 6</div>'
                 f'<div class="ph-name">{qc.esc(_NAMES[number])}</div></div></div>')
    if v is None:
        return f'{badge}<p class="ph-note">{qc.esc(NOTICE)}</p>'
    lead = f'<p class="ph-lead">{qc.bold(v["summary"])}</p>' if v.get("summary") else ""
    points = "".join(f'<p class="ph-pt">• <b>{qc.esc(p["label"])}</b>: {qc.bold(p["text"])}</p>'
                     for p in v["bullets"])
    return f"{badge}{lead}{points}"


def _card(title, body_html, extra_class=""):
    cls = f"ph-card {extra_class}".strip()
    return f'<div class="{cls}"><div class="ph-title">{qc.esc(title)}</div>{body_html}</div>'


def phase_note_html(number):
    """Three muted label/value lines for the current phase; empty when the
    phase is unknown."""
    note = PHASE_NOTES.get(number) if isinstance(number, int) else None
    if not note:
        return ""
    rows = "".join(f'<div class="ph-pn-row"><b>{qc.esc(label)}</b>'
                   f'<span>{qc.esc(note[key])}</span></div>'
                   for key, label in PHASE_NOTE_LABELS)
    return f'<div class="ph-pn">{rows}</div>'


def _cycle_body(number):
    return (f'<div class="ph-cycle-fig">{growth_cycle_svg(number)}</div>'
            f'{phase_note_html(number)}')


def phase_section_html(analysis_text, scorecard_text, theme):
    """The "Phase" section: Phase Analysis card and the growth-cycle card."""
    number = phase_number(analysis_text, scorecard_text)
    left = _card("Phase Analysis", _analysis_body(number, _verdict(analysis_text)))
    right = _card("Growth cycle", _cycle_body(number), "ph-cycle")
    return qc.section_html("Phase", f'{qc.css(PHASE_STYLE)}<div class="ph-row">{left}{right}</div>')


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


# ── payouts ────────────────────────────────────────────────────────────────

UNKNOWN_PAYOUT = "Not enough cash-flow data to tell."


