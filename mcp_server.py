"""
LazyTheta DCF MCP Server
========================
Lets Claude Desktop fill out DCF configs in LazyTheta's Supabase.
Runs locally via stdio transport.

Required env vars:
    SUPABASE_URL          — Supabase project URL
    SUPABASE_SERVICE_KEY  — Service role key (bypasses RLS)
    LAZYTHETA_USER_ID     — Your Supabase user ID
"""

import json
import logging
import os

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment & Supabase client
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
USER_ID = os.environ.get("LAZYTHETA_USER_ID", "")

_client = None


def get_supabase_client():
    """Create or return cached Supabase client."""
    global _client
    if _client is not None:
        return _client

    if not SUPABASE_URL:
        raise ValueError("SUPABASE_URL environment variable is required")
    if not SUPABASE_SERVICE_KEY:
        raise ValueError("SUPABASE_SERVICE_KEY environment variable is required")
    if not USER_ID:
        raise ValueError("LAZYTHETA_USER_ID environment variable is required")

    from supabase import create_client
    _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "LazyTheta DCF",
    instructions="Fill out DCF valuations in LazyTheta's Streamlit app",
)


import gather_data
import dcf_calculator
import config_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_sector_betas(sic_code, sic_description=""):
    """Convert SIC code to sector_betas list of (name, beta, weight) tuples."""
    sic_int = int(sic_code) if sic_code else 0

    if sic_int in gather_data.SIC_TO_SECTOR:
        sector_name, sector_beta = gather_data.SIC_TO_SECTOR[sic_int]
        return [(sector_name, sector_beta, 1.0)]

    dam_betas = gather_data.fetch_sector_betas()
    if dam_betas and sic_description:
        sic_words = set(sic_description.lower().split())
        best_match, best_score = None, 0
        for sector, beta in dam_betas.items():
            sector_words = set(sector.lower().split())
            overlap = len(sic_words & sector_words)
            if overlap > best_score:
                best_score = overlap
                best_match = (sector, beta)
        if best_match and best_score > 0:
            return [(best_match[0], best_match[1], 1.0)]

    return [("Market", 1.0, 1.0)]


def _resolve_sector_margin(sector_betas):
    """Fetch sector median margin from Damodaran, matching on sector name."""
    sector_name = sector_betas[0][0] if sector_betas else ""
    if not sector_name:
        return None

    dam_margins = gather_data.fetch_sector_margins()
    if not dam_margins:
        return None

    if sector_name in dam_margins:
        return dam_margins[sector_name]

    target_words = set(sector_name.lower().replace("/", " ").split())
    best_match, best_score = None, 0
    for sec_name, sec_margin in dam_margins.items():
        sec_words = set(sec_name.lower().replace("/", " ").split())
        overlap = len(target_words & sec_words)
        if overlap > best_score:
            best_score = overlap
            best_match = (sec_name, sec_margin)
    if best_match and best_score > 0:
        return best_match[1]
    return None


# ---------------------------------------------------------------------------
# Tool implementations (testable without MCP decorator)
# ---------------------------------------------------------------------------

def _build_dcf_config_impl(ticker, financial_data, company_name,
                            sic_code=None, sic_description="",
                            margin_of_safety=None, terminal_growth=None,
                            sector_margin=None, consensus=None):
    """Core logic for build_dcf_config."""
    ticker = ticker.upper()

    stock_price, _, _ = gather_data.fetch_stock_price(ticker)
    if stock_price <= 0:
        raise ValueError(f"Could not fetch stock price for {ticker}")

    risk_free_rate = gather_data.fetch_treasury_yield()

    shares = financial_data.get("shares", [])
    shares_latest = shares[-1] if shares else 0
    market_cap = stock_price * shares_latest

    oi_latest = financial_data.get("operating_income", [0])[-1] or 0
    ie_latest = financial_data.get("interest_expense_latest", 0) or 0
    credit_rating, credit_spread = gather_data.synthetic_credit_rating(oi_latest, ie_latest)

    sector_betas = _resolve_sector_betas(sic_code, sic_description)

    if sector_margin is None:
        sector_margin = _resolve_sector_margin(sector_betas)

    peers = []
    if sic_code and market_cap > 0:
        try:
            peer_tickers = gather_data.find_peers(
                sic_code=int(sic_code),
                target_ticker=ticker,
                target_market_cap=market_cap,
            )
            peers = gather_data.fetch_peer_data(peer_tickers)
        except Exception as e:
            logger.warning("Peer lookup failed: %s", e)

    cfg = gather_data.build_config(
        ticker=ticker,
        financials=financial_data,
        stock_price=stock_price,
        market_cap=market_cap,
        shares_yahoo=shares_latest,
        risk_free_rate=risk_free_rate,
        sector_betas=sector_betas,
        credit_spread=credit_spread,
        credit_rating=credit_rating,
        peers=peers,
        company_name=company_name,
        margin_of_safety=margin_of_safety,
        terminal_growth=terminal_growth,
        sector_margin=sector_margin,
        consensus=consensus,
    )

    return cfg


