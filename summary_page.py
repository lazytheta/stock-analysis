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
.ss-row{display:grid;grid-template-columns:minmax(150px,1.3fr) repeat(7,minmax(0,1fr));gap:10px}
@media (max-width:1100px){.ss-row{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media (max-width:560px){.ss-row{grid-template-columns:repeat(2,minmax(0,1fr))}}
.ss-comp{background:#2b2b2f;color:#fff;border-radius:14px;padding:14px 16px;display:flex;
  align-items:center;gap:12px;min-width:0}
.ss-comp b{font-size:1.9rem;line-height:1}
.ss-comp small{display:block;font-size:.62rem;font-weight:700;letter-spacing:.09em;opacity:.7}
.ss-comp span{display:block;font-size:1rem;font-weight:700;letter-spacing:.03em}
.ss-dim{background:var(--qc-inner, var(--bg-secondary));border-radius:14px;padding:12px 12px 10px;
  min-width:0;display:flex;flex-direction:column;gap:6px}
.ss-name{font-size:.62rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);line-height:1.25}
.ss-val{display:flex;justify-content:space-between;align-items:baseline;gap:6px;
  font-size:.86rem;font-weight:700;line-height:1.2}
.ss-bar{display:grid;grid-template-columns:repeat(5,1fr);gap:3px;margin-top:auto}
.ss-bar i{height:5px;border-radius:3px;background:color-mix(in srgb, var(--text) 10%, transparent)}
.vd-head{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 10px}
.vd-pill{display:inline-block;padding:4px 12px;border-radius:999px;font-size:.78rem;font-weight:700;
  color:#fff}
.vd-pill.vd-q{background:color-mix(in srgb, var(--text) 8%, var(--card));color:var(--text)}
.vd-lead{font-size:1.02rem;line-height:1.55;margin:0 0 14px;color:var(--text)}
.vd-pts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
@media (max-width:900px){.vd-pts{grid-template-columns:1fr}}
.vd-pt{background:var(--qc-inner, var(--bg-secondary));border-radius:14px;padding:12px 14px;
  min-width:0}
.vd-pt b{display:block;font-size:.66rem;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 4px}
.vd-pt p{margin:0;font-size:.86rem;line-height:1.5;color:var(--text)}
.vb-card{background:var(--qc-inner, var(--bg-secondary));border-radius:16px;padding:16px 20px;
  margin-top:16px}
.vb-top{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px}
.vb-top .sp-big{margin:0}
.vb-track{position:relative;height:12px;border-radius:999px;margin:34px 0 8px;overflow:visible;
  display:flex}
.vb-track span{height:100%}
.vb-now{position:absolute;top:-30px;transform:translateX(-50%);font-size:.78rem;font-weight:700;
  white-space:nowrap;color:var(--text)}
.vb-now::after{content:"";position:absolute;left:50%;top:20px;width:3px;height:22px;
  margin-left:-1.5px;border-radius:2px;background:var(--text)}
.vb-legend{display:flex;justify-content:space-between;font-size:.76rem;font-weight:700;gap:8px}
.vb-chips{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-top:14px}
@media (max-width:640px){.vb-chips{grid-template-columns:repeat(2,minmax(0,1fr))}}
.vb-chip{background:var(--card);border-radius:12px;padding:9px 12px;font-size:.78rem;
  color:var(--text-muted)}
.vb-chip b{display:block;font-size:.95rem;color:var(--text)}
.fl-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
@media (max-width:760px){.fl-row{grid-template-columns:1fr}}
.fl-card{border-radius:16px;padding:14px 18px}
.fl-card b{display:block;font-size:.66rem;letter-spacing:.08em;text-transform:uppercase;
  margin:0 0 8px}
.fl-card li{font-size:.88rem;line-height:1.5;color:var(--text);margin:0 0 4px}
.fl-card ul{margin:0;padding-left:0;list-style:none}
.pm-act{border-radius:14px;padding:12px 16px;margin-bottom:12px;font-size:.95rem;font-weight:700;
  color:var(--text)}
.pm-act small{display:block;font-size:.62rem;letter-spacing:.08em;text-transform:uppercase;
  color:var(--text-muted);font-weight:700;margin-bottom:2px}
.pm-now{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;
  margin-bottom:18px}
.pm-chip{background:var(--qc-inner, var(--bg-secondary));border-radius:12px;padding:10px 12px;
  font-size:.82rem;line-height:1.45;color:var(--text);min-width:0;overflow-wrap:anywhere}
.pm-chip b{display:block;font-size:.62rem;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin-bottom:2px}
.pm-cols{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:start}
@media (max-width:1000px){.pm-cols{grid-template-columns:1fr}}
.pm-col h5{display:flex;align-items:center;gap:8px;margin:0 0 10px;font-size:.7rem;
  font-weight:700;letter-spacing:.08em;text-transform:uppercase}
.pm-col h5 i{width:9px;height:9px;border-radius:50%;display:inline-block}
.pm-col h5 em{font-style:normal;color:var(--text-muted);font-weight:600}
.pm-item{background:var(--qc-inner, var(--bg-secondary));border-radius:12px;padding:10px 12px;
  margin-bottom:8px;border-left:3px solid transparent;font-size:.84rem;line-height:1.45;
  color:var(--text);overflow-wrap:anywhere}
.pm-item strong{display:block;font-size:.84rem;margin-bottom:2px}
.pm-item span{color:var(--text-muted)}
.pm-dim .pm-item{opacity:.8}
.pm-disc{margin-top:16px}
.pm-disc summary{cursor:pointer;font-size:.68rem;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;color:var(--text-muted)}
.pm-disc .pm-item{margin-top:8px}
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


# ── Score strip ──────────────────────────────────────────────────────────────

_RED, _AMBER, _GREEN = "#c0603f", "#c79a3a", "#2f8f4e"
_RISK_SCORE = {"low": (5, "Low"), "medium": (3, "Medium"), "high": (1, "High")}
_DIRECTION_SCORE = {"widening": (5, "Widening"), "stable": (3, "Stable"),
                    "narrowing": (1, "Narrowing")}


def _tone(score):
    return _GREEN if score >= 4 else _AMBER if score == 3 else _RED


def valuation_score(valuation_summary, price):
    """(1-5, word) from today's price against the fair-value band: at or under
    the buy price 5, clearly under fair value 4, within 5% of it 3, inside
    the band above it 2, above the band 1. (None, "") without a band."""
    vs = valuation_summary if isinstance(valuation_summary, dict) else {}
    mid, high = _num(vs.get("weighted_fv_mid")), _num(vs.get("weighted_fv_high"))
    buy = _num(vs.get("buy_price"))
    px = _num(price) or _num(vs.get("stock_price"))
    if px is None or mid is None:
        return None, ""
    if buy is not None and px <= buy:
        return 5, "Very attractive"
    if px < mid * 0.95:
        return 4, "Attractive"
    if px <= mid * 1.05:
        return 3, "Fair"
    if high is not None and px <= high:
        return 2, "Expensive"
    return 1, "Very expensive"


def dimension_scores(notes, valuation_summary, price):
    """[(label, score 1-5 or None, word)] for the score strip, each from the
    tab that owns it. Never raises; a missing source is (label, None, "")."""
    import business_cards
    import growth_cards
    import management_cards
    notes = notes if isinstance(notes, dict) else {}

    def safe(fn):
        try:
            return fn()
        except Exception:
            return None, ""

    def business():
        cards = business_cards.parse_business_cards(notes.get(business_cards.TITLE))["cards"]
        n = round(1 + 2 * sum(c["pick"] for c in cards) / len(cards))
        return n, ("Like it" if n >= 4 else "Okay" if n == 3 else "Weak")

    moat_v = parse_verdict_section(notes.get("Moat Analysis") or "")

    def moat():
        if not moat_v or moat_v["score"] is None or moat_v["out_of"] != 5:
            return None, ""
        return max(1, round(moat_v["score"])), moat_v["label"]

    def direction():
        word = next((q.strip().lower() for q in (moat_v or {}).get("qualifiers", [])
                     if q.strip().lower() in _DIRECTION_SCORE), None)
        return _DIRECTION_SCORE[word] if word else (None, "")

    def growth():
        score = growth_cards.parse_growth_cards(notes.get(growth_cards.TITLE))["analysis"]["score"]
        return score, growth_cards.SCORE_LABELS[score]

    def management():
        parsed = management_cards.parse_for_display(notes.get(management_cards.TITLE))
        score = parsed["facts"].get("score") or management_cards.score_from_cards(parsed["cards"])
        return score, management_cards.SCORE_LABELS[score - 1]

    def risk():
        v = parse_verdict_section(notes.get("Risk Analysis") or "")
        return _RISK_SCORE.get(str((v or {}).get("label", "")).strip().lower(), (None, ""))

    return [("Business", *safe(business)), ("Moat", *safe(moat)),
            ("Moat direction", *safe(direction)), ("Growth", *safe(growth)),
            ("Management", *safe(management)), ("Risk", *safe(risk)),
            ("Valuation", *valuation_score(valuation_summary, price))]


def score_strip_html(dims, verdict_label, theme):
    """The one-glance strip: composite (average of the scored dimensions)
    with the verdict, then one tile per dimension with its word, score and a
    five-step bar."""
    scored = [s for _l, s, _w in dims if s]
    comp = f"{sum(scored) / len(scored):.1f}" if scored else DASH
    tiles = []
    for label, score, word in dims:
        if score:
            tone = _tone(score)
            bar = "".join(f'<i style="background:{tone}"></i>' if n <= score else "<i></i>"
                          for n in range(1, 6))
            val = (f'<div class="ss-val" style="color:{tone}"><span>{qc.esc(word)}</span>'
                   f'<span>{score}</span></div>')
        else:
            bar = "<i></i>" * 5
            val = f'<div class="ss-val" style="color:{_muted(theme)}"><span>{DASH}</span></div>'
        tiles.append(f'<div class="ss-dim"><div class="ss-name">{qc.esc(label)}</div>{val}'
                     f'<div class="ss-bar">{bar}</div></div>')
    verdict = qc.esc((verdict_label or "").upper()) or "COMPOSITE"
    return (qc.css(STYLE) + f'<div class="ss-row"><div class="ss-comp"><b>{comp}</b><div>'
            f'<small>COMPOSITE</small><span>{verdict}</span></div></div>{"".join(tiles)}</div>')


# ── Verdict ──────────────────────────────────────────────────────────────────

def _verdict_block(text, theme):
    """Verdict pill, conviction, the lead sentence large and each point in a
    box of its own (it was one dense card)."""
    if not (isinstance(text, str) and text.strip()):
        return ('<div class="sp-note">No Investment Summary yet. Ask Claude via the MCP to '
                'fill the "Investment Summary" pre-scan section for this ticker.</div>')
    v = parse_verdict_section(text)
    if v is None:
        return ('<div class="sp-note">The Investment Summary is not in the verdict format yet. '
                'Ask Claude via the MCP to rerun that pre-scan section.</div>')
    tone = band_tone(v["label"]) or _muted(theme)
    pills = f'<span class="vd-pill" style="background:{tone}">{qc.esc(v["label"] or DASH)}</span>'
    pills += "".join(f'<span class="vd-pill vd-q">{qc.esc(q)}</span>' for q in v["qualifiers"])
    pts = "".join(f'<div class="vd-pt"><b>{qc.esc(p["label"])}</b><p>{qc.bold(p["text"])}</p></div>'
                  for p in v["bullets"][:3])
    return (f'<div class="vd-head">{pills}</div><p class="vd-lead">{qc.bold(v["summary"])}</p>'
            + (f'<div class="vd-pts">{pts}</div>' if pts else ""))


def _valuation_card(valuation_summary, price, theme, symbol="$"):
    """Price against the fair-value band as a bar: cheap (under the buy
    price), fair (buy price to the top of the band), expensive (above it),
    with today's price marked; the numbers underneath."""
    vs = valuation_summary if isinstance(valuation_summary, dict) else {}
    low, mid, high = (_num(vs.get(k)) for k in
                      ("weighted_fv_low", "weighted_fv_mid", "weighted_fv_high"))
    buy = _num(vs.get("buy_price"))
    px = _num(price) or _num(vs.get("stock_price"))

    gap = ""
    if px is not None and mid is not None:
        pct = round((px / mid - 1) * 100)
        gap = (f'<span class="sp-gap" style="color:{_RED}">+{pct}% above fair value</span>'
               if pct > 0 else
               f'<span class="sp-gap" style="color:{_GREEN}">{MINUS}{-pct}% below fair value</span>'
               if pct < 0 else '<span class="sp-gap">At fair value</span>')
    top = (f'<div class="vb-top"><div><div class="sp-title">Price vs fair value</div>'
           f'<div class="sp-big">{_money(px, symbol)}</div></div>{gap}</div>')

    bar = ""
    if px is not None and buy is not None and high is not None and high > buy:
        lo_ax = min(buy * 0.85, px * 0.95)
        hi_ax = max(high * 1.15, px * 1.05)
        span = hi_ax - lo_ax

        def pos(x):
            return max(0.0, min(100.0, (x - lo_ax) / span * 100))
        cheap, fair = pos(buy), pos(high) - pos(buy)
        track = (f'<span style="width:{cheap:.1f}%;background:{_GREEN}55;'
                 f'border-radius:999px 0 0 999px"></span>'
                 f'<span style="width:{fair:.1f}%;background:{_AMBER}55"></span>'
                 f'<span style="flex:1;background:{_RED}45;border-radius:0 999px 999px 0"></span>')
        now = f'<div class="vb-now" style="left:{pos(px):.1f}%">Now {_money(px, symbol)}</div>'
        legend = (f'<div class="vb-legend"><span style="color:{_GREEN}">Cheap &lt; {_money(buy, symbol)}</span>'
                  f'<span style="color:{_AMBER}">Fair {_money(mid, symbol)}</span>'
                  f'<span style="color:{_RED}">Expensive &gt; {_money(high, symbol)}</span></div>')
        bar = f'<div class="vb-track">{track}{now}</div>{legend}'

    chips = "".join(f'<div class="vb-chip">{label}<b>{_money(val, symbol)}</b></div>'
                    for label, val in (("Buy price", buy), ("Fair value low", low),
                                       ("Fair value mid", mid), ("Fair value high", high)))
    return f'<div class="vb-card">{top}{bar}<div class="vb-chips">{chips}</div></div>'


def verdict_section_html(investment_summary_text, valuation_summary, price, theme, symbol="$"):
    """White section "Verdict": the Investment Summary as a verdict block,
    then the price against the fair-value band. Never raises."""
    inner = (_verdict_block(investment_summary_text, theme)
             + _valuation_card(valuation_summary, price, theme, symbol))
    return qc.section_html("Verdict", qc.css(STYLE) + inner)


# ── Green / red flags ────────────────────────────────────────────────────────

_TAIL = re.compile(r",?\s*(?:turns? (?:this|it) into|makes? (?:this|it))\b.*$", re.I | re.S)


def flags(investment_summary_text):
    """(green, red) condition lists from the verdict's "What would change
    this" line: the part that leads to higher conviction is green, the part
    that turns it into a pass is red."""
    v = parse_verdict_section(investment_summary_text or "") if investment_summary_text else None
    text = (v or {}).get("footer_text") or ""
    green, red = [], []
    for part in [p.strip() for p in text.split(";") if p.strip()]:
        low = part.lower()
        cond = _TAIL.sub("", part).strip().rstrip(".")
        conds = [c.strip() for c in re.split(r",\s+or\s+|\s+or\s+(?=a |an |the )", cond) if c.strip()]
        if "pass" in low or "avoid" in low or "sell" in low:
            red += conds
        elif "conviction" in low or "buy" in low:
            green += conds
    return green, red


def flags_section_html(investment_summary_text, theme):
    """Green flags (thesis getting stronger) beside red flags (warning signs),
    or "" when the verdict has no "What would change this" line."""
    green, red = flags(investment_summary_text)
    if not green and not red:
        return ""

    def card(title, items, tone, mark):
        rows = "".join(f'<li><span style="color:{tone};font-weight:700">{mark}</span> '
                       f'{qc.esc(i)}</li>' for i in items) or f'<li style="color:{_muted(theme)}">—</li>'
        return (f'<div class="fl-card" style="background:{tone}14;border:1px solid {tone}40">'
                f'<b style="color:{tone}">{title}</b><ul>{rows}</ul></div>')
    inner = (f'<div class="fl-row">{card("Green flags · raises conviction", green, _GREEN, "✓")}'
             f'{card("Red flags · turns it into a pass", red, _RED, "✕")}</div>')
    return qc.section_html("Flags", qc.css(STYLE) + inner)


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

_ACTION_WORDS = re.compile(r"\b(houd|hold|koop|kopen|bijkop|verkoop|verkopen|buy|sell|add|"
                           r"trim|wacht|wait|niets|nothing)", re.I)


def _head_body(raw):
    """Split a trigger into a bold head and the rest at its first ': ' or
    ' — ' (when the head is short enough to be one), else no head."""
    cat, text = split_trigger(raw)
    for sep in (": ", " — "):
        head, found, body = text.partition(sep)
        if found and 3 <= len(head) <= 70:
            return cat, head, body
    return cat, "", text


def render_premortem(pm, theme):
    """Render a structured pre-mortem as a decision board.

    The action from the current view on top, then the view's facts as small
    tiles; Sell / Add / Not a reason as three columns of boxes (one per
    trigger, a bold head where it has one); discipline folded away
    underneath. It used to be stacked lists and one long sentence, which
    read as a wall of text (owner, 2026-10-08).

    Returns None for a legacy string / empty (caller shows a fallback).
    """
    if not isinstance(pm, dict):
        return None
    esc = qc.esc
    muted = theme.get("text_muted", "#888")

    def _items(key):
        return [str(i) for i in (pm.get(key) or []) if str(i).strip()]

    sell, add, ignore = _items("sell"), _items("add"), _items("ignore")
    discipline = _items("discipline")
    current = str(pm.get("current", "") or "").strip()
    if not any((sell, add, ignore, discipline, current)):
        return None

    parts = [qc.css(STYLE)]
    if current:
        frags = [f.strip() for f in re.split(r"\s[·|]\s|·", current) if f.strip()]
        # The action is a spaced arrow ("... → houden"), not a range like
        # "0,36→0,85"; prefer the one that names what to do.
        arrows = [f for f in frags if " → " in f]
        action = next((f for f in arrows if _ACTION_WORDS.search(f.split(" → ")[-1])),
                      arrows[0] if arrows else None)
        parts.append('<div class="sp-title">Current view</div>')
        if action:
            parts.append(f'<div class="pm-act" style="background:{_AMBER}1f">'
                         f'<small>Action</small>{esc(action)}</div>')
        chips = []
        for f in frags:
            if f is action:
                continue
            _cat, head, body = _head_body(f)
            chips.append(f'<div class="pm-chip"><b>{esc(head)}</b>{esc(body)}</div>' if head
                         else f'<div class="pm-chip">{esc(f)}</div>')
        if chips:
            parts.append(f'<div class="pm-now">{"".join(chips)}</div>')

    def _column(title, items, tone, dim=False):
        if not items:
            return ""
        boxes = []
        for raw in items:
            cat, head, body = _head_body(raw)
            tag = f'<span style="color:{tone};font-weight:700">{esc(cat)} </span>' if cat else ""
            inner = (f'<strong>{tag}{esc(head)}</strong><span>{esc(body)}</span>' if head
                     else f'{tag}{esc(body)}')
            boxes.append(f'<div class="pm-item" style="border-left-color:{tone}">{inner}</div>')
        return (f'<div class="pm-col{" pm-dim" if dim else ""}"><h5 style="color:{tone}">'
                f'<i style="background:{tone}"></i>{title} <em>{len(items)}</em></h5>'
                f'{"".join(boxes)}</div>')

    # Sell first: the trigger you most need to have decided in advance is the
    # one you will least want to act on in the moment.
    cols = (_column("Sell when", sell, _RED) + _column("Add when", add, _GREEN)
            + _column("Not a reason", ignore, muted, dim=True))
    if cols:
        parts.append(f'<div class="pm-cols">{cols}</div>')

    if discipline:
        boxes = "".join(f'<div class="pm-item" style="border-left-color:{muted}">{esc(i)}</div>'
                        for i in discipline)
        parts.append(f'<details class="pm-disc"><summary>Discipline ({len(discipline)})'
                     f'</summary>{boxes}</details>')
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
