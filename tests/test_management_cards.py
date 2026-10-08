"""Management Cards: parser (cards + validated facts), prompt, the Management
tab's HTML and its wiring into the ticker page, prompt library and MCP."""
import copy
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import management_cards as mc
import management_page as mp

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}
ROOT = Path(__file__).resolve().parent.parent


def _facts(**kw):
    out = {"ceo": {"name": "Timothy Archer", "since": 2018},
           "insider_ownership_pct": 0.42, "ceo_ownership_pct": 0.11,
           "insider_net_12m_usd": -28_400_000, "buyers": 0, "sellers": 6,
           "planned_sell_pct": 64,
           "ceo_pay": [{"fy": "FY2024", "total_usd": 19_800_000, "stock_pct": 70},
                       {"fy": "FY2025", "total_usd": 24_100_000, "stock_pct": 78}],
           "changes": [{"date": "2025-02-10", "person": "Jane Doe", "role": "CFO",
                        "action": "appointed"}],
           "as_of": "2026-09-30", "source": "SEC Form 4 and DEF 14A via SEC-MCP"}
    out.update(kw)
    return out


def _data(facts="default"):
    data = {"cards": [{"source": k, "pick": 2, "summary": f"{k} summary",
                       "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}
                      for k, *_ in mc.ITEMS]}
    if facts == "default":
        data["facts"] = _facts()
    elif facts is not None:
        data["facts"] = facts
    return data


def _content(**kw):
    return "```json\n" + json.dumps(_data(**kw)) + "\n```"


def _house_style(html):
    assert html.startswith("<")
    assert all("\n" not in b for b in re.findall(r"<style>(.*?)</style>", html, re.S))
    assert "$" not in html


# ── items, parser ──────────────────────────────────────────────────────────

def test_items_and_title():
    assert mc.TITLE == "Management Cards"
    assert mc.ITEMS == (
        ("skin_in_the_game", "Skin in the game", "Do they have skin in the game?",
         ("Little", "Some", "Plenty")),
        ("pay", "Pay", "Is the pay reasonable?", ("Excessive", "Okay", "Reasonable")),
        ("stability", "Stability", "Is the top team stable?",
         ("Unstable", "Some change", "Stable")),
        ("capital_allocation", "Capital allocation", "Do they spend the cash well?",
         ("Poor", "Mixed", "Good")),
    )
    assert mc.MANAGEMENT.directions is None


def test_parse_returns_cards_and_facts():
    out = mc.parse_management_cards(_content())
    assert [c["source"] for c in out["cards"]] == ["skin_in_the_game", "pay", "stability",
                                                   "capital_allocation"]
    f = out["facts"]
    assert f["ceo"] == {"name": "Timothy Archer", "since": 2018}
    assert f["insider_net_12m_usd"] == -28_400_000 and f["sellers"] == 6
    assert f["ceo_pay"][1] == {"fy": "FY2025", "total_usd": 24_100_000, "stock_pct": 78}
    assert f["changes"][0]["role"] == "CFO"
    assert f["as_of"] == "2026-09-30"


def test_parse_refuses_wrong_card_count():
    d = _data(); d["cards"].pop()
    with pytest.raises(ValueError, match="four sources"):
        mc.parse_management_cards(json.dumps(d))


def test_parse_requires_facts():
    with pytest.raises(ValueError, match="facts"):
        mc.parse_management_cards(json.dumps(_data(facts=None)))


def test_all_optional_facts_may_be_null_or_absent():
    facts = {"as_of": "2026-09-30", "source": "SEC-MCP",
             "ceo": None, "insider_ownership_pct": None, "ceo_pay": None, "changes": None}
    f = mc.parse_management_cards(json.dumps(_data(facts=facts)))["facts"]
    assert f["ceo"] is None and f["insider_ownership_pct"] is None
    assert f["buyers"] is None and f["planned_sell_pct"] is None
    assert f["ceo_pay"] == [] and f["changes"] == []


@pytest.mark.parametrize("bad, match", [
    ({"as_of": None}, "as_of"),
    ({"as_of": "30-09-2026"}, "as_of"),
    ({"as_of": "2026-02-30"}, "as_of"),
    ({"source": ""}, "source"),
    ({"source": "x" * 161}, "source"),
    ({"insider_ownership_pct": 101}, "insider_ownership_pct"),
    ({"insider_ownership_pct": -1}, "insider_ownership_pct"),
    ({"ceo_ownership_pct": "5%"}, "ceo_ownership_pct"),
    ({"planned_sell_pct": True}, "planned_sell_pct"),
    ({"insider_net_12m_usd": "a lot"}, "insider_net_12m_usd"),
    ({"buyers": -1}, "buyers"),
    ({"sellers": 2.5}, "sellers"),
    ({"ceo": {"name": "", "since": 2018}}, "ceo"),
    ({"ceo": {"name": "X", "since": "2018"}}, "ceo.since"),
    ({"ceo": "Tim"}, "ceo"),
    ({"ceo_pay": [{"fy": "2025", "total_usd": 1, "stock_pct": 1}]}, "ceo_pay"),
    ({"ceo_pay": [{"fy": "FY2025", "total_usd": -1, "stock_pct": 1}]}, "ceo_pay"),
    ({"ceo_pay": [{"fy": "FY2025", "total_usd": 1, "stock_pct": 120}]}, "ceo_pay"),
    ({"ceo_pay": [{"fy": f"FY202{i}", "total_usd": 1, "stock_pct": 1} for i in range(4)]},
     "ceo_pay"),
    ({"changes": [{"date": "2025-01-01", "person": "A", "role": "CEO"}]}, "changes"),
    ({"changes": [{"date": "Jan 2025", "person": "A", "role": "CEO", "action": "left"}]},
     "changes"),
    ({"changes": [{"date": "2025-01-01", "person": "A", "role": "CEO", "action": "left"}] * 6},
     "changes"),
])
def test_parse_refuses_bad_facts(bad, match):
    with pytest.raises(ValueError, match=match):
        mc.parse_management_cards(json.dumps(_data(facts=_facts(**bad))))


def test_parse_decodes_html_entities_in_texts():
    d = _data()
    d["cards"][0]["summary"] = "Officers &amp; directors own little."
    d["cards"][0]["points"][0]["text"] = "R&amp;D head left"
    d["facts"]["changes"][0]["person"] = "O&#39;Neil"
    d["facts"]["source"] = "Form 4 &amp; DEF 14A"
    out = mc.parse_management_cards(json.dumps(d))
    assert out["cards"][0]["summary"] == "Officers & directors own little."
    assert out["cards"][0]["points"][0]["text"] == "R&D head left"
    assert out["facts"]["changes"][0]["person"] == "O'Neil"
    assert out["facts"]["source"] == "Form 4 & DEF 14A"


# ── prompt ─────────────────────────────────────────────────────────────────

def test_prompt_placeholders_style_and_items():
    import prompt_style
    p = mc.PROMPT
    for token in ("{company}", "{ticker}", "{prior:Business Analysis}"):
        assert token in p
    assert prompt_style.HOW_TO_WRITE in p
    assert p.index(prompt_style.HOW_TO_WRITE) < p.index("```json")
    for k, *_ in mc.ITEMS:
        assert k in p
    for tool in ("GetExecutiveCompensation", "GetInsiderOwnership",
                 "GetInsiderTransactions", "GetExecutiveChanges", "get_fundamentals"):
        assert tool in p


def test_prompt_cleanup_rules():
    p = mc.PROMPT
    for phrase in ("Timothy M. Archer", "18 months", "re-elections", "balance-after",
                   "option exercises", "tax withholding", "shares outstanding",
                   "several years", "never estimate", "10b5-1"):
        assert phrase in p, phrase


def test_prompt_example_json_parses():
    block = re.search(r"```json\s*(.*?)```", mc.PROMPT, re.S).group(1)
    data = json.loads(block)
    assert [c["source"] for c in data["cards"]] == [k for k, *_ in mc.ITEMS]
    full = copy.deepcopy(data)
    for c in full["cards"]:
        c["summary"] = "s"
    mc.parse_management_cards(json.dumps(full))


# ── rendering ──────────────────────────────────────────────────────────────

def test_management_section_has_four_tiles_changes_and_caption():
    html = mp.management_section_html(_content(), THEME)
    _house_style(html)
    assert '<div class="qc-label">Management</div>' in html
    for label in ("INSIDER OWNERSHIP", "CEO OWNERSHIP", "NET INSIDER TRADING", "CEO PAY"):
        assert label in html
    assert "0.42%" in html and "0.11%" in html
    # The net amount is the figure; counts and planned share in the caption.
    assert '<div class="mg-val">−&#36;28M</div>' in html
    assert "0 buyers · 6 sellers · 64% of sales pre-planned" in html
    assert "&#36;24.1M" in html and "FY2025 · 78% in stock" in html
    # Without ceo_shares the ownership tile names the CEO.
    assert "Timothy Archer" in html
    assert "Recent leadership changes" in html and "Jane Doe" in html and "CFO" in html
    assert "2026-09-30" in html and "SEC Form 4 and DEF 14A via SEC-MCP" in html


def test_management_section_with_null_facts_shows_dashes():
    facts = {"as_of": "2026-09-30", "source": "SEC-MCP"}
    html = mp.management_section_html(_content(facts=facts), THEME)
    _house_style(html)
    assert html.count(">—<") >= 4
    assert "No C-suite changes reported." in html


def test_positive_net_trading_and_ceo_pay_without_stock_share():
    facts = _facts(insider_net_12m_usd=1_260_000, buyers=3, sellers=None, planned_sell_pct=None,
                   ceo_pay=[{"fy": "FY2025", "total_usd": 950_000, "stock_pct": None}])
    html = mp.management_section_html(_content(facts=facts), THEME)
    assert '<div class="mg-val">+&#36;1.3M</div>' in html and "3 buyers · — sellers" in html
    assert "&#36;950K" in html and "in stock" not in html


def test_missing_or_invalid_section_shows_the_notice_and_no_cards():
    for content in (None, "", "not json", '{"cards": []}'):
        html = mp.management_section_html(content, THEME)
        _house_style(html)
        assert 'No Management Cards yet.' in html
        assert mp.cards_section_html(content, THEME) == ""


def test_cards_section_renders_four_flip_cards():
    html = mp.cards_section_html(_content(), THEME)
    _house_style(html)
    assert html.count('class="mc-card"') == 4
    assert "Do they have skin in the game?" in html
    assert "Do they spend the cash well?" in html


def test_legacy_three_card_section_still_renders_but_cannot_be_saved():
    d = _data(); d["cards"].pop()          # saved before 2026-10-08
    content = json.dumps(d)
    with pytest.raises(ValueError):
        mc.parse_management_cards(content)
    assert mc.parse_for_display(content)["card_set"] is mc.LEGACY_MANAGEMENT
    assert mp.cards_section_html(content, THEME).count('class="mc-card"') == 3
    assert "Timothy Archer" in mp.management_section_html(content, THEME)


def test_guidance_record_is_optional_and_shown():
    assert mc.parse_management_cards(_content())["facts"]["guidance_record"] is None
    content = _content(facts=_facts(guidance_record={"met": 7, "total": 8}))
    assert mc.parse_management_cards(content)["facts"]["guidance_record"] == {"met": 7, "total": 8}
    html = mp.management_section_html(content, THEME)
    assert "Guidance kept" in html and "<b>7 of 8</b>" in html
    assert "Guidance kept" not in mp.management_section_html(_content(), THEME)


@pytest.mark.parametrize("rec", [{"met": 3, "total": 2}, {"met": 1, "total": 0},
                                 {"met": None, "total": 3}, "7/8"])
def test_bad_guidance_record_is_refused(rec):
    with pytest.raises(ValueError, match="guidance_record"):
        mc.parse_management_cards(_content(facts=_facts(guidance_record=rec)))


def test_prompt_has_capital_allocation_and_guidance():
    assert "capital_allocation" in mc.PROMPT and "GetGuidance" in mc.PROMPT
    assert "guidance_record" in mc.PROMPT and "at most 5 calls" in mc.PROMPT


def test_dollar_signs_in_texts_are_escaped():
    d = _data()
    d["cards"][1]["summary"] = "Pay rose from $12M to $24M."
    d["facts"]["changes"][0]["action"] = "left after a $5M payout"
    content = json.dumps(d)
    assert "$" not in mp.cards_section_html(content, THEME)
    assert "$" not in mp.management_section_html(content, THEME)


def test_render_functions_never_raise(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(mp, "_tiles_html", boom)
    html = mp.management_section_html(_content(), THEME)
    assert html.startswith("<") and "No Management Cards yet." in html
    monkeypatch.setattr(mp.qc, "grid_html", boom)
    assert mp.cards_section_html(_content(), THEME) == ""


# ── wiring ─────────────────────────────────────────────────────────────────

def test_mcp_refuses_malformed_and_accepts_valid(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    store = {"X": {"ai_notes": {}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(t.upper(), cfg))
    out = mcp_server._save_prescan_section_impl("X", "Management Cards",
                                                json.dumps(_data(facts=None)), user_id="u")
    assert "error" in out and "Management Cards" in out["error"]
    mcp_server._save_prescan_section_impl("X", "Management Cards", _content(), user_id="u")
    assert "Management Cards" in store["X"]["ai_notes"]


def test_default_prompts_put_management_cards_right_after_risk_cards():
    import prescan_prompts
    titles = [p["title"] for p in prescan_prompts.DEFAULT_AI_PROMPTS]
    assert titles[titles.index("Risk Cards") + 1] == "Management Cards"
    entry = prescan_prompts.DEFAULT_AI_PROMPTS[titles.index("Management Cards")]
    assert entry["prompt"] is mc.PROMPT


def test_management_cards_does_not_import_prescan_render_at_module_load():
    import importlib
    for name in ("management_cards", "prescan_render"):
        sys.modules.pop(name, None)
    importlib.import_module("management_cards")
    assert "prescan_render" not in sys.modules


def test_ticker_page_has_a_management_tab_after_growth():
    src = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")
    assert ('["Business", "Moat", "Growth", "Management", "Risk", '
            '"Summary", "Capital Return", "Earnings", "Financials", "DCF",') in src
    assert "_tab_growth, _tab_management, _tab_risk" in src
    assert re.search(r"^import management_page$", src, re.M)
    start = src.index("with _tab_management:")
    assert src.index("with _tab_growth:") < start < src.index("with _tab_risk:")
    block = src[start:src.index("with _tab_risk:")]
    assert "management_page.management_section_html(" in block
    assert "management_page.cards_section_html(" in block
    assert ".get(management_cards.TITLE)" in block


def test_dockerfile_copies_management_cards():
    assert "management_cards.py" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_ceo_shares_and_bio():
    facts = _facts(ceo_shares=342_463_325,
                   ceo_bio={"background": "Founded it in 2004.", "reputation": "Credited and criticised."})
    html = mp.management_section_html(_content(facts=facts), THEME)
    assert "342.5M shares" in html
    assert "The CEO" in html and "yrs in the seat" in html
    assert "Founded it in 2004." in html and "How they are seen" in html
    assert "The CEO" not in mp.management_section_html(_content(), THEME)


@pytest.mark.parametrize("bio", ["text", {"background": "x"}, {"background": "x" * 301, "reputation": "y"}])
def test_bad_ceo_bio_is_refused(bio):
    with pytest.raises(ValueError, match="ceo_bio"):
        mc.parse_management_cards(_content(facts=_facts(ceo_bio=bio)))


def test_prompt_asks_for_ceo_shares_and_bio():
    assert "ceo_shares" in mc.PROMPT and "ceo_bio" in mc.PROMPT and "not by pronoun" in mc.PROMPT


def test_score_bar_uses_the_score_or_falls_back_to_the_cards():
    html = mp.management_section_html(_content(facts=_facts(score=3)), THEME)
    assert '<div class="mg-step on"><i>3</i><span>Decent</span></div>' in html
    for label in ("Poor", "Below avg", "Good", "Great"):
        assert f"<span>{label}</span>" in html
    # No score: every card picks 2 -> average 2 -> Great.
    assert '<div class="mg-step on"><i>5</i><span>Great</span></div>' in \
        mp.management_section_html(_content(), THEME)
    with pytest.raises(ValueError, match="score"):
        mc.parse_management_cards(_content(facts=_facts(score=6)))


def test_ceo_panel_photo_or_initials_tenure_and_stake_value():
    facts = _facts(ceo_ownership_pct=13.5, ceo_shares=342_463_325,
                   ceo_bio={"background": "B.", "reputation": "R."})
    html = mp.management_section_html(_content(facts=facts), THEME, price=740.0,
                                      photo="https://upload.wikimedia.org/x.jpg")
    assert '<img class="mg-face" src="https://upload.wikimedia.org/x.jpg"' in html
    assert "Photo: Wikipedia" in html
    assert "owns 13.5% (≈&#36;253.4B)" in html
    no_photo = mp.management_section_html(_content(facts=facts), THEME)
    assert '<div class="mg-face mg-init">TA</div>' in no_photo
    assert "Photo: Wikipedia" not in no_photo and "≈" not in no_photo


def test_ceo_photo_only_when_the_article_names_the_company():
    import ceo_photo
    assert ceo_photo.company_words("Meta Platforms, Inc.") == ["meta"]
    meta = {"description": "American businessman", "extract": "co-founded Facebook and Meta Platforms"}
    assert ceo_photo.matches_company(meta, "Meta Platforms, Inc.")
    namesake = {"description": "British footballer", "extract": "plays for Leeds"}
    assert not ceo_photo.matches_company(namesake, "Lam Research Corporation")
    assert ceo_photo.photo_url("", "Meta") is None
