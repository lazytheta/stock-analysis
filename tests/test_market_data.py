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
    dates = pd.date_range(end="2026-04-30", periods=months, freq="ME")
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


def test_fetch_historical_multiples_negative_eps_quarter_skipped():
    """A loss year doesn't crash the median; negative-eps months are excluded."""
    info = {"trailingEps": 8.0}
    income = make_yf_income_stmt(
        eps_per_year={2025: 8.0, 2024: 7.0, 2023: -1.0, 2022: 5.0},  # 2023 was a loss
    )
    with patch_yfinance_full(info=info, income_stmt=income):
        result = gather_data.fetch_historical_multiples("XYZ")

    assert "historical_trailing_pe" in result
    assert result["historical_trailing_pe"] > 0  # negative-eps months excluded; rest still positive


def test_fetch_historical_multiples_insufficient_history():
    """Too few months → returns only ttm_eps; historical metrics absent."""
    info = {"trailingEps": 5.0}
    short_history = make_yf_history(months=6)
    with patch_yfinance_full(info=info, history=short_history):
        result = gather_data.fetch_historical_multiples("RECENT_IPO")
    # ttm_eps still populated (info has it), but historical metrics absent
    assert "historical_trailing_pe" not in result
    assert "historical_ev_ebitda" not in result
    assert result.get("ttm_eps") == 5.0


def test_fetch_historical_multiples_yfinance_error():
    """yf.Ticker(...) raises → returns empty dict, no crash."""
    fake_yf = MagicMock()
    fake_yf.Ticker = MagicMock(side_effect=Exception("network down"))
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_historical_multiples("XYZ")
    assert result == {}


def test_fetch_historical_multiples_missing_ebitda():
    """No EBITDA data → trailing-PE still computed, EV/EBITDA absent."""
    info = {"trailingEps": 5.0, "sharesOutstanding": 1e9}
    income = make_yf_income_stmt(
        eps_per_year={2025: 5.0, 2024: 4.5, 2023: 4.0, 2022: 3.5},
        ebitda_per_year={2025: None, 2024: None, 2023: None, 2022: None},
    )
    with patch_yfinance_full(info=info, income_stmt=income):
        result = gather_data.fetch_historical_multiples("XYZ")
    assert "historical_trailing_pe" in result
    assert "historical_ev_ebitda" not in result


def test_fetch_historical_multiples_no_shares_outstanding():
    """Missing sharesOutstanding → EV cannot be computed → ev_ebitda absent
    from monthly path; snapshot fallback only fires when enterpriseValue is
    also present."""
    info = {"trailingEps": 5.0}  # no sharesOutstanding, no enterpriseValue
    with patch_yfinance_full(info=info):
        result = gather_data.fetch_historical_multiples("XYZ")
    assert "historical_trailing_pe" in result
    assert "historical_ev_ebitda" not in result


def test_fetch_historical_multiples_snapshot_fallback_on_short_history():
    """Phase 2-B.2: when monthly interpolation skips (history < 24 months) but
    yfinance info has trailingPE / enterpriseValue, fall back to single-point
    snapshot values and flag them in _low_confidence."""
    info = {
        "trailingEps": 5.0,
        "trailingPE": 30.5,
        "enterpriseValue": 100_000_000_000,
        "trailingEbitda": 4_000_000_000,
    }
    short_history = make_yf_history(months=6)
    with patch_yfinance_full(info=info, history=short_history):
        result = gather_data.fetch_historical_multiples("RECENT_IPO")

    assert result["historical_trailing_pe"] == 30.5
    assert result["historical_ev_ebitda"] == 25.0  # 100B / 4B
    assert set(result.get("_low_confidence", [])) == {
        "historical_trailing_pe", "historical_ev_ebitda"
    }


