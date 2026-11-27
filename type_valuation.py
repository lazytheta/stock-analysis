"""Buy price per company type (Notion: "Waarderingsmethode per type bedrijf", v0.2).

Every type keeps its own main test, and every main test ends in ONE buy price,
so the watchlist can show "price ≤ buy price" for all types alike.

| type           | main test                          | where the margin of safety sits     |
| capital_light  | required vs achievable EPS growth  | 2pp buffer on the growth            |
| capital_heavy  | DCF at the hurdle, growth − buffer | 2pp buffer on the growth            |
| cyclical       | normalised EPS × 11                | normalisation + the no-growth P/E   |
| financial      | tangible book × justified P/B      | 20% off                             |
| asset          | value of the assets                | 20% off                             |

The type and its judgement inputs (achievable growth, ROE, asset value, ...)
are authored per ticker in ``cfg["valuation_type"]`` — never generated here.
Facts (EPS, P/E history, margins, book value) come from EDGAR and the stored
price history. A missing input gives no buy price and a one-line reason, never
an invented number.

Pure functions: no network, no Streamlit. Callers pass the DCF as callables so
this module does not depend on dcf_calculator's config shape.
"""

from __future__ import annotations

import statistics

HURDLE = 0.09
BUFFER = 0.02          # growth buffer, capital_light / capital_heavy
PE_CAP = 25.0          # ceiling on the end P/E
CYCLICAL_PE = 11.0     # ≈ 1 / 9%: the no-growth anchor
HORIZON = 5            # years over which the P/E reverts
BOOK_MOS = 0.20        # financial / asset: discount on an estimated value
MIN_CYCLE_YEARS = 10   # a normalisation window needs a full cycle

TYPES = {
    "capital_light": "Capital-light",
    "capital_heavy": "Capital-heavy",
    "cyclical": "Cyclical",
    "financial": "Financial",
    "asset": "Asset-based",
}

# Authored inputs each type needs before it can price. Facts are not listed:
# they come from fundamentals and report their own gaps.
REQUIRED_INPUTS = {
    "capital_light": ("achievable_growth",),
    "capital_heavy": ("achievable_growth",),
    "cyclical": (),
    "financial": ("roe", "book_growth"),
    "asset": ("asset_value_per_share",),
}

INPUT_LABELS = {
    "achievable_growth": "Achievable growth",
    "roe": "Sustainable return on tangible equity",
    "book_growth": "Book value growth",
    "asset_value_per_share": "Value of the assets per share",
}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v else None   # NaN → None


def _last(seq):
    for v in reversed(seq or []):
        v = _num(v)
        if v is not None:
            return v
    return None


# ── Facts ────────────────────────────────────────────────────────────────

def pe_history(eps_by_year, close_by_year):
    """Yearly P/E: the close at (about) the fiscal year end ÷ that year's EPS.

    Loss years are skipped — a negative P/E is not a multiple. Both inputs are
    {fiscal_year: value}."""
    out = []
    for y, eps in sorted((eps_by_year or {}).items()):
        eps = _num(eps)
        px = _num((close_by_year or {}).get(y))
        if eps and eps > 0 and px and px > 0:
            out.append(px / eps)
    return out


def end_pe(pe_hist, years=10, cap=PE_CAP):
    """The P/E the price is assumed to revert to: the lower of the own median
    over `years` and the cap. Without a history the cap stands alone."""
    recent = [p for p in (pe_hist or [])[-years:] if p and p > 0]
    if not recent:
        return cap, None
    med = statistics.median(recent)
    return min(med, cap), med


def required_growth(pe_now, pe_end, hurdle=HURDLE, years=HORIZON, div_yield=0.0):
    """EPS growth per year needed for `hurdle` when the P/E moves from pe_now
    to pe_end over `years` and the dividend yield adds the rest."""
    if not pe_now or not pe_end or pe_now <= 0 or pe_end <= 0:
        return None
    return (1 + hurdle - (div_yield or 0)) * (pe_now / pe_end) ** (1 / years) - 1


