"""Stateless MCP JSON-RPC dispatcher. Stub: only handles initialize + ping +
tools/list with empty list. Tools are wired in Task 4."""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lazytheta-mcp"
SERVER_VERSION = "0.1.0"

logger = logging.getLogger(__name__)


async def _handle_one(message: dict, user_id: str | None) -> dict | None:
    method = message.get("method")
    params = message.get("params") or {}
    request_id = message.get("id")
    is_notification = "id" not in message

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method in ("notifications/initialized", "notifications/cancelled", "notifications/progress"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": []}}

    if is_notification:
        return None
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


async def mcp_endpoint(request: Request) -> Response:
    try:
        if request.method == "GET":
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600, "message": "GET not supported"}},
                status_code=405,
            )
        if request.method == "DELETE":
            return Response(status_code=200)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        user_id = request.scope.get("state", {}).get("user_id")

        if isinstance(body, list):
            responses = []
            for msg in body:
                r = await _handle_one(msg, user_id)
                if r is not None:
                    responses.append(r)
            if not responses:
                return Response(status_code=202)
            return JSONResponse(responses)

        if not isinstance(body, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32600, "message": "Invalid request"}},
                status_code=400,
            )

        response = await _handle_one(body, user_id)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)
    except Exception:
        logger.exception("mcp_endpoint failed")
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": -32603, "message": "Internal server error"}},
            status_code=500,
        )
