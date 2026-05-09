"""Tests for the LazyTheta MCP Cloud Run scaffold."""

import sys
from pathlib import Path

import pytest

# Add the cloudrun dir to sys.path so we can import its modules.
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))


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


def test_mcp_tools_list_returns_empty_list_in_scaffold():
    """Task 2 stub: tools/list returns []. Task 4 wires the actual 11 tools."""
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
    assert response.json()["result"]["tools"] == []


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
