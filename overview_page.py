"""HTML for the Overview tab: the "Company" section (profile + mission) and
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
.ov-company.ov-solo{{grid-template-columns:minmax(0,1fr)}}
@media (max-width:800px){{.ov-company{{grid-template-columns:minmax(0,1fr)}}}}
.ov-profile{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px 18px}}
@media (max-width:520px){{.ov-profile{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.ov-val{{font-size:14px;color:var(--text);line-height:1.35;overflow-wrap:anywhere}}
.ov-diff{{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:8px;
  background:{_CHIP};font-size:13px}}
.ov-diff i{{display:inline-block;width:9px;height:9px;border-radius:2px}}
.ov-tags{{margin-top:16px}}
.ov-chips{{display:flex;flex-wrap:wrap;gap:6px}}
.ov-chip{{display:inline-block;padding:3px 10px;border-radius:8px;background:{_CHIP};
  color:var(--text);font-size:13px}}
.ov-empty{{margin-top:14px;font-size:13px;color:var(--text-muted);line-height:1.45}}
.ov-mission p{{margin:0;font-size:16px;font-style:italic;color:var(--text);line-height:1.55}}
</style>"""

METRICS_STYLE = f"""<style>
.ov-metrics{{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;align-items:start}}
@media (min-width:600px){{.ov-metrics{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (min-width:900px){{.ov-metrics{{grid-template-columns:repeat(3,minmax(0,1fr))}}}}
@media (min-width:1200px){{.ov-metrics{{grid-template-columns:repeat(5,minmax(0,1fr))}}}}
.ov-metrics .ov-ctitle{{margin:0 0 2px}}
.ov-sub{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 6px}}
.ov-row{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  padding:7px 0;border-bottom:1px solid {_HAIRLINE};font-size:14px;color:var(--text)}}
.ov-row:last-child{{border-bottom:none}}
.ov-row b{{font-weight:600;white-space:nowrap}}
.ov-row .ov-h{{font-size:11px;color:var(--text-muted);margin-left:6px}}
</style>"""

_DIFF_COLOUR = {"Easy": "#2e9e5b", "Moderate": "var(--accent)", "Hard": "var(--red)"}

_EMPTY_NOTE = ('Company profile not filled yet. It comes with the '
               '"Company Profile" section.')


def _pair(label, value_html):
    return (f'<div><div class="ov-lbl">{qc.esc(label.upper())}</div>'
            f'<div class="ov-val">{value_html}</div></div>')


def _text(value):
    return DASH if value is None or value == "" else qc.esc(value)


def _difficulty_html(difficulty):
    if not difficulty:
        return DASH
    colour = _DIFF_COLOUR.get(difficulty, "var(--text-muted)")
    return (f'<span class="ov-diff"><i style="background:{colour}"></i>'
            f'{qc.esc(difficulty)}</span>')


def _card(title, body_html, extra_class=""):
    cls = f"ov-card {extra_class}".strip()
    return (f'<div class="{cls}"><div class="ov-ctitle">{qc.esc(title)}</div>'
            f'{body_html}</div>')


def _profile_body(profile, market_cap_m):
    mcap = qc.esc(om.fmt_money_m(market_cap_m))
    if not profile:
        return (f'<div class="ov-profile">{_pair("Market cap", mcap)}</div>'
                f'<div class="ov-empty">{qc.esc(_EMPTY_NOTE)}</div>')
    employees = profile.get("employees")
    # Three columns: Sector | Industry | Market cap; Capital type | Difficulty;
    # Founded | Employees.
    pairs = [
        _pair("Sector", _text(profile.get("sector"))),
        _pair("Industry", _text(profile.get("industry"))),
        _pair("Market cap", mcap),
        _pair("Capital type", _text(profile.get("capital_type"))),
        _pair("Difficulty", _difficulty_html(profile.get("difficulty"))),
        _pair("Founded", _text(profile.get("founded"))),
        _pair("Employees", DASH if employees is None else qc.esc(f"{employees:,}")),
    ]
    tags = profile.get("tags") or []
    tags_html = ""
    if tags:
        chips = "".join(f'<span class="ov-chip">{qc.esc(t)}</span>' for t in tags)
        tags_html = (f'<div class="ov-tags"><div class="ov-lbl">TAGS</div>'
                     f'<div class="ov-chips">{chips}</div></div>')
    return f'<div class="ov-profile">{"".join(pairs)}</div>{tags_html}'


def company_section_html(profile, market_cap_m) -> str:
    """White "Company" section: a Profile card (label/value pairs, tags; $M in
    for market cap) and, beside it, a Mission card. Without a profile only
    Market cap and a muted note, and no Mission card."""
    cards = [_card("Profile", _profile_body(profile, market_cap_m))]
    mission = (profile or {}).get("mission")
    if profile and mission:
        cards.append(_card("Mission", f"<p>{qc.esc(mission)}</p>", "ov-mission"))
    grid_cls = "ov-company" if len(cards) == 2 else "ov-company ov-solo"
    inner = f'<div class="{grid_cls}">{"".join(cards)}</div>'
    return qc.css(CARD_STYLE, COMPANY_STYLE) + qc.section_html("Company", inner)


def _row(label, value, horizon=None):
    h = f'<span class="ov-h">{qc.esc(horizon)}</span>' if horizon else ""
    return (f'<div class="ov-row"><span>{qc.esc(label)}{h}</span>'
            f'<b>{qc.esc(value)}</b></div>')


def _group(title, sub, rows):
    sub_html = f'<div class="ov-sub">{qc.esc(sub.upper())}</div>' if sub else ""
    return _card(title, f'{sub_html}{"".join(rows)}')


_MONEY = {"Cash & investments", "Total debt"}


def key_figures_section_html(metrics: dict) -> str:
    """White "Key figures" section: five flat cards (Profitability, Financial
    Health, Growth, Valuation, Shareholder Returns) in a grid of 5/3/2/1
    columns by width."""
    fy = metrics.get("fy")
    fy_sub = f"Latest fiscal year (FY{fy})" if fy else "Latest fiscal year"
    cards = [
        _group("Profitability", fy_sub, [
            _row(label, om.fmt_pct(v)) for label, v in metrics.get("profitability", [])]),
        _group("Financial Health", None, [
            _row(label, om.fmt_money_m(v) if label in _MONEY else om.fmt_mult(v))
            for label, v in metrics.get("health", [])]),
        _group("Growth", "Compound annual growth", [
            _row(label, om.fmt_pct(v, signed=True), f"{n}Y")
            for label, n, v in metrics.get("growth", [])]),
        _group("Valuation", "At current price", [
            _row(label, om.fmt_mult(v)) for label, v in metrics.get("valuation", [])]),
        _group("Shareholder Returns", None, [
            _row(label, om.fmt_pct(v, signed=True))
            for label, v in metrics.get("returns", [])]),
    ]
    inner = f'<div class="ov-metrics">{"".join(cards)}</div>'
    return qc.css(CARD_STYLE, METRICS_STYLE) + qc.section_html("Key figures", inner)
