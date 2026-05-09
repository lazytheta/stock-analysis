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
    from auth import sign_jwt, verify_jwt

    token = sign_jwt({"type": "access_token", "user_id": "abc"}, ttl_seconds=60)
    payload = verify_jwt(token)
    assert payload["type"] == "access_token"
    assert payload["user_id"] == "abc"


def test_jwt_invalid_token_returns_none():
    from auth import verify_jwt
    assert verify_jwt("not.a.token") is None
    assert verify_jwt("") is None


def test_jwt_expired_token_returns_none(monkeypatch):
    """A JWT past its expiry returns None."""
    from auth import sign_jwt, verify_jwt

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
    from auth import sign_jwt
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
    from auth import sign_jwt
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
    from auth import sign_jwt
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
