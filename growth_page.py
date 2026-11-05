"""HTML and the chart for the Growth tab: the "Growth" section (Growth
Analysis + Consensus cards), the "Capital engine" section (ROCE, ROIIC,
reinvestment rate), the Revenue & net income figure with year-on-year
labels, the CAGR table under it and the "Growth questions" flip cards.

Pure builders, no Streamlit import; the tab in streamlit_app.py fetches the
data, wraps every render in a try and draws the chart in a keyed container.
Sections use question_cards.section_html with flat inner cards, every dynamic
text goes through question_cards.esc (a bare `$` pair renders as LaTeX), and
every <style> block is emitted on one line.
"""

from __future__ import annotations

import plotly.graph_objects as go

import overview_metrics as om
import question_cards as qc
from growth_cards import GROWTH, SCORE_LABELS, TITLE, parse_growth_cards
from overview_page import METRICS_STYLE
from phase_page import CHART_LAYOUT, PAYOUT_COLOUR, money_ticks

NOTICE = 'No growth analysis yet. It comes with the "Growth Cards" section.'
NO_CONSENSUS = "No analyst consensus available."

EARNINGS_COLOUR = PAYOUT_COLOUR

_HAIRLINE = "color-mix(in srgb, var(--text) 10%, transparent)"
_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"

GROWTH_STYLE = f"""<style>
.gr-note{{background:{_INNER};border-radius:16px;padding:16px 18px;box-sizing:border-box;
  font-size:14px;color:var(--text-muted)}}
.gr-row{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  padding:8px 0;border-bottom:1px solid {_HAIRLINE};font-size:.9rem;color:var(--text)}}
.gr-row:last-child{{border-bottom:none}}
.gr-row b{{font-weight:600;white-space:nowrap}}
.gr-empty{{margin:0;font-size:.9rem;color:var(--text-muted)}}
.gr-src{{padding-top:6px;font-size:.74rem;color:var(--text-muted);line-height:1.4}}
.gr-sub{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 2px}}
.gr-sub-gap{{margin-top:16px;padding-top:12px;border-top:1px solid {_HAIRLINE}}}
.gr-cagr{{margin-top:6px}}
.gr-wrap .ms-card{{height:auto;min-height:300px}}
.gr-dim{{color:var(--text-muted);font-weight:400 !important}}
.gr-flags{{margin:12px 0 0;padding:0 0 0 18px;font-size:.82rem;color:var(--text-muted);line-height:1.45}}
</style>"""


def _parse(content):
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        return parse_growth_cards(content)
    except ValueError:
        return None


# ── Growth section ─────────────────────────────────────────────────────────

def _score_tone(score):
    from prescan_render import band_tone
    return band_tone("red" if score <= 2 else "yellow" if score == 3 else "green")


def _analysis_card(analysis, theme):
    score = analysis["score"]
    box = qc.dial_box_html(score, 5, SCORE_LABELS[score], _score_tone(score))
    return qc.summary_card_html("GROWTH ANALYSIS", box, qc.bold(analysis["summary"]),
                                analysis["points"], theme)


def _signed_pct(x):
    return om.DASH if x is None else f"{x:+.1f}%"


