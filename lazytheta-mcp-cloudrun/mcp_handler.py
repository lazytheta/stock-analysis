"""Stateless MCP JSON-RPC dispatcher for LazyTheta DCF (multi-user).

Each /mcp request carries a JWT bearer; SmartAuthMiddleware extracts
user_id from the JWT and stashes it in scope["state"]["user_id"]. This
handler reads it from request.scope and passes it to every tool's
_*_impl function.

The 11 tools call into mcp_server.py's _*_impl functions, which were
multi-user-ified in Task 1 to accept an explicit user_id parameter.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# These imports come from the repo root; the Dockerfile copies them into /app
import mcp_server

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "lazytheta-mcp"
SERVER_VERSION = "1.0.0"

logger = logging.getLogger(__name__)


# ---- Tool implementations ----


async def _tool_build_dcf_config(user_id: str, args: dict) -> Any:
    return mcp_server._build_dcf_config_impl(
        ticker=args["ticker"],
        financial_data=args["financial_data"],
        company_name=args["company_name"],
        sic_code=args.get("sic_code"),
        sic_description=args.get("sic_description", ""),
        margin_of_safety=args.get("margin_of_safety"),
        terminal_growth=args.get("terminal_growth"),
        sector_margin=args.get("sector_margin"),
        consensus=args.get("consensus"),
        valuation_basis=args.get("valuation_basis", "nominal"),
        user_id=user_id,
    )


async def _tool_calculate_valuation(user_id: str, args: dict) -> Any:
    return mcp_server._calculate_valuation_impl(args["config"], user_id=user_id)


async def _tool_calculate_multi_lens_valuation(user_id: str, args: dict) -> Any:
    return mcp_server._calculate_multi_lens_valuation_impl(
        ticker=args["ticker"],
        scenario_grid=args.get("scenario_grid", False),
        user_id=user_id,
    )


async def _tool_refresh_all_valuations(user_id: str, args: dict) -> Any:
    return mcp_server._refresh_all_valuations_impl(
        force=args.get("force", False),
        user_id=user_id,
    )


async def _tool_save_to_watchlist(user_id: str, args: dict) -> Any:
    return mcp_server._save_to_watchlist_impl(
        ticker=args["ticker"],
        cfg=args["config"],
        user_id=user_id,
    )


async def _tool_get_config(user_id: str, args: dict) -> Any:
    return mcp_server._get_config_impl(args["ticker"], user_id=user_id)


async def _tool_get_watchlist(user_id: str, args: dict) -> Any:
    return mcp_server._get_watchlist_impl(user_id=user_id)


async def _tool_update_valuation_inputs(user_id: str, args: dict) -> Any:
    return mcp_server._update_valuation_inputs_impl(
        ticker=args["ticker"],
        fields=args["fields"],
        user_id=user_id,
    )


async def _tool_update_lens_weights(user_id: str, args: dict) -> Any:
    return mcp_server._update_lens_weights_impl(
        ticker=args["ticker"],
        weights=args["weights"],
        user_id=user_id,
    )


async def _tool_get_prescan_prompts(user_id: str, args: dict) -> Any:
    return mcp_server._get_prescan_prompts_impl(args["ticker"], user_id=user_id)


async def _tool_get_prescan_sections(user_id: str, args: dict) -> Any:
    return mcp_server._get_prescan_sections_impl(args["ticker"], user_id=user_id)


async def _tool_save_prescan_section(user_id: str, args: dict) -> Any:
    return mcp_server._save_prescan_section_impl(
        ticker=args["ticker"],
        title=args["title"],
        content=args["content"],
        user_id=user_id,
    )


# ---- Tool definitions (MCP wire format) ----

TOOLS: list[dict] = [
    {
        "name": "build_dcf_config",
        "description": (
            "Build a complete DCF configuration from SEC financial data. "
            "Wraps gather_data.build_config; assembles sector betas, peer set, "
            "stock price, and base assumptions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "financial_data": {"type": "object"},
                "company_name": {"type": "string"},
                "sic_code": {"type": "string"},
                "sic_description": {"type": "string"},
                "margin_of_safety": {"type": "number"},
                "terminal_growth": {"type": "number"},
                "sector_margin": {"type": "number"},
                "consensus": {"type": "object"},
                "valuation_basis": {"type": "string", "enum": ["nominal", "real"]},
            },
            "required": ["ticker", "financial_data", "company_name"],
        },
    },
    {
        "name": "calculate_valuation",
        "description": (
            "Calculate intrinsic value, WACC, and reverse DCF from a config. "
            "Returns wacc, intrinsic_value, buy_price, enterprise_value, "
            "equity_value, tv_pct, implied_growth, implied_margin."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"config": {"type": "object"}},
            "required": ["config"],
        },
    },
    {
        "name": "calculate_multi_lens_valuation",
        "description": (
            "Run the multi-lens fair value (DCF + Peers + Historical + Dividend "
            "+ Reverse DCF) for a watchlist ticker. Auto-fetches market inputs, "
            "peer multiples, and dividend history first. Stores summary back."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "scenario_grid": {"type": "boolean", "default": False},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "refresh_all_valuations",
        "description": (
            "Recompute multi-lens fair value across all watchlist tickers in "
            "parallel. force=True ignores the 7-day staleness check."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"force": {"type": "boolean", "default": False}},
            "required": [],
        },
    },
    {
        "name": "save_to_watchlist",
        "description": "Upsert a complete DCF config into the user's watchlist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "config": {"type": "object"},
            },
            "required": ["ticker", "config"],
        },
    },
    {
        "name": "get_config",
        "description": "Read an existing DCF config by ticker.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_watchlist",
        "description": (
            "List all watchlist tickers with enriched metadata: fv_low/mid/high, "
            "buy_price, current_vs_mid, lens_count, verdict, phase."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_valuation_inputs",
        "description": (
            "Override one or more valuation_inputs fields for a watchlist "
            "ticker (e.g. dividend_5y_cagr, forward_eps, ttm_ebitda). Each "
            "updated field is removed from _auto_filled so the override "
            "survives the next yfinance refresh."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": "Dict of valuation_inputs keys to set",
                },
            },
            "required": ["ticker", "fields"],
        },
    },
    {
        "name": "update_lens_weights",
        "description": (
            "Override one or more lens weights for a watchlist ticker. "
            "Valid keys: dcf, multiples, historical, reverse_dcf, dividend. "
            "Specified keys merge into cfg.lens_weights; unspecified keys "
            "retain their value (or fall back to DEFAULT_LENS_WEIGHTS). "
            "Orchestrator renormalizes active weights to 1.0 at compute "
            "time, so partial overrides like {dcf: 0.6} work. Empty dict "
            "resets to defaults."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "weights": {
                    "type": "object",
                    "description": (
                        "Dict mapping lens keys (dcf, multiples, historical, "
                        "reverse_dcf, dividend) to non-negative floats"
                    ),
                },
            },
            "required": ["ticker", "weights"],
        },
    },
    {
        "name": "get_prescan_prompts",
        "description": (
            "Return the user's prescan prompt library with placeholders "
            "({ticker}, {company}, {prior:Section}) substituted from the "
            "current ai_notes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "get_prescan_sections",
        "description": "Current ai_notes content per prescan section.",
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "save_prescan_section",
        "description": (
            "Write one prescan section to ai_notes. Other sections preserved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["ticker", "title", "content"],
        },
    },
]


TOOL_HANDLERS: dict[str, Callable[[str, dict], Awaitable[Any]]] = {
    "build_dcf_config": _tool_build_dcf_config,
    "calculate_valuation": _tool_calculate_valuation,
    "calculate_multi_lens_valuation": _tool_calculate_multi_lens_valuation,
    "refresh_all_valuations": _tool_refresh_all_valuations,
    "save_to_watchlist": _tool_save_to_watchlist,
    "get_config": _tool_get_config,
    "get_watchlist": _tool_get_watchlist,
    "update_valuation_inputs": _tool_update_valuation_inputs,
    "update_lens_weights": _tool_update_lens_weights,
    "get_prescan_prompts": _tool_get_prescan_prompts,
    "get_prescan_sections": _tool_get_prescan_sections,
    "save_prescan_section": _tool_save_prescan_section,
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
        except (KeyError, ValueError) as e:
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

        # Tool impls return JSON strings; we wrap as text content.
        text = result if isinstance(result, str) else json.dumps(result, default=str)
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
