# LazyTheta DCF MCP — Cloud Run deploy with Supabase Auth bridge

**Date:** 2026-05-09
**Author:** Arjan + Claude
**Status:** Approved

## Problem

The `lazytheta-dcf` MCP server currently runs only locally via stdio, launched
by Claude Desktop from `~/Library/Application Support/Claude/claude_desktop_config.json`.
It cannot be used from claude.ai web or mobile, which means the user cannot
inspect the watchlist, recompute valuations, or update prescan sections from
their phone.

## Goal

Deploy the same MCP toolset (11 tools) to Cloud Run as a remote, multi-user
HTTP server that authenticates via the user's existing Supabase account.
Result: claude.ai connector at `https://lazytheta-mcp-<hash>-ew.a.run.app/mcp`
that works on web, desktop, and mobile.

## Non-goals

- **No new tools.** Pure deploy + auth-bridge work. The existing 11 tools'
  behavior is unchanged.
- **No migration of TastyTrade MCP.** That's already on Vercel; this is a
  separate service.
- **No tool optimization.** SEC EDGAR fetches in `build_dcf_config` and
  `refresh_all_valuations` stay as they are; Cloud Run's 600s timeout
  accommodates them.
- **No GitHub Actions / CI pipeline yet.** First deploy is manual via
  `gcloud run deploy --source .`. Add CI when deploys become frequent.
- **No public sign-up.** Only existing lazytheta.io users can authenticate.
  New users still sign up via lazytheta.io first.

## Architecture

Mirror of `tastytrade-mcp-cloudrun/` (which runs on Vercel; the directory name
is legacy). Same shape: Starlette ASGI app + custom JSON-RPC dispatcher +
OAuth bridge + JWT-based session. Different IdP (Supabase Auth instead of
TastyTrade) and different toolset (DCF tools instead of TT tools).

### Component layout

```
lazytheta-mcp-cloudrun/
├── main.py              # Starlette ASGI app + SmartAuthMiddleware + routes
├── mcp_handler.py       # JSON-RPC dispatcher; routes 11 tools
├── auth.py              # Supabase Auth OAuth bridge + JWT helpers
├── Dockerfile           # python:3.13-slim base
├── requirements.txt     # starlette, uvicorn, httpx, pyjwt, supabase, ...
└── README.md            # local dev + deploy instructions
```

### Code reuse

The Cloud Run handler imports the existing `_*_impl` functions from
`mcp_server.py` (the local stdio MCP) instead of duplicating logic. These
functions are already separated from FastMCP tool wrappers, so they're
suitable for direct reuse:

```python
from mcp_server import (
    _build_dcf_config_impl,
    _calculate_valuation_impl,
    _calculate_multi_lens_valuation_impl,
    _refresh_all_valuations_impl,
    _save_to_watchlist_impl,
    _get_config_impl,
    _get_watchlist_impl,
    _update_valuation_inputs_impl,
)
```

The 3 pre-scan impl functions (`_get_prescan_prompts_impl`,
`_get_prescan_sections_impl`, `_save_prescan_section_impl`) need a light
port: they currently rely on a module-level `USER_ID = os.environ["LAZYTHETA_USER_ID"]`,
which the Cloud Run handler must override per-request from the JWT.

### Required `mcp_server.py` change (multi-user-ify)

Each `_*_impl` function currently uses the module-level `USER_ID`. For
multi-user Cloud Run, they must accept an optional `user_id` parameter
that defaults to the env var (preserving stdio MCP behavior):

```python
def _get_watchlist_impl(user_id: str | None = None) -> str:
    user_id = user_id or USER_ID
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=user_id)
    return json.dumps(entries, default=str)
```

Same shape for the other 10 impl functions. Stdio MCP callers don't pass
`user_id` and continue using the env var; Cloud Run handlers pass the JWT-derived
user_id explicitly.

### Auth flow — Supabase Auth bridge

The user logs in with their existing lazytheta.io credentials (Supabase Auth)
through an OAuth 2.1 + PKCE bridge that wraps Supabase as the IdP.

Two login methods on `/oauth/authorize`:
- **Magic link** (default): user enters email → server triggers Supabase
  `auth.signInWithOtp({email})` → user clicks link in email → server
  verifies token → issues auth-code-JWT
- **Email+password** (optional speedup): form post directly to Supabase
  `auth.signInWithPassword(...)` → server gets user_id → issues auth-code-JWT