def _consensus_card(consensus, theme, guidance=None):
    """Outlook: analyst consensus for the next one or two fiscal years, then
    the company's own guidance when it gave any."""
    title = (f'<div class="mc-q" style="color:{theme["text_muted"]};margin-bottom:12px">'
             f'OUTLOOK</div>')

    def _rows(pairs):
        return "".join(f'<div class="gr-row"><span>{qc.esc(label)}</span>'
                       f'<b>{qc.esc(value)}</b></div>' for label, value in pairs)

    parts = []
    if consensus:
        fy1 = consensus["fiscal_year"]
        pairs = [(f"Revenue growth {fy1}", _signed_pct(consensus["revenue_growth_pct"])),
                 (f"EPS growth {fy1}", _signed_pct(consensus["eps_growth_pct"]))]
        y2 = consensus.get("year2")
        if y2:
            pairs += [(f"Revenue growth {y2['fiscal_year']}", _signed_pct(y2["revenue_growth_pct"])),
                      (f"EPS growth {y2['fiscal_year']}", _signed_pct(y2["eps_growth_pct"]))]
        analysts = consensus["analysts"]
        pairs.append(("Analysts", om.DASH if analysts is None else str(analysts)))
        parts.append(f'<div class="gr-sub">ANALYST CONSENSUS</div><div>{_rows(pairs)}</div>'
                     f'<div class="gr-src">{qc.esc(consensus["source"])}</div>')
    else:
        parts.append(f'<p class="gr-empty">{qc.esc(NO_CONSENSUS)}</p>')
    if guidance:
        # Same rows as the consensus above: what, for when, how much.
        pairs = [(f'{it["metric"]} {it["period"]}', it["value"]) for it in guidance["items"]]
        parts.append(f'<div class="gr-sub gr-sub-gap">COMPANY GUIDANCE</div>'
                     f'<div>{_rows(pairs)}</div>'
                     f'<div class="gr-src">{qc.esc(guidance["source"])}</div>')
    return f'<div class="ms-card">{title}{"".join(parts)}</div>'


def growth_section_html(content: str | None, theme) -> str:
    """The "Growth" section: Growth Analysis (score meter, lead, three points)
    and Consensus as two equal-height cards; a single notice card when the
    "Growth Cards" section is missing or invalid."""
    parsed = _parse(content)
    if parsed is None:
        inner = f'<div class="gr-note">{qc.esc(NOTICE)}</div>'
    else:
        # The Outlook card grows with year two and guidance; the shared card
        # height (300px) would clip it, so here the row sizes to its content
        # and the two cards stay equal height.
        _score = parsed["analysis"]["score"]
        inner = (qc.score_bar_html(_score, list(SCORE_LABELS.values()), _score_tone(_score))
                 + '<div class="gr-wrap">'
                 + qc.summary_row_html(_analysis_card(parsed["analysis"], theme),
                                       _consensus_card(parsed["consensus"], theme,
                                                       parsed.get("guidance")))
                 + '</div>')
    return qc.section_html("Growth", f'{qc.css(GROWTH_STYLE)}{inner}')


# ── Capital engine ─────────────────────────────────────────────────────────

ENGINE_NOTICE = "Not enough EDGAR history for the capital engine (needs at least five years)."
ENGINE_FLAG_TEXT = {
    "small_delta_capital": "Capital barely moved over the window — ROIIC is noise.",
    "negative_delta": "Capital shrank or starting profit was ≤ 0 — ROIIC is not defined.",
    "external_funding": "Last year it reinvested more than it earned — funded from outside.",
    "growth_mismatch": "Implied and actual profit growth differ by more than 5 points.",
    "lag_hides_recent_capex": "A big investment last year is not in the window yet.",
}


def _pct(x):
    return om.DASH if x is None else f"{x:.1f}%"


