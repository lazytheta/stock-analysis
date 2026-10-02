"""Layout for the Financials tab: themed white sections (Returns on capital,
Profitability, Growth, Valuation yield, Balance sheet), each a keyed
st.container with a small-caps label and its charts in a two-column grid.

Pure builders, no Streamlit import; the tab in streamlit_app.py computes the
numbers and draws the charts. Sections are keyed containers styled by
STYLE (they hold Plotly charts, toggles and expanders, so they cannot be
question_cards.section_html); every <style> block is emitted on one line and
every dynamic text goes through question_cards.esc (a bare `$` pair renders
as LaTeX in Streamlit's markdown).
"""

from __future__ import annotations

import question_cards as qc
from phase_page import CHART_LAYOUT, PAYOUT_COLOUR

SECTION_PREFIX = "fin_sec_"

# (key suffix, label) in render order.
SECTIONS = (
    ("returns", "Returns on capital"),
    ("profitability", "Profitability"),
    ("growth", "Growth"),
    ("valuation", "Valuation yield"),
    ("balance", "Balance sheet"),
)
_LABELS = dict(SECTIONS)

CHART_CONFIG = {"displayModeBar": False}
CHART_HEIGHT = 280
SECOND_COLOUR = PAYOUT_COLOUR
FONT_FAMILY = "-apple-system, BlinkMacSystemFont, 'Inter', sans-serif"
_GRID = "rgba(128,128,128,0.15)"

STYLE = qc.css(f"""<style>
[class*="st-key-{SECTION_PREFIX}"]{{background:var(--card);border-top:3px solid var(--accent);
  border-radius:24px;box-shadow:var(--shadow);padding:20px 24px 24px;margin:0 0 18px}}
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"]{{background:transparent !important;
  border:none !important;border-top:1px solid var(--border-light) !important;
  border-radius:0 !important;box-shadow:none !important;transform:none !important;
  animation:none !important}}
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"]:hover{{transform:none !important;
  box-shadow:none !important}}
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"] summary,
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"] summary *,
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"] details,
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"] details > div,
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpanderDetails"]{{border:none !important;
  background:transparent !important;box-shadow:none !important}}
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"] details > summary{{
  padding:12px 2px !important;font-weight:600;font-size:.86rem}}
[class*="st-key-{SECTION_PREFIX}"] [data-testid="stExpander"] details > div{{
  padding:0 2px 12px 2px !important}}
.fin-title{{display:flex;align-items:center;gap:6px;font-size:15px;font-weight:700;
  color:var(--text);margin:0 0 4px}}
.fin-tip{{position:relative;cursor:help}}
.fin-tip-box{{visibility:hidden;opacity:0;position:absolute;left:22px;top:-12px;
  background:var(--card);color:var(--text);border:1px solid var(--border-medium);
  border-radius:8px;padding:10px 14px;font-size:.78rem;line-height:1.5;font-weight:400;
  z-index:999;box-shadow:var(--shadow-hover);pointer-events:none;transition:opacity .15s ease}}
.fin-tip:hover .fin-tip-box{{visibility:visible;opacity:1}}
.fin-caption{{font-size:.78rem;color:var(--text-muted);margin-top:4px;line-height:1.45}}
</style>""")


def style_html() -> str:
    return STYLE


def section_key(name: str) -> str:
    """Container key of a section; STYLE matches every key with this prefix."""
    if name not in _LABELS:
        raise KeyError(name)
    return f"{SECTION_PREFIX}{name}"


def section_label_html(name: str) -> str:
    return f'<div class="qc-label">{qc.esc(_LABELS[name])}</div>'


def chart_key(name: str, ticker: str) -> str:
    return f"fin_{name}_{ticker}"


def chart_title_html(title: str, tip_html: str, theme, width: int = 260) -> str:
    """Bold chart title with a hover "?" tooltip. `tip_html` is trusted markup
    written in streamlit_app.py (it carries <b>/<br>), so it is not escaped;
    `$` is still turned into an entity so a pair cannot become LaTeX."""
    muted = theme.get("text_muted", "#888")
    tip = str(tip_html).replace("$", "&#36;")
    return (f'<div class="fin-title"><span>{qc.esc(title)}</span>'
            f'<span class="fin-tip">'
            f'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" '
            f'style="opacity:0.35;vertical-align:middle">'
            f'<circle cx="8" cy="8" r="7" stroke="{muted}" stroke-width="1.5"/>'
            f'<text x="8" y="11.5" text-anchor="middle" font-size="10" font-weight="600" '
            f'fill="{muted}">?</text></svg>'
            f'<span class="fin-tip-box" style="width:{int(width)}px">{tip}</span>'
            f'</span></div>')


def caption_html(text: str) -> str:
    return f'<div class="fin-caption">{qc.esc(text)}</div>'


def apply_chart_layout(fig, theme, height: int = CHART_HEIGHT):
    """The Phase/Growth chart conventions (compact margins, transparent
    background, legend above the plot's top-left) plus one tick per fiscal
    year and the theme's chart font."""
    fig.update_layout(**{**CHART_LAYOUT, "height": height},
                      font=dict(family=FONT_FAMILY,
                                color=theme.get("chart_font", theme.get("text", "#1d1d1f"))))
    fig.update_xaxes(dtick=1, showgrid=False)
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor=_GRID)
    return fig
