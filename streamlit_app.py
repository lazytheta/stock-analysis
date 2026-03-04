"""
Streamlit web app for Stock Analysis tools.
- DCF Valuation Model Generator
- Portfolio Cost Basis Tracker (Tastytrade)
"""

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import io
import os
import sys
import contextlib
import tempfile
import time
from datetime import date, datetime, timedelta
import re

from gather_data import (
    get_cik,
    fetch_company_submissions,
    fetch_company_facts,
    parse_financials,
    fetch_stock_price,
    fetch_treasury_yield,
    synthetic_credit_rating,
    fetch_sector_betas,
    fetch_sector_margins,
    fetch_consensus_estimates,
    find_peers,
    fetch_peer_data,
    build_config,
    SIC_TO_SECTOR,
    ERP_DEFAULT,
    TERMINAL_GROWTH_DEFAULT,
    MARGIN_OF_SAFETY_DEFAULT,
)
from tastytrade_api import fetch_portfolio_data, fetch_current_prices, fetch_account_balances, fetch_net_liq_history, fetch_sp500_yearly_returns, fetch_benchmark_returns, fetch_ticker_profiles, fetch_yearly_transfers, fetch_portfolio_greeks, fetch_margin_interest
import plotly.graph_objects as go

# ── Page config ──
st.set_page_config(
    page_title="Stock Analysis",
    page_icon="\U0001f4ca",
    layout="wide",
)

# ── Custom CSS ──
st.markdown("""
<style>
    /* ── Apple-inspired design ── */

    /* Global typography */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'SF Pro Display',
                     'Helvetica Neue', Arial, sans-serif;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }

    /* Main content area */
    .main .block-container {
        padding-top: 3rem;
    }

    /* Headings — Apple style */
    h1, h2, h3 {
        color: #1d1d1f !important;
        font-weight: 600 !important;
        letter-spacing: -0.02em !important;
    }
    h2 { font-size: 2rem !important; }
    h3 { font-size: 1.4rem !important; }

    p, li, label, span {
        color: #1d1d1f;
    }

    /* Metric cards — clean, flat Apple style */
    [data-testid="stMetric"] {
        background: #fff;
        border: none;
        border-radius: 18px;
        padding: 20px 24px;
        box-shadow: none;
    }
    [data-testid="stMetric"] label {
        color: #86868b;
        font-size: 0.75rem;
        font-weight: 500;
        letter-spacing: 0.01em;
        text-transform: uppercase;
    }
    [data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-weight: 600;
        color: #1d1d1f;
        font-size: 1.3rem;
    }

    /* Hero card — for the big P/L number */
    .hero-card {
        background: #fff;
        border-radius: 24px;
        padding: 48px 32px;
        box-shadow: none;
        text-align: center;
        margin-bottom: 32px;
    }
    .hero-card .hero-label {
        color: #86868b;
        font-size: 0.85rem;
        font-weight: 500;
        margin: 0 0 8px 0;
        letter-spacing: 0.01em;
        text-transform: uppercase;
    }
    .hero-card .hero-value {
        font-size: 3.2rem;
        font-weight: 700;
        margin: 0;
        letter-spacing: -0.03em;
    }
    .hero-card .hero-sub {
        color: #86868b;
        font-size: 0.95rem;
        font-weight: 400;
        margin: 12px 0 0 0;
    }
    .hero-green { color: #81b29a; }
    .hero-red { color: #e07a5f; }

    /* Stat pills — inline stats below hero */
    .stat-row {
        display: flex;
        justify-content: center;
        gap: 16px;
        margin: 20px 0 0 0;
        flex-wrap: wrap;
    }
    .stat-pill {
        background: #fff;
        border-radius: 980px;
        padding: 8px 18px;
        font-size: 0.82rem;
        color: #86868b;
        font-weight: 400;
    }
    .stat-pill b {
        color: #1d1d1f;
        font-weight: 600;
    }

    /* Success banner (DCF page) — Apple style */
    .success-banner {
        background: #fff;
        border: none;
        border-radius: 24px;
        padding: 40px 32px;
        margin: 24px 0;
        text-align: center;
        box-shadow: none;
    }
    .success-banner h2 {
        color: #1d1d1f;
        margin: 0 0 8px 0;
        font-size: 1.5rem;
        font-weight: 600;
    }
    .success-banner p {
        color: #86868b;
        margin: 0;
        font-size: 0.95rem;
        font-weight: 400;
    }

    /* Chart container */
    .chart-label {
        color: #86868b;
        font-size: 0.75rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 8px;
    }

    /* Hide streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* Form styling — Apple clean */
    .stForm {
        border: none !important;
        border-radius: 18px !important;
        padding: 28px !important;
        background: #fff !important;
        box-shadow: none !important;
    }

    /* Buttons — Green accent */
    .stButton > button[kind="primary"],
    .stDownloadButton > button[kind="primary"],
    .stFormSubmitButton > button[kind="primary"] {
        background-color: #81b29a !important;
        color: white !important;
        border: none !important;
        border-radius: 980px !important;
        padding: 12px 24px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
        letter-spacing: 0 !important;
        transition: background-color 0.2s ease !important;
    }
    .stButton > button[kind="primary"]:hover,
    .stDownloadButton > button[kind="primary"]:hover,
    .stFormSubmitButton > button[kind="primary"]:hover {
        background-color: #6fa88a !important;
    }

    .stButton > button[kind="secondary"],
    .stDownloadButton > button[kind="secondary"] {
        background-color: transparent !important;
        color: #81b29a !important;
        border: none !important;
        border-radius: 980px !important;
        padding: 12px 24px !important;
        font-size: 0.95rem !important;
        font-weight: 500 !important;
    }
    .stButton > button[kind="secondary"]:hover,
    .stDownloadButton > button[kind="secondary"]:hover {
        background-color: rgba(129,178,154,0.06) !important;
    }

    /* Text inputs — clean Apple style */
    .stTextInput > div > div > input,
    .stNumberInput > div > div > input {
        border: 1px solid #d2d2d7 !important;
        border-radius: 12px !important;
        padding: 10px 14px !important;
        font-size: 0.95rem !important;
        background: #fff !important;
        transition: border-color 0.2s ease !important;
    }
    .stTextInput > div > div > input:focus,
    .stNumberInput > div > div > input:focus {
        border-color: #81b29a !important;
        box-shadow: 0 0 0 3px rgba(129,178,154,0.2) !important;
    }

    /* Select boxes */
    .stSelectbox > div > div {
        border-radius: 12px !important;
        border-color: #d2d2d7 !important;
    }

    /* Sliders — Green accent */
    .stSlider [data-baseweb="slider"] [role="slider"] {
        background-color: #81b29a !important;
    }

    /* Expanders — clean */
    [data-testid="stExpander"] {
        border: 1px solid #d2d2d7;
        border-radius: 18px;
        box-shadow: none;
        overflow: hidden;
    }

    /* Dataframes — rounded, clean */
    [data-testid="stDataFrame"] {
        border-radius: 14px;
        overflow: hidden;
    }

    /* Sidebar — minimal Apple style */
    section[data-testid="stSidebar"] {
        background: #fff;
        border-right: none;
    }
    section[data-testid="stSidebar"] [data-testid="stRadio"] label {
        font-weight: 500;
        color: #1d1d1f;
    }
    /* Radio / checkbox accent — green */
    [data-testid="stRadio"] [role="radiogroup"] label[data-checked="true"]::before,
    .stRadio div[role="radiogroup"] label span[data-checked="true"] {
        background-color: #81b29a !important;
        border-color: #81b29a !important;
    }
    input[type="radio"]:checked {
        accent-color: #81b29a !important;
    }
    /* Pills active state */
    button[data-active="true"],
    [data-testid="stPills"] button[aria-pressed="true"],
    [data-testid="stPills"] button[aria-selected="true"] {
        background-color: #81b29a !important;
        color: white !important;
        border-color: #81b29a !important;
    }
    /* Streamlit primary color override */
    :root {
        --primary-color: #81b29a !important;
    }

    /* Toolbar: remove gap between buttons */
    .st-key-toolbar_inline [data-testid="stHorizontalBlock"] {
        gap: 0 !important;
    }
    .st-key-toolbar_inline [data-testid="stColumn"] {
        flex: 0 0 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }

    /* Dividers */
    hr {
        border-color: #d2d2d7 !important;
        opacity: 0.5;
    }

    /* Links */
    a {
        color: #81b29a !important;
        text-decoration: none !important;
    }
    a:hover {
        text-decoration: underline !important;
    }

    /* Status widget */
    [data-testid="stStatusWidget"] {
        border-radius: 18px;
    }

    /* ── Ticker cards (Wheel Cost Basis) ── */
    .card-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        margin-bottom: 16px;
        max-width: 700px;
    }
    .card-left .tk-title {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .card-left .tk-logo {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
    }
    .card-left .tk-name {
        font-size: 1.1rem;
        font-weight: 700;
        color: #1d1d1f;
        margin: 0;
    }
    .card-left .tk-sub {
        font-size: 0.8rem;
        color: #86868b;
        margin: 2px 0;
    }
    .card-center {
        text-align: center;
    }
    .card-center .shares-count {
        font-size: 1.05rem;
        font-weight: 600;
        color: #1d1d1f;
    }
    .card-center .shares-label {
        font-size: 0.78rem;
        color: #86868b;
    }
    .pl-badge {
        display: inline-block;
        padding: 6px 16px;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.9rem;
        color: #fff;
    }
    .pl-badge-green { background: #81b29a; }
    .pl-badge-red { background: #e07a5f; }

    .trade-row {
        display: flex;
        align-items: baseline;
        gap: 28px;
        padding: 12px 0;
        border-bottom: 1px solid #f0f0f2;
    }
    .trade-row:last-child { border-bottom: none; }
    .trade-row .tr-desc {
        min-width: 160px;
    }
    .trade-row .tr-desc .tr-label {
        font-weight: 600;
        font-size: 0.92rem;
        color: #1d1d1f;
        margin: 0;
    }
    .trade-row .tr-desc .tr-date {
        font-size: 0.78rem;
        color: #86868b;
        margin: 0;
    }
    .trade-row .tr-cell {
        text-align: left;
        min-width: 70px;
    }
    .trade-row .tr-cell .tr-val {
        font-size: 0.92rem;
        font-weight: 500;
        color: #1d1d1f;
        margin: 0;
    }
    .trade-row .tr-cell .tr-lbl {
        font-size: 0.72rem;
        color: #86868b;
        margin: 0;
    }
    .trade-row .status-badge {
        display: inline-block;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.75rem;
        font-weight: 600;
        color: #fff;
    }
    .status-closed { background: #81b29a; }
    .status-open { background: #81b29a; }
    .status-assigned { background: #86868b; }

    /* ── Performer block ── */
    .performer-block {
        background: #fff;
        border-radius: 18px;
        padding: 24px;
    }
    .performer-block h4 {
        margin: 0 0 12px 0;
        font-size: 1rem !important;
    }
    .performer-block .portfolio-cards {
        align-items: flex-start;
    }

    /* ── Portfolio strip cards ── */
    .portfolio-cards {
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 8px;
    }
    .portfolio-card {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 16px;
        padding: 12px 16px;
        background: #fff;
        border: 1px solid #d2d2d7;
        border-radius: 14px;
        flex-wrap: wrap;
        width: 100%;
        box-sizing: border-box;
    }
    .portfolio-card .pf-logo {
        width: 30px;
        height: 30px;
        border-radius: 50%;
        object-fit: cover;
        flex-shrink: 0;
    }
    .portfolio-card .pf-ticker {
        font-weight: 700;
        font-size: 1.05rem;
        color: #1d1d1f;
        min-width: 52px;
        flex-shrink: 0;
    }
    .portfolio-card .pf-cell {
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
    }
    .portfolio-card .pf-label {
        font-size: 0.7rem;
        color: #86868b;
        text-transform: uppercase;
        letter-spacing: 0.03em;
        line-height: 1.1;
        white-space: nowrap;
    }
    .portfolio-card .pf-val {
        font-size: 0.95rem;
        font-weight: 600;
        color: #1d1d1f;
        line-height: 1.3;
        white-space: nowrap;
    }
    .portfolio-card .pf-green { color: #81b29a; }
    .portfolio-card .pf-red { color: #e07a5f; }

    /* ── Performer grid (Top/Bottom side by side, stacked on mobile) ── */
    .performer-grid {
        display: flex;
        gap: 12px;
    }
    .performer-grid > div { flex: 1; min-width: 0; }
    @media (max-width: 768px) {
        .performer-grid { flex-direction: column; gap: 24px; }
        .portfolio-card { gap: 10px; padding: 10px 12px; }
        .portfolio-card .pf-cell { flex: 1; }
    }

    /* ── Expandable position cards ── */
    .pf-details { width: 100%; }
    .pf-details summary {
        list-style: none;
        cursor: pointer;
    }
    .pf-details summary::-webkit-details-marker { display: none; }
    .pf-details summary .portfolio-card {
        border-bottom-left-radius: 14px;
        border-bottom-right-radius: 14px;
        transition: border-radius 0.15s ease;
        position: relative;
    }
    .pf-details summary .portfolio-card::after {
        content: "›";
        font-size: 1.6rem;
        color: #86868b;
        flex-shrink: 0;
        position: absolute;
        right: 16px;
        transition: transform 0.2s ease;
    }
    .pf-details[open] summary .portfolio-card::after {
        transform: rotate(90deg);
    }
    .pf-details[open] summary .portfolio-card {
        border-bottom-left-radius: 0;
        border-bottom-right-radius: 0;
        border-bottom: none;
    }
    .pf-details[open] > .portfolio-card {
        border-top-left-radius: 0;
        border-top-right-radius: 0;
        margin-top: 0 !important;
    }

    /* ── Page transition loading overlay (only on full rerun, not fragment) ── */
    @keyframes pf-spin {
        to { transform: rotate(360deg); }
    }
    body:has([data-testid="stSidebar"] [data-stale="true"]) [data-testid="stMain"]::before {
        content: "";
        position: fixed;
        inset: 0;
        background: #fff;
        z-index: 9998;
    }
    body:has([data-testid="stSidebar"] [data-stale="true"]) [data-testid="stMain"]::after {
        content: "";
        position: fixed;
        top: 50%;
        left: 50%;
        width: 28px;
        height: 28px;
        margin: -14px 0 0 -14px;
        border: 3px solid #e5e5ea;
        border-top-color: #1d1d1f;
        border-radius: 50%;
        animation: pf-spin 0.6s linear infinite;
        z-index: 9999;
    }

</style>
""", unsafe_allow_html=True)


