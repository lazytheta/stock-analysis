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


@pytest.mark.parametrize("key,text", [("customers", "Too short."), ("customers", "x" * 701),
                                      ("offer", "Too short."), ("offer", "x" * 901)])
def test_section_length_is_enforced(key, text):
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections[key] = text
    with pytest.raises(ValueError, match=key):
        ce.parse_company_explainer(_block(sections=sections))


def test_offer_may_run_longer_than_the_other_sections():
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["offer"] = "x" * 900
    assert len(ce.parse_company_explainer(_block(sections=sections))["sections"]["offer"]) == 900


def test_four_sections_with_sell_and_model_merged():
    assert [k for k, _ in ce.SECTIONS] == ["offer", "customers", "drivers", "competitors"]
    assert dict(ce.SECTIONS)["competitors"] == "Who they compete with"
    assert dict(ce.SECTIONS)["drivers"] == "What to watch each quarter"
    assert dict(ce.SECTIONS)["offer"] == "What they sell & how they earn"


def _legacy_sections():
    return {"sell": "SELL " + PARA, "customers": PARA, "model": "MODEL " + PARA,
            "drivers": PARA, "chain": "CHAIN " + PARA}


def test_legacy_five_section_explainer_still_parses():
    # Explainers saved before 2026-10-05 have "sell" and "model"; they render
    # joined under the new "offer" heading until re-run.
    out = ce.parse_company_explainer(_block(sections=_legacy_sections()))
    assert list(out["sections"]) == ["offer", "customers", "drivers", "competitors"]
    assert out["sections"]["offer"] == f"SELL {PARA} MODEL {PARA}"
    assert out["sections"]["competitors"] == f"CHAIN {PARA}"


def test_offer_with_legacy_chain_still_parses():
    # META was re-run on 2026-10-05 with "offer" but still "chain".
    sections = {"offer": PARA, "customers": PARA, "drivers": PARA, "chain": PARA}
    out = ce.parse_company_explainer(_block(sections=sections))
    assert out["sections"]["competitors"] == PARA


def test_competitors_mixed_with_chain_is_refused():
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["chain"] = PARA
    with pytest.raises(ValueError, match="chain"):
        ce.parse_company_explainer(_block(sections=sections))


def test_legacy_explainer_missing_model_is_refused():
    sections = _legacy_sections()
    del sections["model"]
    with pytest.raises(ValueError, match="model"):
        ce.parse_company_explainer(_block(sections=sections))


def test_offer_mixed_with_legacy_keys_is_refused():
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["sell"] = PARA
    with pytest.raises(ValueError, match="sell"):
        ce.parse_company_explainer(_block(sections=sections))


def test_prompt_asks_for_the_four_keys():
    for key, _ in ce.SECTIONS:
        assert f'"{key}"' in ce.PROMPT
    assert '"sell"' not in ce.PROMPT and '"model"' not in ce.PROMPT
    assert '"chain"' not in ce.PROMPT and "No suppliers, no customers" in ce.PROMPT


@pytest.mark.parametrize("lead", ["", "x" * 401])
def test_lead_length_is_enforced(lead):
    with pytest.raises(ValueError, match="lead"):
        ce.parse_company_explainer(_block(lead=lead))


def test_source_is_required():
    with pytest.raises(ValueError, match="source"):
        ce.parse_company_explainer(_block(source=""))


def test_not_json_is_refused():
    with pytest.raises(ValueError, match="JSON"):
        ce.parse_company_explainer("no json here")


def test_section_html_shows_all_headings_and_source_but_not_the_lead():
    # The lead is shown in the Overview's Profile card ("What it does").
    html = ce.explainer_section_html(_block(), theme=T)
    assert "What the company does" in html
    assert "Lam Research builds" not in html
    for _, heading in ce.SECTIONS:
        assert ce.qc.esc(heading) in html
    assert "10-K FY2026" in html


def test_dollar_signs_are_escaped():
    sections = {key: PARA for key, _ in ce.SECTIONS}
    sections["offer"] = PARA + " Service brought in $8.4B and systems $14.8B last year."
    html = ce.explainer_section_html(_block(sections=sections), theme=T)
    assert "$8.4B" not in html and "&#36;8.4B" in html


def test_without_explainer_shows_only_a_note():
    html = ce.explainer_section_html(None, theme=T)
    assert "not filled yet" in html


def test_lead_text_returns_the_lead_or_none():
    assert ce.lead_text(_block()).startswith("Lam Research builds")
    assert ce.lead_text(None) is None
    assert ce.lead_text("```json\n{}\n```") is None


def test_lead_may_run_to_three_sentences():
    lead = ("Lam makes chip-making machines. It keeps earning on service for years. "
            "Its customers are a few giant chipmakers, mostly in Asia, who buy in cycles. ") * 2
    assert len(lead.strip()) <= ce.LEAD_MAX
    assert ce.parse_company_explainer(_block(lead=lead.strip()))["lead"]


def test_customers_heading_is_plain():
    assert dict(ce.SECTIONS)["customers"] == "Who their customers are"


def test_invalid_explainer_falls_back_like_a_missing_one():
    html = ce.explainer_section_html("```json\n{}\n```", theme=T)
    assert "not filled yet" in html


def test_style_blocks_are_single_line():
    html = ce.explainer_section_html(_block(), theme=T)
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
    html = ce.explainer_section_html(_block(sections=sections), theme=T)
    assert "G&amp;A ratio" in html and "&amp;amp;" not in html


def test_prompt_asks_for_plain_language_not_a_catalogue():
    assert "Never list product names" in ce.PROMPT
    assert "by name" not in ce.PROMPT