def test_fetch_historical_multiples_snapshot_partial_fill():
    """Snapshot fallback only fills the fields the monthly path missed —
    fields the monthly path computed stay as-is (no _low_confidence flag)."""
    info = {
        "trailingEps": 5.0,
        "sharesOutstanding": 1e9,
        "trailingPE": 99.9,  # snapshot value; should NOT override the monthly median
    }
    # Full happy-path fixtures so monthly historical_trailing_pe is computed
    with patch_yfinance_full(info=info):
        result = gather_data.fetch_historical_multiples("XYZ")

    assert "historical_trailing_pe" in result
    assert result["historical_trailing_pe"] != 99.9  # monthly median wins
    assert "historical_trailing_pe" not in result.get("_low_confidence", [])


def test_fetch_historical_forward_pe_scales_with_current_ratio():
    """Phase 2-B.3: historical_fwd_pe = historical_trailing_pe ×
    (current forwardPE / current trailingPE)."""
    info = {"forwardPE": 30.0, "trailingPE": 50.0}  # ratio 0.6
    with patch_yfinance_info(info):
        result = gather_data.fetch_historical_forward_pe(
            "XYZ", historical_trailing_pe=40.0,
        )
    assert result == {"historical_fwd_pe": 24.0}  # 40 × 0.6


def test_fetch_historical_forward_pe_missing_trailing_pe_returns_empty():
    """No historical_trailing_pe input → can't scale → empty dict."""
    info = {"forwardPE": 30.0, "trailingPE": 50.0}
    with patch_yfinance_info(info):
        result = gather_data.fetch_historical_forward_pe(
            "XYZ", historical_trailing_pe=None,
        )
    assert result == {}


def test_fetch_historical_forward_pe_missing_yfinance_ratio_returns_empty():
    """yfinance lacks forwardPE or trailingPE → empty dict (don't fabricate)."""
    info = {"trailingPE": 50.0}  # no forwardPE
    with patch_yfinance_info(info):
        result = gather_data.fetch_historical_forward_pe(
            "XYZ", historical_trailing_pe=40.0,
        )
    assert result == {}


def test_fetch_historical_forward_pe_yfinance_error_returns_empty():
    """yfinance throws → empty dict, no crash."""
    from unittest.mock import MagicMock as _Mock
    fake_yf = _Mock()
    fake_yf.Ticker = _Mock(side_effect=Exception("boom"))
    with patch.dict("sys.modules", {"yfinance": fake_yf}):
        result = gather_data.fetch_historical_forward_pe(
            "XYZ", historical_trailing_pe=40.0,
        )
    assert result == {}


def test_refresh_all_valuations_force_true_processes_everything():
    """force=True processes every ticker including fresh ones."""
    import json as _json
    import mcp_server
    from unittest.mock import patch as _patch, MagicMock as _Mock
    from datetime import datetime as _dt, UTC as _UTC

    fresh_ts = _dt.now(_UTC).isoformat()
    cfgs = {
        "AAPL": {
            "ticker": "AAPL", "company": "Apple", "stock_price": 100.0,
            "equity_market_value": 100_000, "debt_market_value": 10_000,
            "risk_free_rate": 0.04, "erp": 0.05, "credit_spread": 0.01,
            "tax_rate": 0.21, "sector_betas": [("Tech", 1.1, 1.0)],
            "base_revenue": 50_000, "revenue_growth": [0.05] * 5,
            "op_margins": [0.20] * 5, "terminal_growth": 0.025,
            "terminal_margin": 0.18, "sales_to_capital": 1.5, "sbc_pct": 0.02,
            "shares_outstanding": 1_000,
            "margin_of_safety": 0.20, "cash_bridge": 5_000, "securities": 0,
            "bull_growth_adj": 0.02, "bear_growth_adj": -0.04,
            "bull_margin_adj": 0.02, "bear_margin_adj": -0.02,
            "peers": [],
            "valuation_summary": {"calculated_at": fresh_ts, "weighted_fv_mid": 99.0},
        },
    }

    def fake_load(client, ticker, user_id=None):
        return dict(cfgs[ticker])

    def fake_save(client, ticker, cfg, user_id=None):
        cfgs[ticker] = dict(cfg)

    def fake_list(client, user_id=None):
        return [{"ticker": t} for t in cfgs]

    with _patch.object(mcp_server, "get_supabase_client", lambda: _Mock()), \
         _patch.object(mcp_server.config_store, "load_config", side_effect=fake_load), \
         _patch.object(mcp_server.config_store, "save_config", side_effect=fake_save), \
         _patch.object(mcp_server.config_store, "list_watchlist", side_effect=fake_list), \
         _patch.object(mcp_server, "USER_ID", "u1"), \
         patch_yfinance_full(info={"trailingEps": 5.0, "sharesOutstanding": 1_000_000_000}):
        result = _json.loads(mcp_server._refresh_all_valuations_impl(force=True))

    assert result["computed"] == ["AAPL"]
    assert result["skipped"] == []
    assert result["errors"] == []


