# TastyTrade MCP Server on Cloud Run

**Goal:** Deploy the existing `tasty-agent` MCP server as a remote HTTP/SSE service on Google Cloud Run, so Claude routines and other remote clients can access TastyTrade account data without a local machine running.

**Date:** 2026-04-16

---

## Architecture

```
Claude Routine / Claude Code
        │
        │  HTTP + SSE (Authorization: Bearer <token>)
        ▼
┌─────────────────────────┐
│   Google Cloud Run       │
│                          │
│  main.py (wrapper)       │
│    ├─ Auth middleware     │
│    └─ tasty-agent SSE    │
│         ├─ account_overview │
│         ├─ get_history      │
│         ├─ manage_order     │
│         ├─ get_quotes       │
│         ├─ get_greeks       │
│         ├─ get_gex          │
│         ├─ get_market_metrics│
│         ├─ market_status    │
│         ├─ search_symbols   │
│         └─ watchlist        │
└─────────────────────────┘
        │
        ▼
   TastyTrade API
   (OAuth refresh token)
```

## Components

### 1. `main.py` — Auth wrapper

A thin Python script that:
- Imports `mcp_app` from `tasty_agent.server`
- Adds Starlette middleware that checks `Authorization: Bearer <token>` against `MCP_AUTH_TOKEN` env var
- Returns 401 for missing/invalid tokens
- Runs the SSE server on the port specified by `PORT` env var (Cloud Run sets this, default 8080)

The wrapper does NOT modify any tasty-agent behavior — it only adds an auth gate in front.

### 2. `Dockerfile`

- Base image: `python:3.12-slim`
- Installs `tasty-agent` from PyPI
- Copies `main.py`
- Runs `python main.py`
- Exposes `PORT` (default 8080)

### 3. Environment variables (Cloud Run)

| Variable | Purpose |
|----------|---------|
| `TASTYTRADE_CLIENT_SECRET` | TastyTrade OAuth client secret |
| `TASTYTRADE_REFRESH_TOKEN` | TastyTrade OAuth refresh token |
| `MCP_AUTH_TOKEN` | Bearer token to protect the endpoint |
| `PORT` | Set automatically by Cloud Run (8080) |

### 4. Cloud Run settings

- **Region:** europe-west1 (or nearest)
- **Min instances:** 0 (scale to zero when idle — free tier)
- **Max instances:** 1 (single user, no need for scaling)
- **Memory:** 256MB (sufficient for this workload)
- **CPU:** 1 vCPU
- **Timeout:** 300s (SSE connections can be long-lived)
- **Ingress:** All traffic (protected by bearer token)
- **Auth:** Allow unauthenticated (auth handled by bearer token in app)

## Auth flow

1. Client sends request with `Authorization: Bearer <token>` header
2. Middleware compares token against `MCP_AUTH_TOKEN` env var
3. If match: request proceeds to tasty-agent SSE handler
4. If no match or missing: 401 Unauthorized response

## Available tools (all from tasty-agent, unchanged)

- `account_overview` — balances and open positions
- `get_history` — transaction/order history
- `manage_order` — place/replace/cancel/list orders
- `get_quotes` — live quotes via DXLink streaming
- `get_greeks` — option Greeks
- `get_gex` — gamma exposure analysis
- `get_market_metrics` — IV, beta, P/E, liquidity
- `market_status` — exchange open/closed status
- `search_symbols` — symbol search
- `watchlist` — manage watchlists

## Deployment

### Project structure (new repo or subdirectory)

```
tastytrade-mcp-cloudrun/
├── main.py
├── Dockerfile
└── requirements.txt
```

### Deploy commands

```bash
# Build and deploy to Cloud Run
gcloud run deploy tastytrade-mcp \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --set-env-vars "TASTYTRADE_CLIENT_SECRET=xxx,TASTYTRADE_REFRESH_TOKEN=xxx,MCP_AUTH_TOKEN=xxx" \
  --memory 256Mi \
  --max-instances 1 \
  --min-instances 0 \
  --timeout 300
```

### Configure in Claude

Add as remote MCP server in Claude settings with the Cloud Run URL and bearer token header.

## Out of scope

- Multi-user token mapping (can add later)
- Custom tools beyond what tasty-agent provides
- LazyTheta DCF tools (separate server, separate deployment)
