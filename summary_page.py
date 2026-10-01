"""HTML for the Summary tab: Verdict, Scorecard, Robustness and Pre-mortem &
action triggers, as four white sections.

Pure string builders, read-only: the data is written through the MCP
(save_prescan_section, set_robustness, set_premortem). Same house style as the
Business/Moat/Risk tabs: question_cards.section_html around flat inner cards,
every <style> on one line, every dynamic text through question_cards.esc (a
bare `$` pair would render as LaTeX), and no newlines inside the HTML (one
ends Streamlit's HTML block early).

render_robustness_table and render_premortem moved here from streamlit_app
when the Pre-Scan tab went; their output is unchanged except that `$` is now
an entity.
"""

import re

import question_cards as qc
import robustness as _rob
from prescan_render import band_tone, parse_verdict_section, split_trigger, three_state_html

DASH = "—"
MINUS = "−"

_FONT = ("'DM Sans', -apple-system, BlinkMacSystemFont, 'Helvetica Neue', "
         "Arial, sans-serif")

STYLE = """<style>
.sp-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:stretch}
@media (max-width:760px){.sp-row{grid-template-columns:1fr}}
.sp-card{background:var(--qc-inner, var(--bg-secondary));color:var(--text);border-radius:16px;
  padding:18px 22px;box-sizing:border-box;min-width:0}
.sp-title{font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 12px}
.sp-big{font-size:1.9rem;font-weight:700;line-height:1.1;margin:0 0 4px}
.sp-gap{font-size:.95rem;font-weight:600;margin:0 0 14px}
.sp-kv{display:flex;justify-content:space-between;gap:12px;padding:7px 0;font-size:.86rem;
  border-top:1px solid color-mix(in srgb, var(--text) 10%, transparent)}
.sp-kv span:first-child{color:var(--text-muted)}
.sp-kv span:last-child{font-weight:600;white-space:nowrap}
.sp-note{margin-top:10px;font-size:.8rem;color:var(--text-muted);line-height:1.45}
.sp-rate{padding:8px 0;border-top:1px solid color-mix(in srgb, var(--text) 10%, transparent)}
.sp-rate.sp-first{border-top:none;padding-top:0}
.sp-rhead{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
.sp-rlabel{font-size:.86rem;font-weight:600}
.sp-rnote{font-size:.8rem;color:var(--text-muted);line-height:1.4;margin-top:2px}
.sp-phase{font-size:1.15rem;font-weight:700;margin:0 0 10px}
.sp-sum{font-size:.88rem;line-height:1.55;margin:0}
.sp-metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-top:16px}
@media (max-width:1100px){.sp-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.sp-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}}
.sp-metric{background:var(--qc-inner, var(--bg-secondary));border-radius:14px;padding:12px 14px;
  min-width:0}
.sp-mname{font-size:.68rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 6px;overflow-wrap:anywhere}
.sp-mrate{display:flex;align-items:center;gap:6px;font-size:.82rem;font-weight:700}
.sp-mrate i{display:inline-block;width:10px;height:10px;border-radius:50%}
.sp-mval{font-size:.82rem;line-height:1.4;margin-top:4px;overflow-wrap:anywhere}
</style>"""