def test_refresh_all_valuations_default_skips_fresh():
    """force=False (default) skips tickers whose summary is < 7 days old."""
    import json as _json
    import mcp_server
    from unittest.mock import patch as _patch, MagicMock as _Mock
    from datetime import datetime as _dt, UTC as _UTC

    fresh_ts = _dt.now(_UTC).isoformat()
    cfgs = {
        "FRESH": {
            "ticker": "FRESH",
            "valuation_summary": {"calculated_at": fresh_ts, "weighted_fv_mid": 50.0},
        },
        "EMPTY": {"ticker": "EMPTY"},
    }

    def fake_load(client, ticker, user_id=None):
        return dict(cfgs[ticker])

    def fake_save(client, ticker, cfg, user_id=None):
        cfgs[ticker] = dict(cfg)

    def fake_list(client, user_id=None):
        return [{"ticker": t} for t in cfgs]

    def fake_calc(cfg, scenario_grid=False):
        return {"calculated_at": _dt.now(_UTC).isoformat(), "weighted_fv_mid": 99.0,
                "stock_price": 100.0, "lenses": {}}

    with _patch.object(mcp_server, "get_supabase_client", lambda: _Mock()), \
         _patch.object(mcp_server.config_store, "load_config", side_effect=fake_load), \
         _patch.object(mcp_server.config_store, "save_config", side_effect=fake_save), \
         _patch.object(mcp_server.config_store, "list_watchlist", side_effect=fake_list), \
         _patch.object(mcp_server.valuation_lenses, "calculate_multi_lens_valuation", side_effect=fake_calc), \
         _patch.object(mcp_server, "USER_ID", "u1"):
        result = _json.loads(mcp_server._refresh_all_valuations_impl(force=False))

    assert "EMPTY" in result["computed"]
    assert "FRESH" in result["skipped"]


def test_refresh_all_valuations_per_ticker_error_isolated():
    """One ticker raising during compute doesn't kill the others."""
    import json as _json
    import mcp_server
    from unittest.mock import patch as _patch, MagicMock as _Mock
    from datetime import datetime as _dt, UTC as _UTC

    cfgs = {"GOOD": {"ticker": "GOOD"}, "BAD": {"ticker": "BAD"}}

    def fake_load(client, ticker, user_id=None):
        return dict(cfgs[ticker])

    def fake_save(client, ticker, cfg, user_id=None):
        cfgs[ticker] = dict(cfg)

    def fake_list(client, user_id=None):
        return [{"ticker": t} for t in cfgs]

    def fake_calc(cfg, scenario_grid=False):
        if cfg.get("ticker") == "BAD":
            raise ValueError("boom")
        return {"calculated_at": _dt.now(_UTC).isoformat(), "weighted_fv_mid": 50.0,
                "stock_price": 100.0, "lenses": {}}

    with _patch.object(mcp_server, "get_supabase_client", lambda: _Mock()), \
         _patch.object(mcp_server.config_store, "load_config", side_effect=fake_load), \
         _patch.object(mcp_server.config_store, "save_config", side_effect=fake_save), \
         _patch.object(mcp_server.config_store, "list_watchlist", side_effect=fake_list), \
         _patch.object(mcp_server.valuation_lenses, "calculate_multi_lens_valuation", side_effect=fake_calc), \
         _patch.object(mcp_server, "USER_ID", "u1"):
        result = _json.loads(mcp_server._refresh_all_valuations_impl(force=True))

    assert "GOOD" in result["computed"]
    assert any("BAD" in e for e in result["errors"])
