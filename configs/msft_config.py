"""
Microsoft Corporation (MSFT) — DCF Configuration
==================================================
Last updated: February 16, 2026
Source: FY2020-FY2025 10-K filings + market data

Usage:
    exec(open('/mnt/skills/user/dcf-excel-template/dcf_template.py').read())
    exec(open('/mnt/project/msft_config.py').read())
    build_dcf_model(cfg, '/mnt/user-data/outputs/MSFT_DCF_Model.xlsx')
    # Then recalc:
    # python /mnt/skills/public/xlsx/scripts/recalc.py /mnt/user-data/outputs/MSFT_DCF_Model.xlsx

All values in $M unless noted. Blue cells in Excel = editable assumptions.
"""

cfg = {
    # ──────────────────────────────────────────────
    # COMPANY INFO
    # ──────────────────────────────────────────────
    'company': 'Microsoft Corporation',
    'ticker': 'MSFT',
    'valuation_date': 'Feb 2026',

    # ──────────────────────────────────────────────
    # MARKET DATA (update these when rebuilding)
    # ──────────────────────────────────────────────
    'stock_price': 401.32,                # MSFT close Feb 13, 2026
    'equity_market_value': 2_984_000,     # Market cap ($M)
    'debt_market_value': 53_000,          # Total debt (LT + leases + ST)

    # ──────────────────────────────────────────────
    # WACC INPUTS
    # ──────────────────────────────────────────────
    'risk_free_rate': 0.0404,             # US 10Y Treasury (Feb 13, 2026)
    'erp': 0.047,                         # Equity Risk Premium (Damodaran Jan 2026)
    'credit_spread': 0.006,               # AAA-rated, minimal spread
    'tax_rate': 0.18,                     # Effective tax rate (FY2025 10-K)

    # Damodaran sector betas (weighted by revenue)
    'sector_betas': [
        ('Software', 1.05, 0.65),         # (sector, unlevered beta, revenue weight)
        ('Cloud/Infra', 0.90, 0.35),
    ],

    # Debt breakdown for bridge
    'debt_breakdown': [
        ('Long-Term Debt', 42_000),
        ('Operating Leases', 8_000),
        ('Short-Term Debt', 3_000),
    ],

    # ──────────────────────────────────────────────
    # DCF ASSUMPTIONS (the main levers)
    # ──────────────────────────────────────────────
    'base_year': 2025,
    'base_revenue': 277_600,              # FY2025 revenue
    'base_oi': 127_600,                   # FY2025 operating income
    'base_op_margin': 0.455,              # FY2025 operating margin

    # 10-year projection: decelerating growth
    'revenue_growth': [
        0.13,   # FY2026 — AI/cloud momentum
        0.12,   # FY2027
        0.11,   # FY2028
        0.10,   # FY2029
        0.09,   # FY2030
        0.08,   # FY2031
        0.07,   # FY2032
        0.06,   # FY2033
        0.05,   # FY2034
        0.04,   # FY2035
    ],

    # Operating margins: gradual compression from capex
    'op_margins': [
        0.455,  # FY2026
        0.450,  # FY2027
        0.445,  # FY2028
        0.440,  # FY2029
        0.435,  # FY2030
        0.430,  # FY2031
        0.425,  # FY2032
        0.420,  # FY2033
        0.415,  # FY2034
        0.410,  # FY2035
    ],

    'terminal_growth': 0.03,              # Long-term GDP + inflation
    'terminal_margin': 0.40,              # Steady-state operating margin
    'sales_to_capital': 0.65,             # Reinvestment efficiency
    'sbc_pct': 0.038,                     # SBC as % of revenue (~$10.5B on $277B)

    # ──────────────────────────────────────────────
    # SHARES & BUYBACKS
    # ──────────────────────────────────────────────
    'shares_outstanding': 7_432,          # Diluted shares (M), FY2025
    'buyback_rate': 0.01,                 # Gross annual buyback reduction (~1%)
    'margin_of_safety': 0.25,             # 25% discount for buy price

    # ──────────────────────────────────────────────
    # EQUITY BRIDGE (EV → Equity)
    # ──────────────────────────────────────────────
    'cash_bridge': 78_519,                # Cash & equivalents (FY2025 balance sheet)
    'securities': 16_398,                 # Short-term investments / marketable securities

    # ──────────────────────────────────────────────
    # HISTORICAL BALANCE SHEET (FY2020-FY2025)
    # Used for Invested Capital & Sales-to-Capital
    # ──────────────────────────────────────────────
    'ic_years': [2020, 2021, 2022, 2023, 2024, 2025],

    'current_assets':       [181_935, 184_406, 169_684, 184_257, 159_734, 200_024],
    'cash':                 [ 13_576,  14_224,  13_931,  34_704,  18_315,  19_643],
    'st_investments':       [122_951, 116_110,  90_826,  76_558,  57_228,  68_840],
    'operating_cash':       [0, 0, 0, 0, 0, 0],
    'current_liabilities':  [ 72_310,  88_657,  95_082, 104_149, 125_286, 119_543],
    'st_debt':              [  3_749,   8_072,   2_749,   5_247,  16_601,   6_092],
    'st_leases':            [0, 0, 0, 0, 0, 0],
    'net_ppe':              [ 44_151,  59_715,  74_180,  95_680, 135_591, 189_878],
    'goodwill_intang':      [ 54_588,  67_524,  67_524, 118_655, 119_219, 119_457],

    # ──────────────────────────────────────────────
    # HISTORICAL INCOME STATEMENT (FY2020-FY2025)
    # Used for Summary tab & Calculations tab
    # ──────────────────────────────────────────────
    'hist_revenue':           [143_015, 168_088, 198_270, 211_915, 245_122, 277_600],
    'hist_operating_income':  [ 52_959,  69_916,  83_383,  88_523, 109_433, 127_600],
    'hist_net_income':        [ 44_281,  61_271,  72_738,  72_361,  88_136, 105_600],
    'hist_cost_of_revenue':   [ 46_078,  52_232,  62_650,  65_863,  74_073,  88_900],
    'hist_sbc_values':        [  5_289,   6_118,   7_502,   9_611,  10_800,  12_000],
    'hist_shares':            [  7_571,   7_519,   7_464,   7_430,   7_432,   7_432],

    # ──────────────────────────────────────────────
    # SCENARIO ADJUSTMENTS (Bull/Bear vs Base)
    # ──────────────────────────────────────────────
    'bull_growth_adj': 0.02,              # +2pp growth per year
    'bull_margin_adj': 0.02,              # +2pp margin per year
    'bear_growth_adj': -0.04,             # -4pp growth per year
    'bear_margin_adj': -0.02,             # -2pp margin per year

    # ──────────────────────────────────────────────
    # PEER COMPARISON DATA
    # Update multiples when rebuilding
    # ──────────────────────────────────────────────
    'peers': [
        {
            'ticker': 'AAPL', 'name': 'Apple',
            'ev_revenue': 9.5, 'ev_ebitda': 26.0, 'pe': 33.5,
            'op_margin': 0.315, 'rev_growth': 0.05, 'roic': 0.55,
        },
        {
            'ticker': 'GOOGL', 'name': 'Alphabet',
            'ev_revenue': 6.8, 'ev_ebitda': 17.5, 'pe': 24.0,
            'op_margin': 0.32, 'rev_growth': 0.14, 'roic': 0.30,
        },
        {
            'ticker': 'AMZN', 'name': 'Amazon',
            'ev_revenue': 3.5, 'ev_ebitda': 20.0, 'pe': 42.0,
            'op_margin': 0.11, 'rev_growth': 0.12, 'roic': 0.15,
        },
        {
            'ticker': 'META', 'name': 'Meta',
            'ev_revenue': 9.0, 'ev_ebitda': 17.0, 'pe': 25.0,
            'op_margin': 0.42, 'rev_growth': 0.22, 'roic': 0.35,
        },
    ],
}
