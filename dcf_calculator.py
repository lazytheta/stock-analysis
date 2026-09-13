"""
Standalone DCF calculator for Python-side intrinsic value computation.

Extracted from dcf_template.py to enable watchlist overview calculations
without generating a full Excel workbook.
"""


def _equity_market_value(cfg):
    """Equity market value ($M) used for WACC/CAPM weighting.

    This is a **deliberate input**, on a par with the risk-free rate and the
    ERP: you set it when you author the config and revise it consciously. It
    is deliberately not refreshed from the live price, because under CAPM it
    drives both the Hamada relevering (via D/E) and the WACC weights — so a
    live value would make the discount rate, and every fair value, drift with
    the market. It also drifts the wrong way: price up → D/E down → WACC down
    → fair value up, which would make a stock look cheaper the more expensive
    it gets.

    Some config paths (e.g. an MCP save_to_watchlist with a hand-built config)
    omit it. Those fall back to ``stock_price × shares_outstanding`` so callers
    never raise KeyError — which would silently drop the ticker from the
    watchlist overview — but such a config *does* float with the price. Give
    every config an explicit ``equity_market_value`` to avoid that.
    """
    emv = cfg.get("equity_market_value") or 0
    if emv:
        return emv
    return (cfg.get("stock_price", 0) or 0) * (cfg.get("shares_outstanding", 0) or 0)


DEFAULT_DISCOUNT_MODE = "hurdle"

# The hurdle every name is held to under "hurdle" mode. A config may carry its
# own `hurdle_rate` to hold one business to a higher bar; leaving it out keeps
# the whole watchlist on this one number, so changing the bar is one edit here
# rather than an edit per ticker.
DEFAULT_HURDLE_RATE = 0.09


def _effective_beta(cfg):
    """Beta applied to the equity risk premium in the discount rate.

    Two philosophies, selected by cfg['discount_mode']:

    - "capm" (default): the weighted unlevered sector beta, Hamada-relevered
      for the config's debt/equity. Company risk lives in the discount rate.

    - "opportunity_cost": effective beta = 1.0, so ke = rf + ERP — one
      market-wide opportunity-cost hurdle for every company. Company-
      specific risk is expressed in the cash-flow assumptions and margin of
      safety instead, not the discount rate.

    In both modes the returned value multiplies cfg['erp']. sector_betas is
    left untouched in the config either way (its sector name still drives the
    margin lookup); "opportunity_cost" simply does not read the beta figures.
    """
    if cfg.get("discount_mode", DEFAULT_DISCOUNT_MODE) == "hurdle":
        # Beta is not part of a fixed hurdle. Returning it anyway would let a
        # sector beta leak into anything that reads this for display.
        return 1.0
    if cfg.get("discount_mode", DEFAULT_DISCOUNT_MODE) == "capm":
        eq_val = _equity_market_value(cfg)
        debt_val = cfg["debt_market_value"]
        wu_beta = sum(ub * wt for _, ub, wt in cfg["sector_betas"])
        de_ratio = debt_val / eq_val if eq_val > 0 else 0
        return wu_beta * (1 + (1 - cfg["tax_rate"]) * de_ratio)
    return 1.0


def compute_cost_of_equity(cfg):
    """Compute the cost of equity from the config dict.

    ke = risk_free_rate + effective_beta × erp

    effective_beta follows cfg['discount_mode'] (see _effective_beta): the
    Hamada-relevered sector beta under "capm" (the default), or 1.0 under
    "opportunity_cost". Under "opportunity_cost" this equals compute_wacc(cfg)
    exactly (the WACC IS ke there); under "capm" they coincide only when
    debt = 0.

    Under "hurdle" there is no cost-of-equity calculation at all — the hurdle
    IS the required return, and reporting a different number here would show a
    figure the DCF is not using.

    Returns the cost of equity as a float (e.g. 0.087 for 8.7%).
    """
    if cfg.get("discount_mode", DEFAULT_DISCOUNT_MODE) == "hurdle":
        return _hurdle_rate(cfg)
    return cfg["risk_free_rate"] + _effective_beta(cfg) * cfg["erp"]