def growth_buy_price(eps, growth, pe_end_value, dps=0.0, hurdle=HURDLE,
                     buffer=BUFFER, years=HORIZON):
    """The highest price at which EPS growth of (growth − buffer), a P/E that
    reverts to pe_end and the dividend still return `hurdle` a year.

    Solves (1+g−b)·(pe_end·eps/P)^(1/n) − 1 + dps/P = hurdle for P; the left
    side falls as P rises, so a bisection is exact enough."""
    eps, growth, pe_end_value = _num(eps), _num(growth), _num(pe_end_value)
    dps = _num(dps) or 0.0
    if not eps or eps <= 0 or growth is None or not pe_end_value:
        return None
    g = growth - buffer

    def ret(p):
        return (1 + g) * (pe_end_value * eps / p) ** (1 / years) - 1 + dps / p

    lo, hi = eps * 0.01, eps * 1000
    if ret(hi) > hurdle:
        return None   # even at 1000× earnings: the inputs are not credible
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if ret(mid) > hurdle else (lo, mid)
    return (lo + hi) / 2


def normalised_margin(net_income, revenue, years=MIN_CYCLE_YEARS):
    """Average net margin over the last `years` fiscal years with both values.
    None under `years` of data: a window without a full cycle is not a norm."""
    pairs = [(_num(n), _num(r)) for n, r in zip(net_income or [], revenue or [])]
    pairs = [(n, r) for n, r in pairs if n is not None and r and r > 0][-years:]
    if len(pairs) < years:
        return None, len(pairs)
    margins = [n / r for n, r in pairs]
    return sum(margins) / len(margins), len(pairs)


def justified_pb(roe, growth, hurdle=HURDLE):
    """(ROE − g) ÷ (hurdle − g). None when g ≥ hurdle or ROE ≤ g."""
    roe, growth = _num(roe), _num(growth)
    if roe is None or growth is None or growth >= hurdle or roe <= growth:
        return None
    return (roe - growth) / (hurdle - growth)


def year_end_closes(series):
    """{year: last close of that calendar year} from [(iso_day, close), ...].

    The fiscal-year end month is not stored, so a June year-end (MSFT) is
    priced at December. For a 10-year median that shifts single points, not
    the level."""
    out = {}
    for day, close in series or []:
        try:
            out[int(str(day)[:4])] = float(close)
        except (TypeError, ValueError):
            continue
    return out


