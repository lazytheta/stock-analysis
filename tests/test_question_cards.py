import json
import pytest
import question_cards as qc

CS = qc.CardSet("Test Cards",
                (("a", "A", "Q a?", ("Lo", "Mid", "Hi")),
                 ("b", "B", "Q b?", ("Lo", "Mid", "Hi"))),
                directions=None, trend=False)
THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}


def _p(**over):
    cards = [{"source": k, "pick": 1, "summary": f"{k} $1 to $2.",
              "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}
             for k, *_ in CS.items]
    return {"cards": cards, **over}


def test_parse_without_directions_needs_no_direction_and_ignores_trend():
    out = qc.parse(CS, json.dumps(_p(trend={"x": 1})))
    assert "direction" not in out["cards"][0] and out["trend"] is None


def test_parse_counts_the_items_in_words():
    p = _p(); p["cards"].pop()
    with pytest.raises(ValueError, match="two sources"):
        qc.parse(CS, json.dumps(p))


def test_flip_card_without_direction_has_no_direction_tag():
    card = qc.parse(CS, json.dumps(_p()))["cards"][0]
    html = qc.flip_card_html(CS, card, THEME)
    assert "mc-tag" in html                      # back-side chosen-option tag
    front = html.split("mc-back")[0]
    assert "↗" not in front and "→" not in front.replace("Details →", "")


def test_grid_and_notice_are_single_line_and_dollar_safe():
    cards = qc.parse(CS, json.dumps(_p()))["cards"]
    html = qc.grid_html(CS, cards, THEME)
    assert "\n" not in html and "$" not in html and html.count('class="mc-card"') == 2
    assert "Test Cards" in qc.notice_html("Test Cards", THEME)


def test_question_cards_does_not_import_prescan_render_at_load():
    import importlib, sys
    for n in ("question_cards", "prescan_render"):
        sys.modules.pop(n, None)
    importlib.import_module("question_cards")
    assert "prescan_render" not in sys.modules


def test_sections_carry_the_site_card_style_and_cards_inside_are_flat():
    """One white section per group (24px, accent top, site shadow) with a small
    label; the cards inside are flat panels without border or shadow."""
    html = qc.section_html("Moat sources", "<div>inner</div>")
    flat = html.replace(" ", "")
    assert 'class="qc-section"' in html and "MOAT SOURCES" in html.upper()
    for rule in ("background:var(--card)", "border-top:3pxsolidvar(--accent)",
                 "box-shadow:var(--shadow)", "border-radius:24px"):
        assert rule in flat
    face = flat.split(".mc-face{")[1].split("}")[0]
    assert "box-shadow" not in face and "border-top" not in face and "--qc-inner" in face
    assert "\n" not in html and "$" not in html
    card = qc.parse(CS, json.dumps(_p()))["cards"][0]
    assert "#2b2b2f" not in qc.flip_card_html(CS, card, THEME)
