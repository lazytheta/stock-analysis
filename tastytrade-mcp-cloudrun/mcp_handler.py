"""Stateless MCP JSON-RPC dispatcher (multi-user).

Each /mcp request carries a JWT bearer; SmartAuthMiddleware extracts
user_id from the JWT and stashes it in scope["state"]["user_id"]. This
handler reads it from request.scope and passes it to every tool.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import tt_client
from tt_client import TastyTradeError

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "tastytrade-mcp"
SERVER_VERSION = "0.2.0"

logger = logging.getLogger(__name__)


# ---- Tool implementations ----


async def _tool_get_positions(user_id: str, args: dict) -> Any:
    return await tt_client.get_positions(user_id)


async def _tool_get_account_balance(user_id: str, args: dict) -> Any:
    return await tt_client.get_balances(user_id)


async def _tool_get_quotes(user_id: str, args: dict) -> Any:
    symbols = args.get("symbols") or []
    if not symbols:
        raise ValueError("symbols (list[str]) is required")
    instrument_type = args.get("instrument_type", "equities")
    return await tt_client.get_quotes(user_id, symbols, instrument_type=instrument_type)


async def _tool_get_market_metrics(user_id: str, args: dict) -> Any:
    symbols = args.get("symbols") or []
    if not symbols:
        raise ValueError("symbols (list[str]) is required")
    return await tt_client.get_market_metrics(user_id, symbols)


async def _tool_get_option_chain(user_id: str, args: dict) -> Any:
    symbol = args.get("symbol")
    if not symbol:
        raise ValueError("symbol is required")
    chain = await tt_client.get_option_chain_nested(user_id, symbol)
    expiration = args.get("expiration")
    if expiration and isinstance(chain, dict):
        items = chain.get("items") or []
        for item in items:
            exps = item.get("expirations") or []
            item["expirations"] = [e for e in exps if e.get("expiration-date") == expiration]
    return chain


async def _tool_get_transactions(user_id: str, args: dict) -> Any:
    return await tt_client.get_transactions(
        user_id,
        start_date=args.get("start_date"),
        end_date=args.get("end_date"),
        symbol=args.get("symbol"),
        types=args.get("types"),
        per_page=int(args.get("per_page", 250)),
        page_offset=int(args.get("page_offset", 0)),
    )


async def _tool_get_recent_orders(user_id: str, args: dict) -> Any:
    return await tt_client.get_orders(user_id, status=args.get("status"))


async def _tool_get_watchlists(user_id: str, args: dict) -> Any:
    name = args.get("name")
    if name:
        return await tt_client.get_watchlist(user_id, name)
    return await tt_client.get_watchlists(user_id)


# ---- Tool definitions (MCP wire format) ----

TOOLS: list[dict] = [
    {
        "name": "get_positions",
        "description": (
            "List all open positions in the user's TastyTrade account. Returns symbol, "
            "underlying-symbol, instrument-type, quantity, direction, average-open-price, "
            "close-price (current), expires-at (for options), and realized/unrealized P&L."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_account_balance",
        "description": (
            "Get current account balances: cash-balance, equity-buying-power, "
            "derivative-buying-power, net-liquidating-value, maintenance-requirement."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_quotes",
        "description": (
            "Snapshot quotes for one or more symbols. Returns bid, ask, last, mark, "
            "volume, open-interest, day high/low, prev-close. For options pass "
            "instrument_type='equity-options' with TastyTrade-format option symbols. "
            "NOTE: This REST endpoint does NOT return Greeks or IV — those live on "
            "the streamer only. For option Greeks/IV per strike, call get_option_chain "
            "instead (the nested chain includes implied-volatility per strike)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbols": {"type": "array", "items": {"type": "string"}},
                "instrument_type": {
                    "type": "string",
                    "enum": [
                        "equities",
                        "equity-options",
                        "indices",
                        "futures",
                        "future-options",
                        "cryptocurrencies",
                    ],
                    "default": "equities",
                },
            },
            "required": ["symbols"],
        },
    },
    {
        "name": "get_market_metrics",
        "description": (
            "Per-underlying metrics for options strategy: implied-volatility-index-rank "
            "(IV rank), implied-volatility-percentile, historical-volatility-30-day, beta, "
            "liquidity-rating, earnings.expected-report-date, dividend-ex-date."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"symbols": {"type": "array", "items": {"type": "string"}}},
            "required": ["symbols"],
        },
    },
    {
        "name": "get_option_chain",
        "description": (
            "Full option chain for a symbol (all expirations and strikes, calls+puts). "
            "Nested structure with bid/ask, IV, Greeks, OI, volume per strike. Optional "
            "expiration filter (YYYY-MM-DD)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "expiration": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "get_transactions",
        "description": (
            "Trade history (fills, money movements, dividends, etc.) with date-range, "
            "symbol, type filters. Paginated. For multi-year history, call with "
            "smaller windows to stay within Vercel's 10s timeout."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "symbol": {"type": "string"},
                "types": {"type": "array", "items": {"type": "string"}},
                "per_page": {"type": "integer", "default": 250},
                "page_offset": {"type": "integer", "default": 0},
            },
            "required": [],
        },
    },
    {
        "name": "get_recent_orders",
        "description": (
            "List orders. status='Live' for working/open, 'Filled' for filled, omit for all."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "get_watchlists",
        "description": (
            "List the user's saved TastyTrade watchlists, or fetch one by name. "
            "Without name, returns all watchlist names + metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": [],
        },
    },
]


TOOL_HANDLERS: dict[str, Callable[[str, dict], Awaitable[Any]]] = {
    "get_positions": _tool_get_positions,
    "get_account_balance": _tool_get_account_balance,
    "get_quotes": _tool_get_quotes,
    "get_market_metrics": _tool_get_market_metrics,
    "get_option_chain": _tool_get_option_chain,
    "get_transactions": _tool_get_transactions,
    "get_recent_orders": _tool_get_recent_orders,
    "get_watchlists": _tool_get_watchlists,
}


# ---- JSON-RPC dispatch ----


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
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        if not user_id:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": "Authenticated user required"},
            }
        tool_name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
            }
        try:
            result = await handler(user_id, arguments)
        except (TastyTradeError, ValueError) as e:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }
        except Exception as e:
            logger.exception("Tool %s failed", tool_name)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Internal error: {e}"}],
                    "isError": True,
                },
            }

        text = json.dumps(result, indent=2, default=str)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            },
        }

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
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "GET not supported"}},
                status_code=405,
            )
        if request.method == "DELETE":
            return Response(status_code=200)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
                status_code=400,
            )

        user_id = request.scope.get("state", {}).get("user_id")

        if isinstance(body, list):
            responses: list[dict] = []
            for msg in body:
                r = await _handle_one(msg, user_id)
                if r is not None:
                    responses.append(r)
            if not responses:
                return Response(status_code=202)
            return JSONResponse(responses)

        if not isinstance(body, dict):
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}},
                status_code=400,
            )

        response = await _handle_one(body, user_id)
        if response is None:
            return Response(status_code=202)
        return JSONResponse(response)
    except Exception:
        # Log the full traceback to stderr (Vercel captures stderr); do not
        # leak file paths or library internals to the client.
        logger.exception("mcp_endpoint failed")
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": "Internal server error"},
            },
            status_code=500,
        )
