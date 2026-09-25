import pytest

import aspirant


def _cfg(**over):
    cfg = {
        "category": "Aspirant",
        "dcf_placeholder": False,
        "equity_market_value": 1000.0,
        "sector_betas": [["Software", 1.1, 1.0]],
        "revenue_growth": [0.12, 0.10, 0.08, 0.06, 0.04],
        "op_margins": [0.30, 0.31, 0.32, 0.32, 0.32],
        "valuation_summary": {"weighted_fv_mid": 100.0},
        "ai_notes": {"Moat": "**Moat: Wide 🛡️ · Stable ➡️ · 4/5**\n\nText."},
    }
    cfg.update(over)
    return cfg


def test_categories_include_aspirant_and_the_existing_five():
    assert set(aspirant.CATEGORIES) == {
        "Uncategorized", "Yes", "Maybe", "Watch Later", "No", "Aspirant"}


def test_flat_curves_are_the_build_config_placeholders():
    assert aspirant.curves_are_flat({"revenue_growth": [0.025] * 5, "op_margins": [0.2] * 5})
    assert not aspirant.curves_are_flat({"revenue_growth": [0.1, 0.08], "op_margins": [0.2, 0.2]})
    assert not aspirant.curves_are_flat({"revenue_growth": [0.03, 0.03], "op_margins": [0.2, 0.25]})
    assert aspirant.curves_are_flat({})  # geen curves = niets ingevuld


def test_moat_label_reads_the_verdict_line():
    assert aspirant.moat_label({"Moat": "**Moat: Wide 🛡️ · Stable ➡️ · 4/5**\n\nx"}) == "Wide"
    assert aspirant.moat_label({"Moat": "**Moat: Narrow · Eroding · 2/5**\n\nx"}) == "Narrow"
    assert aspirant.moat_label({"Moat": "Some old free-form report"}) is None
    assert aspirant.moat_label(None) is None


def test_a_complete_wide_aspirant_has_no_blockers():
    assert aspirant.promotion_blockers(_cfg()) == []


@pytest.mark.parametrize("over, fragment", [
    ({"category": "Maybe"}, "not an Aspirant"),
    ({"ai_notes": {"Moat": "**Moat: Narrow · Stable · 3/5**"}}, "Wide"),
    ({"ai_notes": {}}, "Wide"),
    ({"dcf_placeholder": True}, "placeholder"),
    ({"equity_market_value": 0}, "equity_market_value"),
    ({"sector_betas": [["A", 1.1, 1.1]]}, "sector_betas"),
    ({"valuation_summary": None}, "valuation_summary"),
])
def test_each_missing_piece_blocks_promotion(over, fragment):
    blockers = aspirant.promotion_blockers(_cfg(**over))
    assert any(fragment in b for b in blockers), blockers
