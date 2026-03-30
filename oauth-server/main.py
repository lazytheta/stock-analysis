"""
Tastytrade OAuth Server — FastAPI microservice.

Handles the OAuth 2.0 authorization code flow between Streamlit and Tastytrade.
One job: let users click "Connect with Tastytrade" and store the refresh token.
"""

import base64
import hashlib
import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lazy Theta OAuth", docs_url=None, redoc_url=None)

# CORS — only allow the Streamlit app
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.STREAMLIT_APP_URL],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# State TTL in seconds (10 minutes)
_STATE_TTL = 600


# ─── State helpers (Supabase-backed) ────────────────────────────────────────

async def _store_state(state: str, user_id: str, code_verifier: str):
    """Persist OAuth state to Supabase so it survives server restarts."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{config.SUPABASE_URL}/rest/v1/oauth_state",
            json={
                "state": state,
                "user_id": user_id,
                "code_verifier": code_verifier,
            },
            headers={
                "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()


async def _pop_state(state: str) -> dict | None:
    """Retrieve and delete OAuth state. Returns {user_id, code_verifier} or None."""
    async with httpx.AsyncClient() as client:
        # Fetch the state row
        resp = await client.get(
            f"{config.SUPABASE_URL}/rest/v1/oauth_state",
            params={"state": f"eq.{state}", "select": "user_id,code_verifier,created_at"},
            headers={
                "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
                "Accept": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        row = rows[0]

        # Delete it (one-time use)
        await client.delete(
            f"{config.SUPABASE_URL}/rest/v1/oauth_state",
            params={"state": f"eq.{state}"},
            headers={
                "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
            },
            timeout=10,
        )

        # Check expiry
        from datetime import datetime, UTC
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        age = (datetime.now(UTC) - created).total_seconds()
        if age > _STATE_TTL:
            return None

        return {"user_id": row["user_id"], "code_verifier": row["code_verifier"]}


async def _cleanup_expired_states():
    """Remove state entries older than TTL."""
    from datetime import UTC, datetime, timedelta
    cutoff = (datetime.now(UTC) - timedelta(seconds=_STATE_TTL)).isoformat()
    try:
        async with httpx.AsyncClient() as client:
            await client.delete(
                f"{config.SUPABASE_URL}/rest/v1/oauth_state",
                params={"created_at": f"lt.{cutoff}"},
                headers={
                    "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
                },
                timeout=10,
            )
    except Exception as e:
        logger.warning("State cleanup failed: %s", e)


# ─── Endpoints ───────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/auth/tastytrade/login")
async def tastytrade_login(user_id: str = Query(..., description="Supabase user UUID")):
    """Start the OAuth flow: redirect user to Tastytrade's login page."""
    await _cleanup_expired_states()

    # Generate PKCE code verifier + challenge (S256)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge_b64 = base64.urlsafe_b64encode(code_challenge).rstrip(b"=").decode()

    # Generate state for CSRF protection
    state = secrets.token_urlsafe(32)
    await _store_state(state, user_id, code_verifier)

    # Build Tastytrade authorize URL
    params = {
        "client_id": config.TASTYTRADE_CLIENT_ID,
        "redirect_uri": config.TASTYTRADE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid offline_access read",
        "state": state,
        "code_challenge": code_challenge_b64,
        "code_challenge_method": "S256",
    }
    authorize_url = f"{config.TASTYTRADE_AUTHORIZE_URL}?{urlencode(params)}"

    logger.info("OAuth login started for user %s", user_id[:8])
    return RedirectResponse(authorize_url)


@app.get("/auth/tastytrade/callback")
async def tastytrade_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
):
    """Handle Tastytrade's redirect after user login."""
    app_url = config.STREAMLIT_APP_URL

    # User denied access
    if error:
        logger.warning("OAuth denied: %s", error)
        return RedirectResponse(f"{app_url}?tt_error=access_denied")

    # Missing params
    if not code or not state:
        logger.warning("OAuth callback missing code or state")
        return RedirectResponse(f"{app_url}?tt_error=connection_failed")

    # Validate state (CSRF protection)
    pending = await _pop_state(state)
    if not pending:
        logger.warning("OAuth state invalid or expired")
        return RedirectResponse(f"{app_url}?tt_error=session_expired")

    user_id = pending["user_id"]
    code_verifier = pending["code_verifier"]

    # Exchange authorization code for tokens
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                config.TASTYTRADE_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": config.TASTYTRADE_REDIRECT_URI,
                    "client_id": config.TASTYTRADE_CLIENT_ID,
                    "client_secret": config.TASTYTRADE_CLIENT_SECRET,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            tokens = response.json()
    except httpx.HTTPStatusError as e:
        logger.error("Token exchange failed: %s %s", e.response.status_code, e.response.text[:500])
        return RedirectResponse(f"{app_url}?tt_error=token_exchange_failed")
    except Exception as e:
        logger.error("Token exchange error: %s %s", type(e).__name__, str(e)[:300])
        return RedirectResponse(f"{app_url}?tt_error=token_exchange_failed")

    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        logger.error("No refresh_token in response. Keys: %s", list(tokens.keys()))
        return RedirectResponse(f"{app_url}?tt_error=token_exchange_failed")

    # Store refresh token in Supabase
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{config.SUPABASE_URL}/rest/v1/user_credentials",
                json={
                    "user_id": user_id,
                    "service_name": "tastytrade_refresh_token",
                    "credential": refresh_token,
                },
                headers={
                    "apikey": config.SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_SERVICE_ROLE_KEY}",
                    "Content-Type": "application/json",
                    "Prefer": "resolution=merge-duplicates",
                },
                timeout=10,
            )
            resp.raise_for_status()
        logger.info("OAuth tokens stored for user %s", user_id[:8])
    except Exception as e:
        logger.error("Supabase store failed: %s %s", type(e).__name__, str(e)[:300])
        return RedirectResponse(f"{app_url}?tt_error=storage_failed")

    return RedirectResponse(f"{app_url}?tt_connected=true")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