def capital_engine_section_html(engine, roce_pct, roce_metric, theme) -> str:
    """The "Capital engine" section: ROCE (quality of the capital already in),
    ROIIC (quality of the new capital) and the reinvestment rate side by side,
    the detail behind them, then the flags. A noisy ROIIC is shown greyed out,
    never hidden. `engine` is scorecard_utils.capital_engine(fund)."""
    muted = f'color:{theme["text_muted"]};margin-bottom:12px'

    def _rows(pairs):
        return "".join(f'<div class="gr-row"><span>{qc.esc(label)}</span>{value}</div>'
                       for label, value in pairs)

    if not engine:
        inner = f'<div class="gr-note">{qc.esc(ENGINE_NOTICE)}</div>'
        return qc.section_html("Capital engine", f'{qc.css(GROWTH_STYLE)}{inner}')

    flags = engine["flags"]
    noisy = flags["small_delta_capital"] or engine["roiic_pct"] is None
    roiic = (f'<b class="gr-dim" title="{qc.esc(ENGINE_FLAG_TEXT["small_delta_capital"])}">'
             f'{qc.esc(_pct(engine["roiic_pct"]))}</b>' if noisy
             else f'<b>{qc.esc(_pct(engine["roiic_pct"]))}</b>')
    left = (f'<div class="ms-card"><div class="mc-q" style="{muted}">RETURNS &amp; REINVESTMENT</div>'
            + _rows([(f"{roce_metric or 'ROCE'} (existing capital)", f'<b>{qc.esc(_pct(roce_pct))}</b>'),
                     ("ROIIC (new capital)", roiic),
                     ("Reinvestment rate", f'<b>{qc.esc(_pct(engine["reinvestment_rate_window_pct"]))}</b>')])
            + f'<div class="gr-src">{engine["window_years"]}-year window: profit '
              f'FY{engine["start_year"]}→FY{engine["end_year"]}, capital '
              f'FY{engine["capital_start_year"]}→FY{engine["capital_end_year"]} '
              f'(one year behind). Tax rate {qc.esc(_pct(engine["tax_rate_pct"]))}'
              f'{" (fallback)" if engine["tax_rate_fallback"] else ""}.</div></div>')
    trend = " · ".join(f'FY{x["end_year"]} {_pct(x["roiic_pct"])}' for x in engine["trend"])
    right = (f'<div class="ms-card"><div class="mc-q" style="{muted}">GROWTH IT EXPLAINS</div>'
             + _rows([("Implied profit growth", f'<b>{qc.esc(_signed_pct(engine["implied_growth_pct"]))}</b>'),
                      ("Actual profit growth", f'<b>{qc.esc(_signed_pct(engine["actual_growth_pct"]))}</b>'),
                      ("Reinvested last year", f'<b>{qc.esc(_pct(engine["reinvestment_rate_latest_pct"]))}</b>'),
                      ("ROIIC trend", f'<b>{qc.esc(trend or om.DASH)}</b>')])
             + '<div class="gr-src">Implied = reinvestment rate × ROIIC; actual = '
               'yearly growth of after-tax operating profit.</div></div>')
    on = [ENGINE_FLAG_TEXT[k] for k, v in flags.items() if v]
    notes = (f'<ul class="gr-flags">{"".join(f"<li>{qc.esc(t)}</li>" for t in on)}</ul>'
             if on else "")
    inner = f'<div class="gr-wrap">{qc.summary_row_html(left, right)}</div>{notes}'
    return qc.section_html("Capital engine", f'{qc.css(GROWTH_STYLE)}{inner}')


# ── revenue & net income ──────────────────────────────────────────────────

def revenue_earnings_series(fund, years: int) -> tuple[list[int], list, list]:
    """The last `years` fiscal years that have revenue, with revenue and net
    income ($M). Years without revenue are skipped, so a gap in coverage
    never shows as a $0 point."""
    fund = fund or {}
    pairs = sorted((y, r) for y, r in zip(fund.get("years") or [], fund.get("revenue") or [])
                   if r is not None)
    pairs = pairs[-years:] if years > 0 else []
    out_years = [y for y, _ in pairs]
    return (out_years, [r for _, r in pairs],
            [om._at(fund, "net_income", y) for y in out_years])


def yoy_labels(values) -> list[str]:
    """Year-on-year change per point as "+12.3%": empty for the first point,
    when either value is unknown, or when the previous one is ≤ 0 (a change
    from a loss has no meaningful percentage)."""
    out = []
    for i, v in enumerate(values):
        prev = values[i - 1] if i else None
        if prev is None or v is None or prev <= 0:
            out.append("")
        else:
            out.append(f"{(v / prev - 1) * 100:+.1f}%")
    return out


HEADROOM = 1.12


