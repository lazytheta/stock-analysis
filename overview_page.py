"""HTML for the Overview tab: the "Company" section (profile + at a glance) and
the "Key figures" section (five flat cards).

Pure string builders; the tab in streamlit_app.py fetches the data and draws
the "Price vs S&P 500" section (a keyed container, since it holds widgets)
between the two. Both sections use question_cards.section_html, the white
section look of the Business/Moat/Risk tabs, with flat inner cards.
Every dynamic text goes through question_cards.esc (a bare `$` pair would
render as LaTeX) and every <style> block is emitted on one line.
"""

import overview_metrics as om
import question_cards as qc

DASH = om.DASH

_HAIRLINE = "color-mix(in srgb, var(--text) 10%, transparent)"
_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"
# A chip inside a flat card needs to stand out from that card's own tint.
_CHIP = "color-mix(in srgb, var(--text) 7%, var(--card))"

CARD_STYLE = f"""<style>
.ov-card{{background:{_INNER};border-radius:16px;padding:16px 18px;box-sizing:border-box;
  min-width:0}}
.ov-ctitle{{font-size:15px;font-weight:700;color:var(--text);margin:0 0 12px}}
.ov-lbl{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 3px}}
</style>"""

COMPANY_STYLE = f"""<style>
.ov-company{{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px;
  align-items:stretch}}
@media (max-width:800px){{.ov-company{{grid-template-columns:minmax(0,1fr)}}}}
.ov-profile{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px 18px}}
@media (max-width:520px){{.ov-profile{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.ov-val{{font-size:14px;color:var(--text);line-height:1.35;overflow-wrap:anywhere}}
.ov-diff{{display:inline-flex;align-items:center;gap:7px;padding:2px 9px;border-radius:8px;
  background:{_CHIP};font-size:13px}}
.ov-diff i{{display:inline-block;width:7px;height:9px;border-radius:2px;margin-right:-4px;
  background:color-mix(in srgb, var(--text) 18%, transparent)}}
.ov-diff i.on{{background:var(--text)}}
.ov-diff span{{margin-left:4px}}
.ov-why{{margin-top:4px;font-size:12px;color:var(--text-muted);line-height:1.35}}
.ov-phase{{display:inline-flex;align-items:center;gap:7px;cursor:help}}
.ov-phase i{{font-style:normal;display:inline-flex;align-items:center;justify-content:center;
  width:20px;height:20px;border-radius:50%;background:var(--accent);color:#fff;
  font-size:11px;font-weight:700;flex:none}}
.ov-why.ov-clip{{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
  overflow:hidden}}
.ov-tags{{margin-top:16px}}
.ov-chips{{display:flex;flex-wrap:wrap;gap:6px}}
.ov-chip{{display:inline-block;padding:3px 10px;border-radius:8px;background:{_CHIP};
  color:var(--text);font-size:13px}}
.ov-empty{{margin-top:14px;font-size:13px;color:var(--text-muted);line-height:1.45}}
.ov-mission{{margin-top:16px}}
.ov-mission p{{margin:0;font-size:15px;color:var(--text);line-height:1.6}}
.ov-glance{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 18px}}
.ov-gval{{font-size:22px;font-weight:700;color:var(--text);line-height:1.2;margin:2px 0 2px;
  white-space:nowrap}}
.ov-gval.ov-pos{{color:var(--green, #2e7d32)}}
.ov-gcap{{font-size:12px;color:var(--text-muted);line-height:1.35}}
</style>"""

METRICS_STYLE = f"""<style>
.ov-metrics{{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;align-items:stretch}}
.ov-metrics > .ov-card{{height:100%;box-sizing:border-box}}
@media (min-width:600px){{.ov-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (min-width:900px){{.ov-metrics{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
@media (min-width:1200px){{.ov-metrics{{grid-template-columns:repeat(5,minmax(0,1fr))}}}}
.ov-metrics .ov-ctitle{{margin:0 0 2px}}
.ov-sub{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 6px;line-height:14px}}
.ov-row{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  padding:7px 0;border-bottom:1px solid {_HAIRLINE};font-size:14px;line-height:20px;
  color:var(--text)}}
.ov-row:last-child{{border-bottom:none}}
.ov-row b{{font-weight:600;white-space:nowrap}}
.ov-gtab{{width:100%;border-collapse:collapse;font-size:14px;color:var(--text);margin:0}}
.ov-gtab,.ov-gtab tr,.ov-gtab th,.ov-gtab td{{border:none !important;background:transparent !important}}
.ov-gtab tr:nth-child(even),.ov-gtab tr:nth-child(odd){{background:transparent !important}}
.ov-gtab th{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);text-align:right;padding:0 0 6px 6px;line-height:14px}}
.ov-gtab td{{padding:7px 0 7px 6px;text-align:right;font-weight:600;white-space:nowrap;
  line-height:20px;font-variant-numeric:tabular-nums}}
.ov-gtab tr td{{border-bottom:1px solid {_HAIRLINE} !important}}
.ov-gtab tr:last-child td{{border-bottom:none !important}}
.ov-gtab th:first-child,.ov-gtab td:first-child{{text-align:left;padding-left:0;
  white-space:normal}}
.ov-gtab th:first-child{{white-space:nowrap}}
.ov-gtab td:first-child{{font-weight:400}}
</style>"""