Detailed flow:

1. **claude.ai** → `GET /oauth/authorize?redirect_uri=X&code_challenge=Y&state=Z`
   - Server generates and signs state-JWT containing (X, Y, Z)
   - Server renders simple HTML login page with magic link form + password form
2. **User** picks method:
   - **Magic link**: submit email → server calls Supabase Auth `signInWithOtp`,
     embedding state-JWT in the redirect_to → email arrives with link to
     `/oauth/magic-callback?token=X&state=state-JWT`
   - **Password**: submit form → server calls Supabase Auth `signInWithPassword`
     directly → gets `user_id` immediately (skips email roundtrip)
3. **`/oauth/magic-callback`** (or password path) → server verifies/uses
   Supabase access token → recovers `user_id` (UUID) → recovers (X, Y, Z)
   from state-JWT → issues auth-code-JWT containing `{user_id, claude_code_challenge}`
   → `302 Location: X?code=auth-code-JWT&state=Z`
4. **claude.ai** → `POST /oauth/token` with `grant_type=authorization_code`,
   `code=auth-code-JWT`, `code_verifier`, `redirect_uri`
   - Server verifies auth-code-JWT signature
   - Server verifies claude.ai PKCE: `SHA256(code_verifier) == claude_code_challenge`
   - Server issues access-token-JWT containing `{user_id}` with 30-day TTL
   - Returns `{"access_token": "...", "token_type": "Bearer", "expires_in": 2592000}`
5. **claude.ai** → all subsequent requests carry `Authorization: Bearer <access-token-JWT>`
6. **`SmartAuthMiddleware`** ASGI middleware extracts and validates the JWT,
   stashes `user_id` in `scope["state"]["user_id"]`
7. **`/mcp` handler** reads `user_id` from scope and passes it to every tool's
   impl function

JWT signing key: `JWT_SIGNING_KEY` env var, separate from any other service.
64-byte random secret. HS256 signing.

### Routes

| Path | Auth | Purpose |
|------|------|---------|
| `/mcp` | JWT required | MCP JSON-RPC dispatcher (11 tools) |
| `/.well-known/oauth-authorization-server` | public | OAuth metadata (claude.ai discovery) |
| `/.well-known/oauth-protected-resource` | public | Resource metadata |
| `/oauth/register` | public | Dynamic Client Registration (claude.ai handshake) |
| `/oauth/authorize` | public | Login page (magic link OR password) |
| `/oauth/magic-callback` | public | Magic link return; verifies Supabase token |
| `/oauth/token` | public | claude.ai exchange code → access token |
| `/health` | public | Liveness probe |

`SmartAuthMiddleware` allow-list (no JWT required): paths starting with
`/oauth/`, `/.well-known/`, or `/health`.

### MCP JSON-RPC handler

Mirror of `tastytrade-mcp-cloudrun/mcp_handler.py`'s shape. Each tool has an
async wrapper:

```python
async def _tool_get_watchlist(user_id: str, args: dict) -> Any:
    return _get_watchlist_impl(user_id=user_id)

async def _tool_calculate_multi_lens_valuation(user_id: str, args: dict) -> Any:
    return _calculate_multi_lens_valuation_impl(
        ticker=args["ticker"],
        scenario_grid=args.get("scenario_grid", False),
        user_id=user_id,
    )
```

The dispatcher (`mcp_endpoint`) routes JSON-RPC `tools/call` requests by
`name` to the right async wrapper. `tools/list` returns the static schema
list.

11 tool wrappers total, one per existing impl function.

### Cloud Run configuration

Deployed via `gcloud run deploy` with these settings:

