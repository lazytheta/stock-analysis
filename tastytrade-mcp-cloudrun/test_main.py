"""Tests for bearer token auth middleware."""

import os
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _make_app(token: str) -> Starlette:
    """Build a test Starlette app with our auth middleware."""
    from main import BearerTokenMiddleware

    async def homepage(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/", homepage)])
    app.add_middleware(BearerTokenMiddleware, token=token)
    return app


def test_valid_token_passes():
    app = _make_app("secret-token-123")
    client = TestClient(app)
    resp = client.get("/", headers={"Authorization": "Bearer secret-token-123"})
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_missing_auth_header_returns_401():
    app = _make_app("secret-token-123")
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 401


def test_wrong_token_returns_401():
    app = _make_app("secret-token-123")
    client = TestClient(app)
    resp = client.get("/", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_non_bearer_scheme_returns_401():
    app = _make_app("secret-token-123")
    client = TestClient(app)
    resp = client.get("/", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert resp.status_code == 401
