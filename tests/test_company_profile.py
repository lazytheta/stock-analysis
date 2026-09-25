import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import company_profile as cp

GOOD = {"sector": "Communication Services", "industry": "Entertainment",
        "capital_type": "Asset-light", "difficulty": "Moderate", "founded": 1997,
        "employees": 16000, "tags": ["Subscription", "Ad-based"],
        "mission": "To entertain the world."}


def _md(obj):
    return "```json\n" + json.dumps(obj) + "\n```"


def test_valid_profile_parses():
    assert cp.parse_company_profile(_md(GOOD)) == GOOD


def test_nulls_allowed_for_founded_and_employees():
    obj = dict(GOOD, founded=None, employees=None)
    assert cp.parse_company_profile(_md(obj))["employees"] is None


@pytest.mark.parametrize("field,value", [
    ("sector", ""), ("industry", "x" * 61), ("capital_type", "Light"),
    ("difficulty", "Medium"), ("founded", 1500), ("founded", 3000),
    ("founded", "1997"), ("founded", True), ("employees", 0),
    ("employees", 12.5), ("tags", []), ("tags", ["a", "b", "c", "d", "e"]),
    ("tags", ["dup", "dup"]), ("tags", ["x" * 25]), ("tags", [""]),
    ("mission", ""), ("mission", "x" * 201),
    ("sector", 123), ("industry", 4.5), ("mission", 7), ("tags", [42]),
    ("tags", ["ok", True])])
def test_invalid_fields_raise(field, value):
    with pytest.raises(ValueError):
        cp.parse_company_profile(_md(dict(GOOD, **{field: value})))


def test_missing_field_and_bad_json_raise():
    obj = dict(GOOD)
    del obj["mission"]
    with pytest.raises(ValueError):
        cp.parse_company_profile(_md(obj))
    with pytest.raises(ValueError):
        cp.parse_company_profile("no json here")


def test_prompt_mentions_every_field_and_priors():
    for word in ("sector", "industry", "capital_type", "difficulty", "founded",
                 "employees", "tags", "mission", "{prior:Business Analysis}",
                 "{prior:Moat Analysis}", "{company}", "{ticker}"):
        assert word in cp.PROMPT


def test_mcp_validates_company_profile():
    import mcp_server
    assert mcp_server._CARD_PARSERS[cp.TITLE] is cp.parse_company_profile


def test_default_prompt_after_business_cards():
    src = Path(__file__).resolve().parent.parent.joinpath("streamlit_app.py").read_text()
    assert src.index("business_cards.TITLE") < src.index("company_profile.TITLE")


def test_profile_list_html_label_is_company_snapshot_not_title():
    # The Pre-Scan expander already shows "Company Profile" as its own
    # header, so the inner qc-section must use a distinct label -- otherwise
    # it repeats, like the sibling card modules ("Moat sources", "Risk
    # questions", "Business quality") avoid for their sets.
    html = cp.profile_list_html(_md(GOOD), {"text_muted": "#888"})
    assert 'qc-label">Company snapshot</div>' in html
    assert 'qc-label">Company Profile</div>' not in html


def test_profile_list_html_empty_still_uses_company_snapshot_label():
    html = cp.profile_list_html("", {"text_muted": "#888"})
    assert 'qc-label">Company snapshot</div>' in html
    assert 'qc-label">Company Profile</div>' not in html
    assert cp.TITLE in html  # the "No ... yet" notice still names the section
