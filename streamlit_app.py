"""
Streamlit web app for DCF Valuation Model Generator.
Wraps gather_data.py + dcf_template.py in a simple web UI.
"""

import streamlit as st
import pandas as pd
import io
import os
import sys
import contextlib
import tempfile
import time

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

# ── Page config ──
st.set_page_config(
    page_title="DCF Valuation Builder",
    page_icon="\U0001f4ca",
    layout="centered",
)

# ── Custom CSS ──
st.markdown("""
<style>
    /* Tighter metric cards */
    [data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 12px 16px;
    }
    [data-testid="stMetric"] label {
        color: rgba(250, 250, 250, 0.5);
    }

    /* Success banner */
    .success-banner {
        background: linear-gradient(135deg, #1a472a 0%, #0e2f1a 100%);
        border: 1px solid #2d6a3e;
        border-radius: 10px;
        padding: 20px 24px;
        margin: 16px 0;
        text-align: center;
    }
    .success-banner h2 {
        color: #4ade80;
        margin: 0 0 4px 0;
        font-size: 1.3rem;
    }
    .success-banner p {
        color: rgba(250, 250, 250, 0.7);
        margin: 0;
        font-size: 0.9rem;
    }

    /* Chart container */
    .chart-label {
        color: rgba(250, 250, 250, 0.5);
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-bottom: 4px;
    }

    /* Peer table */
    .peer-table th {
        text-align: right !important;
    }

    /* Hide streamlit branding */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}

    /* Form styling */
    .stForm {
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 10px !important;
        padding: 20px !important;
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
#  HEADER
# ══════════════════════════════════════════════════════

st.markdown("## DCF Valuation Model Generator")
st.markdown(
    "Enter a stock ticker to generate a full **Discounted Cash Flow** analysis. "
    "The model pulls data from SEC filings, calculates WACC using Damodaran methodology, "
    "projects 10 years of cash flows, and outputs a professional Excel workbook."
)

st.markdown("")

# ── Sidebar ──
with st.sidebar:
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
        "<small style='color: rgba(250,250,250,0.3)'>Data: SEC EDGAR, Yahoo Finance, Damodaran<br>"
        "Methodology: Damodaran DCF with Sales-to-Capital reinvestment</small>",
        unsafe_allow_html=True,
    )

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
            st.bar_chart(df_growth, color="#4A90D9", height=200)

        with col_m:
            st.markdown('<p class="chart-label">Operating Margin</p>', unsafe_allow_html=True)
            df_margin = pd.DataFrame({
                "Year": proj_years,
                "Margin": [m * 100 for m in margins],
            }).set_index("Year")
            st.bar_chart(df_margin, color="#50C878", height=200)

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
