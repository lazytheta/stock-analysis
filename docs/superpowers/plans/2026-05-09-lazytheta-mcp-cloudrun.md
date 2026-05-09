# LazyTheta MCP Cloud Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the LazyTheta DCF MCP (11 tools) as a multi-user remote service on Cloud Run with an OAuth bridge that authenticates users against their existing Supabase Auth account.

**Architecture:** Starlette ASGI app + custom JSON-RPC dispatcher + OAuth 2.1 + PKCE bridge to Supabase Auth, deployed to Cloud Run in `europe-west4`. The Cloud Run handler imports `_*_impl` functions directly from the existing `mcp_server.py` (multi-user-ified to accept `user_id` per request). claude.ai connector authenticates once per user via magic-link or password, persists JWT, then calls each tool with `Authorization: Bearer <jwt>`.

**Tech Stack:** Starlette, uvicorn, httpx, pyjwt, python-multipart, Supabase Auth (REST), pandas (existing), yfinance (existing), GCP Cloud Run + Artifact Registry.

**Spec:** `docs/superpowers/specs/2026-05-09-lazytheta-mcp-cloudrun-design.md`

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `mcp_server.py` | Existing local stdio MCP | Add optional `user_id` parameter to each of 11 `_*_impl` functions; default falls back to env var |
| `lazytheta-mcp-cloudrun/main.py` | Starlette ASGI app + SmartAuthMiddleware + route table | NEW |
| `lazytheta-mcp-cloudrun/auth.py` | JWT helpers, OAuth metadata, Supabase Auth bridge | NEW |
| `lazytheta-mcp-cloudrun/mcp_handler.py` | JSON-RPC dispatcher, 11 tool wrappers, TOOLS schema list | NEW |
| `lazytheta-mcp-cloudrun/requirements.txt` | Python deps | NEW |
| `lazytheta-mcp-cloudrun/README.md` | Local dev + deploy instructions | NEW |
| `lazytheta-mcp-cloudrun/test_app.py` | Unit tests (mocked Supabase + JWT round-trip) | NEW |
| `Dockerfile` (repo root) | Cloud Run container build | NEW |
| `.gcloudignore` (repo root) | Excludes from gcloud upload | NEW |
| `tests/test_mcp_server_user_id.py` | Tests for multi-user-ified impl functions | NEW |

5 sub-tasks across 1 shared feature branch `feature/lazytheta-mcp-cloudrun`.

---

## Task 1: Multi-user-ify `mcp_server.py` impl functions

**Why first:** the Cloud Run handler will import these `_*_impl` functions and pass per-request `user_id` from the JWT. They currently use a module-level `USER_ID = os.environ["LAZYTHETA_USER_ID"]`. We add an optional parameter that defaults to the env var (preserving stdio MCP behavior).

**Files:**
- Modify: `mcp_server.py` (11 `_*_impl` functions + 11 `@mcp.tool` wrappers that call them)
- Create: `tests/test_mcp_server_user_id.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_server_user_id.py`:

```python
"""Tests that the _*_impl functions accept an explicit user_id parameter
and fall back to the module-level USER_ID env var when omitted."""
from unittest.mock import MagicMock

import pytest


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
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_mcp_server_user_id.py -v`
Expected: FAIL — `TypeError: _get_watchlist_impl() got an unexpected keyword argument 'user_id'` (and similar for the other 8 functions).

- [ ] **Step 3: Update each `_*_impl` function in `mcp_server.py` to accept `user_id`**

Apply this transformation to ALL 11 impl functions. Pattern: add `user_id: str | None = None` as the LAST keyword argument; at the function top, set `user_id = user_id or USER_ID`; pass `user_id` everywhere `USER_ID` was passed.

Update `_build_dcf_config_impl` (line 125):

```python
def _build_dcf_config_impl(ticker, financial_data, company_name,
                          sic_code=None, sic_description="",
                          margin_of_safety=None, terminal_growth=None,
                          sector_margin=None, consensus=None,
                          valuation_basis="nominal",
                          user_id: str | None = None):
    # build_dcf_config doesn't touch Supabase directly, but for consistency
    # we accept user_id (unused here; future-proofs the signature).
    user_id = user_id or USER_ID
    # ... existing body unchanged ...
```

Update `_calculate_valuation_impl` (line 192):

```python
def _calculate_valuation_impl(cfg, user_id: str | None = None):
    user_id = user_id or USER_ID  # unused but signature-consistent
    # ... existing body unchanged ...
```

Update `_calculate_multi_lens_valuation_impl` (line 222):

```python
def _calculate_multi_lens_valuation_impl(ticker, scenario_grid=False,
                                          user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})

    cfg.setdefault("ticker", ticker)
    auto_fetch.auto_fill_valuation_inputs(cfg)
    auto_fetch.auto_fill_peer_market_data(cfg)
    auto_fetch.auto_fill_dividend_inputs(cfg)

    summary = valuation_lenses.calculate_multi_lens_valuation(
        cfg, scenario_grid=scenario_grid
    )
    cfg["valuation_summary"] = summary
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps(summary, default=str)
```

Update `_refresh_all_valuations_impl` (line 247):

```python
def _refresh_all_valuations_impl(force: bool = False,
                                  user_id: str | None = None) -> str:
    """[existing docstring]"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import UTC, datetime, timedelta

    user_id = user_id or USER_ID
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=user_id)
    tickers = [e["ticker"] for e in entries]

    threshold = datetime.now(UTC) - timedelta(days=7)
    # ... rest unchanged except: every config_store.load_config / save_config /
    # list_watchlist call passes user_id=user_id (already does via the variable above)
    # ... and the inner _refresh_one(ticker) closure already uses the outer user_id ...
```

For `_refresh_all_valuations_impl`, the inner `_load(t)` and `_refresh_one(ticker)` closures need to pass `user_id=user_id` instead of `user_id=USER_ID`. Adjust those two call sites within the function body.

Update `_save_to_watchlist_impl` (line 320):

```python
def _save_to_watchlist_impl(ticker, cfg, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return f"Saved {ticker.upper()} to watchlist."
```

Update `_get_config_impl` (line 327):

```python
def _get_config_impl(ticker, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})
    return json.dumps(cfg, default=str)
```

Update `_get_watchlist_impl` (line 336):

```python
def _get_watchlist_impl(user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=user_id)
    return json.dumps(entries, default=str)
```

Update `_update_valuation_inputs_impl` (line 343):

```python
def _update_valuation_inputs_impl(ticker: str, fields: dict,
                                   user_id: str | None = None) -> str:
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    inputs = cfg.setdefault("valuation_inputs", {})
    auto_filled = list(inputs.get("_auto_filled", []))
    for k, v in fields.items():
        inputs[k] = v
        if k in auto_filled:
            auto_filled.remove(k)
    inputs["_auto_filled"] = auto_filled

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps(inputs, default=str)
```

Update `_get_prescan_prompts_impl` (line 603):

```python
def _get_prescan_prompts_impl(ticker, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    # ... rest unchanged ...
```

Update `_get_prescan_sections_impl` (line 632):

```python
def _get_prescan_sections_impl(ticker, user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    # ... rest unchanged ...
```

Update `_save_prescan_section_impl` (line 643):

```python
def _save_prescan_section_impl(ticker, title, content,
                                user_id: str | None = None):
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})
    ai_notes = cfg.setdefault("ai_notes", {})
    ai_notes[title] = content
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({"saved": title, "ticker": ticker.upper()})
```

