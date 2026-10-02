"""Earnings tab: the EPS track record against the estimate, quarterly revenue
and EPS, and what the latest call said.

Sources:
- earnings_history (Supabase): Nasdaq's reported EPS vs the consensus per
  quarter, a row per quarter that grows each quarter (earnings_history.py).
- quarterly_results: revenue and GAAP diluted EPS per quarter from SEC
  companyfacts.
- the "Earnings Brief" pre-scan section (earnings_brief.py): the call's tone,
  three points and the next quarter's consensus.

Nasdaq stores a quarter as its month-end (2026-06-30) while a filing may end
on a 52/53-week date (2026-06-27): rows are matched on quarter end ± 10 days.
Nasdaq's EPS may be on an adjusted basis, so "vs Est." is Nasdaq's own
surprise, never the SEC EPS against Nasdaq's estimate.

Pure builders, no Streamlit import. House style: every dynamic text through
question_cards.esc, every <style> block on one line, a missing value shows as
"—", and no public function raises (bad data degrades to dashes, a notice or
None for a figure).
"""

from __future__ import annotations

import logging
from datetime import date

import plotly.graph_objects as go

import overview_metrics as om
import question_cards as qc
from earnings_brief import TITLE as BRIEF_TITLE
from earnings_brief import parse_earnings_brief
from phase_page import CHART_LAYOUT, PAYOUT_COLOUR, money_ticks

logger = logging.getLogger(__name__)

DASH = "—"
MINUS = "−"
MATCH_DAYS = 10

BEAT_COLOUR = "#2f8f4e"
MISS_COLOUR = "#c0603f"
NO_ESTIMATE_COLOUR = "#9aa0a6"
TONE_COLOURS = {"Positive": "#2f8f4e", "Neutral": "#86868b", "Cautious": "#c79a3a"}

CONSISTENT, MIXED = 75.0, 40.0      # % of quarters beaten
LABELS = ("Consistent beater", "Mixed", "Often misses")

CAPTION = "EPS estimates: Nasdaq consensus. History grows each quarter."
TABLE_NOTE = ("Revenue and EPS: SEC filings (GAAP, diluted). Est. and vs Est.: Nasdaq "
              "consensus and Nasdaq's own surprise, which may use adjusted EPS.")
NO_ESTIMATES = "No EPS estimates on record yet."
NO_QUARTERS = "No quarterly results in the SEC filings."

_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"
_HAIRLINE = "color-mix(in srgb, var(--text) 10%, transparent)"

STYLE = f"""<style>
.er-tiles{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-top:14px}}
@media (max-width:900px){{.er-tiles{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (max-width:480px){{.er-tiles{{grid-template-columns:minmax(0,1fr)}}}}
.er-tile{{background:{_INNER};border-radius:16px;padding:16px 18px;box-sizing:border-box;
  min-width:0}}
.er-lbl{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 3px}}
.er-val{{font-size:22px;font-weight:700;color:var(--text);line-height:1.2;margin:2px 0 4px;
  overflow-wrap:anywhere}}
.er-cap{{font-size:12px;color:var(--text-muted);line-height:1.35}}
.er-head{{display:flex;flex-wrap:wrap;align-items:center;gap:10px}}
.er-chip{{font-size:.7rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  padding:3px 10px;border-radius:10px;white-space:nowrap}}
.er-sentence{{margin:0;font-size:15px;line-height:1.5;color:var(--text)}}
.er-note{{margin:12px 2px 0;font-size:12px;color:var(--text-muted);line-height:1.4}}
.er-qtr{{font-size:13px;font-weight:600;color:var(--text-muted)}}
.er-summary{{margin:12px 0 0;font-size:15px;line-height:1.55;color:var(--text)}}
.er-points{{background:{_INNER};border-radius:16px;padding:6px 18px;margin-top:14px}}
.er-points ul{{margin:0;padding:0;list-style:none}}
.er-points li{{font-size:14px;color:var(--text);line-height:1.5;padding:9px 0;
  border-bottom:1px solid {_HAIRLINE}}}
.er-points li:last-child{{border-bottom:none}}
.er-stale{{margin:10px 0 0;font-size:13px;color:var(--text-muted);font-style:italic}}
table.er-tab{{width:100%;border-collapse:collapse;font-size:14px;margin-top:6px}}
table.er-tab th{{text-align:right;font-size:11px;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--text-muted);padding:6px 8px;
  border-bottom:1px solid {_HAIRLINE}}}
table.er-tab td{{text-align:right;padding:7px 8px;color:var(--text);
  border-bottom:1px solid {_HAIRLINE};font-variant-numeric:tabular-nums;white-space:nowrap}}
table.er-tab th:first-child,table.er-tab td:first-child{{text-align:left}}
.er-scroll{{overflow-x:auto}}
</style>"""


