# test_mcp_server.py
"""Tests for MCP server and config_store user_id parameter support."""
import json
import pytest
from unittest.mock import MagicMock, patch


def test_save_config_with_explicit_user_id():
    """save_config() should use provided user_id instead of calling _get_user_id()."""
    from config_store import save_config

    mock_client = MagicMock()
    mock_client.table.return_value.upsert.return_value.execute.return_value = None

    cfg = {"company": "Test Corp", "stock_price": 100.0}
    save_config(mock_client, "TEST", cfg, user_id="explicit-uid-123")

    # Verify upsert was called with the explicit user_id
    call_args = mock_client.table.return_value.upsert.call_args[0][0]
    assert call_args["user_id"] == "explicit-uid-123"
    assert call_args["ticker"] == "TEST"


def test_load_config_with_explicit_user_id():
    """load_config() should filter by user_id when provided."""
    from config_store import load_config

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.data = {"config": {"company": "Test", "sector_betas": [], "debt_breakdown": []}}
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = mock_resp

    result = load_config(mock_client, "TEST", user_id="uid-456")

    # Should chain two .eq() calls: ticker AND user_id
    eq_calls = mock_client.table.return_value.select.return_value.eq.call_args_list
    assert any(call[0] == ("ticker", "TEST") for call in eq_calls)


def test_list_watchlist_with_explicit_user_id():
    """list_watchlist() should filter by user_id when provided."""
    from config_store import list_watchlist

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.data = [
        {"ticker": "MSFT", "company": "Microsoft", "stock_price": 400, "updated_at": "2026-01-01"}
    ]
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_resp

    result = list_watchlist(mock_client, user_id="uid-789")
    assert len(result) == 1
    assert result[0]["ticker"] == "MSFT"

    # Verify .eq("user_id", ...) was called
    mock_client.table.return_value.select.return_value.eq.assert_called_once_with("user_id", "uid-789")


# ---------------------------------------------------------------------------
# MCP Server tests
# ---------------------------------------------------------------------------

def test_mcp_env_vars_missing(monkeypatch):
    """MCP server should raise clear error when env vars are missing."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("LAZYTHETA_USER_ID", raising=False)

    import mcp_server
    mcp_server._client = None  # Reset cached client
    mcp_server.SUPABASE_URL = ""
    mcp_server.SUPABASE_SERVICE_KEY = ""
    mcp_server.USER_ID = ""

    with pytest.raises(ValueError, match="SUPABASE_URL"):
        mcp_server.get_supabase_client()


def test_mcp_env_vars_present(monkeypatch):
    """MCP server should read env vars on import."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")
    monkeypatch.setenv("LAZYTHETA_USER_ID", "test-uid")

    import importlib
    import mcp_server
    importlib.reload(mcp_server)

    assert mcp_server.USER_ID == "test-uid"


# ---------------------------------------------------------------------------
# build_dcf_config tool
# ---------------------------------------------------------------------------

def test_build_dcf_config_tool():
    """build_dcf_config should resolve sector betas, fetch peers, and call build_config."""
    import mcp_server

    with patch.object(mcp_server, "gather_data") as mock_gd:
        mock_gd.fetch_stock_price.return_value = (150.0, 0, 0)
        mock_gd.fetch_treasury_yield.return_value = 0.04
        mock_gd.synthetic_credit_rating.return_value = ("A+", 0.01)
        mock_gd.SIC_TO_SECTOR = {7372: ("Software (System & Application)", 1.23)}
        mock_gd.fetch_sector_margins.return_value = {"Software (System & Application)": 0.25}
        mock_gd.find_peers.return_value = ["AAPL", "GOOGL"]
        mock_gd.fetch_peer_data.return_value = [
            {"ticker": "AAPL", "name": "Apple", "ev_revenue": 9.5, "ev_ebitda": 26.0,
             "pe": 33.5, "op_margin": 0.315, "rev_growth": 0.05, "roic": 0.55},
        ]
        mock_gd.build_config.return_value = {"company": "Test Corp", "ticker": "TEST"}

        financial_data = {
            "years": [2023, 2024, 2025],
            "revenue": [100_000, 110_000, 120_000],
            "operating_income": [30_000, 33_000, 36_000],
            "shares": [1000, 1000, 1000],
            "interest_expense_latest": 500,
        }

        result = mcp_server._build_dcf_config_impl(
            ticker="TEST",
            financial_data=financial_data,
            company_name="Test Corp",
            sic_code="7372",
        )

        assert result["ticker"] == "TEST"
        mock_gd.build_config.assert_called_once()

        # Verify sector_betas was passed as tuples, not raw dict
        call_kwargs = mock_gd.build_config.call_args
        sector_betas_arg = call_kwargs.kwargs.get("sector_betas")
        assert isinstance(sector_betas_arg, list)
        assert isinstance(sector_betas_arg[0], tuple)
        assert sector_betas_arg[0][0] == "Software (System & Application)"

        # Verify fetch_peer_data was called (not just find_peers)
        mock_gd.fetch_peer_data.assert_called_once_with(["AAPL", "GOOGL"])


# ---------------------------------------------------------------------------
# calculate_valuation tool
# ---------------------------------------------------------------------------

