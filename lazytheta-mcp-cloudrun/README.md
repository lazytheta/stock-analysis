# LazyTheta DCF MCP — Cloud Run

Multi-user remote MCP for the LazyTheta DCF system. Authenticates via
Supabase Auth (the same accounts as lazytheta.io). All 11 tools from
the local stdio MCP are exposed remotely.

## Deployed URL

`https://lazytheta-mcp-93552884631.europe-west4.run.app/mcp`

Deployed 2026-05-11 to project `stock-analysis-489016`, region `europe-west4`.

(Legacy hash-based alias: `https://lazytheta-mcp-hmhla5v6ta-ez.a.run.app/mcp` — both work, GCP issues both.)

## Local development

```bash
cd lazytheta-mcp-cloudrun
pip install -r requirements.txt

# Set required env vars
export JWT_SIGNING_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
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

All 25 tests offline-mocked; no network or Supabase access required.

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
deploys take ~2-3 minutes. First deploy is ~5-10 minutes (Cloud Build +
Artifact Registry initialization).

## Logs

```bash
gcloud run services logs tail lazytheta-mcp --region europe-west4
```

## Architecture

See `docs/superpowers/specs/2026-05-09-lazytheta-mcp-cloudrun-design.md`
for the full design doc.

Quick summary:
- **Starlette ASGI app** with `SmartAuthMiddleware` (pure ASGI, no `BaseHTTPMiddleware`)
- **OAuth 2.1 + PKCE bridge** to Supabase Auth (magic link + email/password tabs)
- **JSON-RPC dispatcher** at `/mcp` routing 11 tool wrappers to `mcp_server._*_impl` functions
- **Multi-user**: each request carries a JWT with `user_id`; the impl functions accept `user_id` per call

## Updating

When `mcp_server.py` or its dependencies (`auto_fetch.py`, `valuation_lenses.py`,
etc.) change, redeploy via `gcloud run deploy ...`. The Cloud Run image
will pick up the latest version.

The local stdio MCP (Claude Desktop config) and this Cloud Run service
share the same `mcp_server.py` and `_*_impl` functions — keep them in
sync by deploying after stdio MCP changes.

## Pre-deploy checklist (one-time setup)

Before the FIRST deploy, you need:

1. **Install gcloud CLI**:
   ```bash
   brew install --cask google-cloud-sdk
   gcloud init
   gcloud auth login
   gcloud auth application-default login
   ```

2. **Enable required APIs** in project `stock-analysis-489016`:
   ```bash
   gcloud services enable \
       run.googleapis.com \
       artifactregistry.googleapis.com \
       cloudbuild.googleapis.com \
       --project stock-analysis-489016
   ```

3. **Create secrets** in GCP Secret Manager:
   ```bash
   # Generate a fresh 48-byte JWT signing key
   JWT_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
   echo -n "$JWT_KEY" | gcloud secrets create JWT_SIGNING_KEY --data-file=-

   # Pull existing Supabase env vars from your local Claude Desktop config
   # at ~/Library/Application Support/Claude/claude_desktop_config.json
   # and store them in GCP Secret Manager
   echo -n "<your-supabase-url>" | gcloud secrets create SUPABASE_URL --data-file=-
   echo -n "<service-role-key>" | gcloud secrets create SUPABASE_SERVICE_KEY --data-file=-
   echo -n "<anon-key>" | gcloud secrets create SUPABASE_ANON_KEY --data-file=-
   ```

4. **Configure Supabase redirect URL allowlist** (manual step in Supabase Dashboard):
   - Go to https://supabase.com/dashboard
   - Open the lazytheta.io project
   - Authentication → URL Configuration
   - Add to "Redirect URLs":
     ```
     https://lazytheta-mcp-*-ew.a.run.app/oauth/magic-callback
     ```
   - Save.

   Required so Supabase will accept our `email_redirect_to` param when issuing magic links.

## Registering on claude.ai

After first successful deploy:

1. Capture the URL from the deploy output (e.g., `https://lazytheta-mcp-abc123-ew.a.run.app`).
2. Update this README's "Deployed URL" section.
3. Open claude.ai → Settings → Connectors → Add custom connector.
4. URL: `https://lazytheta-mcp-<HASH>-ew.a.run.app/mcp`.
5. claude.ai auto-discovers OAuth via `/.well-known/oauth-authorization-server`.
6. Click "Authenticate" → redirects to our `/oauth/authorize` → log in with your lazytheta.io credentials (magic link or email+password) → connector connects.
7. The 11 tools appear under namespace `lazytheta-dcf:<tool_name>` in claude.ai.

## Security

- **`JWT_SIGNING_KEY`** is the only secret that, if leaked, allows token forgery.
  Rotate via `gcloud secrets versions add JWT_SIGNING_KEY` if compromised.
- **Service-role Supabase key** is also sensitive. The MCP service uses it to
  bypass RLS, then explicitly filters by JWT-derived user_id.
- All inter-service traffic is HTTPS via Cloud Run's automatic TLS.
- claude.ai stores the access-token-JWT per user; revoking a user's access
  requires either rotating `JWT_SIGNING_KEY` (revokes all users) or adding
  a per-user revocation list in our token verification (future work).
