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


async def _tool_update_dcf_scenario_adjustments(user_id: str, args: dict) -> Any:
    return mcp_server._update_dcf_scenario_adjustments_impl(
        ticker=args["ticker"],
        fields=args["fields"],
        user_id=user_id,
    )


async def _tool_update_sotp_segments(user_id: str, args: dict) -> Any:
    return mcp_server._update_sotp_segments_impl(
        ticker=args["ticker"],
        segments=args["segments"],
        user_id=user_id,
    )


async def _tool_remove_sotp_segment(user_id: str, args: dict) -> Any:
    return mcp_server._remove_sotp_segment_impl(
        ticker=args["ticker"],
        name=args["name"],
        user_id=user_id,
    )


async def _tool_set_sotp_corporate_overhead(user_id: str, args: dict) -> Any:
    return mcp_server._set_sotp_corporate_overhead_impl(
        ticker=args["ticker"],
        value=args["value"],
        user_id=user_id,
    )


async def _tool_get_fundamentals(user_id: str, args: dict) -> Any:
    return mcp_server._get_fundamentals_impl(
        ticker=args["ticker"],
        n_years=args.get("n_years", 10),
        user_id=user_id,
    )


async def _tool_update_fundamentals(user_id: str, args: dict) -> Any:
    return mcp_server._update_fundamentals_impl(
        ticker=args["ticker"],
        overrides=args["overrides"],
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


async def _tool_set_robustness(user_id: str, args: dict) -> Any:
    return mcp_server._set_robustness_impl(args["ticker"], args["axes"], user_id=user_id)


async def _tool_set_premortem(user_id: str, args: dict) -> Any:
    return mcp_server._set_premortem_impl(args["ticker"], args.get("text", ""), user_id=user_id)


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
            "+ Reverse DCF + SOTP) for a watchlist ticker. SOTP lens activates "
            "automatically when cfg.sotp.segments is non-empty (set via "
            "update_sotp_segments). Auto-fetches market inputs, peer multiples, "
            "and dividend history first. Stores summary back."
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
            "ticker. Valid keys per lens: Dividend (ttm_dividend, "
            "dividend_5y_cagr, median_5y_yield); Historical "
            "(historical_fwd_pe, historical_trailing_pe, "
            "historical_ev_ebitda, forward_eps, ttm_eps, ttm_ebitda); "
            "Multiples (forward_eps, ttm_ebitda). Any other key is silently "
            "stored but ignored by every lens. Each updated field is removed "
            "from _auto_filled so the override survives the next yfinance "
            "refresh."
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
            "Valid keys: dcf, multiples, historical, reverse_dcf, dividend, "
            "sotp. Specified keys merge into cfg.lens_weights; unspecified "
            "keys retain their value (or fall back to DEFAULT_LENS_WEIGHTS). "
            "Orchestrator renormalizes active weights to 1.0 at compute "
            "time, so partial overrides like {dcf: 0.6} work. Empty dict "
            "resets to defaults. SOTP defaults to 0.00 — opt-in per ticker "
            "by setting sotp: 0.10+ once segments are defined."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "weights": {
                    "type": "object",
                    "description": (
                        "Dict mapping lens keys (dcf, multiples, historical, "
                        "reverse_dcf, dividend, sotp) to non-negative floats"
                    ),
                },
            },
            "required": ["ticker", "weights"],
        },
    },
    {
        "name": "update_dcf_scenario_adjustments",
        "description": (
            "Adjust the DCF bear/bull scenario adjustments that drive the "
            "DCF lens's fv_low/fv_high range when scenario_grid=True. "
            "Valid keys: bear_growth_adj, bear_margin_adj, bull_growth_adj, "
            "bull_margin_adj. All values must be numbers (typical magnitudes "
            "±0.01 to ±0.05). Bear keys are usually negative, bull keys "
            "positive. Call calculate_multi_lens_valuation(scenario_grid=True) "
            "afterwards to see the new range."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "fields": {
                    "type": "object",
                    "description": (
                        "Dict with any of: bear_growth_adj, bear_margin_adj, "
                        "bull_growth_adj, bull_margin_adj — all floats"
                    ),
                },
            },
            "required": ["ticker", "fields"],
        },
    },
    {
        "name": "update_sotp_segments",
        "description": (
            "Upsert SOTP segments for a watchlist ticker. For each input "
            "segment, match by 'name' (case-insensitive) against existing "
            "cfg.sotp.segments: match → partial merge of supplied fields; "
            "no match → append as new segment (requires ev_mid > 0). Other "
            "segments are untouched. Allowed segment fields: name (required), "
            "ev_mid (required for new), ev_low, ev_high, revenue, "
            "operating_margin (0-1 decimal), implied_multiple_mid, rationale. "
            "All EV values in $M, non-negative. To remove a segment, use "
            "remove_sotp_segment instead. Call calculate_multi_lens_valuation "
            "afterwards to see the new SOTP lens output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "segments": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "ev_mid": {"type": "number"},
                            "ev_low": {"type": "number"},
                            "ev_high": {"type": "number"},
                            "revenue": {"type": "number"},
                            "operating_margin": {"type": "number"},
                            "implied_multiple_mid": {"type": "number"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
            },
            "required": ["ticker", "segments"],
        },
    },
    {
        "name": "remove_sotp_segment",
        "description": (
            "Remove one SOTP segment by name (case-insensitive) from a "
            "watchlist ticker. Idempotent — no error if the name doesn't "
            "exist. Removing the last segment is allowed and persisted as "
            "an empty list (treated as legitimate user intent by the "
            "config-store guard)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["ticker", "name"],
        },
    },
    {
        "name": "set_sotp_corporate_overhead",
        "description": (
            "Set cfg.sotp.corporate_overhead_ev_adjustment for a watchlist "
            "ticker ($M, typically negative — e.g. -5000 for $5B of "
            "unallocated corporate overhead capitalized into the SOTP "
            "bridge). Initialises cfg.sotp with segments: [] if not yet "
            "present."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "value": {"type": "number"},
            },
            "required": ["ticker", "value"],
        },
    },
    {
        "name": "get_fundamentals",
        "description": (
            "Return per-year EDGAR fundamentals (revenue, OI, FCF, debt, "
            "leases, pension, etc.) for a watchlist ticker, with any stored "
            "per-year overrides applied. Includes a 'headline' object with "
            "computed metrics: avg_roce_pct (ROE-fallback for float "
            "businesses with avg CE/TA < 25%), current_fcf_yield_pct, "
            "current_ebit_ev_pct, latest_adjusted_net_debt_m (incl. leases "
            "+ pension, Moody's/S&P style), latest_net_debt_ebitda. "
            "Read-only — call update_fundamentals to correct values."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "n_years": {"type": "integer", "minimum": 1, "default": 10},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "update_fundamentals",
        "description": (
            "Set per-year overrides for component fundamentals fields when "
            "EDGAR XBRL tagging is broken (e.g. MCD operating leases "
            "post-FY2023). Merge-by-field-year semantics: existing "
            "overrides for other (field, year) pairs stay intact. Pass "
            "null as value to remove that specific override (reverts to "
            "EDGAR value). Allowed component fields only — derived metrics "
            "(fcf, ebitda) are recomputed automatically. Allowed: "
            "revenue, operating_income, net_income, cost_of_revenue, "
            "tax_provision, pretax_income, total_equity, total_debt, "
            "cash, shares, capex, cfo, total_assets, current_liabilities, "
            "goodwill, intangibles, ppe, da, gross_profit, eps, "
            "dividends_per_share, short_term_debt, "
            "operating_lease_liabilities, finance_lease_liabilities, "
            "pension_liabilities."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "overrides": {
                    "type": "object",
                    "description": (
                        "Shape: {field_name: {year_int: number_or_null}}. "
                        "Example: {'operating_lease_liabilities': "
                        "{2024: 12500, 2025: 12800}}"
                    ),
                },
            },
            "required": ["ticker", "overrides"],
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
    {
        "name": "set_robustness",
        "description": (
            "Set the 4 qualitative robustness axes (customers, barriers, "
            "management, industry) for a ticker; ROCE/net-debt + verdict "
            "are computed server-side."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "axes": {"type": "object"},
            },
            "required": ["ticker", "axes"],
        },
    },
    {
        "name": "set_premortem",
        "description": (
            "Set the free-text pre-mortem / action-triggers note ('what would make "
            "me sell — or add?') shown atop the ticker detail page (cfg['premortem']). "
            "Overwrites the existing note; read it back via get_config."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["ticker", "text"],
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
    "update_dcf_scenario_adjustments": _tool_update_dcf_scenario_adjustments,
    "update_sotp_segments": _tool_update_sotp_segments,
    "remove_sotp_segment": _tool_remove_sotp_segment,
    "set_sotp_corporate_overhead": _tool_set_sotp_corporate_overhead,
    "get_fundamentals": _tool_get_fundamentals,
    "update_fundamentals": _tool_update_fundamentals,
    "get_prescan_prompts": _tool_get_prescan_prompts,
    "get_prescan_sections": _tool_get_prescan_sections,
    "save_prescan_section": _tool_save_prescan_section,
    "set_robustness": _tool_set_robustness,
    "set_premortem": _tool_set_premortem,
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