def test_calculate_valuation_tool():
    """calculate_valuation should return WACC, intrinsic value, and reverse DCF."""
    import importlib
    import mcp_server
    importlib.reload(mcp_server)

    cfg = {
        "equity_market_value": 2_000_000,
        "debt_market_value": 50_000,
        "tax_rate": 0.18,
        "sector_betas": [("Software", 1.05, 1.0)],
        "risk_free_rate": 0.04,
        "erp": 0.047,
        "credit_spread": 0.006,
        "base_revenue": 200_000,
        "revenue_growth": [0.10] * 10,
        "op_margins": [0.40] * 10,
        "terminal_growth": 0.03,
        "terminal_margin": 0.35,
        "sales_to_capital": 0.65,
        "sbc_pct": 0.04,
        "shares_outstanding": 7000,
        "margin_of_safety": 0.20,
        "cash_bridge": 50_000,
        "stock_price": 300.0,
    }

    result = json.loads(mcp_server._calculate_valuation_impl(cfg))
    assert "wacc" in result
    assert "intrinsic_value" in result
    assert "buy_price" in result
    assert "implied_growth" in result
    assert "implied_margin" in result
    assert result["intrinsic_value"] > 0


# ---------------------------------------------------------------------------
# save/get/list watchlist tools
# ---------------------------------------------------------------------------

def test_save_to_watchlist_tool():
    """save_to_watchlist should call config_store.save_config with user_id."""
    import mcp_server
    mcp_server.USER_ID = "test-uid"

    mock_client = MagicMock()
    mock_client.table.return_value.upsert.return_value.execute.return_value = None

    with patch.object(mcp_server, "get_supabase_client", return_value=mock_client):
        result = mcp_server._save_to_watchlist_impl("TEST", {"company": "Test", "stock_price": 100})
        assert "saved" in result.lower() or "TEST" in result


def test_get_config_tool():
    """get_config should return config from Supabase."""
    import mcp_server
    mcp_server.USER_ID = "test-uid"

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.data = {"config": {"company": "Test", "ticker": "TEST", "sector_betas": [], "debt_breakdown": []}}
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = mock_resp

    with patch.object(mcp_server, "get_supabase_client", return_value=mock_client):
        result = json.loads(mcp_server._get_config_impl("TEST"))
        assert result["ticker"] == "TEST"


def test_get_watchlist_tool():
    """get_watchlist should return list of tickers."""
    import mcp_server
    mcp_server.USER_ID = "test-uid"

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.data = [
        {"ticker": "MSFT", "company": "Microsoft", "stock_price": 400, "updated_at": "2026-01-01"},
        {"ticker": "AAPL", "company": "Apple", "stock_price": 230, "updated_at": "2026-01-01"},
    ]
    mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = mock_resp

    with patch.object(mcp_server, "get_supabase_client", return_value=mock_client):
        result = json.loads(mcp_server._get_watchlist_impl())
        assert len(result) == 2
        assert result[0]["ticker"] == "MSFT"


def test_fetch_tips_yield_parses_fred_csv():
    """fetch_tips_yield() should parse FRED CSV and return the latest TIPS rate."""
    import gather_data

    sample_csv = (
        b"observation_date,DFII10\n"
        b"2026-04-14,1.88\n"
        b"2026-04-15,1.90\n"
    )
    with patch("gather_data._http_get", return_value=sample_csv):
        rate = gather_data.fetch_tips_yield()
    assert rate == pytest.approx(0.019, abs=0.001)


def test_fetch_tips_yield_fallback_on_failure():
    """fetch_tips_yield() should return default 0.02 when FRED fetch fails."""
    import gather_data

    with patch("gather_data._http_get", side_effect=Exception("network error")):
        rate = gather_data.fetch_tips_yield()
    assert rate == 0.02


def _make_test_financials():
    """Helper: minimal financials dict for build_config tests."""
    return {
        "years": [2022, 2023, 2024, 2025],
        "revenue": [80000, 85000, 90000, 95000],
        "operating_income": [20000, 21000, 22000, 23000],
        "net_income": [16000, 17000, 18000, 19000],
        "cost_of_revenue": [40000, 42000, 44000, 46000],
        "sbc": [2000, 2100, 2200, 2300],
        "shares": [1000, 1000, 1000, 1000],
        "current_assets": [30000, 31000, 32000, 33000],
        "cash": [10000, 11000, 12000, 13000],
        "st_investments": [5000, 5000, 5000, 5000],
        "current_liabilities": [20000, 21000, 22000, 23000],
        "st_debt": [5000, 5000, 5000, 5000],
        "st_leases": [1000, 1000, 1000, 1000],
        "net_ppe": [15000, 16000, 17000, 18000],
        "goodwill_intang": [10000, 10000, 10000, 10000],
        "tax_provision": [4000, 4250, 4500, 4750],
        "pretax_income": [20000, 21000, 22000, 23000],
        "lt_debt_latest": 20000,
        "lt_leases_latest": 3000,
        "st_debt_latest": 5000,
        "interest_expense_latest": 1000,
        "finance_leases_latest": 0,
        "minority_interest_latest": 0,
        "equity_investments_latest": 0,
        "unfunded_pension_latest": 0,
        "entity_public_float": 0,
    }


@patch("gather_data.fetch_treasury_yield", return_value=0.0427)
@patch("gather_data.fetch_tips_yield", return_value=0.019)
@patch("gather_data.fetch_stock_price", return_value=(150.0, 0, 0))
@patch("gather_data.fetch_sector_betas", return_value={"Tech": 1.0})
@patch("gather_data.fetch_sector_margins", return_value={"Tech": 0.25})
@patch("gather_data.find_peers", return_value=[])
@patch("gather_data.fetch_peer_data", return_value=[])
def test_build_dcf_config_impl_real_mode(
    mock_peers_data, mock_peers, mock_margins, mock_betas,
    mock_price, mock_tips, mock_treasury,
):
    """_build_dcf_config_impl with valuation_basis='real' should use TIPS yield."""
    from mcp_server import _build_dcf_config_impl

    financials = _make_test_financials()
    cfg = _build_dcf_config_impl(
        ticker="TEST",
        financial_data=financials,
        company_name="Test Corp",
        sic_code="7372",
        valuation_basis="real",
    )
    assert cfg["valuation_basis"] == "real"
    assert cfg["risk_free_rate"] == pytest.approx(0.019, abs=0.001)
    assert cfg["nominal_risk_free_rate"] == pytest.approx(0.0427, abs=0.001)
    assert "breakeven_inflation" in cfg
    mock_tips.assert_called_once()


