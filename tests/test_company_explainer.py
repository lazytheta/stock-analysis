import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import company_explainer as ce

T = {"text_muted": "#888"}

PARA = ("Lam sells etch and deposition machines that chipmakers install in their "
        "fabs, and then spare parts, upgrades and service contracts on every tool "
        "for as long as that tool keeps running in the fab.")


def _block(**over):
    data = {
        "lead": "Lam Research builds the machines that etch and deposit the layers of a chip.",
        "sections": {key: PARA for key, _ in ce.SECTIONS},
        "source": "10-K FY2026, filed 2026-08-07",
    }
    data.update(over)
    return "```json\n" + json.dumps(data) + "\n```"


def test_parses_a_valid_block():
    out = ce.parse_company_explainer(_block())
    assert out["lead"].startswith("Lam Research")
    assert list(out["sections"]) == [key for key, _ in ce.SECTIONS]
    assert out["source"] == "10-K FY2026, filed 2026-08-07"


def test_missing_section_is_refused():
    sections = {key: PARA for key, _ in ce.SECTIONS}
    del sections["drivers"]
    with pytest.raises(ValueError, match="drivers"):
        ce.parse_company_explainer(_block(sections=sections))


def test_unknown_section_is_refused():
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["history"] = PARA
    with pytest.raises(ValueError, match="history"):
        ce.parse_company_explainer(_block(sections=sections))


@pytest.mark.parametrize("text", ["Too short.", "x" * 701])
def test_section_length_is_enforced(text):
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["sell"] = text
    with pytest.raises(ValueError, match="sell"):
        ce.parse_company_explainer(_block(sections=sections))


@pytest.mark.parametrize("lead", ["", "x" * 241])
def test_lead_length_is_enforced(lead):
    with pytest.raises(ValueError, match="lead"):
        ce.parse_company_explainer(_block(lead=lead))


def test_source_is_required():
    with pytest.raises(ValueError, match="source"):
        ce.parse_company_explainer(_block(source=""))


def test_not_json_is_refused():
    with pytest.raises(ValueError, match="JSON"):
        ce.parse_company_explainer("no json here")


def test_section_html_shows_lead_all_headings_and_source():
    html = ce.explainer_section_html(_block(), mission="ignored", theme=T)
    assert "What the company does" in html
    assert "Lam Research builds" in html
    for _, heading in ce.SECTIONS:
        assert ce.qc.esc(heading) in html
    assert "10-K FY2026" in html


def test_dollar_signs_are_escaped():
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["model"] = PARA + " Service brought in $8.4B and systems $14.8B last year."
    html = ce.explainer_section_html(_block(sections=sections), mission="", theme=T)
    assert "$8.4B" not in html and "&#36;8.4B" in html


def test_without_explainer_falls_back_to_mission_with_a_note():
    html = ce.explainer_section_html(None, mission="To make chips possible.", theme=T)
    assert "To make chips possible." in html
    assert "not filled yet" in html


def test_invalid_explainer_falls_back_like_a_missing_one():
    html = ce.explainer_section_html("```json\n{}\n```", mission="", theme=T)
    assert "not filled yet" in html


def test_style_blocks_are_single_line():
    html = ce.explainer_section_html(_block(), mission="", theme=T)
    for chunk in html.split("<style>")[1:]:
        assert "\n" not in chunk.split("</style>")[0]


def test_dockerfile_ships_company_explainer():
    src = Path(__file__).resolve().parent.parent.joinpath("Dockerfile").read_text()
    assert "company_explainer.py" in src


def test_mcp_refuses_an_invalid_explainer():
    import mcp_server
    assert mcp_server._CARD_PARSERS[ce.TITLE] is ce.parse_company_explainer
    out = mcp_server._save_prescan_section_impl("ABC", ce.TITLE, "```json\n{}\n```")
    assert "error" in out and ce.TITLE in out["error"]


def test_html_entities_in_the_text_are_decoded_once():
    # A model sometimes writes "G&amp;A"; escaping that again would show
    # "G&amp;A" on the page instead of "G&A".
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["drivers"] = PARA + " It reports the G&amp;A ratio."
    out = ce.parse_company_explainer(_block(sections=sections))
    assert "G&A ratio" in out["sections"]["drivers"]
    html = ce.explainer_section_html(_block(sections=sections), mission="", theme=T)
    assert "G&amp;A ratio" in html and "&amp;amp;" not in html
