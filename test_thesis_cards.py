"""Thesis tab: Key Debates and Red Flags prompts, parsers, merge and HTML."""

import json

import pytest

import prescan_prompts
import prompt_style
import thesis_cards as tc

DEBATES = {
    "debates": [
        {"question": "Can a rival close the spread?", "why_it_matters": "Margins halve.",
         "bull": "Network: 60% share (FY25, 10-K)", "bear": "Rival cut fees 20% (Q3)",
         "lean": "open", "answerable": "partly",
         "settles_it": "Take rate below 3% for two quarters, 10-Q",
         "precedent": "2015 price war: share held", "next_research": "Rival 10-K"},
    ],
    "downside_test": {"passes": True, "note": "Core segment still earns $2B"},
    "non_issues": [{"worry": "Capex on new segment", "why_not": "Core funds it"}],
    "conviction_ceiling": {"level": "starter", "capped_by": "Can a rival close the spread?"},
    "premortem": {
        "sell": [{"label": "Take rate", "when": "Take rate below 3% two quarters", "state": "watch"}],
        "add": [], "ignore": ["Capex on new segment"]},
    "sources": ["10-K FY2025"],
}

_KEYS = [k for k, _ in tc.CHECKS]
FLAGS = {
    "verdict": {"level": "Watch", "decided_by": "adjusted_vs_reported", "summary": "One yellow."},
    "checks": [{"check": k, "status": "yellow" if k == "adjusted_vs_reported" else "green",
                "finding": f"{k} finding", "evidence": "10-K"} for k in _KEYS],
    "could_not_check": ["Segment margins"],
    "premortem": {"sell": [{"label": "Adjusted gap", "when": "Adjusted EPS > GAAP +30%", "state": "watch"}],
                  "ignore": ["Amortisation of 2019 deal"]},
    "sources": ["10-K"],
}


def fenced(d):
    return "```json\n" + json.dumps(d) + "\n```"


SAMPLE_DEBATES, SAMPLE_FLAGS = fenced(DEBATES), fenced(FLAGS)


# ── Task 1: prompts and parsers ──────────────────────────────────────

def test_library_order():
    titles = [p["title"] for p in prescan_prompts.DEFAULT_AI_PROMPTS]
    i = titles.index
    assert i("Management Cards") < i("Red Flags") < i("Key Debates") < i("Investment Summary")


def test_summary_reads_thesis():
    inv = next(p for p in prescan_prompts.DEFAULT_AI_PROMPTS
               if p["title"] == "Investment Summary")["prompt"]
    assert "{prior:Key Debates}" in inv and "{prior:Red Flags}" in inv


def test_prompts_carry_placeholders_and_style():
    assert "{prior:Management Cards}" in tc.FLAGS_PROMPT
    assert "{prior:Red Flags}" in tc.DEBATES_PROMPT
    assert prompt_style.HOW_TO_WRITE in tc.DEBATES_PROMPT
    assert prompt_style.HOW_TO_WRITE in tc.FLAGS_PROMPT
    assert tc.DEBATES_TITLE == "Key Debates" and tc.FLAGS_TITLE == "Red Flags"


def test_ten_checks_in_spec_order():
    assert _KEYS[0] == "receivables_inventory" and _KEYS[-1] == "competitor_crosscheck"
    assert len(_KEYS) == 10


def test_parse_debates_fenced():
    d = tc.parse_debates(SAMPLE_DEBATES)
    assert d["debates"][0]["lean"] == "open"
    assert d["conviction_ceiling"]["level"] == "starter"
    assert d["premortem"]["add"] == []


def test_parse_debates_rejects_bad_lean_and_prose():
    with pytest.raises(ValueError):
        tc.parse_debates("Just prose")
    with pytest.raises(ValueError):
        tc.parse_debates(SAMPLE_DEBATES.replace('"open"', '"maybe"'))
    with pytest.raises(ValueError):
        tc.parse_debates(fenced(dict(DEBATES, debates=[])))


def test_parse_debates_missing_premortem_gives_empty_lists():
    d = tc.parse_debates(fenced({k: v for k, v in DEBATES.items() if k != "premortem"}))
    assert d["premortem"] == {"sell": [], "add": [], "ignore": []}


def test_parse_red_flags_status_and_unknown_check():
    f = tc.parse_red_flags(SAMPLE_FLAGS)
    assert f["verdict"]["level"] == "Watch" and len(f["checks"]) == 10
    assert f["premortem"]["add"] == []
    with pytest.raises(ValueError):
        tc.parse_red_flags(SAMPLE_FLAGS.replace('"yellow"', '"purple"'))
    bad = dict(FLAGS, checks=FLAGS["checks"] + [{"check": "vibes", "status": "green"}])
    with pytest.raises(ValueError):
        tc.parse_red_flags(fenced(bad))
