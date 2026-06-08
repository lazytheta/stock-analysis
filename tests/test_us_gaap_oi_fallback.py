"""parse_financials OI fallback: derive operating_income from
pretax_income - NonoperatingIncomeExpense when OperatingIncomeLoss is
missing from the XBRL filing.

Real-world case: large pharma (LLY FY2021-2025) does not tag
OperatingIncomeLoss; only NonoperatingIncomeExpense + pretax. Before this
fallback that broke the robustness gate's ROCE axis.
"""
import gather_data as g


def _annual(*pairs):
    """Build a us-gaap fact { units: { USD: [...] } } from (year, value) pairs.
    Each pair becomes a 10-K duration entry for that fiscal year."""
    return {"units": {"USD": [
        {"form": "10-K", "start": f"{y}-01-01", "end": f"{y}-12-31",
         "fy": y, "fp": "FY", "val": v}
        for y, v in pairs
    ]}}


def _facts(**concepts):
    """Wrap a flat {ConceptName: annual(...)} dict in the EDGAR facts shape."""
    return {"facts": {"us-gaap": concepts}}


# A consistent set of years to use across tests
_YEARS = (2021, 2022, 2023, 2024, 2025)


def _common_revenue():
    return _annual(*[(y, (28_000 + i * 5_000) * 1_000_000)
                     for i, y in enumerate(_YEARS)])


def test_oi_directly_tagged_used_verbatim():
    """When OperatingIncomeLoss is present, parse_financials uses it as-is
    and does NOT touch the derivation path."""
    facts = _facts(
        Revenues=_common_revenue(),
        OperatingIncomeLoss=_annual(
            (2021, 6_357_000_000), (2022, 7_127_000_000),
            (2023, 6_457_000_000), (2024, 12_899_000_000),
            (2025, 26_302_000_000),
        ),
        # Pretax + nonop also present, but should be ignored for OI
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=_annual(
            (2025, 25_731_000_000),
        ),
        NonoperatingIncomeExpense=_annual((2025, -571_000_000)),
    )
    res = g.parse_financials(facts, n_years=5)
    i = res["years"].index(2025)
    assert res["operating_income"][i] == 26_302  # verbatim, not 25731 - (-571) = 26302 (same in this case)
    i21 = res["years"].index(2021)
    assert res["operating_income"][i21] == 6_357


def test_oi_derived_when_loss_tag_missing():
    """LLY-style case: no OperatingIncomeLoss, derive from pretax - nonop."""
    facts = _facts(
        Revenues=_common_revenue(),
        # No OperatingIncomeLoss
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=_annual(
            (2021, 6_156_000_000), (2022, 6_806_000_000),
            (2023, 6_555_000_000), (2024, 12_680_000_000),
            (2025, 25_731_000_000),
        ),
        NonoperatingIncomeExpense=_annual(
            (2021, -201_000_000), (2022, -321_000_000),
            (2023, 98_000_000),  (2024, -219_000_000),
            (2025, -571_000_000),
        ),
    )
    res = g.parse_financials(facts, n_years=5)
    i = res["years"].index(2025)
    # OI = pretax - nonop = 25731 - (-571) = 26302
    assert res["operating_income"][i] == 26_302
    i23 = res["years"].index(2023)
    # OI = pretax - nonop = 6555 - 98 = 6457
    assert res["operating_income"][i23] == 6_457


def test_oi_falls_back_to_other_nonoperating_income_expense():
    """When NonoperatingIncomeExpense is missing, the alt tag
    OtherNonoperatingIncomeExpense is used."""
    facts = _facts(
        Revenues=_common_revenue(),
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=_annual(
            (2025, 10_000_000_000),
        ),
        OtherNonoperatingIncomeExpense=_annual((2025, -500_000_000)),
    )
    res = g.parse_financials(facts, n_years=5)
    i = res["years"].index(2025)
    assert res["operating_income"][i] == 10_500


def test_oi_partial_year_gap_fills_only_missing_years():
    """Some years tagged with OperatingIncomeLoss, some not. Derivation only
    fills the missing years; tagged years remain untouched."""
    facts = _facts(
        Revenues=_common_revenue(),
        OperatingIncomeLoss=_annual(
            # 2021-2023 tagged, 2024-2025 missing
            (2021, 6_357_000_000), (2022, 7_127_000_000), (2023, 6_457_000_000),
        ),
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=_annual(
            (2024, 12_680_000_000), (2025, 25_731_000_000),
        ),
        NonoperatingIncomeExpense=_annual(
            (2024, -219_000_000), (2025, -571_000_000),
        ),
    )
    res = g.parse_financials(facts, n_years=5)
    # Tagged years untouched
    assert res["operating_income"][res["years"].index(2021)] == 6_357
    assert res["operating_income"][res["years"].index(2023)] == 6_457
    # Derived years filled
    assert res["operating_income"][res["years"].index(2024)] == 12_899
    assert res["operating_income"][res["years"].index(2025)] == 26_302


def test_oi_stays_none_when_pretax_or_nonop_also_missing():
    """No regression guard: when neither OI nor the derivation inputs are
    present, OI stays None (rather than e.g. crashing or fabricating)."""
    facts = _facts(
        Revenues=_common_revenue(),
        # No OperatingIncomeLoss
        # Pretax only for 2024 — nothing else
        IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest=_annual(
            (2024, 12_680_000_000),
        ),
        # No NonoperatingIncomeExpense at all
    )
    res = g.parse_financials(facts, n_years=5)
    # All OI values None — pretax exists for 2024 but no nonop counterpart
    assert all(v is None for v in res["operating_income"])
