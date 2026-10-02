"""Earnings tab: beat statistics, the four sections' HTML, the two Plotly
figures, and the tab's wiring into the ticker page."""
import json
import re
import sys
from pathlib import Path

import plotly.graph_objects as go
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import earnings_page as ep

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "accent": "#81b29a",
         "bg_secondary": "#f5f3ee"}
ROOT = Path(__file__).resolve().parent.parent


def _h(end, eps, cons, surprise=None, reported=None):
    return {"fiscal_qtr_end": end, "date_reported": reported, "eps": eps,
            "eps_consensus": cons, "surprise_pct": surprise, "source": "nasdaq"}


HISTORY = [_h("2025-09-30", 1.10, 1.00, 10.0, "2025-10-28"),
           _h("2025-12-31", 1.20, 1.10, 9.1, "2026-01-27"),
           _h("2026-03-31", 1.25, 1.15, 8.7, "2026-04-28"),
           _h("2026-06-30", 1.36, 1.25, 9.0, "2026-07-29")]


def _q(end, label, rev, rev_yoy, eps, eps_yoy):
    return {"end": end, "fiscal_label": label, "revenue": rev, "revenue_yoy": rev_yoy,
            "eps": eps, "eps_yoy": eps_yoy, "derived_q4": False, "split_adjusted": False}


QUARTERS = [_q("2026-03-28", "Q3 FY2026", 82.9e9, 0.183, 4.27, 0.234),
            _q("2026-06-27", "Q4 FY2026", 90.0e9, 0.177, 4.80, 0.315),
            _q("2025-12-27", "Q2 FY2026", 81.3e9, 0.167, 5.16, None),
            _q("2025-06-28", "Q4 FY2025", 76.4e9, None, 3.65, None)]
QUARTERS.sort(key=lambda r: r["end"])


def _brief(qtr_end="2026-06-30", label="Q4 FY2026", tone="Positive", nxt=None):
    return json.dumps({
        "quarter": {"fiscal_qtr_end": qtr_end, "label": label}, "tone": tone,
        "summary": "Cloud demand stayed ahead of capacity; margins held at $1 per share.",
        "points": [{"label": "What went well", "text": "Cloud revenue grew about 40%."},
                   {"label": "What worries", "text": "Data-centre spending keeps rising."},
                   {"label": "Management expects", "text": "Growth above 30% next quarter."}],
        "next": nxt or {"period": "Q1 FY2027", "eps_consensus": 3.85,
                        "revenue_consensus_usd": 80_500_000_000, "analysts": 34},
        "as_of": "2026-07-30", "source": "SEC-MCP: call tone, analyst estimates"})


def _house_style(html):
    assert html.startswith("<")
    assert all("\n" not in b for b in re.findall(r"<style>(.*?)</style>", html, re.S))
    assert "$" not in html


# ── beat statistics ────────────────────────────────────────────────────────

def test_beat_stats_consistent_beater():
    s = ep.beat_stats(HISTORY)
    assert (s["n"], s["beats"]) == (4, 4)
    assert s["label"] == "Consistent beater"
    assert s["avg_surprise"] == pytest.approx(9.2)
    assert s["last"]["result"] == "Beat" and s["last"]["surprise"] == pytest.approx(9.0)
    assert s["sentence"] == "Beat the EPS estimate in 4 of the last 4 quarters, by 9% on average."


@pytest.mark.parametrize("beats, label", [(3, "Consistent beater"), (2, "Mixed"),
                                          (1, "Often misses"), (0, "Often misses")])
def test_labels_by_share_of_beats(beats, label):
    rows = [_h(f"2025-0{i + 1}-28", 1.1 if i < beats else 0.9, 1.0) for i in range(4)]
    assert ep.beat_stats(rows)["label"] == label


def test_forty_percent_is_mixed():
    rows = [_h(f"2025-0{i + 1}-28", 1.1 if i < 2 else 0.9, 1.0) for i in range(5)]
    assert ep.beat_stats(rows)["label"] == "Mixed"          # 2 of 5 = 40%


def test_fewer_than_two_quarters_has_no_label_and_rows_without_consensus_are_skipped():
    s = ep.beat_stats([_h("2026-06-30", 1.0, None), _h("2026-03-31", 0.8, 1.0, None)])
    assert s["n"] == 1 and s["label"] is None
    # surprise computed when Nasdaq left it blank
    assert s["avg_surprise"] == pytest.approx(-20.0)
    assert s["last"]["result"] == "Miss"
    assert s["sentence"] == ("Beat the EPS estimate in 0 of the last 1 quarter; "
                             "on average EPS came in 20% below it.")


