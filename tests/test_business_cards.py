import json
import sys

import pytest

import business_cards

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}

BIZ_VERDICT = ("**Business: Strong \U0001f7e2 · Diversified**\n\nThey sell widgets to everyone.\n\n"
               "- **A**: one\n- **B**: two\n- **C**: three\n\n**Footer:** watch this.")


def _card(source, pick=2):
    return {"source": source, "pick": pick, "summary": f"{source} summary.",
            "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}


def _block(prefix, n=4):
    return {"summary": f"{prefix} summary.",
            "points": [{"label": f"{prefix}L{i}", "text": f"{prefix}T{i}"} for i in range(n)]}


def _revenue():
    return {"period": "FY2026", "total_musd": 1000.0, "growth_pct": 10.0,
            "segments": [{"name": "Segment A", "revenue_musd": 600.0, "growth_pct": 12.0},
                         {"name": "Segment B", "revenue_musd": 400.0, "growth_pct": 8.0}],
            "regions": [{"region": "US", "label": "US", "share_pct": 50.0},
                        {"region": "Europe", "label": "Europe", "share_pct": 30.0},
                        {"region": "Asia Pacific", "label": "APAC", "share_pct": 20.0}]}


def _payload():
    return {"overview": _block("OV"), "profile": _block("PR"), "revenue": _revenue(),
            "cards": [_card(k) for k, *_ in business_cards.ITEMS]}


def test_items_and_title():
    assert business_cards.TITLE == "Business Cards"
    assert [i[0] for i in business_cards.ITEMS] == [
        "predictability", "pricing_power", "recession", "competitive_position"]


def test_parse_accepts_a_full_valid_payload():
    parsed = business_cards.parse_business_cards(json.dumps(_payload()))
    assert len(parsed["cards"]) == 4
    assert len(parsed["overview"]["points"]) == 4
    assert len(parsed["profile"]["points"]) == 4
    assert parsed["revenue"]["total_musd"] == 1000.0
    assert len(parsed["revenue"]["segments"]) == 2
    assert len(parsed["revenue"]["regions"]) == 3


def test_revenue_absent_is_none():
    p = _payload()
    del p["revenue"]
    assert business_cards.parse_business_cards(json.dumps(p))["revenue"] is None


def _segments_dont_sum(p):
    p["revenue"]["segments"][0]["revenue_musd"] = 100.0   # 100 + 400 vs total 1000


def _region_not_in_vocabulary(p):
    p["revenue"]["regions"][0]["region"] = "Mars"


def _duplicate_region(p):
    p["revenue"]["regions"][1]["region"] = "US"            # duplicates regions[0]


def _shares_sum_to_80(p):
    for r, share in zip(p["revenue"]["regions"], (50.0, 20.0, 10.0)):
        r["share_pct"] = share


def _string_number(p):
    p["revenue"]["total_musd"] = "1000"


def _bool_number(p):
    p["revenue"]["total_musd"] = True


@pytest.mark.parametrize("mutate", [
    _segments_dont_sum, _region_not_in_vocabulary, _duplicate_region,
    _shares_sum_to_80, _string_number, _bool_number,
])
def test_parse_refuses_malformed_revenue(mutate):
    p = _payload()
    mutate(p)
    with pytest.raises(ValueError, match="revenue"):
        business_cards.parse_business_cards(json.dumps(p))


def test_overview_section_renders_both_titles_and_four_plus_four_bullets():
    html = business_cards.overview_section_html(None, json.dumps(_payload()), THEME)
    assert "BUSINESS OVERVIEW" in html and "CUSTOMER PROFILE" in html
    assert html.count("<li>") == 8
    assert "OVL0" in html and "PRL0" in html


def test_overview_falls_back_to_business_analysis_verdict_without_business_cards():
    html = business_cards.overview_section_html(BIZ_VERDICT, None, THEME)
    assert "They sell widgets to everyone" in html
    overview_part, profile_part = html.split("CUSTOMER PROFILE", 1)
    assert overview_part.count("<li>") == 3
    assert "Not filled yet." in profile_part


def test_overview_section_shows_not_filled_yet_with_no_business_analysis_either():
    html = business_cards.overview_section_html(None, None, THEME)
    assert html.count("Not filled yet.") == 2


def test_quality_section_renders_four_cards_or_the_notice():
    html = business_cards.quality_section_html(json.dumps(_payload()), THEME)
    assert html.count('class="mc-card"') == 4
    assert "Business Cards" in business_cards.quality_section_html(None, THEME)


def test_prompt_uses_all_three_priors_and_names_every_item_and_region():
    assert "{prior:Business Analysis}" in business_cards.PROMPT
    assert "{prior:Moat Analysis}" in business_cards.PROMPT
    assert "{prior:Key Metrics}" in business_cards.PROMPT
    for key, *_ in business_cards.ITEMS:
        assert key in business_cards.PROMPT
    for region in business_cards.REGIONS:
        assert region in business_cards.PROMPT


def test_americas_is_a_valid_region_between_latin_america_and_europe():
    assert "Americas" in business_cards.REGIONS
    assert business_cards.REGIONS.index("Latin America") \
        < business_cards.REGIONS.index("Americas") < business_cards.REGIONS.index("Europe")


def test_parse_accepts_americas_as_a_region():
    p = _payload()
    p["revenue"]["regions"] = [{"region": "Americas", "label": "Americas", "share_pct": 100.0}]
    parsed = business_cards.parse_business_cards(json.dumps(p))
    assert parsed["revenue"]["regions"][0]["region"] == "Americas"


def test_prompt_tells_the_model_to_omit_revenue_rather_than_estimate():
    assert "omit the" in business_cards.PROMPT and "revenue" in business_cards.PROMPT
    assert "Never estimate numbers" in business_cards.PROMPT


def test_prompt_covers_eliminations_and_combining_duplicate_regions():
    assert "eliminations" in business_cards.PROMPT.lower()
    assert "combine" in business_cards.PROMPT.lower()


def test_dollar_signs_and_newlines_cannot_break_streamlit_markdown():
    p = _payload()
    p["cards"][0]["summary"] = "Grew from $608M to $1.43B."
    quality_html = business_cards.quality_section_html(json.dumps(p), THEME)
    assert "$" not in quality_html and "&#36;608M" in quality_html
    assert "\n" not in quality_html
    overview_html = business_cards.overview_section_html("x", json.dumps(p), THEME)
    assert "$" not in overview_html and "\n" not in overview_html


def test_business_cards_does_not_import_prescan_render_at_module_load():
    for name in ("business_cards", "prescan_render"):
        sys.modules.pop(name, None)
    import importlib
    fresh = importlib.import_module("business_cards")
    importlib.reload(fresh)
    assert "prescan_render" not in sys.modules


from unittest.mock import MagicMock


def test_mcp_refuses_malformed_business_cards_and_accepts_valid(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    store = {"X": {"ai_notes": {}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(t.upper(), cfg))
    out = mcp_server._save_prescan_section_impl("X", "Business Cards", '{"cards": []}', user_id="u")
    assert "error" in out and "Business Cards" in out["error"]
    mcp_server._save_prescan_section_impl("X", "Business Cards", json.dumps(_payload()), user_id="u")
    assert "Business Cards" in store["X"]["ai_notes"]


def test_default_prompts_put_business_cards_right_after_key_metrics():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert titles[titles.index("Key Metrics") + 1] == "Business Cards"


def test_ticker_page_has_a_business_tab_before_moat():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert '["Overview", "Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF"' in src
    assert "business_cards.overview_section_html(" in src
    assert "business_cards.quality_section_html(" in src
    assert "business_revenue.geography_figure(" in src
