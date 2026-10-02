"""Earnings Brief: parser, prompt, prompt-library slot, MCP save validation
and the Dockerfile."""
import copy
import json
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import earnings_brief as eb

ROOT = Path(__file__).resolve().parent.parent


def _data(**kw):
    out = {"quarter": {"fiscal_qtr_end": "2026-06-30", "label": "Q4 FY2026"},
           "tone": "Positive",
           "summary": "Cloud demand stayed ahead of capacity and margins held.",
           "points": [{"label": "What went well", "text": "Cloud revenue grew about 40%."},
                      {"label": "What worries", "text": "Spending on data centres keeps rising."},
                      {"label": "Management expects", "text": "Growth to stay above 30% next quarter."}],
           "next": {"period": "Q ending 2026-09-30", "eps_consensus": 3.85,
                    "revenue_consensus_usd": 80_500_000_000, "analysts": 34},
           "as_of": "2026-07-30", "source": "SEC-MCP: call tone and themes, analyst estimates"}
    out.update(kw)
    return out


def _content(**kw):
    return "```json\n" + json.dumps(_data(**kw)) + "\n```"


def test_title():
    assert eb.TITLE == "Earnings Brief"


def test_parse_valid_raw_and_fenced():
    out = eb.parse_earnings_brief(json.dumps(_data()))
    assert out["quarter"] == {"fiscal_qtr_end": "2026-06-30", "label": "Q4 FY2026"}
    assert out["tone"] == "Positive"
    assert len(out["points"]) == 3
    assert out["next"]["analysts"] == 34 and out["next"]["eps_consensus"] == 3.85
    assert eb.parse_earnings_brief(_content()) == out


def test_tone_is_case_insensitive_and_next_values_may_be_null():
    out = eb.parse_earnings_brief(_content(
        tone="cautious", next={"period": "Q ending 2026-09-30", "eps_consensus": None,
                               "revenue_consensus_usd": None, "analysts": None}))
    assert out["tone"] == "Cautious"
    assert out["next"] == {"period": "Q ending 2026-09-30", "eps_consensus": None,
                           "revenue_consensus_usd": None, "analysts": None}


def test_negative_eps_consensus_is_allowed():
    out = eb.parse_earnings_brief(_content(next={"period": "Q3 FY2026",
                                                 "eps_consensus": -0.12}))
    assert out["next"]["eps_consensus"] == -0.12 and out["next"]["analysts"] is None


def _points(n=3, label="Two words", text="One line."):
    return [{"label": label, "text": text} for _ in range(n)]


@pytest.mark.parametrize("change, match", [
    ({"quarter": None}, "quarter"),
    ({"quarter": {"fiscal_qtr_end": "2026-13-01", "label": "Q4"}}, "quarter.fiscal_qtr_end"),
    ({"quarter": {"fiscal_qtr_end": "2026-06-30", "label": " "}}, "quarter.label"),
    ({"tone": "Bullish"}, "tone"),
    ({"tone": None}, "tone"),
    ({"summary": ""}, "summary"),
    ({"summary": "x" * 241}, "summary"),
    ({"points": _points(2)}, "three points"),
    ({"points": _points(4)}, "three points"),
    ({"points": _points(label="Margins")}, "2-5 words"),
    ({"points": _points(label="one two three four five six")}, "2-5 words"),
    ({"points": _points(text="Line one.\nLine two.")}, "one line"),
    ({"next": None}, "next"),
    ({"next": {"period": "", "eps_consensus": 1}}, "next.period"),
    ({"next": {"period": "Q3", "eps_consensus": "1.2"}}, "next.eps_consensus"),
    ({"next": {"period": "Q3", "revenue_consensus_usd": -5}}, "next.revenue_consensus_usd"),
    ({"next": {"period": "Q3", "analysts": 3.5}}, "next.analysts"),
    ({"next": {"period": "Q3", "analysts": True}}, "next.analysts"),
    ({"as_of": "30-07-2026"}, "as_of"),
    ({"source": ""}, "source"),
    ({"source": "s" * 161}, "source"),
])
def test_parse_refuses_bad_fields(change, match):
    with pytest.raises(ValueError, match=match):
        eb.parse_earnings_brief(json.dumps(_data(**change)))