The `@mcp.tool()` wrappers (e.g. `build_dcf_config`, `calculate_valuation`, etc., starting around line 369) do NOT need changes — they continue to call the impl functions without passing `user_id`, so the env-var fallback kicks in for stdio MCP usage.

- [ ] **Step 4: Run the new tests to confirm they pass**

Run: `python3 -m pytest tests/test_mcp_server_user_id.py -v`
Expected: All 9 tests PASS.

- [ ] **Step 5: Run the existing mcp_server tests to confirm no regressions**

Run: `python3 -m pytest test_mcp_server.py -v`
Expected: 22 PASS (the existing tests don't pass `user_id`, so they hit the env-var fallback path — should still work identically).

- [ ] **Step 6: Run the full multi-lens + market-data suites**

Run: `python3 -m pytest tests/ -v`
Expected: All previously passing tests still pass.

- [ ] **Step 7: Commit**

```bash
git checkout -b feature/lazytheta-mcp-cloudrun
git add mcp_server.py tests/test_mcp_server_user_id.py
git commit -m "$(cat <<'EOF'
refactor(mcp): multi-user-ify _*_impl functions

Each _*_impl function now accepts an optional user_id parameter and
falls back to the module-level USER_ID env var when omitted. This
preserves stdio MCP behavior (single-user via env var) while enabling
the upcoming Cloud Run handler to pass per-request user_id from a JWT.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Scaffold Cloud Run service (smoke-only)

**Why:** stand up a Starlette app with `SmartAuthMiddleware`, `/health` endpoint, and a stub `/mcp` that proves the import chain works end-to-end. No real auth, no real tools yet — just the container-ready scaffolding.

**Files:**
- Create: `lazytheta-mcp-cloudrun/main.py`
- Create: `lazytheta-mcp-cloudrun/mcp_handler.py`
- Create: `lazytheta-mcp-cloudrun/auth.py`
- Create: `lazytheta-mcp-cloudrun/requirements.txt`
- Create: `lazytheta-mcp-cloudrun/test_app.py`
- Create: `Dockerfile` (repo root)
- Create: `.gcloudignore` (repo root)

- [ ] **Step 1: Create `requirements.txt`**

`lazytheta-mcp-cloudrun/requirements.txt`:

```
starlette==0.40.0
uvicorn==0.30.6
httpx==0.27.2
pyjwt==2.9.0
python-multipart==0.0.12
supabase==2.7.4
pandas==2.2.3
yfinance==0.2.51
sec-edgar-downloader==5.0.3
```

(The pandas + yfinance + sec-edgar deps come in via `mcp_server.py`'s import chain; they need to be in this requirements.txt so the Docker build resolves them.)

- [ ] **Step 2: Create minimal `auth.py` with JWT helpers**

`lazytheta-mcp-cloudrun/auth.py`:

```python
"""JWT signing + verification helpers. OAuth bridge functions added in Task 3."""

from __future__ import annotations

import os
import time

import jwt


def _signing_key() -> str:
    key = os.environ.get("JWT_SIGNING_KEY", "")
    if not key:
        raise RuntimeError("JWT_SIGNING_KEY env var not configured")
    return key


def sign_jwt(claims: dict, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, _signing_key(), algorithm="HS256")


def verify_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except Exception:
        return None
```

- [ ] **Step 3: Create stub `mcp_handler.py`**

`lazytheta-mcp-cloudrun/mcp_handler.py`:

```python
"""Stateless MCP JSON-RPC dispatcher. Stub: only handles initialize + ping +
tools/list with empty list. Tools are wired in Task 4."""

from __future__ import annotations

import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lazytheta-mcp"
SERVER_VERSION = "0.1.0"

logger = logging.getLogger(__name__)


async def _handle_one(message: dict, user_id: str | None) -> dict | None:
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled", "notifications/progress"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": []}}

    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


async def mcp_endpoint(request: Request) -> Response:
    try:
        if request.method == "GET":
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600, "message": "GET not supported"}},
                status_code=405,
            )
        if request.method == "DELETE":
            return Response(status_code=200)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        user_id = request.scope.get("state", {}).get("user_id")

        if isinstance(body, list):
            responses = []
            for msg in body:
                r = await _handle_one(msg, user_id)
                if r is not None:
                    responses.append(r)
            if not responses:
                return Response(status_code=202)
            return JSONResponse(responses)

        if not isinstance(body, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600, "message": "Invalid request"}},
                status_code=400,
            )

        response = await _handle_one(body, user_id)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)
    except Exception:
        logger.exception("mcp_endpoint failed")
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32603, "message": "Internal server error"}},
            status_code=500,
        )
```

- [ ] **Step 4: Create `main.py` with Starlette app + middleware**

`lazytheta-mcp-cloudrun/main.py`:

```python
"""LazyTheta DCF MCP Server -- multi-user, OAuth bridge to Supabase Auth.

Routes (filled in across Tasks 2-4):
- /health                                    Liveness probe
- /mcp                                       MCP JSON-RPC (auth: JWT with user_id)
- /.well-known/oauth-authorization-server    OAuth metadata (Task 3)
- /.well-known/oauth-protected-resource      Resource metadata (Task 3)
- /oauth/register                            Dynamic Client Registration (Task 3)
- /oauth/authorize                           claude.ai entry → Supabase login (Task 3)
- /oauth/magic-callback                      Supabase magic-link return (Task 3)
- /oauth/token                               claude.ai exchanges code for access token (Task 3)

Every authenticated request carries a JWT issued after a per-user Supabase
Auth flow. SmartAuthMiddleware extracts user_id from the JWT and stashes it
in scope["state"] for downstream handlers.
"""

import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from auth import verify_jwt
from mcp_handler import mcp_endpoint


PUBLIC_PREFIXES = ("/oauth/", "/.well-known/", "/health")


class SmartAuthMiddleware:
    """Pure ASGI middleware. Public paths pass through; for everything else,
    a Bearer JWT is required. user_id from the JWT is stashed in scope so
    inner handlers can read it.
    """

    def __init__(self, app):
        self.app = app

    async def _passthrough(self, scope, receive, send):
        try:
            return await self.app(scope, receive, send)
        except Exception:
            import sys
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                await send({
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"error":"internal_server_error"}',
                })
            except Exception:
                pass

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await self._passthrough(scope, receive, send)

        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode("latin-1")

        if auth.startswith("Bearer "):
            token = auth[7:]
            payload = verify_jwt(token)
            if payload and payload.get("type") == "access_token" and payload.get("user_id"):
                scope.setdefault("state", {})["user_id"] = payload["user_id"]
                return await self._passthrough(scope, receive, send)

        host = headers.get(b"x-forwarded-host", headers.get(b"host", b"")).decode("latin-1")
        proto = headers.get(b"x-forwarded-proto", b"https").decode("latin-1")
        resource_metadata = f"{proto}://{host}/.well-known/oauth-protected-resource"
        www_auth = f'Bearer resource_metadata="{resource_metadata}"'

        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"www-authenticate", www_auth.encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": b"Unauthorized"})


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "lazytheta-mcp"})


def create_app():
    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/mcp", mcp_endpoint, methods=["POST", "GET", "DELETE"]),
    ]
    starlette_app = Starlette(routes=routes)
    return SmartAuthMiddleware(starlette_app)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