# ── small helpers ──────────────────────────────────────────────────────────

def _num(x):
    if isinstance(x, bool):
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float("inf") else None


def _day(text):
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _rows(history):
    """Valid earnings_history rows, ascending by quarter end."""
    if not isinstance(history, list):
        return []
    out = [r for r in history if isinstance(r, dict) and _day(r.get("fiscal_qtr_end"))]
    return sorted(out, key=lambda r: str(r["fiscal_qtr_end"]))


def _surprise(row):
    """Nasdaq's surprise (%), else (eps − consensus) / |consensus|."""
    s = _num(row.get("surprise_pct"))
    if s is not None:
        return s
    eps, cons = _num(row.get("eps")), _num(row.get("eps_consensus"))
    if eps is None or cons is None or cons == 0:
        return None
    return (eps - cons) / abs(cons) * 100.0


def _result(row):
    eps, cons = _num(row.get("eps")), _num(row.get("eps_consensus"))
    if eps is None or cons is None:
        return None
    if eps > cons:
        return "Beat"
    if eps < cons:
        return "Miss"
    return "In line"


def _signed_pct(x, digits=1):
    if x is None:
        return DASH
    text = f"{abs(x):.{digits}f}%"
    return (MINUS if x < 0 else "+") + text if round(x, digits) != 0 else f"{0:.{digits}f}%"


def _usd_eps(x):
    if x is None:
        return DASH
    return f"{MINUS if x < 0 else ''}${abs(x):.2f}"


def _usd_big(x):
    """Raw dollars → "$80.5B" / "$950M"."""
    if x is None:
        return DASH
    return om.fmt_money_m(x / 1e6)


def _month(d):
    return d.strftime("%b %Y")


def match_estimate(end_iso, history):
    """The earnings_history row for a quarter ending `end_iso`, matched on
    quarter end ± 10 days (closest wins), else None."""
    try:
        end = _day(end_iso)
        if end is None:
            return None
        hits = [(abs((_day(r["fiscal_qtr_end"]) - end).days), r) for r in _rows(history)]
        hits = [(d, r) for d, r in hits if d <= MATCH_DAYS]
        return min(hits, key=lambda h: h[0])[1] if hits else None
    except Exception:
        return None


# ── beat statistics ────────────────────────────────────────────────────────

def _label(beats, n):
    if n < 2:
        return None
    share = beats / n * 100.0
    if share >= CONSISTENT:
        return LABELS[0]
    if share >= MIXED:
        return LABELS[1]
    return LABELS[2]


def _sentence(beats, n, avg):
    if not n:
        return NO_ESTIMATES
    word = "quarter" if n == 1 else "quarters"
    lead = f"Beat the EPS estimate in {beats} of the last {n} {word}"
    if avg is None:
        return f"{lead}."
    if avg < 0:
        return f"{lead}; on average EPS came in {abs(avg):.0f}% below it."
    return f"{lead}, by {avg:.0f}% on average."


def beat_stats(history) -> dict:
    """Over every quarter with both an actual and an estimate: n, beats, the
    label, the average surprise (%), the last quarter {fiscal_qtr_end,
    date_reported, result, surprise} and the summary sentence."""
    counted = [r for r in _rows(history) if _result(r) is not None]
    n = len(counted)
    beats = sum(1 for r in counted if _result(r) == "Beat")
    surprises = [s for s in (_surprise(r) for r in counted) if s is not None]
    avg = sum(surprises) / len(surprises) if surprises else None
    last = None
    if counted:
        r = counted[-1]
        last = {"fiscal_qtr_end": str(r["fiscal_qtr_end"])[:10],
                "date_reported": r.get("date_reported"),
                "result": _result(r), "surprise": _surprise(r)}
    return {"n": n, "beats": beats, "label": _label(beats, n), "avg_surprise": avg,
            "last": last, "sentence": _sentence(beats, n, avg)}


