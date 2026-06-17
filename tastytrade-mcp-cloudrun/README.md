# TastyTrade MCP

Multi-user [Model Context Protocol](https://modelcontextprotocol.io) server that connects [TastyTrade](https://tastytrade.com) brokerage accounts to Claude (web, Desktop, and mobile). Each user authorizes their own TastyTrade account via OAuth — no shared credentials, no per-user setup of the MCP server itself.

Built for **read-only analysis** — positions, strategies, option selection, trade history. No order placement.

## What you can do with it

Once connected, ask Claude things like:

- *"Review my current positions and tell me which ones have the most theta decay risk."*
- *"Find me three strangles to sell on tickers with IV rank above 50."*
- *"What's my realized P&L on TSLA over the past 12 months?"*
- *"Which expirations on AAPL have the highest open interest near the money?"*

Claude composes the right MCP tool calls automatically.

## Tools

| Tool | What it does |
|---|---|
| `get_positions` | All open positions with quantity, P&L, expiration |
| `get_account_balance` | Cash, buying power, NLV, margin requirements |
| `get_quotes` | Snapshot quotes — for options includes Greeks (delta, theta, IV, …) |
| `get_market_metrics` | IV rank, IV percentile, beta, earnings/dividend dates |
| `get_option_chain` | Full nested chain (strikes × expirations × calls/puts), filterable |
| `get_transactions` | Trade history with date/symbol/type filters, paginated |
| `get_recent_orders` | Working or filled orders |
| `get_watchlists` | Read TastyTrade watchlists |

## Architecture

```
                    ┌────────────┐                   ┌──────────────┐
                    │  Claude    │ ── /oauth/* ───►  │ TastyTrade   │
                    │ (web/iOS/  │ ◄── redirect ──   │ login.html   │
                    │  Desktop)  │                   └──────────────┘
                    └─────┬──────┘                           │
                          │                                  │
                          │ MCP (JSON-RPC over HTTPS,        │
                          │  Bearer JWT)                     │
                          ▼                                  ▼
                    ┌─────────────────────────────────────────────┐
                    │  Vercel serverless function (this repo)     │
                    │                                             │
                    │  • OAuth bridge (claude.ai ⇄ TT)            │
                    │  • Stateless MCP JSON-RPC dispatcher        │
                    │  • Per-user token cache                     │
                    └────────┬─────────────────────────┬──────────┘
                             │                         │
                             ▼                         ▼
                    ┌──────────────────┐     ┌────────────────────┐
                    │  Supabase        │     │  api.tastyworks.com│
                    │  mcp_user_tokens │     │  (REST + OAuth)    │
                    └──────────────────┘     └────────────────────┘
```

**Stateless by design.** Every `/mcp` request is independent — no MCP sessions, no event store, no streaming. This was a deliberate choice (see [Design notes](#design-notes)).

## Self-hosting

You don't need to self-host to use this — claude.ai's native connector flow handles the public deployment. But if you want your own:

### 1. Prerequisites

- A [TastyTrade OAuth client application](https://developer.tastytrade.com/oauth/) (not a "Personal OAuth Grant"). Note the `client_id` and `client_secret`. Add `https://YOUR-DOMAIN/oauth/tt-callback` as an allowed redirect URI.
- A [Supabase](https://supabase.com) project. Note the `SUPABASE_URL` and the **service_role** key (not the anon key — RLS on the table requires service role).
- A Vercel account.

### 2. Supabase schema

Run in the Supabase SQL editor:

```sql
CREATE TABLE mcp_user_tokens (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tt_refresh_token TEXT NOT NULL,
    tt_account_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE mcp_user_tokens ENABLE ROW LEVEL SECURITY;
-- service_role bypasses RLS, no policies needed for normal operation.
```

### 3. Deploy to Vercel

```bash
git clone https://github.com/ArjanLig/tastytrade-mcp.git
cd tastytrade-mcp
vercel deploy
```

Set these environment variables on the Vercel project:

| Variable | Value |
|---|---|
| `TASTYTRADE_CLIENT_ID` | TastyTrade OAuth app `client_id` |
| `TASTYTRADE_CLIENT_SECRET` | TastyTrade OAuth app `client_secret` |
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service-role key |
| `JWT_SIGNING_KEY` | Random 32-byte hex (for our own JWTs — generate with `python -c "import secrets; print(secrets.token_hex(32))"`) |

Redeploy. The MCP endpoint is at `https://YOUR-DOMAIN/mcp`.

### 4. Add to Claude

Claude → Settings → Connectors → Add custom integration → URL `https://YOUR-DOMAIN/mcp`. Claude does the OAuth dance, you log into TastyTrade, you're done.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
TASTYTRADE_CLIENT_ID=… TASTYTRADE_CLIENT_SECRET=… \
SUPABASE_URL=… SUPABASE_SERVICE_ROLE_KEY=… \
JWT_SIGNING_KEY=… \
python main.py
# server on http://localhost:8080
```

## Tests

Offline test suite — covers OAuth helpers, security regressions (redirect_uri
validation, PKCE, JWT verification), the MCP JSON-RPC dispatcher, and the auth
middleware. No external service is contacted.

```bash
pip install -r requirements-dev.txt
pytest test_app.py -v
```

## Design notes

A few decisions worth calling out:

### Why a custom MCP dispatcher and not FastMCP?

The MCP Python SDK's `FastMCP` is built around a `StreamableHTTPSessionManager` that lives inside an ASGI lifespan and uses `anyio` task groups. **Vercel's Python runtime doesn't reliably invoke ASGI lifespan events**, so the session manager never initializes; even if you shim it, the task group is task-scoped and dies between serverless invocations.

So `mcp_handler.py` is a hand-rolled stateless JSON-RPC dispatcher (~300 lines). No sessions, no SSE, no event store. Every `POST /mcp` is parsed, dispatched, and answered as a single one-shot JSON response.

### Why JWT-as-state instead of a state table?

The OAuth bridge needs to remember claude.ai's `redirect_uri`, `state`, and `code_challenge` while the user is over at TastyTrade logging in. Most implementations store this in a database keyed by a random ID. Instead, we encode all of it into an HS256-signed JWT and pass it as the OAuth `state` parameter to TastyTrade. When TT redirects back, we decode the JWT and recover everything. **No database round-trip, no state table, no cleanup job.**

### Why pinned dependency versions?

Vercel's serverless Python runtime caches builds. A floating `>=` requirement led to a surprise: a fresh build pulled a newer Starlette release that needed `python-multipart` to parse `application/x-www-form-urlencoded` bodies (it had become a hard dep), which broke `/oauth/token` in production. Versions are now pinned exactly.

## Tech stack

- **Python 3.12** + [Starlette](https://www.starlette.io) (ASGI)
- [httpx](https://www.python-httpx.org) for outbound HTTP (TastyTrade API, Supabase REST, OAuth token exchange)
- [PyJWT](https://github.com/jpadilla/pyjwt) for HS256-signed JWTs (state, auth codes, access tokens)
- [Supabase](https://supabase.com) (PostgreSQL + REST) for per-user token storage
- [Vercel](https://vercel.com) Python serverless runtime
- TastyTrade OAuth 2.1 + PKCE (S256)

## Project layout

```
.
├── main.py              # ASGI entrypoint, routes, auth middleware
├── oauth.py             # OAuth bridge: /authorize, /tt-callback, /token
├── mcp_handler.py       # Stateless MCP JSON-RPC dispatcher + tool definitions
├── tt_client.py         # Per-user TastyTrade REST client (httpx)
├── storage.py           # Supabase REST wrapper
├── requirements.txt     # Pinned deps
├── .python-version      # 3.12
└── Dockerfile           # Optional Cloud Run / container deploy
```

## License

MIT.

## Acknowledgments

- Inspired by [tasty-agent](https://github.com/Lalonas/tasty-agent), the original single-user MCP for TastyTrade. The multi-user/serverless rewrite drops FastMCP and rebuilds the protocol layer from scratch.
- TastyTrade API patterns adapted from [tastyware/tastytrade](https://github.com/tastyware/tastytrade).