- [ ] **Step 5: Create `Dockerfile` at repo root**

`Dockerfile` (in `/Users/administrator/Documents/github/stock-analysis/`):

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY lazytheta-mcp-cloudrun/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Cloud Run handler files
COPY lazytheta-mcp-cloudrun/main.py \
     lazytheta-mcp-cloudrun/auth.py \
     lazytheta-mcp-cloudrun/mcp_handler.py \
     /app/

# Shared modules from repo root, imported by mcp_server.py's _*_impl chain
COPY mcp_server.py auto_fetch.py valuation_lenses.py \
     config_store.py dcf_calculator.py gather_data.py \
     scorecard_utils.py /app/

EXPOSE 8080
ENV PORT=8080

CMD ["python", "main.py"]
```

- [ ] **Step 6: Create `.gcloudignore` at repo root**

`.gcloudignore` (in `/Users/administrator/Documents/github/stock-analysis/`):

```
# Don't upload these to Cloud Build
.git/
.gitignore
.gcloudignore
.github/
.streamlit/
.claude/
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
node_modules/
*.log

# App-specific files NOT needed by the MCP service
streamlit_app.py
oauth-server/
tastytrade-mcp-cloudrun/
configs/
docs/
tests/
test_*.py
demo_*.py
scripts/
*.md
NOTES*

# Required (do NOT exclude these):
# - mcp_server.py, auto_fetch.py, valuation_lenses.py, config_store.py,
#   dcf_calculator.py, gather_data.py, scorecard_utils.py
# - lazytheta-mcp-cloudrun/
# - Dockerfile
# - requirements.txt (handled by Dockerfile copy)
```

- [ ] **Step 7: Create initial `test_app.py` with smoke tests**

`lazytheta-mcp-cloudrun/test_app.py`:

```python
"""Tests for the LazyTheta MCP Cloud Run scaffold."""

import os
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
    import time

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
```

- [ ] **Step 8: Run the tests**

Run from the cloudrun dir:

```bash
cd /Users/administrator/Documents/github/stock-analysis/lazytheta-mcp-cloudrun
python3 -m pytest test_app.py -v
```

Expected: 8 PASS.

If `starlette.testclient` isn't installed, install via the requirements: `pip install -r requirements.txt`.

- [ ] **Step 9: Local smoke test**

Run from the cloudrun dir:

```bash
JWT_SIGNING_KEY=test-key-local python3 main.py &
sleep 2
curl -s http://localhost:8080/health
kill %1
```

Expected output: `{"status":"ok","service":"lazytheta-mcp"}`.

- [ ] **Step 10: Commit**

```bash
git add lazytheta-mcp-cloudrun/ Dockerfile .gcloudignore
git commit -m "$(cat <<'EOF'
feat(cloudrun): scaffold LazyTheta MCP Cloud Run service

Starlette ASGI app + SmartAuthMiddleware + /health + /mcp stub
(only initialize/ping/tools/list with empty list — actual tools
wired in Task 4). JWT helpers in auth.py. Dockerfile at repo root
for multi-module builds (mcp_server.py + supporting modules from
repo root, plus the cloudrun handler files). 8 passing tests.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: OAuth bridge + Supabase Auth integration

**Why:** the SmartAuthMiddleware verifies JWTs but no flow yet exists to issue them. Build the OAuth 2.1 + PKCE bridge between claude.ai and Supabase Auth.

**Files:**
- Modify: `lazytheta-mcp-cloudrun/auth.py` (extend with OAuth handlers + Supabase client)
- Modify: `lazytheta-mcp-cloudrun/main.py` (add OAuth routes)
- Modify: `lazytheta-mcp-cloudrun/test_app.py` (OAuth flow tests)

- [ ] **Step 1: Extend `auth.py` with OAuth handlers**

Replace `lazytheta-mcp-cloudrun/auth.py` with:

```python
"""JWT helpers + OAuth 2.1 + PKCE bridge to Supabase Auth.

claude.ai → /oauth/authorize → user logs in via Supabase (magic link or
password) → /oauth/magic-callback → /oauth/token → access-token-JWT.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response


ACCESS_TOKEN_TTL = 30 * 24 * 3600  # 30 days
AUTH_CODE_TTL = 5 * 60               # 5 minutes
STATE_TTL = 15 * 60                  # 15 min — enough for magic-link email roundtrip


def _signing_key() -> str:
    key = os.environ.get("JWT_SIGNING_KEY", "")
    if not key:
        raise RuntimeError("JWT_SIGNING_KEY env var not configured")
    return key


def _supabase_url() -> str:
    v = os.environ.get("SUPABASE_URL", "")
    if not v:
        raise RuntimeError("SUPABASE_URL env var not configured")
    return v.rstrip("/")


def _supabase_anon_key() -> str:
    v = os.environ.get("SUPABASE_ANON_KEY", "")
    if not v:
        raise RuntimeError("SUPABASE_ANON_KEY env var not configured")
    return v


def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.url.netloc
    return f"{proto}://{host}"


def sign_jwt(claims: dict, ttl_seconds: int) -> str:
    now = int(time.time())
    payload = {**claims, "iat": now, "exp": now + ttl_seconds}
    return jwt.encode(payload, _signing_key(), algorithm="HS256")


def verify_jwt(token: str) -> dict | None:
    try:
        return jwt.decode(token, _signing_key(), algorithms=["HS256"])
    except Exception:
        return None


def _verify_pkce(verifier: str, challenge: str) -> bool:
    if not verifier or not challenge:
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return hmac.compare_digest(expected, challenge)


# ---- OAuth metadata ----


async def well_known_authorization_server(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["mcp"],
    })


async def well_known_protected_resource(request: Request) -> JSONResponse:
    base = _base_url(request)
    return JSONResponse({
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mcp"],
    })


# ---- Dynamic Client Registration ----


async def oauth_register(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        body = {}
    redirect_uris = body.get("redirect_uris") or []
    client_name = body.get("client_name", "anonymous")
    client_id = sign_jwt(
        {"type": "client", "client_name": client_name, "redirect_uris": redirect_uris},
        ttl_seconds=10 * 365 * 24 * 3600,
    )
    return JSONResponse({
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": redirect_uris,
        "client_name": client_name,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    })


# ---- /oauth/authorize: render login page ----

_LOGIN_HTML = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LazyTheta — log in</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:420px;margin:64px auto;padding:0 16px;color:#111;}}
h1{{margin:0 0 8px;font-size:1.4rem;}}
p.sub{{color:#666;margin:0 0 24px;}}
.tab-group{{display:flex;gap:4px;background:#f4f4f5;padding:4px;border-radius:8px;margin-bottom:16px;}}
.tab{{flex:1;padding:8px;text-align:center;border-radius:6px;cursor:pointer;font-size:0.9rem;color:#444;}}
.tab.active{{background:#fff;color:#111;font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,0.06);}}
form{{display:none;flex-direction:column;gap:12px;}}
form.active{{display:flex;}}
label{{font-size:0.85rem;color:#444;}}
input{{padding:10px;border:1px solid #d4d4d8;border-radius:8px;font-size:1rem;}}
button{{padding:12px;background:#6e8a76;color:#fff;border:0;border-radius:8px;font-size:1rem;cursor:pointer;font-weight:600;}}
button:hover{{background:#5a7561;}}
.err{{background:#fff5f5;border:1px solid #fdb;padding:12px;border-radius:8px;color:#900;margin-bottom:16px;font-size:0.9rem;}}
.note{{color:#888;font-size:0.85rem;margin-top:16px;}}
</style></head><body>
<h1>LazyTheta MCP</h1>
<p class="sub">Log in met je lazytheta.io account.</p>
{error_block}
<div class="tab-group">
  <div class="tab active" data-tab="magic">Magic link</div>
  <div class="tab" data-tab="password">Wachtwoord</div>
</div>
<form id="magic-form" class="active" method="post" action="/oauth/authorize/magic">
  <input type="hidden" name="state_jwt" value="{state_jwt}">
  <label>Email</label>
  <input type="email" name="email" required autofocus>
  <button type="submit">Stuur magic link</button>
</form>
<form id="password-form" method="post" action="/oauth/authorize/password">
  <input type="hidden" name="state_jwt" value="{state_jwt}">
  <label>Email</label>
  <input type="email" name="email" required>
  <label>Wachtwoord</label>
  <input type="password" name="password" required>
  <button type="submit">Inloggen</button>
</form>
<p class="note">Geen account? Registreer eerst op <a href="https://lazytheta.io">lazytheta.io</a>.</p>
<script>
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', e => {{
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  e.target.classList.add('active');
  document.querySelectorAll('form').forEach(f => f.classList.remove('active'));
  document.getElementById(e.target.dataset.tab + '-form').classList.add('active');
}}));
</script>
</body></html>"""


def _html_error(message: str, status_code: int = 400) -> HTMLResponse:
    safe = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    body = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><title>OAuth fout</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:-apple-system,system-ui,sans-serif;max-width:480px;margin:64px auto;padding:0 16px;color:#111;}}
h2{{margin:0 0 12px;}} p{{color:#333;}} .err{{background:#fff5f5;border:1px solid #fdb;padding:12px;border-radius:8px;color:#900;}}
</style></head><body><h2>Iets ging mis</h2><div class="err">{safe}</div>
<p style="margin-top:24px;color:#888;">Sluit dit venster en probeer het opnieuw vanuit Claude.</p>
</body></html>"""
    return HTMLResponse(body, status_code=status_code)


async def oauth_authorize(request: Request) -> Response:
    """GET /oauth/authorize?redirect_uri=&code_challenge=&state=&client_id=
    Renders login page (magic link + password tabs)."""
    qp = request.query_params
    claude_redirect = qp.get("redirect_uri", "")
    claude_state = qp.get("state", "")
    claude_code_challenge = qp.get("code_challenge", "")
    claude_code_challenge_method = qp.get("code_challenge_method", "S256")
    claude_client_id = qp.get("client_id", "")

    if not claude_redirect:
        return _html_error("missing redirect_uri")
    if not claude_client_id:
        return _html_error("missing client_id")
    if claude_code_challenge_method != "S256":
        return _html_error("only PKCE S256 is supported")

    client_payload = verify_jwt(claude_client_id)
    if not client_payload or client_payload.get("type") != "client":
        return _html_error("invalid client_id")
    registered_uris = client_payload.get("redirect_uris") or []
    if not registered_uris or claude_redirect not in registered_uris:
        return _html_error("redirect_uri not registered for this client")

    state_jwt = sign_jwt(
        {
            "type": "auth_state",
            "claude_redirect": claude_redirect,
            "claude_state": claude_state,
            "claude_code_challenge": claude_code_challenge,
        },
        ttl_seconds=STATE_TTL,
    )

    return HTMLResponse(_LOGIN_HTML.format(state_jwt=state_jwt, error_block=""))


# ---- Magic link path: send OTP, redirect to claude.ai after callback ----


async def oauth_authorize_magic(request: Request) -> Response:
    """POST /oauth/authorize/magic — user submitted email; trigger Supabase OTP."""
    form = await request.form()
    email = form.get("email", "").strip()
    state_jwt = form.get("state_jwt", "")

    state = verify_jwt(state_jwt)
    if not state or state.get("type") != "auth_state":
        return _html_error("ongeldige of verlopen state")
    if not email:
        return _html_error("email vereist")

    base = _base_url(request)
    callback = f"{base}/oauth/magic-callback?state={state_jwt}"

    # Trigger Supabase magic link; the email link redirects to our callback.
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{_supabase_url()}/auth/v1/otp",
                json={"email": email, "options": {"email_redirect_to": callback}},
                headers={
                    "apikey": _supabase_anon_key(),
                    "Content-Type": "application/json",
                },
            )
    except Exception as e:
        return _html_error(f"kon Supabase niet bereiken: {e}", status_code=502)

    if resp.status_code >= 400:
        return _html_error(
            f"Supabase OTP-aanvraag faalde: {resp.status_code} {resp.text[:200]}"
        )

    body = """<!doctype html><html lang="nl"><head><meta charset="utf-8">
<title>Check je mail</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:-apple-system,system-ui,sans-serif;max-width:420px;margin:64px auto;padding:0 16px;}
.box{background:#f4f7f4;border:1px solid #cfdacf;padding:16px;border-radius:8px;}
</style></head><body><h2>Check je mail</h2>
<div class="box">We hebben een login-link gemaild. Klik op de link om door te gaan.</div>
<p style="color:#888;margin-top:24px;font-size:0.9rem;">Geen mail? Check je spam-folder. De link is 15 minuten geldig.</p>
</body></html>"""
    return HTMLResponse(body)


async def oauth_magic_callback(request: Request) -> Response:
    """GET /oauth/magic-callback?state=&access_token=&refresh_token=...
    Supabase redirects here AFTER user clicks the magic link in their email.
    The Supabase JS magic link puts tokens in the URL fragment (#) which
    server can't read. We use the implicit grant: the callback page loads
    JS that posts the fragment back to /oauth/magic-finalize."""

    qp = request.query_params
    state_jwt = qp.get("state", "")
    if not state_jwt:
        return _html_error("missing state in callback")

    # Render a tiny page that grabs tokens from the URL fragment and POSTs
    # them to /oauth/magic-finalize so the server can finish the bridge.
    body = """<!doctype html><html><head><meta charset="utf-8">
<title>Doorgaan naar Claude...</title>
<style>body{font-family:-apple-system,system-ui,sans-serif;text-align:center;margin-top:64px;color:#444;}</style>
</head><body><p>Een moment...</p>
<script>
(async function() {
  const hash = window.location.hash.slice(1);
  const params = new URLSearchParams(hash);
  const access = params.get('access_token');
  const state = new URLSearchParams(window.location.search).get('state');
  if (!access || !state) {
    document.body.innerHTML = '<p>Ongeldige callback (geen token).</p>';
    return;
  }
  const resp = await fetch('/oauth/magic-finalize', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({access_token: access, state_jwt: state})
  });
  if (resp.redirected) {
    window.location.href = resp.url;
  } else {
    const text = await resp.text();
    document.body.innerHTML = text;
  }
})();
</script></body></html>"""
    return HTMLResponse(body)


async def oauth_magic_finalize(request: Request) -> Response:
    """POST /oauth/magic-finalize — JS in the callback page passes the
    Supabase access_token + state_jwt; we verify with Supabase, get user_id,
    issue our auth-code-JWT, and redirect to claude.ai."""

    form = await request.form()
    sb_access = form.get("access_token", "")
    state_jwt = form.get("state_jwt", "")

    state = verify_jwt(state_jwt)
    if not state or state.get("type") != "auth_state":
        return _html_error("ongeldige of verlopen state")
    if not sb_access:
        return _html_error("missing Supabase access_token")

    user_id = await _verify_supabase_token(sb_access)
    if not user_id:
        return _html_error("Supabase token-validatie faalde")

    return _redirect_to_claude_with_code(state, user_id)


# ---- Password path ----


async def oauth_authorize_password(request: Request) -> Response:
    """POST /oauth/authorize/password — direct email+password login."""
    form = await request.form()
    email = form.get("email", "").strip()
    password = form.get("password", "")
    state_jwt = form.get("state_jwt", "")

    state = verify_jwt(state_jwt)
    if not state or state.get("type") != "auth_state":
        return _html_error("ongeldige of verlopen state")
    if not email or not password:
        return _html_error("email en wachtwoord vereist")

    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{_supabase_url()}/auth/v1/token?grant_type=password",
                json={"email": email, "password": password},
                headers={
                    "apikey": _supabase_anon_key(),
                    "Content-Type": "application/json",
                },
            )
    except Exception as e:
        return _html_error(f"kon Supabase niet bereiken: {e}", status_code=502)

    if resp.status_code >= 400:
        return _html_error("Login mislukt — check email en wachtwoord", status_code=401)

    data = resp.json()
    user_id = (data.get("user") or {}).get("id")
    if not user_id:
        return _html_error("Supabase login response zonder user_id")

    return _redirect_to_claude_with_code(state, user_id)


# ---- Helpers ----


async def _verify_supabase_token(access_token: str) -> str | None:
    """Call Supabase /auth/v1/user with the access token; return user_id."""
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                f"{_supabase_url()}/auth/v1/user",
                headers={
                    "apikey": _supabase_anon_key(),
                    "Authorization": f"Bearer {access_token}",
                },
            )
    except Exception:
        return None

    if resp.status_code >= 400:
        return None

    data = resp.json()
    return data.get("id")


def _redirect_to_claude_with_code(state: dict, user_id: str) -> RedirectResponse:
    auth_code = sign_jwt(
        {
            "type": "auth_code",
            "user_id": user_id,
            "code_challenge": state["claude_code_challenge"],
            "redirect_uri": state["claude_redirect"],
        },
        ttl_seconds=AUTH_CODE_TTL,
    )

    claude_redirect = state["claude_redirect"]
    sep = "&" if "?" in claude_redirect else "?"
    location = (
        f"{claude_redirect}{sep}"
        f"{urlencode({'code': auth_code, 'state': state.get('claude_state', '')})}"
    )
    return RedirectResponse(location, status_code=302)


# ---- /oauth/token: claude.ai exchanges auth_code for access_token ----


async def oauth_token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type", "")
    if grant_type != "authorization_code":
        return JSONResponse(
            {"error": "unsupported_grant_type"}, status_code=400
        )

    code = form.get("code", "")
    code_verifier = form.get("code_verifier", "")
    redirect_uri = form.get("redirect_uri", "")

    payload = verify_jwt(code)
    if not payload or payload.get("type") != "auth_code":
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "bad code"},
            status_code=400,
        )
    if payload.get("redirect_uri") != redirect_uri:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "redirect_uri mismatch"},
            status_code=400,
        )
    if not _verify_pkce(code_verifier, payload.get("code_challenge", "")):
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "PKCE mismatch"},
            status_code=400,
        )

    user_id = payload.get("user_id")
    if not user_id:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "missing user_id"},
            status_code=400,
        )

    access_token = sign_jwt(
        {"type": "access_token", "user_id": user_id, "sub": user_id},
        ttl_seconds=ACCESS_TOKEN_TTL,
    )
    return JSONResponse({
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "scope": "mcp",
    })
```