def label_headroom_range(values):
    """y-axis range from 0 (or below the lowest value, with room for its
    label) to HEADROOM x the highest value, so the year-on-year labels above
    the top points are not clipped. None without data."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    lo, hi = min(0.0, min(vals)), max(0.0, max(vals))
    span = (hi - lo) or 1.0
    pad = (HEADROOM - 1) * span
    return [lo - pad if lo < 0 else 0.0, hi * HEADROOM if hi > 0 else pad]


def revenue_earnings_figure(years, revenue, earnings, theme) -> go.Figure:
    """Revenue and net income ($M) per fiscal year as two lines, each point
    labelled with its year-on-year change (net income below its point, so
    the two label rows do not collide)."""
    xs = [f"FY{y}" for y in years]
    fig = go.Figure()
    for name, ys, colour, pos in (
            ("Revenue", list(revenue), theme.get("accent", "#81b29a"), "top center"),
            ("Net income", list(earnings), EARNINGS_COLOUR, "bottom center")):
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines+markers+text", name=name,
            line=dict(color=colour, width=2.5), marker=dict(size=7, color=colour),
            text=yoy_labels(ys), textposition=pos,
            textfont=dict(size=11, color=colour), cliponaxis=False,
            customdata=[om.fmt_money_m(v) for v in ys],
            hovertemplate=f"%{{customdata}}<extra>{name}</extra>",
        ))
    fig.update_layout(**CHART_LAYOUT)
    padded = label_headroom_range(list(revenue) + list(earnings))
    tickvals, ticktext = money_ticks(padded or [])
    # Ticks come from the padded range and the axis runs up to the top tick,
    # so the top gridline covers the label headroom; the bottom stays at 0
    # (or the padded minimum with losses) rather than snapping to a tick.
    y_range = [padded[0], tickvals[-1]] if padded else None
    fig.update_yaxes(range=y_range, tickmode="array",
                     tickvals=tickvals, ticktext=ticktext, showgrid=True,
                     gridwidth=1, gridcolor="rgba(128,128,128,0.15)", zeroline=True,
                     zerolinecolor="rgba(128,128,128,0.35)")
    fig.update_xaxes(type="category", showgrid=False)
    return fig


def cagr_table_html(fund) -> str:
    """Revenue / Net income / EPS / FCF x 3Y / 5Y / 10Y compound annual growth at the
    fiscal year (overview_metrics' rule: only when start and end are > 0),
    in the Overview's borderless growth-table style."""
    fund = fund or {}
    fy = om._fiscal_year(fund)
    head = "".join(f"<th>{n}Y</th>" for n in om.HORIZONS)
    rows = []
    # EPS and FCF came over from the Business tab's Key figures (2026-10-08),
    # so this is the one place with the full growth table.
    for label, key, get in (("Revenue", "revenue", None), ("Net income", "net_income", None),
                            ("EPS", "eps", None),
                            ("FCF", "fcf", lambda y: om._fcf_at(fund, y))):
        cells = "".join(
            f"<td>{qc.esc(om.fmt_pct(om._cagr(fund, key, fy, n, get) if fy else None, signed=True))}"
            f"</td>" for n in om.HORIZONS)
        rows.append(f"<tr><td>{label}</td>{cells}</tr>")
    return (f'{qc.css(METRICS_STYLE, GROWTH_STYLE)}<div class="gr-cagr">'
            f'<div class="ov-sub">COMPOUND ANNUAL GROWTH</div>'
            f'<table class="ov-gtab"><tr><th></th>{head}</tr>{"".join(rows)}</table></div>')


# ── Growth questions ───────────────────────────────────────────────────────

def questions_section_html(content, theme) -> str:
    """The two Growth flip cards, or the card set's one-line notice."""
    parsed = _parse(content)
    inner = (qc.grid_html(GROWTH, parsed["cards"], theme) if parsed
             else qc.notice_html(TITLE, theme))
    return qc.section_html("Growth questions", inner)