def _hurdle_rate(cfg):
    """The fixed hurdle for this config."""
    rate = cfg.get("hurdle_rate")
    return float(rate) if rate else DEFAULT_HURDLE_RATE


def compute_wacc(cfg):
    """Compute the discount rate from the config dict.

    Three philosophies, selected by cfg['discount_mode']:

    - "hurdle" (default): a fixed required return, the same for every name.
      It moves with nothing — not the risk-free rate, not the ERP, not beta or
      capital structure. The bar you demand of a business does not fall because
      the Treasury cut rates or because the company borrowed cheaply.

    - "capm": the classic equity/debt-weighted WACC blend, with the after-tax
      cost of debt pulling the rate below ke. Company risk enters through the
      Hamada-relevered sector beta.

    - "opportunity_cost": the discount rate IS the cost of equity,
      ke = rf + ERP — one market-wide hurdle that still drifts with rates.

    Returns the rate as a float (e.g. 0.08 for 8%).
    """
    mode = cfg.get("discount_mode", DEFAULT_DISCOUNT_MODE)
    if mode == "hurdle":
        return _hurdle_rate(cfg)
    ke = cfg['risk_free_rate'] + _effective_beta(cfg) * cfg['erp']
    if mode != "capm":
        return ke
    eq_val = _equity_market_value(cfg)
    debt_val = cfg['debt_market_value']
    eq_wt = eq_val / (eq_val + debt_val)
    debt_wt = debt_val / (eq_val + debt_val)
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
    tg = cfg['terminal_growth']
    tm = cfg.get('terminal_margin', margins[-1])

    # Per-year inputs have exactly one representation. A scalar
    # ``sales_to_capital`` used to stand in silently when the list was absent,
    # which let the config carry two different numbers for the same assumption
    # — NVDA held sales_to_capital 8.0 next to stc_per_year 5.0, and the editor
    # displayed the 8.0 under a label reading "Used in DCF". Missing is now an
    # error, so a value on screen can always be traced to the one that ran.
    def _per_year(key, scalar_key):
        values = cfg.get(key)
        if not values:
            values = [cfg[scalar_key]] * n_p      # flat profile from the scalar
        values = [float(v) for v in values]
        if len(values) < n_p:                     # pad from the last authored year
            values = values + [values[-1]] * (n_p - len(values))
        return values[:n_p]

    tax_list = _per_year('tax_per_year', 'tax_rate')
    stc_list = _per_year('stc_per_year', 'sales_to_capital')

    default_wacc = wacc
    wacc_list = cfg.get('wacc_per_year', [default_wacc] * n_p)
    if len(wacc_list) < n_p:
        wacc_list = list(wacc_list) + [wacc_list[-1] if wacc_list else default_wacc] * (n_p - len(wacc_list))

    # Terminal overrides
    tv_tax = cfg.get('terminal_tax', tax_list[-1])
    tv_stc = cfg.get('terminal_stc', stc_list[-1])
    tv_wacc = cfg.get('terminal_wacc', wacc_list[-1])

    # Onmogelijke inputs weigeren in plaats van er een getal van maken.
    # tv = tv_fcff / (tv_wacc - tg) klapt om bij tg >= tv_wacc (intrinsic
    # -105,94 zonder melding), en stc = 0 is een ZeroDivisionError die de
    # watchlist-loop niet opvangt. De dividendlens bewaakt precies dit
    # (ke <= g_term); de DCF deed het niet.
    if tg >= tv_wacc:
        raise ValueError(
            f"terminal_growth {tg:.4f} >= terminal discount rate {tv_wacc:.4f}: "
            f"terminal value is onbepaald")
    _bad_stc = [x for x in [*stc_list, tv_stc] if not x or x <= 0]
    if _bad_stc:
        raise ValueError("sales_to_capital (stc) moet > 0 zijn; gevonden "
                         f"{_bad_stc[0]!r} -- herinvestering is er ongedefinieerd door")

    # Project revenues
    revs = [base_rev]
    for g in growth_rates:
        revs.append(revs[-1] * (1 + g))

    # Discount projected FCFFs
    pv_fcff = 0
    for i in range(1, n_p + 1):
        # op_margins are GAAP (SBC already expensed in operating income), so SBC
        # is counted once here — no separate SBC line (avoids double-counting).
        # Convention decided 2026-06-17 (Option 2); see notifications/SBC spec.
        oi = revs[i] * margins[i - 1]
        nopat = oi * (1 - tax_list[i - 1])
        reinvest = (revs[i] - revs[i - 1]) / stc_list[i - 1]
        fcff = nopat - reinvest
        period = 0.5 + (i - 1)
        df = 1 / (1 + wacc_list[i - 1]) ** period
        pv_fcff += fcff * df

    # Terminal value
    tv_rev = revs[-1] * (1 + tg)
    tv_oi = tv_rev * tm
    tv_nopat = tv_oi * (1 - tv_tax)
    tv_reinvest = (tv_rev - revs[-1]) / tv_stc
    tv_fcff = tv_nopat - tv_reinvest
    tv = tv_fcff / (tv_wacc - tg)
    tv_df = 1 / (1 + tv_wacc) ** (0.5 + n_p - 1)
    pv_tv = tv * tv_df

    # Enterprise & equity value
    ev = pv_fcff + pv_tv
    equity = (ev + cfg['cash_bridge'] + cfg.get('securities', 0)
              + cfg.get('equity_investments', 0)
              - cfg['debt_market_value']
              - cfg.get('minority_interest', 0)
              - cfg.get('unfunded_pension', 0))
    intrinsic = equity / cfg['shares_outstanding'] if cfg['shares_outstanding'] > 0 else 0

    mos = cfg.get('margin_of_safety', 0.20)

    return {
        'intrinsic_value': intrinsic,
        'buy_price': intrinsic * (1 - mos),
        'enterprise_value': ev,
        'equity_value': equity,
        'wacc': wacc,
        'tv_pct': pv_tv / ev if ev > 0 else 0,
        # The per-year inputs this run actually used, padded and resolved.
        # The page renders these rather than re-reading the config, so a
        # displayed number can never diverge from the one that was computed.
        'stc_used': stc_list,
        'terminal_stc_used': tv_stc,
        'tax_used': tax_list,
        'terminal_tax_used': tv_tax,
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

    # Build test ranges — centered on base case, +/- 10 percentage points, 0.5% steps
    if growth_range:
        g_min, g_max, g_step = growth_range
    else:
        g_step = 0.005
        g_min = max(0.0, round(base_cagr - 0.05, 3))
        g_max = round(base_cagr + 0.05, 3)
    growth_tests = []
    g = g_min
    while g <= g_max + 1e-9:
        growth_tests.append(round(g, 4))
        g += g_step

    if margin_range:
        m_min, m_max, m_step = margin_range
    else:
        m_step = 0.005
        m_min = max(0.01, round(base_margin - 0.05, 3))
        m_max = round(base_margin + 0.05, 3)
    margin_tests = []
    m = m_min
    while m <= m_max + 1e-9:
        margin_tests.append(round(m, 4))
        m += m_step

    # Compute matrix
    matrix = []
    for gr in growth_tests:
        for mg in margin_tests:
            price = _dcf_price_with_overrides(cfg, wacc, growth_rate=gr, margin=mg)
            matrix.append({'growth': gr, 'margin': mg, 'price': price})

    # Find closest: price match to market is primary, base case proximity is tiebreaker
    closest = None
    best_score = float('inf')
    for m in matrix:
        price_diff = abs(m['price'] - mkt_price) / max(mkt_price, 1)
        # Small tiebreaker toward base case when prices are equally close
        g_dist = abs(m['growth'] - base_cagr) / max(abs(base_cagr), 0.01)
        m_dist = abs(m['margin'] - base_margin) / max(abs(base_margin), 0.01)
        score = price_diff + (g_dist + m_dist) * 0.01
        if score < best_score:
            best_score = score
            closest = (m['growth'], m['margin'])

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
