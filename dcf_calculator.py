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


def _dcf_price_with_overrides(cfg, wacc, growth_rate=None, margin=None):
    """Compute intrinsic value per share with uniform growth and/or margin overrides."""
    n_p = len(cfg['revenue_growth'])
    override_cfg = dict(cfg)
    if growth_rate is not None:
        override_cfg['revenue_growth'] = [growth_rate] * n_p
    if margin is not None:
        override_cfg['op_margins'] = [margin] * n_p
        override_cfg['terminal_margin'] = margin
    return compute_intrinsic_value(override_cfg, wacc=wacc)['intrinsic_value']


def find_implied_value(cfg, wacc, param, lo, hi, target_price, tol=0.5, max_iter=40):
    """Binary search for the growth rate or margin that matches target_price.

    Args:
        param: 'growth' or 'margin'
        lo, hi: search bounds
        target_price: market price to match
    """
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if param == 'growth':
            price = _dcf_price_with_overrides(cfg, wacc, growth_rate=mid)
        else:
            price = _dcf_price_with_overrides(cfg, wacc, margin=mid)
        if abs(price - target_price) < tol:
            return mid
        if price > target_price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def compute_reverse_dcf(cfg, wacc=None, growth_range=None, margin_range=None):
    """Compute reverse DCF: implied metrics and sensitivity matrix.

    Args:
        cfg: Config dict.
        wacc: Pre-computed WACC (optional).
        growth_range: Tuple (min, max, step) for growth axis. Auto if None.
        margin_range: Tuple (min, max, step) for margin axis. Auto if None.

    Returns dict with:
        implied_growth  — uniform CAGR that matches market price
        implied_margin  — uniform margin that matches market price
        base_cagr       — average of config growth rates
        base_margin     — average of config margins
        market_price    — current stock price
        matrix          — list of dicts with keys: growth, margin, price
        growth_tests    — list of growth rates tested
        margin_tests    — list of margin rates tested
        closest         — (growth, margin) tuple closest to market price
    """
    if wacc is None:
        wacc = compute_wacc(cfg)

    mkt_price = cfg['stock_price']
    base_growth = cfg['revenue_growth']
    base_margins = cfg['op_margins']
    base_cagr = sum(base_growth) / len(base_growth)
    base_margin = sum(base_margins) / len(base_margins)

    # Find implied values via binary search
    implied_growth = find_implied_value(cfg, wacc, 'growth', -0.05, 0.50, mkt_price)
    implied_margin = find_implied_value(cfg, wacc, 'margin', 0.01, 0.80, mkt_price)

    # Build test ranges
    if growth_range:
        g_min, g_max, g_step = growth_range
    else:
        g_step = 0.02
        g_center = round(implied_growth / g_step) * g_step
        g_min = max(0.0, g_center - 0.08)
        g_max = g_center + 0.08
    growth_tests = []
    g = g_min
    while g <= g_max + 1e-9:
        growth_tests.append(round(g, 4))
        g += g_step

    if margin_range:
        m_min, m_max, m_step = margin_range
    else:
        m_step = 0.02
        m_center = round(implied_margin / m_step) * m_step
        m_min = max(0.05, m_center - 0.08)
        m_max = m_center + 0.08
    margin_tests = []
    m = m_min
    while m <= m_max + 1e-9:
        margin_tests.append(round(m, 4))
        m += m_step

    # Compute matrix
    matrix = []
    closest = None
    closest_diff = float('inf')
    for gr in growth_tests:
        for mg in margin_tests:
            price = _dcf_price_with_overrides(cfg, wacc, growth_rate=gr, margin=mg)
            matrix.append({'growth': gr, 'margin': mg, 'price': price})
            diff = abs(price - mkt_price)
            if diff < closest_diff:
                closest_diff = diff
                closest = (gr, mg)

    return {
        'implied_growth': implied_growth,
        'implied_margin': implied_margin,
        'base_cagr': base_cagr,
        'base_margin': base_margin,
        'market_price': mkt_price,
        'matrix': matrix,
        'growth_tests': growth_tests,
        'margin_tests': margin_tests,
        'closest': closest,
        'wacc': wacc,
    }
