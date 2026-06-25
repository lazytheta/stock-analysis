"""Tests for the LazyTheta MCP Cloud Run scaffold."""

import sys
from pathlib import Path

import pytest

# Add the cloudrun dir + repo root to sys.path so we can import both
# the cloudrun modules and the repo-root mcp_server module (which the
# Dockerfile copies into /app at build time).
HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _set_jwt_key(monkeypatch):
    monkeypatch.setenv("JWT_SIGNING_KEY", "test-key-not-for-production")


def test_jwt_round_trip():
    """sign_jwt + verify_jwt round-trip preserves claims."""
    from mcp_auth import sign_jwt, verify_jwt

    token = sign_jwt({"type": "access_token", "user_id": "abc"}, ttl_seconds=60)
    payload = verify_jwt(token)
    assert payload["type"] == "access_token"
    assert payload["user_id"] == "abc"


def test_jwt_invalid_token_returns_none():
    from mcp_auth import verify_jwt
    assert verify_jwt("not.a.token") is None
    assert verify_jwt("") is None


def test_jwt_expired_token_returns_none(monkeypatch):
    """A JWT past its expiry returns None."""
    from mcp_auth import sign_jwt, verify_jwt

    token = sign_jwt({"type": "access_token", "user_id": "abc"}, ttl_seconds=-1)
    # Already expired (ttl is -1 second). verify_jwt should return None.
    assert verify_jwt(token) is None


def test_health_endpoint_returns_ok():
    """GET /health returns 200 with status ok."""
    from starlette.testclient import TestClient
    from main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "lazytheta-mcp"}


def test_mcp_without_jwt_returns_401():
    """POST /mcp without Authorization header returns 401."""
    from starlette.testclient import TestClient
    from main import app

    client = TestClient(app)
    response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "ping", "id": 1})
    assert response.status_code == 401


def test_mcp_with_valid_jwt_passes_to_handler():
    """POST /mcp with a valid Bearer JWT reaches the handler."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    token = sign_jwt({"type": "access_token", "user_id": "smoke-uid"}, ttl_seconds=60)
    client = TestClient(app)
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 1
    assert body["result"] == {}


def test_mcp_initialize_returns_server_info():
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    token = sign_jwt({"type": "access_token", "user_id": "smoke-uid"}, ttl_seconds=60)
    client = TestClient(app)
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {"protocolVersion": "2024-11-05"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["result"]["serverInfo"]["name"] == "lazytheta-mcp"


def test_mcp_tools_list_returns_non_empty_list():
    """Task 4 wires the actual 11 tools — list is no longer empty."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    token = sign_jwt({"type": "access_token", "user_id": "smoke-uid"}, ttl_seconds=60)
    client = TestClient(app)
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    assert isinstance(tools, list) and len(tools) > 0


@pytest.fixture(autouse=True)
def _set_supabase_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "fake-anon-key")