def _calculate_valuation_impl(cfg):
    """Core logic for calculate_valuation."""
    wacc = dcf_calculator.compute_wacc(cfg)
    valuation = dcf_calculator.compute_intrinsic_value(cfg, wacc)
    reverse = dcf_calculator.compute_reverse_dcf(cfg, wacc)

    result = {
        "wacc": round(wacc, 4),
        "intrinsic_value": round(valuation["intrinsic_value"], 2),
        "buy_price": round(valuation["buy_price"], 2),
        "enterprise_value": round(valuation["enterprise_value"], 2),
        "equity_value": round(valuation["equity_value"], 2),
        "tv_pct": round(valuation["tv_pct"], 4),
        "implied_growth": round(reverse["implied_growth"], 4),
        "implied_margin": round(reverse["implied_margin"], 4),
        "market_price": reverse["market_price"],
    }
    if reverse.get("closest"):
        result["closest_growth"] = round(reverse["closest"][0], 4)
        result["closest_margin"] = round(reverse["closest"][1], 4)

    return json.dumps(result)


def _save_to_watchlist_impl(ticker, cfg):
    """Core logic for save_to_watchlist."""
    client = get_supabase_client()
    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return f"Saved {ticker.upper()} to watchlist."


def _get_config_impl(ticker):
    """Core logic for get_config."""
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})
    return json.dumps(cfg, default=str)


def _get_watchlist_impl():
    """Core logic for get_watchlist."""
    client = get_supabase_client()
    entries = config_store.list_watchlist(client, user_id=USER_ID)
    return json.dumps(entries, default=str)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def build_dcf_config(
    ticker: str,
    financial_data: dict,
    company_name: str,
    sic_code: str = "",
    sic_description: str = "",
    margin_of_safety: float = 0,
    terminal_growth: float = 0,
    sector_margin: float = 0,
    consensus: dict | None = None,
) -> str:
    """Build a complete DCF configuration from SEC financial data.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")
        financial_data: Parsed financials dict with keys: years, revenue,
            operating_income, net_income, cost_of_revenue, sbc, shares,
            current_assets, cash, st_investments, current_liabilities,
            st_debt, st_leases, net_ppe, goodwill_intang, buyback,
            tax_provision, pretax_income, lt_debt_latest, lt_leases_latest,
            st_debt_latest, interest_expense_latest, finance_leases_latest,
            minority_interest_latest, equity_investments_latest,
            unfunded_pension_latest
        company_name: Full company name (e.g. "Microsoft Corporation")
        sic_code: SIC code for sector beta + peer lookup (e.g. "7372")
        sic_description: SIC description for fuzzy sector matching
        margin_of_safety: Override default 20%% margin of safety (0 = use default)
        terminal_growth: Override default 2.5%% terminal growth (0 = use default)
        sector_margin: Override sector operating margin (0 = auto from Damodaran)
        consensus: Analyst estimates dict (optional)

    Returns:
        JSON string with the complete DCF config dict.
    """
    try:
        cfg = _build_dcf_config_impl(
            ticker=ticker,
            financial_data=financial_data,
            company_name=company_name,
            sic_code=sic_code or None,
            sic_description=sic_description,
            margin_of_safety=margin_of_safety or None,
            terminal_growth=terminal_growth or None,
            sector_margin=sector_margin or None,
            consensus=consensus,
        )
        return json.dumps(cfg, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def calculate_valuation(config: dict) -> str:
    """Calculate intrinsic value, WACC, and reverse DCF from a config.

    Args:
        config: Complete DCF config dict (from build_dcf_config or get_config).

    Returns:
        JSON with wacc, intrinsic_value, buy_price, enterprise_value,
        equity_value, tv_pct, implied_growth, implied_margin.
    """
    try:
        return _calculate_valuation_impl(config)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def save_to_watchlist(ticker: str, config: dict) -> str:
    """Save a DCF config to the LazyTheta watchlist in Supabase.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")
        config: Complete DCF config dict (from build_dcf_config).

    Returns:
        Confirmation message.
    """
    try:
        return _save_to_watchlist_impl(ticker, config)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_config(ticker: str) -> str:
    """Read an existing DCF config from the LazyTheta watchlist.

    Args:
        ticker: Stock ticker symbol (e.g. "MSFT")

    Returns:
        JSON with the complete DCF config, or error if not found.
    """
    try:
        return _get_config_impl(ticker)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_watchlist() -> str:
    """List all tickers on the LazyTheta watchlist.

    Returns:
        JSON array of {ticker, company, updated, stock_price} entries.
    """
    try:
        return _get_watchlist_impl()
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