def test_empty_history():
    s = ep.beat_stats([])
    assert s["n"] == 0 and s["label"] is None and s["last"] is None
    assert s["sentence"] == "No EPS estimates on record yet."
    assert ep.beat_stats(None)["n"] == 0


def test_in_line_is_not_a_beat():
    s = ep.beat_stats([_h("2026-03-31", 1.0, 1.0, 0.0), _h("2026-06-30", 1.0, 1.0, 0.0)])
    assert s["beats"] == 0 and s["last"]["result"] == "In line"


def test_match_estimate_within_ten_days():
    assert ep.match_estimate("2026-06-27", HISTORY)["fiscal_qtr_end"] == "2026-06-30"
    assert ep.match_estimate("2026-06-15", HISTORY) is None
    assert ep.match_estimate("bad", HISTORY) is None
    assert ep.match_estimate("2026-06-27", None) is None


# ── HTML ───────────────────────────────────────────────────────────────────

def test_summary_section():
    html = ep.summary_section_html(HISTORY, _brief(), THEME)
    _house_style(html)
    assert '<div class="qc-label">Earnings summary</div>' in html
    assert "Consistent beater" in html
    assert "Beat the EPS estimate in 4 of the last 4 quarters, by 9% on average." in html
    for label in ("EPS BEATS", "AVERAGE SURPRISE", "LAST QUARTER", "NEXT QUARTER"):
        assert label in html
    assert "4 of 4" in html and "+9.2%" in html and "Beat +9.0%" in html
    assert "EPS &#36;3.85" in html and "&#36;80.5B" in html and "34 analysts" in html
    assert "Q1 FY2027" in html
    assert "EPS estimates: Nasdaq consensus. History grows each quarter." in html


def test_summary_section_without_data_shows_dashes():
    html = ep.summary_section_html([], None, THEME)
    _house_style(html)
    assert html.count(">—<") >= 4
    assert "No EPS estimates on record yet." in html


def test_next_quarter_with_nulls():
    nxt = {"period": "Q1 FY2027", "eps_consensus": None, "revenue_consensus_usd": None,
           "analysts": None}
    html = ep.summary_section_html(HISTORY, _brief(nxt=nxt), THEME)
    _house_style(html)
    assert "Q1 FY2027" in html and "NEXT QUARTER" in html


def test_quarterly_table_joins_estimates_and_dashes():
    html = ep.quarterly_table_html(QUARTERS, HISTORY)
    _house_style(html)
    for head in ("Period", "Revenue", "YoY", "EPS", "Est.", "vs Est."):
        assert f"<th>{head}</th>" in html
    # newest first
    assert (html.index("Q4 FY2026") < html.index("Q3 FY2026") < html.index("Q2 FY2026")
            < html.index("Q4 FY2025"))
    assert "&#36;90.0B" in html and "+17.7%" in html and "&#36;4.80" in html
    assert "&#36;1.25" in html and "+9.0%" in html            # matched 2026-06-30
    q2 = html[html.index("Q2 FY2026"):]
    assert "&#36;1.10" in q2[:q2.index("</tr>")]              # 2025-12-27 ~ 2025-12-31
    old = html[html.index("Q4 FY2025"):]
    assert old[:old.index("</tr>")].count(">—<") == 3          # no YoY, no estimate


def test_quarterly_table_empty():
    html = ep.quarterly_table_html([], HISTORY)
    _house_style(html)
    assert "No quarterly results" in html


def test_latest_call_section():
    html = ep.latest_call_section_html(_brief(), HISTORY, THEME)
    _house_style(html)
    assert '<div class="qc-label">Latest call</div>' in html
    assert "Positive" in html and ep.TONE_COLOURS["Positive"] in html
    assert "Q4 FY2026" in html and "What went well" in html and "Management expects" in html
    assert "SEC-MCP: call tone, analyst estimates" in html and "2026-07-30" in html
    assert "Out of date" not in html


@pytest.mark.parametrize("tone", ["Neutral", "Cautious"])
def test_tone_colours(tone):
    html = ep.latest_call_section_html(_brief(tone=tone), HISTORY, THEME)
    assert ep.TONE_COLOURS[tone] in html and tone in html


def test_latest_call_out_of_date_note():
    html = ep.latest_call_section_html(_brief("2026-03-31", "Q3 FY2026"), HISTORY, THEME)
    _house_style(html)
    assert "Out of date — covers Q3 FY2026" in html
    # SEC end date a few days before Nasdaq's month-end is the same quarter
    html = ep.latest_call_section_html(_brief("2026-06-27"), HISTORY, THEME)
    assert "Out of date" not in html


