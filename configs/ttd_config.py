"""
Trade Desk, Inc. (TTD) -- DCF Configuration
====================================================
Auto-generated: February 16, 2026
Source: EDGAR XBRL + Yahoo Finance + Treasury.gov

Usage:
    exec(open('dcf_template.py').read())
    exec(open('configs/ttd_config.py').read())
    build_dcf_model(cfg, 'output/TTD_DCF.xlsx')

All values in $M unless noted. Blue cells in Excel = editable assumptions.
"""

cfg = {
    # ──────────────────────────────────────────────
    # COMPANY INFO
    # ──────────────────────────────────────────────
    'company': 'Trade Desk, Inc.',
    'ticker': 'TTD',
    'valuation_date': 'Feb 2026',

    # ──────────────────────────────────────────────
    # MARKET DATA
    # ──────────────────────────────────────────────
    'stock_price': 25.81,
    'equity_market_value': 12_957,
    'debt_market_value': 248,

    # ──────────────────────────────────────────────
    # WACC INPUTS
    # ──────────────────────────────────────────────
    'risk_free_rate': 0.0404,
    'erp': 0.047,
    'credit_spread': 0.004,
    'tax_rate': 0.22,

    'sector_betas': [
        ('Market', 1.0, 1.0),
    ],

    'debt_breakdown': [
        ('Operating Leases', 248),
    ],

    # ──────────────────────────────────────────────
    # DCF ASSUMPTIONS
    # ──────────────────────────────────────────────
    'base_year': 2024,
    'base_revenue': 2_445,
    'base_oi': 427,
    'base_op_margin': 0.175,

    'revenue_growth': [
        0.135,  # FY2025
        0.119,  # FY2026
        0.07,  # FY2027
        0.054,  # FY2028
        0.043,  # FY2029
        0.037,  # FY2030
        0.032,  # FY2031
        0.03,  # FY2032
        0.028,  # FY2033
        0.027,  # FY2034
    ],

    'op_margins': [
        0.169,  # FY2025
        0.163,  # FY2026
        0.157,  # FY2027
        0.157,  # FY2028
        0.155,  # FY2029
        0.154,  # FY2030
        0.152,  # FY2031
        0.15,  # FY2032
        0.149,  # FY2033
        0.147,  # FY2034
    ],

    'terminal_growth': 0.025,
    'terminal_margin': 0.147,
    'sales_to_capital': 4.55,
    'sbc_pct': 0.2572,

    # ──────────────────────────────────────────────
    # SHARES & BUYBACKS
    # ──────────────────────────────────────────────
    'shares_outstanding': 502,
    'buyback_rate': 0.023,
    'margin_of_safety': 0.2,

    # ──────────────────────────────────────────────
    # EQUITY BRIDGE
    # ──────────────────────────────────────────────
    'cash_bridge': 1_369,
    'securities': 552,

    # ──────────────────────────────────────────────
    # HISTORICAL BALANCE SHEET (FY2019-FY2024)
    # ──────────────────────────────────────────────
    'ic_years':            [      2019,       2020,       2021,       2022,       2023,       2024],
    'current_assets':      [     1_449,      2_310,      3_092,      3_846,      4_314,      5_336],
    'cash':                [       131,        437,        754,      1_031,        895,      1_369],
    'st_investments':      [       124,        187,        205,        416,        485,        552],
    'operating_cash':      [         0,          0,          0,          0,          0,          0],
    'current_liabilities': [       930,      1_475,      1_803,      2_029,      2_511,      2_873],
    'st_debt':             [         0,          0,          0,          0,          0,          0],
    'st_leases':           [        15,         38,         46,         52,         56,         64],
    'net_ppe':             [        64,        116,        136,        174,        161,        209],
    'goodwill_intang':     [         0,          0,          0,          0,          0,          0],

    # ──────────────────────────────────────────────
    # HISTORICAL INCOME STATEMENT
    # ──────────────────────────────────────────────
    'hist_revenue':          [       661,        836,      1_196,      1_578,      1_946,      2_445],
    'hist_operating_income': [       112,        144,        125,        114,        200,        427],
    'hist_net_income':       [       108,        242,        138,         53,        179,        393],
    'hist_cost_of_revenue':  [       156,        179,        222,        281,        366,        472],
    'hist_sbc_values':       [        81,        112,        337,        499,        492,        495],
    'hist_shares':           [        48,         49,        499,        500,        500,        502],

    # ──────────────────────────────────────────────
    # SCENARIO ADJUSTMENTS
    # ──────────────────────────────────────────────
    'bull_growth_adj': 0.02,
    'bull_margin_adj': 0.02,
    'bear_growth_adj': -0.04,
    'bear_margin_adj': -0.02,

    # ──────────────────────────────────────────────
    # PEER COMPARISON DATA
    # ──────────────────────────────────────────────
    'peers': [
        {
            'ticker': 'FDS', 'name': 'FACTSET RESEARCH SYSTEMS INC',
            'ev_revenue': 3.8, 'ev_ebitda': 9.1, 'pe': 13.1,
            'op_margin': 0.322, 'rev_growth': 0.054, 'roic': 0.19,
        },
        {
            'ticker': 'FLUT', 'name': 'Flutter Entertainment plc',
            'ev_revenue': 2.0, 'ev_ebitda': 24.5, 'pe': 0,
            'op_margin': 0.062, 'rev_growth': 0.192, 'roic': 0.04,
        },
        {
            'ticker': 'DOCN', 'name': 'DigitalOcean',
            'ev_revenue': 9.6, 'ev_ebitda': 63.7, 'pe': 77.1,
            'op_margin': 0.117, 'rev_growth': 0.127, 'roic': 0.09,
        },
        {
            'ticker': 'IAC', 'name': 'IAC',
            'ev_revenue': 0.8, 'ev_ebitda': 0, 'pe': 0,
            'op_margin': -0.001, 'rev_growth': -0.128, 'roic': -0.0,
        },
        {
            'ticker': 'GRND', 'name': 'Grindr',
            'ev_revenue': 5.8, 'ev_ebitda': 16.5, 'pe': 0,
            'op_margin': 0.27, 'rev_growth': 0.327, 'roic': 0.21,
        },
        {
            'ticker': 'DV', 'name': 'DoubleVerify',
            'ev_revenue': 2.1, 'ev_ebitda': 12.8, 'pe': 29.5,
            'op_margin': 0.125, 'rev_growth': 0.147, 'roic': 0.08,
        },
    ],
}