def test_parse_refuses_non_json_and_non_object():
    with pytest.raises(ValueError, match="JSON"):
        eb.parse_earnings_brief("**Tone: positive**")
    with pytest.raises(ValueError, match="object"):
        eb.parse_earnings_brief("[1, 2]")


def test_parse_decodes_html_entities():
    d = _data(summary="R&amp;D rose &amp; margins held.", source="Call &amp; estimates")
    d["points"][0]["text"] = "O&#39;Neil&#x27;s unit grew."
    d["quarter"]["label"] = "Q4 FY2026 &amp; more"
    out = eb.parse_earnings_brief(json.dumps(d))
    assert out["summary"] == "R&D rose & margins held."
    assert out["points"][0]["text"] == "O'Neil's unit grew."
    assert out["source"] == "Call & estimates"
    assert out["quarter"]["label"] == "Q4 FY2026 & more"


# ── prompt ─────────────────────────────────────────────────────────────────

def test_prompt_placeholders_style_and_tools():
    import prompt_style
    p = eb.PROMPT
    for token in ("{company}", "{ticker}", "{prior:Business Analysis}"):
        assert token in p
    assert prompt_style.HOW_TO_WRITE in p
    assert p.index(prompt_style.HOW_TO_WRITE) < p.index("```json")
    for phrase in ("GetEarningsCallToneAndThemes", "GetAnalystEstimates", "at most 2",
                   "most recently REPORTED", "share price", "never estimate"):
        assert phrase in p, phrase


def test_prompt_example_json_parses():
    block = re.search(r"```json\s*(.*?)```", eb.PROMPT, re.S).group(1)
    eb.parse_earnings_brief(block)


# ── wiring ─────────────────────────────────────────────────────────────────

def test_default_prompts_put_earnings_brief_right_after_management_cards():
    import prescan_prompts
    titles = [p["title"] for p in prescan_prompts.DEFAULT_AI_PROMPTS]
    assert titles[titles.index("Management Cards") + 1] == "Earnings Brief"
    entry = prescan_prompts.DEFAULT_AI_PROMPTS[titles.index("Earnings Brief")]
    assert entry["prompt"] is eb.PROMPT


def test_merge_defaults_seeds_earnings_brief_into_an_existing_library():
    import prescan_prompts
    lib = [{"title": p["title"], "prompt": "mine"} for p in prescan_prompts.DEFAULT_AI_PROMPTS
           if p["title"] != "Earnings Brief"]
    new, changed = prescan_prompts.merge_defaults(copy.deepcopy(lib))
    titles = [p["title"] for p in new]
    assert changed and titles[titles.index("Management Cards") + 1] == "Earnings Brief"


def test_mcp_refuses_malformed_and_accepts_valid(monkeypatch):
    import mcp_server
    assert mcp_server._CARD_PARSERS[eb.TITLE] is eb.parse_earnings_brief
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    store = {"X": {"ai_notes": {}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(t.upper(), cfg))
    out = mcp_server._save_prescan_section_impl("X", "Earnings Brief",
                                                json.dumps(_data(tone="Great")), user_id="u")
    assert "error" in out and "Earnings Brief" in out["error"] and "tone" in out["error"]
    mcp_server._save_prescan_section_impl("X", "Earnings Brief", _content(), user_id="u")
    assert "Earnings Brief" in store["X"]["ai_notes"]


def test_earnings_brief_does_not_import_prescan_render_at_module_load():
    import importlib
    for name in ("earnings_brief", "prescan_render"):
        sys.modules.pop(name, None)
    importlib.import_module("earnings_brief")
    assert "prescan_render" not in sys.modules


def test_dockerfile_copies_earnings_brief():
    assert "earnings_brief.py" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
