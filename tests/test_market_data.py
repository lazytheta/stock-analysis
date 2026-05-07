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


def make_yf_history(months=48, base_price=100.0, growth_pct=0.10):
    """Build a yfinance Ticker.history(period='4y', interval='1mo')-like
    DataFrame with `months` rows of monthly Close prices growing at
    growth_pct per year (linear)."""
    import pandas as pd
    dates = pd.date_range(end="2026-05-01", periods=months, freq="ME")
    monthly_growth = (1 + growth_pct) ** (1 / 12) - 1
    closes = [base_price * (1 + monthly_growth) ** i for i in range(months)]
    return pd.DataFrame({"Close": closes}, index=dates)


def make_yf_income_stmt(eps_per_year=None, ebitda_per_year=None):
    """Build a yfinance Ticker.income_stmt-like DataFrame."""
    import pandas as pd
    if eps_per_year is None:
        eps_per_year = {2025: 8.0, 2024: 7.0, 2023: 6.0, 2022: 5.0}
    if ebitda_per_year is None:
        ebitda_per_year = {2025: 100e9, 2024: 90e9, 2023: 80e9, 2022: 70e9}
    columns = sorted(eps_per_year.keys(), reverse=True)
    cols = pd.DatetimeIndex([f"{y}-12-31" for y in columns])
    rows = {
        "Diluted EPS": [eps_per_year[y] for y in columns],
        "EBITDA": [ebitda_per_year.get(y) for y in columns],
    }
    return pd.DataFrame(rows, index=cols).T


def make_yf_quarterly_balance_sheet(debt_per_quarter=None, cash_per_quarter=None):
    """Build a yfinance Ticker.quarterly_balance_sheet-like DataFrame."""
    import pandas as pd
    if debt_per_quarter is None:
        debt_per_quarter = [50e9] * 16
    if cash_per_quarter is None:
        cash_per_quarter = [80e9] * 16
    cols = pd.date_range(end="2026-03-31", periods=len(debt_per_quarter), freq="QE")
    rows = {
        "Total Debt": debt_per_quarter,
        "Cash And Cash Equivalents": cash_per_quarter,
    }
    return pd.DataFrame(rows, index=cols).T


def patch_yfinance_full(info=None, history=None, income_stmt=None, qbs=None):
    """Comprehensive yfinance mock for fetch_historical_multiples."""
    fake_ticker = MagicMock()
    fake_ticker.info = info or {}
    fake_ticker.history = MagicMock(return_value=history if history is not None else make_yf_history())
    fake_ticker.income_stmt = income_stmt if income_stmt is not None else make_yf_income_stmt()
    fake_ticker.quarterly_balance_sheet = qbs if qbs is not None else make_yf_quarterly_balance_sheet()
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    return patch.dict("sys.modules", {"yfinance": fake_yf})


def test_scaffold_present():
    """Sanity: the test file is discovered and runs."""
    assert True


def test_yf_history_fixture_shape():
    df = make_yf_history(months=48)
    assert len(df) == 48
    assert "Close" in df.columns


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


def test_fetch_market_inputs_falls_back_to_ebitda_when_trailingEbitda_none():
    """Yfinance often returns trailingEbitda=None for large caps and puts
    the value in `ebitda` instead. The fetcher must read both."""
    info = make_yf_info(
        forwardEps=19.42,
        trailingEbitda=None,            # absent
        ebitda=184_457_003_008,         # populated (MSFT-like)
    )
    with patch_yfinance_info(info):
        result = gather_data.fetch_market_inputs("MSFT")
    assert result["forward_eps"] == 19.42
    assert result["ttm_ebitda"] == 184457.0


def test_enrich_peer_falls_back_to_ebitda():
    """Same fallback applies to the peer enricher."""
    peer = {"ticker": "MSFT", "ev_ebitda": 99.9}
    info = make_yf_info(
        forwardPE=22.0,
        enterpriseValue=3_103_113_347_072,
        trailingEbitda=None,
        ebitda=184_457_003_008,
    )
    with patch_yfinance_info(info):
        out = gather_data.enrich_peer_with_market_data(peer)
    # 3.103T / 184.5B = 16.823... → rounded
    assert out["ev_ebitda"] == pytest.approx(16.8, abs=0.05)


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


def test_auto_fill_peer_populates_empty():
    """All peer fields auto-filled, _auto_filled lists tracked per peer."""
    cfg = {
        "ticker": "ABT",
        "peers": [
            {"ticker": "AAPL", "name": "Apple", "ev_ebitda": 99.9, "pe": 33.5},
        ],
    }
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_peer_market_data(cfg)

    peer = cfg["peers"][0]
    assert peer["fwd_pe"] == 30.5
    assert peer["ev_ebitda"] == pytest.approx(24.1, rel=1e-3)
    assert set(peer["_auto_filled"]) == {"fwd_pe", "ev_ebitda"}
    assert "_fetched_at" in peer