def test_calculate_valuation_includes_valuation_basis():
    """_calculate_valuation_impl should include valuation_basis in output."""
    import json
    from mcp_server import _calculate_valuation_impl

    cfg = {
        "equity_market_value": 100000,
        "debt_market_value": 20000,
        "risk_free_rate": 0.019,
        "erp": 0.047,
        "credit_spread": 0.01,
        "tax_rate": 0.20,
        "sector_betas": [("Tech", 1.0, 1.0)],
        "base_revenue": 50000,
        "revenue_growth": [0.05] * 10,
        "op_margins": [0.25] * 10,
        "terminal_growth": 0.005,
        "terminal_margin": 0.20,
        "sales_to_capital": 0.5,
        "sbc_pct": 0.03,
        "shares_outstanding": 1000,
        "margin_of_safety": 0.20,
        "stock_price": 100.0,
        "cash_bridge": 10000,
        "securities": 5000,
        "equity_investments": 0,
        "minority_interest": 0,
        "unfunded_pension": 0,
        "valuation_basis": "real",
        "nominal_risk_free_rate": 0.0427,
        "breakeven_inflation": 0.0237,
    }
    result = json.loads(_calculate_valuation_impl(cfg))
    assert result["valuation_basis"] == "real"
    assert result["nominal_risk_free_rate"] == 0.0427
    assert result["breakeven_inflation"] == 0.0237


def test_build_config_nominal_default():
    """build_config with default valuation_basis should not set real-valuation fields."""
    import gather_data
    financials = _make_test_financials()
    cfg = gather_data.build_config(
        ticker="TEST", financials=financials, stock_price=100.0,
        market_cap=100000, shares_yahoo=1000, risk_free_rate=0.04,
        sector_betas=[("Tech", 1.0, 1.0)], credit_spread=0.01,
        credit_rating="A", peers=[], company_name="Test Corp",
    )
    assert cfg["risk_free_rate"] == 0.04
    assert cfg.get("valuation_basis", "nominal") == "nominal"
    assert "breakeven_inflation" not in cfg


def test_build_config_real_mode():
    """build_config with valuation_basis='real' should store TIPS fields and deflate growth."""
    import gather_data
    financials = _make_test_financials()
    cfg = gather_data.build_config(
        ticker="TEST", financials=financials, stock_price=100.0,
        market_cap=100000, shares_yahoo=1000, risk_free_rate=0.019,
        sector_betas=[("Tech", 1.0, 1.0)], credit_spread=0.01,
        credit_rating="A", peers=[], company_name="Test Corp",
        valuation_basis="real",
        nominal_risk_free_rate=0.0427,
    )
    assert cfg["valuation_basis"] == "real"
    assert cfg["risk_free_rate"] == 0.019
    assert cfg["nominal_risk_free_rate"] == 0.0427
    assert cfg["breakeven_inflation"] == pytest.approx(0.0237, abs=0.001)
    # Terminal growth should default to 0.005 for real mode
    assert cfg["terminal_growth"] == 0.005
    # Nominal revenue growth should be stored
    assert "nominal_revenue_growth" in cfg
    # Real revenue growth should be lower than nominal by ~breakeven
    for real_g, nom_g in zip(cfg["revenue_growth"], cfg["nominal_revenue_growth"]):
        assert real_g < nom_g or nom_g <= 0


def test_convert_to_real():
    """convert_to_real should transform a nominal config to real basis."""
    import gather_data

    nominal_cfg = {
        "risk_free_rate": 0.0427,
        "revenue_growth": [0.08, 0.07, 0.06, 0.05, 0.04, 0.04, 0.03, 0.03, 0.03, 0.025],
        "terminal_growth": 0.025,
        "valuation_basis": "nominal",
    }
    real_cfg = gather_data.convert_to_real(nominal_cfg, tips_yield=0.019, breakeven=0.0237)

    assert real_cfg["valuation_basis"] == "real"
    assert real_cfg["risk_free_rate"] == 0.019
    assert real_cfg["nominal_risk_free_rate"] == 0.0427
    assert real_cfg["breakeven_inflation"] == 0.0237
    assert real_cfg["terminal_growth"] == pytest.approx(0.0013, abs=0.001)
    assert real_cfg["nominal_revenue_growth"] == nominal_cfg["revenue_growth"]
    # Each real growth = nominal - breakeven, floored at 0
    for real_g, nom_g in zip(real_cfg["revenue_growth"], real_cfg["nominal_revenue_growth"]):
        expected = max(nom_g - 0.0237, 0.0)
        assert real_g == pytest.approx(expected, abs=0.0001)