def facts_from(cfg, fund, closes=None, price=None):
    """The facts evaluate() reads, in the units of the share price.

    The DCF config is already in price units (ADR-equivalent shares, converted
    revenue), EDGAR fundamentals are not always: TSM reports per ordinary
    share. So the latest EPS comes from the config, and every per-share series
    from `fund` is scaled by config-EPS ÷ fund-EPS of the latest year. That
    keeps the P/E history consistent with the price; for a foreign filer it
    does assume a constant exchange rate over the window."""
    cfg, fund = cfg or {}, fund or {}
    years = list(fund.get("years") or [])

    def col(key):
        vals = list(fund.get(key) or [])
        return [_num(vals[i]) if i < len(vals) else None for i in range(len(years))]

    ni, rev, shares = col("net_income"), col("revenue"), col("shares")
    eq, gw, intang = col("total_equity"), col("goodwill"), col("intangibles")
    fcf, divs = col("fcf"), col("dividends_paid")

    # Overrides carry shares in millions (ASML: 394.1), EDGAR a raw count
    # (428,000,000): mixed in one series, the early years' EPS came out a
    # million times too small. No listed company has under 100k shares.
    shares = [s * 1e6 if s and 0 < s < 1e5 else s for s in shares]

    so = _num(cfg.get("shares_outstanding"))
    eps_cfg = None
    hist_ni = cfg.get("hist_net_income") or []
    if hist_ni and so and so > 0 and _num(hist_ni[-1]) is not None:
        eps_cfg = _num(hist_ni[-1]) / so

    # No share count in EDGAR (Visa's A/B/C classes): fall back to the
    # config's count for every year. Buybacks then make early-year EPS look a
    # little high and early P/Es a little low — a note, not a reason to stop.
    if so and not any(shares):
        shares = [so * 1e6] * len(years)
    # Gaps (RACE has no count after 2020): carry the nearest known count
    # forward, then backward. Without this the latest EPS silently became
    # 2020's and the scale with it.
    last = None
    for i, s in enumerate(shares):
        if s:
            last = s
        elif last:
            shares[i] = last
    nxt = None
    for i in range(len(shares) - 1, -1, -1):
        if shares[i]:
            nxt = shares[i]
        elif nxt:
            shares[i] = nxt
    fund_eps = [n / s if n is not None and s else None for n, s in zip(ni, shares)]
    # Scale on the fund year whose net income IS the config's latest one: the
    # fundamentals may hold a newer fiscal year than the DCF was built on
    # (MSFT FY2026 vs FY2025), and the config's base_year is not reliable.
    ni_cfg = _num(hist_ni[-1]) if hist_ni else None
    ref = None
    if ni_cfg:
        for n, e in zip(ni, fund_eps):
            if n and e and abs(n / ni_cfg - 1) < 0.02:
                ref = e
    if ref is None and cfg.get("base_year") in years:
        ref = fund_eps[years.index(cfg["base_year"])]
    ref = ref or next((e for e in reversed(fund_eps) if e), None)
    scale = (eps_cfg / ref) if eps_cfg and ref and ref > 0 and eps_cfg > 0 else None
    if scale is None:
        scale = 1e6 if ref else None   # fund money is $M, shares a raw count

    def per_share(vals):
        return [v * scale / s if v is not None and s and scale else None
                for v, s in zip(vals, shares)]

    eps_by_year = dict(zip(years, [e * scale if e is not None and scale else None
                                   for e in fund_eps]))
    close_by_year = year_end_closes(closes)
    tb = per_share([(e - (g or 0) - (i or 0)) if e is not None else None
                    for e, g, i in zip(eq, gw, intang)])
    bv = per_share(eq)
    pb_hist = [close_by_year[y] / b for y, b in zip(years, bv)
               if b and b > 0 and close_by_year.get(y)]
    fcf_ps = _last(per_share(fcf))
    div_ps = _last(per_share([-d if d is not None and d < 0 else d for d in divs]))
    rps = _last(per_share(rev))
    if rps is None:
        base_rev = _num(cfg.get("base_revenue"))
        rps = base_rev / so if base_rev and so else None

    return {
        # the newest fiscal year we have, in price units
        "eps": _last(eps_by_year.values()) if scale else eps_cfg,
        "dps": div_ps or 0.0,
        "pe_history": pe_history(eps_by_year, close_by_year),
        "revenue": rev, "net_income": ni, "shares": shares,
        "revenue_ps": rps,          # price units, from the DCF config
        "tangible_book_ps": _last(tb),
        "pb_history": pb_hist,
        "fcf_yield": (fcf_ps / price) if fcf_ps is not None and price else None,
        "risk_free": _num(cfg.get("risk_free_rate")),
    }


# ── The buy price per type ───────────────────────────────────────────────

def _missing(vt, kind):
    return [k for k in REQUIRED_INPUTS[kind] if _num(vt.get(k)) is None]


