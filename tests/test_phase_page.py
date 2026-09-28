import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import phase_page as pg
import phase_payouts as pp

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd",
         "bg_secondary": "#f5f3ee", "accent": "#2f7d4f"}

ANALYSIS = ("**Phase: Capital return · 5/6**\n\nLead sentence costs $5M to $9M.\n\n"
            "- **A**: x\n- **B**: y\n- **C**: z")
SCORECARD_4 = '```json\n{"verdict": "Buy", "phase": {"number": 4}}\n```'


def _style_blocks(html):
    return re.findall(r"<style>(.*?)</style>", html, re.S)


# ── phase_number ────────────────────────────────────────────────────────────

def test_phase_number_from_analysis_score():
    assert pg.phase_number(ANALYSIS, None) == 5
    assert pg.phase_number(ANALYSIS, SCORECARD_4) == 5


def test_phase_number_falls_back_to_scorecard_when_not_out_of_six():
    text = ANALYSIS.replace("5/6", "4/5")
    assert pg.phase_number(text, SCORECARD_4) == 4
    assert pg.phase_number(None, SCORECARD_4) == 4
    assert pg.phase_number(ANALYSIS.replace("5/6", "7/6"), SCORECARD_4) == 4


def test_phase_number_scorecard_compact_and_out_of_range():
    assert pg.phase_number(None, '{"phase": 2}') == 2
    assert pg.phase_number(None, '{"phase": {"number": 9}}') is None


def test_phase_number_garbage_is_none():
    assert pg.phase_number("just prose", "not json") is None
    assert pg.phase_number(None, None) is None
    assert pg.phase_number(123, ["x"]) is None


# ── phase section ───────────────────────────────────────────────────────────

def test_section_html_with_analysis():
    html = pg.phase_section_html(ANALYSIS, None, THEME)
    assert html.startswith("<")
    assert "Phase Analysis" in html and "Growth cycle" in html
    assert "Capital Return" in html
    assert "Lead sentence costs" in html
    assert "$" not in html
    for label in ("A", "B", "C"):
        assert f"<b>{label}</b>" in html
    for block in _style_blocks(html):
        assert "\n" not in block
    assert html.count('class="gc-band"') == 1
    assert "No phase analysis yet." not in html


def test_section_html_scorecard_only_shows_badge_and_notice():
    html = pg.phase_section_html(None, SCORECARD_4, THEME)
    assert "Operating Leverage" in html
    assert "No phase analysis yet." in html
    assert html.count('class="gc-band"') == 1


def test_section_html_nothing_parses_shows_notice_without_highlight():
    html = pg.phase_section_html("free prose", "nope", THEME)
    assert html.startswith("<")
    assert "No phase analysis yet." in html
    assert "Business Phase Analysis" in html
    assert 'class="gc-band"' not in html
    assert 'class="ph-num"' not in html


# ── growth cycle ────────────────────────────────────────────────────────────

def _labels(svg):
    return " ".join(re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg))


def test_growth_cycle_svg_six_labels_and_one_band():
    svg = pg.growth_cycle_svg(5)
    assert svg.startswith("<svg") and 'viewBox="0 0 600 220"' in svg
    text = _labels(svg)
    for word in ("Startup", "Hypergrowth", "Self Funding", "Operating", "Leverage",
                 "Capital Return", "Decline"):
        assert word in text
    for n in range(1, 7):
        assert re.search(rf">{n} ", svg)
    assert svg.count('class="gc-band"') == 1
    assert svg.count("<path") == 3
    for name in ("Revenue", "Profits", "Payouts"):
        assert name in svg
    assert "#5b6cff" in svg and "var(--accent)" in svg


def test_growth_cycle_svg_without_phase_has_no_band():
    svg = pg.growth_cycle_svg(None)
    assert 'class="gc-band"' not in svg
    assert pg.growth_cycle_svg(0).count('class="gc-band"') == 0


def test_growth_cycle_band_sits_over_the_current_column():
    svg = pg.growth_cycle_svg(3)
    band = re.search(r'<rect class="gc-band" x="([\d.]+)"', svg)
    assert band and float(band.group(1)) == 200.0


# ── revenue / OCF figure ───────────────────────────────────────────────────

def test_revenue_ocf_figure():
    years, rev, cfo = [2021, 2022, 2023], [1000.0, 1500.0, 2400.0], [200.0, -50.0, None]
    fig = pg.revenue_ocf_figure(years, rev, cfo, THEME)
    assert len(fig.data) == 2
    assert list(fig.data[0].y) == rev
    assert list(fig.data[1].y) == cfo
    assert list(fig.data[0].x) == ["FY2021", "FY2022", "FY2023"]
    assert fig.data[0].line.color == "#2f7d4f"
    assert fig.data[1].line.color == "#5b6cff"
    assert list(fig.data[0].customdata) == ["$1.0B", "$1.5B", "$2.4B"]
    assert fig.data[1].customdata[2] == "—"
    assert fig.layout.height == 320
    assert fig.layout.yaxis.tickprefix == "$"
    assert fig.layout.legend.orientation == "h"


# ── payouts ────────────────────────────────────────────────────────────────

def test_payouts_section_contains_both_questions():
    fund = {"years": [2021, 2022, 2023, 2024, 2025, 2026],
            "revenue": [100.0] * 6, "shares": [1e6, 1e6, 1e6, 1e6, 1e6, 0.9e6]}
    cash = {"years": fund["years"], "stock_buybacks": [-5.0] * 6,
            "dividends_paid": [0.0] * 6}
    buy = pp.buyback_card(fund, cash, 10.0)
    div = pp.dividend_card(fund, cash, None, 10.0, 50.0)
    html = pg.payouts_section_html(buy, div, THEME)
    assert html.startswith("<")
    assert "Payouts" in html
    assert "Are they buying back stock?" in html
    assert "Do they pay a dividend?" in html
    assert html.count('class="mc-card"') == 2
    assert "$" not in html
