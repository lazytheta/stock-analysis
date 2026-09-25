"""HTML for the Overview tab: the company profile panel, the mission line and
the four columns of key figures.

Pure string builders; the tab in streamlit_app.py fetches the data, draws the
price chart and wraps everything in the `qc_overview_section` container.
Every dynamic text goes through question_cards.esc (a bare `$` pair would
render as LaTeX) and every <style> block is emitted on one line.
"""

import overview_metrics as om
import question_cards as qc

DASH = om.DASH

_HAIRLINE = "color-mix(in srgb, var(--text) 10%, transparent)"
_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"

PROFILE_STYLE = f"""<style>
.ov-profile{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px 18px}}
.ov-lbl{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 3px}}
.ov-val{{font-size:14px;color:var(--text);line-height:1.35}}
.ov-diff{{display:inline-flex;align-items:center;gap:6px;padding:2px 9px;border-radius:8px;
  background:{_INNER};font-size:13px}}
.ov-diff i{{display:inline-block;width:9px;height:9px;border-radius:2px}}
.ov-tags{{margin-top:16px}}
.ov-chips{{display:flex;flex-wrap:wrap;gap:6px}}
.ov-chip{{display:inline-block;padding:3px 10px;border-radius:8px;background:{_INNER};
  color:var(--text);font-size:13px}}
.ov-empty{{margin-top:14px;font-size:13px;color:var(--text-muted);line-height:1.45}}
</style>"""

# The mission renders in its own st.markdown after the profile panel; its own
# small style keeps PROFILE_STYLE from being emitted twice on the page.
MISSION_STYLE = """<style>
.ov-mission{margin:18px 0 4px}
.ov-mission .ov-mlbl{font-size:11px;font-weight:700;letter-spacing:.07em;
  text-transform:uppercase;color:var(--text-muted);margin:0 0 3px}
.ov-mission p{margin:0;font-size:15px;font-style:italic;color:var(--text);line-height:1.5}
</style>"""

METRICS_STYLE = f"""<style>
.ov-metrics{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:28px;margin-top:18px}}
@media (max-width:900px){{.ov-metrics{{grid-template-columns:1fr}}}}
.ov-group{{margin:0 0 22px}}
.ov-group:last-child{{margin-bottom:0}}
.ov-title{{font-size:15px;font-weight:700;color:var(--text);margin:0 0 2px}}
.ov-sub{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 6px}}
.ov-row{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  padding:7px 0;border-bottom:1px solid {_HAIRLINE};font-size:14px;color:var(--text)}}
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


def profile_panel_html(profile, market_cap_m) -> str:
    """Label/value pairs from the Company Profile, plus Market cap ($M in).
    Without a profile only Market cap and a muted note."""
    mcap = qc.esc(om.fmt_money_m(market_cap_m))
    css = qc.css(PROFILE_STYLE)
    if not profile:
        return (f'{css}<div class="ov-profile">{_pair("Market cap", mcap)}</div>'
                f'<div class="ov-empty">{qc.esc(_EMPTY_NOTE)}</div>')
    employees = profile.get("employees")
    # Rows: Sector | Industry; Market cap alone; Capital type | Difficulty;
    # Founded | Employees. The empty cell keeps Market cap on its own row.
    pairs = [
        _pair("Sector", _text(profile.get("sector"))),
        _pair("Industry", _text(profile.get("industry"))),
        _pair("Market cap", mcap),
        "<div></div>",
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
    return f'{css}<div class="ov-profile">{"".join(pairs)}</div>{tags_html}'


def mission_html(profile) -> str:
    """The mission as one italic line across the full width; "" without one."""
    mission = (profile or {}).get("mission")
    if not mission:
        return ""
    return (f'{qc.css(MISSION_STYLE)}<div class="ov-mission"><div class="ov-mlbl">MISSION</div>'
            f'<p>{qc.esc(mission)}</p></div>')


def _row(label, value, horizon=None):
    h = f'<span class="ov-h">{qc.esc(horizon)}</span>' if horizon else ""
    return (f'<div class="ov-row"><span>{qc.esc(label)}{h}</span>'
            f'<b>{qc.esc(value)}</b></div>')


def _group(title, sub, rows):
    sub_html = f'<div class="ov-sub">{qc.esc(sub.upper())}</div>' if sub else ""
    return (f'<div class="ov-group"><div class="ov-title">{qc.esc(title)}</div>'
            f'{sub_html}{"".join(rows)}</div>')


_MONEY = {"Cash & investments", "Total debt"}


def metrics_html(metrics: dict) -> str:
    """Three columns (one below 900px): Profitability + Financial Health,
    Growth, Valuation + Shareholder Returns."""
    fy = metrics.get("fy")
    fy_sub = f"Latest fiscal year (FY{fy})" if fy else "Latest fiscal year"
    profitability = _group("Profitability", fy_sub, [
        _row(label, om.fmt_pct(v)) for label, v in metrics.get("profitability", [])])
    health = _group("Financial Health", None, [
        _row(label, om.fmt_money_m(v) if label in _MONEY else om.fmt_mult(v))
        for label, v in metrics.get("health", [])])
    growth = _group("Growth", "Compound annual growth", [
        _row(label, om.fmt_pct(v, signed=True), f"{n}Y")
        for label, n, v in metrics.get("growth", [])])
    valuation = _group("Valuation", "At current price", [
        _row(label, om.fmt_mult(v)) for label, v in metrics.get("valuation", [])])
    returns = _group("Shareholder Returns", None, [
        _row(label, om.fmt_pct(v, signed=True)) for label, v in metrics.get("returns", [])])
    columns = (f'<div>{profitability}{health}</div><div>{growth}</div>'
               f'<div>{valuation}{returns}</div>')
    return f'{qc.css(METRICS_STYLE)}<div class="ov-metrics">{columns}</div>'
