"""Source-level checks that the Capital Return tab replaced the Dividend tab."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "streamlit_app.py").read_text(encoding="utf-8")


def _block():
    start = SRC.index("with _tab_capital:")
    end = SRC.index("with _tab_earnings:", start)
    return SRC[start:end]


def test_tab_list_has_capital_return_after_summary_and_no_dividend_tab():
    assert ('"Risk", "Summary", "Capital Return", "Earnings", "Fundamentals", "DCF", '
            '"Reverse DCF", "Peer Comparison", "History"]') in SRC
    assert "_tab_summary, _tab_capital, _tab_earnings" in SRC
    tabs = SRC[SRC.index("= st.tabs(\n        [\"Overview\""):]
    tabs = tabs[:tabs.index("])")]
    assert '"Dividend"' not in tabs
    assert "_tab_dividend" not in SRC
    assert re.search(r"^import capital_return$", SRC, re.M)


def test_dividend_lens_ui_is_gone():
    for gone in ("_ddm_at", "_dividend_conclusion", "_render_dividend_sensitivity_matrix",
                 "tabcard_dividend", "Dividend Lens"):
        assert gone not in SRC


def test_block_reuses_loaders_and_renders_four_sections():
    block = _block()
    assert "_overview_cashflow(ticker)" in block and "_overview_income(ticker)" not in block
    for fn in ("capital_return.headline(", "capital_return.headline_section_html(",
               "capital_return.annual_flows(", "capital_return.cash_use_figure(",
               "capital_return.cash_use_caption(", "capital_return.dividend_stats(",
               "capital_return.dps_figure(", "capital_return.dividend_section_body_html(",
               "capital_return.share_count_series(", "capital_return.shares_figure(",
               "capital_return.share_count_rows(", "capital_return.share_table_html("):
        assert fn in block, fn
    assert block.count("except Exception") >= 4


def test_every_chart_has_a_unique_key_and_sections_are_styled():
    block = _block()
    assert block.count("st.plotly_chart(") == 3
    keys = re.findall(r'key=f"(cr_[a-z]+)_\{ticker\}"', block)
    assert sorted(keys) == ["cr_cash", "cr_dps", "cr_shares"]
    for name in ("qc_cr_cash", "qc_cr_dividend", "qc_cr_shares"):
        assert f'st.container(key="{name}")' in block
        assert f".st-key-{name}" in SRC
