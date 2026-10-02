"""Source-level checks that the Growth tab is wired into the ticker page."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(encoding="utf-8")


def _growth_block():
    start = SRC.index("with _tab_growth:")
    end = SRC.index("with _tab_management:", start)
    return SRC[start:end]


def test_tab_list_has_growth_after_moat():
    assert ('["Overview", "Business", "Phase", "Moat", "Growth", "Management", "Risk", "Summary", '
            '"Capital Return", "Fundamentals", "DCF",') in SRC
    assert "_tab_growth, _tab_management, _tab_risk" in SRC


def test_growth_block_follows_moat_block():
    assert SRC.index("with _tab_moat:") < SRC.index("with _tab_growth:") < SRC.index(
        "with _tab_management:")


def test_growth_modules_imported():
    assert re.search(r"^import growth_cards$", SRC, re.M)
    assert re.search(r"^import growth_page$", SRC, re.M)


def test_growth_block_renders_all_sections():
    block = _growth_block()
    assert "cfg.get('ai_notes')" in block
    assert ".get(growth_cards.TITLE)" in block
    assert "growth_page.growth_section_html(" in block
    assert "growth_page.revenue_earnings_series(" in block
    assert "growth_page.revenue_earnings_figure(" in block
    assert "growth_page.cagr_table_html(fund)" in block
    assert "growth_page.questions_section_html(" in block


def test_growth_block_degrades_to_captions():
    block = _growth_block()
    assert "Growth analysis unavailable right now." in block
    assert "No revenue history available." in block
    assert block.count("except Exception") >= 3


def test_chart_container_and_css_rule():
    block = _growth_block()
    assert 'st.container(key="qc_growth_chart")' in block
    assert "Revenue &amp; net income" in block
    assert '("5Y", "10Y")' in block and 'default="5Y"' in block
    assert 'key=f"gr_range_{ticker}"' in block
    assert 'or "5Y"' in block
    rule = re.search(r"\.st-key-qc_growth_chart\s*\{\{(.*?)\}\}", SRC, re.S)
    phase = re.search(r"\.st-key-qc_phase_chart\s*\{\{(.*?)\}\}", SRC, re.S)
    assert rule and phase and rule.group(1).split() == phase.group(1).split()
