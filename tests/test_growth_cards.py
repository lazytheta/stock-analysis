import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import growth_cards as gc
# Imported at module load, before any test can pop "growth_cards" from
# sys.modules (see the reload test below) -- otherwise a later `import
# mcp_server` picks up a fresh, differently-identitied growth_cards module
# and the "is" identity check against `gc` fails. Mirrors why conftest.py
# pre-imports streamlit_app.
import mcp_server

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}


def _points(prefix, n=3):
    return [{"label": f"{prefix}L{i}", "text": f"{prefix}T{i}"} for i in range(n)]


def _card(source, pick=2):
    return {"source": source, "pick": pick, "summary": f"{source} summary.",
            "points": _points(source)}


def _analysis(score=3):
    return {"score": score, "summary": "Growth is solid.", "points": _points("A")}


def _consensus():
    return {"fiscal_year": "FY2027", "revenue_growth_pct": 15.2, "eps_growth_pct": 12.0,
            "analysts": 23, "source": "SEC-MCP GetAnalystEstimates, 2026-09-24"}


def _payload(consensus="default"):
    out = {"analysis": _analysis(),
           "cards": [_card(k) for k, *_ in gc.ITEMS]}
    if consensus == "default":
        out["consensus"] = _consensus()
    elif consensus is not None:
        out["consensus"] = consensus
    return out


def _md(obj):
    return "```json\n" + json.dumps(obj) + "\n```"


# --- basic shape -----------------------------------------------------------

def test_items_and_title():
    assert gc.TITLE == "Growth Cards"
    assert [i[0] for i in gc.ITEMS] == ["industry", "optionality"]


def test_score_labels():
    assert gc.SCORE_LABELS == {1: "Weak", 2: "Below average", 3: "Average",
                                4: "Strong", 5: "Exceptional"}


def test_valid_full_payload_parses():
    parsed = gc.parse_growth_cards(json.dumps(_payload()))
    assert parsed["analysis"]["score"] == 3
    assert len(parsed["analysis"]["points"]) == 3
    assert parsed["consensus"]["fiscal_year"] == "FY2027"
    assert len(parsed["cards"]) == 2


def test_valid_payload_via_fenced_markdown():
    parsed = gc.parse_growth_cards(_md(_payload()))
    assert parsed["analysis"]["score"] == 3


# --- consensus ---------------------------------------------------------

def test_consensus_omitted_is_none():
    p = _payload(consensus=None)
    assert gc.parse_growth_cards(json.dumps(p))["consensus"] is None


def test_consensus_null_is_none():
    p = _payload()
    p["consensus"] = None
    assert gc.parse_growth_cards(json.dumps(p))["consensus"] is None


def test_consensus_both_growth_figures_null_raises():
    p = _payload()
    p["consensus"]["revenue_growth_pct"] = None
    p["consensus"]["eps_growth_pct"] = None
    with pytest.raises(ValueError, match="consensus"):
        gc.parse_growth_cards(json.dumps(p))


def test_consensus_one_growth_figure_null_is_accepted():
    p = _payload()
    p["consensus"]["eps_growth_pct"] = None
    parsed = gc.parse_growth_cards(json.dumps(p))
    assert parsed["consensus"]["eps_growth_pct"] is None
    assert parsed["consensus"]["revenue_growth_pct"] == 15.2


@pytest.mark.parametrize("analysts", [0, True, "23", -1])
def test_consensus_invalid_analysts_raises(analysts):
    p = _payload()
    p["consensus"]["analysts"] = analysts
    with pytest.raises(ValueError, match="consensus"):
        gc.parse_growth_cards(json.dumps(p))


def test_consensus_analysts_null_is_accepted():
    p = _payload()
    p["consensus"]["analysts"] = None
    assert gc.parse_growth_cards(json.dumps(p))["consensus"]["analysts"] is None


@pytest.mark.parametrize("field,value", [
    ("revenue_growth_pct", 1500), ("revenue_growth_pct", -150),
    ("eps_growth_pct", 1500), ("eps_growth_pct", -150),
    ("revenue_growth_pct", "15.2"), ("revenue_growth_pct", True),
])
def test_consensus_invalid_growth_pct_raises(field, value):
    p = _payload()
    p["consensus"][field] = value
    with pytest.raises(ValueError, match="consensus"):
        gc.parse_growth_cards(json.dumps(p))


