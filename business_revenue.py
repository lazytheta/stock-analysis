"""Business Revenue: the "By segment" panel, the "By geography" world map and
its legend, for the Business tab's Revenue section.

Consumes the validated `revenue` dict produced by
`business_cards.parse_business_cards(content)["revenue"]`.

No module-level import of plotly: `countries_by_region` and
`geography_figure` import it locally, so a caller that only needs the HTML
panels (e.g. a lightweight parser context) never pays for plotly's import.
"""

from decimal import ROUND_HALF_UP, Decimal

import question_cards as qc

# Specific -> broad. A country is assigned to the FIRST of these (that the
# caller actually listed) whose membership contains it.
REGION_ORDER = ("US", "Canada", "China", "Japan", "India", "North America",
                 "Latin America", "Europe", "Middle East & Africa", "EMEA",
                 "Asia Pacific", "Rest of world")

# Countries plotly's gapminder dataset is missing, keyed to a continent.
_EXTRA = {"RUS": "Europe", "UKR": "Europe", "BLR": "Europe", "KAZ": "Asia",
          "UZB": "Asia", "TKM": "Asia", "KGZ": "Asia", "TJK": "Asia",
          "AZE": "Asia", "GEO": "Asia", "ARM": "Asia", "MDA": "Europe",
          "EST": "Europe", "LVA": "Europe", "LTU": "Europe", "GRL": "Americas",
          "PNG": "Oceania", "GUY": "Americas", "SUR": "Americas",
          "BLZ": "Americas", "SSD": "Africa", "ESH": "Africa"}

# These count as Middle East, never as Asia or Europe, regardless of the
# continent gapminder (or _EXTRA) assigns them.
_MIDDLE_EAST = {"SAU", "ARE", "QAT", "KWT", "OMN", "BHR", "YEM", "IRQ", "IRN",
                 "ISR", "JOR", "LBN", "SYR", "TUR", "AFG"}

# Site's green -> beige -> brown family. Biggest/first entry gets the
# strongest green.
SEGMENT_COLORS = ("#5b9a5b", "#7fae7f", "#c9a86a", "#b58b5a", "#9c5b3c", "#8a6e4b")
REGION_COLORS = ("#5b9a5b", "#c9a86a", "#9c5b3c", "#d8c28c", "#7fae7f",
                  "#b58b5a", "#e3d6b0", "#8a6e4b")

_LAND_COLOR = "#e3ded0"


def _country_continents():
    """iso_alpha -> continent, for every country plotly's gapminder dataset
    knows about, plus `_EXTRA`, plus any Middle East code neither one has
    (so it still lands in the universe used for "Rest of world")."""
    import plotly.express as px
    out = {}
    df = px.data.gapminder()[["iso_alpha", "continent"]].drop_duplicates()
    for iso, continent in df.itertuples(index=False):
        out[iso] = continent
    for iso, continent in _EXTRA.items():
        out.setdefault(iso, continent)
    for iso in _MIDDLE_EAST:
        out.setdefault(iso, "Asia")
    return out


def _region_membership(region, continents):
    """The full set of ISO-3 codes `region` covers, before the "first listed
    region wins" pass in `countries_by_region` strips away what an earlier,
    more specific region already claimed."""
    americas = {iso for iso, c in continents.items() if c == "Americas"}
    africa = {iso for iso, c in continents.items() if c == "Africa"}
    europe = {iso for iso, c in continents.items() if c == "Europe"}
    asia = {iso for iso, c in continents.items() if c == "Asia"}
    oceania = {iso for iso, c in continents.items() if c == "Oceania"}

    if region == "US":
        return {"USA"}
    if region == "Canada":
        return {"CAN"}
    if region == "China":
        return {"CHN"}
    if region == "Japan":
        return {"JPN"}
    if region == "India":
        return {"IND"}
    if region == "North America":
        return {"USA", "CAN"}
    if region == "Latin America":
        return americas - {"USA", "CAN", "GRL"}
    if region == "Europe":
        return europe - _MIDDLE_EAST
    if region == "Middle East & Africa":
        return africa | _MIDDLE_EAST
    if region == "EMEA":
        return europe | africa | _MIDDLE_EAST
    if region == "Asia Pacific":
        return (asia | oceania) - _MIDDLE_EAST
    if region == "Rest of world":
        return set(continents)
    return set()


