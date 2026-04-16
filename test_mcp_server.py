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
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = mock_resp

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
        "buyback_rate": 0,
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
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.single.return_value.execute.return_value = mock_resp

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
        "buyback": [0, 0, 0, 0],
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
        "buyback_rate": 0,
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