def test_latest_call_missing_or_invalid_shows_notice():
    for content in (None, "", "not json", '{"tone": "Positive"}'):
        html = ep.latest_call_section_html(content, HISTORY, THEME)
        _house_style(html)
        assert "No Earnings Brief yet." in html


def test_render_functions_never_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ep, "beat_stats", boom)
    assert ep.summary_section_html(HISTORY, _brief(), THEME).startswith("<")
    monkeypatch.setattr(ep, "_points_html", boom)
    assert "No Earnings Brief yet." in ep.latest_call_section_html(_brief(), HISTORY, THEME)
    monkeypatch.setattr(ep, "match_estimate", boom)
    assert ep.quarterly_table_html(QUARTERS, HISTORY).startswith("<")
    for bad in ("x", 5, [None, "y", {"end": None}]):
        ep.quarterly_table_html(bad, bad)
        ep.summary_section_html(bad, bad, THEME)
        ep.latest_call_section_html(bad, bad, THEME)
        ep.eps_figure(bad, THEME)
        ep.quarterly_figure(bad, THEME)


# ── figures ────────────────────────────────────────────────────────────────

def test_eps_figure_dots_and_open_estimates():
    hist = [*HISTORY, _h("2026-09-30", 1.0, 1.2, -16.7)]
    fig = ep.eps_figure(hist, THEME, QUARTERS)
    assert isinstance(fig, go.Figure)
    actual, estimate = fig.data[0], fig.data[1]
    assert actual.name == "Actual" and estimate.name == "Estimate"
    assert list(actual.marker.color) == [ep.BEAT_COLOUR] * 4 + [ep.MISS_COLOUR]
    assert estimate.marker.symbol == "circle-open"
    assert list(actual.text) == ["$1.10", "$1.20", "$1.25", "$1.36", "$1.00"]
    # a Nasdaq month-end that matches a SEC quarter takes its fiscal label
    assert "Q4 FY2026" in list(actual.x)
    assert list(actual.x)[0] == "Sep 2025"


def test_eps_figure_none_without_data():
    assert ep.eps_figure([], THEME) is None
    assert ep.eps_figure([_h("2026-06-30", None, 1.0)], THEME) is None


def test_quarterly_figure_bars_line_and_yoy_labels():
    fig = ep.quarterly_figure(QUARTERS, THEME)
    bars, line = fig.data[0], fig.data[1]
    assert isinstance(bars, go.Bar) and isinstance(line, go.Scatter)
    assert list(bars.x) == ["Q4 FY2025", "Q2 FY2026", "Q3 FY2026", "Q4 FY2026"]
    assert list(bars.text) == ["", "+16.7%", "+18.3%", "+17.7%"]
    assert line.yaxis == "y2"
    assert ep.quarterly_figure([], THEME) is None


# ── wiring ─────────────────────────────────────────────────────────────────

SRC = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")


def _block():
    start = SRC.index("with _tab_earnings:")
    return SRC[start:SRC.index("with _tab_dcf:", start)]


def test_tab_list_has_earnings_after_capital_return():
    assert ('"Summary", "Capital Return", "Earnings", "Fundamentals", "DCF",') in SRC
    assert re.search(r"_tab_capital, _tab_earnings,\s+_tab_fundamentals", SRC)
    assert re.search(r"^import earnings_page$", SRC, re.M)
    assert SRC.index("with _tab_capital:") < SRC.index("with _tab_earnings:")


def test_block_reads_cached_sources_and_renders_four_sections():
    block = _block()
    for fn in ("_earnings_history(ticker)", "_quarterly_results(ticker)",
               "earnings_page.summary_section_html(", "earnings_page.eps_figure(",
               "earnings_page.quarterly_figure(", "earnings_page.quarterly_table_html(",
               "earnings_page.latest_call_section_html(", ".get(earnings_brief.TITLE)"):
        assert fn in block, fn
    assert block.count("except Exception") >= 4
    assert "@st.cache_data(ttl=3600, show_spinner=False)\ndef _earnings_history(" in SRC
    assert "@st.cache_data(ttl=86400, show_spinner=False)\ndef _quarterly_results(" in SRC


def test_every_chart_has_a_unique_key_and_sections_are_styled():
    block = _block()
    assert block.count("st.plotly_chart(") == 2
    keys = re.findall(r'key=f"(er_[a-z]+)_\{ticker\}"', block)
    assert sorted(keys) == ["er_eps", "er_quarters"]
    for name in ("qc_er_eps", "qc_er_quarters"):
        assert f'st.container(key="{name}")' in block
        assert f".st-key-{name}" in SRC