- [ ] **Step 2: Update `main.py` to register OAuth routes**

In `lazytheta-mcp-cloudrun/main.py`, replace the imports + `create_app()` with:

```python
from auth import (
    oauth_authorize,
    oauth_authorize_magic,
    oauth_authorize_password,
    oauth_magic_callback,
    oauth_magic_finalize,
    oauth_register,
    oauth_token,
    verify_jwt,
    well_known_authorization_server,
    well_known_protected_resource,
)
from mcp_handler import mcp_endpoint


# (SmartAuthMiddleware unchanged from Task 2)


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "lazytheta-mcp"})


def create_app():
    routes = [
        Route("/health", health, methods=["GET"]),
        Route(
            "/.well-known/oauth-authorization-server",
            well_known_authorization_server,
            methods=["GET"],
        ),
        Route(
            "/.well-known/oauth-protected-resource",
            well_known_protected_resource,
            methods=["GET"],
        ),
        Route("/oauth/register", oauth_register, methods=["POST"]),
        Route("/oauth/authorize", oauth_authorize, methods=["GET"]),
        Route("/oauth/authorize/magic", oauth_authorize_magic, methods=["POST"]),
        Route("/oauth/authorize/password", oauth_authorize_password, methods=["POST"]),
        Route("/oauth/magic-callback", oauth_magic_callback, methods=["GET"]),
        Route("/oauth/magic-finalize", oauth_magic_finalize, methods=["POST"]),
        Route("/oauth/token", oauth_token, methods=["POST"]),
        Route("/mcp", mcp_endpoint, methods=["POST", "GET", "DELETE"]),
    ]
    starlette_app = Starlette(routes=routes)
    return SmartAuthMiddleware(starlette_app)
```

- [ ] **Step 3: Add OAuth tests to `test_app.py`**

Append to `lazytheta-mcp-cloudrun/test_app.py`:

```python
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
    from auth import verify_jwt
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
    from auth import sign_jwt
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
    from auth import sign_jwt
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
    from auth import sign_jwt, verify_jwt
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
    import auth

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

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeClient)

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
    from auth import sign_jwt
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

    import auth

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

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeClient)

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
    from auth import sign_jwt, verify_jwt
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
    from auth import sign_jwt
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
    from auth import sign_jwt
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
```

- [ ] **Step 4: Run all tests**

```bash
cd /Users/administrator/Documents/github/stock-analysis/lazytheta-mcp-cloudrun
python3 -m pytest test_app.py -v
```

Expected: 16+ PASS (8 from Task 2 + 8 new OAuth tests).

- [ ] **Step 5: Commit**

