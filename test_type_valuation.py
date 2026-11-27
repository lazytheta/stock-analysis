"""Buy price per company type — the worked examples from the Notion page."""

import pytest

import type_valuation as tv


def test_visa_example_from_notion():
    # K/W 34 at $409 → EPS ≈ 12.03; 11% − 2pp = 9% = hurdle, so the 1.09s cancel
    bp = tv.growth_buy_price(12.03, 0.11, 25.0)
    assert bp == pytest.approx(12.03 * 25, rel=1e-6)


def test_required_growth_visa():
    assert tv.required_growth(34, 25) == pytest.approx(0.159, abs=0.001)


def test_required_growth_table_row():
    # Notion table: P/E 30 → end 20 needs ~18%
    assert tv.required_growth(30, 20) == pytest.approx(0.18, abs=0.005)


def test_dividend_raises_buy_price():
    assert tv.growth_buy_price(10, 0.08, 20, dps=3) > tv.growth_buy_price(10, 0.08, 20)


def test_low_growth_needs_rerating_or_yield():
    # g − b below the hurdle: buy only below the end multiple
    assert tv.growth_buy_price(10, 0.06, 20) < 10 * 20


def test_end_pe_takes_lower_of_median_and_cap():
    assert tv.end_pe([30, 32, 28, 35])[0] == 25.0
    assert tv.end_pe([15, 18, 20]) == (18, 18)
    assert tv.end_pe([]) == (25.0, None)


def test_pe_history_skips_loss_years():
    assert tv.pe_history({2020: -1, 2021: 2, 2022: 4}, {2020: 50, 2021: 40, 2022: 80}) == [20, 20]


def test_justified_pb_notion_example():
    assert tv.justified_pb(0.15, 0.03) == pytest.approx(2.0)
    assert tv.justified_pb(0.05, 0.03) == pytest.approx(0.333, abs=0.001)
    assert tv.justified_pb(0.15, 0.09) is None


def test_capital_light_full():
    r = tv.evaluate({"type": "capital_light", "achievable_growth": 0.11}, 409,
                    {"eps": 12.03, "pe_history": [30] * 10}, implied_return=0.072)
    assert r["buy_price"] == pytest.approx(300.75, rel=1e-4)
    assert r["distance"] == pytest.approx(409 / 300.75 - 1, rel=1e-4)
    assert r["control"]["agrees"] is True       # both say: not now
    assert r["label"] == "Capital-light"


def test_missing_growth_gives_no_price_and_a_reason():
    r = tv.evaluate({"type": "capital_light"}, 409, {"eps": 12.03})
    assert r["buy_price"] is None
    assert r["missing"] == ["Achievable growth"]


def test_no_type():
    r = tv.evaluate(None, 100, {})
    assert r["buy_price"] is None and r["note"] == "Type not set"


def test_cyclical_normalises_over_a_full_cycle():
    rev = [100] * 10
    ni = [5, 15, 10, 10, 10, 10, 10, 10, 10, 10]     # avg 10%
    r = tv.evaluate({"type": "cyclical"}, 50,
                    {"revenue": rev, "net_income": ni, "shares": [10] * 10})
    # rev/share 10 × 10% × 11
    assert r["buy_price"] == pytest.approx(11.0)
    assert r["main"]["window_years"] == 10


def test_cyclical_short_history_is_missing_not_guessed():
    r = tv.evaluate({"type": "cyclical"}, 50,
                    {"revenue": [100] * 6, "net_income": [10] * 6, "shares": [10] * 6})
    assert r["buy_price"] is None and r["missing"]


def test_cyclical_authored_margin_overrides():
    r = tv.evaluate({"type": "cyclical", "normalised_margin": 0.2}, 50,
                    {"revenue": [100] * 3, "net_income": [10] * 3, "shares": [10] * 3})
    assert r["buy_price"] == pytest.approx(10 * 0.2 * 11)


def test_capital_heavy_uses_dcf_with_buffer():
    seen = {}

    def dcf(shift):
        seen["shift"] = shift
        return 280.0

    r = tv.evaluate({"type": "capital_heavy", "achievable_growth": 0.12}, 364,
                    {"eps": 14.0, "pe_history": [20] * 10}, dcf_value=dcf)
    assert r["buy_price"] == 280.0 and seen["shift"] == -tv.BUFFER
    assert r["control"]["agrees"] is not None


def test_financial():
    r = tv.evaluate({"type": "financial", "roe": 0.15, "book_growth": 0.03}, 50,
                    {"tangible_book_ps": 30, "pb_history": [1.5] * 10})
    assert r["buy_price"] == pytest.approx(30 * 2.0 * 0.8)


def test_asset():
    r = tv.evaluate({"type": "asset", "asset_value_per_share": 200}, 256, {})
    assert r["buy_price"] == pytest.approx(160)


def test_eps_override_for_one_offs():
    r = tv.evaluate({"type": "capital_light", "achievable_growth": 0.11,
                     "eps_override": 15.0}, 480, {"eps": 18.0, "pe_history": [30] * 10})
    assert r["main"]["eps"] == 15.0 and r["main"]["eps_overridden"]
    assert r["buy_price"] == pytest.approx(15 * 25, rel=1e-4)


def test_pe_end_override_replaces_the_rule():
    r = tv.evaluate({"type": "capital_light", "achievable_growth": 0.11,
                     "pe_end_override": 18}, 480, {"eps": 15.0, "pe_history": [30] * 10})
    assert r["main"]["pe_end"] == 18 and r["main"]["pe_end_overridden"]
    assert r["main"]["pe_median"] == 30
    assert r["buy_price"] == pytest.approx(15 * 18, rel=1e-4)


def test_facts_scale_adr_and_missing_shares():
    cfg = {"hist_net_income": [100.0], "shares_outstanding": 10.0}    # $M, M shares
    fund = {"years": [2024, 2025], "net_income": [80.0, 100.0],
            "shares": [None, None], "revenue": [400.0, 500.0]}
    f = tv.facts_from(cfg, fund, [("2024-12-31", 160.0), ("2025-12-31", 200.0)], 200)
    assert f["eps"] == pytest.approx(10.0)
    assert f["pe_history"] == pytest.approx([20.0, 20.0])
    assert f["revenue_ps"] == pytest.approx(50.0)


def test_share_gaps_are_filled_so_the_latest_eps_is_latest():
    # RACE: EDGAR stops tagging the share count after 2020
    cfg = {"hist_net_income": [1600.0], "shares_outstanding": 180.0}
    fund = {"years": [2020, 2025], "net_income": [600.0, 1600.0],
            "shares": [185e6, None]}
    f = tv.facts_from(cfg, fund, [], 400)
    assert f["eps"] == pytest.approx(1600 / 180, rel=0.03)