def test_convert_to_real_floors_at_zero():
    """convert_to_real should floor growth rates at 0%."""
    import gather_data

    nominal_cfg = {
        "risk_free_rate": 0.04,
        "revenue_growth": [0.01, 0.005],
        "terminal_growth": 0.025,
        "valuation_basis": "nominal",
    }
    real_cfg = gather_data.convert_to_real(nominal_cfg, tips_yield=0.019, breakeven=0.021)
    # 0.01 - 0.021 = -0.011 -> floored to 0.0
    assert real_cfg["revenue_growth"][0] == 0.0
    # 0.005 - 0.021 = -0.016 -> floored to 0.0
    assert real_cfg["revenue_growth"][1] == 0.0
    # terminal: 0.025 - 0.021 = 0.004
    assert real_cfg["terminal_growth"] == pytest.approx(0.004, abs=0.001)


# ---------------------------------------------------------------------------
# update_valuation_inputs tool
# ---------------------------------------------------------------------------

def test_update_valuation_inputs_writes_field(monkeypatch):
    """Calling the tool writes the field into cfg.valuation_inputs and saves."""
    import json as _json
    import mcp_server

    storage = {
        "TEST": {
            "company": "Test",
            "ticker": "TEST",
            "valuation_inputs": {
                "_auto_filled": ["dividend_5y_cagr"],
                "dividend_5y_cagr": 0.05,
            },
        },
    }

    def fake_load(client, ticker, user_id=None):
        return dict(storage[ticker.upper()])

    def fake_save(client, ticker, cfg, user_id=None):
        storage[ticker.upper()] = dict(cfg)

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_valuation_inputs_impl(
        "TEST", {"dividend_5y_cagr": 0.10}
    )
    result = _json.loads(result_json)

    assert result["dividend_5y_cagr"] == 0.10
    saved = storage["TEST"]
    assert saved["valuation_inputs"]["dividend_5y_cagr"] == 0.10


def test_update_valuation_inputs_removes_from_auto_filled(monkeypatch):
    """Overriding a field removes it from _auto_filled so future refresh
    won't overwrite the user value."""
    import mcp_server

    storage = {
        "TEST": {
            "ticker": "TEST",
            "valuation_inputs": {
                "_auto_filled": ["dividend_5y_cagr", "ttm_dividend"],
                "dividend_5y_cagr": 0.05,
                "ttm_dividend": 4.00,
            },
        },
    }

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    mcp_server._update_valuation_inputs_impl("TEST", {"dividend_5y_cagr": 0.10})
    saved = storage["TEST"]
    assert "dividend_5y_cagr" not in saved["valuation_inputs"]["_auto_filled"]
    # Other auto-filled fields untouched
    assert "ttm_dividend" in saved["valuation_inputs"]["_auto_filled"]


def test_update_valuation_inputs_preserves_other_fields(monkeypatch):
    """Updating one field doesn't disturb others."""
    import mcp_server

    storage = {
        "TEST": {
            "ticker": "TEST",
            "valuation_inputs": {
                "_auto_filled": ["dividend_5y_cagr", "ttm_dividend", "forward_eps"],
                "dividend_5y_cagr": 0.05,
                "ttm_dividend": 4.00,
                "forward_eps": 8.00,
            },
        },
    }

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    mcp_server._update_valuation_inputs_impl("TEST", {"dividend_5y_cagr": 0.10})
    saved = storage["TEST"]
    assert saved["valuation_inputs"]["ttm_dividend"] == 4.00
    assert saved["valuation_inputs"]["forward_eps"] == 8.00


def test_update_valuation_inputs_unknown_ticker_returns_error(monkeypatch):
    """If the ticker isn't on the watchlist, return a JSON error string."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_valuation_inputs_impl(
        "UNKNOWN", {"dividend_5y_cagr": 0.10}
    )
    assert "error" in _json.loads(result_json)


# ---------------------------------------------------------------------------
# update_lens_weights
# ---------------------------------------------------------------------------


def _make_lens_weights_fake_storage(initial_weights):
    """Helper: build a fake Supabase storage with one TEST ticker."""
    storage = {
        "TEST": {
            "company": "Test",
            "ticker": "TEST",
            "lens_weights": dict(initial_weights),
        },
    }
    return storage


def test_update_lens_weights_merges_partial(monkeypatch):
    """Partial override merges into existing lens_weights; unspecified keys
    retain their current value."""
    import json as _json
    import mcp_server

    storage = _make_lens_weights_fake_storage(
        {"dcf": 0.50, "multiples": 0.25, "historical": 0.25}
    )

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_lens_weights_impl(
        "TEST", {"dividend": 0.20}
    )
    result = _json.loads(result_json)

    # dividend added, others preserved
    assert result["dividend"] == 0.20
    assert result["dcf"] == 0.50
    assert result["multiples"] == 0.25
    assert result["historical"] == 0.25
    # saved to storage
    assert storage["TEST"]["lens_weights"]["dividend"] == 0.20


def test_update_lens_weights_empty_dict_resets_to_defaults(monkeypatch):
    """Empty weights dict → cfg["lens_weights"] = {} so the orchestrator
    falls back to DEFAULT_LENS_WEIGHTS."""
    import json as _json
    import mcp_server

    storage = _make_lens_weights_fake_storage(
        {"dcf": 0.90, "multiples": 0.10}  # user had custom weights
    )

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_lens_weights_impl("TEST", {})
    result = _json.loads(result_json)

    assert result == {}
    assert storage["TEST"]["lens_weights"] == {}


def test_update_lens_weights_rejects_unknown_key(monkeypatch):
    """Unknown lens key → error JSON with the invalid keys listed."""
    import json as _json
    import mcp_server

    storage = _make_lens_weights_fake_storage({"dcf": 0.50})

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_lens_weights_impl(
        "TEST", {"sentiment": 0.5, "dcf": 0.4}  # sentiment is not a real lens
    )
    body = _json.loads(result_json)
    assert "error" in body
    assert "sentiment" in body["error"]
    # No write happened
    assert storage["TEST"]["lens_weights"] == {"dcf": 0.50}


def test_update_lens_weights_rejects_negative_value(monkeypatch):
    """Negative weight → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_lens_weights_fake_storage({"dcf": 0.50})

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()]),
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_lens_weights_impl(
        "TEST", {"dividend": -0.1}
    )
    body = _json.loads(result_json)
    assert "error" in body
    assert "dividend" in body["error"]
    # Unchanged storage
    assert storage["TEST"]["lens_weights"] == {"dcf": 0.50}


