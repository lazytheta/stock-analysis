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
import valuation_lenses


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
                            sector_margin=None, consensus=None,
                            valuation_basis="nominal"):
    """Core logic for build_dcf_config."""
    ticker = ticker.upper()

    stock_price, _, _ = gather_data.fetch_stock_price(ticker)
    if stock_price <= 0:
        raise ValueError(f"Could not fetch stock price for {ticker}")

    risk_free_rate = gather_data.fetch_treasury_yield()

    nominal_risk_free_rate = None
    if valuation_basis == "real":
        nominal_risk_free_rate = risk_free_rate
        risk_free_rate = gather_data.fetch_tips_yield()

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
        valuation_basis=valuation_basis,
        nominal_risk_free_rate=nominal_risk_free_rate,
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

    # Include valuation basis metadata
    result["valuation_basis"] = cfg.get("valuation_basis", "nominal")
    if cfg.get("valuation_basis") == "real":
        result["nominal_risk_free_rate"] = cfg.get("nominal_risk_free_rate")
        result["breakeven_inflation"] = cfg.get("breakeven_inflation")

    return json.dumps(result)


def _calculate_multi_lens_valuation_impl(ticker, scenario_grid=False):
    """Core logic for calculate_multi_lens_valuation: load cfg, run all
    lenses, persist summary, return JSON."""
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})

    summary = valuation_lenses.calculate_multi_lens_valuation(
        cfg, scenario_grid=scenario_grid
    )
    cfg["valuation_summary"] = summary
    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return json.dumps(summary, default=str)


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
    valuation_basis: str = "nominal",
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
        valuation_basis: "nominal" (default) or "real" (TIPS-based, inflation-adjusted)

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
            valuation_basis=valuation_basis,
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
def calculate_multi_lens_valuation(ticker: str, scenario_grid: bool = False) -> str:
    """Run multi-lens fair value (DCF + Trading Multiples + Reverse DCF)
    for a watchlist ticker and persist the summary to Supabase.

    Use this after editing valuation_inputs or peers to refresh the
    fair value estimate. The result is also surfaced via get_watchlist().

    Args:
        ticker: Stock ticker symbol (e.g. "ABT")
        scenario_grid: If True, run a 4x4 bull/bear DCF scenario grid for
            the DCF lens fv_low/fv_high. Default False uses ±15% bands
            around the base intrinsic.

    Returns:
        JSON valuation_summary dict. See spec for schema.
    """
    try:
        return _calculate_multi_lens_valuation_impl(ticker, scenario_grid)
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
    """List all tickers on the LazyTheta watchlist with multi-lens valuation summary.

    Each entry has these keys (always present; values may be None when no
    valuation_summary is stored — run calculate_multi_lens_valuation to populate):
        ticker, company, updated, stock_price,
        fv_low, fv_mid, fv_high, buy_price, current_vs_mid,
        lens_count, verdict, phase

    Returns:
        JSON array of dicts with the schema above.
    """
    try:
        return _get_watchlist_impl()
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Pre-scan / AI Research Sections
# ---------------------------------------------------------------------------

def _fill_prompt_template(prompt: str, ticker: str, company: str, prior_results: dict) -> str:
    """Apply {ticker}, {company}, {prior:Section Title} substitutions.

    Mirrors streamlit_app.py's _fill_prompt so the prompt Claude sees here is
    identical to what ▶ Run would send via Groq/Gemini."""
    import re

    ticker = ticker.upper()

    def _sub_prior(m):
        title = m.group(1).strip()
        content = (prior_results.get(title) or "").strip()
        if not content:
            return f"(no prior '{title}' analysis available for this ticker)"
        return content

    filled = re.sub(r"\{prior:([^}]+)\}", _sub_prior, prompt)
    filled = filled.replace("{ticker}", ticker).replace("{company}", company)
    if "{ticker}" not in prompt and "{company}" not in prompt and "{prior:" not in prompt:
        filled = (
            f"**IMPORTANT OVERRIDE:** The company to analyze is "
            f"**{company} (ticker: {ticker})**. "
            f"Do NOT ask the user for a company — it is provided here. "
            f"Begin the analysis immediately using this company.\n\n"
            f"---\n\n{filled}"
        )
    return filled


def _get_prescan_prompts_impl(ticker):
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}
    company = cfg.get("company", ticker.upper())

    prefs = config_store.load_user_prefs(client, user_id=USER_ID)
    library = prefs.get("ai_prompts") or []
    if not library:
        return {"error": "Prompt library is empty. Open a watchlist editor in the app once to seed defaults."}

    ai_notes = cfg.get("ai_notes") or {}
    if not isinstance(ai_notes, dict):
        ai_notes = {}

    out = []
    for entry in library:
        title = entry.get("title")
        prompt_template = entry.get("prompt", "")
        if not title:
            continue
        out.append({
            "title": title,
            "prompt": _fill_prompt_template(prompt_template, ticker, company, ai_notes),
        })
    return out


def _get_prescan_sections_impl(ticker):
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}
    ai_notes = cfg.get("ai_notes") or {}
    if not isinstance(ai_notes, dict):
        ai_notes = {}
    return ai_notes


def _save_prescan_section_impl(ticker, title, content):
    if not title.strip():
        return {"error": "title is required"}
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}

    ai_notes = cfg.get("ai_notes") or {}
    if not isinstance(ai_notes, dict):
        ai_notes = {}
    ai_notes[title] = content
    cfg["ai_notes"] = ai_notes

    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return f"Saved {ticker.upper()} → '{title}' ({len(content)} chars)."


@mcp.tool()
def get_prescan_prompts(ticker: str) -> str:
    """Return the user's pre-scan prompts with {ticker}/{company}/{prior:...}
    placeholders already substituted, ready to send to an LLM.

    Use this to fill in the AI Research Sections in the LazyTheta watchlist
    editor. Each entry has the section title and the filled prompt — generate
    a markdown answer per section, then call save_prescan_section to persist.

    Args:
        ticker: Stock ticker symbol (e.g. "NFLX")

    Returns:
        JSON array of {title, prompt} objects, in the order they appear in
        the user's prompt library. Or {"error": "..."} on failure.
    """
    try:
        return json.dumps(_get_prescan_prompts_impl(ticker), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_prescan_sections(ticker: str) -> str:
    """List the existing pre-scan section content for a ticker.

    Useful to see what's already filled in (so Claude knows what to skip
    or update). For the Scorecard section, the content is a fenced JSON
    block; for other sections it's free-form Markdown.

    Args:
        ticker: Stock ticker symbol (e.g. "NFLX")

    Returns:
        JSON object {title: content_string} for every existing section.
    """
    try:
        return json.dumps(_get_prescan_sections_impl(ticker), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def save_prescan_section(ticker: str, title: str, content: str) -> str:
    """Save Markdown content (or a fenced JSON block, for the Scorecard) to
    one pre-scan section of a ticker. Other sections are preserved.

    Args:
        ticker: Stock ticker symbol (e.g. "NFLX")
        title: Section title — must match one of the user's prompt library
            entries (e.g. "Business Description", "Moat", "Scorecard").
        content: Markdown body. For the Scorecard section, format as a
            ```json fenced block to be parsed by the visual renderer.

    Returns:
        Confirmation string or {"error": "..."} JSON.
    """
    try:
        result = _save_prescan_section_impl(ticker, title, content)
        if isinstance(result, dict):
            return json.dumps(result)
        return result
    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
