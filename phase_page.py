"""HTML and the chart for the Phase tab: the "Phase" section (Phase Analysis
card + growth-cycle diagram), the Revenue & Operating Cash Flow figure and the
"Payouts" section.

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
from phase_payouts import PAYOUTS

PHASES = ((1, "Startup"), (2, "Hypergrowth"), (3, "Self Funding"),
          (4, "Operating Leverage"), (5, "Capital Return"), (6, "Decline"))
_NAMES = dict(PHASES)

PAYOUT_COLOUR = "#5b6cff"

NOTICE = 'No phase analysis yet. It comes with the "Business Phase Analysis" section.'

_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"

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
.ph-lead{{margin:0 0 10px;font-size:14px;line-height:1.5}}
.ph-pt{{margin:0 0 7px;font-size:14px;line-height:1.45}}
.ph-note{{margin:0;font-size:13px;color:var(--text-muted);line-height:1.45}}
.ph-svg{{display:block;width:100%;height:auto}}
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

_COL = 100          # six equal columns across a 600-wide viewBox
_ZERO = 130         # y of the zero baseline

# Stylised curves, one point per column centre plus both edges.
_XS = (0, 50, 150, 250, 350, 450, 550, 600)
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
    """"4 Operating Leverage" is too wide for a 100-unit column: long names
    wrap before their last word."""
    text = f"{n} {name}"
    return [text] if len(text) <= 14 else text.rsplit(" ", 1)


def growth_cycle_svg(current):
    """The six-phase growth cycle with Revenue / Profits / Payouts curves; the
    current phase (1–6) gets an accent band and a numbered badge."""
    current = current if isinstance(current, int) and 1 <= current <= 6 else None
    parts = []
    if current:
        x0 = (current - 1) * _COL
        cx = x0 + _COL // 2
        parts.append(f'<rect class="gc-band" x="{x0}" y="24" width="{_COL}" height="196" '
                     f'rx="8" fill="var(--accent)" fill-opacity="0.12"/>')
        parts.append(f'<circle cx="{cx}" cy="38" r="12" fill="var(--accent)"/>'
                     f'<text x="{cx}" y="42.5" text-anchor="middle" font-size="13" '
                     f'font-weight="700" fill="#fff">{current}</text>')
    for i in range(1, 6):
        parts.append(f'<line x1="{i * _COL}" y1="28" x2="{i * _COL}" y2="176" '
                     f'stroke="var(--text-muted)" stroke-opacity="0.18"/>')
    parts.append(f'<line x1="0" y1="{_ZERO}" x2="600" y2="{_ZERO}" stroke="var(--text-muted)" '
                 f'stroke-opacity="0.55" stroke-dasharray="3 3"/>'
                 f'<text x="596" y="{_ZERO - 4}" text-anchor="end" font-size="9" '
                 f'fill="var(--text-muted)">0</text>')
    for name, colour, ys in _CURVES:
        parts.append(f'<path d="{_smooth_path(_XS, ys)}" fill="none" stroke="{colour}" '
                     f'stroke-width="2.5" stroke-linecap="round"><title>{name}</title></path>')
    # Legend: three short swatches across the top row.
    for i, (name, colour, _ys) in enumerate(_CURVES):
        x = 180 + i * 90
        parts.append(f'<line x1="{x}" y1="11" x2="{x + 16}" y2="11" stroke="{colour}" '
                     f'stroke-width="2.5" stroke-linecap="round"/>'
                     f'<text x="{x + 21}" y="14.5" font-size="11" '
                     f'fill="var(--text-muted)">{name}</text>')
    for n, name in PHASES:
        cx = (n - 1) * _COL + _COL // 2
        on = n == current
        fill = "var(--text)" if on else "var(--text-muted)"
        weight = ' font-weight="700"' if on else ""
        lines = _label_lines(n, name)
        y0 = 196 if len(lines) == 1 else 190
        spans = "".join(f'<tspan x="{cx}" y="{y0 + 13 * j}">{qc.esc(line)}</tspan>'
                        for j, line in enumerate(lines))
        parts.append(f'<text text-anchor="middle" font-size="12" fill="{fill}"{weight}>'
                     f'{spans}</text>')
    return (f'<svg class="ph-svg" viewBox="0 0 600 220" role="img" '
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


def _card(title, body_html):
    return f'<div class="ph-card"><div class="ph-title">{qc.esc(title)}</div>{body_html}</div>'


def phase_section_html(analysis_text, scorecard_text, theme):
    """The "Phase" section: Phase Analysis card and the growth-cycle card."""
    number = phase_number(analysis_text, scorecard_text)
    left = _card("Phase Analysis", _analysis_body(number, _verdict(analysis_text)))
    right = _card("Growth cycle", growth_cycle_svg(number))
    return qc.section_html("Phase", f'{qc.css(PHASE_STYLE)}<div class="ph-row">{left}{right}</div>')


# ── revenue & operating cash flow ───────────────────────────────────────────

def _tick_label(t):
    if t == 0:
        return "$0"
    return ("-" if t < 0 else "") + om.fmt_money_m(abs(t))


def _money_ticks(values, n=5):
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
    fig.update_layout(
        height=320,
        margin=dict(l=10, r=10, t=30, b=10),
        hovermode="x unified",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", x=0, xanchor="left", y=1.12, yanchor="bottom"),
    )
    tickvals, ticktext = _money_ticks(list(revenue) + list(cfo))
    fig.update_yaxes(tickmode="array", tickvals=tickvals, ticktext=ticktext, showgrid=True,
                     gridwidth=1, gridcolor="rgba(128,128,128,0.15)", zeroline=True,
                     zerolinecolor="rgba(128,128,128,0.35)")
    fig.update_xaxes(type="category", showgrid=False)
    return fig


# ── payouts ────────────────────────────────────────────────────────────────

UNKNOWN_PAYOUT = "Not enough cash-flow data to tell."


def _unknown_card_html(question, theme):
    """Same size and face as a flip card, but it does not flip: without the
    fiscal year's cash-flow statement "No" would be a guess."""
    muted = theme.get("text_muted", "#888")
    return (f'<div class="mc-card" style="cursor:default"><div class="mc-inner">'
            f'<div class="mc-face"><span class="mc-q" style="color:{muted}">{qc.esc(question)}</span>'
            f'<div style="margin:auto 0;text-align:center;font-size:.9rem;color:{muted}">'
            f'{qc.esc(UNKNOWN_PAYOUT)}</div></div></div></div>')


def payouts_section_html(buyback_card, dividend_card, theme):
    """The two payout cards; a None card (cash flow unknown) gets a flat
    notice card with its question in the same slot."""
    cards = []
    for (_key, _name, question, _options), card in zip(PAYOUTS.items,
                                                       (buyback_card, dividend_card)):
        cards.append(_unknown_card_html(question, theme) if card is None
                     else qc.flip_card_html(PAYOUTS, card, theme))
    grid = f'{qc.css(qc.STYLE)}<div class="mc-grid">{"".join(cards)}</div>'
    return qc.section_html("Payouts", grid)