def test_update_lens_weights_unknown_ticker_returns_error(monkeypatch):
    """Unknown ticker → error JSON."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_lens_weights_impl(
        "UNKNOWN", {"dcf": 0.5}
    )
    assert "error" in _json.loads(result_json)


# ---------------------------------------------------------------------------
# update_sotp_segments
# ---------------------------------------------------------------------------


def _make_sotp_fake_storage(initial_sotp=None):
    """Helper: build a fake Supabase storage with one TEST ticker."""
    cfg = {"company": "Test", "ticker": "TEST"}
    if initial_sotp is not None:
        cfg["sotp"] = dict(initial_sotp)
    return {"TEST": cfg}


def _patch_sotp_storage(monkeypatch, storage):
    """Helper: wire load_config/save_config to the in-memory storage."""
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()])
            if t.upper() in storage else None,
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")


def test_update_sotp_segments_adds_new_segment(monkeypatch):
    """Calling with a new segment name appends it to cfg.sotp.segments."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()  # no sotp key yet
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl(
        "TEST",
        [{"name": "AWS", "ev_mid": 800000, "rationale": "8x EV/EBITDA"}],
    )
    result = _json.loads(result_json)

    assert result["segment_count"] == 1
    saved = storage["TEST"]["sotp"]["segments"]
    assert len(saved) == 1
    assert saved[0]["name"] == "AWS"
    assert saved[0]["ev_mid"] == 800000
    assert saved[0]["rationale"] == "8x EV/EBITDA"


def test_update_sotp_segments_merges_existing_by_name(monkeypatch):
    """Existing segment matched by name (case-insensitive) gets partial-merged;
    other fields and other segments stay intact."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [
            {"name": "AWS", "ev_mid": 800000, "ev_low": 700000,
             "rationale": "old rationale"},
            {"name": "Retail", "ev_mid": 200000},
        ],
    })
    _patch_sotp_storage(monkeypatch, storage)

    import json as _json
    result_json = mcp_server._update_sotp_segments_impl(
        "TEST",
        [{"name": "aws", "rationale": "updated rationale"}],  # lowercase match
    )
    result = _json.loads(result_json)
    assert "error" not in result
    assert result["segment_count"] == 2

    segs = {s["name"]: s for s in storage["TEST"]["sotp"]["segments"]}
    assert segs["AWS"]["ev_mid"] == 800000  # untouched
    assert segs["AWS"]["ev_low"] == 700000  # untouched
    assert segs["AWS"]["rationale"] == "updated rationale"  # merged
    assert segs["Retail"]["ev_mid"] == 200000  # other segment untouched


def test_update_sotp_segments_mixed_new_and_update(monkeypatch):
    """A single call can both update an existing segment and add a new one."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._update_sotp_segments_impl(
        "TEST",
        [
            {"name": "AWS", "ev_mid": 900000},  # update
            {"name": "Advertising", "ev_mid": 150000},  # new
        ],
    )

    segs = storage["TEST"]["sotp"]["segments"]
    assert len(segs) == 2
    by_name = {s["name"]: s for s in segs}
    assert by_name["AWS"]["ev_mid"] == 900000
    assert by_name["Advertising"]["ev_mid"] == 150000


def test_update_sotp_segments_unknown_ticker_returns_error(monkeypatch):
    """Unknown ticker → error JSON."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_sotp_segments_impl(
        "UNKNOWN", [{"name": "AWS", "ev_mid": 100}]
    )
    assert "error" in _json.loads(result_json)


def test_update_sotp_segments_empty_list_returns_error(monkeypatch):
    """Empty segments list → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage({"segments": [{"name": "AWS", "ev_mid": 100}]})
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl("TEST", [])
    assert "error" in _json.loads(result_json)
    # unchanged
    assert storage["TEST"]["sotp"]["segments"] == [{"name": "AWS", "ev_mid": 100}]


def test_update_sotp_segments_new_segment_without_ev_mid_returns_error(monkeypatch):
    """New segment without ev_mid > 0 → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl(
        "TEST", [{"name": "AWS", "rationale": "no ev"}]
    )
    body = _json.loads(result_json)
    assert "error" in body
    assert "AWS" in body["error"]
    # no sotp written
    assert "sotp" not in storage["TEST"] or not storage["TEST"]["sotp"].get("segments")


def test_update_sotp_segments_negative_ev_returns_error(monkeypatch):
    """Negative EV value → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl(
        "TEST", [{"name": "AWS", "ev_mid": -100}]
    )
    body = _json.loads(result_json)
    assert "error" in body
    assert "ev_mid" in body["error"]


# ---------------------------------------------------------------------------
# remove_sotp_segment
# ---------------------------------------------------------------------------