def _num(v):
    """A positive float, or None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _money(v, symbol="$"):
    f = _num(v)
    return DASH if f is None else qc.esc(f"{symbol}{f:,.2f}")


def _muted(theme):
    return (theme or {}).get("text_muted", "#888")


# ── Verdict ──────────────────────────────────────────────────────────────────

def _verdict_card(text, theme):
    title = "Verdict"
    if not (isinstance(text, str) and text.strip()):
        return (f'<div class="ms-card"><div class="mc-q" style="color:{_muted(theme)};'
                f'margin-bottom:12px">{title.upper()}</div>'
                f'<div class="sp-note">No Investment Summary yet. Ask Claude via the MCP '
                f'to fill the "Investment Summary" pre-scan section for this ticker.</div></div>')
    v = parse_verdict_section(text)
    if v is None:
        return (f'<div class="ms-card"><div class="mc-q" style="color:{_muted(theme)};'
                f'margin-bottom:12px">{title.upper()}</div>'
                f'<div class="sp-note">The Investment Summary is not in the verdict format '
                f'yet. Ask Claude via the MCP to rerun that pre-scan section.</div></div>')
    tone = band_tone(v["label"]) or _muted(theme)
    box = qc.word_box_html("●", v["label"] or DASH, tone)
    head = title.upper()
    if v["qualifiers"]:
        head += " · " + " · ".join(q.upper() for q in v["qualifiers"])
    return qc.summary_card_html(qc.esc(head), box, qc.bold(v["summary"]), v["bullets"][:3], theme)


def _price_card(valuation_summary, price, theme, symbol="$"):
    vs = valuation_summary if isinstance(valuation_summary, dict) else {}
    low, mid, high = (_num(vs.get(k)) for k in
                      ("weighted_fv_low", "weighted_fv_mid", "weighted_fv_high"))
    buy = _num(vs.get("buy_price"))
    px = _num(price) or _num(vs.get("stock_price"))

    gap = ""
    if px is not None and mid is not None:
        pct = round((px / mid - 1) * 100)
        if pct > 0:
            gap = (f'<div class="sp-gap" style="color:{band_tone("red")}">'
                   f'+{pct}% above fair value</div>')
        elif pct < 0:
            gap = (f'<div class="sp-gap" style="color:{band_tone("green")}">'
                   f'{MINUS}{-pct}% below fair value</div>')
        else:
            gap = '<div class="sp-gap">At fair value</div>'
    elif px is not None:
        gap = f'<div class="sp-gap" style="color:{_muted(theme)}">No fair value yet</div>'

    rows = "".join(
        f'<div class="sp-kv"><span>{label}</span><span>{_money(val, symbol)}</span></div>'
        for label, val in (("Fair value low", low), ("Fair value mid", mid),
                           ("Fair value high", high), ("Buy price", buy)))
    note = ""
    if px is not None and buy is not None and px < buy:
        note = (f'<div class="sp-note">Under the buy price of {_money(buy, symbol)}.</div>')
    return (f'<div class="ms-card sp-price">'
            f'<div class="mc-q" style="color:{_muted(theme)};margin-bottom:12px">'
            f'PRICE VS FAIR VALUE</div>'
            f'<div class="sp-big">{_money(px, symbol)}</div>{gap}{rows}{note}</div>')


def verdict_section_html(investment_summary_text, valuation_summary, price, theme, symbol="$"):
    """White section "Verdict": the Investment Summary verdict beside a price
    card (fair value low/mid/high, buy price, current price and its gap to fair
    value mid). Missing pieces read as a dash or a muted note; never raises."""
    row = qc.summary_row_html(_verdict_card(investment_summary_text, theme),
                              _price_card(valuation_summary, price, theme, symbol))
    return qc.section_html("Verdict", qc.css(STYLE) + row)


# ── Scorecard ────────────────────────────────────────────────────────────────

_RATINGS = (("business_description", "Business"), ("moat", "Moat"),
            ("long_term_potential", "Long-term potential"))


def _d(x):
    return x if isinstance(x, dict) else {}


def _phase_text(phase):
    if isinstance(phase, dict):
        num, name = phase.get("number"), str(phase.get("name") or "").strip()
    else:
        num, name = phase, ""
    if num in (None, "") and not name:
        return ""
    out = f"Phase {num if num not in (None, '') else '?'}"
    return out + (f" · {name}" if name else "")


def _rating_row(label, rating, note, theme, first=False):
    dots = three_state_html(rating, ("", "", ""), size=17, theme=theme)
    note_html = f'<div class="sp-rnote">{qc.esc(note)}</div>' if str(note or "").strip() else ""
    return (f'<div class="sp-rate{" sp-first" if first else ""}"><div class="sp-rhead">'
            f'<span class="sp-rlabel">{qc.esc(label)}</span><div>{dots}</div></div>'
            f'{note_html}</div>')


def _metric_card(km, theme):
    km = _d(km)
    rating = str(km.get("rating") or "").strip().lower()
    tone = band_tone(rating) if rating in ("red", "yellow", "green") else None
    word = rating.capitalize() if tone else DASH
    dot = f'<i style="background:{tone}"></i>' if tone else ""
    return (f'<div class="sp-metric"><div class="sp-mname">{qc.esc(km.get("name") or DASH)}</div>'
            f'<div class="sp-mrate" style="color:{tone or _muted(theme)}">{dot}<span>{word}</span>'
            f'</div><div class="sp-mval">{qc.esc(km.get("value") or "")}</div></div>')


def scorecard_section_html(scorecard, theme):
    """White section "Scorecard": phase and three-sentence summary beside the
    four rated rows (business, moat, long-term potential, execution risk),
    then the key metrics as a grid. No valuation row: the DCF owns that."""
    if not isinstance(scorecard, dict) or not scorecard:
        return qc.section_html("Scorecard", qc.notice_html("Scorecard", theme))

    ap = _d(scorecard.get("all_phases"))
    er = _d(scorecard.get("execution_risk"))
    rows = "".join(_rating_row(label, _d(ap.get(k)).get("rating"), _d(ap.get(k)).get("note"),
                               theme, first=(i == 0)) for i, (k, label) in enumerate(_RATINGS))
    rows += _rating_row("Execution risk", er.get("rating"), er.get("note"), theme)

    phase = _phase_text(scorecard.get("phase"))
    summary = str(scorecard.get("summary") or "").strip()
    left = (f'<div class="sp-card"><div class="sp-title">Phase &amp; summary</div>'
            + (f'<div class="sp-phase">{qc.esc(phase)}</div>' if phase else "")
            + (f'<p class="sp-sum">{qc.esc(" ".join(summary.split()))}</p>' if summary
               else f'<p class="sp-sum" style="color:{_muted(theme)}">No summary.</p>')
            + '</div>')
    right = f'<div class="sp-card"><div class="sp-title">Ratings</div>{rows}</div>'

    kms = scorecard.get("key_metrics")
    kms = [k for k in kms if isinstance(k, dict)] if isinstance(kms, list) else []
    metrics = ("<div class=\"sp-metrics\">" + "".join(_metric_card(k, theme) for k in kms)
               + "</div>") if kms else ""
    return qc.section_html("Scorecard",
                           qc.css(STYLE) + f'<div class="sp-row">{left}{right}</div>{metrics}')


# ── Robustness ───────────────────────────────────────────────────────────────

def render_robustness_table(cfg: dict, theme: dict) -> str:
    """Render the Prasad robustness assessment as a headline verdict card plus
    one three-state row per axis. Pure HTML-string builder (no Streamlit calls).

    The verdict gets the large selector because it is the answer; the six axes
    get compact ones because they are the working. Three circles rather than a
    marker on a gradient: the bands ARE three states, and a continuous track
    invited reading a position between them that the data does not carry.
    """
    _esc = qc.esc
    rob = (cfg or {}).get("robustness") or {}
    axes = rob.get("axes") or {}
    text = theme.get("text", "#111")
    muted = theme.get("text_muted", "#888")
    if not axes:
        return (f'<div style="color:{muted};font-size:0.85rem;margin:6px 0 14px">'
                'Robustness not yet assessed — run the Robustness section.</div>')

    border_light = theme.get("border_light", "#e8e8ed")
    bg = theme.get("bg_secondary", "#f4f2ee")

    def _val_label(key, ax):
        if key == "roce" and ax.get("value") is not None:
            return f'{ax["value"]:.0f}% {ax.get("metric", "ROCE")}'
        if key == "net_debt" and ax.get("value") is not None:
            v = ax["value"]
            return "net cash" if v <= 0 else f'{v:.1f}× EBITDA'
        return ax.get("note", "") or ""

    # ── Headline: the verdict, large ──
    verdict = rob.get("verdict", "")
    reason = _esc(rob.get("verdict_reason", ""))
    head = (
        f'<div style="background:{bg};border-radius:16px;padding:20px 22px 16px;'
        f'margin-bottom:14px">'
        f'<div style="text-align:center;font-size:0.7rem;font-weight:700;'
        f'letter-spacing:0.09em;color:{muted};text-transform:uppercase;'
        f'margin-bottom:16px">How robust is this business?</div>'
        + three_state_html(verdict, ("Fragile", "Borderline", "Robust"),
                           size=48, theme=theme)
        + (f'<div style="text-align:center;margin-top:16px;padding-top:14px;'
           f'border-top:1px solid {border_light};font-size:0.9rem;'
           f'line-height:1.5;color:{text}">{reason}</div>' if reason else '')
        + '</div>'
    )

    # ── Working: one compact row per axis ──
    rows = []
    for key, label, is_db, _src in _rob.AXES:
        ax = axes.get(key, {})
        note = _esc(_val_label(key, ax))
        flag = (f'<span title="deal-breaker" style="color:{muted};'
                f'font-size:0.68rem">&#9873;</span>' if is_db else '')
        rows.append(
            f'<div style="display:flex;align-items:center;gap:14px;'
            f'padding:7px 0;border-top:1px solid {border_light};font-size:0.82rem">'
            f'<div style="width:168px;flex:none;color:{text};white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis">{label} {flag}</div>'
            f'<div style="flex:none">'
            + three_state_html(ax.get("band"), ("", "", ""), size=17, theme=theme)
            + f'</div>'
            f'<div title="{note}" style="flex:1;min-width:0;color:{muted};'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{note}</div>'
            f'</div>'
        )

    legend = ('&#9873; deal-breaker (ROCE &middot; net debt &middot; management) — '
              'a red sinks the verdict, amber caps it at borderline')
    foot = (f'<div style="color:{muted};font-size:0.7rem;margin-top:10px;'
            f'border-top:1px solid {border_light};padding-top:8px">{legend}</div>')
    return (f'<div style="font-family:{_FONT};margin:2px 0 4px">'
            f'{head}{"".join(rows)}{foot}</div>')


def robustness_section_html(cfg, theme):
    return qc.section_html("Robustness", render_robustness_table(cfg, theme or {}))


# ── Pre-mortem ───────────────────────────────────────────────────────────────

def render_premortem(pm, theme):
    """Render a structured pre-mortem as a decision board.

    Five stacked lists ran to forty-odd items on the bigger tickers — CPRT has
    45 — which is a wall you skim rather than a rule you check. The two lists
    you act on sit side by side, the one you deliberately do NOT act on sits
    beside them greyed, and process goes underneath.

    Returns None for a legacy string / empty (caller shows a fallback).
    """
    if not isinstance(pm, dict):
        return None
    _esc = qc.esc

    muted = theme.get("text_muted", "#888")
    txt = theme.get("text", "#111")
    border = theme.get("border_light", "#e8e8ed")
    bg = theme.get("bg_secondary", "#f4f2ee")
    red, green = "#c0603f", "#2f8f4e"

    def _items(key):
        return [str(i) for i in (pm.get(key) or []) if str(i).strip()]

    sell, add, ignore = _items("sell"), _items("add"), _items("ignore")
    discipline = _items("discipline")
    current = str(pm.get("current", "") or "").strip()
    if not any((sell, add, ignore, discipline, current)):
        return None

    def _column(title, items, tone, dim=False):
        if not items:
            return ""
        rows = []
        for raw in items:
            cat, body = split_trigger(raw)
            tag = (f'<div style="font-size:0.62rem;font-weight:700;'
                   f'letter-spacing:0.07em;color:{tone};opacity:0.9;'
                   f'margin-bottom:1px">{_esc(cat)}</div>' if cat else "")
            rows.append(
                f'<li style="margin:0 0 9px 0;line-height:1.45;'
                f'font-size:0.86rem;color:{muted if dim else txt}">'
                f'{tag}{_esc(body)}</li>'
            )
        return (
            f'<div style="flex:1;min-width:230px">'
            f'<div style="display:flex;align-items:baseline;gap:7px;'
            f'padding-bottom:7px;margin-bottom:10px;'
            f'border-bottom:2px solid {tone}{"55" if dim else ""}">'
            f'<span style="font-size:0.68rem;font-weight:700;letter-spacing:0.08em;'
            f'text-transform:uppercase;color:{tone}">{title}</span>'
            f'<span style="font-size:0.68rem;color:{muted}">{len(items)}</span>'
            f'</div>'
            f'<ul style="margin:0;padding-left:16px">{"".join(rows)}</ul></div>'
        )

    parts = []
    if current:
        parts.append(
            f'<div style="background:{bg};border-radius:12px;padding:12px 16px;'
            f'margin-bottom:16px;font-size:0.86rem;line-height:1.5;color:{txt}">'
            f'<span style="font-size:0.62rem;font-weight:700;letter-spacing:0.08em;'
            f'text-transform:uppercase;color:{muted};display:block;'
            f'margin-bottom:4px">Current view</span>'
            f'{_esc(current)}</div>'
        )

    # Sell first: the trigger you most need to have decided in advance is the
    # one you will least want to act on in the moment.
    cols = (_column("Sell when", sell, red)
            + _column("Add when", add, green)
            + _column("Not a reason", ignore, muted, dim=True))
    if cols:
        parts.append(f'<div style="display:flex;gap:26px;flex-wrap:wrap;'
                     f'align-items:flex-start">{cols}</div>')

    if discipline:
        # Process, not triggers — folded away so it stops competing with the
        # rules you actually check a position against.
        rows = "".join(
            f'<li style="margin:0 0 6px 0;line-height:1.45;font-size:0.82rem;'
            f'color:{muted}">{_esc(i)}</li>' for i in discipline
        )
        parts.append(
            f'<details style="margin-top:18px;padding-top:12px;'
            f'border-top:1px solid {border}">'
            f'<summary style="cursor:pointer;font-size:0.68rem;font-weight:700;'
            f'letter-spacing:0.08em;text-transform:uppercase;color:{muted}">'
            f'Discipline ({len(discipline)})</summary>'
            f'<ul style="margin:10px 0 0;padding-left:16px">{rows}</ul></details>'
        )

    return "".join(parts)


def premortem_section_html(pm, theme):
    """White section "Pre-mortem & action triggers": the decision board, an
    older free-text pre-mortem as escaped text, or a muted notice."""
    theme = theme or {}
    label = "Pre-mortem & action triggers"
    board = render_premortem(pm, theme)
    if board:
        return qc.section_html(label, board)
    if isinstance(pm, str) and pm.strip():
        lines = [qc.esc(ln) for ln in re.split(r"\r?\n", pm.strip())]
        return qc.section_html(
            label, f'<div style="font-size:.86rem;line-height:1.5;color:{theme.get("text", "#111")}">'
                   + "<br>".join(lines) + "</div>")
    return qc.section_html(
        label, f'<div style="color:{_muted(theme)};font-size:.9rem;padding:14px 2px">'
               f'No pre-mortem yet. Ask Claude via the MCP (set_premortem) to write the '
               f'action triggers for this ticker.</div>')