def countries_by_region(regions):
    """ISO-3 codes per region in `regions`, each country assigned to the
    first region in REGION_ORDER (restricted to the ones actually listed)
    whose membership contains it."""
    continents = _country_continents()
    ordered = [r for r in REGION_ORDER if r in regions]
    taken = set()
    out = {r: [] for r in regions}
    for region in ordered:
        members = _region_membership(region, continents) - taken
        out[region] = sorted(members)
        taken |= members
    return out


def _fmt_musd(value):
    """">= 1000 -> "$x.xxB" (decimal, half-up, so 3195.0 reads "$3.20B" rather
    than the "$3.19B" plain float division gives via round-half-even on
    3.194999999999999...); else "$x.xM"."""
    if value is None:
        return "—"
    if abs(value) >= 1000:
        billions = (Decimal(str(value)) / Decimal(1000)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"${billions}B"
    return f"${value:.1f}M"


def _fmt_pct(value):
    return f"{value:.0f}%" if value is not None else "—"


def _growth_html(growth_pct, theme):
    """▲/▼ + percent, green when non-negative else red, or None when there is
    no growth figure to show."""
    if growth_pct is None:
        return None
    up = growth_pct >= 0
    color = theme.get("accent", "#81b29a") if up else theme.get("red", "#e07a5f")
    arrow = "▲" if up else "▼"
    return f'<span style="color:{color};font-weight:700">{arrow} {abs(growth_pct):.1f}%</span>'


def _panel_style(theme, radius):
    bg = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"
    return (f'background:{bg};color:{theme.get("text", "#1d1d1f")};border-radius:{radius};'
            f'padding:18px 22px;box-sizing:border-box')


def _panel_title_html(text, theme):
    return (f'<span style="font-size:.72rem;font-weight:700;letter-spacing:.07em;'
            f'text-transform:uppercase;color:{theme.get("text_muted", "#86868b")}">{qc.esc(text)}</span>')


def segments_panel_html(revenue, theme):
    """Flat "BY SEGMENT" panel: total + 1-yr growth top-right, a stacked bar
    of segment shares, and a segment/share/revenue/growth table."""
    total = revenue.get("total_musd")
    segments = revenue.get("segments") or []
    muted = theme.get("text_muted", "#86868b")
    divider = theme.get("divider", "rgba(0,0,0,0.06)")

    growth_html = _growth_html(revenue.get("growth_pct"), theme)
    growth_part = f'<div style="font-size:.78rem;margin-top:2px">{growth_html} &#183; 1-YR</div>' if growth_html else ""
    header = (f'<div style="display:flex;justify-content:space-between;align-items:flex-start;'
              f'gap:12px;margin-bottom:14px">{_panel_title_html("BY SEGMENT", theme)}'
              f'<div style="text-align:right"><div style="font-size:1.3rem;font-weight:700">'
              f'{qc.esc(_fmt_musd(total))}</div>{growth_part}</div></div>')

    bar_parts = []
    rows = []
    for i, seg in enumerate(segments):
        color = SEGMENT_COLORS[i % len(SEGMENT_COLORS)]
        share = (seg["revenue_musd"] / total * 100) if total else 0.0
        label = f'{share:.0f}%' if share >= 8 else ""
        bar_parts.append(
            f'<div style="flex:{max(share, 0.001):.4f} 0 0;background:{color};display:flex;'
            f'align-items:center;justify-content:center;color:#fff;font-size:.72rem;'
            f'font-weight:700;overflow:hidden;white-space:nowrap">{label}</div>')
        seg_growth = _growth_html(seg.get("growth_pct"), theme) or f'<span style="color:{muted}">&#8212;</span>'
        rows.append(
            f'<tr>'
            f'<td style="padding:8px 0;border-bottom:1px solid {divider}">'
            f'<span style="display:inline-block;width:10px;height:10px;border-radius:3px;'
            f'background:{color};margin-right:8px;vertical-align:middle"></span>{qc.esc(seg["name"])}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid {divider};text-align:right">{_fmt_pct(share)}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid {divider};text-align:right">'
            f'{qc.esc(_fmt_musd(seg["revenue_musd"]))}</td>'
            f'<td style="padding:8px 0;border-bottom:1px solid {divider};text-align:right">{seg_growth}</td>'
            f'</tr>')

    bar_html = (f'<div style="display:flex;width:100%;height:28px;border-radius:8px;overflow:hidden;'
                f'margin:0 0 16px">{"".join(bar_parts)}</div>')
    thead = (f'<tr><th style="text-align:left;font-size:.66rem;font-weight:700;letter-spacing:.05em;'
              f'text-transform:uppercase;color:{muted};padding:0 0 8px;border-bottom:1px solid {divider}">Segment</th>'
              f'<th style="text-align:right;font-size:.66rem;font-weight:700;letter-spacing:.05em;'
              f'text-transform:uppercase;color:{muted};padding:0 0 8px;border-bottom:1px solid {divider}">Share</th>'
              f'<th style="text-align:right;font-size:.66rem;font-weight:700;letter-spacing:.05em;'
              f'text-transform:uppercase;color:{muted};padding:0 0 8px;border-bottom:1px solid {divider}">Revenue</th>'
              f'<th style="text-align:right;font-size:.66rem;font-weight:700;letter-spacing:.05em;'
              f'text-transform:uppercase;color:{muted};padding:0 0 8px;border-bottom:1px solid {divider}">1-YR Growth</th></tr>')
    table_html = f'<table style="width:100%;border-collapse:collapse;font-size:.84rem">{thead}{"".join(rows)}</table>'

    style = _panel_style(theme, "16px")
    return f'<div style="{style}">{header}{bar_html}{table_html}</div>'


def geography_figure(revenue, theme):
    """A Plotly go.Figure choropleth, one single-colour trace per listed
    region, or None when there are no regions to plot."""
    regions = revenue.get("regions") or []
    if not regions:
        return None

    import plotly.graph_objects as go

    by_region = countries_by_region([r["region"] for r in regions])
    fig = go.Figure()
    for i, r in enumerate(regions):
        codes = by_region.get(r["region"], [])
        color = REGION_COLORS[i % len(REGION_COLORS)]
        hover = f'{r["label"]} &#183; {_fmt_pct(r["share_pct"])}'
        fig.add_trace(go.Choropleth(
            locations=codes,
            z=[1] * len(codes),
            locationmode="ISO-3",
            colorscale=[[0, color], [1, color]],
            showscale=False,
            marker_line_color="rgba(255,255,255,0.4)",
            marker_line_width=0.4,
            hovertext=[hover] * len(codes),
            hoverinfo="text",
            name=r["label"],
        ))

    fig.update_geos(showframe=False, showcoastlines=False, projection_type="natural earth",
                     bgcolor="rgba(0,0,0,0)", showland=True, landcolor=_LAND_COLOR,
                     lataxis_range=[-58, 85])
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                       margin=dict(l=0, r=0, t=0, b=0), height=320, showlegend=False)
    return fig


