"""Offline tests for tastytrade-mcp.

Focus: security regressions, OAuth helpers, MCP dispatcher logic, auth middleware.
External services (TastyTrade, Supabase) are not exercised — these tests cover
only the parts of the request flow that don't require them. Run: pytest -v.
"""

import base64
import hashlib
import os
import secrets

# Env vars must be set before any of the app modules are imported, since they
# read os.environ in module-level helpers (e.g. SUPABASE_URL in storage.py).
os.environ.setdefault("JWT_SIGNING_KEY", "test_" + secrets.token_hex(16))
os.environ.setdefault("TASTYTRADE_CLIENT_ID", "test-client-id")
os.environ.setdefault("TASTYTRADE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

import pytest
from starlette.testclient import TestClient

import oauth
from main import app


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _access_token(user_id: str = "user-1") -> str:
    return oauth.sign_jwt(
        {"type": "access_token", "user_id": user_id, "sub": user_id}, ttl_seconds=3600
    )


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def registered_client_id(client):
    resp = client.post(
        "/oauth/register",
        json={
            "redirect_uris": ["https://claude.ai/callback"],
            "client_name": "Test Client",
        },
    )
    assert resp.status_code == 200
    return resp.json()["client_id"]


# ─── OAuth helpers ──────────────────────────────────────────────────────────


def test_sign_verify_jwt_round_trip():
    token = oauth.sign_jwt({"hello": "world", "type": "test"}, ttl_seconds=60)
    payload = oauth.verify_jwt(token)
    assert payload is not None
    assert payload["hello"] == "world"
    assert payload["type"] == "test"


def test_verify_jwt_returns_none_for_invalid_token():
    assert oauth.verify_jwt("not.a.jwt") is None
    assert oauth.verify_jwt("") is None
    assert oauth.verify_jwt("a.b.c") is None


def test_pkce_s256_verification():
    verifier = secrets.token_urlsafe(64)
    challenge = _challenge(verifier)
    assert oauth._verify_pkce(verifier, challenge) is True
    assert oauth._verify_pkce(verifier, "wrong-challenge") is False
    assert oauth._verify_pkce("wrong-verifier", challenge) is False
    assert oauth._verify_pkce("", challenge) is False
    assert oauth._verify_pkce(verifier, "") is False


# ─── /oauth/authorize security regression tests ─────────────────────────────


def test_authorize_missing_client_id_rejected(client):
    resp = client.get(
        "/oauth/authorize",
        params={
            "redirect_uri": "https://claude.ai/cb",
            "state": "x",
            "code_challenge": "y",
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "missing client_id" in resp.text


def test_authorize_invalid_client_id_rejected(client):
    resp = client.get(
        "/oauth/authorize",
        params={
            "client_id": "fake",
            "redirect_uri": "https://claude.ai/cb",
            "state": "x",
            "code_challenge": "y",
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "invalid client_id" in resp.text


def test_authorize_mismatched_redirect_uri_rejected(client, registered_client_id):
    """Phishing attempt: client_id is valid, but redirect_uri isn't registered."""
    resp = client.get(
        "/oauth/authorize",
        params={
            "client_id": registered_client_id,
            "redirect_uri": "https://attacker.com/steal",
            "state": "x",
            "code_challenge": "y",
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "redirect_uri not registered" in resp.text


def test_authorize_valid_request_redirects_to_tastytrade(client, registered_client_id):
    resp = client.get(
        "/oauth/authorize",
        params={
            "client_id": registered_client_id,
            "redirect_uri": "https://claude.ai/callback",
            "state": "x",
            "code_challenge": "y",
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("https://my.tastytrade.com/auth.html")
    assert "client_id=test-client-id" in location
    assert "code_challenge_method=S256" in location


def test_authorize_rejects_non_s256_pkce(client, registered_client_id):
    resp = client.get(
        "/oauth/authorize",
        params={
            "client_id": registered_client_id,
            "redirect_uri": "https://claude.ai/callback",
            "state": "x",
            "code_challenge": "y",
            "code_challenge_method": "plain",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 400


# ─── /oauth/token ───────────────────────────────────────────────────────────


def test_token_unsupported_grant_type(client):
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "password"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


def test_token_invalid_code(client):
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "not-a-jwt",
            "code_verifier": "v",
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_token_redirect_uri_mismatch(client):
    verifier = secrets.token_urlsafe(64)
    auth_code = oauth.sign_jwt(
        {
            "type": "auth_code",
            "user_id": "user-1",
            "code_challenge": _challenge(verifier),
            "redirect_uri": "https://claude.ai/cb",
        },
        ttl_seconds=300,
    )
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": verifier,
            "redirect_uri": "https://attacker.com/cb",
        },
    )
    assert resp.status_code == 400


def test_token_pkce_mismatch(client):
    verifier = secrets.token_urlsafe(64)
    auth_code = oauth.sign_jwt(
        {
            "type": "auth_code",
            "user_id": "user-1",
            "code_challenge": _challenge(verifier),
            "redirect_uri": "https://claude.ai/cb",
        },
        ttl_seconds=300,
    )
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": "wrong-verifier",
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    assert resp.status_code == 400


def test_token_success_returns_jwt_with_user_id(client):
    verifier = secrets.token_urlsafe(64)
    auth_code = oauth.sign_jwt(
        {
            "type": "auth_code",
            "user_id": "user-42",
            "code_challenge": _challenge(verifier),
            "redirect_uri": "https://claude.ai/cb",
        },
        ttl_seconds=300,
    )
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "code_verifier": verifier,
            "redirect_uri": "https://claude.ai/cb",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    payload = oauth.verify_jwt(body["access_token"])
    assert payload["type"] == "access_token"
    assert payload["user_id"] == "user-42"


# ─── /mcp dispatcher ────────────────────────────────────────────────────────


def test_mcp_no_auth_returns_401_with_resource_metadata(client):
    resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
    assert resp.status_code == 401
    assert "oauth-protected-resource" in resp.headers["www-authenticate"]


def test_mcp_initialize(client):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 1,
            "params": {"protocolVersion": "2024-11-05"},
        },
    )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["serverInfo"]["name"] == "tastytrade-mcp"
    assert "tools" in result["capabilities"]


def test_mcp_tools_list_returns_eight_tools(client):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 2},
    )
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert len(tools) == 8
    assert {"get_positions", "get_account_balance", "get_option_chain"} <= names


def test_mcp_unknown_method(client):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"jsonrpc": "2.0", "method": "nonexistent/method", "id": 3},
    )
    assert resp.json()["error"]["code"] == -32601


def test_mcp_notification_returns_202(client):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert resp.status_code == 202


def test_mcp_ping(client):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 4},
    )
    assert resp.status_code == 200
    assert resp.json()["result"] == {}


def test_mcp_tools_call_unknown_tool(client):
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {_access_token()}"},
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 5,
            "params": {"name": "nonexistent", "arguments": {}},
        },
    )
    err = resp.json()["error"]
    assert err["code"] == -32602
    assert "Unknown tool" in err["message"]


# ─── Auth middleware ────────────────────────────────────────────────────────


def test_middleware_invalid_jwt_returns_401(client):
    resp = client.post(
        "/mcp",
        headers={"Authorization": "Bearer not.a.real.jwt"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    assert resp.status_code == 401


def test_middleware_jwt_without_user_id_returns_401(client):
    """A JWT must contain user_id with type=access_token; otherwise reject."""
    forged = oauth.sign_jwt({"type": "access_token"}, ttl_seconds=60)
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {forged}"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    assert resp.status_code == 401


def test_middleware_jwt_with_wrong_type_returns_401(client):
    """A non-access-token JWT (e.g. forged auth_code) must be rejected."""
    forged = oauth.sign_jwt(
        {"type": "auth_code", "user_id": "x"}, ttl_seconds=60
    )
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {forged}"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
    )
    assert resp.status_code == 401


def test_middleware_well_known_passes_without_auth(client):
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200
    assert "authorization_endpoint" in resp.json()


def test_middleware_oauth_register_passes_without_auth(client):
    resp = client.post(
        "/oauth/register",
        json={"redirect_uris": ["https://x.com"], "client_name": "test"},
    )
    assert resp.status_code == 200
    assert "client_id" in resp.json()