- **Project**: `stock-analysis-489016`
- **Service name**: `lazytheta-mcp`
- **Region**: `europe-west4` (Eemshaven NL — lowest latency from user's location)
- **Min instances**: 0 (no idle cost, ~3-5s cold start)
- **Max instances**: 5 (ample for single-user)
- **CPU**: 1 vCPU
- **Memory**: 1 GiB (covers pandas + yfinance + sec_edgar runtime working set)
- **Timeout**: 600s (covers `refresh_all_valuations` worst-case)
- **Concurrency**: 80 (Cloud Run default; handlers are IO-bound on Supabase + yfinance)
- **Authentication**: `--allow-unauthenticated` at the platform level (our
  Starlette app does its own JWT auth)
- **Container port**: 8080 (Cloud Run convention; Starlette via uvicorn binds to it)

### Environment variables

Set via `gcloud secrets` and `--set-env-vars`:

| Var | Source | Purpose |
|-----|--------|---------|
| `SUPABASE_URL` | existing lazytheta.io project | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | existing | Service-role key for server-side queries (bypasses RLS for the JWT-authenticated user) |
| `SUPABASE_ANON_KEY` | existing | For Supabase Auth API calls (`signInWithOtp`, `signInWithPassword`) |
| `JWT_SIGNING_KEY` | NEW (generate 64 random bytes) | Signs our auth-code-JWT and access-token-JWT |
| `MCP_PUBLIC_URL` | NEW | E.g. `https://lazytheta-mcp-xxxxx-ew.a.run.app` — used in OAuth metadata responses and redirect URLs |

### Dockerfile

Standard Python slim base. **Build context is the repo root** (`stock-analysis/`),
not the `lazytheta-mcp-cloudrun/` subdir, so that the Dockerfile can `COPY`
shared modules from the parent. `.gcloudignore` excludes everything except
the cloudrun subdir + the specific shared modules we need:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY lazytheta-mcp-cloudrun/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the cloudrun handler + the shared modules from the repo root.
COPY lazytheta-mcp-cloudrun/*.py /app/
COPY mcp_server.py auto_fetch.py valuation_lenses.py \
     config_store.py dcf_calculator.py gather_data.py \
     scorecard_utils.py /app/

EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

Deploy command:

```bash
# Run from repo root, with --source pointing at the repo root
gcloud run deploy lazytheta-mcp \
    --source . \
    --dockerfile lazytheta-mcp-cloudrun/Dockerfile \
    ...
```

Alternative if `gcloud` doesn't support `--dockerfile` flag in our version:
keep the Dockerfile at repo root (`./Dockerfile`) and document that this
is the canonical Dockerfile for the Cloud Run service.

### claude.ai connector registration

Once deployed, register on claude.ai:

1. Settings → Connectors → "Add custom connector"
2. URL: `https://lazytheta-mcp-<hash>-ew.a.run.app/mcp`
3. claude.ai auto-discovers OAuth via `/.well-known/oauth-authorization-server`
4. First use: claude.ai redirects to `/oauth/authorize` → user logs in →
   token persisted by claude.ai in their connector store
5. Tools appear in the connector list (11 tools, namespaced as
   `lazytheta-dcf:<tool_name>`)

## Implementation breakdown

Logically split into 5 sub-tasks; each is a self-contained commit + tested.
Single feature branch `feature/lazytheta-mcp-cloudrun`.

### Sub-task 1: Multi-user-ify `mcp_server.py`

**Why first:** auth bridge needs `_*_impl` functions to accept `user_id`
explicitly. Independent of Cloud Run; can ship to main on its own.

- Add optional `user_id: str | None = None` parameter to each `_*_impl`
- Default fallback: `user_id = user_id or USER_ID` (preserves stdio behavior)
- Update `config_store.load_user_prefs/save_user_prefs` similarly if not done
- Tests: existing test_mcp_server.py tests stay green; add 1-2 tests
  verifying explicit `user_id` overrides the env var

### Sub-task 2: Scaffold Cloud Run service

- Create `lazytheta-mcp-cloudrun/` directory
- `main.py`: Starlette app, `SmartAuthMiddleware`, route table
- `mcp_handler.py`: JSON-RPC dispatcher with `_tool_get_watchlist` only
  (smoke test for the rest)
- `Dockerfile`, `requirements.txt`, `README.md`
- Local dev: `uvicorn main:app --reload --port 8080`
- Smoke test: `curl http://localhost:8080/health` returns `{"status": "ok"}`

### Sub-task 3: OAuth bridge + Supabase Auth integration

- `auth.py`: JWT helpers, OAuth metadata responses, `oauth_authorize`,
  `oauth_token`, `oauth_register`
- `oauth_authorize` renders login page (HTML form with magic link + password)
- `oauth_magic_callback` verifies Supabase OTP token → recovers user_id
- Email+password path: POST to `/oauth/authorize/password` → Supabase
  `signInWithPassword` → user_id
- Auth-code-JWT and access-token-JWT signing/verification with `JWT_SIGNING_KEY`
- `SmartAuthMiddleware` extracts user_id from access-token-JWT
- Tests in `test_app.py`: OAuth flow happy path with mocked Supabase Auth,
  JWT validation (valid/expired/tampered), middleware allow-list

### Sub-task 4: Wire all 11 tools

- `mcp_handler.py`: implement remaining 10 `_tool_*` async wrappers
- Port 3 pre-scan impl functions to accept `user_id`
- `tools/list` static schema covers all 11 tools with proper input schemas
- Tests: dispatcher routes each tool name to correct wrapper; bad tool name
  returns JSON-RPC error; missing required arg returns error

### Sub-task 5 (deploy + smoke)

- `gcloud auth login` + `gcloud config set project stock-analysis-489016`
- Create secrets: `gcloud secrets create JWT_SIGNING_KEY ...` etc.
- `gcloud run deploy lazytheta-mcp --source . --region europe-west4 ...`
- Capture the URL output (`https://lazytheta-mcp-<hash>-ew.a.run.app`)
- Curl `/health` and `/.well-known/oauth-authorization-server` from external
- Register on claude.ai → trigger OAuth flow → verify magic link arrives
  and login completes
- Try each of the 11 tools from claude.ai chat with a sample ticker

## Tests

- **Unit tests** in `lazytheta-mcp-cloudrun/test_app.py` (~30 tests):
  - OAuth flow happy path with mocked `httpx` calls to Supabase Auth
  - JWT signing and verification (round-trip, tampered, expired)
  - `SmartAuthMiddleware` allow-list (public paths pass; protected without
    JWT returns 401)
  - JSON-RPC dispatcher: each tool name routes correctly; unknown tool
    returns JSON-RPC error -32601
  - Each tool wrapper: mock the underlying `_*_impl` to confirm `user_id`
    is passed through correctly
- **No integration tests** with real Cloud Run. Manual smoke after deploy.
- **Existing test suites stay green:** `test_mcp_server.py` (after
  multi-user-ify), `tests/test_multi_lens.py`, etc.

## Risks

- **Bundle size**: pandas + yfinance + sec_edgar = ~100MB Docker image.
  Cloud Run accepts up to 32GB so this is fine; cold start ~3-5s. Acceptable
  for personal mobile use.
- **SEC EDGAR rate limits from Cloud Run egress IP**: Google Cloud's egress
  IPs are shared across many tenants. SEC throttles aggressively per IP.
  For single-user low-frequency use, no issue; if it ever bites, fall back
  to fewer SEC fetches per request.
- **Magic link email deliverability**: Supabase sends from `noreply@mail.app.supabase.io`
  by default. Some mail providers may classify as spam. Mitigation: user
  can use the email+password path instead. Long-term: configure custom SMTP
  in Supabase if needed.
- **Cold start tax**: After 15 min idle the instance recycles. First request
  costs 3-5s. `--min-instances=1` solves this for ~$5/month. Skip for MVP.
- **OAuth metadata correctness**: claude.ai is strict about
  `/.well-known/oauth-authorization-server` shape. Mirror exactly what
  tastytrade-mcp does (proven working).
- **State-JWT in Supabase magic link `redirect_to`**: Supabase requires
  the redirect URL to be allowlisted in project settings. Need to add
  `https://lazytheta-mcp-<hash>-ew.a.run.app/oauth/magic-callback*` to the
  redirect URLs in Supabase Dashboard → Authentication → URL Configuration
  before first deploy. Document this in deploy README.

## Migration / rollout

- **Existing local stdio MCP**: stays as-is. Both stdio (Claude Desktop)
  and Cloud Run (claude.ai) coexist; user can choose.
- **No DB migration**: `lazytheta-mcp` reuses the same Supabase tables
  as the Streamlit app + stdio MCP. Multi-user RLS is already enforced;
  the new service just authenticates via Supabase Auth and uses the
  service-role key with explicit `user_id` filtering.
- **Backward compat**: stdio MCP keeps using `LAZYTHETA_USER_ID` env var
  (single-user mode); Cloud Run uses JWT-derived user_id (multi-user mode).
  Both paths work because `_*_impl` functions accept either.

## Open questions

None remaining. User confirmed:
- Auth: Supabase Auth via OAuth bridge (option A) ✅
- Tools: all 11 with code-reuse from `mcp_server.py` (option A) ✅
- Region: `europe-west4` (Eemshaven NL) ✅
- Magic link + email+password both supported on `/oauth/authorize` ✅
- Single combined plan, 5 sub-tasks ✅