def test_remove_sotp_segment_removes_existing(monkeypatch):
    """Removing an existing segment leaves the rest intact."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [
            {"name": "AWS", "ev_mid": 800000},
            {"name": "Retail", "ev_mid": 200000},
        ],
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._remove_sotp_segment_impl("TEST", "AWS")

    segs = storage["TEST"]["sotp"]["segments"]
    assert len(segs) == 1
    assert segs[0]["name"] == "Retail"


def test_remove_sotp_segment_case_insensitive(monkeypatch):
    """Name match is case-insensitive."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._remove_sotp_segment_impl("TEST", "aws")

    assert storage["TEST"]["sotp"]["segments"] == []


def test_remove_sotp_segment_missing_name_is_noop(monkeypatch):
    """Removing a non-existing name is a no-op, not an error."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
    })
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._remove_sotp_segment_impl("TEST", "NonExistent")
    result = _json.loads(result_json)
    assert "error" not in result
    assert storage["TEST"]["sotp"]["segments"] == [{"name": "AWS", "ev_mid": 800000}]


def test_remove_sotp_segment_no_sotp_dict_is_noop(monkeypatch):
    """Removing from a cfg that has no sotp key is a no-op."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()  # no sotp
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._remove_sotp_segment_impl("TEST", "AWS")
    result = _json.loads(result_json)
    assert "error" not in result


def test_remove_sotp_segment_unknown_ticker_returns_error(monkeypatch):
    """Unknown ticker → error JSON."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._remove_sotp_segment_impl("UNKNOWN", "AWS")
    assert "error" in _json.loads(result_json)


# ---------------------------------------------------------------------------
# set_sotp_corporate_overhead
# ---------------------------------------------------------------------------


def test_set_sotp_corporate_overhead_writes_value(monkeypatch):
    """Set the overhead value on a cfg that already has sotp segments."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
        "corporate_overhead_ev_adjustment": 0,
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._set_sotp_corporate_overhead_impl("TEST", -5000)

    assert storage["TEST"]["sotp"]["corporate_overhead_ev_adjustment"] == -5000
    # segments untouched
    assert storage["TEST"]["sotp"]["segments"] == [{"name": "AWS", "ev_mid": 800000}]


def test_set_sotp_corporate_overhead_initialises_sotp_dict(monkeypatch):
    """If cfg has no sotp dict, the call creates one with segments: []."""
    import mcp_server

    storage = _make_sotp_fake_storage()  # no sotp
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._set_sotp_corporate_overhead_impl("TEST", -2500)

    saved = storage["TEST"]["sotp"]
    assert saved["corporate_overhead_ev_adjustment"] == -2500
    assert saved.get("segments") == []


def test_set_sotp_corporate_overhead_non_number_returns_error(monkeypatch):
    """Non-numeric value → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage({"corporate_overhead_ev_adjustment": 0})
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._set_sotp_corporate_overhead_impl("TEST", "abc")
    body = _json.loads(result_json)
    assert "error" in body
    assert storage["TEST"]["sotp"]["corporate_overhead_ev_adjustment"] == 0


def test_set_sotp_corporate_overhead_unknown_ticker_returns_error(monkeypatch):
    """Unknown ticker → error JSON."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._set_sotp_corporate_overhead_impl("UNKNOWN", -100)
    assert "error" in _json.loads(result_json)


# ---------------------------------------------------------------------------
# Integration: SOTP round-trip
# ---------------------------------------------------------------------------


def test_sotp_round_trip_segments_then_calculate_multi_lens(monkeypatch):
    """After update_sotp_segments, calculate_multi_lens_valuation returns a
    populated 'sotp' lens with fv_low/mid/high."""
    import mcp_server
    import valuation_lenses

    # Minimal cfg with the bridge inputs the SOTP lens needs
    storage = {
        "AMZN": {
            "ticker": "AMZN",
            "company": "Amazon",
            "shares_outstanding": 10000,    # 10,000 M shares
            "cash": [50000],                # $50B
            "st_investments": [0],
            "debt_market_value": 60000,     # $60B
            "minority_interest": 0,
            "unfunded_pension": 0,
            "equity_investments": 0,
            "lens_weights": {"sotp": 1.0, "dcf": 0, "multiples": 0,
                              "historical": 0, "reverse_dcf": 0, "dividend": 0},
        },
    }
    _patch_sotp_storage(monkeypatch, storage)

    # Step 1: add two segments via the tool
    mcp_server._update_sotp_segments_impl(
        "AMZN",
        [
            {"name": "AWS", "ev_mid": 800000, "ev_low": 700000, "ev_high": 900000},
            {"name": "Retail", "ev_mid": 200000, "ev_low": 150000, "ev_high": 250000},
        ],
    )

    # Step 2: compute lenses on the resulting cfg
    cfg = storage["AMZN"]
    lens_out = valuation_lenses.compute_sotp_lens(cfg)

    assert lens_out is not None
    assert lens_out["fv_mid"] > 0
    assert lens_out["fv_low"] <= lens_out["fv_mid"] <= lens_out["fv_high"]
    assert lens_out["details"]["segment_count"] == 2


# ---------------------------------------------------------------------------
# apply_fundamentals_overrides helper
# ---------------------------------------------------------------------------


def test_apply_fundamentals_overrides_replaces_component():
    """Override on a component (operating_income) takes effect at the
    matching year index; other years stay untouched."""
    import gather_data
    fund = {
        "years": [2023, 2024, 2025],
        "operating_income": [100.0, 120.0, 130.0],
    }
    out = gather_data.apply_fundamentals_overrides(
        fund, {"operating_income": {2024: 999.0}}
    )
    assert out["operating_income"] == [100.0, 999.0, 130.0]
    # Original untouched (no mutation)
    assert fund["operating_income"] == [100.0, 120.0, 130.0]


