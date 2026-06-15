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
