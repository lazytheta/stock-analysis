"""Tests for Phase 2-B auto-fetch market data."""
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

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


def test_fetch_market_inputs_yfinance_error():
    """yfinance.Ticker raises → fetcher returns {} (no crash, no propagation)."""
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(side_effect=Exception("network down"))
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}


def test_fetch_market_inputs_info_property_raises():
    """yf.Ticker(...).info access raises → fetcher returns {}."""
    fake_ticker = MagicMock()
    type(fake_ticker).info = PropertyMock(side_effect=RuntimeError("boom"))
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_market_inputs("XYZ")
    assert result == {}


def test_enrich_peer_happy_path():
    """fwd_pe is added; ev_ebitda is replaced with real (EV / trailingEbitda)."""
    peer = {"ticker": "AAPL", "name": "Apple", "ev_ebitda": 99.9, "pe": 33.5}
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        out = gather_data.enrich_peer_with_market_data(peer)

    assert out["fwd_pe"] == 30.5
    # 3.5T / 145B = 24.137... → round 1 decimal
    assert out["ev_ebitda"] == pytest.approx(24.1, rel=1e-3)
    # original dict not mutated
    assert peer["ev_ebitda"] == 99.9
    assert "fwd_pe" not in peer


def test_enrich_peer_no_ticker_returns_unchanged_copy():
    """Peer without ticker → returns copy unchanged, no yfinance call."""
    peer = {"name": "no-ticker", "ev_ebitda": 12.0}
    out = gather_data.enrich_peer_with_market_data(peer)
    assert out == peer
    assert out is not peer  # is a copy


def test_enrich_peer_only_fwd_pe_available():
    """Only forwardPE available → fwd_pe added, ev_ebitda left unchanged."""
    peer = {"ticker": "X", "ev_ebitda": 99.9}
    info = make_yf_info(forwardPE=22.0, enterpriseValue=None, trailingEbitda=None)
    with patch_yfinance_info(info):
        out = gather_data.enrich_peer_with_market_data(peer)
    assert out["fwd_pe"] == 22.0
    assert out["ev_ebitda"] == 99.9


def test_enrich_peer_yfinance_error_returns_unchanged():
    """yfinance raises → original peer fields preserved."""
    peer = {"ticker": "X", "ev_ebitda": 99.9, "pe": 20.0}
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(side_effect=Exception("boom"))
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        out = gather_data.enrich_peer_with_market_data(peer)
    assert out == peer
    assert "fwd_pe" not in out


def test_enrich_peer_zero_ev_skipped():
    """EV is 0 (anomaly) → don't compute a junk multiple."""
    peer = {"ticker": "X", "ev_ebitda": 99.9}
    info = make_yf_info(forwardPE=22.0, enterpriseValue=0, trailingEbitda=10_000_000_000)
    with patch_yfinance_info(info):
        out = gather_data.enrich_peer_with_market_data(peer)
    assert out["fwd_pe"] == 22.0
    assert out["ev_ebitda"] == 99.9  # unchanged


import streamlit_app


def test_auto_fill_inputs_populates_empty():
    """Empty valuation_inputs → both keys filled, both in _auto_filled."""
    cfg = {"ticker": "ABT", "valuation_inputs": {}}
    info = make_yf_info(forwardEps=5.48, trailingEbitda=11_800_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_valuation_inputs(cfg)

    inputs = cfg["valuation_inputs"]
    assert inputs["forward_eps"] == 5.48
    assert inputs["ttm_ebitda"] == 11800.0
    assert set(inputs["_auto_filled"]) == {"forward_eps", "ttm_ebitda"}
    assert "_fetched_at" in inputs


def test_auto_fill_inputs_respects_user_set_value():
    """forward_eps set by user (not in _auto_filled) → not overwritten."""
    cfg = {
        "ticker": "ABT",
        "valuation_inputs": {"forward_eps": 5.48},  # no _auto_filled key
    }
    info = make_yf_info(forwardEps=5.50, trailingEbitda=11_800_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_valuation_inputs(cfg)

    inputs = cfg["valuation_inputs"]
    assert inputs["forward_eps"] == 5.48                # preserved
    assert inputs["ttm_ebitda"] == 11800.0              # newly filled
    assert "ttm_ebitda" in inputs["_auto_filled"]
    assert "forward_eps" not in inputs["_auto_filled"]  # user value, not auto


def test_auto_fill_inputs_overwrites_previous_auto_value():
    """forward_eps in _auto_filled list → overwritten with new yfinance value."""
    cfg = {
        "ticker": "ABT",
        "valuation_inputs": {
            "forward_eps": 5.40,
            "_auto_filled": ["forward_eps"],
        },
    }
    info = make_yf_info(forwardEps=5.55)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_valuation_inputs(cfg)

    assert cfg["valuation_inputs"]["forward_eps"] == 5.55


def test_auto_fill_inputs_doesnt_overwrite_with_none():
    """Existing auto-filled value preserved when yfinance returns None."""
    cfg = {
        "ticker": "ABT",
        "valuation_inputs": {
            "forward_eps": 5.48,
            "_auto_filled": ["forward_eps"],
        },
    }
    # yfinance returns nothing (e.g. error path or empty info)
    with patch_yfinance_info({}):
        streamlit_app._auto_fill_valuation_inputs(cfg)
    assert cfg["valuation_inputs"]["forward_eps"] == 5.48


def test_auto_fill_inputs_fetched_at_always_set():
    """_fetched_at is updated even when no fields wrote."""
    cfg = {"ticker": "ABT", "valuation_inputs": {}}
    with patch_yfinance_info({}):
        streamlit_app._auto_fill_valuation_inputs(cfg)
    assert "_fetched_at" in cfg["valuation_inputs"]
