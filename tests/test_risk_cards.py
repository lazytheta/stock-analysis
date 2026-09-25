import json
import sys

import pytest

import risk_cards

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}
RISK_TEXT = ("**Risk: Medium \U0001f7e1 · Competition**\n\nThe damage would come from CRM.\n\n"
             "- **CRM contest**: Salesforce.\n- **Concentration**: 10.1%.\n"
             "- **Disruption**: AI agents.\n\n**What would change this:** growth.")
SAAS_TEXT = ("**AI exposure: Resilient \U0001f7e1 · 2/4**\n\nAI is both a tool and a threat.\n\n"
             "- **Liability**: regulated.\n- **Network**: data.\n- **Model**: seats.\n\n"
             "**Where it would break:** seats.")


def _p():
    return {"cards": [{"source": k, "pick": 2, "summary": f"{k} summary",
                       "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}
                      for k, *_ in risk_cards.ITEMS]}


def test_items_and_title():
    assert risk_cards.TITLE == "Risk Cards"
    assert [i[0] for i in risk_cards.ITEMS] == [
        "concentration", "disruption", "outside_forces", "financial_health"]


def test_parse_accepts_four_and_refuses_three():
    assert len(risk_cards.parse_risk_cards(json.dumps(_p()))["cards"]) == 4
    p = _p(); p["cards"].pop()
    with pytest.raises(ValueError, match="four sources"):
        risk_cards.parse_risk_cards(json.dumps(p))


def test_cards_section_renders_four_or_the_notice():
    assert risk_cards.cards_section_html(json.dumps(_p()), THEME).count('class="mc-card"') == 4
    assert "Risk Cards" in risk_cards.cards_section_html(None, THEME)


def test_summary_row_shows_both_verdicts():
    html = risk_cards.summary_row_html(RISK_TEXT, SAAS_TEXT, THEME)
    assert html.count('class="ms-card"') == 2
    assert "EXECUTION RISK" in html and "MEDIUM" in html
    assert "AI EXPOSURE" in html and "RESILIENT" in html and "CRM contest" in html
    assert "\n" not in html and "$" not in html


def test_summary_row_with_one_missing_side_and_with_none():
    html = risk_cards.summary_row_html(RISK_TEXT, "free text", THEME)
    assert "Not in the verdict format yet." in html
    assert risk_cards.summary_row_html("x", "y", THEME) is None


def test_prompt_uses_both_priors_and_names_every_item():
    assert "{prior:Risk Analysis}" in risk_cards.PROMPT
    assert "{prior:SaaSpocalypse Resistance}" in risk_cards.PROMPT
    for k, *_ in risk_cards.ITEMS:
        assert k in risk_cards.PROMPT


def test_risk_cards_does_not_import_prescan_render_at_module_load():
    for name in ("risk_cards", "prescan_render"):
        sys.modules.pop(name, None)
    import importlib
    fresh = importlib.import_module("risk_cards")
    importlib.reload(fresh)
    assert "prescan_render" not in sys.modules


from unittest.mock import MagicMock


def test_mcp_refuses_malformed_risk_cards_and_accepts_valid(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    store = {"X": {"ai_notes": {}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(t.upper(), cfg))
    out = mcp_server._save_prescan_section_impl("X", "Risk Cards", '{"cards": []}', user_id="u")
    assert "error" in out and "Risk Cards" in out["error"]
    mcp_server._save_prescan_section_impl("X", "Risk Cards", json.dumps(_p()), user_id="u")
    assert "Risk Cards" in store["X"]["ai_notes"]


def test_default_prompts_put_risk_cards_right_after_risk_analysis():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert titles[titles.index("Risk Analysis") + 1] == "Risk Cards"


def test_ticker_page_has_a_risk_tab_after_moat():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert '["Pre-Scan", "Moat", "Risk", "Fundamentals", "DCF"' in src
    assert "risk_cards.summary_row_html(" in src and "risk_cards.cards_section_html(" in src