def test_apply_fundamentals_overrides_recomputes_fcf_and_ebitda():
    """Derived metrics (fcf = cfo + capex, ebitda = oi + da) recompute
    after a component override — caller doesn't have to set them."""
    import gather_data
    fund = {
        "years": [2024],
        "cfo": [200.0],
        "capex": [-50.0],
        "operating_income": [100.0],
        "da": [30.0],
        "fcf": [150.0],
        "ebitda": [130.0],
    }
    out = gather_data.apply_fundamentals_overrides(
        fund, {"cfo": {2024: 300.0}, "operating_income": {2024: 200.0}}
    )
    assert out["fcf"] == [250.0]      # 300 + (-50)
    assert out["ebitda"] == [230.0]   # 200 + 30


def test_apply_fundamentals_overrides_ignores_unknown_field():
    """Override on a field not in the whitelist is silently ignored (defensive)."""
    import gather_data
    fund = {"years": [2024], "operating_income": [100.0]}
    out = gather_data.apply_fundamentals_overrides(
        fund, {"some_unknown_metric": {2024: 999.0}}
    )
    assert "some_unknown_metric" not in out
    assert out["operating_income"] == [100.0]


def test_apply_fundamentals_overrides_ignores_unmatched_year():
    """Override on a year not in fund.years is silently ignored."""
    import gather_data
    fund = {"years": [2024, 2025], "operating_income": [100.0, 110.0]}
    out = gather_data.apply_fundamentals_overrides(
        fund, {"operating_income": {2099: 999.0}}
    )
    assert out["operating_income"] == [100.0, 110.0]


# ---------------------------------------------------------------------------
# _get_fundamentals_impl + _update_fundamentals_impl
# ---------------------------------------------------------------------------


