import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import growth_cards as gc
import growth_page as gp
import phase_page as pg

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd",
         "bg_secondary": "#f5f3ee", "accent": "#2f7d4f"}

RED, YELLOW, GREEN = "#c0603f", "#c79a3a", "#2f8f4e"


def _points(prefix):
    return [{"label": f"{prefix}L{i}", "text": f"{prefix}T{i}"} for i in range(3)]


def _consensus(**kw):
    out = {"fiscal_year": "FY2027", "revenue_growth_pct": 15.2, "eps_growth_pct": -3.0,
           "analysts": 23, "source": "SEC-MCP GetAnalystEstimates, 2026-09-24"}
    out.update(kw)
    return out


def _content(score=3, consensus="default", summary="Growth is **solid**.", guidance=None):
    data = {"analysis": {"score": score, "summary": summary, "points": _points("A")},
            "cards": [{"source": k, "pick": 1, "summary": f"{k} summary.",
                       "points": _points(k)} for k, *_ in gc.ITEMS]}
    if consensus == "default":
        data["consensus"] = _consensus()
    elif consensus is not None:
        data["consensus"] = consensus
    if guidance is not None:
        data["guidance"] = guidance
    return "```json\n" + json.dumps(data) + "\n```"


def _style_blocks(html):
    return re.findall(r"<style>(.*?)</style>", html, re.S)


def _house_style(html):
    assert html.startswith("<")
    assert all("\n" not in block for block in _style_blocks(html))
    assert "$" not in html


# ── Growth section ─────────────────────────────────────────────────────────

def test_growth_section_has_both_cards():
    html = gp.growth_section_html(_content(), THEME)
    _house_style(html)
    assert '<div class="qc-label">Growth</div>' in html
    assert "GROWTH ANALYSIS" in html and "CONSENSUS" in html
    assert "Growth is <b>solid</b>." in html
    for i in range(3):
        assert f"<b>AL{i}</b>: AT{i}" in html
    assert html.count('class="ms-card"') == 2


def test_growth_section_score_label_and_tone():
    for score, label, tone in ((1, "WEAK", RED), (2, "BELOW AVERAGE", RED),
                               (3, "AVERAGE", YELLOW), (4, "STRONG", GREEN),
                               (5, "EXCEPTIONAL", GREEN)):
        html = gp.growth_section_html(_content(score=score), THEME)
        assert label in html, score
        assert f'stroke="{tone}"' in html, score
        assert f">{score}</text>" in html


def test_consensus_values():
    html = gp.growth_section_html(_content(), THEME)
    for label in ("OUTLOOK", "ANALYST CONSENSUS", "Revenue growth FY2027",
                  "EPS growth FY2027", "Analysts"):
        assert label in html, label
    assert "+15.2%" in html
    assert "-3.0%" in html
    assert ">23<" in html
    assert "SEC-MCP GetAnalystEstimates, 2026-09-24" in html


def test_consensus_missing_values_dash():
    html = gp.growth_section_html(
        _content(consensus=_consensus(eps_growth_pct=None, analysts=None)), THEME)
    assert "+15.2%" in html
    assert html.count(">—<") == 2


def test_consensus_absent_line():
    html = gp.growth_section_html(_content(consensus=None), THEME)
    assert "No analyst consensus available." in html
    assert "Revenue growth" not in html
    assert "OUTLOOK" in html


def test_outlook_shows_year_two_and_guidance():
    cons = dict(_consensus(), year2={"fiscal_year": "FY2028", "revenue_growth_pct": 12.1,
                                     "eps_growth_pct": None})
    guide = {"period": "Q3 2026", "text": "Revenue $47.5B-$50.5B",
             "source": "Q2 2026 earnings release, 2026-07-29"}
    html = gp.growth_section_html(_content(consensus=cons, guidance=guide), THEME)
    _house_style(html)
    assert "Revenue growth FY2028" in html and "+12.1%" in html
    assert "COMPANY GUIDANCE · Q3 2026" in html
    assert "Revenue &#36;47.5B-&#36;50.5B" in html


def test_growth_section_notice_when_missing_or_invalid():
    for content in (None, "", "not json", _content(score=9)):
        html = gp.growth_section_html(content, THEME)
        _house_style(html)
        assert 'No growth analysis yet. It comes with the &quot;Growth Cards&quot; section.' in html
        assert "GROWTH ANALYSIS" not in html
        assert "ms-card" not in html.split("</style>")[-1]


def test_growth_section_escapes_text():
    html = gp.growth_section_html(
        _content(summary="Revenue went $608M to $1.43B <script>",
                 consensus=_consensus(source="Src $x <b>")), THEME)
    _house_style(html)
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "Src &#36;x &lt;b&gt;" in html


# ── series and YoY labels ──────────────────────────────────────────────────

def test_revenue_earnings_series_last_n_years_with_revenue():
    fund = {"years": [2019, 2020, 2021, 2022, 2023],
            "revenue": [80.0, None, 100.0, 120.0, 150.0],
            "net_income": [5.0, 6.0, None, 10.0, -4.0]}
    assert gp.revenue_earnings_series(fund, 3) == (
        [2021, 2022, 2023], [100.0, 120.0, 150.0], [None, 10.0, -4.0])
    assert gp.revenue_earnings_series(fund, 10)[0] == [2019, 2021, 2022, 2023]
    assert gp.revenue_earnings_series({}, 5) == ([], [], [])
    assert gp.revenue_earnings_series(None, 5) == ([], [], [])


def test_yoy_labels():
    assert gp.yoy_labels([100.0, 120.0, 90.0]) == ["", "+20.0%", "-25.0%"]
    assert gp.yoy_labels([]) == []
    assert gp.yoy_labels([50.0]) == [""]


