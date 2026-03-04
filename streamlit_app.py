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

from dcf_calculator import compute_wacc, compute_intrinsic_value, compute_reverse_dcf
from config_store import save_config, load_config, list_watchlist, remove_from_watchlist
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
    fetch_sector_s2c,
    fetch_consensus_estimates,
    find_peers,
    fetch_peer_data,
    build_config,
    SIC_TO_SECTOR,
    ERP_DEFAULT,
    TERMINAL_GROWTH_DEFAULT,
    MARGIN_OF_SAFETY_DEFAULT,
    fetch_fundamentals,
    fetch_historical_prices,
    fetch_balance_sheet,
    fetch_income_statement,
    fetch_cashflow_statement,
)
from tastytrade_api import fetch_portfolio_data, fetch_current_prices, fetch_account_balances, fetch_net_liq_history, fetch_sp500_yearly_returns, fetch_benchmark_returns, fetch_ticker_profiles, fetch_yearly_transfers, fetch_portfolio_greeks, fetch_margin_interest, fetch_margin_for_position, fetch_margin_requirements, fetch_beta_weighted_delta
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
    /* ── Refined with Edge ── */

    /* Global typography — DM Serif Display (headers) + DM Sans (body) */
    @import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Sans:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'DM Sans', -apple-system, BlinkMacSystemFont,
                     'Helvetica Neue', Arial, sans-serif;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }

    /* Subtle noise texture overlay */
    body::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 0;
        opacity: 0.03;
        background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='1'/%3E%3C/svg%3E");
        background-repeat: repeat;
        background-size: 256px 256px;
    }

    /* Page load animation */
    @keyframes fadeInUp {
        from { opacity: 0; transform: translateY(12px); }
        to { opacity: 1; transform: translateY(0); }
    }

    /* Custom scrollbar */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #c4c4c6; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #81b29a; }

    /* Focus states */
    *:focus-visible {
        outline: 2px solid #81b29a !important;
        outline-offset: 2px !important;
    }

    /* Main content area */
    .main .block-container {
        padding-top: 3rem;
    }

    /* Headings — Editorial serif */
    h1, h2, h3 {
        font-family: 'DM Serif Display', Georgia, 'Times New Roman', serif !important;
        color: #1d1d1f !important;
        font-weight: 400 !important;
        letter-spacing: -0.01em !important;
    }
    h2 { font-size: 2rem !important; }
    h3 { font-size: 1.4rem !important; }

    p, li, label, span {
        color: #1d1d1f;
    }

    /* Metric cards — with subtle depth */
    [data-testid="stMetric"] {
        background: #fff;
        border: none;
        border-radius: 18px;
        padding: 20px 24px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        animation: fadeInUp 0.4s ease-out both;
    }
    [data-testid="stMetric"]:nth-child(1) { animation-delay: 0s; }
    [data-testid="stMetric"]:nth-child(2) { animation-delay: 0.05s; }
    [data-testid="stMetric"]:nth-child(3) { animation-delay: 0.1s; }
    [data-testid="stMetric"]:nth-child(4) { animation-delay: 0.15s; }
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

    /* Hero card — editorial with green accent */
    .hero-card {
        background: #fff;
        border-radius: 24px;
        border-top: 3px solid #81b29a;
        padding: 48px 32px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        text-align: center;
        margin-bottom: 32px;
        animation: fadeInUp 0.4s ease-out both;
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
        font-family: 'DM Sans', -apple-system, sans-serif;
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

    /* Stat pills — frosted glass */
    .stat-row {
        display: flex;
        justify-content: center;
        gap: 16px;
        margin: 20px 0 0 0;
        flex-wrap: wrap;
    }
    .stat-pill {
        background: rgba(255,255,255,0.7);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border: 1px solid rgba(255,255,255,0.5);
        border-radius: 980px;
        padding: 8px 18px;
        font-size: 0.95rem;
        color: #86868b;
        font-weight: 400;
    }
    .stat-pill b {
        color: #1d1d1f;
        font-weight: 600;
    }

    /* Success banner (DCF page) */
    .success-banner {
        background: #fff;
        border: none;
        border-radius: 24px;
        padding: 40px 32px;
        margin: 24px 0;
        text-align: center;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        animation: fadeInUp 0.4s ease-out both;
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

    /* Expanders — with hover lift */
    [data-testid="stExpander"] {
        border: 1px solid #d2d2d7;
        border-radius: 18px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        overflow: hidden;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        animation: fadeInUp 0.4s ease-out both;
    }
    [data-testid="stExpander"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.06);
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
        transition: background-color 0.2s ease;
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

    /* Dividers — consistent subtle separators */
    hr {
        border-color: rgba(0,0,0,0.06) !important;
        opacity: 1;
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

    /* Cumulative Returns — white block, green accent */
    .st-key-cumulative_block {
        background: #fff;
        border-radius: 24px;
        border-top: 3px solid #81b29a;
        padding: 32px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .st-key-cumulative_block .performer-block {
        background: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
    }
    .st-key-cumulative_block .performer-block:hover {
        transform: none;
        box-shadow: none;
    }

    /* Results hero + chart — single continuous white block */
    .st-key-results_hero {
        background: #fff;
        border-radius: 24px;
        border-top: 3px solid #81b29a;
        padding: 32px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .st-key-results_hero .hero-card {
        background: none;
        border-top: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
        margin-bottom: 0;
        animation: none;
    }

    /* Portfolio Allocation — white block, green accent, no outer frame */
    .st-key-allocation_block {
        background: #fff;
        border-radius: 24px;
        border-top: 3px solid #81b29a;
        padding: 32px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }

    /* Greeks / BWD / Interest — CSS Grid, equal-height cards */
    .greeks-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 16px;
        margin-bottom: 24px;
    }
    .greeks-grid .hero-card {
        height: 100%;
        box-sizing: border-box;
        display: flex;
        flex-direction: column;
        justify-content: center;
        align-items: center;
    }

    /* Margin overview — single continuous white block */
    .st-key-margin_block {
        background: #fff;
        border-radius: 24px;
        border-top: 3px solid #81b29a;
        padding: 32px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }
    .st-key-margin_block .stTextInput > div > div > input,
    .st-key-margin_block .stNumberInput > div > div > input {
        background: #f5f4f0 !important;
        border: none !important;
        box-shadow: none !important;
    }
    .st-key-margin_block .hero-card {
        background: none;
        border-top: none;
        border-radius: 0;
        padding: 0;
        box-shadow: none;
        margin-bottom: 0;
        animation: none;
    }

    /* ── Ticker cards (Wheel Cost Basis) ── */
    [class*="st-key-wheel_card_"] {
        background: #fff;
        border-radius: 24px;
        border-top: 3px solid #81b29a;
        padding: 24px 32px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        margin-bottom: 16px;
    }
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
        border-bottom: 1px solid rgba(0,0,0,0.06);
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

    /* Section title bar */
    .section-title-bar {
        background: #fff;
        border-radius: 14px;
        padding: 12px 20px;
        margin-bottom: 10px;
        font-family: 'DM Serif Display', Georgia, serif;
        font-size: 1.1rem;
        font-weight: 400;
        color: #1d1d1f;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
    }

    /* ── Performer block — with hover lift ── */
    .performer-block {
        background: #fff;
        border-radius: 18px;
        padding: 24px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.04);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .performer-block:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.06);
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
        border-left: 3px solid #81b29a;
        border-radius: 14px;
        flex-wrap: wrap;
        width: 100%;
        box-sizing: border-box;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .portfolio-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0,0,0,0.06);
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
        background: #fafaf8;
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
        border-top-color: #81b29a;
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


def _watchlist_overview():
    st.markdown("## Watchlist")
    st.markdown(
        '<p style="color: #86868b; font-size: 1.05rem; line-height: 1.6; max-width: 560px;">'
        'Track intrinsic value vs market price for your watchlist. '
        'Click a ticker to edit the full DCF model.'
        '</p>',
        unsafe_allow_html=True,
    )

    # Red hover effect for delete buttons
    st.markdown("""<style>
    button[data-testid="stBaseButton-secondary"]:has(span[data-testid="stIconMaterial"]):hover {
        background: #fee2e2 !important;
        border-color: #ef4444 !important;
        color: #dc2626 !important;
    }
    </style>""", unsafe_allow_html=True)

    # ── Add ticker ──
    st.markdown("")
    wl_add_col1, wl_add_col2 = st.columns([3, 1], vertical_alignment="center")
    with wl_add_col1:
        wl_ticker = st.text_input(
            "Add ticker",
            placeholder="e.g. AAPL",
            label_visibility="collapsed",
            key="wl_ticker_input",
        )
    with wl_add_col2:
        wl_add = st.button("Add to Watchlist", use_container_width=True, type="primary")

    if wl_add and wl_ticker:
        ticker_clean = wl_ticker.strip().upper()
        try:
            _, wl_cfg, _ = run_analysis(
                ticker_clean,
                peer_mode="Auto-discover",
                manual_peers="",
                margin_of_safety=MARGIN_OF_SAFETY_DEFAULT,
                terminal_growth=TERMINAL_GROWTH_DEFAULT,
                n_peers=6,
            )
            save_config(ticker_clean, wl_cfg)
            st.success(f"{ticker_clean} added to watchlist")
            st.rerun()
        except Exception as e:
            st.error(f"Could not analyse {ticker_clean}: {e}")

    # ── Overview table ──
    watchlist = list_watchlist()
    if not watchlist:
        st.info("Your watchlist is empty. Add a ticker above or use 'Add to Watchlist' on the DCF page.")
        return

    @st.cache_data(ttl=30)
    def _fetch_prices_batch(tickers_tuple):
        prices = fetch_current_prices(list(tickers_tuple))
        return {t: (p["price"] if p else 0.0) for t, p in prices.items()}

    wl_tickers = [item['ticker'] for item in watchlist if load_config(item['ticker']) is not None]
    batch_prices = _fetch_prices_batch(tuple(wl_tickers)) if wl_tickers else {}

    rows = []
    for item in watchlist:
        t = item['ticker']
        cfg_wl = load_config(t)
        if cfg_wl is None:
            continue
        try:
            live_price = batch_prices.get(t, 0.0)
            if live_price > 0:
                cfg_wl['stock_price'] = live_price
            val = compute_intrinsic_value(cfg_wl)
            upside = (val['intrinsic_value'] / live_price - 1) if live_price > 0 else 0
            ni = cfg_wl.get('hist_net_income', [])
            sh = cfg_wl.get('shares_outstanding', 0)
            eps = ni[-1] / sh if ni and sh else 0
            pe = live_price / eps if eps > 0 else None
            # FCF Yield
            fcf_list = cfg_wl.get('hist_fcf', [])
            sh_out = cfg_wl.get('shares_outstanding', 0)
            if fcf_list and sh_out and live_price > 0:
                fcf_yield_val = (fcf_list[-1] * 1e6 / sh_out) / live_price
            else:
                fcf_yield_val = None
        except Exception:
            continue
        rows.append({
            'ticker': t,
            'company': cfg_wl.get('company', t),
            'price': live_price,
            'intrinsic': val['intrinsic_value'],
            'buy_price': val['buy_price'],
            'upside': upside,
            'pe': pe,
            'fcf_yield': fcf_yield_val,
        })

    rows.sort(key=lambda r: r['upside'], reverse=True)

    # Header
    hdr = st.columns([0.4, 1.4, 2.2, 1.0, 1.0, 1.0, 0.9, 0.8, 0.9, 0.3])
    _wl_hdr = ["", "Ticker", "Company", "Price", "Intrinsic", "Buy Price", "Upside", "P/E", "FCF Yield", ""]
    for col, label in zip(hdr, _wl_hdr):
        if label:
            col.markdown(f"**{label}**")

    # Rows — edit icon navigates to editor
    for row in rows:
        t = row['ticker']
        up_color = "green" if row['upside'] > 0 else "red"
        cols = st.columns([0.4, 1.4, 2.2, 1.0, 1.0, 1.0, 0.9, 0.8, 0.9, 0.3], vertical_alignment="center")
        with cols[0]:
            if st.button("", key=f"wl_edit_{t}", icon=":material/edit:"):
                st.query_params["edit"] = t
                st.rerun()
        logo_url = f"https://assets.parqet.com/logos/symbol/{t}"
        cols[1].markdown(
            f'<img src="{logo_url}" style="width:24px;height:24px;border-radius:50%;object-fit:cover;vertical-align:middle;margin-right:6px" onerror="this.style.display=\'none\'"><strong>{t}</strong>',
            unsafe_allow_html=True,
        )
        cols[2].markdown(row['company'])
        cols[3].markdown(f"${row['price']:.2f}")
        cols[4].markdown(f"${row['intrinsic']:.2f}")
        cols[5].markdown(f"${row['buy_price']:.2f}")
        cols[6].markdown(f":{up_color}[{row['upside']:+.1%}]")
        cols[7].markdown(f"{row['pe']:.1f}x" if row['pe'] else "—")
        cols[8].markdown(f"{row['fcf_yield']:.1%}" if row['fcf_yield'] else "—")
        with cols[9]:
            if st.button("", key=f"wl_rm_row_{t}", icon=":material/close:"):
                remove_from_watchlist(t)
                st.rerun()

    st.markdown("")


def _dcf_editor(ticker):
    """Full DCF editor page for a single ticker."""
    cfg = load_config(ticker)
    if cfg is None:
        st.error(f"No config found for {ticker}")
        if st.button("\u2190 Watchlist", key="editor_back_err"):
            del st.query_params["edit"]
            st.rerun()
        return

    # ── Back button ──
    if st.button("\u2190 Watchlist", key="editor_back"):
        del st.query_params["edit"]
        st.rerun()

    # ── Live price ──
    @st.cache_data(ttl=30)
    def _price(t):
        try:
            p, _, _ = fetch_stock_price(t)
            return p
        except Exception:
            return 0.0

    live_price = _price(ticker)
    if live_price > 0:
        cfg['stock_price'] = live_price

    # ── Valuation summary (hero card) ──
    val = compute_intrinsic_value(cfg)
    upside = (val['intrinsic_value'] / live_price - 1) if live_price > 0 else 0
    up_color = "#81b29a" if upside >= 0 else "#e07a5f"
    up_sign = "+" if upside >= 0 else ""

    st.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">{cfg.get("company", ticker)}</p>'
        f'<div style="display:flex;align-items:center;justify-content:center;gap:12px">'
        f'<img src="https://assets.parqet.com/logos/symbol/{ticker}" '
        f'style="width:36px;height:36px;border-radius:50%;object-fit:cover" '
        f'onerror="this.style.display=\'none\'">'
        f'<p class="hero-value" style="font-size:2rem;margin:0">{ticker}</p>'
        f'</div>'
        f'<div class="stat-row">'
        f'<span class="stat-pill">Price <b>${live_price:.2f}</b></span>'
        f'<span class="stat-pill">Intrinsic Value <b>${val["intrinsic_value"]:.2f}</b></span>'
        f'<span class="stat-pill">Buy Price <b>${val["buy_price"]:.2f}</b></span>'
        f'<span class="stat-pill">Upside <b style="color:{up_color}">{up_sign}{upside:.1%}</b></span>'
        f'<span class="stat-pill">WACC <b>{val["wacc"]:.1%}</b></span>'
        f'<span class="stat-pill">EV <b>${val["enterprise_value"]:,.0f}M</b></span>'
        f'<span class="stat-pill">Equity Value <b>${val["equity_value"]:,.0f}M</b></span>'
        f'<span class="stat-pill">TV % of EV <b>{val["tv_pct"]:.0%}</b></span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Projections data (needed inside and after expander)
    base_year = cfg.get('base_year', 2025)
    growth = list(cfg.get('revenue_growth', []))
    margins = list(cfg.get('op_margins', []))

    # ── Tabs: DCF / Reverse DCF / Peer Comparison ──
    st.markdown("---")
    _tab_dcf, _tab_rdcf, _tab_peers, _tab_fundamentals, _tab_key_ratios = st.tabs(["DCF", "Reverse DCF", "Peer Comparison", "Fundamentals", "Key Ratios"])

    with _tab_dcf:
        st.markdown("#### Discounting Cash Flows")

        # ── WACC Inputs (collapsible) ──
        _ww_val = '<div style="display:flex;justify-content:space-between;padding:6px 0"><span style="{extra}">{label}</span><span style="{extra}">{value}</span></div>'
        _ww_sep = '<div style="border-top:1px solid rgba(128,128,128,0.25);margin:2px 0"></div>'

        with st.expander("### WACC", expanded=False):
          with st.container(border=True):
            cfg['risk_free_rate'] = st.number_input(
                "Risk-Free Rate %", value=cfg.get('risk_free_rate', 0.04) * 100,
                step=0.1, format="%.2f", key="ed_rfr",
            ) / 100
            cfg['erp'] = st.number_input(
                "Equity Risk Premium %", value=cfg.get('erp', 0.055) * 100,
                step=0.1, format="%.2f", key="ed_erp",
            ) / 100
            cfg['credit_spread'] = st.number_input(
                "Credit Spread %", value=cfg.get('credit_spread', 0.01) * 100,
                step=0.1, format="%.2f", key="ed_cs",
            ) / 100
            cfg['tax_rate'] = st.number_input(
                "Tax Rate %", value=cfg.get('tax_rate', 0.21) * 100,
                step=0.5, format="%.1f", key="ed_tax",
            ) / 100

            st.markdown(_ww_sep, unsafe_allow_html=True)

            cfg['equity_market_value'] = int(st.number_input(
                "Equity Market Value ($M)", value=int(cfg.get('equity_market_value', 0)),
                step=1000, key="ed_eq_val",
            ))
            cfg['debt_market_value'] = int(st.number_input(
                "Debt Market Value ($M)", value=int(cfg.get('debt_market_value', 0)),
                step=100, key="ed_debt_val",
            ))

            _eq_val = cfg['equity_market_value']
            _debt_val = cfg['debt_market_value']
            _total_cap = _eq_val + _debt_val
            _eq_wt = _eq_val / _total_cap if _total_cap > 0 else 0
            _debt_wt = _debt_val / _total_cap if _total_cap > 0 else 0
            st.markdown(_ww_val.format(label="Equity Weight", value=f"{_eq_wt:.1%}", extra="color:#86868b;"), unsafe_allow_html=True)
            st.markdown(_ww_val.format(label="Debt Weight", value=f"{_debt_wt:.1%}", extra="color:#86868b;"), unsafe_allow_html=True)

            st.markdown(_ww_sep, unsafe_allow_html=True)

            # Sector betas
            @st.cache_data(ttl=3600, show_spinner=False)
            def _damodaran_betas():
                return fetch_sector_betas()

            dam_betas = _damodaran_betas()
            sector_list = sorted(dam_betas.keys()) if dam_betas else []

            betas = list(cfg.get('sector_betas', []))
            # Auto-detect sector from SIC code if no betas configured yet
            if not betas:
                _sic = cfg.get('sic_code', 0)
                if _sic and _sic in SIC_TO_SECTOR:
                    _auto_name, _auto_beta = SIC_TO_SECTOR[_sic]
                    betas = [(_auto_name, _auto_beta, 1.0)]
                elif dam_betas:
                    _sic_desc = cfg.get('sic_description', '')
                    if _sic_desc:
                        _sic_words = set(_sic_desc.lower().split())
                        _best, _best_score = None, 0
                        for _s, _b in dam_betas.items():
                            _overlap = len(_sic_words & set(_s.lower().split()))
                            if _overlap > _best_score:
                                _best_score = _overlap
                                _best = (_s, _b)
                        if _best and _best_score > 0:
                            betas = [(_best[0], _best[1], 1.0)]
                    if not betas:
                        betas = [("Market", dam_betas.get("Market", 1.0), 1.0)]
                else:
                    betas = [("Market", 1.0, 1.0)]
            st.markdown("**Sector Betas**")
            updated_betas = []
            for i, (name, beta, weight) in enumerate(betas):
                bc1, bc2, bc3, bc4 = st.columns([3, 2, 2, 0.5])
                with bc1:
                    if sector_list:
                        if name and name not in sector_list:
                            sector_list = [name] + sector_list
                        idx = sector_list.index(name) if name in sector_list else 0
                        new_name = st.selectbox(
                            "Sector", sector_list, index=idx, key=f"ed_bn_{i}",
                        )
                        new_beta = dam_betas.get(new_name, float(beta))
                    else:
                        new_name = st.text_input("Sector", value=name, key=f"ed_bn_{i}")
                        new_beta = float(beta)
                with bc2:
                    new_beta = st.number_input(
                        "Unlevered Beta", value=float(new_beta), step=0.01,
                        format="%.2f", key=f"ed_bb_{i}",
                    )
                with bc3:
                    new_weight = st.number_input(
                        "Revenue Weight", value=float(weight), step=0.05,
                        format="%.2f", key=f"ed_bw_{i}",
                    )
                with bc4:
                    st.markdown("<div style='height:32px'></div>", unsafe_allow_html=True)
                    if st.button("\u2212", key=f"ed_bdel_{i}"):
                        continue
                updated_betas.append((new_name, new_beta, new_weight))
            if st.button("+ Add sector", key="ed_badd"):
                default_name = sector_list[0] if sector_list else "Market"
                default_beta = dam_betas.get(default_name, 1.0) if dam_betas else 1.0
                updated_betas.append((default_name, default_beta, 1.0))
            cfg['sector_betas'] = updated_betas

            _wu_beta = sum(ub * wt for _, ub, wt in cfg['sector_betas']) if cfg['sector_betas'] else 1.0
            _de_ratio = _debt_val / _eq_val if _eq_val > 0 else 0
            _lev_beta = _wu_beta * (1 + (1 - cfg['tax_rate']) * _de_ratio)
            st.markdown(_ww_val.format(label="Weighted Unlevered \u03b2", value=f"{_wu_beta:.2f}", extra="color:#86868b;"), unsafe_allow_html=True)
            st.markdown(_ww_val.format(label="Levered \u03b2", value=f"{_lev_beta:.2f}", extra="font-weight:700;"), unsafe_allow_html=True)

            st.markdown(_ww_sep, unsafe_allow_html=True)

            _ke = cfg['risk_free_rate'] + _lev_beta * cfg['erp']
            _kd = (cfg['risk_free_rate'] + cfg['credit_spread']) * (1 - cfg['tax_rate'])
            st.markdown(_ww_val.format(label="Cost of Equity", value=f"{_ke:.2%}", extra="font-weight:700;"), unsafe_allow_html=True)
            st.markdown(_ww_val.format(label="Cost of Debt (after-tax)", value=f"{_kd:.2%}", extra="font-weight:700;"), unsafe_allow_html=True)

            st.markdown(_ww_sep, unsafe_allow_html=True)

            if _total_cap > 0:
                _wacc_computed = _eq_wt * _ke + _debt_wt * _kd
                st.markdown(_ww_val.format(label="WACC", value=f"{_wacc_computed:.2%}",
                                           extra="font-weight:700;font-size:1.15rem;color:#81b29a;"), unsafe_allow_html=True)
            else:
                st.warning("Equity + Debt market value must be > 0 to compute WACC")

        _s2c_val = '<div style="display:flex;justify-content:space-between;padding:6px 0"><span style="{extra}">{label}</span><span style="{extra}">{value}</span></div>'
        _s2c_sep = '<div style="border-top:1px solid rgba(128,128,128,0.25);margin:2px 0"></div>'

        with st.expander("### Sales-to-Capital", expanded=False):
          with st.container(border=True):
            _s2c_years = cfg.get('ic_years', [])
            _s2c_rev = cfg.get('hist_revenue', [])
            _s2c_ca = cfg.get('current_assets', [])
            _s2c_cash = cfg.get('cash', [])
            _s2c_si = cfg.get('st_investments', [])
            _s2c_cl = cfg.get('current_liabilities', [])
            _s2c_sd = cfg.get('st_debt', [])
            _s2c_sl = cfg.get('st_leases', [])
            _s2c_ppe = cfg.get('net_ppe', [])
            _s2c_gi = cfg.get('goodwill_intang', [])
            _s2c_n = len(_s2c_years)

            if _s2c_n >= 2 and len(_s2c_rev) >= _s2c_n:
                _s2c_ratios = []
                for _si in range(1, _s2c_n):
                    _rev_chg = _s2c_rev[_si] - _s2c_rev[_si - 1]
                    _ncwc_now = (_s2c_ca[_si] - _s2c_cash[_si] - _s2c_si[_si]) - (_s2c_cl[_si] - _s2c_sd[_si] - _s2c_sl[_si])
                    _ncwc_prev = (_s2c_ca[_si-1] - _s2c_cash[_si-1] - _s2c_si[_si-1]) - (_s2c_cl[_si-1] - _s2c_sd[_si-1] - _s2c_sl[_si-1])
                    _delta_ncwc = _ncwc_now - _ncwc_prev
                    _delta_ppe = _s2c_ppe[_si] - _s2c_ppe[_si - 1]
                    _delta_gi = _s2c_gi[_si] - _s2c_gi[_si - 1]
                    _ic_chg = _delta_ncwc + _delta_ppe + _delta_gi

                    _yr_label = f"{_s2c_years[_si-1]}\u2192{_s2c_years[_si]}"
                    st.markdown(_s2c_val.format(label=f"**{_yr_label}**", value="", extra="font-weight:700;"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Revenue", value=f"${_rev_chg:,.0f}", extra="color:#86868b;"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Non-cash WC", value=f"${_delta_ncwc:,.0f}", extra="color:#86868b;"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Net PP&E", value=f"${_delta_ppe:,.0f}", extra="color:#86868b;"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Goodwill & Intang.", value=f"${_delta_gi:,.0f}", extra="color:#86868b;"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="\u2003\u0394 Invested Capital", value=f"${_ic_chg:,.0f}", extra="font-weight:700;"), unsafe_allow_html=True)
                    if _ic_chg > 0 and _rev_chg != 0:
                        _yr_s2c = _rev_chg / _ic_chg
                        _s2c_ratios.append(_yr_s2c)
                        st.markdown(_s2c_val.format(label="\u2003Sales-to-Capital", value=f"{_yr_s2c:.2f}", extra="font-weight:700;"), unsafe_allow_html=True)
                    else:
                        st.markdown(_s2c_val.format(label="\u2003Sales-to-Capital", value="n/a", extra="color:#86868b;"), unsafe_allow_html=True)
                    st.markdown(_s2c_sep, unsafe_allow_html=True)

                if _s2c_ratios:
                    _s2c_ratios.sort()
                    _s2c_median = _s2c_ratios[len(_s2c_ratios) // 2]
                    st.markdown(_s2c_val.format(label="Median Sales-to-Capital", value=f"{_s2c_median:.2f}",
                                               extra="font-weight:700;font-size:1.15rem;color:#81b29a;"), unsafe_allow_html=True)
                    st.markdown(_s2c_val.format(label="Used in DCF", value=f"{cfg.get('sales_to_capital', 1.0):.2f}",
                                               extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
            else:
                st.info("Not enough historical data to compute Sales-to-Capital breakdown")

            # Sector reference from Damodaran
            st.markdown(_s2c_sep, unsafe_allow_html=True)
            @st.cache_data(ttl=3600, show_spinner=False)
            def _damodaran_s2c():
                return fetch_sector_s2c()

            _dam_s2c = _damodaran_s2c()
            if _dam_s2c:
                _sector_names = [name for name, _, _ in cfg.get('sector_betas', [])]
                _matched = []
                for _sn in _sector_names:
                    # Exact match first
                    if _sn in _dam_s2c:
                        _matched.append((_sn, _dam_s2c[_sn]))
                    else:
                        # Fuzzy: match on first word(s) before parentheses or common prefix
                        _sn_base = _sn.split("(")[0].strip().lower()
                        _sn_words = set(_sn.lower().split())
                        _best_name, _best_score = None, 0
                        for _ds in _dam_s2c:
                            _ds_base = _ds.split("(")[0].strip().lower()
                            _ds_words = set(_ds.lower().split())
                            _overlap = len(_sn_words & _ds_words)
                            if _sn_base == _ds_base:
                                _overlap += 5  # strong boost for matching base name
                            if _overlap > _best_score:
                                _best_score = _overlap
                                _best_name = _ds
                        if _best_name and _best_score >= 1:
                            _matched.append((_best_name, _dam_s2c[_best_name]))
                st.markdown("**Sector Reference (Damodaran)**")
                if _matched:
                    for _sn, _sv in _matched:
                        st.markdown(_s2c_val.format(label=f"\u2003{_sn}", value=f"{_sv:.2f}",
                                                   extra="color:#86868b;"), unsafe_allow_html=True)
                else:
                    st.markdown('<p style="color:#86868b;font-size:0.85rem">No matching sector found</p>', unsafe_allow_html=True)

        st.markdown('<p style="color:#86868b;font-size:0.85rem">In millions</p>', unsafe_allow_html=True)

        _n = len(growth)
        _base_rev = cfg.get('base_revenue', 0)
        _base_oi = cfg.get('base_oi', 0)
        _tg = cfg.get('terminal_growth', 0.03)
        _tm = cfg.get('terminal_margin', margins[-1] if margins else 0.30)

        # Expand single-value assumptions to per-year lists
        _default_wacc = compute_wacc(cfg) if cfg.get('equity_market_value', 0) + cfg.get('debt_market_value', 0) > 0 else 0.08
        _wacc_list = list(cfg.get('wacc_per_year', [_default_wacc] * _n))
        if len(_wacc_list) < _n:
            _wacc_list.extend([_wacc_list[-1] if _wacc_list else _default_wacc] * (_n - len(_wacc_list)))
        _default_tax = cfg.get('tax_rate', 0.21)
        _tax_list = list(cfg.get('tax_per_year', [_default_tax] * _n))
        if len(_tax_list) < _n:
            _tax_list.extend([_tax_list[-1] if _tax_list else _default_tax] * (_n - len(_tax_list)))
        _default_stc = cfg.get('sales_to_capital', 1.0)
        _stc_list = list(cfg.get('stc_per_year', [_default_stc] * _n))
        if len(_stc_list) < _n:
            _stc_list.extend([_stc_list[-1] if _stc_list else _default_stc] * (_n - len(_stc_list)))
        _default_sbc = cfg.get('sbc_pct', 0.004)
        _sbc_list = list(cfg.get('sbc_per_year', [_default_sbc] * _n))
        if len(_sbc_list) < _n:
            _sbc_list.extend([_sbc_list[-1] if _sbc_list else _default_sbc] * (_n - len(_sbc_list)))

        # Terminal column editable values (defaults from config or last year)
        _tv_tax_default = cfg.get('terminal_tax', _tax_list[-1] if _tax_list else _default_tax)
        _tv_stc_default = cfg.get('terminal_stc', _stc_list[-1] if _stc_list else _default_stc)
        _tv_sbc_default = cfg.get('terminal_sbc', _sbc_list[-1] if _sbc_list else _default_sbc)
        # Pre-read terminal WACC from session state (widget rendered after TV calc)
        _tv_wacc_default = cfg.get('terminal_wacc', _wacc_list[-1] if _wacc_list else _default_wacc)
        _tv_wacc = st.session_state.get("ed_w_tv", _tv_wacc_default * 100) / 100

        # Column layout: label + base year + 10 projection years + terminal
        _cw = [1.8] + [1] * (_n + 2)
        _tv_col = _n + 2  # terminal column index
        _cs = 'font-size:0.78rem;padding:2px 0;min-height:28px;display:flex;align-items:center;justify-content:right'
        _cs_bold = _cs + ';font-weight:700'
        _cs_label = 'font-size:0.78rem;padding:2px 0;min-height:28px;display:flex;align-items:center'
        _cs_label_bold = _cs_label + ';font-weight:700'
        _cs_sep = 'border-top:2px solid #d2d2d7;' + _cs
        _cs_hdr = 'font-size:0.78rem;padding:4px 0;min-height:32px;display:flex;align-items:center;justify-content:right;font-weight:700;border-bottom:2px solid #d2d2d7'
        _cs_hdr_label = 'font-size:0.78rem;padding:4px 0;min-height:32px;display:flex;align-items:center;font-weight:700;border-bottom:2px solid #d2d2d7'
        _tv_bg = 'background:rgba(0,0,0,0.03);border-radius:4px;padding-left:4px;padding-right:4px'

        def _dcf_row_label(cols, label, bold=False):
            with cols[0]:
                st.markdown(f"<div style='{_cs_label_bold if bold else _cs_label}'>{label}</div>", unsafe_allow_html=True)

        def _dcf_row_val(cols, idx, text, bold=False, sep=False, tv=False):
            style = _cs_sep if sep else (_cs_bold if bold else _cs)
            if tv or idx == _tv_col:
                style += f';{_tv_bg}'
            with cols[idx]:
                st.markdown(f"<div style='{style}'>{text}</div>", unsafe_allow_html=True)

        def _dcf_row_input(cols, idx, key, value, step, fmt, is_pct=True):
            with cols[idx]:
                v = st.number_input(key, value=value * 100 if is_pct else value,
                                    step=step, format=fmt, key=key, label_visibility="collapsed")
                return v / 100 if is_pct else v

        def _dcf_divider():
            st.markdown("<div style='border-top:1px solid #e5e5ea;margin:2px 0'></div>", unsafe_allow_html=True)

        # ── Year header row ──
        with st.container(border=True):
            hdr = st.columns(_cw)
            with hdr[0]:
                st.markdown(f"<div style='{_cs_hdr_label}'></div>", unsafe_allow_html=True)
            with hdr[1]:
                st.markdown(f"<div style='{_cs_hdr}'>{base_year}</div>", unsafe_allow_html=True)
            for i in range(_n):
                with hdr[i + 2]:
                    st.markdown(f"<div style='{_cs_hdr}'>{base_year + i + 1}</div>", unsafe_allow_html=True)
            with hdr[_tv_col]:
                st.markdown(f"<div style='{_cs_hdr};{_tv_bg}'>Terminal</div>", unsafe_allow_html=True)

            # ── Period row ──
            pr = st.columns(_cw)
            _dcf_row_label(pr, "Period")
            _dcf_row_val(pr, 1, "0")
            for i in range(_n):
                _dcf_row_val(pr, i + 2, f"{0.5 + i:.1f}")
            _dcf_row_val(pr, _tv_col, "")

            # ── Revenue Growth (editable) ──
            gr = st.columns(_cw)
            _dcf_row_label(gr, "Revenue Growth", bold=True)
            _dcf_row_val(gr, 1, "")
            for i in range(_n):
                growth[i] = _dcf_row_input(gr, i + 2, f"ed_g_{i}", growth[i], 0.5, "%.2f")
            _tg = _dcf_row_input(gr, _tv_col, "ed_tg_tv", _tg, 0.5, "%.2f")

            _revs = [_base_rev]
            for g in growth:
                _revs.append(_revs[-1] * (1 + g))

            # ── Revenue (computed) ──
            rv = st.columns(_cw)
            _dcf_row_label(rv, "Revenue")
            _dcf_row_val(rv, 1, f"{_base_rev:,.0f}")
            for i in range(_n):
                _dcf_row_val(rv, i + 2, f"{_revs[i + 1]:,.0f}")
            _tv_rev = _revs[-1] * (1 + _tg)
            _dcf_row_val(rv, _tv_col, f"{_tv_rev:,.0f}")

            # ── Operating Margin (editable) ──
            mr = st.columns(_cw)
            _dcf_row_label(mr, "Operating Margin", bold=True)
            _base_margin = cfg.get('base_op_margin', 0)
            _dcf_row_val(mr, 1, f"{_base_margin:.2%}")
            for i in range(_n):
                margins[i] = _dcf_row_input(mr, i + 2, f"ed_m_{i}", margins[i], 0.5, "%.2f")
            _tm = _dcf_row_input(mr, _tv_col, "ed_tm_tv", _tm, 0.5, "%.2f")

            # ── Operating Income (computed) ──
            oi_row = st.columns(_cw)
            _dcf_row_label(oi_row, "Operating Income")
            _dcf_row_val(oi_row, 1, f"{_base_oi:,.0f}")
            _oi_vals = [_revs[i + 1] * margins[i] for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(oi_row, i + 2, f"{_oi_vals[i]:,.0f}")
            _tv_oi = _tv_rev * _tm
            _dcf_row_val(oi_row, _tv_col, f"{_tv_oi:,.0f}")

            _dcf_divider()  # ── Revenue → NOPAT ──

            # ── Tax Rate (editable) ──
            tr = st.columns(_cw)
            _dcf_row_label(tr, "Tax Rate", bold=True)
            _dcf_row_val(tr, 1, f"{_default_tax:.2%}")
            for i in range(_n):
                _tax_list[i] = _dcf_row_input(tr, i + 2, f"ed_t_{i}", _tax_list[i], 0.5, "%.2f")
            _tv_tax = _dcf_row_input(tr, _tv_col, "ed_t_tv", _tv_tax_default, 0.5, "%.2f")

            # ── NOPAT (computed) ──
            np_row = st.columns(_cw)
            _dcf_row_label(np_row, "NOPAT")
            _base_nopat = _base_oi * (1 - _default_tax)
            _dcf_row_val(np_row, 1, f"{_base_nopat:,.0f}")
            _nopat_vals = [_oi_vals[i] * (1 - _tax_list[i]) for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(np_row, i + 2, f"{_nopat_vals[i]:,.0f}")
            _tv_nopat = _tv_oi * (1 - _tv_tax)
            _dcf_row_val(np_row, _tv_col, f"{_tv_nopat:,.0f}")

            _dcf_divider()  # ── NOPAT → Reinvestment ──

            # ── Sales-to-Capital (editable) ──
            sc_row = st.columns(_cw)
            _dcf_row_label(sc_row, "Sales-to-Capital", bold=True)
            _dcf_row_val(sc_row, 1, "")
            for i in range(_n):
                _stc_list[i] = _dcf_row_input(sc_row, i + 2, f"ed_s_{i}", _stc_list[i], 0.05, "%.2f", is_pct=False)
            _tv_stc = _dcf_row_input(sc_row, _tv_col, "ed_s_tv", _tv_stc_default, 0.05, "%.2f", is_pct=False)

            # ── Reinvestment (computed) ──
            ri_row = st.columns(_cw)
            _dcf_row_label(ri_row, "Reinvestment")
            _dcf_row_val(ri_row, 1, "")
            _reinvest_vals = [(_revs[i + 1] - _revs[i]) / _stc_list[i] if _stc_list[i] else 0 for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(ri_row, i + 2, f"{_reinvest_vals[i]:,.0f}")
            _tv_reinvest = (_tv_rev - _revs[-1]) / _tv_stc if _tv_stc else 0
            _dcf_row_val(ri_row, _tv_col, f"{_tv_reinvest:,.0f}")

            # ── SBC % (editable) ──
            sbc_row = st.columns(_cw)
            _dcf_row_label(sbc_row, "SBC % of Revenue", bold=True)
            _dcf_row_val(sbc_row, 1, "")
            for i in range(_n):
                _sbc_list[i] = _dcf_row_input(sbc_row, i + 2, f"ed_sbc_{i}", _sbc_list[i], 0.1, "%.2f")
            _tv_sbc_pct = _dcf_row_input(sbc_row, _tv_col, "ed_sbc_tv", _tv_sbc_default, 0.1, "%.2f")

            # ── SBC After-Tax (computed) ──
            sbc_at_row = st.columns(_cw)
            _dcf_row_label(sbc_at_row, "SBC (after-tax)")
            _dcf_row_val(sbc_at_row, 1, "")
            _sbc_vals = [_revs[i + 1] * _sbc_list[i] * (1 - _tax_list[i]) for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(sbc_at_row, i + 2, f"{_sbc_vals[i]:,.0f}")
            _tv_sbc = _tv_rev * _tv_sbc_pct * (1 - _tv_tax)
            _dcf_row_val(sbc_at_row, _tv_col, f"{_tv_sbc:,.0f}")

            _dcf_divider()  # ── Reinvestment → FCFF ──

            # ── FCFF (computed) ──
            fcff_row = st.columns(_cw)
            _dcf_row_label(fcff_row, "FCFF")
            _dcf_row_val(fcff_row, 1, "")
            _fcff_vals = [_nopat_vals[i] - _reinvest_vals[i] - _sbc_vals[i] for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(fcff_row, i + 2, f"{_fcff_vals[i]:,.0f}")
            _tv_fcff = _tv_nopat - _tv_reinvest - _tv_sbc
            _dcf_row_val(fcff_row, _tv_col, f"{_tv_fcff:,.0f}")

            # ── Undiscounted TV ──
            tv_row = st.columns(_cw)
            _dcf_row_label(tv_row, "Undiscounted TV")
            for i in range(_n + 1):
                _dcf_row_val(tv_row, i + 1, "")
            _tv_undiscounted = _tv_fcff / (_tv_wacc - _tg) if (_tv_wacc - _tg) > 0 else 0
            _dcf_row_val(tv_row, _tv_col, f"{_tv_undiscounted:,.0f}")

            _dcf_divider()  # ── FCFF → Discounting ──

            # ── WACC (editable) ──
            wr = st.columns(_cw)
            _dcf_row_label(wr, "WACC", bold=True)
            _dcf_row_val(wr, 1, "")
            for i in range(_n):
                _wacc_list[i] = _dcf_row_input(wr, i + 2, f"ed_w_{i}", _wacc_list[i], 0.1, "%.2f")
            _tv_wacc = _dcf_row_input(wr, _tv_col, "ed_w_tv", _tv_wacc, 0.1, "%.2f")

            # ── Cumulative Discount Factor (computed) ──
            df_row = st.columns(_cw)
            _dcf_row_label(df_row, "Cum. Discount Factor")
            _dcf_row_val(df_row, 1, "1")
            _df_vals = []
            for i in range(_n):
                period = 0.5 + i
                df = 1 / (1 + _wacc_list[i]) ** period if _wacc_list[i] > 0 else 1
                _df_vals.append(df)
                _dcf_row_val(df_row, i + 2, f"{df:.2f}")
            _dcf_row_val(df_row, _tv_col, "")

            # ── PV of FCFF (computed, with separator) ──
            pv_row = st.columns(_cw)
            _dcf_row_label(pv_row, "PV of FCFF", bold=True)
            _dcf_row_val(pv_row, 1, "", sep=True)
            _pv_vals = [_fcff_vals[i] * _df_vals[i] for i in range(_n)]
            for i in range(_n):
                _dcf_row_val(pv_row, i + 2, f"{_pv_vals[i]:,.0f}", sep=True)
            _tv_df = 1 / (1 + _tv_wacc) ** (0.5 + _n - 1) if _tv_wacc > 0 and _n > 0 else 1
            _pv_tv = _tv_undiscounted * _tv_df
            _dcf_row_val(pv_row, _tv_col, f"{_pv_tv:,.0f}", sep=True)

            # ── Enterprise Value ──
            _sum_pv = sum(_pv_vals)
            _ev = _sum_pv + _pv_tv
            ev_row = st.columns(_cw)
            _dcf_row_label(ev_row, "Enterprise Value", bold=True)
            _dcf_row_val(ev_row, 1, f"{_ev:,.0f}", bold=True)
            for i in range(_n + 1):
                _dcf_row_val(ev_row, i + 2, "")

        # Write back edited values
        cfg['revenue_growth'] = growth
        cfg['op_margins'] = margins
        cfg['wacc_per_year'] = _wacc_list
        cfg['tax_per_year'] = _tax_list
        cfg['stc_per_year'] = _stc_list
        cfg['sbc_per_year'] = _sbc_list
        cfg['terminal_growth'] = _tg
        cfg['terminal_margin'] = _tm
        cfg['terminal_tax'] = _tv_tax
        cfg['terminal_stc'] = _tv_stc
        cfg['terminal_wacc'] = _tv_wacc
        cfg['terminal_sbc'] = _tv_sbc_pct

        st.markdown("<br>", unsafe_allow_html=True)

        # Equity bridge — interactive waterfall
        st.markdown("#### Valuation Bridge")
        _wf_val = '<div style="display:flex;justify-content:space-between;padding:6px 0"><span style="{extra}">{label}</span><span style="{extra}">{value}</span></div>'
        _wf_sep = '<div style="border-top:1px solid rgba(128,128,128,0.25);margin:2px 0"></div>'

        with st.container(border=True):
            st.markdown(_wf_val.format(label="Enterprise Value", value=f"${_ev:,.0f}",
                                       extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
            cfg['cash_bridge'] = int(st.number_input(
                "\u2003+ Cash ($M)", value=int(cfg.get('cash_bridge', 0)),
                step=100, key="ed_cash",
            ))
            cfg['securities'] = int(st.number_input(
                "\u2003+ Securities ($M)", value=int(cfg.get('securities', 0)),
                step=100, key="ed_sec",
            ))
            _cash_sec = cfg['cash_bridge'] + cfg['securities']
            cfg['debt_market_value'] = int(st.number_input(
                "\u2003\u2212 Debt ($M)", value=int(cfg.get('debt_market_value', 0)),
                step=100, key="ed_debt",
            ))
            _debt = cfg['debt_market_value']

            st.markdown(_wf_sep, unsafe_allow_html=True)
            _equity = _ev + _cash_sec - _debt
            st.markdown(_wf_val.format(label="Equity Value", value=f"${_equity:,.0f}",
                                       extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)

            cfg['shares_outstanding'] = int(st.number_input(
                "\u2003\u00f7 Shares Outstanding (M)", value=int(cfg.get('shares_outstanding', 0)),
                step=10, key="ed_shares",
            ))
            cfg['buyback_rate'] = st.number_input(
                "\u2003\u00d7 Bruto Buyback Rate %", value=cfg.get('buyback_rate', 0.0) * 100,
                step=0.5, format="%.1f", key="ed_bb_rate",
            ) / 100
            _adj_shares = cfg['shares_outstanding'] * (1 - cfg['buyback_rate']) ** _n
            _intrinsic = _equity / _adj_shares if _adj_shares > 0 else 0

            st.markdown(_wf_sep, unsafe_allow_html=True)
            st.markdown(_wf_val.format(label="Intrinsic Value", value=f"${_intrinsic:,.2f}",
                                       extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)

            cfg['margin_of_safety'] = st.slider(
                "\u2003\u00d7 Margin of Safety", 0, 50,
                value=int(cfg.get('margin_of_safety', 0.20) * 100),
                step=5, format="%d%%", key="ed_mos",
            ) / 100
            _mos = cfg['margin_of_safety']
            _buy = _intrinsic * (1 - _mos)

            st.markdown(_wf_sep, unsafe_allow_html=True)
            st.markdown(_wf_val.format(label="Buy Price", value=f"${_buy:,.2f}",
                                       extra="font-weight:700;font-size:1.15rem;color:#81b29a;"), unsafe_allow_html=True)

            _cur_price = cfg.get('stock_price', 0)
            if _cur_price > 0:
                _upside = (_buy / _cur_price - 1) * 100
                _up_color = "#81b29a" if _upside >= 0 else "#e07a5f"
                _up_label = "upside" if _upside >= 0 else "downside"
                st.markdown(_wf_sep, unsafe_allow_html=True)
                st.markdown(_wf_val.format(label="Current Price", value=f"${_cur_price:,.2f}",
                                           extra="font-weight:700;font-size:1.05rem;"), unsafe_allow_html=True)
                st.markdown(_wf_val.format(label=f"\u2003{_up_label}",
                                           value=f"{_upside:+.1f}%",
                                           extra=f"font-weight:700;font-size:1.05rem;color:{_up_color};"), unsafe_allow_html=True)

        # ── Historical Data (read-only, inside DCF tab) ──
        ic_years = cfg.get('ic_years', [])
        if ic_years:
            with st.expander("#### Historical Data", expanded=False):
                st.markdown("**Income Statement ($M)**")
                hist_rows = [
                    ("Revenue", cfg.get('hist_revenue', [])),
                    ("Operating Income", cfg.get('hist_operating_income', [])),
                    ("Net Income", cfg.get('hist_net_income', [])),
                    ("Cost of Revenue", cfg.get('hist_cost_of_revenue', [])),
                    ("SBC", cfg.get('hist_sbc_values', [])),
                    ("Shares (M)", cfg.get('hist_shares', [])),
                ]
                hist_header = "| |" + "|".join(str(y) for y in ic_years) + "|\n"
                hist_header += "|---|" + "|".join("---:" for _ in ic_years) + "|\n"
                hist_body = ""
                for label, vals in hist_rows:
                    if vals:
                        hist_body += f"| **{label}** |" + "|".join(f"{v:,}" for v in vals) + "|\n"
                if hist_body:
                    st.markdown(hist_header + hist_body)

                st.markdown("**Balance Sheet ($M)**")
                bs_rows = [
                    ("Current Assets", cfg.get('current_assets', [])),
                    ("Cash", cfg.get('cash', [])),
                    ("ST Investments", cfg.get('st_investments', [])),
                    ("Operating Cash", cfg.get('operating_cash', [])),
                    ("Current Liabilities", cfg.get('current_liabilities', [])),
                    ("ST Debt", cfg.get('st_debt', [])),
                    ("ST Leases", cfg.get('st_leases', [])),
                    ("Net PP&E", cfg.get('net_ppe', [])),
                    ("Goodwill & Intang.", cfg.get('goodwill_intang', [])),
                ]
                bs_header = "| |" + "|".join(str(y) for y in ic_years) + "|\n"
                bs_header += "|---|" + "|".join("---:" for _ in ic_years) + "|\n"
                bs_body = ""
                for label, vals in bs_rows:
                    if vals:
                        bs_body += f"| **{label}** |" + "|".join(f"{v:,}" for v in vals) + "|\n"
                if bs_body:
                    st.markdown(bs_header + bs_body)

    with _tab_rdcf:
        import pandas as pd

        st.markdown("#### Reverse DCF")

        # ── Adjustable ranges (expander) ──
        _rdcf_g_range = None
        _rdcf_m_range = None
        with st.expander("Adjust ranges"):
            _rc1, _rc2, _rc3 = st.columns(3)
            with _rc1:
                st.markdown("**Revenue CAGR**")
                _rg_min = st.number_input("Min %", value=0.0, step=1.0, format="%.0f", key="rdcf_gmin") / 100
                _rg_max = st.number_input("Max %", value=30.0, step=1.0, format="%.0f", key="rdcf_gmax") / 100
                _rg_step = st.number_input("Step %", value=2.0, step=0.5, format="%.1f", key="rdcf_gstep") / 100
                if _rg_step > 0 and _rg_max > _rg_min:
                    _rdcf_g_range = (_rg_min, _rg_max, _rg_step)
            with _rc2:
                st.markdown("**Operating Margin**")
                _rm_min = st.number_input("Min %", value=5.0, step=1.0, format="%.0f", key="rdcf_mmin") / 100
                _rm_max = st.number_input("Max %", value=40.0, step=1.0, format="%.0f", key="rdcf_mmax") / 100
                _rm_step = st.number_input("Step %", value=2.0, step=0.5, format="%.1f", key="rdcf_mstep") / 100
                if _rm_step > 0 and _rm_max > _rm_min:
                    _rdcf_m_range = (_rm_min, _rm_max, _rm_step)
            with _rc3:
                st.markdown("**WACC**")
                _rdcf_wacc = st.number_input(
                    "WACC %", value=val['wacc'] * 100,
                    step=0.1, format="%.2f", key="rdcf_wacc",
                ) / 100

        # ── Compute reverse DCF ──
        _rdcf = compute_reverse_dcf(cfg, wacc=_rdcf_wacc,
                                     growth_range=_rdcf_g_range,
                                     margin_range=_rdcf_m_range)

        # ── Market vs Your Base Case comparison ──
        _bc = _rdcf['base_cagr']
        _bm = _rdcf['base_margin']
        _closest = _rdcf['closest']
        _impl_g, _impl_m = _closest if _closest else (0, 0)

        _mc1, _mc2 = st.columns(2)
        with _mc1:
            st.markdown(
                f'<div style="border:1px solid #e8e8ed;border-radius:12px;padding:20px;text-align:center">'
                f'<div style="color:#86868b;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Market implies</div>'
                f'<div style="font-size:1.8rem;font-weight:700;margin:8px 0;color:#1d1d1f">{_impl_g:.0%} CAGR &nbsp;+&nbsp; {_impl_m:.0%} Margin</div>'
                f'<div style="color:#86868b;font-size:0.85rem">to justify ${_rdcf["market_price"]:.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        with _mc2:
            st.markdown(
                f'<div style="border:1px solid #e8e8ed;border-radius:12px;padding:20px;text-align:center">'
                f'<div style="color:#86868b;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;font-weight:600">Your base case</div>'
                f'<div style="font-size:1.8rem;font-weight:700;margin:8px 0;color:#1d1d1f">{_bc:.0%} CAGR &nbsp;+&nbsp; {_bm:.0%} Margin</div>'
                f'<div style="color:#86868b;font-size:0.85rem">DCF value ${val["intrinsic_value"]:.2f}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # ── Conclusion ──
        if _impl_g > _bc * 1.2 or _impl_m > _bm * 1.2:
            _conclusion = (f"Market is more optimistic than your base case — "
                           f"it prices in {_impl_g:.0%} CAGR / {_impl_m:.0%} margin "
                           f"vs your {_bc:.0%} / {_bm:.0%}.")
        elif _impl_g < _bc * 0.8 or _impl_m < _bm * 0.8:
            _conclusion = (f"Potential undervaluation — market only requires "
                           f"{_impl_g:.0%} CAGR / {_impl_m:.0%} margin, "
                           f"below your {_bc:.0%} / {_bm:.0%} base case.")
        else:
            _conclusion = (f"Fairly priced — market-implied assumptions "
                           f"({_impl_g:.0%} CAGR / {_impl_m:.0%} margin) "
                           f"are close to your base case ({_bc:.0%} / {_bm:.0%}).")
        st.markdown(
            f'<div style="color:#86868b;font-size:0.85rem;text-align:center;margin:12px 0 16px">{_conclusion}</div>',
            unsafe_allow_html=True,
        )

        # ── Sensitivity matrix ──
        st.markdown(f"**Sensitivity Matrix** — WACC: {_rdcf['wacc']:.2%} | Market: ${_rdcf['market_price']:.2f}")

        _g_tests = _rdcf['growth_tests']
        _m_tests = _rdcf['margin_tests']
        _closest = _rdcf['closest']
        _mkt = _rdcf['market_price']

        # Build pivot table
        _matrix_data = {}
        for entry in _rdcf['matrix']:
            _matrix_data[(entry['growth'], entry['margin'])] = entry['price']

        _df_data = []
        for g in _g_tests:
            row = {}
            for mg in _m_tests:
                row[f"{mg:.0%}"] = _matrix_data.get((g, mg), 0)
            _df_data.append(row)
        _df = pd.DataFrame(_df_data, index=[f"{g:.0%}" for g in _g_tests])
        _df.index.name = "CAGR \\ Margin"

        # Style the matrix
        def _style_matrix(df):
            styles = pd.DataFrame('', index=df.index, columns=df.columns)
            for i, g in enumerate(_g_tests):
                for j, mg in enumerate(_m_tests):
                    price = _matrix_data.get((g, mg), 0)
                    col_name = f"{mg:.0%}"
                    row_name = f"{g:.0%}"
                    if (g, mg) == _closest:
                        styles.loc[row_name, col_name] = 'background-color: #81b29a; color: white; font-weight: bold'
                    elif price >= _mkt:
                        styles.loc[row_name, col_name] = 'background-color: rgba(129,178,154,0.15); color: #1d1d1f'
                    else:
                        styles.loc[row_name, col_name] = 'background-color: rgba(224,122,95,0.15); color: #1d1d1f'
            return styles

        _styled = _df.style.apply(_style_matrix, axis=None).format("${:,.0f}")
        _row_height = 35
        _header_height = 40
        _df_height = _header_height + len(_g_tests) * _row_height + 10
        st.dataframe(_styled, use_container_width=True, height=_df_height)

        # ── Legend ──
        st.markdown(
            '<div style="display:flex;gap:20px;font-size:0.8rem;color:#86868b;margin-top:4px">'
            '<span><span style="display:inline-block;width:12px;height:12px;background:#81b29a;border-radius:2px;vertical-align:middle;margin-right:4px"></span>Market-implied</span>'
            '<span><span style="display:inline-block;width:12px;height:12px;background:rgba(129,178,154,0.15);border:1px solid #81b29a;border-radius:2px;vertical-align:middle;margin-right:4px"></span>Undervalued</span>'
            '<span><span style="display:inline-block;width:12px;height:12px;background:rgba(224,122,95,0.15);border:1px solid #e07a5f;border-radius:2px;vertical-align:middle;margin-right:4px"></span>Overvalued</span>'
            '</div>',
            unsafe_allow_html=True,
        )

    with _tab_peers:
        _base_margin_p = cfg.get('base_op_margin', 0)
        _rev_growth_p = growth[0] if growth else 0
        _ev_rev_p = _ev / _base_rev if _base_rev else 0
        st.markdown("#### Peer Comparison")

        # Compute metrics for current ticker
        _mkt_cap_p = cfg.get('equity_market_value', 0)
        _debt_p = cfg.get('debt_market_value', 0)
        _cash_p = cfg.get('cash_bridge', 0)
        _ev_calc_p = _mkt_cap_p + _debt_p - _cash_p
        _ebitda_p = _base_oi * 1.3 if _base_oi > 0 else 0
        _ev_ebitda_p = _ev_calc_p / _ebitda_p if _ebitda_p > 0 else 0
        _ni_list = cfg.get('hist_net_income', [])
        _ni_p = _ni_list[-1] if _ni_list else 0
        _pe_p = _mkt_cap_p / _ni_p if _ni_p > 0 else 0
        # ROIC: NOPAT / Invested Capital
        _ca_list = cfg.get('current_assets', [])
        _cl_list = cfg.get('current_liabilities', [])
        _ppe_list = cfg.get('net_ppe', [])
        _gi_list = cfg.get('goodwill_intang', [])
        _sd_list = cfg.get('st_debt', [])
        _ca_p = _ca_list[-1] if _ca_list else 0
        _cl_p = _cl_list[-1] if _cl_list else 0
        _ppe_p = _ppe_list[-1] if _ppe_list else 0
        _gi_p = _gi_list[-1] if _gi_list else 0
        _sd_p = _sd_list[-1] if _sd_list else 0
        _ic_p = (_ca_p - _cash_p) + _ppe_p + _gi_p - (_cl_p - _sd_p)
        _nopat_p = _base_oi * (1 - 0.21)
        _roic_p = _nopat_p / _ic_p if _ic_p > 0 else 0

        # Build all rows: current ticker first, then peers
        peers = cfg.get('peers', [])
        _peer_rows = [
            {"ticker": ticker, "ev_revenue": _ev_rev_p, "ev_ebitda": _ev_ebitda_p,
             "pe": _pe_p, "op_margin": _base_margin_p, "rev_growth": _rev_growth_p,
             "roic": _roic_p, "is_self": True},
        ] + [dict(**p, is_self=False) for p in peers]

        _peer_metrics = [
            ("EV/Rev", "ev_revenue", "x", 1),
            ("EV/EBITDA", "ev_ebitda", "x", 1),
            ("P/E", "pe", "x", 1),
            ("Op Margin", "op_margin", "%", 1),
            ("Rev Growth", "rev_growth", "%", 1),
            ("ROIC", "roic", "%", 0),
        ]

        _th_style = ('text-align:right;padding:8px 12px;border-bottom:2px solid #d2d2d7;color:#86868b;'
                     'font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em')
        _ptable = (
            '<div style="overflow-x:auto">'
            '<table style="width:100%;border-collapse:collapse;font-size:0.9rem">'
            '<thead><tr>'
            f'<th style="text-align:left;padding:8px 12px;border-bottom:2px solid #d2d2d7;color:#86868b;'
            f'font-size:0.75rem;text-transform:uppercase;letter-spacing:0.03em">Company</th>'
        )
        for mlabel, _, _, _ in _peer_metrics:
            _ptable += f'<th style="{_th_style}">{mlabel}</th>'
        _ptable += '</tr></thead><tbody>'

        for idx_p, pr in enumerate(_peer_rows):
            _is_self = pr.get("is_self", False)
            _pt = pr.get("ticker", "")
            _logo_url = f"https://assets.parqet.com/logos/symbol/{_pt}"
            _row_bg = 'background:#f9f9fb;' if _is_self else ''
            _fw = 'font-weight:700;' if _is_self else ''
            _ptable += f'<tr style="{_row_bg}">'
            _ptable += (
                f'<td style="padding:10px 12px;border-bottom:1px solid #e8e8ed;{_fw}">'
                f'<div style="display:flex;align-items:center;gap:10px">'
                f'<img src="{_logo_url}" style="width:28px;height:28px;border-radius:50%;object-fit:cover" '
                f'onerror="this.style.display=\'none\'">'
                f'<span>{_pt}</span>'
                f'</div></td>'
            )
            for _, mkey, mfmt, mdec in _peer_metrics:
                _mv = pr.get(mkey, 0)
                if mfmt == "%" and _mv:
                    _mstr = f'{_mv:.{mdec}%}'
                elif mfmt == "x" and _mv:
                    _mstr = f'{_mv:.{mdec}f}x'
                else:
                    _mstr = "—"
                _ptable += (
                    f'<td style="text-align:right;padding:10px 12px;border-bottom:1px solid #e8e8ed;{_fw}">'
                    f'{_mstr}</td>'
                )
            _ptable += '</tr>'
        _ptable += '</tbody></table></div>'
        st.markdown(_ptable, unsafe_allow_html=True)

        # ── Manage peers ──
        st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)
        _peer_tickers = [p.get("ticker", "") for p in peers]
        _kept = st.multiselect("Peers", options=_peer_tickers, default=_peer_tickers,
                               key="ed_peer_select", label_visibility="collapsed")
        if set(_kept) != set(_peer_tickers):
            cfg['peers'] = [p for p in peers if p.get("ticker") in _kept]
            save_config(ticker, cfg)
            st.rerun()

        with st.form("add_peer_form"):
            _ac1, _ac2 = st.columns([5, 1])
            with _ac1:
                _new_peer = st.text_input("Add peer", key="ed_add_peer",
                                          placeholder="Add peer — e.g. MSFT, GOOG",
                                          label_visibility="collapsed")
            with _ac2:
                _add_clicked = st.form_submit_button("+ Add", use_container_width=True)
        if _add_clicked and _new_peer:
            _new_tickers = [t.strip().upper() for t in _new_peer.split(",") if t.strip()]
            _existing = {p.get("ticker") for p in peers}
            _to_fetch = [t for t in _new_tickers if t not in _existing and t != ticker]
            if _to_fetch:
                with st.spinner(f"Fetching data for {', '.join(_to_fetch)}..."):
                    _new_peers = fetch_peer_data(_to_fetch)
                if _new_peers:
                    peers.extend(_new_peers)
                    cfg['peers'] = peers
                    save_config(ticker, cfg)
                    st.session_state.pop("ed_peer_select", None)
                    st.rerun()
                else:
                    st.warning("Could not fetch peer data. Check the ticker(s).")

    with _tab_fundamentals:
        st.markdown("#### Fundamentals")

        @st.cache_data(ttl=300, show_spinner="Loading fundamentals...")
        def _cached_fundamentals(t):
            return fetch_fundamentals(t, n_years=11)

        fund = _cached_fundamentals(ticker)
        _yrs = fund['years']
        _n = len(_yrs)

        # Chart style constants
        _COLORS = {
            'primary': '#81b29a',
            'secondary': '#e07a5f',
            'accent': '#3d405b',
            'tertiary': '#f2cc8f',
        }

        def _base_layout(fig, height=280):
            fig.update_layout(
                margin=dict(t=10, b=20, l=50, r=20),
                height=height,
                font=dict(
                    family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                    color="#1d1d1f",
                ),
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(gridcolor='#f0f0f2', dtick=1),
                yaxis=dict(gridcolor='#f0f0f2'),
                legend=dict(
                    orientation="h", yanchor="top", y=-0.15,
                    xanchor="center", x=0.5, font=dict(size=11),
                ),
                hovermode="x unified",
            )
            return fig

        def _pct_growth(values):
            result = [None]
            for i in range(1, len(values)):
                prev, curr = values[i - 1], values[i]
                if prev and prev != 0 and curr is not None:
                    result.append((curr / prev) - 1)
                else:
                    result.append(None)
            return result

        # ── Operating Leverage (compact table) ──
        st.markdown("**Operating Leverage**")
        rev_g = _pct_growth(fund['revenue'])
        oi_g = _pct_growth(fund['operating_income'])
        if _n >= 3:
            _ol_cell = 'text-align:right;padding:6px 10px;font-size:0.85rem'
            _ol_hdr = 'text-align:right;padding:6px 10px;font-size:0.85rem;color:#86868b'
            _ol_label = 'text-align:left;padding:6px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap'
            _ol_html = (
                '<div style="overflow-x:auto">'
                '<table style="width:100%;border-collapse:collapse">'
                '<thead><tr>'
                f'<th style="{_ol_hdr};text-align:left"></th>'
            )
            _ol_avg = f'{_ol_cell};font-weight:600;border-left:2px solid #d2d2d7'
            for yr in _yrs[1:]:
                _ol_html += f'<th style="{_ol_hdr}">{yr}</th>'
            _ol_html += f'<th style="{_ol_hdr};border-left:2px solid #d2d2d7">Avg</th>'
            _ol_html += '</tr></thead><tbody>'

            # Revenue Growth row
            _rev_vals = [rev_g[i] for i in range(1, _n) if rev_g[i] is not None]
            _rev_avg = sum(_rev_vals) / len(_rev_vals) if _rev_vals else None
            _ol_html += f'<tr style="border-top:1px solid #f0f0f2"><td style="{_ol_label}">Revenue Growth</td>'
            for i in range(1, _n):
                v = rev_g[i]
                _ol_html += f'<td style="{_ol_cell}">{v*100:.1f}%</td>' if v is not None else f'<td style="{_ol_cell}">—</td>'
            _ol_html += f'<td style="{_ol_avg}">{_rev_avg*100:.1f}%</td>' if _rev_avg is not None else f'<td style="{_ol_avg}">—</td>'
            _ol_html += '</tr>'

            # OI Growth row — green when OI growth > Rev growth, red when below
            _oi_vals = [oi_g[i] for i in range(1, _n) if oi_g[i] is not None]
            _oi_avg = sum(_oi_vals) / len(_oi_vals) if _oi_vals else None
            _ol_html += f'<tr style="border-top:1px solid #f0f0f2"><td style="{_ol_label}">OI Growth</td>'
            for i in range(1, _n):
                r, o = rev_g[i], oi_g[i]
                if o is not None:
                    if r is not None and o > r:
                        color = '#81b29a'
                    elif r is not None and o < r:
                        color = '#e07a5f'
                    else:
                        color = '#1d1d1f'
                    weight = 'font-weight:600;' if color != '#1d1d1f' else ''
                    _ol_html += f'<td style="{_ol_cell};color:{color};{weight}">{o*100:.1f}%</td>'
                else:
                    _ol_html += f'<td style="{_ol_cell}">—</td>'
            if _oi_avg is not None and _rev_avg is not None:
                _oi_avg_color = '#81b29a' if _oi_avg > _rev_avg else '#e07a5f'
                _ol_html += f'<td style="{_ol_avg};color:{_oi_avg_color}">{_oi_avg*100:.1f}%</td>'
            else:
                _ol_html += f'<td style="{_ol_avg}">—</td>'
            _ol_html += '</tr>'

            # DOL row
            _dol_vals = []
            for i in range(1, _n):
                r, o = rev_g[i], oi_g[i]
                if r and o and r != 0:
                    _dol_vals.append(o / r)
            _dol_avg = sum(_dol_vals) / len(_dol_vals) if _dol_vals else None
            _ol_html += f'<tr style="border-top:1px solid #f0f0f2"><td style="{_ol_label}">DOL</td>'
            for i in range(1, _n):
                r, o = rev_g[i], oi_g[i]
                if r and o and r != 0:
                    dol = o / r
                    color = '#81b29a' if dol > 1 else '#e07a5f'
                    weight = 'font-weight:600;'
                    _ol_html += f'<td style="{_ol_cell};color:{color};{weight}">{dol:.1f}x</td>'
                else:
                    _ol_html += f'<td style="{_ol_cell}">—</td>'
            if _dol_avg is not None:
                _dol_avg_color = '#81b29a' if _dol_avg > 1 else '#e07a5f'
                _ol_html += f'<td style="{_ol_avg};color:{_dol_avg_color}">{_dol_avg:.1f}x</td>'
            else:
                _ol_html += f'<td style="{_ol_avg}">—</td>'
            _ol_html += '</tr>'

            _ol_html += '</tbody></table></div>'
            st.markdown(_ol_html, unsafe_allow_html=True)
            st.caption("DOL > 1 = elke % omzetgroei vertaalt in meer dan 1% winstgroei (schaalvoordeel)")
        else:
            st.info("Insufficient data for Operating Leverage (need 3+ years)")

        # ── Margins ──
        st.markdown("")
        st.markdown("**Margins**")
        if _n >= 3:
            rev = fund['revenue']
            gross_m = [(rev[i] - fund['cost_of_revenue'][i]) / rev[i] * 100
                       if rev[i] and fund['cost_of_revenue'][i] is not None else None
                       for i in range(_n)]
            op_m = [fund['operating_income'][i] / rev[i] * 100
                    if rev[i] and fund['operating_income'][i] is not None else None
                    for i in range(_n)]
            fcf_m = [fund['fcf'][i] / rev[i] * 100
                     if rev[i] and fund['fcf'][i] is not None else None
                     for i in range(_n)]
            fig = go.Figure()
            for name, vals, color in [
                ('Gross', gross_m, _COLORS['primary']),
                ('Operating', op_m, _COLORS['accent']),
                ('FCF', fcf_m, _COLORS['tertiary']),
            ]:
                fig.add_trace(go.Scatter(
                    x=_yrs, y=vals, name=name,
                    line=dict(color=color, width=2.5),
                    hovertemplate='%{y:.1f}%<extra>' + name + ' Margin</extra>',
                ))
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig)
            st.plotly_chart(fig, use_container_width=True)

            # Margins table with numbers + Operating Margin delta
            _m_cell = 'text-align:right;padding:5px 10px;font-size:0.85rem'
            _m_hdr = 'text-align:right;padding:5px 10px;font-size:0.85rem;color:#86868b'
            _m_label = 'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap'
            _m_avg_style = f'{_m_cell};font-weight:600;border-left:2px solid #d2d2d7'
            _m_html = (
                '<div style="overflow-x:auto">'
                '<table style="width:100%;border-collapse:collapse">'
                '<thead><tr>'
                f'<th style="{_m_hdr};text-align:left"></th>'
            )
            for yr in _yrs:
                _m_html += f'<th style="{_m_hdr}">{yr}</th>'
            _m_html += f'<th style="{_m_hdr};border-left:2px solid #d2d2d7">Avg</th>'
            _m_html += '</tr></thead><tbody>'

            for label, vals in [('Gross', gross_m), ('Operating', op_m), ('FCF', fcf_m)]:
                _valid = [v for v in vals if v is not None]
                _avg = sum(_valid) / len(_valid) if _valid else None
                _m_html += f'<tr style="border-top:1px solid #f0f0f2"><td style="{_m_label}">{label}</td>'
                for v in vals:
                    _m_html += f'<td style="{_m_cell}">{v:.1f}%</td>' if v is not None else f'<td style="{_m_cell}">—</td>'
                _m_html += f'<td style="{_m_avg_style}">{_avg:.1f}%</td>' if _avg is not None else f'<td style="{_m_avg_style}">—</td>'
                _m_html += '</tr>'

            # Operating Margin delta row — expanding margin = operating leverage
            _m_html += f'<tr style="border-top:1px solid #f0f0f2"><td style="{_m_label}">Op Margin \u0394</td>'
            _delta_vals = []
            for i in range(_n):
                if i == 0:
                    _m_html += f'<td style="{_m_cell}">—</td>'
                elif op_m[i] is not None and op_m[i - 1] is not None:
                    d = op_m[i] - op_m[i - 1]
                    _delta_vals.append(d)
                    color = '#81b29a' if d > 0 else '#e07a5f'
                    sign = '+' if d > 0 else ''
                    _m_html += f'<td style="{_m_cell};color:{color};font-weight:600">{sign}{d:.1f}pp</td>'
                else:
                    _m_html += f'<td style="{_m_cell}">—</td>'
            _d_avg = sum(_delta_vals) / len(_delta_vals) if _delta_vals else None
            if _d_avg is not None:
                d_color = '#81b29a' if _d_avg > 0 else '#e07a5f'
                d_sign = '+' if _d_avg > 0 else ''
                _m_html += f'<td style="{_m_avg_style};color:{d_color}">{d_sign}{_d_avg:.1f}pp</td>'
            else:
                _m_html += f'<td style="{_m_avg_style}">—</td>'
            _m_html += '</tr>'

            _m_html += '</tbody></table></div>'
            st.markdown(_m_html, unsafe_allow_html=True)
            st.caption("Op Margin \u0394 > 0 bij groeiende omzet = operating leverage (schaalvoordeel in kosten)")
        else:
            st.info("Insufficient data for Margins (need 3+ years)")

        # ── Row 2: ROIC + FCF Conversion ──
        _r2c1, _r2c2 = st.columns(2)

        with _r2c1:
            st.markdown("**ROIC**")
            if _n >= 3:
                roic_vals = []
                for i in range(_n):
                    oi = fund['operating_income'][i]
                    eq = fund['total_equity'][i]
                    debt = fund['total_debt'][i]
                    cash_v = fund['cash'][i]
                    tp = fund['tax_provision'][i]
                    pti = fund['pretax_income'][i]
                    tax_rate = tp / pti if pti and pti != 0 else 0.21
                    nopat = oi * (1 - tax_rate) if oi is not None else 0
                    ic = (eq or 0) + (debt or 0) - (cash_v or 0)
                    roic_vals.append(nopat / ic * 100 if ic > 0 else None)

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=roic_vals, name='ROIC',
                    line=dict(color=_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>ROIC</extra>',
                ))
                wacc_pct = val.get('wacc', 0) * 100
                if wacc_pct > 0:
                    fig.add_hline(
                        y=wacc_pct, line_dash="dash",
                        line_color=_COLORS['secondary'],
                        annotation_text=f"WACC {wacc_pct:.1f}%",
                        annotation_position="top right",
                    )
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for ROIC (need 3+ years)")

        with _r2c2:
            st.markdown("**FCF Conversion**")
            if _n >= 3:
                conv = [fund['fcf'][i] / fund['net_income'][i] * 100
                        if fund['net_income'][i] and fund['net_income'][i] != 0
                           and fund['fcf'][i] is not None
                        else None
                        for i in range(_n)]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=conv, name='FCF / Net Income',
                    line=dict(color=_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.0f}%<extra>FCF Conversion</extra>',
                ))
                fig.add_hline(y=100, line_dash="dash", line_color=_COLORS['accent'],
                              annotation_text="100%", annotation_position="top right")
                fig.add_hline(y=70, line_dash="dot", line_color=_COLORS['secondary'],
                              annotation_text="70%", annotation_position="bottom right")
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for FCF Conversion (need 3+ years)")

        # ── Row 3: Revenue per Share Growth + Debt/FCF ──
        _r3c1, _r3c2 = st.columns(2)

        with _r3c1:
            st.markdown("**Revenue per Share Growth**")
            if _n >= 3:
                rps = [fund['revenue'][i] * 1e6 / fund['shares'][i]
                       if fund['shares'][i] and fund['shares'][i] > 0
                          and fund['revenue'][i] is not None
                       else 0
                       for i in range(_n)]
                rps_g = _pct_growth(rps)
                rev_g_clean = _pct_growth(fund['revenue'])
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[r * 100 if r is not None else None for r in rev_g_clean[1:]],
                    name='Revenue Growth',
                    line=dict(color=_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>Rev Growth</extra>',
                ))
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[r * 100 if r is not None else None for r in rps_g[1:]],
                    name='Rev/Share Growth',
                    line=dict(color=_COLORS['accent'], width=2.5, dash='dash'),
                    hovertemplate='%{y:.1f}%<extra>Rev/Share Growth</extra>',
                ))
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for Revenue per Share (need 3+ years)")

        with _r3c2:
            st.markdown("**Debt / FCF**")
            if _n >= 3:
                debt_fcf = []
                for i in range(_n):
                    fcf_v = fund['fcf'][i]
                    debt_v = fund['total_debt'][i]
                    if fcf_v and fcf_v > 0 and debt_v is not None:
                        debt_fcf.append(debt_v / fcf_v)
                    else:
                        debt_fcf.append(None)
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=debt_fcf, name='Debt/FCF',
                    line=dict(color=_COLORS['accent'], width=2.5),
                    hovertemplate='%{y:.1f}x<extra>Debt/FCF</extra>',
                ))
                fig.add_hline(y=3, line_dash="dash", line_color=_COLORS['primary'],
                              annotation_text="3x", annotation_position="top right")
                fig.add_hline(y=5, line_dash="dash", line_color=_COLORS['secondary'],
                              annotation_text="5x", annotation_position="top right")
                fig.update_yaxes(ticksuffix='x')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for Debt/FCF (need 3+ years)")

        # ── Row 4: FCF Yield (full width) ──
        st.markdown("**FCF Yield**")
        if _n >= 2 and live_price > 0:
            fcf_yield = []
            for i in range(_n):
                sh = fund['shares'][i]
                if sh and sh > 0 and fund['fcf'][i] is not None:
                    fps = fund['fcf'][i] * 1e6 / sh
                    fcf_yield.append(fps / live_price * 100)
                else:
                    fcf_yield.append(None)

            current_fy = fcf_yield[-1] if fcf_yield[-1] is not None else 0
            fy_color = '#81b29a' if current_fy > 3 else ('#e07a5f' if current_fy < 1 else '#1d1d1f')
            st.markdown(
                f'<div style="text-align:center;padding:8px 0">'
                f'<span style="font-size:2rem;font-weight:700;color:{fy_color}">{current_fy:.1f}%</span>'
                f'<span style="color:#86868b;font-size:0.9rem;margin-left:8px">current FCF Yield</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs, y=fcf_yield, name='FCF Yield',
                line=dict(color=_COLORS['primary'], width=2.5),
                fill='tozeroy', fillcolor='rgba(129,178,154,0.15)',
                hovertemplate='%{y:.1f}%<extra>FCF Yield</extra>',
            ))
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig, height=250)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Insufficient data for FCF Yield")

    with _tab_key_ratios:
        st.markdown("#### Key Ratios")

        fund = _cached_fundamentals(ticker)
        _yrs = fund['years']
        _n = len(_yrs)

        @st.cache_data(ttl=300, show_spinner="Loading historical prices...")
        def _cached_hist_prices(t, yrs):
            return fetch_historical_prices(t, list(yrs))

        _hist_prices = _cached_hist_prices(ticker, tuple(_yrs))

        # ── Table helper styles ──
        _kr_cell = 'text-align:right;padding:5px 10px;font-size:0.85rem'
        _kr_hdr = 'text-align:right;padding:5px 10px;font-size:0.85rem;color:#86868b'
        _kr_label = 'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap;width:220px;min-width:220px'
        _kr_avg_style = f'{_kr_cell};font-weight:600;border-left:2px solid #d2d2d7'

        def _kr_table_start():
            html = (
                '<div style="overflow-x:auto">'
                '<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
                '<colgroup>'
                '<col style="width:220px">'
            )
            for _ in _yrs:
                html += '<col>'
            html += '<col>'  # Avg column
            html += '</colgroup>'
            html += '<thead><tr>'
            html += f'<th style="{_kr_hdr};text-align:left"></th>'
            for yr in _yrs:
                html += f'<th style="{_kr_hdr}">{yr}</th>'
            html += f'<th style="{_kr_hdr};border-left:2px solid #d2d2d7">Avg</th>'
            html += '</tr></thead><tbody>'
            nonlocal _kr_row_idx
            _kr_row_idx = 0
            return html

        def _kr_fmt(v, fmt):
            if fmt == 'pct1':
                return f'{v:.1f}%'
            elif fmt == 'dec1':
                return f'{v:.1f}'
            elif fmt == 'dec2':
                return f'{v:.2f}'
            elif fmt == 'dollar2':
                return f'${v:.2f}'
            elif fmt == 'num0':
                return f'{v:,.0f}'
            return str(v)

        _kr_row_idx = 0  # for zebra striping

        def _kr_row(label, vals, fmt='pct1'):
            """Render one table row with alternating background."""
            nonlocal _kr_row_idx
            bg = 'background:#f9f9fb' if _kr_row_idx % 2 == 1 else ''
            _kr_row_idx += 1
            row_style = f'border-top:1px solid #f0f0f2;{bg}'
            html = f'<tr style="{row_style}"><td style="{_kr_label}">{label}</td>'
            valid_vals = []
            for v in vals:
                if v is None:
                    html += f'<td style="{_kr_cell}">—</td>'
                    continue
                valid_vals.append(v)
                html += f'<td style="{_kr_cell}">{_kr_fmt(v, fmt)}</td>'
            # Avg column
            avg = sum(valid_vals) / len(valid_vals) if valid_vals else None
            if avg is not None:
                html += f'<td style="{_kr_avg_style}">{_kr_fmt(avg, fmt)}</td>'
            else:
                html += f'<td style="{_kr_avg_style}">—</td>'
            html += '</tr>'
            return html

        def _kr_separator():
            cols = _n + 2
            return f'<tr><td colspan="{cols}" style="padding:4px"></td></tr>'

        def _kr_pct_change(vals):
            result = []
            for i in range(len(vals)):
                if i == 0 or vals[i] is None or vals[i - 1] is None or vals[i - 1] == 0:
                    result.append(None)
                else:
                    result.append((vals[i] / vals[i - 1] - 1) * 100)
            return result

        # ── Balance Sheet ──
        @st.cache_data(ttl=300, show_spinner="Loading balance sheet...")
        def _cached_balance_sheet(t):
            return fetch_balance_sheet(t, n_years=11)

        bsheet = _cached_balance_sheet(ticker)
        _bs_yrs = bsheet['years']
        _bs_n = len(_bs_yrs)

        with st.expander("Balance Sheet", expanded=False):
            if _bs_n < 1:
                st.info("Insufficient balance sheet data")
            else:
                _bs_cell = 'text-align:right;padding:5px 10px;font-size:0.85rem'
                _bs_hdr = 'text-align:right;padding:5px 10px;font-size:0.85rem;color:#86868b'
                _bs_label = 'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap;width:220px;min-width:220px'
                _bs_row_idx = 0

                def _bs_table_start():
                    nonlocal _bs_row_idx
                    _bs_row_idx = 0
                    html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
                        '<colgroup><col style="width:220px">'
                    )
                    for _ in _bs_yrs:
                        html += '<col>'
                    html += '</colgroup><thead><tr>'
                    html += f'<th style="{_bs_hdr};text-align:left"></th>'
                    for yr in _bs_yrs:
                        html += f'<th style="{_bs_hdr}">{yr}</th>'
                    html += '</tr></thead><tbody>'
                    return html

                def _bs_row(label, vals, bold=False):
                    nonlocal _bs_row_idx
                    bg = 'background:#f9f9fb' if _bs_row_idx % 2 == 1 else ''
                    _bs_row_idx += 1
                    row_style = f'border-top:1px solid #f0f0f2;{bg}'
                    fw = ';font-weight:700' if bold else ''
                    lbl_style = f'{_bs_label}{fw}'
                    cell_style = f'{_bs_cell}{fw}'
                    html = f'<tr style="{row_style}"><td style="{lbl_style}">{label}</td>'
                    for v in vals:
                        if v is None:
                            html += f'<td style="{cell_style}">—</td>'
                        else:
                            html += f'<td style="{cell_style}">{v:,.0f}</td>'
                    html += '</tr>'
                    return html

                def _bs_section_hdr(label):
                    nonlocal _bs_row_idx
                    _bs_row_idx = 0
                    cols = _bs_n + 1
                    return (f'<tr><td colspan="{cols}" style="text-align:left;padding:10px 10px 5px;'
                            f'font-size:0.85rem;font-weight:700;color:#1d1d1f">{label}</td></tr>')

                def _bs_separator():
                    cols = _bs_n + 1
                    return f'<tr><td colspan="{cols}" style="padding:4px"></td></tr>'

                # Liabilities & Equity always equals Total Assets by definition
                _liab_eq = bsheet['total_assets']

                # Assets
                st.markdown("**Assets** — *in millions*")
                html = _bs_table_start()
                html += _bs_row('Cash & Equivalents', bsheet['cash'])
                html += _bs_row('Short-Term Investments', bsheet['short_term_investments'])
                html += _bs_row('Accounts Receivable', bsheet['accounts_receivable'])
                html += _bs_row('Inventories', bsheet['inventories'])
                html += _bs_row('Other Current Assets', bsheet['other_current_assets'])
                html += _bs_row('Total Current Assets', bsheet['total_current_assets'], bold=True)
                html += _bs_separator()
                html += _bs_row('Investments', bsheet['investments'])
                html += _bs_row('Property, Plant, & Equipment (Net)', bsheet['ppe'])
                html += _bs_row('Goodwill', bsheet['goodwill'])
                html += _bs_row('Other Intangible Assets', bsheet['intangibles'])
                html += _bs_row('Operating Lease Assets', bsheet['leases'])
                html += _bs_row('Deferred Tax Assets', bsheet['deferred_tax_assets'])
                html += _bs_row('Other Assets', bsheet['other_assets'])
                html += _bs_row('Total Assets', bsheet['total_assets'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Liabilities
                st.markdown("")
                st.markdown("")
                st.markdown("**Liabilities** — *in millions*")
                html = _bs_table_start()
                html += _bs_row('Accounts Payable', bsheet['accounts_payable'])
                html += _bs_row('Tax Payable', bsheet['tax_payable'])
                html += _bs_row('Accrued Liabilities', bsheet['accrued_liabilities'])
                html += _bs_row('Short-Term Debt', bsheet['short_term_debt'])
                html += _bs_row('Current Portion of Capital Leases', bsheet['current_capital_leases'])
                html += _bs_row('Deferred Revenue', bsheet['deferred_revenue_current'])
                html += _bs_row('Other Current Liabilities', bsheet['other_current_liabilities'])
                html += _bs_row('Total Current Liabilities', bsheet['total_current_liabilities'], bold=True)
                html += _bs_separator()
                html += _bs_row('Long-Term Debt', bsheet['long_term_debt'])
                html += _bs_row('Capital Leases', bsheet['capital_leases'])
                html += _bs_row('Deferred Revenue', bsheet['deferred_revenue_noncurrent'])
                html += _bs_row('Other Liabilities', bsheet['other_liabilities'])
                html += _bs_row('Total Liabilities', bsheet['total_liabilities'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Equity
                st.markdown("")
                st.markdown("")
                st.markdown("**Equity** — *in millions*")
                html = _bs_table_start()
                html += _bs_row('Retained Earnings', bsheet['retained_earnings'])
                html += _bs_row('Common Stock', bsheet['common_stock'])
                html += _bs_row('AOCI', bsheet['aoci'])
                html += _bs_row("Shareholders' Equity", bsheet['shareholders_equity'], bold=True)
                html += _bs_row('Liabilities & Equity', _liab_eq, bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

        # ── Income Statement ──
        @st.cache_data(ttl=300, show_spinner="Loading income statement...")
        def _cached_income_stmt(t):
            return fetch_income_statement(t, n_years=11)

        istmt = _cached_income_stmt(ticker)
        _is_yrs = istmt['years']
        _is_n = len(_is_yrs)

        with st.expander("Income Statement", expanded=False):
            if _is_n < 1:
                st.info("Insufficient income statement data")
            else:
                _is_cell = 'text-align:right;padding:5px 10px;font-size:0.85rem'
                _is_hdr = 'text-align:right;padding:5px 10px;font-size:0.85rem;color:#86868b'
                _is_label = 'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap;width:220px;min-width:220px'
                _is_row_idx = 0

                def _is_table_start():
                    nonlocal _is_row_idx
                    _is_row_idx = 0
                    html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
                        '<colgroup><col style="width:220px">'
                    )
                    for _ in _is_yrs:
                        html += '<col>'
                    html += '</colgroup><thead><tr>'
                    html += f'<th style="{_is_hdr};text-align:left"></th>'
                    for yr in _is_yrs:
                        html += f'<th style="{_is_hdr}">{yr}</th>'
                    html += '</tr></thead><tbody>'
                    return html

                def _is_row(label, vals, fmt='num0', bold=False):
                    nonlocal _is_row_idx
                    bg = 'background:#f9f9fb' if _is_row_idx % 2 == 1 else ''
                    _is_row_idx += 1
                    row_style = f'border-top:1px solid #f0f0f2;{bg}'
                    fw = ';font-weight:700' if bold else ''
                    lbl_style = f'{_is_label}{fw}'
                    cell_style = f'{_is_cell}{fw}'
                    html = f'<tr style="{row_style}"><td style="{lbl_style}">{label}</td>'
                    for v in vals:
                        if v is None:
                            html += f'<td style="{cell_style}">—</td>'
                        elif fmt == 'num0':
                            html += f'<td style="{cell_style}">{v:,.0f}</td>'
                        elif fmt == 'dollar2':
                            html += f'<td style="{cell_style}">${v:.2f}</td>'
                        else:
                            html += f'<td style="{cell_style}">{v}</td>'
                    html += '</tr>'
                    return html

                def _is_separator():
                    cols = _is_n + 1
                    return f'<tr><td colspan="{cols}" style="padding:4px"></td></tr>'

                # Negate expenses for display (show as positive numbers)
                def _neg(vals):
                    return [(-v if v is not None else None) for v in vals]

                # Shares in millions for display
                def _shares_m(vals):
                    return [(round(v / 1e6, 0) if v is not None else None) for v in vals]

                # Revenue & Gross Profit
                st.markdown("**Revenue & Gross Profit** — *in millions*")
                html = _is_table_start()
                html += _is_row('Revenue', istmt['revenue'])
                html += _is_row('Cost of Revenue', istmt['cost_of_revenue'])
                html += _is_row('Gross Profit', istmt['gross_profit'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Operating Expenses & Income
                st.markdown("")
                st.markdown("")
                st.markdown("**Operating Expenses & Income** — *in millions*")
                html = _is_table_start()
                html += _is_row('Research & Development', istmt['rd'])
                html += _is_row('Selling, General & Administrative', istmt['sga'])
                html += _is_row('Other Operating Expenses', istmt['other_operating'])
                html += _is_row('Operating Income', istmt['operating_income'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Non-Operating & Pretax
                st.markdown("")
                st.markdown("")
                st.markdown("**Non-Operating & Pretax Income** — *in millions*")
                html = _is_table_start()
                html += _is_row('Interest Income', istmt['interest_income'])
                html += _is_row('Interest Expense', istmt['interest_expense'])
                for _ex_label, _ex_vals in istmt.get('extras_non_operating', []):
                    html += _is_row(_ex_label, _ex_vals)
                html += _is_row('Other Income / Expense', istmt['other_income'])
                html += _is_row('Pretax Income', istmt['pretax_income'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Net Income
                st.markdown("")
                st.markdown("")
                st.markdown("**Net Income** — *in millions*")
                html = _is_table_start()
                html += _is_row('Tax Provision', istmt['tax_provision'])
                html += _is_row('Net Income', istmt['net_income'], bold=True)
                html += _is_separator()
                html += _is_row('EBITDA', istmt['ebitda'])
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Per Share & Shares
                st.markdown("")
                st.markdown("")
                st.markdown("**Per-Share Data & Shares**")
                html = _is_table_start()
                html += _is_row('Basic EPS', istmt['eps_basic'], fmt='dollar2')
                html += _is_row('Diluted EPS', istmt['eps_diluted'], fmt='dollar2')
                html += _is_separator()
                html += _is_row('Basic Shares', _shares_m(istmt['shares_basic']))
                html += _is_row('Diluted Shares', _shares_m(istmt['shares_diluted']))
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

        # ── Cash Flow Statement ──
        @st.cache_data(ttl=300, show_spinner="Loading cash flow statement...")
        def _cached_cashflow(t):
            return fetch_cashflow_statement(t, n_years=11)

        cflow = _cached_cashflow(ticker)
        _cf_yrs = cflow['years']
        _cf_n = len(_cf_yrs)

        with st.expander("Cash Flow Statement", expanded=False):
            if _cf_n < 1:
                st.info("Insufficient cash flow data")
            else:
                _cf_cell = 'text-align:right;padding:5px 10px;font-size:0.85rem'
                _cf_hdr = 'text-align:right;padding:5px 10px;font-size:0.85rem;color:#86868b'
                _cf_label = 'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap;width:220px;min-width:220px'
                _cf_row_idx = 0

                def _cf_table_start():
                    nonlocal _cf_row_idx
                    _cf_row_idx = 0
                    html = (
                        '<div style="overflow-x:auto">'
                        '<table style="width:100%;border-collapse:collapse;table-layout:fixed">'
                        '<colgroup><col style="width:220px">'
                    )
                    for _ in _cf_yrs:
                        html += '<col>'
                    html += '</colgroup><thead><tr>'
                    html += f'<th style="{_cf_hdr};text-align:left"></th>'
                    for yr in _cf_yrs:
                        html += f'<th style="{_cf_hdr}">{yr}</th>'
                    html += '</tr></thead><tbody>'
                    return html

                def _cf_row(label, vals, bold=False):
                    nonlocal _cf_row_idx
                    bg = 'background:#f9f9fb' if _cf_row_idx % 2 == 1 else ''
                    _cf_row_idx += 1
                    row_style = f'border-top:1px solid #f0f0f2;{bg}'
                    fw = ';font-weight:700' if bold else ''
                    lbl_style = f'{_cf_label}{fw}'
                    cell_style = f'{_cf_cell}{fw}'
                    html = f'<tr style="{row_style}"><td style="{lbl_style}">{label}</td>'
                    for v in vals:
                        if v is None:
                            html += f'<td style="{cell_style}">—</td>'
                        else:
                            html += f'<td style="{cell_style}">{v:,.0f}</td>'
                    html += '</tr>'
                    return html

                def _cf_separator():
                    cols = _cf_n + 1
                    return f'<tr><td colspan="{cols}" style="padding:4px"></td></tr>'

                # Operating Activities
                st.markdown("**Operating Activities** — *in millions*")
                html = _cf_table_start()
                html += _cf_row('Net Income', cflow['net_income_cf'])
                html += _cf_separator()
                html += _cf_row('Depreciation & Amortization', cflow['da_cf'])
                html += _cf_row('Stock-Based Compensation', cflow['sbc'])
                html += _cf_row('Deferred Taxes', cflow['deferred_tax'])
                html += _cf_row('Other Non-Cash Items', cflow['other_noncash'])
                html += _cf_separator()
                html += _cf_row('Change in Receivables', cflow['change_receivables'])
                html += _cf_row('Change in Inventory', cflow['change_inventory'])
                html += _cf_row('Change in Payables', cflow['change_payables'])
                html += _cf_row('Other Working Capital', cflow['change_other_wc'])
                html += _cf_separator()
                html += _cf_row('Cash from Operations', cflow['operating_cf'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Investing Activities
                st.markdown("")
                st.markdown("")
                st.markdown("**Investing Activities** — *in millions*")
                html = _cf_table_start()
                html += _cf_row('Capital Expenditure', cflow['capex'])
                html += _cf_row('Acquisitions', cflow['acquisitions'])
                html += _cf_row('Purchases of Investments', cflow['purchases_investments'])
                html += _cf_row('Sales of Investments', cflow['sales_investments'])
                html += _cf_row('Other Investing', cflow['other_investing'])
                html += _cf_separator()
                html += _cf_row('Cash from Investing', cflow['investing_cf'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Financing Activities
                st.markdown("")
                st.markdown("")
                st.markdown("**Financing Activities** — *in millions*")
                html = _cf_table_start()
                html += _cf_row('Debt Issuance', cflow['debt_issuance'])
                html += _cf_row('Debt Repayment', cflow['debt_repayment'])
                html += _cf_row('Stock Buybacks', cflow['stock_buybacks'])
                html += _cf_row('Dividends Paid', cflow['dividends_paid'])
                html += _cf_row('Stock Issuance', cflow['stock_issuance'])
                html += _cf_row('Other Financing', cflow['other_financing'])
                html += _cf_separator()
                html += _cf_row('Cash from Financing', cflow['financing_cf'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # Cash Position
                st.markdown("")
                st.markdown("")
                st.markdown("**Cash Position** — *in millions*")
                html = _cf_table_start()
                html += _cf_row('Effect of FX', cflow['fx_effect'])
                html += _cf_row('Net Change in Cash', cflow['net_change_cash'])
                html += _cf_separator()
                html += _cf_row('Beginning Cash', cflow['beginning_cash'])
                html += _cf_row('Ending Cash', cflow['ending_cash'], bold=True)
                html += _cf_separator()
                html += _cf_row('Free Cash Flow', cflow['fcf'], bold=True)
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

        if _n < 2:
            st.info("Insufficient data for Key Ratios (need 2+ years)")
        else:
            # Pre-compute references
            rev = fund['revenue']
            oi = fund['operating_income']
            ni = fund['net_income']
            eq = fund['total_equity']
            debt = fund['total_debt']
            cash_v = fund['cash']
            ta = fund['total_assets']
            cl = fund['current_liabilities']
            gw = fund['goodwill']
            intang = fund['intangibles']
            ppe_v = fund['ppe']
            da_v = fund['da']
            gp = fund['gross_profit']
            eps_v = fund['eps']
            dps = fund['dividends_per_share']
            shares = fund['shares']
            cfo_v = fund['cfo']
            capex_v = fund['capex']
            fcf_v = fund['fcf']
            tp = fund['tax_provision']
            pti = fund['pretax_income']

            with st.expander("Key Ratios", expanded=False):
                # ── 1. Returns ──
                st.markdown("**Returns**")
                roa = [ni[i] / ta[i] * 100 if ni[i] is not None and ta[i] else None for i in range(_n)]
                roe = [ni[i] / eq[i] * 100 if ni[i] is not None and eq[i] else None for i in range(_n)]
                roic = []
                for i in range(_n):
                    if oi[i] is not None and pti[i] and pti[i] != 0:
                        tax_rate = (tp[i] / pti[i]) if tp[i] is not None else 0.21
                        nopat = oi[i] * (1 - tax_rate)
                        ic = (eq[i] or 0) + (debt[i] or 0) - (cash_v[i] or 0)
                        roic.append(nopat / ic * 100 if ic > 0 else None)
                    else:
                        roic.append(None)
                roce = [oi[i] / (ta[i] - (cl[i] or 0)) * 100
                        if oi[i] is not None and ta[i] and (ta[i] - (cl[i] or 0)) > 0
                        else None for i in range(_n)]
                rotc = [oi[i] / (ta[i] - (cl[i] or 0) - (gw[i] or 0) - (intang[i] or 0)) * 100
                        if oi[i] is not None and ta[i] and (ta[i] - (cl[i] or 0) - (gw[i] or 0) - (intang[i] or 0)) > 0
                        else None for i in range(_n)]

                html = _kr_table_start()
                html += _kr_row('Return on Assets', roa, 'pct1')
                html += _kr_row('Return on Equity', roe, 'pct1')
                html += _kr_row('Return on Invested Capital', roic, 'pct1')
                html += _kr_row('Return on Capital Employed', roce, 'pct1')
                html += _kr_row('Return on Tangible Capital', rotc, 'pct1')
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # ── 2. Margins ──
                st.markdown("")
                st.markdown("")
                st.markdown("**Margins as % of Revenue**")
                gross_m = [gp[i] / rev[i] * 100 if gp[i] is not None and rev[i] else None for i in range(_n)]
                ebitda_m = [(oi[i] + (da_v[i] or 0)) / rev[i] * 100
                            if oi[i] is not None and rev[i] else None for i in range(_n)]
                op_m = [oi[i] / rev[i] * 100 if oi[i] is not None and rev[i] else None for i in range(_n)]
                pretax_m = [pti[i] / rev[i] * 100 if pti[i] is not None and rev[i] else None for i in range(_n)]
                net_m = [ni[i] / rev[i] * 100 if ni[i] is not None and rev[i] else None for i in range(_n)]
                fcf_m = [fcf_v[i] / rev[i] * 100 if fcf_v[i] is not None and rev[i] else None for i in range(_n)]

                html = _kr_table_start()
                html += _kr_row('Gross Margin', gross_m, 'pct1')
                html += _kr_row('EBITDA Margin', ebitda_m, 'pct1')
                html += _kr_row('Operating Margin', op_m, 'pct1')
                html += _kr_row('Pretax Margin', pretax_m, 'pct1')
                html += _kr_row('Net Margin', net_m, 'pct1')
                html += _kr_row('FCF Margin', fcf_m, 'pct1')
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # ── 3. Capital Structure ──
                st.markdown("")
                st.markdown("")
                st.markdown("**Capital Structure**")
                a2e = [ta[i] / eq[i] if ta[i] and eq[i] and eq[i] != 0 else None for i in range(_n)]
                e2a = [eq[i] / ta[i] if eq[i] is not None and ta[i] else None for i in range(_n)]
                d2e = [(debt[i] or 0) / eq[i] if eq[i] and eq[i] != 0 else None for i in range(_n)]
                d2a = [(debt[i] or 0) / ta[i] if ta[i] else None for i in range(_n)]

                html = _kr_table_start()
                html += _kr_row('Assets to Equity', a2e, 'dec1')
                html += _kr_row('Equity to Assets', e2a, 'dec1')
                html += _kr_row('Debt to Equity', d2e, 'dec1')
                html += _kr_row('Debt to Assets', d2a, 'dec1')
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # ── 4. YoY Growth ──
                st.markdown("")
                st.markdown("")
                st.markdown("**Year-Over-Year Growth**")
                ebitda_abs = [(oi[i] or 0) + (da_v[i] or 0) if oi[i] is not None else None for i in range(_n)]

                html = _kr_table_start()
                html += _kr_row('Revenue', _kr_pct_change(rev))
                html += _kr_row('Gross Profit', _kr_pct_change(gp))
                html += _kr_row('EBITDA', _kr_pct_change(ebitda_abs))
                html += _kr_row('Operating Income', _kr_pct_change(oi))
                html += _kr_row('Pretax Income', _kr_pct_change(pti))
                html += _kr_row('Net Income', _kr_pct_change(ni))
                html += _kr_row('Diluted EPS', _kr_pct_change(eps_v))
                html += _kr_separator()
                html += _kr_row('Diluted Shares', _kr_pct_change(shares))
                html += _kr_separator()
                html += _kr_row('PP&E', _kr_pct_change(ppe_v))
                html += _kr_row('Total Assets', _kr_pct_change(ta))
                html += _kr_row('Equity', _kr_pct_change(eq))
                html += _kr_separator()
                html += _kr_row('Cash from Operations', _kr_pct_change(cfo_v))
                html += _kr_row('Capital Expenditures', _kr_pct_change(capex_v))
                html += _kr_row('Free Cash Flow', _kr_pct_change(fcf_v))
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # ── 5. Valuation Metrics ──
                st.markdown("")
                st.markdown("")
                st.markdown("**Valuation Metrics** — *in millions*")
                if _hist_prices:
                    mkt_cap = []
                    pe = []
                    pb = []
                    ps = []
                    for i in range(_n):
                        yr = _yrs[i]
                        price = _hist_prices.get(yr)
                        sh = shares[i]
                        if price and sh and sh > 0:
                            mc = price * sh / 1e6  # to millions
                            mkt_cap.append(round(mc, 0))
                            pe.append(price / eps_v[i] if eps_v[i] and eps_v[i] > 0 else None)
                            bvps = eq[i] * 1e6 / sh if eq[i] else None
                            pb.append(price / bvps if bvps and bvps > 0 else None)
                            rps = rev[i] * 1e6 / sh if rev[i] else None
                            ps.append(price / rps if rps and rps > 0 else None)
                        else:
                            mkt_cap.append(None)
                            pe.append(None)
                            pb.append(None)
                            ps.append(None)

                    html = _kr_table_start()
                    html += _kr_row('Market Capitalization', mkt_cap, 'num0')
                    html += _kr_row('Price-to-Earnings', pe, 'dec2')
                    html += _kr_row('Price-to-Book', pb, 'dec2')
                    html += _kr_row('Price-to-Sales', ps, 'dec2')
                    html += '</tbody></table></div>'
                    st.markdown(html, unsafe_allow_html=True)
                else:
                    st.info("Insufficient data for Valuation Metrics")

                # ── 6. Dividends ──
                st.markdown("")
                st.markdown("")
                st.markdown("**Dividends**")
                has_dividends = any(d is not None and d > 0 for d in dps)
                if has_dividends:
                    payout = [dps[i] / eps_v[i] * 100 if dps[i] is not None and eps_v[i] and eps_v[i] > 0 else None
                              for i in range(_n)]
                    html = _kr_table_start()
                    html += _kr_row('Dividends per Share', dps, 'dollar2')
                    html += _kr_row('Payout Ratio', payout, 'pct1')
                    html += '</tbody></table></div>'
                    st.markdown(html, unsafe_allow_html=True)
                else:
                    st.caption("No dividend history available")

                # ── 7. Per-Share Items ──
                st.markdown("")
                st.markdown("")
                st.markdown("**Per-Share Items**")
                def _per_share(vals):
                    return [vals[i] * 1e6 / shares[i] if vals[i] is not None and shares[i] and shares[i] > 0 else None
                            for i in range(_n)]

                rev_ps = _per_share(rev)
                ebitda_ps = _per_share(ebitda_abs)
                oi_ps = _per_share(oi)
                fcf_ps = _per_share(fcf_v)
                bv_ps = _per_share(eq)
                tbv = [(eq[i] or 0) - (gw[i] or 0) - (intang[i] or 0) if eq[i] is not None else None for i in range(_n)]
                tbv_ps = _per_share(tbv)

                html = _kr_table_start()
                html += _kr_row('Revenue', rev_ps, 'dec2')
                html += _kr_row('EBITDA', ebitda_ps, 'dec2')
                html += _kr_row('Operating Income', oi_ps, 'dec2')
                html += _kr_row('Diluted EPS', eps_v, 'dec2')
                html += _kr_row('Free Cash Flow', fcf_ps, 'dec2')
                html += _kr_row('Book Value', bv_ps, 'dec2')
                html += _kr_row('Tangible Book Value', tbv_ps, 'dec2')
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

                # ── 8. Supplementary Items ──
                st.markdown("")
                st.markdown("")
                st.markdown("**Supplementary Items** — *in millions*")
                tbv_abs = tbv

                html = _kr_table_start()
                html += _kr_row('Free Cash Flow', fcf_v, 'num0')
                html += _kr_row('Book Value', eq, 'num0')
                html += _kr_row('Tangible Book Value', tbv_abs, 'num0')
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)

    # ── Action buttons ──
    st.markdown("---")
    btn1, btn2, btn3 = st.columns(3)
    with btn1:
        if st.button("Save", key="ed_save", use_container_width=True, type="primary"):
            save_config(ticker, cfg)
            st.success(f"{ticker} saved")
            st.rerun()
    with btn2:
        @st.cache_data(ttl=60, show_spinner=False)
        def _cached_excel(ticker_key, cfg_json):
            import json as _json
            return _build_excel_bytes(_json.loads(cfg_json))

        import json as _json
        excel_bytes = _cached_excel(ticker, _json.dumps(cfg, sort_keys=True, default=str))
        st.download_button(
            label="Download Excel",
            data=excel_bytes,
            file_name=f"{ticker}_DCF.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="ed_dl",
            type="primary",
        )
    with btn3:
        if st.button("Remove from Watchlist", key="ed_remove", use_container_width=True, type="primary"):
            remove_from_watchlist(ticker)
            del st.query_params["edit"]
            st.rerun()


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
        ["Portfolio", "Watchlist", "Wheel Cost Basis", "Results"],
        label_visibility="collapsed",
    )
    st.markdown("---")

    if page in ("Portfolio", "Wheel Cost Basis", "Results"):
        st.markdown("### Tastytrade")
        if st.button("Refresh Data", use_container_width=True, type="primary"):
            st.session_state.pop("portfolio_data", None)
            st.session_state.pop("portfolio_account", None)
            st.session_state.pop("portfolio_prices", None)
            st.session_state.pop("net_liq_all", None)
            st.session_state.pop("yearly_transfers", None)
            st.session_state.pop("benchmark_returns", None)
            st.session_state.pop("portfolio_fetched_at", None)
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
    """Fetch and enrich portfolio data (cached in session_state, auto-refreshes every 5 min)."""
    # Auto-refresh after 5 minutes
    fetched_at = st.session_state.get("portfolio_fetched_at", 0)
    if "portfolio_data" in st.session_state and time.time() - fetched_at > 300:
        for k in ["portfolio_data", "portfolio_account", "portfolio_prices",
                   "net_liq_all", "yearly_transfers", "benchmark_returns"]:
            st.session_state.pop(k, None)
        for k in [k for k in st.session_state if k.startswith("net_liq_")]:
            st.session_state.pop(k, None)

    if "portfolio_data" not in st.session_state:
        with st.spinner("Fetching transactions from Tastytrade..."):
            try:
                cost_basis, acct = fetch_portfolio_data()
                st.session_state.portfolio_data = cost_basis
                st.session_state.portfolio_account = acct
                st.session_state.portfolio_fetched_at = time.time()
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
        active_tickers = [
            t for t, d in cost_basis.items()
            if d["shares_held"] > 0 or _has_open_options(d)
        ]
        if active_tickers:
            with st.spinner("Fetching current prices..."):
                st.session_state.portfolio_prices = fetch_current_prices(active_tickers)
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
        elif price_data:
            # Options-only position — store underlying price for reference
            data["current_price"] = price_data["price"]
            data["previous_close"] = price_data.get("previousClose") or price_data["price"]
            data["market_value"] = 0.0
            data["total_pl_real"] = data["total_pl"]
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


def _has_open_options(data):
    """Check if a ticker has any open option positions."""
    return bool(_find_open_options(data.get("trades", [])))


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
#  WATCHLIST PAGE — Track multiple DCF valuations
# ══════════════════════════════════════════════════════

if page == "Watchlist":

    st.markdown(
        "<style>.block-container { max-width: 1400px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Route: editor or overview ──
    edit_ticker = st.query_params.get("edit")
    if edit_ticker:
        _dcf_editor(edit_ticker.upper())
    else:
        _watchlist_overview()


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

    held = {
        t: d for t, d in cost_basis.items()
        if d["shares_held"] > 0 or _has_open_options(d)
    }

    if not held:
        st.info("No active positions.")
        st.stop()

    held_tickers = list(held.keys())

    # ── Margin / Buying Power (with integrated simulator) ──
    @st.cache_data(ttl=60, show_spinner=False)
    def _cached_account_balances():
        return fetch_account_balances()

    @st.cache_data(ttl=120, show_spinner=False)
    def _cached_margin_requirements():
        return fetch_margin_requirements()

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

        # ── Compute assignment exposure from open short options ──
        total_assignment = 0.0
        total_assign_margin = 0.0
        assignment_entries = []
        portfolio = st.session_state.get("portfolio_data", {})
        prices_cache = st.session_state.get("portfolio_prices", {})

        for ticker, data in portfolio.items():
            opts = _find_open_options(data["trades"])
            for opt in opts:
                if opt["cp"] == "P":
                    shares = opt["quantity"] * 100
                    exposure = opt["strike"] * shares
                    # Fetch margin requirement for holding assigned shares
                    _amk = f"_assign_margin_{ticker.upper()}_{shares}"
                    if _amk not in st.session_state:
                        _amr = fetch_margin_for_position(ticker, shares)
                        st.session_state[_amk] = _amr
                    _amr = st.session_state[_amk]
                    margin = abs(_amr["change_in_margin"]) if _amr else exposure * 0.50
                    total_assignment += exposure
                    total_assign_margin += margin
                    _apct = margin / exposure * 100 if exposure > 0 else 0
                    assignment_entries.append(f'{opt["quantity"]}x {ticker} ${opt["strike"]:.0f}P = ${margin:,.0f} ({_apct:.0f}%)')
                elif opt["cp"] == "C" and opt["type"] != "CC":
                    # Naked short calls only — covered calls don't need extra margin
                    shares = opt["quantity"] * 100
                    cur_price = prices_cache.get(ticker, {}).get("price", 0) if prices_cache else 0
                    exposure = cur_price * shares
                    _amk = f"_assign_margin_{ticker.upper()}_{shares}"
                    if _amk not in st.session_state:
                        _amr = fetch_margin_for_position(ticker, shares)
                        st.session_state[_amk] = _amr
                    _amr = st.session_state[_amk]
                    margin = abs(_amr["change_in_margin"]) if _amr else exposure * 0.50
                    total_assignment += exposure
                    total_assign_margin += margin
                    _apct = margin / exposure * 100 if exposure > 0 else 0
                    assignment_entries.append(f'{opt["quantity"]}x {ticker} ${opt["strike"]:.0f}C = ${margin:,.0f} ({_apct:.0f}%)')

        # ── Compute simulation impact from session state ──
        if "sim_rows" not in st.session_state:
            st.session_state["sim_rows"] = 1

        total_sim_cost = 0.0
        total_sim_margin = 0.0
        sim_entries = []

        for i in range(st.session_state["sim_rows"]):
            ticker = st.session_state.get(f"sim_tick_{i}", "")
            shares = st.session_state.get(f"sim_sh_{i}", 100)
            price = st.session_state.get(f"sim_pr_{i}", 0.0)
            if ticker and price > 0 and shares > 0:
                cost = price * shares
                _margin_key = f"_sim_margin_{ticker.upper()}_{shares}"
                if _margin_key not in st.session_state:
                    _mr = fetch_margin_for_position(ticker, shares)
                    st.session_state[_margin_key] = _mr
                _mr = st.session_state[_margin_key]
                margin = abs(_mr["change_in_margin"]) if _mr else cost * 0.50
                total_sim_cost += cost
                total_sim_margin += margin
                _pct = margin / cost * 100 if cost > 0 else 0
                sim_entries.append(f'{shares}x {ticker.upper()} @ ${price:,.2f} ({_pct:.0f}%)')

        # ── Compute final values (base + simulation + assignment) ──
        show_used = used_bp + total_sim_margin + total_assign_margin
        show_bp = bp - total_sim_margin - total_assign_margin
        show_excess = maint_excess - total_sim_margin - total_assign_margin
        show_usage = (show_used / total_bp * 100) if total_bp > 0 else 0
        show_drop = (show_excess / net_liq * 100) if net_liq > 0 else 0

        # Margin call line: point on bar where maintenance excess = 0
        margin_call_pct = ((show_used + show_excess) / total_bp * 100) if total_bp > 0 else 100

        if show_usage < 50:
            bar_color = "#81b29a"
            status = "Cash"
        elif show_usage < 75:
            bar_color = "#f2cc8f"
            status = "Margin"
        else:
            bar_color = "#e07a5f"
            status = "High Leverage"

        # Simulation subtitle
        sim_note = ""
        if total_sim_cost > 0:
            sim_label = " + ".join(sim_entries)
            sim_note = (
                f'<div style="margin-bottom:12px;padding:8px 12px;background:#f7f8fa;border-radius:8px;'
                f'border:1px dashed #d2d2d7;font-size:0.85rem">'
                f'<span style="color:#86868b">Simulating: </span>'
                f'<b>{sim_label}</b>'
                f'<span style="color:#86868b"> = ${total_sim_cost:,.0f} — margin ${total_sim_margin:,.0f}</span>'
                f'</div>'
            )

        # Assignment risk info block
        assign_note = ""
        if total_assignment > 0:
            assign_label = " | ".join(assignment_entries)
            assign_note = (
                f'<div style="margin-bottom:12px;padding:8px 12px;background:#f7f8fa;border-radius:8px;'
                f'border:1px dashed #d2d2d7;font-size:0.85rem">'
                f'<span style="color:#86868b">Assignment Risk: </span>'
                f'<b>{assign_label}</b>'
                f'<span style="color:#86868b"> — margin ${total_assign_margin:,.0f}</span>'
                f'</div>'
            )

        st.markdown(
            f'<div class="hero-card">'
            f'<h4>Margin Overview</h4>'
            f'{assign_note}'
            f'{sim_note}'
            f'<div style="margin:16px 0">'
            f'  <div style="display:flex;justify-content:space-between;margin-bottom:6px">'
            f'    <span style="font-size:0.85rem;color:#86868b">BP Used: ${show_used:,.0f} / ${total_bp:,.0f}</span>'
            f'    <span style="font-size:0.85rem;font-weight:600;color:{bar_color}">{status} ({show_usage:.0f}%) · <span style="color:#e07a5f">MC at {margin_call_pct:.0f}%</span></span>'
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
            + (f'<span class="stat-pill">Assignment Margin <b>${total_assign_margin:,.0f}</b></span>' if total_assign_margin > 0 else '') +
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── Simulator inputs (below the overview bar) ──
        st.markdown('<p style="font-weight:600;margin-top:24px;margin-bottom:4px;font-size:0.9rem;color:#86868b;text-transform:uppercase;letter-spacing:0.03em">Simulate Positions</p>', unsafe_allow_html=True)

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

        _, btn_add, btn_reset, _ = st.columns([1, 1, 1, 1])
        with btn_add:
            if st.button("Add row", key="sim_add_row", type="primary", use_container_width=True):
                st.session_state["sim_rows"] += 1
                st.rerun()
        with btn_reset:
            if st.button("Reset", key="sim_reset", type="primary", use_container_width=True):
                for i in range(st.session_state["sim_rows"]):
                    st.session_state.pop(f"sim_tick_{i}", None)
                    st.session_state.pop(f"sim_sh_{i}", None)
                    st.session_state.pop(f"sim_pr_{i}", None)
                st.session_state["sim_rows"] = 1
                st.rerun()

        # Store balance for other cards
        st.session_state["_margin_cash"] = bal["cash_balance"]
        st.session_state["_net_liq"] = bal["net_liquidating_value"]

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
            elif price_data:
                data["current_price"] = price_data["price"]
                data["previous_close"] = price_data.get("previousClose") or price_data["price"]

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
                    "Premie", "Days", "Weight", "Margin", "Margin %"]
        default_cols = ["Shares", "Buy Price", "Cost/Share", "Current Price", "Day %",
                        "Mkt Value", "Unrealized P/L", "Return %", "Weight"]
        sort_options = ["Ticker", "Weight", "Day %", "Return %", "Unrealized P/L", "Mkt Value", "Ann. %", "Margin %"]

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

        # ── Per-position margin requirements ──
        try:
            _margin_reqs = _cached_margin_requirements()
        except Exception:
            _margin_reqs = {}

        for row in rows:
            row["Weight"] = row["Mkt Value"] / total_value * 100 if total_value else 0.0
            _mr = _margin_reqs.get(row["Ticker"], {})
            row["Margin"] = _mr.get("margin_requirement", 0)
            _mv = row["Mkt Value"]
            row["Margin %"] = (row["Margin"] / _mv * 100) if _mv > 0 else 0

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
            if col == "Margin":
                return f"${val:,.0f}", cls
            if col == "Margin %":
                return f"{val:.0f}%", cls
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
    st.markdown("<br>", unsafe_allow_html=True)
    with st.container(key="margin_block"):
        _margin_overview()

    # ── Portfolio Greeks, BWD & Margin Interest ──
    gk = None
    try:
        from concurrent.futures import ThreadPoolExecutor
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(fetch_portfolio_greeks)
        try:
            gk = future.result(timeout=15)
        except Exception:
            gk = None
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        gk = None

    bwd = None
    try:
        bwd = fetch_beta_weighted_delta()
    except Exception:
        bwd = None

    mi = None
    try:
        mi = fetch_margin_interest()
    except Exception:
        mi = None

    cash = st.session_state.get("_margin_cash", 0.0)
    debt = abs(cash) if cash < 0 else 0.0
    has_greeks = gk and gk["positions"]
    has_bwd = bwd and bwd["spy_price"] > 0
    has_interest = debt > 0 or (mi and mi["total"] < 0)

    _cards = []
    if has_greeks:
        _cards.append("greeks")
    if has_bwd:
        _cards.append("bwd")
    if has_interest:
        _cards.append("interest")

    if _cards:
        # Build card HTML fragments
        _card_htmls = []

        if has_greeks:
            tot = gk["totals"]
            theta = tot["theta"]
            delta = tot["delta"]
            vega = tot["vega"]
            theta_color = "#81b29a" if theta >= 0 else "#e07a5f"
            _card_htmls.append(
                f'<div class="hero-card">'
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
                f'</div>'
            )

        if has_bwd:
            _bwd_total = bwd["portfolio_bwd"]
            _spy_p = bwd["spy_price"]
            _dollar_1pct = bwd["dollar_per_1pct"]
            _nlv = st.session_state.get("_net_liq", 0)
            _port_pct = (_dollar_1pct / _nlv * 100) if _nlv > 0 else 0
            _pct_color = "#e07a5f" if _port_pct > 0 else "#81b29a"

            _td = 'padding:4px 8px;border-bottom:1px solid rgba(0,0,0,0.06)'
            _bwd_rows = ""
            for bp in bwd["positions"]:
                _bp_loss = -bp["dollar_per_1pct"]
                _bp_color = "#e07a5f" if _bp_loss < 0 else "#81b29a"
                _bwd_rows += (
                    f'<tr>'
                    f'<td style="{_td}">{bp["ticker"]}</td>'
                    f'<td style="{_td};text-align:right">{bp["beta"]:.2f}</td>'
                    f'<td style="{_td};text-align:right">{bp["bwd"]:+,.1f}</td>'
                    f'<td style="{_td};text-align:right;color:{_bp_color}">${_bp_loss:+,.0f}</td>'
                    f'</tr>'
                )
            _card_htmls.append(
                f'<div class="hero-card">'
                f'<h4>Beta-Weighted Delta</h4>'
                f'<div style="text-align:center;margin-bottom:16px">'
                f'  <span style="font-size:1.6rem;font-weight:700;color:{_pct_color}">-{_port_pct:.2f}%</span>'
                f'  <span style="font-size:0.85rem;color:#86868b">if S&P 500 drops 1%</span>'
                f'</div>'
                f'<div class="stat-row">'
                f'<span class="stat-pill">P/L <b style="color:{_pct_color}">-${abs(_dollar_1pct):,.0f}</b></span>'
                f'<span class="stat-pill">BWD <b>{_bwd_total:+,.1f}</b></span>'
                f'<span class="stat-pill">SPY <b>${_spy_p:,.0f}</b></span>'
                f'</div>'
                f'<details style="margin-top:8px">'
                f'<summary style="cursor:pointer;font-size:0.8rem;color:#86868b">Breakdown</summary>'
                f'<table style="width:100%;border-collapse:collapse;font-size:0.8rem;margin-top:6px">'
                f'<thead><tr style="color:#86868b;font-size:0.7rem;text-transform:uppercase">'
                f'<th style="text-align:left;padding:3px 8px;border-bottom:1px solid #d2d2d7">Ticker</th>'
                f'<th style="text-align:right;padding:3px 8px;border-bottom:1px solid #d2d2d7">Beta</th>'
                f'<th style="text-align:right;padding:3px 8px;border-bottom:1px solid #d2d2d7">BWD</th>'
                f'<th style="text-align:right;padding:3px 8px;border-bottom:1px solid #d2d2d7">P/L</th>'
                f'</tr></thead>'
                f'<tbody>{_bwd_rows}</tbody>'
                f'</table>'
                f'</details>'
                f'</div>'
            )

        if has_interest:
            cur_mo = abs(mi["current_month"]) if mi else 0
            ytd = abs(mi["ytd"]) if mi else 0
            total_int = abs(mi["total"]) if mi else 0
            _card_htmls.append(
                f'<div class="hero-card">'
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
                f'</div>'
            )

        # Render as single HTML grid
        st.markdown(
            f'<div class="greeks-grid">{"".join(_card_htmls)}</div>',
            unsafe_allow_html=True,
        )

    # ── Portfolio Exposure (loads independently via fragment) ──
    @st.cache_data(ttl=86400, show_spinner=False)
    def _cached_ticker_profiles(tickers_tuple):
        return fetch_ticker_profiles(list(tickers_tuple))

    @st.fragment
    def _portfolio_exposure():
        st.markdown("<h4 style='text-align:center'>Portfolio Allocation</h4>", unsafe_allow_html=True)
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

    with st.container(key="allocation_block"):
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
        """Active = shares held or any open option positions."""
        if data["shares_held"] > 0:
            return True
        return _has_open_options(data)

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

        with st.container(key=f"wheel_card_{ticker}"):
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
    active_positions = sum(
        1 for d in cost_basis.values()
        if d["shares_held"] > 0 or _has_open_options(d)
    )

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

    with st.container(key="results_hero"):
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
      period_map = {"1M": "1m", "3M": "3m", "6M": "6m", "YTD": "ytd", "1Y": "1y", "All": "all"}
      selected_period = st.pills(
          "Period", options=list(period_map.keys()), default="YTD",
      )
      time_back = period_map[selected_period]
      # YTD uses 1y data filtered client-side to Jan 1 of current year
      api_time_back = "1y" if time_back == "ytd" else time_back
      cache_key = f"net_liq_{api_time_back}"
      if cache_key not in st.session_state:
          try:
              with st.spinner("Loading net liq history..."):
                  st.session_state[cache_key] = fetch_net_liq_history(api_time_back)
          except Exception:
              st.session_state[cache_key] = None

      net_liq_data = st.session_state[cache_key]
      if net_liq_data:
          df_liq = pd.DataFrame(net_liq_data)
          df_liq["time"] = pd.to_datetime(df_liq["time"])
          df_liq = df_liq.set_index("time")
          if time_back == "ytd":
              df_liq = df_liq[df_liq.index >= f"{pd.Timestamp.now().year}-01-01"]
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
        f'<div class="performer-grid" style="margin:24px 0">'
        f'<div>'
        f'<div class="section-title-bar">Top Performers</div>'
        f'<div class="portfolio-cards">{_performer_cards(top5)}</div>'
        f'</div>'
        f'<div>'
        f'<div class="section-title-bar" style="border-left-color:#e07a5f">Bottom Performers</div>'
        f'<div class="portfolio-cards">{_performer_cards(bottom5)}</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.container(key="cumulative_block"):
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
                f'<div class="section-title-bar">Returns &nbsp;<span style="font-weight:400;font-size:0.85rem;color:#86868b">'
                f'Cumulative: <span class="pf-val{total_ret_cls}" style="font-size:0.85rem">{total_return:+.1f}%</span>'
                f'</span></div>'
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
                _yr_border = "#81b29a" if yr_ret >= 0 else "#e07a5f"
                returns_html += (
                    f'<details class="portfolio-card" style="border-left:3px solid {_yr_border};padding:12px 16px;margin-bottom:8px;display:block">'
                    f'<summary style="cursor:pointer;font-weight:600;color:#1d1d1f;list-style:none">'
                    f'{yr} — <span style="color:{yr_color}">{yr_ret:+.1f}%</span></summary>'
                    f'{mo_cards}'
                    f'</details>'
                )
            st.markdown(returns_html, unsafe_allow_html=True)

        with col_dep:
            if has_deposits:
                dep_html = (
                    f'<div class="section-title-bar">Deposits &nbsp;<span style="font-weight:400;font-size:0.85rem;color:#86868b">'
                    f'Total: <span class="pf-val{total_dep_cls}" style="font-size:0.85rem">${total_deposited:+,.0f}</span>'
                    f'</span></div>'
                )
                for yr, yr_data in sorted_transfers:
                    amount = yr_data["total"]
                    months = yr_data.get("months", {})
                    dep_color = "#81b29a" if amount >= 0 else "#e07a5f"
                    _dep_border = "#81b29a" if amount >= 0 else "#e07a5f"
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
                        f'<details class="portfolio-card" style="border-left:3px solid {_dep_border};padding:12px 16px;margin-bottom:8px;display:block">'
                        f'<summary style="cursor:pointer;font-weight:600;color:#1d1d1f;list-style:none">'
                        f'{yr} — <span style="color:{dep_color}">${amount:+,.0f}</span></summary>'
                        f'{month_cards}'
                        f'</details>'
                    )
                st.markdown(dep_html, unsafe_allow_html=True)

    st.markdown("")

    # ── Per-ticker cards (sorted by Total P/L, best first) ──
    with st.expander(f"All Positions ({len(sorted_tickers)})"):
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
