import json
import sys

import pytest

import moat_cards

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}


def _card(source, pick=2, direction="stable"):
    return {"source": source, "pick": pick, "direction": direction,
            "summary": f"{source} summary.",
            "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}


def _payload(over=None):
    # over maps a card index (int) to field overrides. Not **kwargs: dict
    # unpacking into keyword arguments requires string keys, but the index
    # here is an int, so the overrides are passed as one positional dict.
    cards = [_card(k) for k, *_ in moat_cards.SOURCES]
    for i, c in enumerate(cards):
        c.update((over or {}).get(i, {}))
    return {"cards": cards}


def test_sources_are_the_five_in_order():
    assert [s[0] for s in moat_cards.SOURCES] == [
        "switching_costs", "network_effects", "intangible_assets",
        "low_cost", "counter_positioning"]
    assert all(len(s[3]) == 3 for s in moat_cards.SOURCES)


def test_parse_accepts_raw_and_fenced_json():
    raw = json.dumps(_payload())
    assert len(moat_cards.parse_moat_cards(raw)["cards"]) == 5
    fenced = "```json\n" + raw + "\n```"
    assert moat_cards.parse_moat_cards(fenced)["cards"][0]["source"] == "switching_costs"


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p["cards"].pop(), "five sources"),
    (lambda p: p["cards"].reverse(), "five sources"),
    (lambda p: p["cards"][0].update(pick=3), "pick"),
    (lambda p: p["cards"][0].update(pick=True), "pick"),
    (lambda p: p["cards"][1].update(direction="up"), "direction"),
    (lambda p: p["cards"][2].update(summary=" "), "summary"),
    (lambda p: p["cards"][3]["points"].pop(), "three points"),
    (lambda p: p["cards"][4]["points"][0].update(text=""), "three points"),
])
def test_parse_refuses_malformed_cards(mutate, fragment):
    p = _payload()
    mutate(p)
    with pytest.raises(ValueError, match=fragment):
        moat_cards.parse_moat_cards(json.dumps(p))


def test_parse_refuses_non_json():
    with pytest.raises(ValueError, match="JSON"):
        moat_cards.parse_moat_cards("**Moat: Wide**")


def test_prompt_uses_prior_moat_analysis_and_names_every_source():
    assert "{prior:Moat Analysis}" in moat_cards.PROMPT
    for key, *_ in moat_cards.SOURCES:
        assert key in moat_cards.PROMPT


from unittest.mock import MagicMock


@pytest.fixture
def mcp(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")
    store = {}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(
                            t.upper(), {**store.get(t.upper(), {}), **cfg}))
    monkeypatch.setattr(mcp_server.config_store, "load_all_configs",
                        lambda c, user_id=None, include_ai_notes=True: dict(store))
    return mcp_server, store


def test_save_refuses_malformed_moat_cards(mcp):
    m, store = mcp
    store["X"] = {"ai_notes": {}}
    out = m._save_prescan_section_impl("X", "Moat Cards", '{"cards": []}')
    assert "error" in out and "Moat Cards" in out["error"]
    assert "Moat Cards" not in store["X"]["ai_notes"]


def test_save_accepts_valid_moat_cards(mcp):
    m, store = mcp
    store["X"] = {"ai_notes": {}}
    m._save_prescan_section_impl("X", "Moat Cards", json.dumps(_payload()))
    assert "Moat Cards" in store["X"]["ai_notes"]


def test_other_sections_are_not_validated(mcp):
    m, store = mcp
    store["X"] = {"ai_notes": {}}
    m._save_prescan_section_impl("X", "Moat Analysis", "free text")
    assert store["X"]["ai_notes"]["Moat Analysis"] == "free text"


def test_tickers_missing_section(mcp):
    m, store = mcp
    store.update({
        "AAA": {"ai_notes": {"Moat Analysis": "x"}},
        "BBB": {"ai_notes": {"Moat Analysis": "x", "Moat Cards": "{}"}},
        "CCC": {"ai_notes": {}},
        "DDD": {"ai_notes": {"Moat Analysis": "y"}},
    })
    out = json.loads(m._tickers_missing_section_impl(
        "Moat Cards", requires="Moat Analysis", limit=1))
    assert out == {"tickers": ["AAA"], "remaining": 1}