def test_yoy_labels_skip_nonpositive_previous_and_none():
    assert gp.yoy_labels([-10.0, 20.0, 0.0, 5.0]) == ["", "", "-100.0%", ""]
    assert gp.yoy_labels([10.0, None, 12.0]) == ["", "", ""]
    assert gp.yoy_labels([10.0, -5.0]) == ["", "-150.0%"]


# ── figure ─────────────────────────────────────────────────────────────────

def test_revenue_earnings_figure():
    years, rev, earn = [2021, 2022, 2023], [1000.0, 1500.0, 2400.0], [200.0, -50.0, None]
    fig = gp.revenue_earnings_figure(years, rev, earn, THEME)
    assert len(fig.data) == 2
    assert [t.name for t in fig.data] == ["Revenue", "Net income"]
    assert list(fig.data[0].x) == ["FY2021", "FY2022", "FY2023"]
    assert list(fig.data[0].y) == rev and list(fig.data[1].y) == earn
    assert fig.data[0].line.color == "#2f7d4f"
    assert fig.data[1].line.color == "#5b6cff"
    assert list(fig.data[0].text) == ["", "+50.0%", "+60.0%"]
    assert list(fig.data[1].text) == ["", "-125.0%", ""]
    assert fig.data[0].textposition == "top center"
    assert fig.data[1].textposition == "bottom center"
    assert "text" in fig.data[0].mode and "markers" in fig.data[0].mode
    assert list(fig.data[0].customdata) == ["$1.0B", "$1.5B", "$2.4B"]
    assert fig.layout.height == 300
    assert fig.layout.paper_bgcolor == "rgba(0,0,0,0)"
    assert fig.layout.legend.x == 0 and fig.layout.legend.xanchor == "left"


def test_revenue_earnings_figure_uses_phase_ticks():
    years, rev, earn = [2021, 2022, 2023], [1000.0, 1500.0, 2400.0], [200.0, -50.0, None]
    fig = gp.revenue_earnings_figure(years, rev, earn, THEME)
    padded = gp.label_headroom_range(rev + earn)
    vals, text = pg.money_ticks(padded)
    assert list(fig.layout.yaxis.tickvals) == vals
    assert list(fig.layout.yaxis.ticktext) == text
    assert vals[-1] >= 2400 * 1.12 and vals[0] <= padded[0]
    ticks = dict(zip(vals, text))
    assert ticks[2000.0] == "$2.0B" and ticks[0] == "$0"


def test_revenue_earnings_figure_range_from_zero_with_label_headroom():
    fig = gp.revenue_earnings_figure([2021, 2022], [1000.0, 2000.0], [100.0, 300.0], THEME)
    lo, hi = fig.layout.yaxis.range
    assert lo == 0 and hi >= 2000.0 * 1.12
    assert hi == fig.layout.yaxis.tickvals[-1]
    assert fig.layout.legend.y == 1.0 and fig.layout.margin.t <= 30


def test_revenue_earnings_figure_range_below_zero_for_losses():
    fig = gp.revenue_earnings_figure([2021, 2022], [1000.0, 2000.0], [-200.0, 300.0], THEME)
    lo, hi = fig.layout.yaxis.range
    assert lo < -200 and hi >= 2000.0 * 1.12
    assert lo == gp.label_headroom_range([1000.0, 2000.0, -200.0, 300.0])[0]
    assert hi == fig.layout.yaxis.tickvals[-1]
    assert gp.label_headroom_range([None]) is None


# ── CAGR table ─────────────────────────────────────────────────────────────

def test_cagr_table_values_and_dashes():
    years = list(range(2014, 2025))            # 2014..2024, 11 years
    fund = {"years": years,
            "revenue": [100.0 * 1.1 ** (y - 2014) for y in years],
            "net_income": [None] * 5 + [-5.0, 8.0, 10.0, 12.0, 14.0, 20.0]}
    html = gp.cagr_table_html(fund)
    _house_style(html)
    assert 'class="ov-gtab"' in html
    assert "<th>3Y</th><th>5Y</th><th>10Y</th>" in html
    rev_row = re.search(r"<tr><td>Revenue</td>(.*?)</tr>", html).group(1)
    assert rev_row.count("+10.0%") == 3
    earn_row = re.search(r"<tr><td>Net income</td>(.*?)</tr>", html).group(1)
    # 3Y: 10 -> 20 over 2021..2024 = +26.0%; 5Y start is -5 -> dash; 10Y start None -> dash
    assert "+26.0%" in earn_row
    assert earn_row.count("—") == 2


def test_cagr_table_empty_fund_all_dashes():
    html = gp.cagr_table_html({})
    assert html.count("<td>—</td>") == 12      # Revenue, Net income, EPS, FCF x 3
    assert gp.cagr_table_html(None).count("<td>—</td>") == 12


def test_cagr_table_has_eps_and_fcf():
    html = gp.cagr_table_html({})
    order = ["Revenue", "Net income", "EPS", "FCF"]
    pos = [html.index(f"<td>{x}</td>") for x in order]
    assert pos == sorted(pos)


# ── Growth questions ───────────────────────────────────────────────────────

def test_questions_section():
    html = gp.questions_section_html(_content(), THEME)
    _house_style(html)
    assert '<div class="qc-label">Growth questions</div>' in html
    assert "Is the industry growing?" in html
    assert "Can new offerings drive growth?" in html
    assert html.count('class="mc-card"') == 2


def test_questions_section_notice():
    for content in (None, "bad", _content(score=0)):
        html = gp.questions_section_html(content, THEME)
        _house_style(html)
        assert '<div class="qc-label">Growth questions</div>' in html
        assert "mc-card" not in html.split("</style>")[-1]
        assert "Growth Cards" in html
