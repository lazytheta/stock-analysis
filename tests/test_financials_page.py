"""financials_page helpers and source-level checks that the Financials tab
(formerly Fundamentals) is wired in the house style."""
import re
import sys
from pathlib import Path

import plotly.graph_objects as go
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import financials_page as fp
from phase_page import CHART_LAYOUT

SRC = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(encoding="utf-8")
THEME = {"text": "#222", "text_muted": "#888", "chart_font": "#1d1d1f", "accent": "#2f7d4f"}


def _block():
    start = SRC.index("with _tab_financials:")
    return SRC[start:SRC.index("with _tab_dcf:", start)]


# ── helpers ────────────────────────────────────────────────────────────────

def test_style_is_one_line_and_targets_the_section_prefix():
    style = fp.style_html()
    assert style.startswith("<style>") and style.endswith("</style>")
    assert "\n" not in style
    assert '[class*="st-key-fin_sec_"]' in style
    assert "border-top:3px solid var(--accent)" in style
    assert "border-radius:24px" in style
    # Details expanders are flat inside a section, not cards on a card.
    assert '[class*="st-key-fin_sec_"] [data-testid="stExpander"]' in style


def test_section_keys_and_labels():
    assert [n for n, _ in fp.SECTIONS] == ["returns", "profitability", "growth",
                                           "valuation", "balance"]
    for name, label in fp.SECTIONS:
        assert fp.section_key(name) == f"fin_sec_{name}"
        assert fp.section_label_html(name) == f'<div class="qc-label">{label}</div>'
    with pytest.raises(KeyError):
        fp.section_key("nope")


def test_chart_key():
    assert fp.chart_key("roce", "MSFT") == "fin_roce_MSFT"


def test_chart_title_escapes_title_and_dollars_but_keeps_tip_markup():
    html = fp.chart_title_html("A & B", "<b>&gt;5%</b> from $1 to $2", THEME, width=240)
    assert '<span>A &amp; B</span>' in html
    assert "<b>&gt;5%</b>" in html
    assert "$" not in html and "&#36;1 to &#36;2" in html
    assert 'class="fin-tip-box" style="width:240px"' in html
    assert 'stroke="#888"' in html


def test_caption_escapes():
    assert fp.caption_html("In $M. A < B") == (
        '<div class="fin-caption">In &#36;M. A &lt; B</div>')


def test_apply_chart_layout_uses_shared_conventions():
    fig = go.Figure(go.Scatter(x=[2020, 2021], y=[1, 2]))
    fig.update_yaxes(ticksuffix="%")
    out = fp.apply_chart_layout(fig, THEME)
    assert out is fig
    lay = fig.layout
    assert lay.height == fp.CHART_HEIGHT
    assert lay.margin.l == CHART_LAYOUT["margin"]["l"]
    assert lay.margin.t == CHART_LAYOUT["margin"]["t"]
    assert lay.paper_bgcolor == "rgba(0,0,0,0)"
    assert lay.hovermode == "x unified"
    assert lay.font.color == "#1d1d1f"
    assert lay.xaxis.dtick == 1 and lay.xaxis.showgrid is False
    assert lay.yaxis.ticksuffix == "%"  # earlier axis settings survive
    assert fp.apply_chart_layout(go.Figure(), THEME, height=200).layout.height == 200


def test_chart_config_hides_modebar():
    assert fp.CHART_CONFIG == {"displayModeBar": False}


# ── wiring ─────────────────────────────────────────────────────────────────

def test_tab_label_is_financials():
    tabs = SRC[SRC.index('= st.tabs(\n        ["Business"'):]
    tabs = tabs[:tabs.index("])")]
    assert '"Financials"' in tabs
    assert '"Fundamentals"' not in tabs
    assert "_tab_fundamentals" not in SRC
    assert re.search(r"^import financials_page$", SRC, re.M)


def test_block_has_no_markdown_header():
    block = _block()
    assert "####" not in block


def test_every_plotly_chart_has_a_unique_fin_key_and_no_modebar():
    block = _block()
    calls = re.findall(r"st\.plotly_chart\((.*?)\)\n", block, re.S)
    assert len(calls) == 9
    keys = []
    for call in calls:
        m = re.search(r'key=f"(fin_[a-z_]+)_\{ticker\}"', call)
        assert m, call
        keys.append(m.group(1))
        assert "config=financials_page.CHART_CONFIG" in call
    assert len(set(keys)) == len(keys)


def test_block_renders_the_five_sections_in_order():
    block = _block()
    assert "financials_page.style_html()" in block
    assert 'section_key("returns")' in block
    pos = [block.index(f'_fin_section("{n}"') for n in ("profitability", "growth",
                                                          "valuation", "balance")]
    assert block.index('section_key("returns")') < pos[0]
    assert pos == sorted(pos)
    assert "fund_sec_" not in block


def test_float_toggle_kept_in_returns_header():
    block = _block()
    ret = block[block.index('section_key("returns")'):block.index('_fin_section("profitability"')]
    assert 'key=f"roce_float_{ticker}"' in ret
    assert ret.index("st.toggle(") < ret.index("_fin_ret_l, _fin_ret_r = st.columns(")
    assert "cfg['roce_metric_override'] = 'ROE'" in ret
    assert "save_config(_sb_client, ticker, cfg)" in ret


def test_no_bare_dollar_in_markdown_tables():
    block = _block()
    assert '">${' not in block
    assert "($M)</td>" not in block and "($)</td>" not in block