def evaluate(vt, price, facts, dcf_value=None, implied_return=None,
             hurdle=HURDLE):
    """Buy price and control test for one ticker.

    vt:      cfg["valuation_type"] — {"type": ..., authored inputs, "rationale": ...}
    price:   live price
    facts:   {"eps", "dps", "pe_history", "revenue", "net_income", "shares",
              "tangible_book_ps", "pb_history"} — any may be absent
    dcf_value(growth_shift) -> fair value per share at the hurdle, no MoS
    implied_return: the DCF's implied return at the live price

    Returns a dict that never raises on bad input: buy_price None plus
    `missing` (labels) or `note` when it cannot price.
    """
    vt = vt if isinstance(vt, dict) else {}
    kind = vt.get("type")
    out = {"type": kind, "label": TYPES.get(kind), "buy_price": None,
           "price": _num(price), "distance": None, "main": {},
           "control": {"agrees": None, "text": None},
           "missing": [], "note": None}
    if kind not in TYPES:
        out["note"] = "Type not set"
        return out
    out["missing"] = [INPUT_LABELS[k] for k in _missing(vt, kind)]
    price = out["price"]
    # Clean earnings: an authored EPS replaces a year with one-offs in it
    # (MSFT FY2026). Only with a reason, shown on the page.
    eps = _num(vt.get("eps_override"))
    eps_overridden = eps is not None
    if eps is None:
        eps = _num(facts.get("eps"))
    dps = _num(facts.get("dps")) or 0.0
    pe_now = price / eps if price and eps and eps > 0 else None
    pe_end, pe_med = end_pe(facts.get("pe_history"))
    # An authored end P/E replaces the rule when the median comes from a
    # different growth phase (DECK: HOKA years). Only with a reason.
    pe_end_overridden = _num(vt.get("pe_end_override")) is not None
    if pe_end_overridden:
        pe_end = _num(vt.get("pe_end_override"))
    m = out["main"]

    if kind == "capital_light":
        g = _num(vt.get("achievable_growth"))
        m.update(eps=eps, eps_overridden=eps_overridden,
                 pe_now=pe_now, pe_end=pe_end, pe_median=pe_med,
                 pe_end_overridden=pe_end_overridden,
                 achievable_growth=g, buffer=BUFFER,
                 required_growth=required_growth(
                     pe_now, pe_end, hurdle, div_yield=(dps / price if price else 0)))
        if eps is not None and eps <= 0:
            out["note"] = "No earnings to price on"
        elif not out["missing"]:
            out["buy_price"] = growth_buy_price(eps, g, pe_end, dps, hurdle)
        ir = _num(implied_return)
        if ir is not None:
            out["control"]["text"] = f"DCF implied return {ir:.1%} vs hurdle {hurdle:.0%}"
            out["control"]["_says_buy"] = ir >= hurdle

    elif kind == "capital_heavy":
        g = _num(vt.get("achievable_growth"))
        req = required_growth(pe_now, pe_end, hurdle,
                              div_yield=(dps / price if price else 0))
        m.update(eps=eps, eps_overridden=eps_overridden,
                 achievable_growth=g, buffer=BUFFER, pe_now=pe_now,
                 pe_end=pe_end, pe_median=pe_med, required_growth=req,
                 pe_end_overridden=pe_end_overridden)
        if not out["missing"]:
            if dcf_value is None:
                out["note"] = "No DCF to price on"
            else:
                try:
                    out["buy_price"] = _num(dcf_value(-BUFFER))
                except Exception:
                    out["note"] = "DCF failed"
        if req is not None and g is not None:
            out["control"]["text"] = (f"Price asks {req:.1%} EPS growth; "
                                      f"achievable {g:.1%} − {BUFFER:.0%} buffer")
            out["control"]["_says_buy"] = req <= g - BUFFER

    elif kind == "cyclical":
        nm_auto, n_years = normalised_margin(facts.get("net_income"), facts.get("revenue"))
        nm = _num(vt.get("normalised_margin"))
        nm_used = nm if nm is not None else nm_auto
        rps = _num(facts.get("revenue_ps"))
        if rps is None:
            rev, shares = _last(facts.get("revenue")), _last(facts.get("shares"))
            rps = rev / shares if rev and shares else None
        norm_eps = rps * nm_used if rps and nm_used is not None else None
        m.update(normalised_margin=nm_used, normalised_margin_auto=nm_auto,
                 margin_overridden=nm is not None, window_years=n_years,
                 revenue_per_share=rps, normalised_eps=norm_eps,
                 target_pe=CYCLICAL_PE)
        if nm_used is None:
            out["missing"].append(f"A full cycle of margins ({n_years} of "
                                  f"{MIN_CYCLE_YEARS} years)")
        elif norm_eps is None or norm_eps <= 0:
            out["note"] = "No normalised earnings to price on"
        else:
            out["buy_price"] = norm_eps * CYCLICAL_PE
        ir = _num(implied_return)
        if ir is not None:
            out["control"]["text"] = f"DCF implied return {ir:.1%} vs hurdle {hurdle:.0%}"
            out["control"]["_says_buy"] = ir >= hurdle

    elif kind == "financial":
        tb = _num(facts.get("tangible_book_ps"))
        pb = justified_pb(vt.get("roe"), vt.get("book_growth"), hurdle)
        m.update(tangible_book_ps=tb, roe=_num(vt.get("roe")),
                 book_growth=_num(vt.get("book_growth")), justified_pb=pb,
                 mos=BOOK_MOS)
        if tb is None or tb <= 0:
            out["missing"].append("Tangible book value")
        if not out["missing"]:
            if pb is None:
                out["note"] = "ROE must exceed growth, and growth stay below the hurdle"
            else:
                out["buy_price"] = tb * pb * (1 - BOOK_MOS)
        hist = [p for p in (facts.get("pb_history") or []) if p and p > 0][-10:]
        if hist and tb and price:
            med = statistics.median(hist)
            out["control"]["text"] = (f"P/B {price / tb:.2f}× vs own 10y median "
                                      f"{med:.2f}×")
            out["control"]["_says_buy"] = price / tb <= med

    elif kind == "asset":
        v = _num(vt.get("asset_value_per_share"))
        m.update(asset_value_per_share=v, mos=BOOK_MOS)
        if not out["missing"]:
            out["buy_price"] = v * (1 - BOOK_MOS)
        fcf_y = _num(facts.get("fcf_yield"))
        rf = _num(facts.get("risk_free"))
        if fcf_y is not None and rf is not None:
            out["control"]["text"] = f"FCF yield {fcf_y:.1%} vs risk-free {rf:.1%}"
            out["control"]["_says_buy"] = fcf_y >= rf

    bp = out["buy_price"]
    if bp is not None and bp <= 0:
        out["buy_price"], out["note"] = None, "Buy price came out at or below zero"
        bp = None
    if bp is not None and price:
        out["distance"] = price / bp - 1
        says = out["control"].pop("_says_buy", None)
        if says is not None:
            out["control"]["agrees"] = says == (price <= bp)
    out["control"].pop("_says_buy", None)
    return out


