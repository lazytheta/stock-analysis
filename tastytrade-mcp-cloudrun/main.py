"""TastyTrade MCP Server -- Cloud Run wrapper with bearer token auth."""

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