def _patch_fund_storage(monkeypatch, storage, fake_fund):
    """Helper: wire load/save_config to in-memory storage, fake EDGAR fetch."""
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()])
            if t.upper() in storage else None,
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(
        mcp_server.gather_data, "fetch_fundamentals",
        lambda ticker, n_years=10: dict(fake_fund),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")


def test_get_fundamentals_returns_overridden_data(monkeypatch):
    """Stored overrides take effect in the returned 'raw' arrays."""
    import json as _json
    import mcp_server
    storage = {
        "MCD": {
            "ticker": "MCD",
            "equity_market_value": 200000,  # $M
            "fundamentals_overrides": {
                "operating_lease_liabilities": {"2024": 12500.0, "2025": 12800.0},
            },
        },
    }
    fake_fund = {
        "years": [2023, 2024, 2025],
        "operating_income": [11000.0, 11500.0, 12000.0],
        "operating_lease_liabilities": [12000.0, None, None],
        "total_assets": [55000.0, 55000.0, 55000.0],
        "current_liabilities": [10000.0, 10000.0, 10000.0],
        "cash": [4000.0, 1000.0, 800.0],
        "goodwill": [3000.0, 3000.0, 3000.0],
        "total_debt": [40000.0, 40000.0, 40000.0],
        "fcf": [5000.0, 5500.0, 6000.0],
        "da": [2000.0, 2100.0, 2200.0],
        "net_income": [8000.0, 8500.0, 9000.0],
        "total_equity": [-4500.0, -4500.0, -4500.0],
        "shares": [None, None, None],
        "cfo": [9000.0, 9500.0, 10000.0],
        "capex": [-2000.0, -2000.0, -2000.0],
        "short_term_debt": [400.0, 800.0, 1500.0],
        "finance_lease_liabilities": [1600.0, 1800.0, 2400.0],
        "pension_liabilities": [None, None, None],
    }
    _patch_fund_storage(monkeypatch, storage, fake_fund)

    result = _json.loads(mcp_server._get_fundamentals_impl("MCD", n_years=3))
    assert result["ticker"] == "MCD"
    # Override took effect
    assert result["raw"]["operating_lease_liabilities"] == [12000.0, 12500.0, 12800.0]
    # Headline computed
    assert result["headline"]["latest_year"] == 2025
    assert result["headline"]["current_ebit_ev_pct"] is not None
    # Float-business not triggered for MCD (CE/TA > 25%)
    assert result["headline"]["roce_metric"] == "ROCE"


def test_get_fundamentals_unknown_ticker_returns_error(monkeypatch):
    import json as _json
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")
    result_json = mcp_server._get_fundamentals_impl("UNKNOWN")
    assert "error" in _json.loads(result_json)


def test_update_fundamentals_merges_by_field_year(monkeypatch):
    """New (field, year) overrides merge with existing ones."""
    import json as _json
    import mcp_server
    storage = {
        "MCD": {
            "ticker": "MCD",
            "fundamentals_overrides": {
                "operating_lease_liabilities": {"2024": 12500.0},
            },
        },
    }
    _patch_fund_storage(monkeypatch, storage, {"years": []})

    result = _json.loads(mcp_server._update_fundamentals_impl(
        "MCD", {"operating_lease_liabilities": {2025: 12800}},
    ))
    saved = storage["MCD"]["fundamentals_overrides"]
    assert saved["operating_lease_liabilities"]["2024"] == 12500.0
    assert saved["operating_lease_liabilities"]["2025"] == 12800.0
    assert result["field_count"] == 1
    assert result["total_override_cells"] == 2


def test_update_fundamentals_null_value_removes_override(monkeypatch):
    """Passing null as value removes that specific (field, year)."""
    import mcp_server
    storage = {
        "MCD": {
            "ticker": "MCD",
            "fundamentals_overrides": {
                "operating_lease_liabilities": {"2024": 12500.0, "2025": 12800.0},
            },
        },
    }
    _patch_fund_storage(monkeypatch, storage, {"years": []})

    mcp_server._update_fundamentals_impl(
        "MCD", {"operating_lease_liabilities": {2024: None}},
    )
    saved = storage["MCD"]["fundamentals_overrides"]
    assert "2024" not in saved["operating_lease_liabilities"]
    assert saved["operating_lease_liabilities"]["2025"] == 12800.0


def test_update_fundamentals_removes_empty_field(monkeypatch):
    """Removing the last (year) override for a field drops the field entry."""
    import mcp_server
    storage = {
        "MCD": {
            "ticker": "MCD",
            "fundamentals_overrides": {
                "operating_lease_liabilities": {"2024": 12500.0},
            },
        },
    }
    _patch_fund_storage(monkeypatch, storage, {"years": []})

    mcp_server._update_fundamentals_impl(
        "MCD", {"operating_lease_liabilities": {2024: None}},
    )
    saved = storage["MCD"].get("fundamentals_overrides", {})
    assert "operating_lease_liabilities" not in saved


def test_update_fundamentals_rejects_unknown_field(monkeypatch):
    import json as _json
    import mcp_server
    storage = {"MCD": {"ticker": "MCD"}}
    _patch_fund_storage(monkeypatch, storage, {"years": []})

    result = _json.loads(mcp_server._update_fundamentals_impl(
        "MCD", {"some_unknown_metric": {2024: 100}},
    ))
    assert "error" in result
    assert "some_unknown_metric" in result["error"]


def test_update_fundamentals_rejects_non_number(monkeypatch):
    import json as _json
    import mcp_server
    storage = {"MCD": {"ticker": "MCD"}}
    _patch_fund_storage(monkeypatch, storage, {"years": []})

    result = _json.loads(mcp_server._update_fundamentals_impl(
        "MCD", {"operating_income": {2024: "not-a-number"}},
    ))
    assert "error" in result


def test_update_fundamentals_unknown_ticker_returns_error(monkeypatch):
    import json as _json
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")
    result_json = mcp_server._update_fundamentals_impl(
        "UNKNOWN", {"operating_income": {2024: 100}},
    )
    assert "error" in _json.loads(result_json)


def test_phase_gate_metrics_computes_rule_of_40_and_incr_roic():
    """_phase_gate_metrics: Rule of 40 (3y CAGR + FCF margin), incremental ROIC
    (3-delta), and latest ROCE + rising on EBIT/(TA-CL)."""
    import mcp_server
    fund = {
        "years":               [2021, 2022, 2023, 2024, 2025],
        "revenue":             [100, 130, 170, 220, 286],
        "fcf":                 [5, 10, 18, 24, 28.6],
        "operating_income":    [10, 15, 22, 30, 40],
        "pretax_income":       [10, 15, 22, 30, 40],
        "tax_provision":       [0, 0, 0, 0, 0],   # tr=0 -> NOPAT == EBIT
        "total_debt":          [0, 0, 0, 0, 0],
        "total_equity":        [50, 60, 75, 95, 120],
        "cash":                [0, 0, 0, 0, 0],
        "total_assets":        [100, 120, 150, 190, 240],
        "current_liabilities": [20, 20, 20, 20, 20],
    }
    m = mcp_server._phase_gate_metrics(fund)
    assert m["fcf_margin_pct"] == pytest.approx(10.0, abs=0.1)          # 28.6/286
    assert m["revenue_cagr_3y_pct"] == pytest.approx(30.06, abs=0.5)    # 130->286 / 3y
    assert m["rule_of_40_pct"] == pytest.approx(40.06, abs=0.6)
    assert m["incremental_roic_pct"] == pytest.approx(41.67, abs=0.5)   # d25/d60
    assert m["roce_latest_pct"] == pytest.approx(18.18, abs=0.2)        # 40/220
    assert m["roce_rising"] is True


def test_phase_gate_metrics_guards_on_thin_data():
    import mcp_server
    # <4 usable years -> incremental ROIC None; sparse -> other fields None-safe
    m = mcp_server._phase_gate_metrics({
        "years": [2024, 2025], "revenue": [100, 130], "fcf": [10, 13],
        "operating_income": [10, 15], "total_assets": [100, 120],
        "current_liabilities": [20, 20], "total_equity": [50, 60],
        "total_debt": [0, 0], "cash": [0, 0],
    })
    assert m["incremental_roic_pct"] is None        # needs >=4 deltas
    assert m["fcf_margin_pct"] == pytest.approx(10.0, abs=0.1)
    assert m["roce_latest_pct"] == pytest.approx(15.0, abs=0.2)  # 15/(120-20)


def test_phase_gate_metrics_incr_roic_none_when_capital_shrinks():
    import mcp_server
    # invested capital shrinks across the window -> guard returns None
    m = mcp_server._phase_gate_metrics({
        "years": [2021, 2022, 2023, 2024, 2025],
        "revenue": [100, 110, 120, 130, 140], "fcf": [10, 11, 12, 13, 14],
        "operating_income": [10, 11, 12, 13, 14],
        "pretax_income": [10, 11, 12, 13, 14], "tax_provision": [0, 0, 0, 0, 0],
        "total_debt": [0, 0, 0, 0, 0],
        "total_equity": [120, 110, 100, 90, 80],   # shrinking (buybacks)
        "cash": [0, 0, 0, 0, 0],
        "total_assets": [200, 200, 200, 200, 200],
        "current_liabilities": [50, 50, 50, 50, 50],
    })
    assert m["incremental_roic_pct"] is None