# ── Earnings Brief ─────────────────────────────────────────────────────────

def _brief(content):
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        return parse_earnings_brief(content)
    except Exception:
        return None


def _newest_quarter(history):
    rows = _rows(history)
    return _day(rows[-1]["fiscal_qtr_end"]) if rows else None


def is_out_of_date(brief, history) -> bool:
    """True when earnings_history has a quarter newer than the brief's (by
    more than the ± 10-day matching window)."""
    try:
        newest = _newest_quarter(history)
        covered = _day(brief["quarter"]["fiscal_qtr_end"])
        return bool(newest and covered and (newest - covered).days > MATCH_DAYS)
    except Exception:
        return False


# ── HTML ───────────────────────────────────────────────────────────────────

def _tile(label, value, caption):
    return (f'<div class="er-tile"><div class="er-lbl">{qc.esc(label.upper())}</div>'
            f'<div class="er-val">{qc.esc(value)}</div>'
            f'<div class="er-cap">{qc.esc(caption)}</div></div>')


def _chip(text, colour):
    return (f'<span class="er-chip" style="background:{colour}22;color:{colour}">'
            f'{qc.esc(text)}</span>')


def _label_colour(label):
    return {LABELS[0]: BEAT_COLOUR, LABELS[1]: TONE_COLOURS["Cautious"],
            LABELS[2]: MISS_COLOUR}.get(label, NO_ESTIMATE_COLOUR)


def _next_tile(brief):
    nxt = (brief or {}).get("next") or {}
    eps, rev, analysts = nxt.get("eps_consensus"), nxt.get("revenue_consensus_usd"), \
        nxt.get("analysts")
    value = DASH if eps is None else f"EPS {_usd_eps(eps)}"
    bits = []
    if rev is not None:
        bits.append(f"Revenue {_usd_big(rev)}")
    if analysts is not None:
        bits.append(f"{analysts} analyst" + ("" if analysts == 1 else "s"))
    if nxt.get("period"):
        bits.append(nxt["period"])
    return _tile("Next quarter", value, " · ".join(bits) or "consensus not available")


def _summary_tiles(s, brief):
    n = s["n"]
    last = s["last"]
    if last:
        res = last["result"]
        value = res if res == "In line" else f"{res} {_signed_pct(last['surprise'])}"
        cap = f"quarter ending {_month(_day(last['fiscal_qtr_end']))}"
        if last.get("date_reported"):
            cap += f" · reported {last['date_reported']}"
    else:
        value, cap = DASH, "no estimate on record"
    return [
        _tile("EPS beats", f"{s['beats']} of {n}" if n else DASH,
              "quarters above the estimate"),
        _tile("Average surprise", _signed_pct(s["avg_surprise"]),
              "actual vs estimate, per quarter"),
        _tile("Last quarter", value, cap),
        _next_tile(brief),
    ]


def summary_section_html(history, brief_content, theme) -> str:
    """The white "Earnings summary" section: label chip and sentence, four
    tiles (beats, average surprise, last quarter, next quarter) and the
    source caption."""
    try:
        s = beat_stats(history)
        head = _chip(s["label"], _label_colour(s["label"])) if s["label"] else ""
        sentence = s["sentence"]
        tiles = _summary_tiles(s, _brief(brief_content))
    except Exception as e:
        logger.warning("Earnings summary failed: %s", e)
        head, sentence = "", NO_ESTIMATES
        tiles = [_tile(n, DASH, "") for n in
                 ("EPS beats", "Average surprise", "Last quarter", "Next quarter")]
    inner = (f'{qc.css(STYLE)}<div class="er-head">{head}'
             f'<p class="er-sentence">{qc.esc(sentence)}</p></div>'
             f'<div class="er-tiles">{"".join(tiles)}</div>'
             f'<p class="er-note">{qc.esc(CAPTION)}</p>')
    return qc.section_html("Earnings summary", inner)


def _quarters(quarters):
    if not isinstance(quarters, list):
        return []
    return [q for q in quarters if isinstance(q, dict) and _day(q.get("end"))]


