"""The Phase tab was folded into the Business tab (phase in the Profile card)
and the Financials tab (revenue & operating cash flow chart) on 2026-10-06."""
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(encoding="utf-8")


def test_there_is_no_phase_tab():
    assert "_tab_phase" not in SRC
    assert ('["Business", "Moat", "Growth", "Management", "Risk", "Summary", '
            '"Capital Return", "Earnings", "Financials", "DCF",') in SRC


def test_business_tab_passes_the_phase_to_the_profile_card():
    start = SRC.index("with _tab_business:")
    block = SRC[start:SRC.index("with _tab_moat:", start)]
    assert "phase_page.phase_info(" in block
    assert '"Business Phase Analysis"' in block and '"Scorecard"' in block
    assert "phase=_ophase" in block


def test_financials_growth_section_has_the_revenue_ocf_chart():
    start = SRC.index('_fin_growth = _fin_section("growth"')
    block = SRC[start:SRC.index('_fin_section("valuation")', start)]
    assert "phase_payouts.revenue_ocf_series(" in block
    assert "phase_page.revenue_ocf_figure(" in block
    assert 'key=f"fin_rocf_range_{ticker}"' in block
    assert "No revenue history available." in block
