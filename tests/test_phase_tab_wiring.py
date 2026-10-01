"""Source-level checks that the Phase tab is wired into the ticker page."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(encoding="utf-8")


def _phase_block():
    start = SRC.index("with _tab_phase:")
    end = SRC.index("with _tab_moat:", start)
    return SRC[start:end]


def test_tab_list_has_phase_after_business():
    assert ('["Overview", "Business", "Phase", "Moat", "Growth", "Risk", "Summary", '
            '"Fundamentals", "DCF",') in SRC
    assert "_tab_business, _tab_phase, _tab_moat" in SRC


def test_phase_block_renders_all_three_sections():
    block = _phase_block()
    assert "phase_page.phase_section_html(" in block
    assert '"Business Phase Analysis"' in block and '"Scorecard"' in block
    assert "phase_page.revenue_ocf_figure(" in block
    assert "phase_payouts.revenue_ocf_series(" in block
    assert "phase_page.payouts_section_html(" in block
    assert "phase_payouts.buyback_card(" in block and "phase_payouts.dividend_card(" in block


def test_phase_block_reuses_overview_loaders():
    block = _phase_block()
    assert "_overview_cashflow(ticker)" in block and "_overview_income(ticker)" in block
    assert "overview_metrics.glance(fund, cfg)" in block
    assert "No revenue history available." in block
    assert "Payouts unavailable right now." in block


def test_chart_container_and_css_rule():
    block = _phase_block()
    assert 'st.container(key="qc_phase_chart")' in block
    assert 'key=f"ph_range_{ticker}"' in block
    rule = re.search(r"\.st-key-qc_phase_chart\s*\{\{(.*?)\}\}", SRC, re.S)
    ov = re.search(r"\.st-key-qc_ov_price\s*\{\{(.*?)\}\}", SRC, re.S)
    assert rule and ov and rule.group(1).split() == ov.group(1).split()
