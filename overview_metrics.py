"""Key figures for the Overview tab, from the existing EDGAR fetchers.

Pure functions: the caller fetches (and caches) fundamentals, income and
cash-flow statements and passes a price; nothing here touches the network.
Every figure that cannot be computed is None, rendered as an em dash.
"""

from __future__ import annotations

DASH = "—"
HORIZONS = (3, 5, 10)


def _at(series: dict | None, key: str, year: int | None):
    if not series or year is None:
        return None
    years = series.get("years") or []
    values = series.get(key) or []
    if year not in years:
        return None
    i = years.index(year)
    return values[i] if i < len(values) else None


def _div(a, b, positive_b=False):
    if a is None or b is None:
        return None
    if b == 0 or (positive_b and b <= 0):
        return None
    return a / b


def _cagr(series: dict, key: str, year: int, n: int):
    end = _at(series, key, year)
    start = _at(series, key, year - n)
    if end is None or start is None or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1.0 / n) - 1.0


def _fiscal_year(fund: dict):
    years = fund.get("years") or []
    revenue = fund.get("revenue") or []
    for y, r in sorted(zip(years, revenue), reverse=True):
        if r is not None:
            return y
    return None


def compute(fund: dict, income: dict | None, cashflow: dict | None,
            price: float | None) -> dict:
    fy = _fiscal_year(fund or {})

    def f(key):
        return _at(fund, key, fy)

    revenue = f("revenue")
    op_income = f("operating_income")
    fcf = f("fcf")
    equity = f("total_equity")
    shares = f("shares")
    eps = f("eps")
    cash = f("cash")
    sti = f("short_term_investments")

    cash_inv = None if cash is None else cash + (sti or 0.0)
    interest = _at(income, "interest_expense", fy)
    ebit_int = (_div(op_income, abs(interest))
                if interest not in (None, 0) else None)

    mcap = (price * shares / 1e6
            if price and price > 0 and shares and shares > 0 else None)

    def yield_of(key):
        amount = _at(cashflow, key, fy)
        if mcap is None or amount is None:
            return None
        return abs(amount) / mcap

    repay = _at(cashflow, "debt_repayment", fy)
    issue = _at(cashflow, "debt_issuance", fy)
    paydown = (None if mcap is None or repay is None
               else (abs(repay) - (issue or 0.0)) / mcap)
    div_y, buy_y = yield_of("dividends_paid"), yield_of("stock_buybacks")
    parts = [div_y, buy_y, paydown]
    total = None if any(p is None for p in parts) else sum(parts)

    return {
        "fy": fy,
        "profitability": [
            ("Gross margin", _div(f("gross_profit"), revenue, True)),
            ("Operating margin", _div(op_income, revenue, True)),
            ("Net margin", _div(f("net_income"), revenue, True)),
            ("FCF margin", _div(fcf, revenue, True)),
        ],
        "health": [
            ("Cash & investments", cash_inv),
            ("Total debt", f("total_debt")),
            ("Debt / Equity", _div(f("total_debt"), equity, True)),
            ("EBIT / Interest", ebit_int),
        ],
        "growth": [(label, n, _cagr(fund, key, fy, n) if fy else None)
                   for label, key in (("Revenue", "revenue"), ("EPS", "eps"),
                                      ("FCF", "fcf"))
                   for n in HORIZONS],
        "valuation": [
            ("P/S", _div(mcap, revenue, True)),
            ("P/E", _div(price, eps, True) if mcap is not None else None),
            ("P/B", _div(mcap, equity, True)),
            ("P/FCF", _div(mcap, fcf, True)),
        ],
        "returns": [
            ("Dividend yield", div_y),
            ("Buyback yield", buy_y),
            ("Debt paydown yield", paydown),
            ("Total shareholder yield", total),
        ],
    }


def fmt_pct(x, signed=False) -> str:
    if x is None:
        return DASH
    return f"{x * 100:+.1f}%" if signed else f"{x * 100:.1f}%"


def fmt_mult(x) -> str:
    return DASH if x is None else f"{x:.1f}×"


def fmt_money_m(x) -> str:
    if x is None:
        return DASH
    if abs(x) >= 1000:
        return f"${x / 1000:.1f}B"
    return f"${x:.0f}M"