def test_well_known_authorization_server():
    from starlette.testclient import TestClient
    from main import app

    client = TestClient(app)
    r = client.get("/.well-known/oauth-authorization-server",
                   headers={"x-forwarded-proto": "https",
                            "x-forwarded-host": "lazytheta-mcp.example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["issuer"] == "https://lazytheta-mcp.example.com"
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert body["token_endpoint"].endswith("/oauth/token")
    assert body["code_challenge_methods_supported"] == ["S256"]


def test_oauth_register_returns_signed_client_id():
    from starlette.testclient import TestClient
    from mcp_auth import verify_jwt
    from main import app

    client = TestClient(app)
    r = client.post("/oauth/register", json={
        "client_name": "claude-ai",
        "redirect_uris": ["https://claude.ai/oauth/callback"],
    })
    assert r.status_code == 200
    body = r.json()
    assert "client_id" in body
    payload = verify_jwt(body["client_id"])
    assert payload["type"] == "client"
    assert payload["redirect_uris"] == ["https://claude.ai/oauth/callback"]


def test_oauth_authorize_renders_login_page():
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    client_id = sign_jwt(
        {"type": "client", "redirect_uris": ["https://claude.ai/cb"]},
        ttl_seconds=3600,
    )
    client = TestClient(app)
    r = client.get("/oauth/authorize", params={
        "redirect_uri": "https://claude.ai/cb",
        "code_challenge": "abc123",
        "code_challenge_method": "S256",
        "client_id": client_id,
        "state": "claude-state-xyz",
    })
    assert r.status_code == 200
    assert "Magic link" in r.text
    assert "Wachtwoord" in r.text
    assert 'name="state_jwt"' in r.text


def test_oauth_authorize_rejects_unregistered_redirect_uri():
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    client_id = sign_jwt(
        {"type": "client", "redirect_uris": ["https://claude.ai/legit"]},
        ttl_seconds=3600,
    )
    client = TestClient(app)
    r = client.get("/oauth/authorize", params={
        "redirect_uri": "https://attacker.com/steal",
        "code_challenge": "abc123",
        "code_challenge_method": "S256",
        "client_id": client_id,
        "state": "x",
    })
    assert r.status_code == 400
    assert "redirect_uri not registered" in r.text


def test_oauth_authorize_password_happy_path(monkeypatch):
    """Submit email+password → Supabase login mocked → redirect to claude.ai with code."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt, verify_jwt
    from main import app

    state_jwt = sign_jwt(
        {
            "type": "auth_state",
            "claude_redirect": "https://claude.ai/cb",
            "claude_state": "claude-state",
            "claude_code_challenge": "claude-challenge",
        },
        ttl_seconds=600,
    )

    # Mock Supabase password login
    import mcp_auth

    class FakeResp:
        status_code = 200
        def json(self):
            return {
                "access_token": "sb-access",
                "user": {"id": "user-uuid-123", "email": "x@y.z"},
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", FakeClient)

    client = TestClient(app)
    # follow_redirects=False so we can inspect the redirect
    r = client.post(
        "/oauth/authorize/password",
        data={"email": "x@y.z", "password": "secret", "state_jwt": state_jwt},
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://claude.ai/cb")
    # Parse the code from the redirect URL
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(location).query)
    assert "code" in qs
    code_payload = verify_jwt(qs["code"][0])
    assert code_payload["type"] == "auth_code"
    assert code_payload["user_id"] == "user-uuid-123"


def test_oauth_authorize_password_invalid_credentials(monkeypatch):
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    state_jwt = sign_jwt(
        {
            "type": "auth_state",
            "claude_redirect": "https://claude.ai/cb",
            "claude_state": "x",
            "claude_code_challenge": "y",
        },
        ttl_seconds=600,
    )

    import mcp_auth

    class FakeResp:
        status_code = 400
        text = '{"error":"invalid_credentials"}'
        def json(self):
            return {"error": "invalid_credentials"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, *args, **kwargs):
            return FakeResp()

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", FakeClient)

    client = TestClient(app)
    r = client.post(
        "/oauth/authorize/password",
        data={"email": "x@y.z", "password": "wrong", "state_jwt": state_jwt},
    )
    assert r.status_code == 401


def test_oauth_token_exchanges_auth_code():
    """claude.ai POSTs the auth_code → we verify and issue access_token JWT."""
    import hashlib
    import base64
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt, verify_jwt
    from main import app

    # Mock claude PKCE pair
    code_verifier = "test-verifier-aaaaaaaaaaaaaaaaaaaaaaaa"
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    auth_code = sign_jwt(
        {
            "type": "auth_code",
            "user_id": "user-uuid-456",
            "code_challenge": code_challenge,
            "redirect_uri": "https://claude.ai/cb",
        },
        ttl_seconds=300,
    )

    client = TestClient(app)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": auth_code,
        "code_verifier": code_verifier,
        "redirect_uri": "https://claude.ai/cb",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "Bearer"
    payload = verify_jwt(body["access_token"])
    assert payload["type"] == "access_token"
    assert payload["user_id"] == "user-uuid-456"


def test_oauth_token_rejects_pkce_mismatch():
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    auth_code = sign_jwt(
        {
            "type": "auth_code",
            "user_id": "u1",
            "code_challenge": "challenge-from-real-claude",
            "redirect_uri": "https://claude.ai/cb",
        },
        ttl_seconds=300,
    )
    client = TestClient(app)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": auth_code,
        "code_verifier": "wrong-verifier-attacker-supplied",
        "redirect_uri": "https://claude.ai/cb",
    })
    assert r.status_code == 400
    assert "PKCE" in r.json()["error_description"]


def test_oauth_token_rejects_redirect_uri_mismatch():
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    auth_code = sign_jwt(
        {
            "type": "auth_code",
            "user_id": "u1",
            "code_challenge": "x",
            "redirect_uri": "https://claude.ai/legit",
        },
        ttl_seconds=300,
    )
    client = TestClient(app)
    r = client.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": auth_code,
        "code_verifier": "v",
        "redirect_uri": "https://attacker.com/steal",
    })
    assert r.status_code == 400
    assert "redirect_uri" in r.json()["error_description"]


def test_oauth_authorize_magic_triggers_supabase_otp(monkeypatch):
    """POST /oauth/authorize/magic → server calls Supabase OTP API → renders
    'check your mail' confirmation page."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    state_jwt = sign_jwt(
        {
            "type": "auth_state",
            "claude_redirect": "https://claude.ai/cb",
            "claude_state": "claude-state",
            "claude_code_challenge": "claude-challenge",
        },
        ttl_seconds=600,
    )

    import mcp_auth

    captured = {}

    class FakeResp:
        status_code = 200
        text = "{}"
        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeResp()

    monkeypatch.setattr(mcp_auth.httpx, "AsyncClient", FakeClient)

    client = TestClient(app)
    r = client.post(
        "/oauth/authorize/magic",
        data={"email": "user@example.com", "state_jwt": state_jwt},
    )
    assert r.status_code == 200
    assert "Check je mail" in r.text
    # Verify Supabase OTP endpoint was called with the email + redirect
    assert "/auth/v1/otp" in captured["url"]
    assert captured["json"]["email"] == "user@example.com"
    assert "/oauth/magic-callback" in captured["json"]["options"]["email_redirect_to"]


def test_oauth_magic_finalize_redirects_to_claude_with_code(monkeypatch):
    """POST /oauth/magic-finalize with a Supabase access_token → server
    verifies via Supabase /auth/v1/user → redirects to claude.ai with
    auth_code JWT containing user_id."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt, verify_jwt
    from main import app

    state_jwt = sign_jwt(
        {
            "type": "auth_state",
            "claude_redirect": "https://claude.ai/cb",
            "claude_state": "claude-state-yz",
            "claude_code_challenge": "claude-challenge-abc",
        },
        ttl_seconds=600,
    )

    import mcp_auth

    # Mock _verify_supabase_token to return a fake user_id (avoids needing
    # to also mock Supabase's /auth/v1/user endpoint).
    async def fake_verify(token):
        assert token == "sb-access-token-from-magic-link"
        return "user-uuid-from-magic"

    monkeypatch.setattr(mcp_auth, "_verify_supabase_token", fake_verify)

    client = TestClient(app)
    r = client.post(
        "/oauth/magic-finalize",
        data={
            "access_token": "sb-access-token-from-magic-link",
            "state_jwt": state_jwt,
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith("https://claude.ai/cb")
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(location).query)
    assert "code" in qs
    code_payload = verify_jwt(qs["code"][0])
    assert code_payload["type"] == "auth_code"
    assert code_payload["user_id"] == "user-uuid-from-magic"
    assert code_payload["redirect_uri"] == "https://claude.ai/cb"
    assert code_payload["code_challenge"] == "claude-challenge-abc"


def test_oauth_magic_finalize_rejects_invalid_supabase_token(monkeypatch):
    """If Supabase rejects the access_token, the server returns an HTML error,
    not a redirect to claude.ai."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    state_jwt = sign_jwt(
        {
            "type": "auth_state",
            "claude_redirect": "https://claude.ai/cb",
            "claude_state": "x",
            "claude_code_challenge": "y",
        },
        ttl_seconds=600,
    )

    import mcp_auth

    async def fake_verify(token):
        return None  # Supabase rejected

    monkeypatch.setattr(mcp_auth, "_verify_supabase_token", fake_verify)

    client = TestClient(app)
    r = client.post(
        "/oauth/magic-finalize",
        data={
            "access_token": "tampered-or-expired",
            "state_jwt": state_jwt,
        },
        follow_redirects=False,
    )
    assert r.status_code == 400
    assert "Supabase token-validatie" in r.text