# Complexity is shown as a neutral 1-3 meter, not traffic-light colours: a
# hard-to-understand business is not a bad one (owner, 2026-10-05).
_DIFF_LEVEL = {"Easy": 1, "Moderate": 2, "Hard": 3}

_EMPTY_NOTE = ('Company profile not filled yet. It comes with the '
               '"Company Profile" section.')


def _pair(label, value_html):
    return (f'<div><div class="ov-lbl">{qc.esc(label.upper())}</div>'
            f'<div class="ov-val">{value_html}</div></div>')


def _text(value):
    return DASH if value is None or value == "" else qc.esc(value)


def _difficulty_html(difficulty, reason=""):
    if not difficulty:
        return DASH
    level = _DIFF_LEVEL.get(difficulty, 0)
    meter = "".join('<i class="on"></i>' if n <= level else "<i></i>" for n in (1, 2, 3))
    why = f'<div class="ov-why">{qc.esc(reason)}</div>' if reason else ""
    return f'<span class="ov-diff">{meter}<span>{qc.esc(difficulty)}</span></span>{why}'


def _phase_html(phase):
    """The phase number in a disc, its name, and the analysis's lead sentence
    clipped to two lines; the full sentence plus what the phase looks like,
    which valuation fits and what moves it on sit in the tooltip."""
    if not phase:
        return DASH
    tip = qc.esc(phase.get("tooltip") or "").replace("\n", "&#10;")
    why = (f'<div class="ov-why ov-clip">{qc.esc(phase["summary"])}</div>'
           if phase.get("summary") else "")
    return (f'<span class="ov-phase" title="{tip}"><i>{phase["number"]}</i>'
            f'{qc.esc(phase["name"])}</span>{why}')


def _card(title, body_html, extra_class=""):
    cls = f"ov-card {extra_class}".strip()
    return (f'<div class="{cls}"><div class="ov-ctitle">{qc.esc(title)}</div>'
            f'{body_html}</div>')


def _profile_body(profile, market_cap_m, summary=None, phase=None):
    mcap = qc.esc(om.fmt_money_m(market_cap_m))
    if not profile:
        return (f'<div class="ov-profile">{_pair("Market cap", mcap)}</div>'
                f'<div class="ov-empty">{qc.esc(_EMPTY_NOTE)}</div>')
    employees = profile.get("employees")
    # Three columns: Sector | Industry | Market cap; Capital type | Complexity |
    # Phase; Founded | Employees. Phase replaced the Phase tab (2026-10-06).
    pairs = [
        _pair("Sector", _text(profile.get("sector"))),
        _pair("Industry", _text(profile.get("industry"))),
        _pair("Market cap", mcap),
        _pair("Capital type", _text(profile.get("capital_type"))),
        _pair("Complexity", _difficulty_html(profile.get("difficulty"),
                                             profile.get("difficulty_reason") or "")),
        _pair("Phase", _phase_html(phase)),
        _pair("Founded", _text(profile.get("founded"))),
        _pair("Employees", DASH if employees is None else qc.esc(f"{employees:,}")),
    ]
    tags = profile.get("tags") or []
    tags_html = ""
    if tags:
        chips = "".join(f'<span class="ov-chip">{qc.esc(t)}</span>' for t in tags)
        tags_html = (f'<div class="ov-tags"><div class="ov-lbl">TAGS</div>'
                     f'<div class="ov-chips">{chips}</div></div>')
    # "What it does": the Company Explainer's 2-3 sentence lead. The profile's
    # own mission statement is no longer shown (owner, 2026-10-02).
    summary_html = (f'<div class="ov-mission ov-summary"><div class="ov-lbl">WHAT IT DOES</div>'
                    f'<p>{qc.esc(summary)}</p></div>' if summary else "")
    return f'<div class="ov-profile">{"".join(pairs)}</div>{tags_html}{summary_html}'


def _tile(label, value, caption, positive=False):
    cls = "ov-gval ov-pos" if positive else "ov-gval"
    return (f'<div><div class="ov-lbl">{qc.esc(label.upper())}</div>'
            f'<div class="{cls}">{qc.esc(value)}</div>'
            f'<div class="ov-gcap">{qc.esc(caption)}</div></div>')