def test_default_prompts_put_moat_cards_right_after_moat_analysis():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert titles[titles.index("Moat Analysis") + 1] == "Moat Cards"
    entry = streamlit_app.DEFAULT_AI_PROMPTS[titles.index("Moat Cards")]
    assert entry["prompt"] is moat_cards.PROMPT


def test_insert_prompt_is_idempotent_and_keeps_the_rest():
    from scripts.add_moat_cards_prompt import insert_prompt
    lib = [{"title": "Business Analysis", "prompt": "a"},
           {"title": "Moat Analysis", "prompt": "b"},
           {"title": "Risk Analysis", "prompt": "c"}]
    new, changed = insert_prompt(lib)
    assert changed and [p["title"] for p in new] == [
        "Business Analysis", "Moat Analysis", "Moat Cards", "Risk Analysis"]
    again, changed2 = insert_prompt(new)
    assert not changed2 and again == new
    assert lib[1]["prompt"] == "b"            # input untouched


def test_should_write_refuses_missing_or_empty_prompt_library():
    from scripts.add_moat_cards_prompt import should_write
    assert should_write({"ai_prompts": [{"title": "x", "prompt": "y"}]}) is True
    assert should_write({"ai_prompts": []}) is False
    assert should_write({}) is False
    assert should_write({"ai_prompts": None}) is False
    assert should_write({"ai_prompts": "not-a-list"}) is False


def test_flip_card_shows_pick_direction_and_three_points_on_the_back():
    card = moat_cards.parse_moat_cards(json.dumps(_payload({0: {"pick": 1, "direction": "widening"}})))["cards"][0]
    html = moat_cards.flip_card_html(card, THEME)
    assert 'type="checkbox"' in html and 'class="mc-flip"' in html
    assert "How hard is it to switch?" in html
    assert html.count('data-active="1"') == 1           # one lit answer
    assert "MODERATE" in html.upper() and "widening" in html.lower()
    assert all(f"L{i}" in html and f"T{i}" in html for i in range(3))
    assert "Back to summary" in html


def test_sources_row_has_a_dot_per_source():
    cards = moat_cards.parse_moat_cards(json.dumps(_payload()))["cards"]
    html = moat_cards.sources_row_html(cards, THEME)
    for _key, name, *_ in moat_cards.SOURCES:
        assert name.upper() in html.upper()


def test_direction_card_reads_the_verdict_line():
    text = ("**Moat: Wide 🛡️ · Narrowing ↘️ · 4/5**\n\nX has a **wide moat**.\n\n"
            "- **A**: one\n- **B**: two\n- **C**: three\n\n**Weakest link:** pricing.")
    html = moat_cards.direction_card_html(text, THEME)
    assert "NARROWING" in html.upper() and "pricing" in html
    assert moat_cards.direction_card_html("free text", THEME) is None


def test_cards_section_without_data_shows_a_notice_not_a_crash():
    for content in (None, "", "not json"):
        html = moat_cards.cards_section_html(content, THEME)
        assert "Moat Cards" in html and "mc-card" not in html


def test_cards_section_with_data_renders_five_cards_and_the_style():
    html = moat_cards.cards_section_html(json.dumps(_payload()), THEME)
    assert html.count('class="mc-card"') == 5 and "<style>" in html


def test_moat_cards_does_not_import_prescan_render_at_module_load():
    for name in ("moat_cards", "prescan_render"):
        sys.modules.pop(name, None)
    import importlib
    fresh = importlib.import_module("moat_cards")
    importlib.reload(fresh)
    assert "prescan_render" not in sys.modules


def test_ticker_page_has_a_moat_tab_after_pre_scan():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert '["Pre-Scan", "Moat", "Fundamentals", "DCF"' in src
    assert "moat_cards.cards_section_html(" in src
    assert "moat_cards.direction_card_html(" in src