@pytest.mark.parametrize("value", [-100, 1000, -100.0, 1000.0])
def test_consensus_growth_pct_boundaries_accepted(value):
    p = _payload()
    p["consensus"]["revenue_growth_pct"] = value
    assert gc.parse_growth_cards(json.dumps(p))["consensus"]["revenue_growth_pct"] == value


@pytest.mark.parametrize("value", [-100.1, 1000.1])
def test_consensus_growth_pct_just_outside_boundaries_rejected(value):
    p = _payload()
    p["consensus"]["revenue_growth_pct"] = value
    with pytest.raises(ValueError, match="revenue_growth_pct"):
        gc.parse_growth_cards(json.dumps(p))


@pytest.mark.parametrize("field", ["fiscal_year", "source"])
def test_consensus_empty_string_field_raises(field):
    p = _payload()
    p["consensus"][field] = ""
    with pytest.raises(ValueError, match="consensus"):
        gc.parse_growth_cards(json.dumps(p))


def test_consensus_not_an_object_raises():
    p = _payload()
    p["consensus"] = "FY2027"
    with pytest.raises(ValueError, match="consensus"):
        gc.parse_growth_cards(json.dumps(p))


# --- analysis ------------------------------------------------------------

@pytest.mark.parametrize("score", [0, 6, True, "3", 3.5, None])
def test_invalid_score_raises(score):
    p = _payload()
    p["analysis"]["score"] = score
    with pytest.raises(ValueError, match="analysis"):
        gc.parse_growth_cards(json.dumps(p))


def test_analysis_missing_raises():
    p = _payload()
    del p["analysis"]
    with pytest.raises(ValueError, match="analysis"):
        gc.parse_growth_cards(json.dumps(p))


def test_analysis_empty_summary_raises():
    p = _payload()
    p["analysis"]["summary"] = "  "
    with pytest.raises(ValueError, match="analysis"):
        gc.parse_growth_cards(json.dumps(p))


def test_analysis_wrong_point_count_raises():
    p = _payload()
    p["analysis"]["points"] = _points("A", 2)
    with pytest.raises(ValueError, match="analysis"):
        gc.parse_growth_cards(json.dumps(p))


# --- cards -----------------------------------------------------------------

def test_cards_wrong_order_raises():
    p = _payload()
    p["cards"] = list(reversed(p["cards"]))
    with pytest.raises(ValueError):
        gc.parse_growth_cards(json.dumps(p))


def test_cards_missing_raises():
    p = _payload()
    del p["cards"]
    with pytest.raises(ValueError):
        gc.parse_growth_cards(json.dumps(p))


def test_not_json_raises():
    with pytest.raises(ValueError):
        gc.parse_growth_cards("not json at all")


# --- prompt ------------------------------------------------------------

def test_prompt_has_all_placeholders_and_never_estimate():
    for token in ("{company}", "{ticker}", "{prior:Long-Term Potential}",
                  "{prior:Business Analysis}", "{prior:Key Metrics}",
                  "GetAnalystEstimates", "never estimate"):
        assert token in gc.PROMPT


def test_prompt_explains_deriving_consensus_growth():
    # GetAnalystEstimates returns levels, not growth: the prompt must say how
    # to turn them into growth figures and that doing so is required.
    for phrase in ("FIRST fiscal year not yet reported",
                   "last REPORTED fiscal-year actual",
                   "same accounting basis",
                   "set eps_growth_pct to null",
                   "computing this ratio is required",
                   "Use null for a growth figure when the change is outside "
                   "−100% to 1000% or the prior-year value is ≤ 0."):
        assert phrase in " ".join(gc.PROMPT.split()), phrase


def test_prompt_names_every_item():
    for key, *_ in gc.ITEMS:
        assert key in gc.PROMPT


# --- rendering ---------------------------------------------------------

def test_prescan_section_html_renders_cards_and_notice():
    html = gc.prescan_section_html(json.dumps(_payload()), THEME)
    assert html.count('class="mc-card"') == 2
    assert "GROWTH ANALYSIS" in html
    assert "Growth Cards" in gc.prescan_section_html(None, THEME)


def test_prescan_section_html_no_consensus_shows_the_notice_line():
    html = gc.prescan_section_html(json.dumps(_payload(consensus=None)), THEME)
    assert "No analyst consensus available." in html


def test_dollar_signs_and_newlines_cannot_break_streamlit_markdown():
    p = _payload()
    p["analysis"]["summary"] = "Grew from $608M to $1.43B."
    html = gc.prescan_section_html(json.dumps(p), THEME)
    assert "$" not in html and "&#36;608M" in html
    assert "\n" not in html