def quarterly_table_html(quarters, history) -> str:
    """Period | Revenue | YoY | EPS | Est. | vs Est., newest first, with the
    estimate joined from earnings_history on quarter end ± 10 days."""
    style = qc.css(STYLE)
    try:
        rows = _quarters(quarters)
        if not rows:
            return f'{style}<p class="er-note">{qc.esc(NO_QUARTERS)}</p>'
        head = ("<tr><th>Period</th><th>Revenue</th><th>YoY</th><th>EPS</th>"
                "<th>Est.</th><th>vs Est.</th></tr>")
        body = []
        for q in sorted(rows, key=lambda r: r["end"], reverse=True):
            try:
                est = match_estimate(q["end"], history)
            except Exception:
                est = None
            est_eps = _num((est or {}).get("eps_consensus"))
            vs = _surprise(est) if est else None
            values = (q.get("fiscal_label") or f"Q ending {q['end']}",
                      _usd_big(_num(q.get("revenue"))),
                      _signed_pct(_pct(q.get("revenue_yoy"))),
                      _usd_eps(_num(q.get("eps"))),
                      _usd_eps(est_eps),
                      _signed_pct(vs))
            body.append("<tr>" + "".join(f"<td>{qc.esc(v)}</td>" for v in values) + "</tr>")
        return (f'{style}<div class="er-scroll"><table class="er-tab">{head}{"".join(body)}'
                f'</table></div><p class="er-note">{qc.esc(TABLE_NOTE)}</p>')
    except Exception as e:
        logger.warning("Earnings table failed: %s", e)
        return f'{style}<p class="er-note">{qc.esc(NO_QUARTERS)}</p>'


def _pct(fraction):
    x = _num(fraction)
    return None if x is None else x * 100.0


def _points_html(points):
    items = "".join(f'<li><b>{qc.esc(p["label"])}</b>: {qc.esc(p["text"])}</li>'
                    for p in points)
    return f'<div class="er-points"><ul>{items}</ul></div>'


def _notice(theme):
    return qc.section_html("Latest call", qc.notice_html(BRIEF_TITLE, theme or {
        "text_muted": "#888"}))


def latest_call_section_html(content, history, theme) -> str:
    """The white "Latest call" section from the Earnings Brief: tone chip,
    quarter, summary, three points and the source; a muted "Out of date"
    note when Nasdaq has a newer quarter; the notice when there is no valid
    brief."""
    theme = theme if isinstance(theme, dict) else {}
    theme = {"text_muted": "#888", **theme}
    b = _brief(content)
    if b is None:
        return _notice(theme)
    try:
        tone = b["tone"]
        head = (f'<div class="er-head">{_chip(tone, TONE_COLOURS.get(tone, NO_ESTIMATE_COLOUR))}'
                f'<span class="er-qtr">{qc.esc(b["quarter"]["label"])}</span></div>')
        stale = (f'<p class="er-stale">Out of date — covers {qc.esc(b["quarter"]["label"])}</p>'
                 if is_out_of_date(b, history) else "")
        inner = (f'{qc.css(STYLE)}{head}{stale}'
                 f'<p class="er-summary">{qc.esc(b["summary"])}</p>'
                 f'{_points_html(b["points"])}'
                 f'<p class="er-note">As of {qc.esc(b["as_of"])} · Source: '
                 f'{qc.esc(b["source"])}</p>')
        return qc.section_html("Latest call", inner)
    except Exception as e:
        logger.warning("Latest call failed: %s", e)
        return _notice(theme)


# ── figures ────────────────────────────────────────────────────────────────

def _x_label(row, quarters):
    end = _day(row["fiscal_qtr_end"])
    best = None
    for q in _quarters(quarters):
        d = abs((_day(q["end"]) - end).days)
        if d <= MATCH_DAYS and (best is None or d < best[0]) and q.get("fiscal_label") \
                and not str(q["fiscal_label"]).startswith("Q ending"):
            best = (d, q["fiscal_label"])
    return best[1] if best else _month(end)