# ── Helper functions ──
def _flush_clean(buf, prev_pos, status):
    """Write only key progress lines (skip noisy debug output)."""
    buf.seek(prev_pos)
    new_text = buf.read()
    # We don't show the raw output — status steps handle the display
    return buf.tell()


def _build_excel_bytes(cfg):
    """Build DCF Excel model in memory, return bytes."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_path = os.path.join(script_dir, "dcf_template.py")

    ns = {}
    with open(template_path, "r") as f:
        exec(f.read(), ns)

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        ns["build_dcf_model"](cfg, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def run_analysis(ticker, peer_mode, manual_peers, margin_of_safety, terminal_growth, n_peers):
    """Run the full DCF pipeline and return (excel_bytes, cfg, credit_rating)."""

    buf = io.StringIO()
    _cr = "N/A"

    with st.status("Analyzing " + ticker + "...", expanded=True) as status:
        pos = 0

        # ── Step 1: Company lookup ──
        status.write("\u23f3 Looking up company in SEC EDGAR...")
        with contextlib.redirect_stdout(buf):
            cik = get_cik(ticker)
            time.sleep(0.2)
            submissions = fetch_company_submissions(cik)
        company_name = submissions.get("name", ticker)
        sic_code = int(submissions.get("sic", 0))
        sic_desc = submissions.get("sicDescription", "")
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 **{company_name}** \u2014 {sic_desc}")

        # ── Step 2: Sector betas ──
        status.write("\u23f3 Determining sector & beta...")
        with contextlib.redirect_stdout(buf):
            if sic_code in SIC_TO_SECTOR:
                sector_name, sector_beta = SIC_TO_SECTOR[sic_code]
                sector_betas = [(sector_name, sector_beta, 1.0)]
            else:
                dam_betas = fetch_sector_betas()
                if dam_betas:
                    best_match, best_score = None, 0
                    sic_words = set(sic_desc.lower().split())
                    for sector, beta in dam_betas.items():
                        sector_words = set(sector.lower().split())
                        overlap = len(sic_words & sector_words)
                        if overlap > best_score:
                            best_score = overlap
                            best_match = (sector, beta)
                    if best_match and best_score > 0:
                        sector_name, sector_beta = best_match
                        sector_betas = [(sector_name, sector_beta, 1.0)]
                    else:
                        sector_betas = [("Market", 1.0, 1.0)]
                else:
                    sector_betas = [("Market", 1.0, 1.0)]
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 Sector: {sector_betas[0][0]} (beta {sector_betas[0][1]:.2f})")

        # ── Step 3: Financials ──
        status.write("\u23f3 Fetching financial statements from EDGAR...")
        with contextlib.redirect_stdout(buf):
            facts = fetch_company_facts(cik)
            financials = parse_financials(facts, n_years=6)
        pos = _flush_clean(buf, pos, status)
        years = financials.get("years", [])
        if years:
            status.write(f"\u2705 {len(years)} years of data ({years[0]}\u2013{years[-1]})")
        else:
            status.write(f"\u2705 Financial data loaded")

        # ── Step 4: Market data ──
        status.write("\u23f3 Fetching market data...")
        with contextlib.redirect_stdout(buf):
            stock_price, market_cap, shares_yahoo = fetch_stock_price(ticker)
            risk_free_rate = fetch_treasury_yield()
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 ${stock_price:.2f} per share \u2014 10Y Treasury: {risk_free_rate:.2%}")

        # ── Step 5: Credit rating + sector margin + consensus ──
        status.write("\u23f3 Analyzing credit, margins & analyst estimates...")
        with contextlib.redirect_stdout(buf):
            oi_latest = financials["operating_income"][-1] if financials["operating_income"] else 0
            ie_latest = financials["interest_expense_latest"]
            credit_rating, credit_spread = synthetic_credit_rating(oi_latest, ie_latest)
            _cr = credit_rating

            if market_cap == 0 and stock_price > 0:
                edgar_shares = financials["shares"][-1] if financials["shares"] and financials["shares"][-1] > 0 else 0
                if edgar_shares > 0:
                    market_cap = round(stock_price * edgar_shares, 0)

            sector_margin = None
            sector_name_for_margin = sector_betas[0][0] if sector_betas else ""
            if sector_name_for_margin:
                dam_margins = fetch_sector_margins()
                if dam_margins:
                    if sector_name_for_margin in dam_margins:
                        sector_margin = dam_margins[sector_name_for_margin]
                    else:
                        target_words = set(sector_name_for_margin.lower().replace("/", " ").split())
                        best_m, best_s = None, 0
                        for sec_name, sec_margin in dam_margins.items():
                            sec_words = set(sec_name.lower().replace("/", " ").split())
                            overlap = len(target_words & sec_words)
                            if overlap > best_s:
                                best_s = overlap
                                best_m = (sec_name, sec_margin)
                        if best_m and best_s > 0:
                            sector_margin = best_m[1]

            consensus = fetch_consensus_estimates(ticker)
        pos = _flush_clean(buf, pos, status)
        status.write(f"\u2705 Credit: {credit_rating} (spread {credit_spread:.2%})")

        # ── Step 6: Peers ──
        peers = []
        peer_tickers = []
        if peer_mode == "Auto-discover":
            status.write(f"\u23f3 Auto-discovering {n_peers} comparable companies...")
            with contextlib.redirect_stdout(buf):
                peer_tickers = find_peers(
                    sic_code=sic_code,
                    target_ticker=ticker,
                    target_market_cap=market_cap,
                    n_peers=n_peers,
                )
            pos = _flush_clean(buf, pos, status)
            if peer_tickers:
                status.write(f"\u2705 Found peers: {', '.join(peer_tickers)}")
        elif peer_mode == "Manual" and manual_peers:
            peer_tickers = [t.strip().upper() for t in manual_peers.split(",") if t.strip()]

        if peer_tickers:
            status.write(f"\u23f3 Gathering data for {len(peer_tickers)} peers...")
            with contextlib.redirect_stdout(buf):
                peers = fetch_peer_data(peer_tickers)
            pos = _flush_clean(buf, pos, status)
            status.write(f"\u2705 {len(peers)} peer profiles loaded")
        elif peer_mode != "None":
            status.write("\u2705 No peers found")
        else:
            status.write("\u2705 Peer analysis skipped")

        # ── Step 7: Build config ──
        status.write("\u23f3 Building DCF model assumptions...")
        with contextlib.redirect_stdout(buf):
            cfg = build_config(
                ticker=ticker,
                financials=financials,
                stock_price=stock_price,
                market_cap=market_cap,
                shares_yahoo=shares_yahoo,
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
        pos = _flush_clean(buf, pos, status)

        # Validate: enough data to build a meaningful DCF?
        base_rev = cfg.get("base_revenue", 0)
        base_year = cfg.get("base_year", 0)
        if base_rev <= 0:
            raise ValueError(
                f"{company_name} has no revenue data (or $0). "
                "A DCF model requires a company with revenue history."
            )
        if base_year < 2018:
            raise ValueError(
                f"{company_name}'s most recent filing is from {base_year}. "
                "The data is too old for a meaningful DCF analysis."
            )
        if market_cap <= 0 and stock_price <= 0:
            raise ValueError(
                f"Could not determine market cap or stock price for {ticker}. "
                "The company may be delisted or have no trading data."
            )

        status.write(f"\u2705 Configuration complete")

        # ── Step 8: Generate Excel ──
        status.write("\u23f3 Generating Excel DCF model...")
        with contextlib.redirect_stdout(buf):
            excel_bytes = _build_excel_bytes(cfg)
        pos = _flush_clean(buf, pos, status)

        status.update(label=f"Analysis complete \u2014 {company_name} ({ticker})", state="complete", expanded=False)

    return excel_bytes, cfg, _cr


# ══════════════════════════════════════════════════════
#  SIDEBAR — Navigation + page-specific settings
# ══════════════════════════════════════════════════════

with st.sidebar:
    page = st.radio(
        "Navigate",
        ["DCF Valuation", "Portfolio", "Wheel Cost Basis", "Results"],
        label_visibility="collapsed",
    )
    st.markdown("---")

    if page == "DCF Valuation":
        st.markdown("### Settings")
        st.markdown("")

        margin_of_safety = st.slider(
            "Margin of Safety",
            min_value=0,
            max_value=50,
            value=int(MARGIN_OF_SAFETY_DEFAULT * 100),
            step=5,
            format="%d%%",
            help="Discount applied to fair value to determine buy price",
        ) / 100.0

        terminal_growth = st.slider(
            "Terminal Growth Rate",
            min_value=1.0,
            max_value=5.0,
            value=TERMINAL_GROWTH_DEFAULT * 100,
            step=0.5,
            format="%.1f%%",
            help="Long-term perpetuity growth rate (GDP + inflation)",
        ) / 100.0

        n_peers = st.number_input(
            "Number of Peers",
            min_value=0,
            max_value=20,
            value=6,
            help="How many comparable companies to include",
        )

        st.markdown("---")
        st.markdown(
            "<small style='color: #86868b'>Data: SEC EDGAR, Yahoo Finance, Damodaran<br>"
            "Methodology: Damodaran DCF with Sales-to-Capital reinvestment</small>",
            unsafe_allow_html=True,
        )

    elif page in ("Portfolio", "Wheel Cost Basis", "Results"):
        st.markdown("### Tastytrade")
        if st.button("Refresh Data", use_container_width=True, type="primary"):
            st.session_state.pop("portfolio_data", None)
            st.session_state.pop("portfolio_account", None)
            st.session_state.pop("portfolio_prices", None)
            st.session_state.pop("net_liq_all", None)
            st.session_state.pop("yearly_transfers", None)
            st.session_state.pop("benchmark_returns", None)
            for k in [k for k in st.session_state if k.startswith("net_liq_")]:
                st.session_state.pop(k, None)
            st.rerun()

        st.markdown("---")
        st.markdown(
            "<small style='color: #86868b'>Data: Tastytrade API<br>"
            "Tracks wheel strategy cost basis</small>",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════
#  SHARED DATA LOADING FOR PORTFOLIO PAGES
# ══════════════════════════════════════════════════════

def _load_portfolio_data():
    """Fetch and enrich portfolio data (cached in session_state)."""
    if "portfolio_data" not in st.session_state:
        with st.spinner("Fetching transactions from Tastytrade..."):
            try:
                cost_basis, acct = fetch_portfolio_data()
                st.session_state.portfolio_data = cost_basis
                st.session_state.portfolio_account = acct
            except Exception as e:
                st.error(f"Failed to fetch data: {e}")
                with st.expander("Error details"):
                    st.exception(e)
                st.stop()

    cost_basis = st.session_state.portfolio_data
    acct = st.session_state.get("portfolio_account", "")

    if not cost_basis:
        st.info("No transactions found.")
        st.stop()

    if "portfolio_prices" not in st.session_state:
        held_tickers = [t for t, d in cost_basis.items() if d["shares_held"] > 0]
        if held_tickers:
            with st.spinner("Fetching current prices..."):
                st.session_state.portfolio_prices = fetch_current_prices(held_tickers)
        else:
            st.session_state.portfolio_prices = {}

    prices = st.session_state.portfolio_prices

    for ticker, data in cost_basis.items():
        price_data = prices.get(ticker)
        shares = data["shares_held"]
        if price_data and shares > 0:
            price = price_data["price"]
            data["current_price"] = price
            data["previous_close"] = price_data.get("previousClose") or price
            data["market_value"] = price * shares
            data["total_pl_real"] = data["total_pl"] + data["market_value"]
        else:
            data["current_price"] = 0.0
            data["previous_close"] = 0.0
            data["market_value"] = 0.0
            data["total_pl_real"] = data["total_pl"]

    return cost_basis


def _color_val(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return "color: #81b29a"
        elif val < 0:
            return "color: #e07a5f"
    return ""


def _parse_option_symbol(symbol):
    """Extract strike, expiration, and type from OCC option symbol like MSFT  250321C00420000."""
    if not symbol:
        return None, None, None
    m = re.match(r'^(.+?)\s*(\d{6})([CP])(\d{8})$', symbol.strip())
    if not m:
        return None, None, None
    date_str, cp, strike_raw = m.group(2), m.group(3), m.group(4)
    strike = int(strike_raw) / 1000
    try:
        exp = datetime.strptime(date_str, "%y%m%d")
        return strike, exp.strftime("%d-%m-%Y"), cp
    except ValueError:
        return strike, None, cp


def _find_open_options(trades):
    """Find currently open option positions from a ticker's trade list.

    Returns list of dicts with keys: symbol, type (CSP/CC/Put/Call),
    strike, expiration, quantity, premium.
    """
    positions = {}  # keyed by symbol
    for t in trades:
        inst = t.get("instrument_type", "")
        if "Option" not in inst:
            continue
        symbol = t.get("symbol", "")
        action = t.get("action", "")
        label = t.get("label", "")
        qty = t["quantity"]
        net = t["net_value"]

        if symbol not in positions:
            positions[symbol] = {"qty": 0, "premium": 0.0, "label": "", "trades": []}

        pos = positions[symbol]
        if action == "Sell to Open":
            pos["qty"] += qty
            pos["premium"] += net
            pos["label"] = label  # CSP or CC
        elif action == "Buy to Open":
            pos["qty"] += qty
            pos["premium"] += net
            pos["label"] = label
        elif action in ("Buy to Close", "Sell to Close"):
            pos["qty"] -= qty
            pos["premium"] += net
        elif label in ("Expired", "Assignment"):
            pos["qty"] -= qty

    result = []
    for symbol, pos in positions.items():
        if pos["qty"] > 0:
            strike, exp, cp = _parse_option_symbol(symbol)
            opt_type = pos["label"] or ("Put" if cp == "P" else "Call" if cp == "C" else "Option")
            result.append({
                "symbol": symbol,
                "type": opt_type,
                "strike": strike,
                "expiration": exp,
                "cp": cp,
                "quantity": int(pos["qty"]),
                "premium": pos["premium"],
            })
    return result


# ══════════════════════════════════════════════════════
#  DCF VALUATION PAGE
# ══════════════════════════════════════════════════════

if page == "DCF Valuation":

    st.markdown(
        "<style>.block-container { max-width: 730px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )
    st.markdown("## DCF Valuation")
    st.markdown(
        '<p style="color: #86868b; font-size: 1.05rem; line-height: 1.6; max-width: 560px;">'
        'Generate a full Discounted Cash Flow analysis from SEC filings. '
        '10-year projection, WACC calculation, and a professional Excel workbook.'
        '</p>',
        unsafe_allow_html=True,
    )

    st.markdown("")

    # ── Input Form ──
    with st.form("dcf_form"):
        col1, col2 = st.columns([2, 1])

        with col1:
            ticker_input = st.text_input(
                "Stock Ticker",
                placeholder="e.g. MSFT, AAPL, PANW",
                max_chars=10,
            )

        with col2:
            peer_mode = st.selectbox(
                "Peer Comparison",
                options=["Auto-discover", "Manual", "None"],
                index=0,
            )

        manual_peers = ""
        if peer_mode == "Manual":
            manual_peers = st.text_input(
                "Peer tickers (comma-separated)",
                placeholder="AAPL, GOOGL, AMZN, META",
            )

        submitted = st.form_submit_button("Generate DCF Model", type="primary", use_container_width=True)

    # ── Run Analysis ──
    if submitted and ticker_input:
        ticker = ticker_input.strip().upper()

        try:
            excel_bytes, cfg, credit_rating = run_analysis(
                ticker=ticker,
                peer_mode=peer_mode,
                manual_peers=manual_peers,
                margin_of_safety=margin_of_safety,
                terminal_growth=terminal_growth,
                n_peers=n_peers,
            )

            st.session_state.excel_data = excel_bytes
            st.session_state.cfg = cfg
            st.session_state.ticker = ticker
            st.session_state.credit_rating = credit_rating

        except Exception as e:
            st.error(f"Analysis failed: {e}")
            with st.expander("Error details"):
                st.exception(e)

    elif submitted:
        st.warning("Please enter a ticker symbol.")


    # ══════════════════════════════════════════════════════
    #  RESULTS
    # ══════════════════════════════════════════════════════

    if "excel_data" in st.session_state and st.session_state.excel_data:
        ticker = st.session_state.ticker
        cfg = st.session_state.cfg
        credit_rating = st.session_state.get("credit_rating", "N/A")

        # ── Success Banner + Download ──
        st.markdown(
            f'<div class="success-banner">'
            f'<h2>{cfg.get("company", ticker)} ({ticker})</h2>'
            f'<p>DCF model ready \u2014 10-year projection with {len(cfg.get("peers", []))} peer comparisons</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.download_button(
            label=f"Download {ticker}_DCF.xlsx",
            data=st.session_state.excel_data,
            file_name=f"{ticker}_DCF.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )

        st.markdown("")

        # ── Key Metrics ──
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Stock Price", f"${cfg.get('stock_price', 0):.2f}")
        c2.metric("Market Cap", f"${cfg.get('equity_market_value', 0):,.0f}M")
        c3.metric("Revenue", f"${cfg.get('base_revenue', 0):,.0f}M")
        c4.metric("Op. Margin", f"{cfg.get('base_op_margin', 0):.1%}")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("WACC Inputs", f"Rf {cfg.get('risk_free_rate', 0):.1%}")
        c6.metric("Credit", credit_rating)
        c7.metric("Terminal Growth", f"{cfg.get('terminal_growth', 0):.1%}")
        c8.metric("MoS", f"{cfg.get('margin_of_safety', 0):.0%}")

        st.markdown("")

        # ── Projection Charts ──
        growth = cfg.get("revenue_growth", [])
        margins = cfg.get("op_margins", [])
        base_year = cfg.get("base_year", 2025)

        if growth and margins:
            col_g, col_m = st.columns(2)

            proj_years = [str(base_year + i + 1) for i in range(len(growth))]

            with col_g:
                st.markdown('<p class="chart-label">Revenue Growth</p>', unsafe_allow_html=True)
                df_growth = pd.DataFrame({
                    "Year": proj_years,
                    "Growth": [g * 100 for g in growth],
                }).set_index("Year")
                st.bar_chart(df_growth, color="#81b29a", height=200)

            with col_m:
                st.markdown('<p class="chart-label">Operating Margin</p>', unsafe_allow_html=True)
                df_margin = pd.DataFrame({
                    "Year": proj_years,
                    "Margin": [m * 100 for m in margins],
                }).set_index("Year")
                st.bar_chart(df_margin, color="#81b29a", height=200)

        # ── Peer Comparison Table ──
        peers = cfg.get("peers", [])
        if peers:
            st.markdown("")
            st.markdown("#### Peer Comparison")

            peer_rows = []
            for p in peers:
                peer_rows.append({
                    "Ticker": p.get("ticker", ""),
                    "Company": p.get("name", ""),
                    "EV/Revenue": f"{p.get('ev_revenue', 0):.1f}x",
                    "EV/EBITDA": f"{p.get('ev_ebitda', 0):.1f}x",
                    "P/E": f"{p.get('pe', 0):.1f}x",
                    "Op. Margin": f"{p.get('op_margin', 0):.1%}",
                    "Rev. Growth": f"{p.get('rev_growth', 0):.1%}",
                })

            df_peers = pd.DataFrame(peer_rows)
            st.dataframe(
                df_peers,
                use_container_width=True,
                hide_index=True,
            )

        # ── Detailed Assumptions (collapsed) ──
        with st.expander("View Detailed Assumptions"):
            st.markdown("**Revenue Growth Trajectory**")
            if growth:
                growth_str = " \u2192 ".join(f"{g:.1%}" for g in growth)
                st.code(growth_str, language=None)

            st.markdown("**Operating Margin Trajectory**")
            if margins:
                margin_str = " \u2192 ".join(f"{m:.1%}" for m in margins)
                st.code(margin_str, language=None)

            det1, det2 = st.columns(2)
            with det1:
                st.markdown("**WACC Components**")
                st.markdown(f"- Risk-free rate: {cfg.get('risk_free_rate', 0):.2%}")
                st.markdown(f"- Equity risk premium: {cfg.get('erp', 0):.2%}")
                st.markdown(f"- Credit spread: {cfg.get('credit_spread', 0):.2%}")
                st.markdown(f"- Tax rate: {cfg.get('tax_rate', 0):.0%}")

            with det2:
                st.markdown("**Other Assumptions**")
                st.markdown(f"- Sales-to-capital: {cfg.get('sales_to_capital', 0):.2f}")
                st.markdown(f"- SBC as %% of revenue: {cfg.get('sbc_pct', 0):.1%}")
                st.markdown(f"- Buyback rate: {cfg.get('buyback_rate', 0):.1%}")
                st.markdown(f"- Terminal margin: {cfg.get('terminal_margin', 0):.1%}")

            betas = cfg.get("sector_betas", [])
            if betas:
                st.markdown("**Sector Betas**")
                for name, beta, weight in betas:
                    st.markdown(f"- {name}: {beta:.2f} (weight {weight:.0%})")

            debt = cfg.get("debt_breakdown", [])
            if debt:
                st.markdown("**Debt Breakdown**")
                for label, amount in debt:
                    st.markdown(f"- {label}: ${amount:,.0f}M")


# ══════════════════════════════════════════════════════
#  PORTFOLIO PAGE — Active positions overview
# ══════════════════════════════════════════════════════

elif page == "Portfolio":

    st.markdown(
        "<style>.block-container { max-width: 1200px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )
    st.markdown("")
    cost_basis = _load_portfolio_data()

    held = {t: d for t, d in cost_basis.items() if d["shares_held"] > 0}

    if not held:
        st.info("No active positions.")
        st.stop()

    held_tickers = list(held.keys())

    # ── Margin / Buying Power (with integrated simulator) ──
    @st.cache_data(ttl=60, show_spinner=False)
    def _cached_account_balances():
        return fetch_account_balances()

    def _margin_overview():
        st.markdown("")
        try:
            bal = _cached_account_balances()
        except Exception:
            bal = None

        if not bal:
            return

        net_liq = bal["net_liquidating_value"]
        maint_excess = bal["maintenance_excess"]
        bp = bal["derivative_buying_power"]
        used_bp = bal["used_derivative_buying_power"]
        total_bp = bp + used_bp

        # ── Simulator inputs (rendered first so we know the margin impact) ──
        st.markdown('<p style="font-weight:600;margin-top:16px;margin-bottom:4px">Simulate Positions</p>', unsafe_allow_html=True)

        if "sim_rows" not in st.session_state:
            st.session_state["sim_rows"] = 1

        total_sim_cost = 0.0
        total_sim_margin = 0.0
        sim_entries = []

        h1, h2, h3 = st.columns([1, 1, 1], gap="small")
        h1.markdown('<span style="font-size:0.8rem;color:#86868b">Ticker</span>', unsafe_allow_html=True)
        h2.markdown('<span style="font-size:0.8rem;color:#86868b">Shares</span>', unsafe_allow_html=True)
        h3.markdown('<span style="font-size:0.8rem;color:#86868b">Price</span>', unsafe_allow_html=True)

        for i in range(st.session_state["sim_rows"]):
            c1, c2, c3 = st.columns([1, 1, 1], gap="small")
            with c1:
                ticker = st.text_input("Ticker", placeholder="AAPL", key=f"sim_tick_{i}", label_visibility="collapsed")
            with c2:
                shares = st.number_input("Shares", min_value=0, value=100, step=10, key=f"sim_sh_{i}", label_visibility="collapsed")

            # Auto-fetch price when ticker is entered and price not yet set
            price_key = f"sim_pr_{i}"
            if ticker and st.session_state.get(price_key, 0.0) == 0.0:
                try:
                    _sp = fetch_current_prices([ticker.upper()])
                    _spd = _sp.get(ticker.upper())
                    if _spd and _spd["price"]:
                        st.session_state[price_key] = float(_spd["price"])
                except Exception:
                    pass

            with c3:
                price = st.number_input("Price", min_value=0.0, step=0.01, format="%.2f", key=price_key, label_visibility="collapsed")

            if ticker and price > 0 and shares > 0:
                cost = price * shares
                margin = cost * 0.25
                total_sim_cost += cost
                total_sim_margin += margin
                sim_entries.append(f'{shares}x {ticker.upper()} @ ${price:,.2f}')

        _, btn_col, _ = st.columns([1, 1, 1])
        with btn_col:
            if st.button("Add row", key="sim_add_row", type="primary", use_container_width=True):
                st.session_state["sim_rows"] += 1
                st.rerun()

        # ── Compute final values (base + simulation) ──
        show_used = used_bp + total_sim_margin
        show_bp = bp - total_sim_margin
        show_excess = maint_excess - total_sim_margin
        show_usage = (show_used / total_bp * 100) if total_bp > 0 else 0
        show_drop = (show_excess / net_liq * 100) if net_liq > 0 else 0

        if show_usage < 50:
            bar_color = "#81b29a"
            status = "Healthy"
        elif show_usage < 75:
            bar_color = "#f2cc8f"
            status = "Moderate"
        else:
            bar_color = "#e07a5f"
            status = "Caution"

        # Simulation subtitle
        sim_note = ""
        if total_sim_cost > 0:
            sim_label = " + ".join(sim_entries)
            sim_note = (
                f'<div style="margin-bottom:12px;padding:8px 12px;background:#f7f8fa;border-radius:8px;'
                f'border:1px dashed #d2d2d7;font-size:0.85rem">'
                f'<span style="color:#86868b">Simulating: </span>'
                f'<b>{sim_label}</b>'
                f'<span style="color:#86868b"> = ${total_sim_cost:,.0f}</span>'
                f'</div>'
            )

        st.markdown(
            f'<div class="hero-card">'
            f'<h4>Margin Overview</h4>'
            f'{sim_note}'
            f'<div style="margin:16px 0">'
            f'  <div style="display:flex;justify-content:space-between;margin-bottom:6px">'
            f'    <span style="font-size:0.85rem;color:#86868b">BP Used: ${show_used:,.0f} / ${total_bp:,.0f}</span>'
            f'    <span style="font-size:0.85rem;font-weight:600;color:{bar_color}">{status} ({show_usage:.0f}%)</span>'
            f'  </div>'
            f'  <div style="position:relative;height:28px">'
            f'    <div style="position:absolute;top:8px;left:0;right:0;background:#f0f0f2;border-radius:8px;height:12px;overflow:hidden">'
            f'      <div style="background:{bar_color};width:{min(show_usage, 100):.0f}%;height:100%;border-radius:8px;'
            f'           transition:width 0.3s ease"></div>'
            f'    </div>'
            f'    <div style="position:absolute;left:50%;top:0;height:28px;display:flex;flex-direction:column;align-items:center;transform:translateX(-50%)">'
            f'      <div style="width:2px;height:28px;background:#f2cc8f"></div>'
            f'    </div>'
            f'    <div style="position:absolute;left:75%;top:0;height:28px;display:flex;flex-direction:column;align-items:center;transform:translateX(-50%)">'
            f'      <div style="width:2px;height:28px;background:#e07a5f"></div>'
            f'    </div>'
            f'  </div>'
            f'  <div style="position:relative;height:16px;font-size:0.7rem;color:#86868b">'
            f'    <span style="position:absolute;left:50%;transform:translateX(-50%)">50%</span>'
            f'    <span style="position:absolute;left:75%;transform:translateX(-50%)">75%</span>'
            f'  </div>'
            f'</div>'
            f'<div class="stat-row">'
            f'<span class="stat-pill">Buying Power <b>${show_bp:,.0f}</b></span>'
            f'<span class="stat-pill">BP in Use <b>${show_used:,.0f}</b></span>'
            f'<span class="stat-pill">Buffer <b>${show_excess:,.0f}</b></span>'
            f'<span class="stat-pill">Margin Call at <b>-{show_drop:.0f}%</b></span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Store balance for interest card
        st.session_state["_margin_cash"] = bal["cash_balance"]

    @st.fragment(run_every=timedelta(seconds=30))
    def _portfolio_cards():
        # Fetch fresh prices + account balances
        prices = fetch_current_prices(held_tickers)
        try:
            balances = fetch_account_balances()
        except Exception:
            balances = None

        for ticker, data in held.items():
            price_data = prices.get(ticker)
            shares = data["shares_held"]
            if price_data and shares > 0:
                p = price_data["price"]
                data["current_price"] = p
                data["previous_close"] = price_data.get("previousClose") or p
                data["market_value"] = p * shares

        # ── Hero card ──
        if balances:
            net_liq = balances["net_liquidating_value"]
            cash = balances["cash_balance"]
        else:
            net_liq = sum(d["market_value"] for d in held.values())
            cash = 0.0

        total_value = sum(d["market_value"] for d in held.values())
        total_prev = sum(d.get("previous_close", 0) * d["shares_held"] for d in held.values())
        day_chg_pct = ((total_value - total_prev) / total_prev * 100) if total_prev else 0.0
        day_chg_cls = "hero-green" if day_chg_pct >= 0 else "hero-red"
        day_chg_sign = "+" if day_chg_pct >= 0 else ""

        day_chg_dollar = total_value - total_prev
        day_dollar_sign = "+" if day_chg_dollar >= 0 else ""
        nlv_cls = "hero-green" if net_liq >= 0 else "hero-red"

        st.markdown(
            f'<div class="hero-card">'
            f'<p class="hero-label">Net Liquidating Value</p>'
            f'<p class="hero-value {nlv_cls}">${net_liq:,.0f}</p>'
            f'<p class="hero-sub"><span class="{day_chg_cls}">{day_chg_sign}{day_chg_pct:.2f}% ({day_dollar_sign}${abs(day_chg_dollar):,.0f})</span> today &nbsp;·&nbsp; {len(held)} active positions</p>'
            f'<div class="stat-row">'
            f'<span class="stat-pill">Cash <b>${cash:,.0f}</b></span>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Column picker & Sort ──
        all_cols = ["Shares", "Buy Price", "Cost/Share", "Break-even", "Current Price",
                    "Day %", "Mkt Value", "Unrealized P/L", "Return %", "Ann. %",
                    "Premie", "Days", "Weight"]
        default_cols = ["Shares", "Buy Price", "Cost/Share", "Current Price", "Day %",
                        "Mkt Value", "Unrealized P/L", "Return %", "Weight"]
        sort_options = ["Ticker", "Weight", "Day %", "Return %", "Unrealized P/L", "Mkt Value", "Ann. %"]

        with st.container(key="toolbar_inline"):
            col_left, col_right = st.columns(2)
            with col_left:
                with st.popover("\u2699 Columns"):
                    selected = st.pills(
                        "Toggle columns",
                        all_cols,
                        default=default_cols,
                        selection_mode="multi",
                        label_visibility="collapsed",
                    )
            with col_right:
                with st.popover("\u2195 Sort"):
                    sort_by = st.pills(
                        "Sort by",
                        sort_options,
                        default="Ticker",
                        label_visibility="collapsed",
                    )

        # ── Build rows ──
        rows = []
        for ticker, data in held.items():
            wheels = data.get("wheels", [])
            last_wheel = wheels[-1] if wheels else None

            purchase_price = 0.0
            wheel_equity_cost = 0.0
            wheel_option_pl = 0.0
            wheel_shares = 0
            total_buy_price = 0.0

            if last_wheel:
                for t in last_wheel["trades"]:
                    if t["instrument_type"] == "Equity":
                        wheel_equity_cost += t["net_value"]
                        action = t.get("action", "")
                        txn_type = t.get("type", "")
                        qty = t["quantity"]
                        price = t["price"] if t["price"] else abs(t["net_value"]) / qty if qty else 0.0
                        if txn_type == "Receive Deliver":
                            if t["net_value"] < 0:
                                wheel_shares += qty
                                total_buy_price += price * qty
                        elif "Buy" in action:
                            wheel_shares += qty
                            total_buy_price += price * qty
                    elif "Option" in t["instrument_type"]:
                        wheel_option_pl += t["net_value"]

                if wheel_shares > 0:
                    purchase_price = total_buy_price / wheel_shares
                shares = data["shares_held"]
                wheel_cps = (wheel_equity_cost + wheel_option_pl) / shares if shares else 0.0
            else:
                wheel_cps = data["cost_per_share"]

            unrealized = data["market_value"] + wheel_equity_cost if last_wheel else data["market_value"] + data["equity_cost"]
            days_held = (date.today() - last_wheel["start"]).days if last_wheel else 0

            prev = data.get("previous_close", 0)
            cur = data["current_price"]
            day_change_pct = ((cur - prev) / prev * 100) if prev else 0.0

            initial_investment = abs(wheel_equity_cost) if last_wheel else abs(data["equity_cost"])
            return_pct = (unrealized / initial_investment * 100) if initial_investment else 0.0
            if days_held > 0 and initial_investment:
                ann_return = ((1 + unrealized / initial_investment) ** (365 / days_held) - 1) * 100
            else:
                ann_return = 0.0

            shares = data["shares_held"]
            break_even = abs(wheel_cps) if last_wheel and shares else purchase_price

            rows.append({
                "Logo": f"https://assets.parqet.com/logos/symbol/{ticker}",
                "Ticker": ticker,
                "Shares": shares,
                "Buy Price": purchase_price,
                "Cost/Share": wheel_cps,
                "Break-even": break_even,
                "Current Price": cur,
                "Day %": day_change_pct,
                "Mkt Value": data["market_value"],
                "Unrealized P/L": unrealized,
                "Return %": return_pct,
                "Ann. %": ann_return,
                "Premie": wheel_option_pl if last_wheel else 0.0,
                "Days": days_held,
            })

        for row in rows:
            row["Weight"] = row["Mkt Value"] / total_value * 100 if total_value else 0.0

        # ── Sort rows ──
        if sort_by == "Ticker":
            rows.sort(key=lambda r: r["Ticker"])
        else:
            rows.sort(key=lambda r: r.get(sort_by, 0), reverse=True)

        # ── Format helpers ──
        color_cols_set = {"Unrealized P/L", "Day %", "Return %", "Ann. %"}

        def _fmt_cell(col, val):
            cls = ""
            if col in color_cols_set:
                cls = " pf-green" if val > 0 else " pf-red" if val < 0 else ""
            if col in ("Buy Price", "Cost/Share", "Break-even", "Current Price"):
                return f"${val:,.2f}", cls
            if col == "Mkt Value":
                return f"${val:,.0f}", cls
            if col == "Unrealized P/L":
                return f"${val:+,.0f}", cls
            if col == "Premie":
                return f"${val:,.0f}", cls
            if col in ("Day %", "Return %", "Ann. %"):
                return f"{val:+.2f}%", cls
            if col == "Weight":
                return f"{val:.1f}%", cls
            if col == "Shares":
                return f"{int(val)}", cls
            if col == "Days":
                return f"{int(val)}", cls
            return f"{val}", cls

        # ── Detect open options per ticker ──
        opts_by_ticker = {}
        for ticker, data in held.items():
            open_opts = _find_open_options(data.get("trades", []))
            if open_opts:
                opts_by_ticker[ticker] = open_opts

        # ── Render cards ──
        cards_html = '<div class="portfolio-cards">'
        for row in rows:
            cells = ""
            for col in selected:
                fval, cls = _fmt_cell(col, row[col])
                cells += (
                    f'<div class="pf-cell">'
                    f'<span class="pf-label">{col}</span>'
                    f'<span class="pf-val{cls}">{fval}</span>'
                    f'</div>'
                )
            card_inner = (
                f'<div class="portfolio-card">'
                f'<img class="pf-logo" src="{row["Logo"]}" onerror="this.style.display=\'none\'">'
                f'<span class="pf-ticker">{row["Ticker"]}</span>'
                f'{cells}'
                f'</div>'
            )

            ticker = row["Ticker"]
            open_opts = opts_by_ticker.get(ticker)

            if open_opts:
                # Build option sub-cards
                opt_cards = ''
                for opt in open_opts:
                    strike_str = f"${opt['strike']:,.2f}" if opt["strike"] else "\u2014"
                    exp_str = opt["expiration"] or "\u2014"
                    prem_cls = " pf-green" if opt["premium"] > 0 else " pf-red" if opt["premium"] < 0 else ""
                    opt_cards += (
                        f'<div class="portfolio-card" style="border-style:dashed;margin-top:6px">'
                        f'<span class="pf-ticker" style="min-width:40px">{opt["type"]}</span>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Strike</span>'
                        f'<span class="pf-val">{strike_str}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Expiration</span>'
                        f'<span class="pf-val">{exp_str}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Qty</span>'
                        f'<span class="pf-val">{opt["quantity"]}</span>'
                        f'</div>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Premium</span>'
                        f'<span class="pf-val{prem_cls}">${opt["premium"]:+,.0f}</span>'
                        f'</div>'
                        f'</div>'
                    )
                cards_html += (
                    f'<details class="pf-details">'
                    f'<summary>{card_inner}</summary>'
                    f'{opt_cards}'
                    f'</details>'
                )
            else:
                cards_html += card_inner

        cards_html += '</div>'
        st.markdown(cards_html, unsafe_allow_html=True)

    _portfolio_cards()
    _margin_overview()

    # ── Portfolio Greeks & Margin Interest (side by side) ──
    gk = None
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(fetch_portfolio_greeks)
            gk = future.result(timeout=15)
    except Exception:
        gk = None

    mi = None
    try:
        mi = fetch_margin_interest()
    except Exception:
        mi = None

    cash = st.session_state.get("_margin_cash", 0.0)
    debt = abs(cash) if cash < 0 else 0.0
    has_greeks = gk and gk["positions"]
    has_interest = debt > 0 or (mi and mi["total"] < 0)

    if has_greeks or has_interest:
        if has_greeks and has_interest:
            col_greeks, col_interest = st.columns(2)
        elif has_greeks:
            col_greeks = st.container()
            col_interest = None
        else:
            col_interest = st.container()
            col_greeks = None

        if has_greeks and col_greeks:
            tot = gk["totals"]
            theta = tot["theta"]
            delta = tot["delta"]
            vega = tot["vega"]
            theta_color = "#81b29a" if theta >= 0 else "#e07a5f"

            with col_greeks:
                st.markdown(
                    f'<div class="hero-card" style="height:100%">'
                    f'<h4>Portfolio Greeks</h4>'
                    f'<div style="text-align:center;margin-bottom:16px">'
                    f'  <span style="font-size:1.6rem;font-weight:700;color:{theta_color}">${theta:,.0f}</span>'
                    f'  <span style="font-size:0.85rem;color:#86868b">theta / day</span>'
                    f'</div>'
                    f'<div class="stat-row">'
                    f'<span class="stat-pill">Delta <b>{delta:,.0f}</b>'
                    f'  <span style="font-size:0.7rem;color:#86868b">$ per $1 move</span></span>'
                    f'<span class="stat-pill">Vega <b>${vega:,.0f}</b>'
                    f'  <span style="font-size:0.7rem;color:#86868b">per 1%% IV</span></span>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        if has_interest and col_interest:
            est_monthly = debt * 0.11 / 12
            cur_mo = abs(mi["current_month"]) if mi else 0
            ytd = abs(mi["ytd"]) if mi else 0
            total_int = abs(mi["total"]) if mi else 0

            with col_interest:
                st.markdown(
                    f'<div class="hero-card" style="height:100%">'
                    f'<h4>Margin Interest</h4>'
                    f'<div style="text-align:center;margin-bottom:16px">'
                    f'  <span style="font-size:1.6rem;font-weight:700;color:#e07a5f">${debt:,.0f}</span>'
                    f'  <span style="font-size:0.85rem;color:#86868b">margin debt</span>'
                    f'</div>'
                    f'<div class="stat-row">'
                    f'<span class="stat-pill">This Month <b style="color:#e07a5f">-${cur_mo:,.0f}</b></span>'
                    f'<span class="stat-pill">YTD <b style="color:#e07a5f">-${ytd:,.0f}</b></span>'
                    f'<span class="stat-pill">All Time <b style="color:#e07a5f">-${total_int:,.0f}</b></span>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── Portfolio Exposure (loads independently via fragment) ──
    @st.cache_data(ttl=86400, show_spinner=False)
    def _cached_ticker_profiles(tickers_tuple):
        return fetch_ticker_profiles(list(tickers_tuple))

    @st.fragment
    def _portfolio_exposure():
        st.markdown("")
        try:
            with st.spinner("Loading sector & country data..."):
                profiles = _cached_ticker_profiles(tuple(held_tickers))
            total_mv = sum(d["market_value"] for d in held.values())

            if total_mv > 0:
                sector_values = {}
                country_values = {}
                for ticker, data in held.items():
                    mv = data["market_value"]
                    profile = profiles.get(ticker, {})
                    sector = profile.get("sector", "Unknown")
                    country = profile.get("country", "Unknown")
                    sector_values[sector] = sector_values.get(sector, 0) + mv
                    country_values[country] = country_values.get(country, 0) + mv

                sector_sorted = sorted(sector_values.items(), key=lambda x: x[1], reverse=True)
                country_sorted = sorted(country_values.items(), key=lambda x: x[1], reverse=True)

                EXPOSURE_COLORS = [
                    '#81b29a', '#3d405b', '#e07a5f', '#f2cc8f', '#9b8ec4',
                    '#64b5f6', '#e57373', '#81c784', '#ffb74d', '#4dd0e1',
                    '#ba68c8', '#a1887f',
                ]

                def _donut_chart(labels, values):
                    fig = go.Figure(data=[go.Pie(
                        labels=labels,
                        values=values,
                        hole=0.55,
                        textinfo='label+percent',
                        textposition='outside',
                        marker=dict(colors=EXPOSURE_COLORS[:len(labels)]),
                        hovertemplate='%{label}<br>$%{value:,.0f}<br>%{percent}<extra></extra>',
                        pull=[0.02] * len(labels),
                    )])
                    fig.update_layout(
                        showlegend=True,
                        legend=dict(
                            orientation="h",
                            yanchor="top",
                            y=-0.02,
                            xanchor="center",
                            x=0.5,
                            font=dict(size=12, color="#1d1d1f"),
                        ),
                        margin=dict(t=40, b=20, l=20, r=20),
                        height=480,
                        font=dict(
                            family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                            color="#1d1d1f",
                        ),
                        paper_bgcolor='rgba(0,0,0,0)',
                        plot_bgcolor='rgba(0,0,0,0)',
                    )
                    return fig

                tab_sector, tab_country = st.tabs(["By Sector", "By Country"])

                with tab_sector:
                    labels = [s[0] for s in sector_sorted]
                    values = [s[1] for s in sector_sorted]
                    st.plotly_chart(_donut_chart(labels, values), use_container_width=True)

                with tab_country:
                    labels = [c[0] for c in country_sorted]
                    values = [c[1] for c in country_sorted]
                    st.plotly_chart(_donut_chart(labels, values), use_container_width=True)

        except Exception as e:
            st.warning(f"Could not load portfolio exposure: {e}")

    _portfolio_exposure()


# ══════════════════════════════════════════════════════
#  WHEEL COST BASIS PAGE — Detailed trade history
# ══════════════════════════════════════════════════════

elif page == "Wheel Cost Basis":

    st.markdown("")
    cost_basis = _load_portfolio_data()

    # ── Helper: parse strike + expiration from OCC symbol ──
    def _parse_option(symbol):
        """Extract strike, expiration, and type from OCC option symbol like MSFT  250321C00420000."""
        if not symbol:
            return None, None, None
        m = re.match(r'^(.+?)\s*(\d{6})([CP])(\d{8})$', symbol.strip())
        if not m:
            return None, None, None
        date_str, cp, strike_raw = m.group(2), m.group(3), m.group(4)
        strike = int(strike_raw) / 1000
        try:
            exp = datetime.strptime(date_str, "%y%m%d")
            return strike, exp.strftime("%d-%m-%Y"), cp
        except ValueError:
            return strike, None, cp

    def _is_put(t):
        """Check if trade is put via OCC symbol, fallback to description."""
        _, _, cp = _parse_option(t.get("symbol"))
        if cp:
            return cp == "P"
        return "Put" in (t.get("description") or "")

    def _is_call(t):
        """Check if trade is call via OCC symbol, fallback to description."""
        _, _, cp = _parse_option(t.get("symbol"))
        if cp:
            return cp == "C"
        return "Call" in (t.get("description") or "")

    # ── Helper: detect if a ticker has an active position ──
    def _is_active(data):
        """Active = open CSP, shares held, or open CC."""
        if data["shares_held"] > 0:
            return True
        # If the last wheel is completed or options_only, no active position
        wheels = data.get("wheels", [])
        if wheels and wheels[-1]["status"] in ("completed", "options_only"):
            return False
        # Count open/close for CSP and CC
        trades = data.get("trades", [])
        open_csp = 0
        open_cc = 0
        for t in trades:
            label = t.get("label", "")
            inst = t.get("instrument_type", "")
            if label == "CSP":
                open_csp += 1
            elif label in ("BTC CSP", "STC Put"):
                open_csp -= 1
            elif label == "Expired" and _is_put(t):
                open_csp -= 1
            elif label == "Assignment" and "Option" in inst:
                if _is_put(t):
                    open_csp -= 1
                elif _is_call(t):
                    open_cc -= 1
            elif label == "CC":
                open_cc += 1
            elif label in ("BTC CC", "STC Call"):
                open_cc -= 1
            elif label == "Expired" and _is_call(t):
                open_cc -= 1
        return open_csp > 0 or open_cc > 0

    # ── Helper: categorize trades ──
    def _categorize(trades):
        csp = [t for t in trades if t.get("label") in ("CSP", "BTC CSP") or (t.get("label") == "Expired" and "Put" in (t.get("description") or ""))]
        cc = [t for t in trades if t.get("label") in ("CC", "BTC CC") or (t.get("label") == "Expired" and "Call" in (t.get("description") or ""))]
        sh = [t for t in trades if t.get("instrument_type") == "Equity" or t.get("label") == "Dividend"]
        return csp, cc, sh

    # ── Helper: render trade rows ──
    def _render_trades(trades):
        if not trades:
            st.caption("No trades.")
            return
        html = ""
        for t in reversed(trades):
            qty_val = int(t["quantity"]) if t["quantity"] == int(t["quantity"]) else t["quantity"]
            price_str = f'{t["price"]:,.2f}' if t["price"] else "\u2014"
            net = t["net_value"]
            net_color = "#81b29a" if net >= 0 else "#e07a5f"
            trade_date = t["date"].strftime("%d-%m-%Y") if hasattr(t["date"], "strftime") else t["date"]

            # Friendly labels for equity trades
            label_raw = t["label"]
            if t.get("instrument_type") == "Equity":
                if label_raw == "Assignment":
                    label_raw = "Buy Shares" if t["net_value"] < 0 else "Sell Shares"
                elif label_raw == "Stock Buy":
                    label_raw = "Buy Shares"
                elif label_raw == "Stock Sell":
                    label_raw = "Sell Shares"

            # Option info: strike + expiration
            strike, exp, _cp = _parse_option(t.get("symbol"))
            if strike is not None:
                label_str = f'{label_raw} @ {strike:,.2f}'
                date_str = f'{trade_date} &nbsp; exp {exp}' if exp else trade_date
            else:
                label_str = label_raw
                date_str = trade_date

            html += (
                f'<div class="trade-row">'
                f'  <div class="tr-desc">'
                f'    <p class="tr-label">{label_str}</p>'
                f'    <p class="tr-date">{date_str}</p>'
                f'  </div>'
                f'  <div class="tr-cell">'
                f'    <p class="tr-val">{qty_val}</p>'
                f'    <p class="tr-lbl">Qty</p>'
                f'  </div>'
                f'  <div class="tr-cell">'
                f'    <p class="tr-val">{price_str}</p>'
                f'    <p class="tr-lbl">Fill</p>'
                f'  </div>'
                f'  <div class="tr-cell">'
                f'    <p class="tr-val" style="color:{net_color}">${abs(net):,.2f}</p>'
                f'    <p class="tr-lbl">P/L</p>'
                f'  </div>'
                f'</div>'
            )
        st.markdown(html, unsafe_allow_html=True)

    # ── Helper: render tabs per trade category ──
    def _render_tabs(trades, key_suffix):
        csp, cc, sh = _categorize(trades)
        tab_csp, tab_cc, tab_shares = st.tabs([
            f"CSP ({len(csp)})",
            f"CC ({len(cc)})",
            f"Shares ({len(sh)})",
        ])
        with tab_csp:
            _render_trades(csp)
        with tab_cc:
            _render_trades(cc)
        with tab_shares:
            _render_trades(sh)

    # ── Helper: render a ticker card ──
    def _render_ticker_card(ticker, data):
        pl = data["total_pl_real"]
        shares = data["shares_held"]
        wheels = data.get("wheels", [])
        cur_price = data["current_price"]
        prev_close = data.get("previous_close", cur_price)

        # Calculate buy price and adjusted cost from last wheel
        last_wheel = wheels[-1] if wheels else None
        buy_price = 0.0
        adj_cost = data["cost_per_share"]
        wheel_equity = 0.0
        wheel_option = 0.0
        w_shares = 0
        w_buy_total = 0.0
        if last_wheel:
            for t in last_wheel["trades"]:
                if t["instrument_type"] == "Equity":
                    wheel_equity += t["net_value"]
                    action = t.get("action", "")
                    txn_type = t.get("type", "")
                    qty = t["quantity"]
                    p = t["price"] if t["price"] else abs(t["net_value"]) / qty if qty else 0.0
                    if txn_type == "Receive Deliver" and t["net_value"] < 0:
                        w_shares += qty
                        w_buy_total += p * qty
                    elif "Buy" in action:
                        w_shares += qty
                        w_buy_total += p * qty
                elif "Option" in t["instrument_type"]:
                    wheel_option += t["net_value"]
            if w_shares > 0:
                buy_price = w_buy_total / w_shares
            if shares > 0:
                adj_cost = (wheel_equity + wheel_option) / shares

        # Day change
        day_chg = ((cur_price - prev_close) / prev_close * 100) if prev_close else 0.0
        day_color = "#81b29a" if day_chg >= 0 else "#e07a5f"

        with st.container(border=True):
            all_trades = data.get("trades", [])

            # Toggle: per wheel vs all transactions
            per_wheel = st.toggle("Per wheel", key=f"wheel_toggle_{ticker}") if all_trades else False

            # P/L: last wheel only when toggled, otherwise total
            if per_wheel and last_wheel:
                display_pl = last_wheel["pl"]
                if shares > 0:
                    display_pl += data["market_value"]
            else:
                display_pl = pl

            pl_badge = "pl-badge-green" if display_pl >= 0 else "pl-badge-red"
            pl_sign = "+$" if display_pl >= 0 else "-$"

            logo_url = f"https://assets.parqet.com/logos/symbol/{ticker}"
            st.markdown(
                f'<div class="card-header">'
                f'  <div class="card-left">'
                f'    <div class="tk-title">'
                f'      <img class="tk-logo" src="{logo_url}" onerror="this.style.display=\'none\'">'
                f'      <p class="tk-name">{ticker} @ {buy_price:,.2f}</p>'
                f'    </div>'
                f'    <p class="tk-sub">(Adjusted: {abs(adj_cost):,.2f})</p>'
                f'    <p class="tk-sub">Current Price</p>'
                f'    <p class="tk-sub" style="color:{day_color}; font-weight:500">'
                f'      {cur_price:,.2f} ({day_chg:+.2f}%)</p>'
                f'  </div>'
                f'  <div class="card-center">'
                f'    <p class="shares-count">{shares}</p>'
                f'    <p class="shares-label">shares held</p>'
                f'  </div>'
                f'  <div>'
                f'    <span class="pl-badge {pl_badge}">{pl_sign}{abs(display_pl):,.2f}</span>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            if not all_trades:
                return

            if per_wheel:
                for i, wheel in reversed(list(enumerate(wheels))):
                    status = wheel["status"]
                    w_pl = wheel["pl"]
                    w_pl_sign = "+$" if w_pl >= 0 else "-$"
                    w_start = wheel['start'].strftime("%d-%m-%Y") if hasattr(wheel['start'], 'strftime') else wheel['start']
                    w_end = wheel['end'].strftime("%d-%m-%Y") if hasattr(wheel['end'], 'strftime') else wheel['end']
                    if status == "completed":
                        label = f"Wheel {i + 1} \u2014 {w_start} \u2192 {w_end}"
                    elif status == "active":
                        label = f"Wheel {i + 1} (active) \u2014 {w_start} \u2192 now"
                    else:
                        label = f"CSP Income \u2014 {w_start} \u2192 {w_end}"
                    with st.expander(f"{label}  \u2014  {w_pl_sign}{abs(w_pl):,.2f}"):
                        _render_tabs(wheel["trades"], f"{ticker}_w{i}")
            else:
                n_total = len(all_trades)
                with st.expander(f"Transactions ({n_total})"):
                    _render_tabs(all_trades, f"{ticker}_all")

    # ── Two-column card layout ──
    st.markdown(
        "<style>.block-container { max-width: 1400px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Client-side live search (pure JS, no server roundtrip) ──
    st.markdown(
        '<input type="text" id="ticker-live-search" placeholder="Search ticker..." '
        'style="width:100%;padding:10px 14px;font-size:16px;border:1px solid #ddd;'
        'border-radius:8px;margin-bottom:12px;outline:none;box-sizing:border-box;'
        'background:#fafafa;" onfocus="this.style.borderColor=\'#4a90d9\'" '
        'onblur="this.style.borderColor=\'#ddd\'">',
        unsafe_allow_html=True,
    )

    # ── Split tickers into active / closed ──
    active_tickers = {t: d for t, d in cost_basis.items() if _is_active(d)}
    closed_tickers = {t: d for t, d in cost_basis.items() if not _is_active(d)}

    def _render_grid(tickers):
        items = list(tickers.items())
        for i in range(0, len(items), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j < len(items):
                    with col:
                        _render_ticker_card(items[i + j][0], items[i + j][1])

    st.markdown(f"### Active ({len(active_tickers)})")
    if active_tickers:
        _render_grid(active_tickers)
    else:
        st.caption("No active positions.")

    st.markdown(f"### Closed ({len(closed_tickers)})")
    if closed_tickers:
        _render_grid(closed_tickers)
    else:
        st.caption("No closed positions.")

    # ── JS: instant client-side card filtering ──
    components.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const input = doc.getElementById("ticker-live-search");
            if (!input || input.dataset.bound) return;
            input.dataset.bound = "1";
            input.addEventListener("input", function() {
                const q = this.value.toUpperCase();
                // Find all ticker name elements
                const names = doc.querySelectorAll(".tk-name");
                names.forEach(function(el) {
                    // Extract ticker from "TICKER @ 123.45"
                    const ticker = el.textContent.split(" ")[0].toUpperCase();
                    // Traverse up to the stColumn container
                    let col = el.closest('[data-testid="stColumn"]');
                    if (col) {
                        col.style.display = (!q || ticker.includes(q)) ? "" : "none";
                    }
                });
                // Hide empty rows (both columns hidden)
                doc.querySelectorAll('[data-testid="stHorizontalBlock"]').forEach(function(row) {
                    const cols = row.querySelectorAll('[data-testid="stColumn"]');
                    if (cols.length === 0) return;
                    const anyVisible = Array.from(cols).some(c => c.style.display !== "none");
                    row.style.display = anyVisible ? "" : "none";
                });
            });
        })();
        </script>
        """,
        height=0,
    )


