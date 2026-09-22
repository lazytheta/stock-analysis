"""Ferrari rapporteert alleen in euro's; de IFRS-vulling zocht dollars."""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import gather_data
import streamlit_app


def _facts(units):
    return {"facts": {"ifrs-full": {"Revenue": {"units": {u: [] for u in units}}}}}


def test_usd_wins_when_the_filer_offers_a_translation():
    assert gather_data._ifrs_unit(_facts(["EUR", "USD"])) == "USD"


def test_a_euro_only_filer_reports_in_euros():
    assert gather_data._ifrs_unit(_facts(["EUR"])) == "EUR"


def test_no_ifrs_revenue_means_dollars():
    assert gather_data._ifrs_unit({"facts": {}}) == "USD"


def test_fcf_yield_converts_a_euro_fund_into_the_dollar_price():
    fund = {"years": [2025], "fcf": [1000.0], "shares": [100_000_000],
            "currency": "EUR"}
    with patch.object(gather_data, "fetch_fx_rate", return_value=1.15):
        # 1000 mln EUR x 1.15 = 1150 mln USD / 100 mln aandelen = 11.5 USD per
        # aandeel, tegen een koers van 230 USD: 5%
        assert streamlit_app._latest_fcf_yield(fund, None, 230.0) == pytest.approx(0.05)


def test_fcf_yield_refuses_to_guess_without_a_rate():
    fund = {"years": [2025], "fcf": [1000.0], "shares": [100_000_000],
            "currency": "EUR"}
    with patch.object(gather_data, "fetch_fx_rate", return_value=None):
        assert streamlit_app._latest_fcf_yield(fund, None, 230.0) is None
