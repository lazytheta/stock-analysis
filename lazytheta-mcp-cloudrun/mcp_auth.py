"""JWT helpers + OAuth 2.1 + PKCE bridge to Supabase Auth.

claude.ai → /oauth/authorize → user logs in via Supabase (magic link or
password) → /oauth/magic-callback → /oauth/token → access-token-JWT.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
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
