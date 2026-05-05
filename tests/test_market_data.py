"""Tests for Phase 2-B auto-fetch market data."""
from unittest.mock import MagicMock, patch

import gather_data


def make_yf_info(**overrides):
    """Build a yfinance Ticker.info-like dict with sensible defaults.

    Pass kwargs to override or set fields, e.g. make_yf_info(forwardEps=5.48).
    Pass None as value to simulate field absence.
    """
    info = {
        "forwardEps": 5.48,
        "trailingEbitda": 11_800_000_000,
        "forwardPE": 21.0,
        "enterpriseValue": 200_000_000_000,
    }
    for k, v in overrides.items():
        if v is None:
            info.pop(k, None)
        else:
            info[k] = v
    return info


def patch_yfinance_info(info_dict):
    """Returns a context manager that mocks yfinance.Ticker(...).info."""
    fake_ticker = MagicMock()
    fake_ticker.info = info_dict
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    return patch.dict("sys.modules", {"yfinance": fake_yf})


def test_scaffold_present():
    """Sanity: the test file is discovered and runs."""
    assert True


def test_fetch_market_inputs_happy_path():
    """Both fields populated; output uses $M for ttm_ebitda."""
    info = make_yf_info(forwardEps=5.48, trailingEbitda=11_800_000_000)
    with patch_yfinance_info(info):
        result = gather_data.fetch_market_inputs("ABT")
    assert result == {"forward_eps": 5.48, "ttm_ebitda": 11800.0}


def test_fetch_market_inputs_missing_fields():
    """Empty info → empty result, no crash."""
    with patch_yfinance_info({}):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}


def test_fetch_market_inputs_partial():
    """Only forwardEps available → only forward_eps in result."""
    info = make_yf_info(trailingEbitda=None)  # drop trailingEbitda
    with patch_yfinance_info(info):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {"forward_eps": 5.48}


def test_fetch_market_inputs_zero_or_negative_skipped():
    """Zero/negative values are not real data — skip them."""
    info = make_yf_info(forwardEps=0, trailingEbitda=-100)
    with patch_yfinance_info(info):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}