# ══════════════════════════════════════════════════════
#  RESULTS PAGE — P/L performance overview
# ══════════════════════════════════════════════════════

elif page == "Results":

    st.markdown("")
    cost_basis = _load_portfolio_data()

    # ── Compute aggregates ──
    total_pl_real = sum(d["total_pl_real"] for d in cost_basis.values())
    total_option_pl = sum(d["option_pl"] for d in cost_basis.values())
    total_dividends = sum(d["dividends"] for d in cost_basis.values())
    active_positions = sum(1 for d in cost_basis.values() if d["shares_held"] > 0)

    realized_pl = sum(
        w["pl"] for d in cost_basis.values()
        for w in d.get("wheels", []) if w["status"] == "completed"
    )
    unrealized_pl = sum(
        d["market_value"] + d["equity_cost"]
        for d in cost_basis.values() if d["shares_held"] > 0
    )

    pl_color_class = "hero-green" if total_pl_real >= 0 else "hero-red"
    pl_sign = "+" if total_pl_real >= 0 else ""

    # ── Compute CAGR from net liq history (deposit-adjusted) ──
    cagr_pill = ""
    if "net_liq_all" not in st.session_state:
        try:
            with st.spinner("Loading full net liq history..."):
                st.session_state["net_liq_all"] = fetch_net_liq_history("all")
        except Exception:
            st.session_state["net_liq_all"] = None
    if "yearly_transfers" not in st.session_state:
        try:
            with st.spinner("Loading cash transfer history..."):
                st.session_state["yearly_transfers"] = fetch_yearly_transfers()
        except Exception:
            st.session_state["yearly_transfers"] = {}

    nl_all_early = st.session_state.get("net_liq_all")
    transfers_early = st.session_state.get("yearly_transfers", {})
    if nl_all_early:
        # Yearly Simple Dietz returns, then compound to CAGR
        df_cagr = pd.DataFrame(nl_all_early)
        df_cagr["time"] = pd.to_datetime(df_cagr["time"])
        df_cagr = df_cagr.sort_values("time")
        df_cagr["year"] = df_cagr["time"].dt.year
        yr_close = df_cagr.groupby("year")["close"].last()
        yr_list = sorted(yr_close.index)
        compound = 1.0
        for i in range(1, len(yr_list)):
            prev_yr, cur_yr = yr_list[i - 1], yr_list[i]
            start_v = yr_close[prev_yr]
            end_v = yr_close[cur_yr]
            yr_data = transfers_early.get(cur_yr, {})
            net_dep = yr_data["total"] if isinstance(yr_data, dict) and "total" in yr_data else 0.0
            denom = start_v + 0.5 * net_dep
            if denom > 0:
                compound *= (1 + (end_v - start_v - net_dep) / denom)
        days = (df_cagr["time"].iloc[-1] - df_cagr["time"].iloc[0]).days
        n_years = days / 365.25
        if n_years > 0:
            cagr = (compound ** (1 / n_years) - 1) * 100
            cagr_sign = "+" if cagr >= 0 else ""
            cagr_pill = f'<span class="stat-pill">CAGR <b>{cagr_sign}{cagr:.1f}%</b></span>'

    # ── Hero card ──
    portfolio_val_pill = ""
    total_dep_pill = ""
    ytd_pill = ""
    if nl_all_early:
        pv = df_cagr["close"].iloc[-1]
        portfolio_val_pill = f'<span class="stat-pill">Portfolio Value <b>${pv:,.0f}</b></span>'

        total_dep = sum(v["total"] for v in transfers_early.values()) if transfers_early else 0
        total_dep_pill = f'<span class="stat-pill">Total Deposited <b>${total_dep:,.0f}</b></span>'

        true_pl = pv - total_dep
        true_pl_sign = "+" if true_pl >= 0 else ""
        true_pl_class = "hero-green" if true_pl >= 0 else "hero-red"

        # YTD return from last close of previous year to current
        from datetime import datetime
        cur_year = datetime.now().year
        yr_close = df_cagr.groupby("year")["close"].last()
        if cur_year - 1 in yr_close.index:
            ytd_start = yr_close[cur_year - 1]
            ytd_end = pv
            yr_dep = transfers_early.get(cur_year, {})
            ytd_dep = yr_dep["total"] if isinstance(yr_dep, dict) and "total" in yr_dep else 0.0
            ytd_denom = ytd_start + 0.5 * ytd_dep
            if ytd_denom > 0:
                ytd_ret = (ytd_end - ytd_start - ytd_dep) / ytd_denom * 100
                ytd_sign = "+" if ytd_ret >= 0 else ""
                ytd_pill = f'<span class="stat-pill">YTD <b>{ytd_sign}{ytd_ret:.1f}%</b></span>'

    hero_pl = true_pl if nl_all_early else total_pl_real
    hero_pl_class = true_pl_class if nl_all_early else pl_color_class
    hero_pl_sign = true_pl_sign if nl_all_early else pl_sign

    st.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">Total P/L</p>'
        f'<p class="hero-value {hero_pl_class}">{hero_pl_sign}${abs(hero_pl):,.0f}</p>'
        f'<p class="hero-sub">{active_positions} active positions</p>'
        f'<div class="stat-row">'
        f'{portfolio_val_pill}'
        f'{total_dep_pill}'
        f'{cagr_pill}'
        f'{ytd_pill}'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Net Liq History chart ──
    period_map = {"1M": "1m", "3M": "3m", "6M": "6m", "1Y": "1y", "All": "all"}
    selected_period = st.pills(
        "Period", options=list(period_map.keys()), default="1Y",
    )
    time_back = period_map[selected_period]
    cache_key = f"net_liq_{time_back}"
    if cache_key not in st.session_state:
        try:
            with st.spinner("Loading net liq history..."):
                st.session_state[cache_key] = fetch_net_liq_history(time_back)
        except Exception:
            st.session_state[cache_key] = None

    net_liq_data = st.session_state[cache_key]
    if net_liq_data:
        df_liq = pd.DataFrame(net_liq_data)
        df_liq["time"] = pd.to_datetime(df_liq["time"])
        df_liq = df_liq.set_index("time")
        first_close = df_liq["close"].iloc[0]
        last_close = df_liq["close"].iloc[-1]
        pct_change = ((last_close - first_close) / first_close * 100) if first_close else 0
        pct_color = "#81b29a" if pct_change >= 0 else "#e07a5f"
        pct_sign = "+" if pct_change >= 0 else ""
        st.markdown(
            f'<span style="font-size:1.3rem;font-weight:700;color:{pct_color}">'
            f'{pct_sign}{pct_change:.2f}%</span> '
            f'<span style="color:#86868b;font-size:0.85rem">{selected_period}</span>',
            unsafe_allow_html=True,
        )
        fig_liq = go.Figure()
        fig_liq.add_trace(go.Scatter(
            x=df_liq.index,
            y=df_liq["close"],
            mode="lines",
            line=dict(color="#81b29a", width=2),
            fill="tozeroy",
            fillcolor="rgba(129,178,154,0.18)",
            hovertemplate="$%{y:,.0f}<extra></extra>",
        ))
        fig_liq.update_layout(
            margin=dict(t=10, b=20, l=40, r=20),
            height=300,
            font=dict(
                family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                color="#1d1d1f",
            ),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="#f0f0f2"),
            yaxis=dict(gridcolor="#f0f0f2"),
            showlegend=False,
        )
        st.plotly_chart(fig_liq, use_container_width=True)
    else:
        st.info("Net liquidation history unavailable.")

    st.markdown("")

    # ── Top / Bottom performers ──
    sorted_tickers = sorted(
        cost_basis.items(), key=lambda x: x[1]["total_pl_real"], reverse=True,
    )
    top5 = sorted_tickers[:5]
    bottom5 = sorted_tickers[-5:][::-1]

    def _performer_cards(items):
        cards = ''
        for ticker, data in items:
            logo = f"https://assets.parqet.com/logos/symbol/{ticker}"
            pl = data["total_pl_real"]
            pl_cls = " pf-green" if pl > 0 else " pf-red" if pl < 0 else ""
            opt = data["option_pl"]
            opt_cls = " pf-green" if opt > 0 else " pf-red" if opt < 0 else ""
            cards += (
                f'<div class="portfolio-card">'
                f'<img class="pf-logo" src="{logo}" onerror="this.style.display=\'none\'">'
                f'<span class="pf-ticker">{ticker}</span>'
                f'<div class="pf-cell"><span class="pf-label">Total P/L</span>'
                f'<span class="pf-val{pl_cls}">${pl:+,.0f}</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Options P/L</span>'
                f'<span class="pf-val{opt_cls}">${opt:+,.0f}</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Dividends</span>'
                f'<span class="pf-val">${data["dividends"]:,.0f}</span></div>'
                f'</div>'
            )
        return cards

    st.markdown(
        f'<div class="performer-block">'
        f'<div class="performer-grid">'
        f'<div>'
        f'<h4>Top Performers</h4>'
        f'<div class="portfolio-cards">{_performer_cards(top5)}</div>'
        f'</div>'
        f'<div>'
        f'<h4>Bottom Performers</h4>'
        f'<div class="portfolio-cards">{_performer_cards(bottom5)}</div>'
        f'</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.container():
        if "benchmark_returns" not in st.session_state:
            try:
                with st.spinner("Loading benchmark data..."):
                    st.session_state["benchmark_returns"] = fetch_benchmark_returns()
            except Exception:
                st.session_state["benchmark_returns"] = {}

        nl_all = st.session_state.get("net_liq_all")
        bench_returns = st.session_state["benchmark_returns"]
        transfers = st.session_state.get("yearly_transfers", {})

        if nl_all:
            df_nl = pd.DataFrame(nl_all)
            df_nl["time"] = pd.to_datetime(df_nl["time"])
            df_nl["year"] = df_nl["time"].dt.year
            # Last close per year
            year_close = df_nl.groupby("year")["close"].last()
            port_returns = {}
            years = sorted(year_close.index)
            for i in range(1, len(years)):
                prev_yr, cur_yr = years[i - 1], years[i]
                start_val = year_close[prev_yr]
                end_val = year_close[cur_yr]
                yr_data = transfers.get(cur_yr, {})
                net_dep = yr_data["total"] if isinstance(yr_data, dict) and "total" in yr_data else 0.0
                # Simple Dietz: adjust for deposits/withdrawals (assume mid-year)
                denominator = start_val + 0.5 * net_dep
                if denominator > 0:
                    port_returns[cur_yr] = (end_val - start_val - net_dep) / denominator * 100
                else:
                    port_returns[cur_yr] = 0.0

            # Collect all years across portfolio + all benchmarks
            all_years_set = set(port_returns.keys())
            for b_returns in bench_returns.values():
                all_years_set |= set(b_returns.keys())
            all_years = sorted(all_years_set)

            rows_yr = []
            for yr in all_years:
                if yr in port_returns:
                    row = {"year": str(yr), "portfolio": round(port_returns[yr], 1)}
                    for bench_name, b_returns in bench_returns.items():
                        row[bench_name] = round(b_returns.get(yr, 0), 1) if yr in b_returns else None
                    rows_yr.append(row)
            if rows_yr:
                # ── Cards ──
                cards_html = '<div class="portfolio-cards">'
                for row in reversed(rows_yr):
                    port_val = row["portfolio"]
                    port_cls = " pf-green" if port_val >= 0 else " pf-red"
                    cells = (
                        f'<div class="pf-cell">'
                        f'<span class="pf-label">Portfolio</span>'
                        f'<span class="pf-val{port_cls}">{port_val:+.1f}%</span>'
                        f'</div>'
                    )
                    for bench_name in bench_returns:
                        bval = row.get(bench_name)
                        if bval is not None:
                            b_cls = " pf-green" if bval >= port_val else " pf-red"
                            cells += (
                                f'<div class="pf-cell">'
                                f'<span class="pf-label">{bench_name}</span>'
                                f'<span class="pf-val{b_cls}">{bval:+.1f}%</span>'
                                f'</div>'
                            )
                        else:
                            cells += (
                                f'<div class="pf-cell">'
                                f'<span class="pf-label">{bench_name}</span>'
                                f'<span class="pf-val">\u2014</span>'
                                f'</div>'
                            )
                    cards_html += (
                        f'<div class="portfolio-card" style="justify-content:center;text-align:center">'
                        f'<span class="pf-ticker">{row["year"]}</span>'
                        f'{cells}'
                        f'</div>'
                    )
                cards_html += '</div>'

                # ── Cumulative line chart ──
                chart_years = [row["year"] for row in rows_yr]
                LINE_COLORS = ["#81b29a", "#86868b", "#e07a5f", "#f2cc8f"]

                def _cumulative(yearly_pcts):
                    """Compound yearly % returns into cumulative %."""
                    cum = []
                    factor = 1.0
                    for pct in yearly_pcts:
                        if pct is None:
                            cum.append(None)
                        else:
                            factor *= (1 + pct / 100)
                            cum.append(round((factor - 1) * 100, 1))
                    return cum

                cum_port = _cumulative([row["portfolio"] for row in rows_yr])

                fig_yr = go.Figure()
                fig_yr.add_trace(go.Scatter(
                    x=chart_years,
                    y=cum_port,
                    name="Portfolio",
                    mode="lines+markers",
                    line=dict(color=LINE_COLORS[0], width=3),
                    marker=dict(size=7),
                ))
                for idx, bench_name in enumerate(bench_returns):
                    cum_bench = _cumulative([row.get(bench_name, None) for row in rows_yr])
                    fig_yr.add_trace(go.Scatter(
                        x=chart_years,
                        y=cum_bench,
                        name=bench_name,
                        mode="lines+markers",
                        line=dict(color=LINE_COLORS[(idx + 1) % len(LINE_COLORS)], width=2),
                        marker=dict(size=5),
                    ))
                fig_yr.add_hline(y=0, line_dash="dot", line_color="#d2d2d7", line_width=1)
                fig_yr.update_layout(
                    hovermode="x unified",
                    yaxis_title="Cumulative Return %",
                    yaxis_ticksuffix="%",
                    xaxis=dict(
                        type="category",
                        gridcolor="#f0f0f2",
                    ),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=12, color="#1d1d1f"),
                    ),
                    margin=dict(t=40, b=20, l=40, r=20),
                    height=380,
                    font=dict(
                        family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                        color="#1d1d1f",
                    ),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    yaxis=dict(gridcolor="#f0f0f2", zerolinecolor="#d2d2d7"),
                )

                st.markdown(
                    '<div class="performer-block">'
                    '<h4>Cumulative Returns vs Benchmarks</h4>',
                    unsafe_allow_html=True,
                )
                col_chart, col_cards = st.columns([3, 1])
                with col_chart:
                    st.plotly_chart(fig_yr, use_container_width=True)
                with col_cards:
                    st.markdown(cards_html, unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.info("Not enough history for yearly returns.")
        else:
            st.info("Net liq history unavailable for yearly returns.")

    # ── Returns per year / month ──
    MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    if nl_all and transfers is not None:
        df_ret = pd.DataFrame(nl_all)
        df_ret["time"] = pd.to_datetime(df_ret["time"])
        df_ret["year"] = df_ret["time"].dt.year
        df_ret["month"] = df_ret["time"].dt.month

        # Monthly returns using Simple Dietz (adjusted for deposits)
        # Use last close of previous month as start value
        df_ret = df_ret.sort_values("time")
        month_last_close = df_ret.groupby(["year", "month"])["close"].last()
        month_periods = list(month_last_close.index)
        monthly_returns = {}
        for i in range(1, len(month_periods)):
            prev_yr, prev_mo = month_periods[i - 1]
            yr, mo = month_periods[i]
            start_val = month_last_close[(prev_yr, prev_mo)]
            end_val = month_last_close[(yr, mo)]
            yr_transfers = transfers.get(yr, {})
            mo_dep = yr_transfers.get("months", {}).get(mo, 0) if isinstance(yr_transfers, dict) else 0
            denom = start_val + 0.5 * mo_dep
            if denom > 0:
                ret = (end_val - start_val - mo_dep) / denom * 100
            else:
                ret = 0.0
            monthly_returns.setdefault(yr, {})[mo] = round(ret, 1)

        # Yearly returns from monthly compounding
        yearly_returns = {}
        for yr, months in monthly_returns.items():
            factor = 1.0
            for mo in sorted(months):
                factor *= (1 + months[mo] / 100)
            yearly_returns[yr] = round((factor - 1) * 100, 1)

        total_factor = 1.0
        for yr in sorted(yearly_returns):
            total_factor *= (1 + yearly_returns[yr] / 100)
        total_return = round((total_factor - 1) * 100, 1)
        total_ret_cls = " pf-green" if total_return >= 0 else " pf-red"

        # ── Returns & Deposits side by side ──
        has_deposits = bool(transfers)
        sorted_transfers = sorted(transfers.items(), reverse=True) if has_deposits else []
        total_deposited = sum(v["total"] for v in transfers.values()) if has_deposits else 0
        total_dep_cls = " pf-green" if total_deposited >= 0 else " pf-red"

        col_ret, col_dep = st.columns(2)

        with col_ret:
            returns_html = (
                f'<div class="performer-block">'
                f'<h4>Returns &nbsp;<span style="font-weight:400;font-size:0.85rem;color:#86868b">'
                f'Cumulative: <span class="pf-val{total_ret_cls}" style="font-size:0.85rem">{total_return:+.1f}%</span>'
                f'</span></h4>'
            )
            for yr in sorted(yearly_returns, reverse=True):
                yr_ret = yearly_returns[yr]
                yr_color = "#81b29a" if yr_ret >= 0 else "#e07a5f"
                mo_cards = '<div class="portfolio-cards">'
                for mo in range(1, 13):
                    mo_ret = monthly_returns.get(yr, {}).get(mo)
                    if mo_ret is None:
                        continue
                    mo_cls = " pf-green" if mo_ret >= 0 else " pf-red"
                    mo_cards += (
                        f'<div class="portfolio-card" style="justify-content:center;text-align:center">'
                        f'<span class="pf-ticker" style="min-width:40px">{MONTH_NAMES[mo]}</span>'
                        f'<div class="pf-cell">'
                        f'<span class="pf-val {mo_cls}">{mo_ret:+.1f}%</span>'
                        f'</div>'
                        f'</div>'
                    )
                mo_cards += '</div>'
                returns_html += (
                    f'<details style="border:1px solid #d2d2d7;border-radius:18px;padding:12px 16px;margin-bottom:8px">'
                    f'<summary style="cursor:pointer;font-weight:600;color:#1d1d1f;list-style:none">'
                    f'{yr} — <span style="color:{yr_color}">{yr_ret:+.1f}%</span></summary>'
                    f'{mo_cards}'
                    f'</details>'
                )
            returns_html += '</div>'
            st.markdown(returns_html, unsafe_allow_html=True)

        with col_dep:
            if has_deposits:
                dep_html = (
                    f'<div class="performer-block">'
                    f'<h4>Deposits &nbsp;<span style="font-weight:400;font-size:0.85rem;color:#86868b">'
                    f'Total: <span class="pf-val{total_dep_cls}" style="font-size:0.85rem">${total_deposited:+,.0f}</span>'
                    f'</span></h4>'
                )
                for yr, yr_data in sorted_transfers:
                    amount = yr_data["total"]
                    months = yr_data.get("months", {})
                    dep_color = "#81b29a" if amount >= 0 else "#e07a5f"
                    month_cards = '<div class="portfolio-cards">'
                    for mo in range(1, 13):
                        mo_val = months.get(mo)
                        if mo_val is None:
                            continue
                        mo_cls = " pf-green" if mo_val >= 0 else " pf-red"
                        month_cards += (
                            f'<div class="portfolio-card" style="justify-content:center;text-align:center">'
                            f'<span class="pf-ticker" style="min-width:40px">{MONTH_NAMES[mo]}</span>'
                            f'<div class="pf-cell">'
                            f'<span class="pf-val{mo_cls}">${mo_val:+,.0f}</span>'
                            f'</div>'
                            f'</div>'
                        )
                    month_cards += '</div>'
                    dep_html += (
                        f'<details style="border:1px solid #d2d2d7;border-radius:18px;padding:12px 16px;margin-bottom:8px">'
                        f'<summary style="cursor:pointer;font-weight:600;color:#1d1d1f;list-style:none">'
                        f'{yr} — <span style="color:{dep_color}">${amount:+,.0f}</span></summary>'
                        f'{month_cards}'
                        f'</details>'
                    )
                dep_html += '</div>'
                st.markdown(dep_html, unsafe_allow_html=True)

    st.markdown("")

    # ── Per-ticker cards (sorted by Total P/L, best first) ──
    def _fmt_result_cell(col, val):
        cls = ""
        if col in ("Options P/L", "Total P/L"):
            cls = " pf-green" if val > 0 else " pf-red" if val < 0 else ""
        if col in ("Options P/L", "Equity Cost", "Total P/L", "Dividends"):
            return f"${val:+,.0f}" if col in ("Options P/L", "Total P/L") else f"${val:,.0f}", cls
        if col == "Mkt Value":
            return f"${val:,.0f}", cls
        if col == "Wheels":
            return f"{val}", cls
        return f"{val}", cls

    result_cols = ["Wheels", "Options P/L", "Equity Cost", "Mkt Value", "Total P/L", "Dividends"]

    rows = []
    for ticker, data in sorted_tickers:
        wheels = data.get("wheels", [])
        completed = sum(1 for w in wheels if w["status"] == "completed")
        active = any(w["status"] == "active" for w in wheels)
        wheel_str = str(completed) + (" +1 active" if active else "")
        rows.append({
            "Logo": f"https://assets.parqet.com/logos/symbol/{ticker}",
            "Ticker": ticker,
            "Wheels": wheel_str,
            "Options P/L": data["option_pl"],
            "Equity Cost": data["equity_cost"],
            "Mkt Value": data["market_value"],
            "Total P/L": data["total_pl_real"],
            "Dividends": data["dividends"],
        })

    cards_html = '<div class="portfolio-cards">'
    for row in rows:
        cells = ""
        for col in result_cols:
            fval, cls = _fmt_result_cell(col, row[col])
            cells += (
                f'<div class="pf-cell">'
                f'<span class="pf-label">{col}</span>'
                f'<span class="pf-val{cls}">{fval}</span>'
                f'</div>'
            )
        cards_html += (
            f'<div class="portfolio-card">'
            f'<img class="pf-logo" src="{row["Logo"]}" onerror="this.style.display=\'none\'">'
            f'<span class="pf-ticker">{row["Ticker"]}</span>'
            f'{cells}'
            f'</div>'
        )
    cards_html += '</div>'

    st.markdown(cards_html, unsafe_allow_html=True)
