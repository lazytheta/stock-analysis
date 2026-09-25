from unittest.mock import patch

import pytest

import aspirant


def _patch_edgar(gd, price=0.0):
    fin = {"years": [2021, 2022, 2023, 2024, 2025], "shares": [100.0] * 5,
           "operating_income": [50.0] * 5, "interest_expense_latest": 1.0}
    return [
        patch.object(gd, "get_cik", return_value="0000000001"),
        patch.object(gd, "fetch_company_submissions",
                     return_value={"name": "Test Corp", "sic": "7372",
                                   "sicDescription": "Software"}),
        patch.object(gd, "resolve_sector_betas", return_value=[("Software", 1.1, 1.0)]),
        patch.object(gd, "fetch_company_facts", return_value={}),
        patch.object(gd, "parse_financials", return_value=fin),
        patch.object(gd, "fetch_stock_price", return_value=(price, 0, 0)),
        patch.object(gd, "synthetic_credit_rating", return_value=("AA", 0.01)),
        patch.object(gd, "build_config", side_effect=lambda **kw: {
            "stock_price": kw["stock_price"], "base_revenue": 10.0,
            "base_year": 2025, "company": kw["company_name"]}),
        patch.object(gd, "fetch_fundamentals", return_value={}),
    ]


def test_build_base_config_uses_the_given_price_and_skips_yahoo():
    import contextlib
    import gather_data as gd
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _patch_edgar(gd, price=0.0)]
        cfg = gd.build_base_config("TST", stock_price=42.0)
    assert cfg["stock_price"] == 42.0
    yahoo = mocks[5]
    yahoo.assert_not_called()


def test_build_base_config_without_any_price_says_so():
    import contextlib
    import gather_data as gd
    with contextlib.ExitStack() as stack:
        for p in _patch_edgar(gd, price=0.0):
            stack.enter_context(p)
        with pytest.raises(ValueError, match="stock_price"):
            gd.build_base_config("TST")


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


def test_list_watchlist_carries_category_and_markers():
    from unittest.mock import MagicMock
    from config_store import list_watchlist
    client = MagicMock()
    resp = MagicMock()
    resp.data = [{"ticker": "ABC", "company": "Abc", "stock_price": 1, "updated_at": "",
                  "category": "Aspirant", "dcf_placeholder": True,
                  "promoted_by": None, "promoted_at": None},
                 {"ticker": "OLD", "company": "Old", "stock_price": 1, "updated_at": ""}]
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = resp
    out = {e["ticker"]: e for e in list_watchlist(client, user_id="u")}
    assert out["ABC"]["category"] == "Aspirant" and out["ABC"]["dcf_placeholder"] is True
    assert out["OLD"]["category"] == "Uncategorized" and out["OLD"]["dcf_placeholder"] is False
