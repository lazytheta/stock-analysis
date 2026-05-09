"""LazyTheta DCF MCP Server -- multi-user, OAuth bridge to Supabase Auth.

Routes (filled in across Tasks 2-4):
- /health                                    Liveness probe
- /mcp                                       MCP JSON-RPC (auth: JWT with user_id)
- /.well-known/oauth-authorization-server    OAuth metadata (Task 3)
- /.well-known/oauth-protected-resource      Resource metadata (Task 3)
- /oauth/register                            Dynamic Client Registration (Task 3)
- /oauth/authorize                           claude.ai entry -> Supabase login (Task 3)
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

from mcp_auth import (
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


app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