def geography_header_html(revenue, theme):
    """Top half of the "BY GEOGRAPHY" panel: label + total, meant to sit above
    the Plotly chart (a Streamlit container, not this HTML, holds the chart)."""
    total = revenue.get("total_musd")
    style = _panel_style(theme, "16px 16px 0 0")
    return (f'<div style="{style};padding-bottom:6px">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'{_panel_title_html("BY GEOGRAPHY", theme)}'
            f'<span style="font-size:1.3rem;font-weight:700">{qc.esc(_fmt_musd(total))}</span>'
            f'</div></div>')


def geography_legend_html(revenue, theme):
    """Bottom half of the "BY GEOGRAPHY" panel: one coloured dot + "label ·
    share%" per listed region, same colours as `geography_figure`."""
    regions = revenue.get("regions") or []
    items = []
    for i, r in enumerate(regions):
        color = REGION_COLORS[i % len(REGION_COLORS)]
        items.append(
            f'<span style="display:inline-flex;align-items:center;gap:6px;font-size:.82rem">'
            f'<span style="display:inline-block;width:9px;height:9px;border-radius:50%;'
            f'background:{color}"></span>{qc.esc(r["label"])} &#183; {_fmt_pct(r["share_pct"])}</span>')
    style = _panel_style(theme, "0 0 16px 16px")
    return (f'<div style="{style};padding-top:10px">'
            f'<div style="display:flex;flex-wrap:wrap;gap:10px 18px">{"".join(items)}</div></div>')