def test_auto_fill_peer_respects_user_set_value():
    """User-set fwd_pe (not in _auto_filled) → preserved."""
    cfg = {
        "ticker": "ABT",
        "peers": [
            {"ticker": "AAPL", "fwd_pe": 28.0, "ev_ebitda": 99.9},  # no _auto_filled
        ],
    }
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_peer_market_data(cfg)

    peer = cfg["peers"][0]
    assert peer["fwd_pe"] == 28.0   # preserved
    assert peer["ev_ebitda"] == pytest.approx(24.1, rel=1e-3)  # was overwritten
    assert "ev_ebitda" in peer["_auto_filled"]
    assert "fwd_pe" not in peer["_auto_filled"]


def test_auto_fill_peer_skips_invalid_entries():
    """Non-dict and ticker-less peers are skipped without raising."""
    cfg = {
        "ticker": "ABT",
        "peers": [
            "not a dict",                       # garbage
            {"name": "no-ticker"},              # no ticker
            {"ticker": "AAPL", "ev_ebitda": 99.9},
        ],
    }
    info = make_yf_info(forwardPE=30.5, enterpriseValue=3_500_000_000_000,
                        trailingEbitda=145_000_000_000)
    with patch_yfinance_info(info):
        streamlit_app._auto_fill_peer_market_data(cfg)

    # only the valid peer is enriched
    assert cfg["peers"][0] == "not a dict"
    assert "fwd_pe" not in cfg["peers"][1]
    assert cfg["peers"][2]["fwd_pe"] == 30.5


def test_refresh_one_calls_auto_fill_before_orchestrator():
    """End-to-end: refresh on a stale ticker fills market data, then runs orchestrator."""
    cfg_in = {
        "ticker": "ABT",
        "company": "Abbott",
        "stock_price": 88.0,
        "equity_market_value": 152_000,
        "debt_market_value": 60_000,
        "risk_free_rate": 0.04,
        "erp": 0.05,
        "credit_spread": 0.01,
        "tax_rate": 0.21,
        "sector_betas": [("Healthcare", 0.9, 1.0)],
        "base_revenue": 41_000,
        "revenue_growth": [0.04] * 5,
        "op_margins": [0.20] * 5,
        "terminal_growth": 0.025,
        "terminal_margin": 0.18,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.02,
        "shares_outstanding": 1_750,
        "buyback_rate": 0.0,
        "margin_of_safety": 0.20,
        "cash_bridge": 8_000,
        "securities": 0,
        "bull_growth_adj": 0.02,
        "bear_growth_adj": -0.04,
        "bull_margin_adj": 0.02,
        "bear_margin_adj": -0.02,
        "peers": [
            {"ticker": "JNJ", "ev_ebitda": 99.9, "pe": 18.0,
             "op_margin": 0.25, "rev_growth": 0.03, "roic": 0.20},
        ],
    }
    storage = {"ABT": cfg_in}
    info = make_yf_info(
        forwardEps=5.48, trailingEbitda=11_800_000_000,
        forwardPE=22.0, enterpriseValue=420_000_000_000,
    )

    fake_save_called_with = []

    def fake_save(client, ticker, cfg, user_id=None):
        fake_save_called_with.append((ticker, dict(cfg)))

    with patch_yfinance_info(info), \
         patch.object(streamlit_app, "save_config", side_effect=fake_save):
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=storage, user_id="u1", force=True,
        )

    assert "ABT" in result["computed"]
    saved_ticker, saved_cfg = fake_save_called_with[0]
    assert saved_ticker == "ABT"
    # Auto-fill set valuation_inputs
    assert saved_cfg["valuation_inputs"]["forward_eps"] == 5.48
    assert saved_cfg["valuation_inputs"]["ttm_ebitda"] == 11800.0
    # Auto-fill enriched the peer
    peer = saved_cfg["peers"][0]
    assert peer["fwd_pe"] == 22.0
    # Orchestrator ran (summary present)
    assert "valuation_summary" in saved_cfg
    assert saved_cfg["valuation_summary"]["weighted_fv_mid"] > 0


def test_fetch_historical_multiples_happy_path():
    """All inputs available → returns three keys with reasonable values."""
    info = {"trailingEps": 8.5, "sharesOutstanding": 7.43e9}
    history = make_yf_history(months=48, base_price=200.0, growth_pct=0.05)
    income = make_yf_income_stmt(
        eps_per_year={2025: 8.0, 2024: 7.0, 2023: 6.0, 2022: 5.0},
        ebitda_per_year={2025: 100e9, 2024: 90e9, 2023: 80e9, 2022: 70e9},
    )
    qbs = make_yf_quarterly_balance_sheet()
    with patch_yfinance_full(info=info, history=history, income_stmt=income, qbs=qbs):
        result = gather_data.fetch_historical_multiples("MSFT")

    assert "historical_trailing_pe" in result
    assert result["historical_trailing_pe"] > 0
    assert result["historical_trailing_pe"] < 100  # sanity: P/E within reasonable range
    assert "historical_ev_ebitda" in result
    assert result["historical_ev_ebitda"] > 0
    assert result["ttm_eps"] == 8.5
