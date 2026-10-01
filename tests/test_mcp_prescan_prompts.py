"""get_prescan_prompts seeds the user's prompt library from the shipped
defaults (the Pre-Scan tab used to do this). Offline: Supabase is mocked."""
from unittest.mock import MagicMock

import pytest

import prescan_prompts

DEFAULT_TITLES = [p["title"] for p in prescan_prompts.DEFAULT_AI_PROMPTS]


@pytest.fixture
def mcp(monkeypatch):
    import mcp_server

    state = {"saved": [], "prefs": {}, "fail_read": False}

    def fake_load_config(client, ticker, user_id=None):
        return {"company": "Test Co", "ai_notes": {"Moat Analysis": "wide moat"}}

    def fake_load_prefs(client, user_id=None, raise_errors=False):
        if state["fail_read"]:
            if raise_errors:
                raise RuntimeError("supabase down")
            return {"delta_min": 0.2}
        return dict(state["prefs"])

    def fake_save_prefs(client, prefs, user_id=None):
        state["saved"].append((prefs, user_id))

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load_config)
    monkeypatch.setattr(mcp_server.config_store, "load_user_prefs", fake_load_prefs)
    monkeypatch.setattr(mcp_server.config_store, "save_user_prefs", fake_save_prefs)
    return mcp_server, state


def test_empty_library_is_seeded_and_saved(mcp):
    mcp_server, state = mcp
    state["prefs"] = {"delta_min": 0.2, "strategy_start": "2026-07-28"}
    out = mcp_server._get_prescan_prompts_impl("TEST", user_id="u1")
    assert isinstance(out, list)
    assert [e["title"] for e in out] == DEFAULT_TITLES
    assert "Test Co (TEST)" in next(e["prompt"] for e in out if e["title"] == "Robustness")
    assert len(state["saved"]) == 1
    saved, uid = state["saved"][0]
    assert uid == "u1"
    assert [p["title"] for p in saved["ai_prompts"]] == DEFAULT_TITLES
    # Other prefs survive the write.
    assert saved["strategy_start"] == "2026-07-28" and saved["delta_min"] == 0.2


def test_missing_default_is_added_and_custom_text_kept(mcp):
    mcp_server, state = mcp
    lib = [{"title": t, "prompt": f"custom {t} {{ticker}}"} for t in DEFAULT_TITLES
           if t != "Risk Cards"]
    state["prefs"] = {"ai_prompts": lib}
    out = mcp_server._get_prescan_prompts_impl("TEST", user_id="u1")
    titles = [e["title"] for e in out]
    assert titles == DEFAULT_TITLES
    assert next(e["prompt"] for e in out if e["title"] == "Moat Analysis") == "custom Moat Analysis TEST"
    assert len(state["saved"]) == 1


def test_complete_library_is_not_saved_again(mcp):
    mcp_server, state = mcp
    state["prefs"] = {"ai_prompts": [{"title": t, "prompt": "p"} for t in DEFAULT_TITLES]}
    out = mcp_server._get_prescan_prompts_impl("TEST", user_id="u1")
    assert len(out) == len(DEFAULT_TITLES)
    assert state["saved"] == []


def test_failed_read_serves_defaults_without_overwriting_prefs(mcp):
    """load_user_prefs swallows read errors and returns bare defaults; writing
    a seeded library over that would wipe the user's real prefs."""
    mcp_server, state = mcp
    state["fail_read"] = True
    out = mcp_server._get_prescan_prompts_impl("TEST", user_id="u1")
    assert [e["title"] for e in out] == DEFAULT_TITLES
    assert state["saved"] == []


def test_no_empty_library_error_any_more(mcp):
    mcp_server, _state = mcp
    out = mcp_server._get_prescan_prompts_impl("TEST", user_id="u1")
    assert not (isinstance(out, dict) and "error" in out)


def test_load_user_prefs_raise_errors_flag():
    import config_store

    client = MagicMock()
    client.table.side_effect = RuntimeError("boom")
    prefs = config_store.load_user_prefs(client, user_id="u1")
    assert prefs["delta_min"] == 0.20  # swallowed, defaults
    with pytest.raises(RuntimeError):
        config_store.load_user_prefs(client, user_id="u1", raise_errors=True)
