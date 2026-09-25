import sys

import business_revenue as br

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd",
          "bg_secondary": "#f5f3ee", "accent": "#5b9a5b", "red": "#e07a5f"}


def _revenue():
    return {"period": "FY2026", "total_musd": 3195.0, "growth_pct": 16.3,
            "segments": [{"name": "Streaming", "revenue_musd": 1430.0, "growth_pct": 21.0},
                         {"name": "Advertising", "revenue_musd": 965.0, "growth_pct": -4.0},
                         {"name": "Licensing", "revenue_musd": 800.0, "growth_pct": None}],
            "regions": [{"region": "North America", "label": "UCAN", "share_pct": 44.0},
                        {"region": "EMEA", "label": "EMEA", "share_pct": 32.0},
                        {"region": "Latin America", "label": "LATAM", "share_pct": 12.0},
                        {"region": "Asia Pacific", "label": "APAC", "share_pct": 12.0}]}


# ── countries_by_region ─────────────────────────────────────────────────

def test_specific_to_broad_assignment():
    out = br.countries_by_region(["North America", "Europe", "Asia Pacific", "Rest of world"])
    assert "USA" in out["North America"]
    assert "DEU" in out["Europe"]
    assert "JPN" in out["Asia Pacific"]
    assert "BRA" in out["Rest of world"]
    assert "SAU" in out["Rest of world"]


def test_listed_country_specific_region_wins_over_asia_pacific():
    out = br.countries_by_region(["US", "China", "Asia Pacific"])
    assert "CHN" in out["China"]
    assert "CHN" not in out["Asia Pacific"]


def test_emea_covers_europe_africa_and_middle_east():
    out = br.countries_by_region(["EMEA"])
    assert {"DEU", "ZAF", "SAU"} <= set(out["EMEA"])


def test_latin_america_excludes_us_and_canada():
    out = br.countries_by_region(["Latin America"])
    assert "USA" not in out["Latin America"]
    assert "CAN" not in out["Latin America"]
    assert "BRA" in out["Latin America"]


# ── segments_panel_html ─────────────────────────────────────────────────

def test_segments_panel_shows_total_growth_and_every_segment():
    html = br.segments_panel_html(_revenue(), THEME)
    assert "&#36;3.20B" in html
    assert "16.3%" in html
    for seg in ("Streaming", "Advertising", "Licensing"):
        assert seg in html
    assert html.count("<tr>") == 3 + 1  # header row + one per segment
    assert "$" not in html
    assert "\n" not in html


def test_segments_panel_marks_negative_and_missing_growth():
    html = br.segments_panel_html(_revenue(), THEME)
    assert "4.0%" in html and THEME["red"] in html
    assert "&#8212;" in html  # Licensing has no growth figure


# ── geography_figure ────────────────────────────────────────────────────

def test_geography_figure_has_one_trace_per_listed_region():
    fig = br.geography_figure(_revenue(), THEME)
    assert len(fig.data) == 4


def test_geography_figure_none_when_no_regions():
    rev = _revenue()
    rev["regions"] = []
    assert br.geography_figure(rev, THEME) is None


# ── geography_header_html / geography_legend_html ──────────────────────

def test_geography_header_shows_total():
    html = br.geography_header_html(_revenue(), THEME)
    assert "BY GEOGRAPHY" in html
    assert "&#36;3.20B" in html


def test_geography_legend_shows_each_label_with_share():
    html = br.geography_legend_html(_revenue(), THEME)
    for label, share in (("UCAN", "44%"), ("EMEA", "32%"), ("LATAM", "12%"), ("APAC", "12%")):
        assert label in html and share in html


# ── module import ────────────────────────────────────────────────────────

def test_module_import_does_not_import_plotly():
    for name in list(sys.modules):
        if name == "plotly" or name.startswith("plotly."):
            del sys.modules[name]
    sys.modules.pop("business_revenue", None)
    import importlib
    importlib.import_module("business_revenue")
    assert "plotly" not in sys.modules
