import json

import pytest

import moat_cards


def _card(source, pick=2, direction="stable"):
    return {"source": source, "pick": pick, "direction": direction,
            "summary": f"{source} summary.",
            "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}


def _payload(**over):
    cards = [_card(k) for k, *_ in moat_cards.SOURCES]
    for i, c in enumerate(cards):
        c.update(over.get(i, {}))
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