```bash
git add lazytheta-mcp-cloudrun/auth.py lazytheta-mcp-cloudrun/main.py lazytheta-mcp-cloudrun/test_app.py
git commit -m "$(cat <<'EOF'
feat(cloudrun): OAuth bridge to Supabase Auth

OAuth 2.1 + PKCE bridge: claude.ai → /oauth/authorize → login page
(magic link + password tabs) → Supabase Auth → /oauth/token → JWT
with user_id. Magic link uses Supabase OTP + a JS callback page that
posts the URL-fragment access_token back to /oauth/magic-finalize.
Password path goes direct to Supabase REST.

8 new tests cover OAuth metadata, register, authorize page rendering,
unregistered redirect rejection, password happy path with mocked
Supabase, password invalid credentials, token exchange, and PKCE +
redirect_uri mismatch rejections.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire all 11 tools

**Why:** the dispatcher returns `tools: []` from Task 2's stub. Now we wire the 11 actual tools — 8 import from `mcp_server.py` directly (after Task 1's multi-user-ify), 3 prescan tools call their `_*_impl` counterparts.

**Files:**
- Modify: `lazytheta-mcp-cloudrun/mcp_handler.py` (replace stub with full TOOLS list + wrappers)
- Modify: `lazytheta-mcp-cloudrun/test_app.py` (add tool dispatcher tests)

- [ ] **Step 1: Replace `mcp_handler.py` with full implementation**

Replace `lazytheta-mcp-cloudrun/mcp_handler.py`:

```python
"""Stateless MCP JSON-RPC dispatcher for LazyTheta DCF (multi-user).

Each /mcp request carries a JWT bearer; SmartAuthMiddleware extracts
user_id from the JWT and stashes it in scope["state"]["user_id"]. This
handler reads it from request.scope and passes it to every tool's
_*_impl function.

The 11 tools call into mcp_server.py's _*_impl functions, which were
multi-user-ified in Task 1 to accept an explicit user_id parameter.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# These imports come from the repo root; the Dockerfile copies them into /app
import mcp_server

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lazytheta-mcp"
SERVER_VERSION = "1.0.0"

logger = logging.getLogger(__name__)


# ---- Tool implementations ----


async def _tool_build_dcf_config(user_id: str, args: dict) -> Any:
    return mcp_server._build_dcf_config_impl(
        ticker=args["ticker"],
        financial_data=args["financial_data"],
        company_name=args["company_name"],
        sic_code=args.get("sic_code"),
        sic_description=args.get("sic_description", ""),
        margin_of_safety=args.get("margin_of_safety"),
        terminal_growth=args.get("terminal_growth"),
        sector_margin=args.get("sector_margin"),
        consensus=args.get("consensus"),
        valuation_basis=args.get("valuation_basis", "nominal"),
        user_id=user_id,
    )


async def _tool_calculate_valuation(user_id: str, args: dict) -> Any:
    return mcp_server._calculate_valuation_impl(args["config"], user_id=user_id)


async def _tool_calculate_multi_lens_valuation(user_id: str, args: dict) -> Any:
    return mcp_server._calculate_multi_lens_valuation_impl(
        ticker=args["ticker"],
        scenario_grid=args.get("scenario_grid", False),
        user_id=user_id,
    )


async def _tool_refresh_all_valuations(user_id: str, args: dict) -> Any:
    return mcp_server._refresh_all_valuations_impl(
        force=args.get("force", False),
        user_id=user_id,
    )


async def _tool_save_to_watchlist(user_id: str, args: dict) -> Any:
    return mcp_server._save_to_watchlist_impl(
        ticker=args["ticker"],
        cfg=args["config"],
        user_id=user_id,
    )


async def _tool_get_config(user_id: str, args: dict) -> Any:
    return mcp_server._get_config_impl(args["ticker"], user_id=user_id)


async def _tool_get_watchlist(user_id: str, args: dict) -> Any:
    return mcp_server._get_watchlist_impl(user_id=user_id)


async def _tool_update_valuation_inputs(user_id: str, args: dict) -> Any:
    return mcp_server._update_valuation_inputs_impl(
        ticker=args["ticker"],
        fields=args["fields"],
        user_id=user_id,
    )


async def _tool_get_prescan_prompts(user_id: str, args: dict) -> Any:
    return mcp_server._get_prescan_prompts_impl(args["ticker"], user_id=user_id)


async def _tool_get_prescan_sections(user_id: str, args: dict) -> Any:
    return mcp_server._get_prescan_sections_impl(args["ticker"], user_id=user_id)


async def _tool_save_prescan_section(user_id: str, args: dict) -> Any:
    return mcp_server._save_prescan_section_impl(
        ticker=args["ticker"],
        title=args["title"],
        content=args["content"],
        user_id=user_id,
    )


# ---- Tool definitions (MCP wire format) ----

TOOLS: list[dict] = [
    {
        "name": "build_dcf_config",
        "description": (
            "Build a complete DCF configuration from SEC financial data. "
            "Wraps gather_data.build_config; assembles sector betas, peer set, "
            "stock price, and base assumptions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "financial_data": {"type": "object"},
                "company_name": {"type": "string"},
                "sic_code": {"type": "string"},
                "sic_description": {"type": "string"},
                "margin_of_safety": {"type": "number"},
                "terminal_growth": {"type": "number"},
                "sector_margin": {"type": "number"},
                "consensus": {"type": "object"},
                "valuation_basis": {"type": "string", "enum": ["nominal", "real"]},
            },
            "required": ["ticker", "financial_data", "company_name"],
        },
    },
    {
        "name": "calculate_valuation",
        "description": (
            "Calculate intrinsic value, WACC, and reverse DCF from a config. "
            "Returns wacc, intrinsic_value, buy_price, enterprise_value, "
            "equity_value, tv_pct, implied_growth, implied_margin."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"config": {"type": "object"}},
            "required": ["config"],
        },
    },
    {
        "name": "calculate_multi_lens_valuation",
        "description": (
            "Run the multi-lens fair value (DCF + Peers + Historical + Dividend "
            "+ Reverse DCF) for a watchlist ticker. Auto-fetches market inputs, "
            "peer multiples, and dividend history first. Stores summary back."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "scenario_grid": {"type": "boolean", "default": False},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "refresh_all_valuations",
        "description": (
            "Recompute multi-lens fair value across all watchlist tickers in "
            "parallel. force=True ignores the 7-day staleness check."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"force": {"type": "boolean", "default": False}},
            "required": [],
        },
    },
    {
        "name": "save_to_watchlist",
        "description": "Upsert a complete DCF config into the user's watchlist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "config": {"type": "object"},
            },
            "required": ["ticker", "config"],
        },
    },
    {
        "name": "get_config",
        "description": "Read an existing DCF config by ticker.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_watchlist",
        "description": (
            "List all watchlist tickers with enriched metadata: fv_low/mid/high, "
            "buy_price, current_vs_mid, lens_count, verdict, phase."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_valuation_inputs",
        "description": (
            "Override one or more valuation_inputs fields for a watchlist "
            "ticker (e.g. dividend_5y_cagr, forward_eps, ttm_ebitda). Each "
            "updated field is removed from _auto_filled so the override "
            "survives the next yfinance refresh."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": "Dict of valuation_inputs keys to set",
                },
            },
            "required": ["ticker", "fields"],
        },
    },
    {
        "name": "get_prescan_prompts",
        "description": (
            "Return the user's prescan prompt library with placeholders "
            "({ticker}, {company}, {prior:Section}) substituted from the "
            "current ai_notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_prescan_sections",
        "description": "Current ai_notes content per prescan section.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "save_prescan_section",
        "description": (
            "Write one prescan section to ai_notes. Other sections preserved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["ticker", "title", "content"],
        },
    },
]


TOOL_HANDLERS: dict[str, Callable[[str, dict], Awaitable[Any]]] = {
    "build_dcf_config": _tool_build_dcf_config,
    "calculate_valuation": _tool_calculate_valuation,
    "calculate_multi_lens_valuation": _tool_calculate_multi_lens_valuation,
    "refresh_all_valuations": _tool_refresh_all_valuations,
    "save_to_watchlist": _tool_save_to_watchlist,
    "get_config": _tool_get_config,
    "get_watchlist": _tool_get_watchlist,
    "update_valuation_inputs": _tool_update_valuation_inputs,
    "get_prescan_prompts": _tool_get_prescan_prompts,
    "get_prescan_sections": _tool_get_prescan_sections,
    "save_prescan_section": _tool_save_prescan_section,
}


# ---- JSON-RPC dispatch ----


async def _handle_one(message: dict, user_id: str | None) -> dict | None:
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled", "notifications/progress"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        if not user_id:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": "Authenticated user required"},
            }
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
            }
        try:
            result = await handler(user_id, arguments)
        except (KeyError, ValueError) as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }
        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Internal error: {e}"}],
                    "isError": True,
                },
            }

        # Tool impls return JSON strings; we wrap as text content.
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            },
        }

    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


async def mcp_endpoint(request: Request) -> Response:
    try:
        if request.method == "GET":
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600, "message": "GET not supported"}},
                status_code=405,
            )
        if request.method == "DELETE":
            return Response(status_code=200)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        user_id = request.scope.get("state", {}).get("user_id")

        if isinstance(body, list):
            responses = []
            for msg in body:
                r = await _handle_one(msg, user_id)
                if r is not None:
                    responses.append(r)
            if not responses:
                return Response(status_code=202)
            return JSONResponse(responses)

        if not isinstance(body, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600, "message": "Invalid request"}},
                status_code=400,
            )

        response = await _handle_one(body, user_id)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)
    except Exception:
        logger.exception("mcp_endpoint failed")
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32603, "message": "Internal server error"}},
            status_code=500,
        )
```

- [ ] **Step 2: Add tool dispatcher tests**

Append to `lazytheta-mcp-cloudrun/test_app.py`:

```python
def test_tools_list_returns_11_tools():
    """tools/list now returns the full set of 11 tools."""
    from starlette.testclient import TestClient
    from auth import sign_jwt
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
    assert len(tools) == 11
    names = {t["name"] for t in tools}
    assert names == {
        "build_dcf_config", "calculate_valuation", "calculate_multi_lens_valuation",
        "refresh_all_valuations", "save_to_watchlist", "get_config",
        "get_watchlist", "update_valuation_inputs",
        "get_prescan_prompts", "get_prescan_sections", "save_prescan_section",
    }


def test_tools_call_get_watchlist_passes_user_id(monkeypatch):
    """tools/call → get_watchlist routes user_id from JWT to _get_watchlist_impl."""
    from starlette.testclient import TestClient
    from auth import sign_jwt
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
    from auth import sign_jwt
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
    from auth import sign_jwt
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


def test_tools_call_save_prescan_section_passes_three_args(monkeypatch):
    from starlette.testclient import TestClient
    from auth import sign_jwt
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
```

- [ ] **Step 3: Run tests**

```bash
cd /Users/administrator/Documents/github/stock-analysis/lazytheta-mcp-cloudrun
python3 -m pytest test_app.py -v
```

Expected: 21 PASS (16 from prior tasks + 5 new dispatcher tests).

If imports fail with `ModuleNotFoundError: mcp_server`, prepend the repo root to sys.path in the test file. The pattern in `test_app.py` should be:

```python
HERE = Path(__file__).parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))
```

If tests fail because env vars are missing for the import chain (`SUPABASE_URL` needed by mcp_server's `get_supabase_client`), the existing `_set_supabase_env` fixture should cover it. If `LAZYTHETA_USER_ID` is also required at import time, add `monkeypatch.setenv("LAZYTHETA_USER_ID", "test-uid")` to the fixture.

- [ ] **Step 4: Commit**

```bash
git add lazytheta-mcp-cloudrun/mcp_handler.py lazytheta-mcp-cloudrun/test_app.py
git commit -m "$(cat <<'EOF'
feat(cloudrun): wire all 11 LazyTheta MCP tools

Replace the stub mcp_handler.py with the full TOOLS list and 11 tool
wrappers that delegate to mcp_server._*_impl functions (multi-user-ified
in Task 1). Each wrapper passes the JWT-derived user_id explicitly so
multiple users can use the same Cloud Run instance with proper data
isolation.

5 new dispatcher tests: tools/list returns 11 names, get_watchlist
routes user_id, unknown tool returns -32602, update_valuation_inputs
and save_prescan_section pass args correctly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Deploy to Cloud Run + smoke

**Why:** the code is done; this task installs gcloud, configures the project, sets secrets, deploys, registers in claude.ai, and smokes through one tool call per user.

**Files:**
- Create: `lazytheta-mcp-cloudrun/README.md`
- No code changes — operational task.

- [ ] **Step 1: Install `gcloud` CLI** (if not already done)

```bash
brew install --cask google-cloud-sdk
```

Then initialize:

```bash
gcloud init
# Login with Google account
# Pick existing project: stock-analysis-489016
# Default region: europe-west4

gcloud auth login
gcloud auth application-default login
```

Verify:

```bash
gcloud config list
```

Expected: `project = stock-analysis-489016`, `region = europe-west4`.

- [ ] **Step 2: Enable required APIs**

```bash
gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    --project stock-analysis-489016
```

Expected: each enable confirms success or "already enabled".

- [ ] **Step 3: Generate `JWT_SIGNING_KEY` and create secrets**

```bash
# Generate a fresh 64-byte base64 key
JWT_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")

# Create secrets in GCP Secret Manager
echo -n "$JWT_KEY" | gcloud secrets create JWT_SIGNING_KEY \
    --project stock-analysis-489016 \
    --data-file=-

# Pull existing Supabase env vars from your local config and store
# (read SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY from
#  ~/Library/Application\ Support/Claude/claude_desktop_config.json
#  or wherever you keep them)
echo -n "https://xyz.supabase.co" | gcloud secrets create SUPABASE_URL --data-file=-
echo -n "<service-role-key>" | gcloud secrets create SUPABASE_SERVICE_KEY --data-file=-
echo -n "<anon-key>" | gcloud secrets create SUPABASE_ANON_KEY --data-file=-
```

For each `gcloud secrets create` you'll be prompted for replication policy if no default. Pick `automatic`.

- [ ] **Step 4: Configure Supabase Auth redirect URL allowlist**

This step is in the Supabase Dashboard, NOT gcloud:

1. Go to https://supabase.com/dashboard
2. Open the lazytheta.io project
3. Authentication → URL Configuration
4. Add to "Redirect URLs" allowlist:
   ```
   https://lazytheta-mcp-*-ew.a.run.app/oauth/magic-callback
   ```
   (The wildcard covers the hash that Cloud Run assigns to the URL — Supabase supports wildcards in this field.)
5. Save.

This is required so Supabase will accept our `email_redirect_to` param when issuing magic links.

- [ ] **Step 5: First deploy**

From the repo root:

```bash
cd /Users/administrator/Documents/github/stock-analysis

gcloud run deploy lazytheta-mcp \
    --project stock-analysis-489016 \
    --source . \
    --region europe-west4 \
    --platform managed \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --timeout 600 \
    --min-instances 0 \
    --max-instances 5 \
    --set-secrets="JWT_SIGNING_KEY=JWT_SIGNING_KEY:latest,SUPABASE_URL=SUPABASE_URL:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest"
```

The first deploy takes ~5-10 minutes (build, push to Artifact Registry, deploy). Capture the URL output, e.g.:

```
Service URL: https://lazytheta-mcp-abc123-ew.a.run.app
```

- [ ] **Step 6: Smoke `/health` from external**

```bash
curl https://lazytheta-mcp-abc123-ew.a.run.app/health
```

Expected: `{"status":"ok","service":"lazytheta-mcp"}`.

If 500 or 404, check logs:

```bash
gcloud run services logs tail lazytheta-mcp --region europe-west4
```

- [ ] **Step 7: Smoke OAuth metadata**

```bash
curl https://lazytheta-mcp-abc123-ew.a.run.app/.well-known/oauth-authorization-server
```

Expected: JSON with `issuer`, `authorization_endpoint`, `token_endpoint`, etc.

- [ ] **Step 8: Register on claude.ai**

1. Open https://claude.ai
2. Settings → Connectors → Add custom connector
3. URL: `https://lazytheta-mcp-abc123-ew.a.run.app/mcp`
4. claude.ai auto-discovers OAuth via `/.well-known/...`
5. claude.ai shows "Authenticate" button → click → redirects to our `/oauth/authorize`
6. Login page appears → use magic link OR password (your lazytheta.io credentials)
7. After login, redirected back to claude.ai → connector status: "Connected"

- [ ] **Step 9: Smoke each tool from claude chat**

In a new claude.ai chat with the connector enabled, try:

```
"Use lazytheta-dcf to list my watchlist"
→ should call get_watchlist and return the list of tickers

"What's MSFT's current valuation summary?"
→ should call get_config(MSFT) or calculate_multi_lens_valuation(MSFT)

"Set PEP's expected dividend growth to 8%"
→ should call update_valuation_inputs(ticker=PEP, fields={dividend_5y_cagr: 0.08})

"Refresh all my watchlist valuations"
→ should call refresh_all_valuations(force=False)
```

Watch for tool errors. If a tool fails:

```bash
gcloud run services logs tail lazytheta-mcp --region europe-west4
```

Common issues:
- Missing import: a shared module wasn't copied into the Docker image. Check Dockerfile COPY list.
- Env var error: secret didn't propagate. Check `gcloud secrets versions list <SECRET_NAME>`.
- Supabase 401: service key may be wrong. Re-check value.

- [ ] **Step 10: Document the deployed URL + create README**

Create `lazytheta-mcp-cloudrun/README.md`:

```markdown
# LazyTheta DCF MCP — Cloud Run

Multi-user remote MCP for the LazyTheta DCF system. Authenticates via
Supabase Auth (the same accounts as lazytheta.io). All 11 tools from
the local stdio MCP are exposed remotely.

## Deployed URL

`https://lazytheta-mcp-<HASH>-ew.a.run.app/mcp`

(Replace `<HASH>` with the actual hash from `gcloud run services list`.)

## Local development

```bash
cd lazytheta-mcp-cloudrun
pip install -r requirements.txt

# Set required env vars
export JWT_SIGNING_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export SUPABASE_URL="https://...supabase.co"
export SUPABASE_ANON_KEY="..."
export SUPABASE_SERVICE_KEY="..."

# Run dev server
python3 main.py
# Server listens on http://localhost:8080
```

## Tests

```bash
cd lazytheta-mcp-cloudrun
python3 -m pytest test_app.py -v
```

All tests offline-mocked; no network or Supabase access required.

## Deploy

```bash
# From repo root, NOT from lazytheta-mcp-cloudrun/
cd /Users/administrator/Documents/github/stock-analysis

gcloud run deploy lazytheta-mcp \
    --project stock-analysis-489016 \
    --source . \
    --region europe-west4 \
    --platform managed \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --timeout 600 \
    --min-instances 0 \
    --max-instances 5 \
    --set-secrets="JWT_SIGNING_KEY=JWT_SIGNING_KEY:latest,SUPABASE_URL=SUPABASE_URL:latest,SUPABASE_SERVICE_KEY=SUPABASE_SERVICE_KEY:latest,SUPABASE_ANON_KEY=SUPABASE_ANON_KEY:latest"
```

Subsequent deploys: same command. Cloud Run builds incrementally so most
deploys take ~2-3 minutes.

## Logs

```bash
gcloud run services logs tail lazytheta-mcp --region europe-west4
```

## Architecture

See `docs/superpowers/specs/2026-05-09-lazytheta-mcp-cloudrun-design.md`
for the full design doc.

## Updating

When `mcp_server.py` or its dependencies (`auto_fetch.py`, `valuation_lenses.py`,
etc.) change, redeploy via `gcloud run deploy ...`. The Cloud Run image
will pick up the latest version.

## Security

- JWT_SIGNING_KEY is the only secret that, if leaked, allows token forgery.
  Rotate via `gcloud secrets versions add JWT_SIGNING_KEY` if compromised.
- Service-role Supabase key is also sensitive. The MCP service uses it to
  bypass RLS, then explicitly filters by JWT-derived user_id.
- All inter-service traffic is HTTPS via Cloud Run's automatic TLS.
- claude.ai stores the access-token-JWT per user; revoking a user's access
  requires either rotating JWT_SIGNING_KEY (revokes all users) or adding
  a per-user revocation list in our token verification (future work).
```

- [ ] **Step 11: Final verification — all tests + ruff**

```bash
cd /Users/administrator/Documents/github/stock-analysis

# Full pytest suite (existing + new)
python3 -m pytest tests/ test_mcp_server.py lazytheta-mcp-cloudrun/test_app.py -v

# Ruff on new code
python3 -m ruff check lazytheta-mcp-cloudrun/
```

Expected: all tests pass; ruff clean on new files. Pre-existing ruff debt on
other files stays untouched.

- [ ] **Step 12: Commit + push to main + cleanup**

```bash
git add lazytheta-mcp-cloudrun/README.md
git commit -m "$(cat <<'EOF'
docs(cloudrun): add README with deploy + local dev instructions

Documents the deployed URL placeholder, local dev setup, test command,
deploy command, log tailing, and security notes.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"

# Merge feature branch
git checkout main
git pull
git merge --ff-only feature/lazytheta-mcp-cloudrun

# Push
git push origin main

# Delete feature branch
git branch -d feature/lazytheta-mcp-cloudrun
```

---

## Notes for the implementer

- **Branch handling:** start from a clean main: `git checkout main && git pull`. Then `git checkout -b feature/lazytheta-mcp-cloudrun`. All 5 tasks land on this single branch with one commit each (~5 commits total).
- **Don't run gcloud from the cloudrun subdir.** The `--source .` must point at the repo root so the Dockerfile can `COPY` shared modules from there.
- **First deploy is slow** (~5-10 min) because of Cloud Build + Artifact Registry initialization. Subsequent deploys are ~2-3 min.
- **Region locking:** once the service is created in `europe-west4`, you can't move it without re-creating. Confirm this is the right region before deploy.
- **Supabase Magic Link redirect URL allowlist** must be configured manually in the Supabase dashboard (step 4 of Task 5) BEFORE first deploy, otherwise magic-link emails will be rejected.
- **No CI/CD yet.** All deploys are manual via `gcloud run deploy ...`. If you want GitHub Actions, that's a follow-up task post-MVP.
- **Cold start tax:** first request after 15 min idle is ~3-5s slow. Acceptable for personal mobile use. If it ever becomes annoying, set `--min-instances=1` (~$5/month).
- **Module name collision:** `mcp_server.py` is the local stdio MCP file. Don't accidentally add a file named the same in the cloudrun dir. The Dockerfile copies the repo-root version into `/app/`.
- **MCP tool errors are caught and returned as `isError: true` in the JSON-RPC response.** This means tool failures don't crash the dispatcher; they show up as error text in claude.ai's tool result panel.
