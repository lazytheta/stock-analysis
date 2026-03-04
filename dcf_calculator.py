"""
Standalone DCF calculator for Python-side intrinsic value computation.

Extracted from dcf_template.py to enable watchlist overview calculations
without generating a full Excel workbook.
"""


def compute_wacc(cfg):
    """Compute Weighted Average Cost of Capital from config dict.

    Returns the WACC as a float (e.g. 0.08 for 8%).
    """
    eq_val = cfg['equity_market_value']
    debt_val = cfg['debt_market_value']
    eq_wt = eq_val / (eq_val + debt_val)
    debt_wt = debt_val / (eq_val + debt_val)
    wu_beta = sum(ub * wt for _, ub, wt in cfg['sector_betas'])
    de_ratio = debt_val / eq_val if eq_val > 0 else 0
    lev_beta = wu_beta * (1 + (1 - cfg['tax_rate']) * de_ratio)
    ke = cfg['risk_free_rate'] + lev_beta * cfg['erp']
    kd = (cfg['risk_free_rate'] + cfg['credit_spread']) * (1 - cfg['tax_rate'])
    return eq_wt * ke + debt_wt * kd


def compute_intrinsic_value(cfg, wacc=None):
    """Run a full DCF and return valuation metrics.

    Args:
        cfg: Config dict with all DCF assumptions.
        wacc: Optional pre-computed WACC. If None, computed from cfg.

    Returns dict with:
        intrinsic_value  — fair value per share (before margin of safety)
        buy_price        — fair value * (1 - margin_of_safety)
        enterprise_value — sum of discounted FCFFs + terminal value
        equity_value     — EV + cash - debt
        wacc             — weighted average cost of capital used
        tv_pct           — terminal value as % of enterprise value
    """
    if wacc is None:
        wacc = compute_wacc(cfg)

    growth_rates = cfg['revenue_growth']
    margins = cfg['op_margins']
    n_p = len(growth_rates)
    base_rev = cfg['base_revenue']
    tax_r = cfg['tax_rate']
    stc = cfg['sales_to_capital']
    sbc_p = cfg.get('sbc_pct', 0.004)
    tg = cfg['terminal_growth']
    tm = cfg.get('terminal_margin', margins[-1])

    # Project revenues
    revs = [base_rev]
    for g in growth_rates:
        revs.append(revs[-1] * (1 + g))

    # Discount projected FCFFs
    pv_fcff = 0
    for i in range(1, n_p + 1):
        oi = revs[i] * margins[i - 1]
        nopat = oi * (1 - tax_r)
        reinvest = (revs[i] - revs[i - 1]) / stc
        sbc = revs[i] * sbc_p * (1 - tax_r)
        fcff = nopat - reinvest - sbc
        period = 0.5 + (i - 1)
        df = 1 / (1 + wacc) ** period
        pv_fcff += fcff * df

    # Terminal value
    tv_rev = revs[-1] * (1 + tg)
    tv_oi = tv_rev * tm
    tv_nopat = tv_oi * (1 - tax_r)
    tv_reinvest = (tv_rev - revs[-1]) / stc
    tv_sbc = tv_rev * sbc_p * (1 - tax_r)
    tv_fcff = tv_nopat - tv_reinvest - tv_sbc
    tv = tv_fcff / (wacc - tg)
    tv_df = 1 / (1 + wacc) ** (0.5 + n_p - 1)
    pv_tv = tv * tv_df

    # Enterprise & equity value
    ev = pv_fcff + pv_tv
    equity = ev + cfg['cash_bridge'] + cfg.get('securities', 0) - cfg['debt_market_value']
    adj_shares = cfg['shares_outstanding'] * (1 - cfg['buyback_rate']) ** n_p
    intrinsic = equity / adj_shares if adj_shares > 0 else 0

    mos = cfg.get('margin_of_safety', 0.20)

    return {
        'intrinsic_value': intrinsic,
        'buy_price': intrinsic * (1 - mos),
        'enterprise_value': ev,
        'equity_value': equity,
        'wacc': wacc,
        'tv_pct': pv_tv / ev if ev > 0 else 0,
    }
