import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import phase_page as pg

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

def test_phase_notes_cover_six_phases():
    assert sorted(pg.PHASE_NOTES) == [1, 2, 3, 4, 5, 6]
    for note in pg.PHASE_NOTES.values():
        assert set(note) == {"looks_like", "valuation", "moves_on"}
        assert all(0 < len(v) <= 80 for v in note.values())


# ── growth cycle ────────────────────────────────────────────────────────────

def _labels(svg):
    return " ".join(re.findall(r"<tspan[^>]*>([^<]*)</tspan>", svg))


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
    assert fig.layout.height == 300
    assert all(t.lstrip("-").startswith("$") for t in fig.layout.yaxis.ticktext)
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.yaxis.rangemode == "tozero"
    assert fig.layout.legend.x == 0 and fig.layout.legend.y == 1.0
    assert fig.layout.margin.t <= 30


def test_revenue_ocf_figure_ticks_in_billions():
    years, rev, cfo = [2021, 2022, 2023], [1000.0, 1500.0, 2400.0], [200.0, -50.0, None]
    fig = pg.revenue_ocf_figure(years, rev, cfo, THEME)
    ticks = dict(zip(fig.layout.yaxis.tickvals, fig.layout.yaxis.ticktext))
    assert ticks[2000.0] == "$2.0B"
    assert ticks[0] == "$0"
    assert min(ticks) <= -50 and max(ticks) >= 2400
    assert ticks[-500.0] == "-$500M"


def test_revenue_ocf_figure_ticks_in_millions():
    fig = pg.revenue_ocf_figure([2024, 2025], [510.0, 640.0], [90.0, 120.0], THEME)
    assert "$600M" in fig.layout.yaxis.ticktext
    assert not any(t.endswith("B") for t in fig.layout.yaxis.ticktext)




def test_phase_info_for_the_profile_card():
    analysis = ("**Phase: 5/6 · Capital Return**\n\nMeta **returns cash** to owners, "
                "but paused buybacks in 2026.\n\n- **Payouts**: dividend since 2024")
    info = pg.phase_info(analysis, None)
    assert info["number"] == 5 and info["name"] == "Capital Return"
    assert "**" not in info["summary"]
    assert "Valuation fits:" in info["tooltip"] and "Phase 5 of 6" in info["tooltip"]


def test_phase_info_none_when_unknown():
    assert pg.phase_info(None, None) is None
    assert pg.phase_info("garbage", "garbage") is None