# ---------------------------------------------------------------------------
# Task 4: Tool dispatcher tests
# ---------------------------------------------------------------------------


def test_tools_list_returns_24_tools():
    """tools/list returns 24 tools (incl. set_premortem + notification tools)."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    token = sign_jwt({"type": "access_token", "user_id": "u"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    assert len(tools) == 24
    names = {t["name"] for t in tools}
    assert names == {
        "build_dcf_config", "calculate_valuation", "calculate_multi_lens_valuation",
        "refresh_all_valuations", "save_to_watchlist", "get_config",
        "get_watchlist", "update_valuation_inputs", "update_lens_weights",
        "update_dcf_scenario_adjustments",
        "update_sotp_segments", "remove_sotp_segment", "set_sotp_corporate_overhead",
        "get_fundamentals", "update_fundamentals",
        "get_prescan_prompts", "get_prescan_sections", "save_prescan_section",
        "set_robustness", "set_premortem",
        "add_reminder", "list_reminders", "delete_reminder", "set_ticker_alert",
    }


def test_tools_call_get_watchlist_passes_user_id(monkeypatch):
    """tools/call -> get_watchlist routes user_id from JWT to _get_watchlist_impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(user_id=None):
        captured["user_id"] = user_id
        return '[]'
    monkeypatch.setattr(mcp_server, "_get_watchlist_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "get_watchlist", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    assert captured["user_id"] == "jwt-uid"


def test_tools_call_unknown_tool_returns_error():
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    token = sign_jwt({"type": "access_token", "user_id": "u"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {"name": "nonexistent_tool", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    body = r.json()
    assert body["error"]["code"] == -32602
    assert "Unknown tool" in body["error"]["message"]


def test_tools_call_update_valuation_inputs_passes_args(monkeypatch):
    """update_valuation_inputs receives ticker, fields, user_id correctly."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, fields, user_id=None):
        captured.update({"ticker": ticker, "fields": fields, "user_id": user_id})
        return '{"saved": true}'
    monkeypatch.setattr(mcp_server, "_update_valuation_inputs_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "update_valuation_inputs",
                "arguments": {"ticker": "PEP", "fields": {"dividend_5y_cagr": 0.08}},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {
        "ticker": "PEP",
        "fields": {"dividend_5y_cagr": 0.08},
        "user_id": "jwt-uid",
    }


def test_tools_call_update_lens_weights_passes_args(monkeypatch):
    """update_lens_weights receives ticker, weights, user_id correctly."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, weights, user_id=None):
        captured.update({"ticker": ticker, "weights": weights, "user_id": user_id})
        return '{"dividend": 0.20}'
    monkeypatch.setattr(mcp_server, "_update_lens_weights_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "update_lens_weights",
                "arguments": {"ticker": "PEP", "weights": {"dividend": 0.20}},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {
        "ticker": "PEP",
        "weights": {"dividend": 0.20},
        "user_id": "jwt-uid",
    }


def test_tools_call_save_prescan_section_passes_three_args(monkeypatch):
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, title, content, user_id=None):
        captured.update({
            "ticker": ticker, "title": title, "content": content, "user_id": user_id,
        })
        return '{"saved": "Test"}'
    monkeypatch.setattr(mcp_server, "_save_prescan_section_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "u"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "save_prescan_section",
                "arguments": {"ticker": "MSFT", "title": "Notes", "content": "content"},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {
        "ticker": "MSFT", "title": "Notes", "content": "content", "user_id": "u",
    }


def test_tools_call_update_sotp_segments_passes_args(monkeypatch):
    """update_sotp_segments routes ticker, segments, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, segments, user_id=None):
        captured.update({"ticker": ticker, "segments": segments, "user_id": user_id})
        return '{"segment_count": 1}'
    monkeypatch.setattr(mcp_server, "_update_sotp_segments_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "update_sotp_segments",
                "arguments": {
                    "ticker": "AMZN",
                    "segments": [{"name": "AWS", "ev_mid": 800000}],
                },
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {
        "ticker": "AMZN",
        "segments": [{"name": "AWS", "ev_mid": 800000}],
        "user_id": "jwt-uid",
    }


def test_tools_call_remove_sotp_segment_passes_args(monkeypatch):
    """remove_sotp_segment routes ticker, name, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, name, user_id=None):
        captured.update({"ticker": ticker, "name": name, "user_id": user_id})
        return '{"removed": 1}'
    monkeypatch.setattr(mcp_server, "_remove_sotp_segment_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "remove_sotp_segment",
                "arguments": {"ticker": "AMZN", "name": "AWS"},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {"ticker": "AMZN", "name": "AWS", "user_id": "jwt-uid"}


def test_tools_call_set_sotp_corporate_overhead_passes_args(monkeypatch):
    """set_sotp_corporate_overhead routes ticker, value, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, value, user_id=None):
        captured.update({"ticker": ticker, "value": value, "user_id": user_id})
        return '{"set": true}'
    monkeypatch.setattr(mcp_server, "_set_sotp_corporate_overhead_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "set_sotp_corporate_overhead",
                "arguments": {"ticker": "AMZN", "value": -5000},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {"ticker": "AMZN", "value": -5000, "user_id": "jwt-uid"}


def test_tools_call_get_fundamentals_passes_args(monkeypatch):
    """get_fundamentals routes ticker, n_years, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, n_years=10, user_id=None):
        captured.update({"ticker": ticker, "n_years": n_years, "user_id": user_id})
        return '{"raw": {}}'
    monkeypatch.setattr(mcp_server, "_get_fundamentals_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "get_fundamentals",
                "arguments": {"ticker": "MCD", "n_years": 5},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {"ticker": "MCD", "n_years": 5, "user_id": "jwt-uid"}


def test_tools_call_get_fundamentals_default_n_years(monkeypatch):
    """get_fundamentals without n_years uses default 10."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, n_years=10, user_id=None):
        captured.update({"n_years": n_years})
        return '{}'
    monkeypatch.setattr(mcp_server, "_get_fundamentals_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "u"}, ttl_seconds=60)
    client = TestClient(app)
    client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0", "method": "tools/call", "id": 1,
            "params": {"name": "get_fundamentals", "arguments": {"ticker": "AAPL"}},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert captured["n_years"] == 10


def test_tools_call_update_fundamentals_passes_args(monkeypatch):
    """update_fundamentals routes ticker, overrides, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, overrides, user_id=None):
        captured.update({"ticker": ticker, "overrides": overrides, "user_id": user_id})
        return '{"field_count": 1}'
    monkeypatch.setattr(mcp_server, "_update_fundamentals_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    overrides_payload = {"operating_lease_liabilities": {"2024": 12500}}
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "update_fundamentals",
                "arguments": {"ticker": "MCD", "overrides": overrides_payload},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {
        "ticker": "MCD",
        "overrides": overrides_payload,
        "user_id": "jwt-uid",
    }