# ── Into the stored valuation summary ────────────────────────────────────

def dcf_value_fn(cfg):
    """fair value per share at the hurdle (no margin of safety) with every
    year of the model's revenue growth shifted by `shift`. For capital_heavy:
    the model's own path, minus the buffer."""
    import dcf_calculator

    def value(shift):
        growth = [g + shift for g in cfg["revenue_growth"]]
        return dcf_calculator.compute_intrinsic_value(
            dict(cfg, revenue_growth=growth))["intrinsic_value"]
    return value


def attach(summary, cfg, fund, closes):
    """Add the per-type result to a valuation_summary, in place.

    With a type that can price, the summary's buy_price BECOMES the type's buy
    price, so every reader (watchlist, portfolio, hero pills, MCP) shows one
    number; the DCF's own stays in dcf_buy_price. Without a type, or when an
    input is missing, buy_price stays the DCF one and buy_basis says so.
    Never raises: a failure leaves the DCF summary as it was."""
    summary["dcf_buy_price"] = summary.get("buy_price")
    summary["buy_basis"] = "dcf"
    vt = cfg.get("valuation_type")
    if not isinstance(vt, dict) or vt.get("type") not in TYPES:
        summary.pop("by_type", None)
        return summary
    try:
        price = _num(cfg.get("stock_price"))
        facts = facts_from(cfg, fund, closes, price)
        dcf_fn = dcf_value_fn(cfg) if cfg.get("revenue_growth") else None
        res = evaluate(vt, price, facts, dcf_value=dcf_fn,
                       implied_return=summary.get("implied_return"))
    except Exception as e:   # the DCF summary must survive
        summary["by_type"] = {"type": vt.get("type"), "label": TYPES.get(vt.get("type")),
                              "buy_price": None, "note": f"Failed: {e}"}
        return summary
    res["facts"] = {k: facts.get(k) for k in ("eps", "dps", "pe_history",
                                              "tangible_book_ps", "fcf_yield")}
    summary["by_type"] = res
    if res.get("buy_price") is not None:
        summary["buy_price"] = round(res["buy_price"], 2)
        summary["buy_basis"] = "type"
    return summary