def eps_figure(history, theme, quarters=None):
    """Per quarter a filled dot for the actual EPS (green beat, red miss, grey
    without an estimate) labelled with its value, and an open circle for the
    estimate. None without any actual."""
    try:
        theme = theme if isinstance(theme, dict) else {}
        rows = [r for r in _rows(history) if _num(r.get("eps")) is not None]
        if not rows:
            return None
        xs = [_x_label(r, quarters) for r in rows]
        eps = [_num(r["eps"]) for r in rows]
        est = [_num(r.get("eps_consensus")) for r in rows]
        colours = [{"Beat": BEAT_COLOUR, "Miss": MISS_COLOUR}.get(_result(r), NO_ESTIMATE_COLOUR)
                   for r in rows]
        labels = [f"{'-' if v < 0 else ''}${abs(v):.2f}" for v in eps]
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=xs, y=eps, mode="markers+text", name="Actual", text=labels,
            textposition="top center", textfont=dict(size=12, color=theme.get("text", "#444")),
            marker=dict(size=14, color=colours),
            customdata=[f"{lbl} · {_result(r) or 'no estimate'}" for lbl, r in zip(labels, rows)],
            hovertemplate="%{customdata}<extra>Actual</extra>"))
        fig.add_trace(go.Scatter(
            x=xs, y=est, mode="markers", name="Estimate",
            marker=dict(size=14, symbol="circle-open",
                        color=theme.get("text_muted", "#888"), line=dict(width=2)),
            customdata=[DASH if v is None else f"${v:.2f}" for v in est],
            hovertemplate="%{customdata}<extra>Estimate</extra>"))
        fig.update_layout(**{**CHART_LAYOUT, "height": 280})
        values = [v for v in eps + est if v is not None]
        pad = max((max(values) - min(values)) * 0.25, abs(max(values)) * 0.1, 0.05)
        fig.update_yaxes(range=[min(min(values), 0) - pad * 0.2, max(values) + pad],
                         tickprefix="$", showgrid=True, gridwidth=1,
                         gridcolor="rgba(128,128,128,0.15)", zeroline=True,
                         zerolinecolor="rgba(128,128,128,0.35)")
        fig.update_xaxes(type="category", showgrid=False)
        return fig
    except Exception as e:
        logger.warning("EPS figure failed: %s", e)
        return None


def quarterly_figure(quarters, theme):
    """Revenue bars ($M, labelled with revenue YoY) and diluted EPS as a line
    on a second axis, per quarter. None without any quarter."""
    try:
        theme = theme if isinstance(theme, dict) else {}
        rows = sorted(_quarters(quarters), key=lambda r: r["end"])
        if not rows:
            return None
        xs = [r.get("fiscal_label") or f"Q ending {r['end']}" for r in rows]
        rev = [None if _num(r.get("revenue")) is None else _num(r["revenue"]) / 1e6 for r in rows]
        yoy = [_pct(r.get("revenue_yoy")) for r in rows]
        eps = [_num(r.get("eps")) for r in rows]
        eps_yoy = [_pct(r.get("eps_yoy")) for r in rows]
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=xs, y=rev, name="Revenue", marker_color=theme.get("accent", "#81b29a"),
            text=["" if v is None else _signed_pct(v).replace(MINUS, "-") for v in yoy],
            textposition="outside", cliponaxis=False,
            textfont=dict(size=11, color=theme.get("text_muted", "#888")),
            customdata=[f"{om.fmt_money_m(v)} · YoY {_signed_pct(y)}" for v, y in zip(rev, yoy)],
            hovertemplate="%{customdata}<extra>Revenue</extra>"))
        fig.add_trace(go.Scatter(
            x=xs, y=eps, name="EPS (diluted)", mode="lines+markers", yaxis="y2",
            line=dict(color=PAYOUT_COLOUR, width=2.5), marker=dict(size=7, color=PAYOUT_COLOUR),
            customdata=[f"{_usd_eps(v)} · YoY {_signed_pct(y)}" for v, y in zip(eps, eps_yoy)],
            hovertemplate="%{customdata}<extra>EPS</extra>"))
        fig.update_layout(**{**CHART_LAYOUT, "height": 320,
                             "margin": dict(l=10, r=10, t=40, b=10)})
        tickvals, ticktext = money_ticks(rev)
        top = max([v for v in rev if v is not None] or [0])
        fig.update_layout(
            yaxis=dict(rangemode="tozero", tickmode="array", tickvals=tickvals,
                       ticktext=ticktext, showgrid=True, gridwidth=1,
                       gridcolor="rgba(128,128,128,0.15)",
                       range=[0, top * 1.15] if top > 0 else None),
            yaxis2=dict(overlaying="y", side="right", tickprefix="$", showgrid=False,
                        rangemode="tozero"),
            bargap=0.35)
        fig.update_xaxes(type="category", showgrid=False)
        return fig
    except Exception as e:
        logger.warning("Quarterly figure failed: %s", e)
        return None
