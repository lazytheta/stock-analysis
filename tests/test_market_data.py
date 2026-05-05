"""Tests for Phase 2-B auto-fetch market data."""
from unittest.mock import MagicMock, patch


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
