"""Unit tests for scorecard_utils display helpers."""

import pytest

from scorecard_utils import prettify_company_name


@pytest.mark.parametrize("raw,expected", [
    # EDGAR all-caps issuer names get title-cased
    ("TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "Taiwan Semiconductor Manufacturing Co Ltd"),
    ("ABBOTT LABORATORIES", "Abbott Laboratories"),
    ("ADOBE INC.", "Adobe Inc."),
    ("COMCAST CORP", "Comcast Corp"),
    ("HOME DEPOT, INC.", "Home Depot, Inc."),
    ("PROCTER & GAMBLE Co", "Procter & Gamble Co"),
    ("BANK OF AMERICA CORP", "Bank of America Corp"),  # connector lowercased
    # Brand acronyms / internal capitals preserved
    ("NVIDIA CORP", "NVIDIA Corp"),
    ("AT&T INC.", "AT&T Inc."),
    ("MERCADOLIBRE INC", "MercadoLibre Inc"),
    # Already nicely-cased names are left untouched
    ("AbbVie Inc.", "AbbVie Inc."),
    ("Amazon.com Inc", "Amazon.com Inc"),
    ("McDonald's Corporation", "McDonald's Corporation"),
    ("Eli Lilly and Company", "Eli Lilly and Company"),
    ("PepsiCo Inc", "PepsiCo Inc"),
])
def test_prettify_company_name(raw, expected):
    assert prettify_company_name(raw) == expected


@pytest.mark.parametrize("val", [None, "", "   ", 123, {"x": 1}])
def test_prettify_company_name_handles_non_strings(val):
    # Should never raise; returns the input unchanged for non-text values.
    assert prettify_company_name(val) == val


from scorecard_utils import compute_roce_metric


def _fund(oi, ta, cl, ni=None, eq=None):
    n = len(oi)
    return {
        "years": list(range(2016, 2016 + n)),
        "operating_income": oi, "total_assets": ta, "current_liabilities": cl,
        "net_income": ni or [None] * n, "total_equity": eq or [None] * n,
    }


def test_roce_uses_ta_minus_cl_no_goodwill_subtraction():
    # CE = TA − CL only; goodwill/cash irrelevant. EBIT 20 / (100−20) = 25%.
    metric, val = compute_roce_metric(_fund([20.0], [100.0], [20.0]))
    assert metric == "ROCE"
    assert round(val, 1) == 25.0


def test_genuine_float_auto_falls_back_to_roe():
    # Current liabilities eat 80% of assets → CE/TA 0.20 < 0.25 → ROE.
    f = _fund([10.0], [100.0], [80.0], ni=[15.0], eq=[50.0])
    metric, val = compute_roce_metric(f)
    assert metric == "ROE"
    assert round(val, 1) == 30.0  # 15 / 50


def test_acquisition_heavy_stays_roce():
    # Big asset base, modest CL → CE/TA 0.70 ≥ 0.25 → ROCE (no goodwill drag).
    metric, _ = compute_roce_metric(_fund([12.0], [100.0], [30.0]))
    assert metric == "ROCE"


def test_manual_override_forces_roe_on_non_float():
    # Auto would say ROCE, but the float flag forces ROE.
    f = _fund([12.0], [100.0], [30.0], ni=[8.0], eq=[40.0])
    metric, val = compute_roce_metric(f, {"roce_metric_override": "ROE"})
    assert metric == "ROE"
    assert round(val, 1) == 20.0  # 8 / 40


def test_manual_override_forces_roce_on_float():
    # Auto would say ROE (float), but override pins ROCE.
    f = _fund([10.0], [100.0], [80.0], ni=[15.0], eq=[50.0])
    metric, val = compute_roce_metric(f, {"roce_metric_override": "ROCE"})
    assert metric == "ROCE"
    assert round(val, 1) == 50.0  # 10 / (100−80)
