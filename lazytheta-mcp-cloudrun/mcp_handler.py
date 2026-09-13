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


# ── Trading 212 (read-only) ──
# user_id comes from the JWT, never from the arguments: this server runs with
# the service-role key, so a caller-supplied id would read another user's
# brokerage account.
async def _tool_t212_positions(user_id: str, args: dict) -> Any:
    return mcp_server._t212_positions_impl(user_id=user_id)


async def _tool_t212_balance(user_id: str, args: dict) -> Any:
    return mcp_server._t212_balance_impl(user_id=user_id)


async def _tool_t212_transactions(user_id: str, args: dict) -> Any:
    return mcp_server._t212_transactions_impl(
        ticker=args.get("ticker"),
        start_date=args.get("start_date"),
        end_date=args.get("end_date"),
        user_id=user_id,
    )


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


async def _tool_refresh_peer_multiples(user_id: str, args: dict) -> Any:
    return mcp_server._refresh_peer_multiples_impl(
        ticker=args["ticker"],
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
    return mcp_server._set_premortem_impl(
        args["ticker"], current=args.get("current", ""), sell=args.get("sell"),
        add=args.get("add"), ignore=args.get("ignore"),
        discipline=args.get("discipline"), user_id=user_id)


async def _tool_add_reminder(user_id: str, args: dict) -> Any:
    return mcp_server._add_reminder_impl(
        args["text"], args["fire_date"], ticker=(args.get("ticker") or None), user_id=user_id)


async def _tool_list_reminders(user_id: str, args: dict) -> Any:
    return mcp_server._list_reminders_impl(user_id=user_id)


async def _tool_delete_reminder(user_id: str, args: dict) -> Any:
    return mcp_server._delete_reminder_impl(args["reminder_id"], user_id=user_id)


async def _tool_set_ticker_alert(user_id: str, args: dict) -> Any:
    return mcp_server._set_ticker_alert_impl(args["ticker"], args["enabled"], user_id=user_id)


async def _tool_add_price_alert(user_id: str, args: dict) -> Any:
    return mcp_server._add_price_alert_impl(
        args["ticker"], args["target"], direction=(args.get("direction") or None),
        note=(args.get("note") or None), user_id=user_id)


async def _tool_list_price_alerts(user_id: str, args: dict) -> Any:
    return mcp_server._list_price_alerts_impl(user_id=user_id)


async def _tool_delete_price_alert(user_id: str, args: dict) -> Any:
    return mcp_server._delete_price_alert_impl(args["alert_id"], user_id=user_id)


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
            "+ Reverse DCF) for a watchlist ticker. Auto-fetches market inputs, peer multiples, "
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
        "description": (
            "Upsert a complete DCF config into the user's watchlist. Two fields "
            "are validated because both silently move the discount rate. "
            "'equity_market_value' ($M) must be present and positive — it "
            "anchors the CAPM rate (D/E relevering + WACC weights); without it "
            "the rate follows the day's price. 'sector_betas' is a list of "
            "[name, unlevered_beta, revenue_weight] where beta_u = sum(beta x "
            "weight), so the weights must sum to 1.0 and a single sector takes "
            "weight 1.0 — never repeat the beta in the weight slot, that squares "
            "it. A config failing either check is refused, not stored."
        ),
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
            "Valid keys: dcf, multiples, historical, reverse_dcf, dividend. "
            "Specified keys merge into cfg.lens_weights; unspecified "
            "keys retain their value (or fall back to DEFAULT_LENS_WEIGHTS). "
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
        "name": "refresh_peer_multiples",
        "description": (
            "Recompute trailing P/E and EV/EBIT for a watchlist ticker's peer "
            "set — and the ticker's own ttm_eps/ttm_ebit — from EDGAR filings "
            "+ current price, and save them into the config. No external "
            "multiples provider and no rate limit; safe to re-run any time to "
            "refresh. Peers that are foreign filers or lack computable earnings "
            "are left out of the trailing anchors. Returns per-peer "
            "trailing_pe/ev_ebit (null where not computable)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}},
            "required": ["ticker"],
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
            "Set the structured pre-mortem / action-triggers shown atop the Pre-Scan "
            "tab (cfg['premortem']), with the SAME fixed sections for every ticker. "
            "Fields: current (one-line view: spot / cost basis / fair value / buy "
            "price), sell (sell/thesis-breaker triggers), add (buy-more triggers), "
            "ignore (signals that are NOT a reason to sell), discipline (decision "
            "rules). Overwrites; read back via get_config."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "current": {"type": "string"},
                "sell": {"type": "array", "items": {"type": "string"}},
                "add": {"type": "array", "items": {"type": "string"}},
                "ignore": {"type": "array", "items": {"type": "string"}},
                "discipline": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "add_reminder",
        "description": (
            "Schedule a custom reminder; fires on the date via Telegram (if linked) "
            "+ the in-app notifications feed. fire_date is 'YYYY-MM-DD'; ticker is "
            "optional."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "fire_date": {"type": "string"},
                "ticker": {"type": "string"},
            },
            "required": ["text", "fire_date"],
        },
    },
    {
        "name": "list_reminders",
        "description": "List the user's pending custom reminders (id, fire_date, text, ticker).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_reminder",
        "description": "Delete a pending custom reminder by its id (from list_reminders).",
        "inputSchema": {
            "type": "object",
            "properties": {"reminder_id": {"type": "string"}},
            "required": ["reminder_id"],
        },
    },
    {
        "name": "set_ticker_alert",
        "description": (
            "Turn buy-price + earnings alerts on/off for a watchlist ticker (per-ticker "
            "opt-in; alerts only fire for 'Yes'-category tickers)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "enabled": {"type": "boolean"},
            },
            "required": ["ticker", "enabled"],
        },
    },
    {
        "name": "add_price_alert",
        "description": (
            "Set a one-shot price-target alert (standalone — NOT the buy-price auto "
            "alert). Fires once via Telegram + in-app when the price crosses the "
            "target. direction is 'above' or 'below'; if omitted it's inferred from "
            "the last known price."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "target": {"type": "number"},
                "direction": {"type": "string", "enum": ["above", "below"]},
                "note": {"type": "string"},
            },
            "required": ["ticker", "target"],
        },
    },
    {
        "name": "list_price_alerts",
        "description": "List the user's active price-target alerts (id, ticker, direction, target).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "delete_price_alert",
        "description": "Delete a price-target alert by its id (from list_price_alerts).",
        "inputSchema": {
            "type": "object",
            "properties": {"alert_id": {"type": "string"}},
            "required": ["alert_id"],
        },
    },
    {
        "name": "t212_positions",
        "description": (
            "Open Trading 212 positions: shares, FIFO cost per share, current "
            "price, market value and unrealized P/L. All figures converted to "
            "USD. Read-only. Requires the user to have connected a Trading 212 "
            "API key in Lazy Theta."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "t212_balance",
        "description": (
            "Trading 212 account value and free cash, converted to USD, with "
            "the account's own currency and the FX rate applied so the figures "
            "can be checked against the broker's own screen."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "t212_transactions",
        "description": (
            "Trading 212 fill history, oldest first: ticker, date, buy/sell, "
            "quantity, price and net value. Optionally narrowed to one ticker "
            "or a date window. Prices converted to USD."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string",
                           "description": "Only this ticker, e.g. RDDT"},
                "start_date": {"type": "string",
                               "description": "YYYY-MM-DD, inclusive"},
                "end_date": {"type": "string",
                             "description": "YYYY-MM-DD, inclusive"},
            },
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
    "t212_positions": _tool_t212_positions,
    "t212_balance": _tool_t212_balance,
    "t212_transactions": _tool_t212_transactions,
    "update_valuation_inputs": _tool_update_valuation_inputs,
    "update_lens_weights": _tool_update_lens_weights,
    "update_dcf_scenario_adjustments": _tool_update_dcf_scenario_adjustments,
    "get_fundamentals": _tool_get_fundamentals,
    "update_fundamentals": _tool_update_fundamentals,
    "refresh_peer_multiples": _tool_refresh_peer_multiples,
    "get_prescan_prompts": _tool_get_prescan_prompts,
    "get_prescan_sections": _tool_get_prescan_sections,
    "save_prescan_section": _tool_save_prescan_section,
    "set_robustness": _tool_set_robustness,
    "set_premortem": _tool_set_premortem,
    "add_reminder": _tool_add_reminder,
    "list_reminders": _tool_list_reminders,
    "delete_reminder": _tool_delete_reminder,
    "set_ticker_alert": _tool_set_ticker_alert,
    "add_price_alert": _tool_add_price_alert,
    "list_price_alerts": _tool_list_price_alerts,
    "delete_price_alert": _tool_delete_price_alert,
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