def _num(x):
    """A finite float, else None (bad data must not crash the page)."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _glance_body(glance):
    g = glance or {}
    metric = g.get("roce_metric") if g.get("roce_metric") in ("ROCE", "ROE") else "ROCE"
    roce = _num(g.get("roce_pct"))
    net = _num(g.get("net_cash_m"))
    conv = _num(g.get("fcf_conversion"))
    shares = _num(g.get("share_change_5y"))
    n = g.get("margin_years")
    margin_cap = f"{n}-year average" if n else "10-year average"

    def _pct(x):
        x = _num(x)
        return DASH if x is None else f"{x * 100:.1f}%"

    # Quality, growth, price, balance sheet, capital return -- in that order,
    # two per row (owner, 2026-10-06).
    tiles = [
        _tile(metric, DASH if roce is None else f"{roce:.1f}%", "10-year average"),
        _tile("Gross margin", _pct(g.get("gross_margin")), margin_cap),
        _tile("Operating margin", _pct(g.get("op_margin")), margin_cap),
        _tile("Revenue growth", om.fmt_pct(_num(g.get("revenue_cagr_5y")), signed=True),
              "per year, last 5 years"),
        _tile("FCF conversion", DASH if conv is None else f"{conv * 100:.0f}%",
              "free cash flow / net income"),
        _tile("FCF yield", _pct(g.get("fcf_yield")), "free cash flow / market cap"),
        _tile("Net debt" if net is not None and net < 0 else "Net cash",
              om.fmt_money_m(None if net is None else abs(net)),
              "cash & investments − debt", positive=net is not None and net > 0),
        _tile("Shares per year (5y)", om.fmt_pct(shares, signed=True),
              "buybacks" if shares is not None and shares < 0 else "dilution"),
    ]
    return f'<div class="ov-glance">{"".join(tiles)}</div>'


def company_section_html(profile, market_cap_m, glance: dict | None = None,
                         summary: str | None = None, phase: dict | None = None) -> str:
    """White "Company" section: a Profile card (label/value pairs, tags,
    mission; $M in for market cap) and, beside it, an "At a glance" card with
    eight tiles from overview_metrics.glance. Without a profile the Profile
    card holds only Market cap and a muted note; the glance card always
    shows, with dashes for missing values."""
    cards = [_card("Profile", _profile_body(profile, market_cap_m, summary, phase)),
             _card("At a glance", _glance_body(glance))]
    inner = f'<div class="ov-company">{"".join(cards)}</div>'
    return qc.css(CARD_STYLE, COMPANY_STYLE) + qc.section_html("Company", inner)


def _row(label, value):
    return (f'<div class="ov-row"><span>{qc.esc(label)}</span>'
            f'<b>{qc.esc(value)}</b></div>')


def _group(title, sub, rows):
    sub_html = f'<div class="ov-sub">{qc.esc(sub.upper())}</div>' if sub else ""
    return _card(title, f'{sub_html}{"".join(rows)}')


_MONEY = {"Cash & investments", "Total debt"}


def _growth_table(growth, sub):
    """Rows Revenue / EPS / FCF, columns 3Y / 5Y / 10Y, from compute()'s
    (label, years, value) triples. The subtitle sits in the header row's
    first cell, so the rows line up with the other cards' rows."""
    labels, horizons, values = [], [], {}
    for label, n, v in growth:
        if label not in labels:
            labels.append(label)
        if n not in horizons:
            horizons.append(n)
        values[(label, n)] = v
    head = "".join(f"<th>{qc.esc(f'{n}Y')}</th>" for n in horizons)
    rows = "".join(
        f"<tr><td>{qc.esc(label)}</td>"
        + "".join(f"<td>{qc.esc(om.fmt_pct(values.get((label, n)), signed=True))}</td>"
                  for n in horizons)
        + "</tr>"
        for label in labels)
    return (f'<table class="ov-gtab"><tr><th>{qc.esc(sub.upper())}</th>{head}</tr>'
            f'{rows}</table>')


def key_figures_section_html(metrics: dict) -> str:
    """White "Key figures" section: five flat cards (Profitability, Financial
    Health, Growth, Valuation, Shareholder Returns) in a grid of 5/3/2/1
    columns by width. Every card has a one-line subtitle naming its basis,
    so the rows of all five line up."""
    fy = metrics.get("fy")
    fy_sub = f"Latest fiscal year (FY{fy})" if fy else "Latest fiscal year"
    fy_end = f"End of FY{fy}" if fy else "Latest balance sheet"
    fy_paid = f"FY{fy} payouts ÷ market cap" if fy else "Payouts ÷ market cap"
    cards = [
        _group("Profitability", fy_sub, [
            _row(label, om.fmt_pct(v)) for label, v in metrics.get("profitability", [])]),
        _group("Financial Health", fy_end, [
            _row(label, om.fmt_money_m(v) if label in _MONEY else om.fmt_mult(v))
            for label, v in metrics.get("health", [])]),
        _card("Growth", _growth_table(metrics.get("growth", []), "Per year")),
        _group("Valuation", "At current price", [
            _row(label, om.fmt_mult(v)) for label, v in metrics.get("valuation", [])]),
        _group("Shareholder Returns", fy_paid, [
            _row(label, om.fmt_pct(v, signed=True))
            for label, v in metrics.get("returns", [])]),
    ]
    inner = f'<div class="ov-metrics">{"".join(cards)}</div>'
    return qc.css(CARD_STYLE, METRICS_STYLE) + qc.section_html("Key figures", inner)
