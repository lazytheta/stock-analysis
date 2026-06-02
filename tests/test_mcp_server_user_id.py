"""Tests that the _*_impl functions accept an explicit user_id parameter
and fall back to the module-level USER_ID env var when omitted."""
from unittest.mock import MagicMock


def test_get_watchlist_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = {}

    def fake_list(client, user_id=None):
        captured["user_id"] = user_id
        return []

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "list_watchlist", fake_list)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._get_watchlist_impl(user_id="explicit-uid-123")
    assert captured["user_id"] == "explicit-uid-123"


def test_get_watchlist_impl_falls_back_to_env_user_id(monkeypatch):
    """When user_id is omitted, the impl uses the module-level USER_ID env var."""
    import mcp_server

    captured = {}

    def fake_list(client, user_id=None):
        captured["user_id"] = user_id
        return []

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "list_watchlist", fake_list)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._get_watchlist_impl()
    assert captured["user_id"] == "env-fallback-uid"


def test_get_config_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = {}

    def fake_load(client, ticker, user_id=None):
        captured["user_id"] = user_id
        return {"company": "Test"}

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._get_config_impl("AAPL", user_id="other-uid")
    assert captured["user_id"] == "other-uid"


def test_save_to_watchlist_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = {}

    def fake_save(client, ticker, cfg, user_id=None):
        captured["user_id"] = user_id

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._save_to_watchlist_impl("AAPL", {"company": "Apple"}, user_id="caller-uid")
    assert captured["user_id"] == "caller-uid"


def test_update_valuation_inputs_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = {}
    storage = {"AAPL": {"valuation_inputs": {"_auto_filled": []}}}

    def fake_load(client, ticker, user_id=None):
        captured["load_user_id"] = user_id
        return dict(storage[ticker.upper()])

    def fake_save(client, ticker, cfg, user_id=None):
        captured["save_user_id"] = user_id
        storage[ticker.upper()] = dict(cfg)

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._update_valuation_inputs_impl(
        "AAPL", {"forward_eps": 7.0}, user_id="multi-user-uid"
    )
    assert captured["load_user_id"] == "multi-user-uid"
    assert captured["save_user_id"] == "multi-user-uid"


def test_calculate_multi_lens_valuation_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = {}
    cfg = {
        "company": "Test",
        "ticker": "TEST",
        "stock_price": 100.0,
        "equity_market_value": 1000,
        "debt_market_value": 100,
        "sector_betas": [("Sector", 1.0, 1.0)],
        "tax_rate": 0.21,
        "risk_free_rate": 0.04,
        "erp": 0.05,
        "credit_spread": 0.01,
        "base_revenue": 50_000,
        "revenue_growth": [0.05] * 5,
        "op_margins": [0.20] * 5,
        "terminal_growth": 0.025,
        "terminal_margin": 0.20,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.02,
        "shares_outstanding": 1_000,
        "margin_of_safety": 0.20,
        "cash_bridge": 5_000,
        "securities": 0,
        "peers": [],
    }

    def fake_load(client, ticker, user_id=None):
        captured["load_user_id"] = user_id
        return dict(cfg)

    def fake_save(client, ticker, cfg, user_id=None):
        captured["save_user_id"] = user_id

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server.auto_fetch, "auto_fill_valuation_inputs", lambda c: None)
    monkeypatch.setattr(mcp_server.auto_fetch, "auto_fill_peer_market_data", lambda c: None)
    monkeypatch.setattr(mcp_server.auto_fetch, "auto_fill_dividend_inputs", lambda c: None)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._calculate_multi_lens_valuation_impl("TEST", user_id="explicit-uid")
    assert captured["load_user_id"] == "explicit-uid"
    assert captured["save_user_id"] == "explicit-uid"


def test_refresh_all_valuations_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = []

    def fake_list(client, user_id=None):
        captured.append(("list", user_id))
        return []  # empty watchlist → fast exit

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "list_watchlist", fake_list)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._refresh_all_valuations_impl(force=True, user_id="batch-uid")
    assert ("list", "batch-uid") in captured


def test_get_prescan_prompts_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = {}

    def fake_load(client, ticker, user_id=None):
        captured["user_id"] = user_id
        return {"company": "Test", "ticker": "TEST", "ai_notes": {}}

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")
    monkeypatch.setattr(
        mcp_server, "_PRESCAN_PROMPTS",
        [{"title": "Test", "prompt": "Test {ticker}"}],
        raising=False,
    )

    mcp_server._get_prescan_prompts_impl("TEST", user_id="prescan-uid")
    assert captured["user_id"] == "prescan-uid"


def test_save_prescan_section_impl_uses_explicit_user_id(monkeypatch):
    import mcp_server

    captured = {}

    def fake_load(client, ticker, user_id=None):
        captured["load_user_id"] = user_id
        return {"company": "Test", "ai_notes": {}}

    def fake_save(client, ticker, cfg, user_id=None):
        captured["save_user_id"] = user_id

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server, "USER_ID", "env-fallback-uid")

    mcp_server._save_prescan_section_impl(
        "TEST", "Section A", "content", user_id="writer-uid"
    )
    assert captured["load_user_id"] == "writer-uid"
    assert captured["save_user_id"] == "writer-uid"


def test_get_supabase_client_no_longer_requires_LAZYTHETA_USER_ID(monkeypatch):
    """For Cloud Run multi-user mode, get_supabase_client must instantiate
    with only SUPABASE_URL + SUPABASE_SERVICE_KEY. user_id flows in via
    JWT per request, not from a module-level env var."""
    import mcp_server

    # Clear the cached client and the USER_ID
    monkeypatch.setattr(mcp_server, "_client", None)
    monkeypatch.setattr(mcp_server, "USER_ID", "")
    monkeypatch.setattr(mcp_server, "SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setattr(mcp_server, "SUPABASE_SERVICE_KEY", "fake-service-key")

    # Mock the supabase import inside the function
    fake_client = object()
    fake_supabase = type("FakeSupabase", (), {
        "create_client": staticmethod(lambda url, key: fake_client),
    })()
    monkeypatch.setitem(__import__("sys").modules, "supabase", fake_supabase)

    # Should NOT raise ValueError about LAZYTHETA_USER_ID anymore
    client = mcp_server.get_supabase_client()
    assert client is fake_client


def test_set_robustness_impl_builds_and_persists(monkeypatch):
    import mcp_server
    saved = {}

    fake_cfg = {"company": "Meta", "ai_notes": {}, "robustness": {"overrides": {"management": "fragile"}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config", lambda *a, **k: fake_cfg)
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, **k: saved.update(cfg))
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(mcp_server.gather_data, "fetch_fundamentals", lambda *a, **k: {"years": [2025]})
    monkeypatch.setattr(mcp_server.gather_data, "apply_fundamentals_overrides", lambda f, o: f)
    monkeypatch.setattr(mcp_server, "_compute_fundamentals_headline",
                        lambda fund, cfg: {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                                           "latest_net_debt_ebitda": -0.4,
                                           "latest_adjusted_net_debt_m": -20000.0})

    axes = {"customers": {"band": "robust"}, "barriers": {"band": "robust"},
            "management": {"band": "robust"}, "industry": {"band": "robust"}}
    out = mcp_server._set_robustness_impl("META", axes, user_id="u1")

    assert "robustness" in saved
    assert "Robustness" in saved["ai_notes"]
    # override (management=fragile) wins over the passed 'robust' -> fragile verdict
    assert saved["robustness"]["verdict_mapped"] == "pass"
    assert "META" in out
