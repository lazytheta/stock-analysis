"""OAuth 2.1 + PKCE bridge for claude.ai ↔ our server ↔ TastyTrade.

Multi-user model — each user authorizes their own TastyTrade account.

Flow:
1. claude.ai → GET /oauth/authorize?redirect_uri=X&code_challenge=Y&state=Z
   We generate our own PKCE pair (verifier/challenge) for the trip to TT.
   Encode (X, Y, Z, our verifier) in a signed state-JWT.
   Redirect to TastyTrade login URL with state=state-JWT.

2. User logs in at TastyTrade and approves access.

3. TastyTrade → GET /oauth/tt-callback?code=A&state=state-JWT
   Decode state-JWT to recover (X, Y, Z, our verifier).
   Exchange code A at TT token endpoint with our verifier and our client_secret.
   Receive TT refresh_token.
   Insert row in Supabase, get user_id (UUID).
   Auto-pick first TT account, store on row.
   Issue auth-code-JWT carrying {user_id, claude_code_challenge}.
   Redirect to X?code=auth-code-JWT&state=Z.

4. claude.ai → POST /oauth/token (grant_type=authorization_code, code=auth-code-JWT,
   code_verifier, redirect_uri).
   Verify our auth-code-JWT, verify claude.ai PKCE.
   Issue access-token-JWT carrying {user_id}. Return.

The resulting access token is a JWT containing user_id; SmartAuthMiddleware
validates it and attaches user_id to scope so tt_client can fetch the right
TastyTrade tokens from Supabase.
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

from storage import create_user_with_tt_token, set_account_id

ACCESS_TOKEN_TTL = 30 * 24 * 3600
AUTH_CODE_TTL = 5 * 60
STATE_TTL = 10 * 60  # how long the user has between /authorize and TT redirect-back

TT_AUTHORIZE_URL = "https://my.tastytrade.com/auth.html"
TT_TOKEN_URL = "https://api.tastytrade.com/oauth/token"
TT_API_BASE = os.environ.get("TASTYTRADE_API_BASE", "https://api.tastyworks.com")
TT_SCOPE = "openid offline_access read"


def _signing_key() -> str:
    key = os.environ.get("JWT_SIGNING_KEY", "")
    if not key:
        raise RuntimeError("JWT_SIGNING_KEY env var not configured")
    return key


def _tt_client_id() -> str:
    v = os.environ.get("TASTYTRADE_CLIENT_ID", "")
    if not v:
        raise RuntimeError("TASTYTRADE_CLIENT_ID env var not configured")
    return v


def _tt_client_secret() -> str:
    v = os.environ.get("TASTYTRADE_CLIENT_SECRET", "")
    if not v:
        raise RuntimeError("TASTYTRADE_CLIENT_SECRET env var not configured")
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


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) using S256."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


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


# ---- /oauth/authorize: claude.ai → us → redirect to TT ----


async def oauth_authorize(request: Request) -> Response:
    qp = request.query_params
    claude_redirect = qp.get("redirect_uri", "")
    claude_state = qp.get("state", "")
    claude_code_challenge = qp.get("code_challenge", "")
    claude_code_challenge_method = qp.get("code_challenge_method", "S256")
    claude_client_id = qp.get("client_id", "")

    if not claude_redirect:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "missing redirect_uri"},
            status_code=400,
        )
    if not claude_client_id:
        return JSONResponse(
            {"error": "invalid_request", "error_description": "missing client_id"},
            status_code=400,
        )
    if claude_code_challenge_method != "S256":
        return JSONResponse(
            {"error": "invalid_request", "error_description": "only PKCE S256 supported"},
            status_code=400,
        )

    # Validate redirect_uri against the URIs registered for this client.
    # Prevents OAuth-phishing where an attacker tricks a victim into
    # logging into TastyTrade and redirects the auth code to attacker.com.
    client_payload = verify_jwt(claude_client_id)
    if not client_payload or client_payload.get("type") != "client":
        return JSONResponse(
            {"error": "invalid_request", "error_description": "invalid client_id"},
            status_code=400,
        )
    registered_uris = client_payload.get("redirect_uris") or []
    if not registered_uris or claude_redirect not in registered_uris:
        return JSONResponse(
            {
                "error": "invalid_request",
                "error_description": "redirect_uri not registered for this client",
            },
            status_code=400,
        )

    tt_verifier, tt_challenge = _pkce_pair()
    state_jwt = sign_jwt(
        {
            "type": "tt_state",
            "claude_redirect": claude_redirect,
            "claude_state": claude_state,
            "claude_code_challenge": claude_code_challenge,
            "tt_verifier": tt_verifier,
        },
        ttl_seconds=STATE_TTL,
    )

    base = _base_url(request)
    tt_redirect_uri = f"{base}/oauth/tt-callback"

    tt_params = {
        "client_id": _tt_client_id(),
        "redirect_uri": tt_redirect_uri,
        "response_type": "code",
        "scope": TT_SCOPE,
        "state": state_jwt,
        "code_challenge": tt_challenge,
        "code_challenge_method": "S256",
    }
    tt_url = f"{TT_AUTHORIZE_URL}?{urlencode(tt_params)}"
    return RedirectResponse(tt_url, status_code=302)


# ---- /oauth/tt-callback: TT → us → redirect back to claude.ai ----


async def oauth_tt_callback(request: Request) -> Response:
    qp = request.query_params
    error = qp.get("error")
    if error:
        return _html_error(f"TastyTrade weigerde de autorisatie: {error}")

    code = qp.get("code")
    state = qp.get("state")
    if not code or not state:
        return _html_error("Ontbrekende code of state in de callback URL.")

    payload = verify_jwt(state)
    if not payload or payload.get("type") != "tt_state":
        return _html_error("Ongeldige of verlopen state.")

    base = _base_url(request)
    tt_redirect_uri = f"{base}/oauth/tt-callback"

    # Exchange TT auth code for tokens.
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                TT_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": tt_redirect_uri,
                    "client_id": _tt_client_id(),
                    "client_secret": _tt_client_secret(),
                    "code_verifier": payload["tt_verifier"],
                },
            )
    except Exception as e:
        return _html_error(f"Kon TastyTrade niet bereiken: {e}")

    if resp.status_code >= 400:
        return _html_error(f"TastyTrade token-uitwisseling faalde: {resp.status_code} {resp.text[:200]}")

    tokens = resp.json()
    refresh_token = tokens.get("refresh_token")
    access_token = tokens.get("access_token")
    if not refresh_token:
        return _html_error("Geen refresh_token ontvangen van TastyTrade.")

    # Create user row in Supabase.
    try:
        user_id = await create_user_with_tt_token(refresh_token)
    except Exception as e:
        return _html_error(f"Kon credentials niet opslaan: {e}")

    # Auto-select first account (MVP — picker can be added later).
    if access_token:
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                acct_resp = await http.get(
                    f"{TT_API_BASE}/customers/me/accounts",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if acct_resp.status_code < 400:
                items = acct_resp.json().get("data", {}).get("items", [])
                if items:
                    first = items[0].get("account", items[0])
                    account_number = first.get("account-number") or first.get("account_number")
                    if account_number:
                        await set_account_id(user_id, account_number)
        except Exception:
            pass  # account picker can fix this later

    # Build our auth code (JWT) for claude.ai.
    auth_code = sign_jwt(
        {
            "type": "auth_code",
            "user_id": user_id,
            "code_challenge": payload["claude_code_challenge"],
            "redirect_uri": payload["claude_redirect"],
        },
        ttl_seconds=AUTH_CODE_TTL,
    )

    claude_redirect = payload["claude_redirect"]
    sep = "&" if "?" in claude_redirect else "?"
    location = f"{claude_redirect}{sep}{urlencode({'code': auth_code, 'state': payload.get('claude_state', '')})}"
    return RedirectResponse(location, status_code=302)


# ---- /oauth/token: claude.ai exchanges our auth code for access token ----


async def oauth_token(request: Request) -> JSONResponse:
    form = await request.form()
    grant_type = form.get("grant_type", "")
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    code = form.get("code", "")
    code_verifier = form.get("code_verifier", "")
    redirect_uri = form.get("redirect_uri", "")

    payload = verify_jwt(code)
    if not payload or payload.get("type") != "auth_code":
        return JSONResponse({"error": "invalid_grant", "error_description": "bad code"}, status_code=400)
    if payload.get("redirect_uri") != redirect_uri:
        return JSONResponse({"error": "invalid_grant", "error_description": "redirect_uri mismatch"}, status_code=400)
    if not _verify_pkce(code_verifier, payload.get("code_challenge", "")):
        return JSONResponse({"error": "invalid_grant", "error_description": "PKCE mismatch"}, status_code=400)

    user_id = payload.get("user_id")
    if not user_id:
        return JSONResponse({"error": "invalid_grant", "error_description": "missing user_id"}, status_code=400)

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


# ---- HTML helpers ----


def _html_error(message: str) -> HTMLResponse:
    safe = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    body = f"""<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><title>OAuth fout</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:-apple-system,system-ui,sans-serif;max-width:480px;margin:64px auto;padding:0 16px;color:#111;}}
h2{{margin:0 0 12px;}} p{{color:#333;}} .err{{background:#fff5f5;border:1px solid #fdb;padding:12px;border-radius:8px;color:#900;}}
</style></head><body><h2>Iets ging mis</h2><div class="err">{safe}</div>
<p style="margin-top:24px;color:#888;">Sluit dit venster en probeer het opnieuw vanuit Claude.</p>
</body></html>"""
    return HTMLResponse(body, status_code=400)
