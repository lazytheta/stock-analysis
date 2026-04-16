# TastyTrade MCP on Cloud Run — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy tasty-agent as a remote SSE MCP server on Google Cloud Run with bearer token auth.

**Architecture:** A thin `main.py` wrapper imports tasty-agent's `mcp_app`, adds Starlette auth middleware, overrides host/port for Cloud Run, and runs uvicorn. Packaged in a Docker container.

**Tech Stack:** Python 3.12, tasty-agent (PyPI), Starlette middleware, uvicorn, Google Cloud Run.

**Spec:** `docs/superpowers/specs/2026-04-16-tastytrade-mcp-cloudrun-design.md`

---

## File Structure

```
tastytrade-mcp-cloudrun/       # New directory in project root
├── main.py                    # Auth middleware wrapper + SSE server startup
├── Dockerfile                 # Container image definition
├── requirements.txt           # Python dependencies (just tasty-agent)
└── test_main.py               # Tests for auth middleware
```

- `main.py` — Imports `mcp_app` from tasty-agent, gets its Starlette SSE app, wraps it with bearer token auth middleware, runs uvicorn on `0.0.0.0:PORT`.
- `Dockerfile` — Python 3.12-slim base, installs requirements, copies main.py, runs it.
- `requirements.txt` — Pins `tasty-agent`.
- `test_main.py` — Tests that auth middleware blocks/allows requests correctly.

---

## Task 1: Create project directory and requirements.txt

**Files:**
- Create: `tastytrade-mcp-cloudrun/requirements.txt`

- [ ] **Step 1: Create directory and requirements file**

```bash
mkdir -p tastytrade-mcp-cloudrun
```

Write `tastytrade-mcp-cloudrun/requirements.txt`:

```
tasty-agent>=4.0.0
```

- [ ] **Step 2: Commit**

```bash
git add tastytrade-mcp-cloudrun/requirements.txt
git commit -m "chore: init tastytrade-mcp-cloudrun with requirements"
```

---

## Task 2: Write auth middleware tests

**Files:**
- Create: `tastytrade-mcp-cloudrun/test_main.py`

- [ ] **Step 1: Write tests for bearer token auth middleware**

Write `tastytrade-mcp-cloudrun/test_main.py`:

```python
"""Tests for bearer token auth middleware."""

import os
from unittest.mock import AsyncMock, patch

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


def test_empty_token_env_allows_all_requests():
    """When MCP_AUTH_TOKEN is empty, middleware should not be added (no auth)."""
    from main import create_app

    with patch.dict(os.environ, {"MCP_AUTH_TOKEN": ""}, clear=False):
        app = create_app.__wrapped__() if hasattr(create_app, '__wrapped__') else create_app()
        client = TestClient(app)
        resp = client.get("/sse")
        # SSE endpoint exists but we're not doing a real SSE handshake,
        # so 4xx/5xx from SSE handler is fine — the point is it's not 401
        assert resp.status_code != 401
```

