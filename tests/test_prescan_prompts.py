"""prescan_prompts: the shipped default prompt library and merge_defaults,
which seeds a user's library now that the Pre-Scan tab is gone."""
import copy

import prescan_prompts as pp

TITLES = [p["title"] for p in pp.DEFAULT_AI_PROMPTS]


def test_streamlit_app_still_exposes_the_same_list():
    import streamlit_app
    assert streamlit_app.DEFAULT_AI_PROMPTS is pp.DEFAULT_AI_PROMPTS


def test_defaults_are_title_prompt_pairs_with_unique_titles():
    assert len(TITLES) == len(set(TITLES))
    for p in pp.DEFAULT_AI_PROMPTS:
        assert p["title"].strip() and p["prompt"].strip()
    assert TITLES[0] == "Robustness" and TITLES[-1] == "Scorecard"


def test_empty_library_gets_every_default_in_order():
    new, changed = pp.merge_defaults([])
    assert changed is True
    assert [p["title"] for p in new] == TITLES
    assert all(set(p) == {"title", "prompt"} for p in new)


def test_none_library_is_treated_as_empty():
    new, changed = pp.merge_defaults(None)
    assert changed is True and [p["title"] for p in new] == TITLES


def test_complete_library_is_unchanged():
    lib = [{"title": p["title"], "prompt": p["prompt"]} for p in pp.DEFAULT_AI_PROMPTS]
    before = copy.deepcopy(lib)
    new, changed = pp.merge_defaults(lib)
    assert changed is False
    assert new == before
    assert lib == before  # input not mutated


def test_one_missing_in_the_middle_is_inserted_after_its_predecessor():
    missing = "Risk Cards"
    lib = [{"title": t, "prompt": f"user {t}"} for t in TITLES if t != missing]
    new, changed = pp.merge_defaults(lib)
    assert changed is True
    titles = [p["title"] for p in new]
    assert titles == TITLES
    prev = TITLES[TITLES.index(missing) - 1]
    assert titles.index(missing) == titles.index(prev) + 1
    inserted = new[titles.index(missing)]
    assert inserted["prompt"] == pp.DEFAULT_AI_PROMPTS[TITLES.index(missing)]["prompt"]


def test_existing_prompt_text_is_kept():
    lib = [{"title": "Moat Analysis", "prompt": "my own moat prompt"}]
    new, _ = pp.merge_defaults(lib)
    moat = next(p for p in new if p["title"] == "Moat Analysis")
    assert moat["prompt"] == "my own moat prompt"
    assert [p["title"] for p in new].count("Moat Analysis") == 1


def test_custom_prompts_stay_in_place():
    lib = [{"title": "My own", "prompt": "x"},
           {"title": "Moat Analysis", "prompt": "m"}]
    new, _ = pp.merge_defaults(lib)
    titles = [p["title"] for p in new]
    # Defaults that precede Moat Analysis have no existing predecessor, so they
    # go to the front; the ones after it follow it. The custom prompt keeps its
    # place relative to Moat Analysis.
    assert titles[0] == "Robustness"
    assert titles.index("My own") == titles.index("Moat Analysis") - 1
    assert titles[titles.index("Moat Analysis") + 1] == TITLES[TITLES.index("Moat Analysis") + 1]
    assert set(TITLES) <= set(titles)


def test_first_default_missing_goes_to_the_front():
    lib = [{"title": t, "prompt": "p"} for t in TITLES[1:]]
    new, changed = pp.merge_defaults(lib)
    assert changed is True
    assert [p["title"] for p in new] == TITLES


def test_entries_without_title_are_kept():
    lib = [{"prompt": "orphan"}]
    new, _ = pp.merge_defaults(lib)
    assert {"prompt": "orphan"} in new