def test_growth_cards_does_not_import_prescan_render_at_module_load():
    for name in ("growth_cards", "prescan_render"):
        sys.modules.pop(name, None)
    import importlib
    fresh = importlib.import_module("growth_cards")
    importlib.reload(fresh)
    assert "prescan_render" not in sys.modules


# --- wiring ------------------------------------------------------------

def test_mcp_validates_growth_cards():
    assert mcp_server._CARD_PARSERS[gc.TITLE] is gc.parse_growth_cards


def test_mcp_refuses_malformed_growth_cards_and_accepts_valid(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    store = {"X": {"ai_notes": {}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(t.upper(), cfg))
    out = mcp_server._save_prescan_section_impl("X", "Growth Cards", '{"cards": []}', user_id="u")
    assert "error" in out and "Growth Cards" in out["error"]
    mcp_server._save_prescan_section_impl(
        "X", "Growth Cards", json.dumps(_payload()), user_id="u")
    assert "Growth Cards" in store["X"]["ai_notes"]


def test_default_prompts_order_company_profile_then_growth_cards():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert titles.index("Company Profile") < titles.index("Growth Cards")
    assert titles[titles.index("Company Profile") + 1] == "Growth Cards"


def test_default_prompt_source_order_company_profile_before_growth_cards():
    src = Path(__file__).resolve().parent.parent.joinpath("prescan_prompts.py").read_text()
    assert src.index("company_profile.TITLE") < src.index("growth_cards.TITLE")


def test_dockerfile_ships_growth_cards():
    src = Path(__file__).resolve().parent.parent.joinpath("Dockerfile").read_text()
    assert "growth_cards.py" in src



def test_year2_and_guidance_parse_and_are_optional():
    p = _payload()
    assert gc.parse_growth_cards(_md(p))["consensus"]["year2"] is None
    assert gc.parse_growth_cards(_md(p))["guidance"] is None
    p["consensus"]["year2"] = {"fiscal_year": "FY2028", "revenue_growth_pct": 12.1,
                               "eps_growth_pct": None}
    p["guidance"] = {"items": [{"metric": "Revenue", "period": "Q3 2026", "value": "$47.5B-$50.5B"},
                               {"metric": "Capex", "period": "FY2026", "value": "$130B-$145B"}],
                     "source": "Q2 2026 earnings release, 2026-07-29"}
    out = gc.parse_growth_cards(_md(p))
    assert out["consensus"]["year2"]["fiscal_year"] == "FY2028"
    assert out["guidance"]["items"][1] == {"metric": "Capex", "period": "FY2026",
                                           "value": "$130B-$145B"}


def test_first_guidance_form_is_read_as_one_row():
    p = _payload()
    p["guidance"] = {"period": "Q3 2026", "text": "Revenue $47.5B-$50.5B", "source": "x"}
    rows = gc.parse_growth_cards(_md(p))["guidance"]["items"]
    assert rows == [{"metric": "Guidance", "period": "Q3 2026", "value": "Revenue $47.5B-$50.5B"}]


@pytest.mark.parametrize("year2", [
    "FY2028", {"fiscal_year": "", "revenue_growth_pct": 1.0},
    {"fiscal_year": "FY2028", "revenue_growth_pct": None, "eps_growth_pct": None},
    {"fiscal_year": "FY2028", "revenue_growth_pct": 2000}])
def test_bad_year2_is_refused(year2):
    p = _payload()
    p["consensus"]["year2"] = year2
    with pytest.raises(ValueError, match="year2"):
        gc.parse_growth_cards(_md(p))


@pytest.mark.parametrize("guidance", [
    "Revenue up", {"period": "Q3", "text": "", "source": "x"},
    {"period": "Q3", "text": "x" * 141, "source": "x"}, {"period": "Q3", "text": "x"},
    {"items": [], "source": "x"},
    {"items": [{"metric": "Revenue", "period": "Q3", "value": "x"}] * 5, "source": "x"},
    {"items": [{"metric": "Revenue", "period": "Q3"}], "source": "x"}])
def test_bad_guidance_is_refused(guidance):
    p = _payload()
    p["guidance"] = guidance
    with pytest.raises(ValueError, match="guidance"):
        gc.parse_growth_cards(_md(p))


def test_prompt_asks_for_year2_and_guidance():
    assert "year2" in gc.PROMPT and "GetGuidance" in gc.PROMPT