- [ ] **Step 2: Run tests to verify they fail (main.py doesn't exist yet)**

Run: `cd tastytrade-mcp-cloudrun && python3 -m pytest test_main.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'main'`

---

## Task 3: Implement main.py with auth middleware

**Files:**
- Create: `tastytrade-mcp-cloudrun/main.py`

- [ ] **Step 1: Write main.py**

Write `tastytrade-mcp-cloudrun/main.py`:

```python
"""TastyTrade MCP Server — Cloud Run wrapper with bearer token auth."""

import os

import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class BearerTokenMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != self.token:
            return Response("Unauthorized", status_code=401)
        return await call_next(request)


def create_app():
    from tasty_agent.server import mcp_app

    mcp_app.settings.host = "0.0.0.0"
    mcp_app.settings.port = int(os.environ.get("PORT", "8080"))

    app = mcp_app.sse_app()

    token = os.environ.get("MCP_AUTH_TOKEN", "")
    if token:
        app.add_middleware(BearerTokenMiddleware, token=token)

    return app


if __name__ == "__main__":
    app = create_app()
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd tastytrade-mcp-cloudrun && pip install tasty-agent starlette httpx && python3 -m pytest test_main.py -v`
Expected: All 5 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tastytrade-mcp-cloudrun/main.py tastytrade-mcp-cloudrun/test_main.py
git commit -m "feat: add main.py with bearer token auth middleware and tests"
```

---

## Task 4: Create Dockerfile

**Files:**
- Create: `tastytrade-mcp-cloudrun/Dockerfile`

- [ ] **Step 1: Write Dockerfile**

Write `tastytrade-mcp-cloudrun/Dockerfile`:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8080
ENV PORT=8080

CMD ["python", "main.py"]
```

- [ ] **Step 2: Test Docker build locally**

Run: `cd tastytrade-mcp-cloudrun && docker build -t tastytrade-mcp .`
Expected: Build succeeds, image created.

- [ ] **Step 3: Test Docker run locally**

Run:
```bash
docker run --rm -p 8080:8080 \
  -e TASTYTRADE_CLIENT_SECRET="$TASTYTRADE_CLIENT_SECRET" \
  -e TASTYTRADE_REFRESH_TOKEN="$TASTYTRADE_REFRESH_TOKEN" \
  -e MCP_AUTH_TOKEN="test-token" \
  tastytrade-mcp
```

In another terminal, verify auth works:
```bash
# Should return 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/sse

# Should attempt SSE connection (200 or hang = auth passed)
curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer test-token" http://localhost:8080/sse
```

- [ ] **Step 4: Commit**

```bash
git add tastytrade-mcp-cloudrun/Dockerfile
git commit -m "feat: add Dockerfile for Cloud Run deployment"
```

---

## Task 5: Install gcloud CLI and deploy to Cloud Run

**Files:** None (infrastructure only)

- [ ] **Step 1: Install gcloud CLI (if not installed)**

Run:
```bash
brew install --cask google-cloud-sdk
```

- [ ] **Step 2: Authenticate and set project**

Run:
```bash
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
```

(The user must complete the interactive browser login.)

- [ ] **Step 3: Generate a secure MCP_AUTH_TOKEN**

Run:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Save the output — this is the bearer token for the MCP endpoint.

- [ ] **Step 4: Deploy to Cloud Run**

Run from `tastytrade-mcp-cloudrun/`:
```bash
gcloud run deploy tastytrade-mcp \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars "TASTYTRADE_CLIENT_SECRET=<value>,TASTYTRADE_REFRESH_TOKEN=<value>,MCP_AUTH_TOKEN=<generated-token>" \
  --memory 256Mi \
  --max-instances 1 \
  --min-instances 0 \
  --timeout 300
```

Expected: Deployment succeeds, prints a service URL like `https://tastytrade-mcp-xxxxx-ew.a.run.app`.

- [ ] **Step 5: Verify deployment**

Run:
```bash
SERVICE_URL=$(gcloud run services describe tastytrade-mcp --region europe-west1 --format 'value(status.url)')

# Should return 401
curl -s -o /dev/null -w "%{http_code}" "$SERVICE_URL/sse"

# Should start SSE stream (interrupt with Ctrl+C after seeing output)
curl -N -H "Authorization: Bearer <generated-token>" "$SERVICE_URL/sse"
```

---

## Task 6: Configure as remote MCP in Claude

**Files:** None (Claude settings)

- [ ] **Step 1: Add remote MCP server in Claude settings**

Go to Claude settings (claude.ai or Claude Code) and add a new remote MCP server:
- **Name:** `tastytrade`
- **URL:** `https://tastytrade-mcp-xxxxx-ew.a.run.app/sse`
- **Headers:** `Authorization: Bearer <generated-token>`

For Claude Code, update `~/.claude/.mcp.json`:
```json
{
  "mcpServers": {
    "tastytrade": {
      "type": "sse",
      "url": "https://tastytrade-mcp-xxxxx-ew.a.run.app/sse",
      "headers": {
        "Authorization": "Bearer <generated-token>"
      }
    }
  }
}
```

- [ ] **Step 2: Test the remote MCP**

Open a new Claude Code session and invoke one of the tools:
```
Use the tastytrade MCP to get my account overview
```

Expected: Returns account balances and positions from TastyTrade.

- [ ] **Step 3: Commit final config notes**

```bash
git add -A tastytrade-mcp-cloudrun/
git commit -m "docs: complete tastytrade MCP Cloud Run deployment"
```
