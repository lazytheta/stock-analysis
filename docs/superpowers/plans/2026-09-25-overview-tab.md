# Overview Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A first "Overview" tab on the ticker page with a company profile, a price-vs-S&P chart fed by a daily Nasdaq price-history job, and four columns of key figures from EDGAR.

**Architecture:** Pure, tested modules do the work — `overview_metrics.py` (key figures from existing EDGAR fetchers), `price_history.py` (Nasdaq history parsing + incremental job logic), `overview_chart.py` (range/percent/CAGR maths + Plotly figure), `company_profile.py` (the Claude-authored "Company Profile" section: prompt + validation) and `overview_page.py` (HTML for profile panel and figure columns). `streamlit_app.py` only wires them into a new first tab. A Cloud Run Job (`scripts/run_price_history.py`) fills a shared Supabase table `price_history`.

**Tech Stack:** Python 3.11+ (job image 3.13), Streamlit 1.54, Plotly 6.5, supabase-py, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-overview-tab-design.md`

## Global Constraints

- Tab order: `["Overview", "Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF", "Reverse DCF", "Peer Comparison", "Dividend", "History"]`; Overview is first (Streamlit opens the first tab).
- House style: white section `.qc-section` look (24px radius, 3px `var(--accent)` top border, `var(--shadow)`), flat inner panels `color-mix(in srgb, var(--text) 4%, var(--card))`, 16px radius; labels in the `.qc-label` style. CSS must be emitted on ONE line (`question_cards.css(...)`) and every dynamic text through `question_cards.esc()` (turns `$` into `&#36;`, else Streamlit renders LaTeX). HTML passed to `st.markdown(..., unsafe_allow_html=True)` must start with a tag.
- Missing value → `—` (em dash), never 0 and never an exception.
- Units: fundamentals/cash-flow/income amounts are $M; `fund["shares"]` is a raw count; EPS in dollars.
- Formatting: percentages one decimal with sign for growth/returns (`+12.6%`), margins one decimal (`48.5%`), multiples one decimal with `×` (`31.7×`), money via `$9.1B` (≥ 1000 M, one decimal) / `$510M` (< 1000 M, whole).
- Company Profile section title exactly `"Company Profile"`; capital_type ∈ {"Asset-light","Asset-heavy"}; difficulty ∈ {"Easy","Moderate","Hard"}.
- Table `price_history(ticker text, day date, close numeric, primary key (ticker, day))`; job name `price-history`; schedule `30 23 * * 1-5` Europe/Amsterdam; history window 10 years; upsert batches of 500; 0.3 s pause between tickers.
- Range buttons `1M 6M YTD 1Y 3Y 5Y 10Y`, default `5Y`; CAGR only shown for ranges of at least 1 year.
- Tests offline; `python3 -m ruff check .` clean; comment language follows the file (new modules: English docstrings are fine; match nearby style in streamlit_app.py).

---

### Task 1: Key figures — `overview_metrics.py`

**Files:**
- Create: `overview_metrics.py`
- Create: `tests/test_overview_metrics.py`

**Interfaces:**
- Consumes: dicts shaped like `gather_data.fetch_fundamentals` / `fetch_income_statement` / `fetch_cashflow_statement` output: each has `"years": [int, ...]` and aligned lists (values may be `None`).
- Produces:
  - `compute(fund: dict, income: dict | None, cashflow: dict | None, price: float | None) -> dict` returning
    `{"fy": int|None, "profitability": [(label, value)], "health": [(label, value)], "growth": [(label, horizon, value)], "valuation": [(label, value)], "returns": [(label, value)]}` where values are raw floats (fractions for percentages, e.g. 0.485) or `None`.
  - `fmt_pct(x, signed=False) -> str`, `fmt_mult(x) -> str`, `fmt_money_m(x) -> str` (all return `"—"` for None).

- [ ] **Step 1: Write failing tests** — `tests/test_overview_metrics.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import overview_metrics as om

YEARS = list(range(2016, 2027))  # 11 years, FY2026 last


def _fund(**over):
    n = len(YEARS)
    base = {
        "years": YEARS,
        "revenue": [100.0 * 1.1 ** i for i in range(n)],
        "gross_profit": [50.0 * 1.1 ** i for i in range(n)],
        "operating_income": [30.0 * 1.1 ** i for i in range(n)],
        "net_income": [20.0 * 1.1 ** i for i in range(n)],
        "fcf": [25.0 * 1.1 ** i for i in range(n)],
        "cash": [40.0] * n,
        "short_term_investments": [10.0] * n,
        "total_debt": [60.0] * n,
        "total_equity": [120.0] * n,
        "shares": [1_000_000.0] * n,          # raw count
        "eps": [2.0 * 1.2 ** i for i in range(n)],
    }
    base.update(over)
    return base


def _income(**over):
    base = {"years": YEARS, "interest_expense": [5.0] * len(YEARS)}
    base.update(over)
    return base


def _cash(**over):
    n = len(YEARS)
    base = {"years": YEARS, "dividends_paid": [-3.0] * n,
            "stock_buybacks": [-6.0] * n, "debt_repayment": [-4.0] * n,
            "debt_issuance": [1.0] * n}
    base.update(over)
    return base


def _get(pairs, label):
    return dict(pairs)[label]


def test_fiscal_year_is_last_year_with_revenue():
    f = _fund()
    f["revenue"][-1] = None
    assert om.compute(f, _income(), _cash(), 100.0)["fy"] == 2025


def test_profitability_margins():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    p = out["profitability"]
    assert abs(_get(p, "Gross margin") - 0.5) < 1e-9
    assert abs(_get(p, "Operating margin") - 0.3) < 1e-9
    assert abs(_get(p, "Net margin") - 0.2) < 1e-9
    assert abs(_get(p, "FCF margin") - 0.25) < 1e-9


def test_financial_health():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    h = out["health"]
    assert _get(h, "Cash & investments") == 50.0
    assert _get(h, "Total debt") == 60.0
    assert abs(_get(h, "Debt / Equity") - 0.5) < 1e-9
    ebit = 30.0 * 1.1 ** 10
    assert abs(_get(h, "EBIT / Interest") - ebit / 5.0) < 1e-9


def test_negative_equity_and_no_interest_give_none():
    f = _fund(total_equity=[-5.0] * len(YEARS))
    out = om.compute(f, _income(interest_expense=[0.0] * len(YEARS)), _cash(), 100.0)
    assert _get(out["health"], "Debt / Equity") is None
    assert _get(out["health"], "EBIT / Interest") is None
    assert _get(out["valuation"], "P/B") is None


def test_growth_cagr_per_horizon():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    g = {(lab, h): v for lab, h, v in out["growth"]}
    assert abs(g[("Revenue", 3)] - 0.10) < 1e-9
    assert abs(g[("Revenue", 10)] - 0.10) < 1e-9
    assert abs(g[("EPS", 5)] - 0.20) < 1e-9
    assert abs(g[("FCF", 3)] - 0.10) < 1e-9


def test_growth_none_when_start_not_positive_or_too_short():
    n = len(YEARS)
    f = _fund(fcf=[-1.0] * (n - 3) + [5.0, 6.0, 7.0])
    out = om.compute(f, _income(), _cash(), 100.0)
    g = {(lab, h): v for lab, h, v in out["growth"]}
    assert g[("FCF", 5)] is None
    short = {k: (v[-4:] if isinstance(v, list) else v) for k, v in _fund().items()}
    out2 = om.compute(short, None, None, 100.0)
    g2 = {(lab, h): v for lab, h, v in out2["growth"]}
    assert g2[("Revenue", 3)] is not None and g2[("Revenue", 5)] is None


def test_valuation_and_returns_at_price():
    out = om.compute(_fund(), _income(), _cash(), 100.0)
    mcap = 100.0 * 1_000_000 / 1e6            # $100M
    rev = 100.0 * 1.1 ** 10
    v = out["valuation"]
    assert abs(_get(v, "P/S") - mcap / rev) < 1e-9
    assert abs(_get(v, "P/E") - 100.0 / (2.0 * 1.2 ** 10)) < 1e-9
    assert abs(_get(v, "P/B") - mcap / 120.0) < 1e-9
    r = out["returns"]
    assert abs(_get(r, "Dividend yield") - 0.03) < 1e-9
    assert abs(_get(r, "Buyback yield") - 0.06) < 1e-9
    assert abs(_get(r, "Debt paydown yield") - 0.03) < 1e-9
    assert abs(_get(r, "Total shareholder yield") - 0.12) < 1e-9


def test_no_price_means_no_valuation_or_returns():
    out = om.compute(_fund(), _income(), _cash(), None)
    assert all(v is None for _, v in out["valuation"])
    assert all(v is None for _, v in out["returns"])


def test_missing_statements_are_tolerated():
    out = om.compute(_fund(), None, None, 100.0)
    assert _get(out["health"], "EBIT / Interest") is None
    assert _get(out["returns"], "Dividend yield") is None
    assert _get(out["returns"], "Total shareholder yield") is None


def test_formatters():
    assert om.fmt_pct(0.4851) == "48.5%"
    assert om.fmt_pct(0.126, signed=True) == "+12.6%"
    assert om.fmt_pct(-0.02, signed=True) == "-2.0%"
    assert om.fmt_mult(31.72) == "31.7×"
    assert om.fmt_money_m(9120.0) == "$9.1B"
    assert om.fmt_money_m(510.4) == "$510M"
    assert om.fmt_pct(None) == om.fmt_mult(None) == om.fmt_money_m(None) == "—"
```

- [ ] **Step 2: Run** — `python3 -m pytest tests/test_overview_metrics.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement** `overview_metrics.py`:

```python
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
```

- [ ] **Step 4: Run** — tests PASS; `python3 -m ruff check overview_metrics.py tests/test_overview_metrics.py` clean.

- [ ] **Step 5: Commit** — `git add overview_metrics.py tests/test_overview_metrics.py && git commit -m "Overview: key figures from EDGAR statements"`

---

### Task 2: Price history — `price_history.py`, job script, image and deploy script

**Files:**
- Create: `price_history.py`
- Create: `scripts/run_price_history.py`
- Create: `Dockerfile.prices`, `cloudbuild.prices.yaml`, `scripts/deploy_price_job.sh`
- Create: `docs/sql/price_history.sql`
- Create: `tests/test_price_history.py`

**Interfaces:**
- Consumes: `quotes._nasdaq_symbol_ok(ticker) -> bool` (exists in quotes.py).
- Produces:
  - `price_history.HISTORY_URL` (format keys `symbol`, `cls`, `start`, `end`)
  - `parse_rows(payload) -> list[tuple[str, float]]` — `[("YYYY-MM-DD", close), ...]` ascending by date, skipping rows with unparseable date/close or close ≤ 0; `[]` for `data: null`.
  - `fetch_history(symbol, start: date, end: date, fetch=None) -> list[tuple[str, float]]` — stocks first, etf when stocks yields no rows.
  - `start_date(last_day: date | None, today: date, years: int = 10) -> date`
  - `update_ticker(ticker, today, last_day, fetch, upsert, batch=500) -> int` — rows written.
  - `run(client, today=None, fetch=None, sleep=time.sleep) -> dict` — `{"tickers": n, "rows": n, "errors": [ticker, ...]}`.
  - `load_series(client, tickers: list[str], since: date) -> dict[str, list[tuple[str, float]]]` for the app (select from `price_history`, ascending).

- [ ] **Step 1: Write failing tests** — `tests/test_price_history.py`:

```python
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import price_history as ph


def _payload(rows):
    return {"data": {"tradesTable": {"rows": rows}}}


def test_parse_rows_sorts_ascending_and_cleans():
    p = _payload([
        {"date": "09/24/2026", "close": "$71.72"},
        {"date": "09/23/2026", "close": "$1,071.36"},
        {"date": "bad", "close": "$1.00"},
        {"date": "09/22/2026", "close": "N/A"},
    ])
    assert ph.parse_rows(p) == [("2026-09-23", 1071.36), ("2026-09-24", 71.72)]


def test_parse_rows_null_data():
    assert ph.parse_rows({"data": None}) == []
    assert ph.parse_rows(None) == []


def test_fetch_history_falls_back_to_etf():
    calls = []

    def fetch(url):
        calls.append(url)
        if "assetclass=etf" in url:
            return _payload([{"date": "09/24/2026", "close": "$760.00"}])
        return {"data": None}

    out = ph.fetch_history("SPY", date(2026, 9, 1), date(2026, 9, 25), fetch=fetch)
    assert out == [("2026-09-24", 760.0)]
    assert "fromdate=2026-09-01" in calls[0] and "todate=2026-09-25" in calls[0]
    assert "limit=9999" in calls[0]


def test_start_date_backfill_and_incremental():
    today = date(2026, 9, 25)
    assert ph.start_date(None, today) == date(2016, 9, 25)
    assert ph.start_date(date(2026, 9, 23), today) == date(2026, 9, 24)


def test_update_ticker_upserts_in_batches():
    rows = [(f"2026-01-{d:02d}", float(d)) for d in range(1, 8)]
    batches = []
    n = ph.update_ticker("NFLX", date(2026, 9, 25), None,
                         fetch=lambda s, a, b: rows,
                         upsert=lambda recs: batches.append(recs), batch=3)
    assert n == 7
    assert [len(b) for b in batches] == [3, 3, 1]
    assert batches[0][0] == {"ticker": "NFLX", "day": "2026-01-01", "close": 1.0}


def test_update_ticker_nothing_new_when_up_to_date():
    called = []
    n = ph.update_ticker("NFLX", date(2026, 9, 25), date(2026, 9, 25),
                         fetch=lambda *a: called.append(a) or [],
                         upsert=lambda recs: None)
    assert n == 0 and called == []


class FakeTable:
    def __init__(self, db, name):
        self.db, self.name, self.filters, self._order, self._limit = db, name, [], None, None
        self._payload, self._select = None, None

    def select(self, cols):
        self._select = cols
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self.filters.append(("in", col, list(vals)))
        return self

    def gte(self, col, val):
        self.filters.append(("gte", col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def upsert(self, recs, on_conflict=None):
        self.db.setdefault("upserts", []).append((self.name, recs, on_conflict))
        self._payload = recs
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.db.get(("data", self.name), lambda t: [])(self)
        return r


class FakeClient:
    def __init__(self, db):
        self.db = db

    def table(self, name):
        return FakeTable(self.db, name)


def test_run_collects_tickers_skips_non_us_and_continues_on_error():
    db = {
        ("data", "watchlist_configs"): lambda t: [
            {"ticker": "NFLX"}, {"ticker": "RMS.PA"}, {"ticker": "NFLX"},
            {"ticker": "BOOM"}],
        ("data", "price_history"): lambda t: [],
    }

    def fetch(symbol, start, end):
        if symbol == "BOOM":
            raise RuntimeError("down")
        return [("2026-09-24", 1.0)]

    out = ph.run(FakeClient(db), today=date(2026, 9, 25), fetch=fetch,
                 sleep=lambda s: None)
    assert out["tickers"] == 3          # NFLX, BOOM, SPY (RMS.PA skipped)
    assert out["errors"] == ["BOOM"]
    assert out["rows"] == 2
    written = [recs[0]["ticker"] for name, recs, oc in db["upserts"]]
    assert sorted(written) == ["NFLX", "SPY"]
    assert all(oc == "ticker,day" for _, _, oc in db["upserts"])
```

- [ ] **Step 2: Run** — FAIL (module missing).

- [ ] **Step 3: Implement** `price_history.py`:

```python
"""Daily closes from Nasdaq into Supabase `price_history`, and reading them back.

Why a table: Yahoo blocks the app's hosts, and a chart that fetched ten years
of history on every page view would be exactly the traffic that gets a source
blocked. A Cloud Run Job (scripts/run_price_history.py) writes once per
trading day; the app only reads.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import quotes

logger = logging.getLogger(__name__)

HISTORY_URL = ("https://api.nasdaq.com/api/quote/{symbol}/historical"
               "?assetclass={cls}&fromdate={start}&todate={end}&limit=9999")
TABLE = "price_history"
YEARS = 10
BENCHMARK = "SPY"


def _close(text):
    if not isinstance(text, str):
        return None
    try:
        value = float(text.replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def parse_rows(payload) -> list:
    data = payload.get("data") if isinstance(payload, dict) else None
    table = (data or {}).get("tradesTable") if isinstance(data, dict) else None
    rows = (table or {}).get("rows") or []
    out = []
    for row in rows:
        try:
            day = datetime.strptime(row.get("date", ""), "%m/%d/%Y").date()
        except (ValueError, TypeError, AttributeError):
            continue
        close = _close(row.get("close"))
        if close is not None:
            out.append((day.isoformat(), close))
    return sorted(out)


def fetch_history(symbol, start, end, fetch=None) -> list:
    fetch = fetch or quotes._nasdaq_get
    for cls in ("stocks", "etf"):
        rows = parse_rows(fetch(HISTORY_URL.format(
            symbol=symbol, cls=cls, start=start.isoformat(), end=end.isoformat())))
        if rows:
            return rows
    return []


def start_date(last_day, today, years: int = YEARS):
    if last_day is None:
        return today.replace(year=today.year - years)
    return last_day + timedelta(days=1)


def update_ticker(ticker, today, last_day, fetch, upsert, batch=500) -> int:
    start = start_date(last_day, today)
    if start > today:
        return 0
    rows = fetch(ticker, start, today)
    records = [{"ticker": ticker, "day": d, "close": c} for d, c in rows]
    for i in range(0, len(records), batch):
        upsert(records[i:i + batch])
    return len(records)


def _tickers(client) -> list:
    rows = client.table("watchlist_configs").select("ticker").execute().data or []
    names = [r.get("ticker") for r in rows]
    us = [t for t in dict.fromkeys(names) if quotes._nasdaq_symbol_ok(t)]
    return [t for t in us if t != BENCHMARK] + [BENCHMARK]


def _last_day(client, ticker):
    rows = (client.table(TABLE).select("day").eq("ticker", ticker)
            .order("day", desc=True).limit(1).execute().data or [])
    return date.fromisoformat(rows[0]["day"]) if rows else None


def run(client, today=None, fetch=None, sleep=time.sleep) -> dict:
    today = today or date.today()
    fetch = fetch or fetch_history
    tickers = _tickers(client)

    def upsert(records):
        client.table(TABLE).upsert(records, on_conflict="ticker,day").execute()

    total, errors = 0, []
    for i, ticker in enumerate(tickers):
        if i:
            sleep(0.3)
        try:
            total += update_ticker(ticker, today, _last_day(client, ticker),
                                   fetch, upsert)
        except Exception as e:
            logger.warning("price history for %s failed: %s: %s",
                           ticker, type(e).__name__, e)
            errors.append(ticker)
    return {"tickers": len(tickers), "rows": total, "errors": errors}


def load_series(client, tickers, since) -> dict:
    """{ticker: [(iso_day, close), ...]} ascending, paging past the 1000-row cap."""
    out = {t: [] for t in tickers}
    for ticker in tickers:
        start = 0
        while True:
            data = (client.table(TABLE).select("day, close").eq("ticker", ticker)
                    .gte("day", since.isoformat()).order("day")
                    .range(start, start + 999).execute().data or [])
            out[ticker].extend((r["day"], float(r["close"])) for r in data)
            if len(data) < 1000:
                break
            start += 1000
    return out
```

Note for the implementer: the FakeClient in the tests returns the same rows for any `range`; `load_series` is not unit-tested with paging here (add a test that returns 1000 rows on the first page and 5 on the second if you extend the fake — optional, keep it small).

- [ ] **Step 4: Job script** — `scripts/run_price_history.py`:

```python
"""Cloud Run Job: add the latest Nasdaq daily closes to Supabase price_history."""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import price_history  # noqa: E402


def main():
    logging.basicConfig(level=logging.INFO)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"],
                           os.environ["SUPABASE_SERVICE_KEY"])
    result = price_history.run(client)
    print(f"price_history: {result}")
    if result["errors"] and len(result["errors"]) == result["tickers"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Image + deploy** — `Dockerfile.prices`: copy the structure of `Dockerfile.screener` (same base image `python:3.13-slim`, same way of installing requirements — read that file and mirror it) but COPY only `gather_data.py quotes.py price_history.py` and `scripts/run_price_history.py`, `CMD ["python", "scripts/run_price_history.py"]`. `cloudbuild.prices.yaml`: copy `cloudbuild.screener.yaml`, replacing `Dockerfile.screener` → `Dockerfile.prices` and image name `screener` → `price-history`. `scripts/deploy_price_job.sh`: copy `scripts/deploy_screener_job.sh` with `JOB="price-history"`, `SCHEDULE="30 23 * * 1-5"`, `--memory 512Mi`, `--task-timeout 3600`, config `cloudbuild.prices.yaml`, scheduler name `$JOB-daily`, comments updated (daily after the US close). `chmod +x`.

- [ ] **Step 6: SQL** — `docs/sql/price_history.sql`:

```sql
-- Shared daily closes (no user_id). Written by the price-history Cloud Run Job
-- with the service key; read by any logged-in user.
create table if not exists public.price_history (
  ticker text not null,
  day date not null,
  close numeric not null,
  primary key (ticker, day)
);
alter table public.price_history enable row level security;
drop policy if exists price_history_read on public.price_history;
create policy price_history_read on public.price_history
  for select to authenticated using (true);
```

- [ ] **Step 7: Run** — `python3 -m pytest tests/test_price_history.py -q` PASS; `python3 -m ruff check .` clean; `bash -n scripts/deploy_price_job.sh` OK. Do NOT run the deploy script or touch Supabase.

- [ ] **Step 8: Commit** — `git add price_history.py scripts/run_price_history.py Dockerfile.prices cloudbuild.prices.yaml scripts/deploy_price_job.sh docs/sql/price_history.sql tests/test_price_history.py && git commit -m "price_history: Nasdaq daily closes job and reader"`

---

### Task 3: Chart maths and figure — `overview_chart.py`

**Files:**
- Create: `overview_chart.py`
- Create: `tests/test_overview_chart.py`

**Interfaces:**
- Consumes: series `[(iso_day, close), ...]` ascending (from `price_history.load_series`).
- Produces:
  - `RANGES = ("1M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y")`, `DEFAULT_RANGE = "5Y"`
  - `with_live_point(series, price, today: date) -> list` — appends `(today.isoformat(), price)` when price > 0 and today is after the last day (replaces nothing otherwise).
  - `range_start(rng: str, last_day: date) -> date` — 1M = −1 month, 6M = −6 months, YTD = Jan 1 of last_day's year, nY = −n years (use `last_day.replace(year=...)`, clamp Feb 29 to Feb 28; months via calendar arithmetic, clamping the day).
  - `aligned_pct(stock, bench, start: date) -> tuple[list[str], list[float], list[float]]` — dates where both series have a close on/after `start` (inner join on day), each series as percent change vs its first close in that window (fractions, 0.0 at the first point). Empty lists when fewer than 2 common points.
  - `total_and_cagr(pcts, days, min_years=1.0) -> tuple[float|None, float|None]` — total = last pct; CAGR = (1+total)^(365.25/span_days) − 1 when span ≥ min_years×365, else None. `days` are ISO strings.
  - `figure(dates, stock_pcts, bench_pcts, ticker, theme) -> plotly.graph_objects.Figure` — two lines (stock: `theme["accent"]` with a light fill to zero; S&P 500: `theme["bench"]`), y-axis as percent (`tickformat=".0%"`), transparent background, no legend (labels are in the header), height 360, margins small, hovermode "x unified", `showgrid` light.
  - `header_html(ticker, rng, stock_total, stock_cagr, bench_total, bench_cagr) -> str` — two lines as in the spec: `{TICKER} · {rng} <b>+40.0%</b> CAGR +7.0%` and `S&P 500 <b>+68.2%</b> CAGR +11.0%`, CAGR part omitted when None; green for ≥ 0, red for < 0 (use `var(--green, #2e7d32)` / `var(--red, #c62828)` inline colours); all text through `question_cards.esc`.

- [ ] **Step 1: Write failing tests** — `tests/test_overview_chart.py` covering, with exact expected values:
  - `with_live_point([("2026-09-24", 10.0)], 11.0, date(2026, 9, 25))` → appends `("2026-09-25", 11.0)`; with `date(2026, 9, 24)` → unchanged; with price 0/None → unchanged.
  - `range_start("1M", date(2026, 3, 31)) == date(2026, 2, 28)`; `range_start("6M", date(2026, 9, 25)) == date(2026, 3, 25)`; `range_start("YTD", date(2026, 9, 25)) == date(2026, 1, 1)`; `range_start("5Y", date(2024, 2, 29)) == date(2019, 2, 28)`; `range_start("10Y", date(2026, 9, 25)) == date(2016, 9, 25)`.
  - `aligned_pct` with stock `[("2026-01-01",10),("2026-01-02",11),("2026-01-05",12)]`, bench `[("2026-01-02",100),("2026-01-05",110)]`, start `2026-01-01` → dates `["2026-01-02","2026-01-05"]`, stock `[0.0, 12/11-1]`, bench `[0.0, 0.1]`; fewer than 2 common points → `([], [], [])`.
  - `total_and_cagr([0.0, 0.21], ["2024-09-25", "2026-09-25"])` → total 0.21, CAGR ≈ 0.1 (abs tol 1e-3); span < 1 year → CAGR None; empty → `(None, None)`.
  - `header_html("NFLX", "5Y", 0.4, 0.07, 0.682, None)` contains `NFLX · 5Y`, `+40.0%`, `CAGR +7.0%`, `S&amp;P 500`, `+68.2%`, no second `CAGR`, and no raw `$`.
  - `figure(...)` returns a Figure with 2 traces whose `y` equal the inputs.

- [ ] **Step 2: Run** — FAIL.

- [ ] **Step 3: Implement** `overview_chart.py` to the interface above (import `plotly.graph_objects as go` at module top; `question_cards` for `esc`). Keep it one file, no Streamlit import.

- [ ] **Step 4: Run** — PASS; ruff clean.

- [ ] **Step 5: Commit** — `git add overview_chart.py tests/test_overview_chart.py && git commit -m "Overview: price-vs-S&P chart maths and figure"`

---

### Task 4: "Company Profile" section — `company_profile.py`, MCP, prompt, routines

**Files:**
- Create: `company_profile.py`
- Create: `tests/test_company_profile.py`
- Modify: `mcp_server.py` (`_CARD_PARSERS`, ≈ line 79)
- Modify: `Dockerfile` (MCP COPY list, ≈ lines 14-18: add `company_profile.py`)
- Modify: `streamlit_app.py` (`DEFAULT_AI_PROMPTS`: new entry directly after the `business_cards.TITLE` entry ≈ line 1848; import `company_profile` next to `import business_cards`)
- Modify: `docs/routines/moat-cards-backfill.md`, `docs/routines/aspirant-weekly.md`

**Interfaces:**
- Produces: `company_profile.TITLE = "Company Profile"`, `company_profile.PROMPT: str`, `company_profile.parse_company_profile(content: str) -> dict` (raises `ValueError` with a readable message), `CAPITAL_TYPES = ("Asset-light", "Asset-heavy")`, `DIFFICULTIES = ("Easy", "Moderate", "Hard")`.

- [ ] **Step 1: Read** `business_cards.py` (how PROMPT uses `{company}`, `{ticker}`, `{prior:...}` placeholders and how the fenced JSON block is extracted — reuse `question_cards`' JSON-extraction helper if business_cards does; otherwise mirror its code) and `mcp_server.py` `_CARD_PARSERS` usage.

- [ ] **Step 2: Write failing tests** — `tests/test_company_profile.py`:

```python
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import company_profile as cp

GOOD = {"sector": "Communication Services", "industry": "Entertainment",
        "capital_type": "Asset-light", "difficulty": "Moderate", "founded": 1997,
        "employees": 16000, "tags": ["Subscription", "Ad-based"],
        "mission": "To entertain the world."}


def _md(obj):
    return "```json\n" + json.dumps(obj) + "\n```"


def test_valid_profile_parses():
    assert cp.parse_company_profile(_md(GOOD)) == GOOD


def test_nulls_allowed_for_founded_and_employees():
    obj = dict(GOOD, founded=None, employees=None)
    assert cp.parse_company_profile(_md(obj))["employees"] is None


@pytest.mark.parametrize("field,value", [
    ("sector", ""), ("industry", "x" * 61), ("capital_type", "Light"),
    ("difficulty", "Medium"), ("founded", 1500), ("founded", 3000),
    ("founded", "1997"), ("founded", True), ("employees", 0),
    ("employees", 12.5), ("tags", []), ("tags", ["a", "b", "c", "d", "e"]),
    ("tags", ["dup", "dup"]), ("tags", ["x" * 25]), ("tags", [""]),
    ("mission", ""), ("mission", "x" * 201)])
def test_invalid_fields_raise(field, value):
    with pytest.raises(ValueError):
        cp.parse_company_profile(_md(dict(GOOD, **{field: value})))


def test_missing_field_and_bad_json_raise():
    obj = dict(GOOD)
    del obj["mission"]
    with pytest.raises(ValueError):
        cp.parse_company_profile(_md(obj))
    with pytest.raises(ValueError):
        cp.parse_company_profile("no json here")


def test_prompt_mentions_every_field_and_priors():
    for word in ("sector", "industry", "capital_type", "difficulty", "founded",
                 "employees", "tags", "mission", "{prior:Business Analysis}",
                 "{prior:Moat Analysis}", "{company}", "{ticker}"):
        assert word in cp.PROMPT


def test_mcp_validates_company_profile():
    import mcp_server
    assert mcp_server._CARD_PARSERS[cp.TITLE] is cp.parse_company_profile


def test_default_prompt_after_business_cards():
    src = Path(__file__).resolve().parent.parent.joinpath("streamlit_app.py").read_text()
    assert src.index("business_cards.TITLE") < src.index("company_profile.TITLE")
```

- [ ] **Step 3: Implement** `company_profile.py`: `TITLE`, the two tuples, `PROMPT` (English, same voice as business_cards.PROMPT): context `{prior:Business Analysis}` and `{prior:Moat Analysis}` for `**{company} ({ticker})**`; instructions per field — sector/industry in GICS wording; capital_type Asset-light when the business needs little PP&E/inventory to grow (software, platforms, brands) else Asset-heavy; difficulty = how hard the business is to understand (Easy: one product and a simple revenue model; Moderate: several segments or a less obvious model; Hard: conglomerates, banks/insurers, biotech pipelines, heavy accounting judgement); founded = founding year; employees = full-time employees from the latest 10-K (null if not stated); tags = 2–4 short business-model tags (e.g. Subscription, Marketplace, Ad-based, Recurring revenue); mission = the company's own mission statement if it has one, else one plain sentence ≤ 200 characters. Never estimate; use null where the field allows it. Output ONLY a fenced JSON block (show the example from the spec). `parse_company_profile` validates exactly the rules in the spec (bool is not int; strip strings; tags unique case-insensitively) and returns the dict with only the eight keys.

- [ ] **Step 4: Wire** — add to `mcp_server._CARD_PARSERS`: `company_profile.TITLE: company_profile.parse_company_profile` (+ `import company_profile`); add `company_profile.py` to the MCP `Dockerfile` COPY line; add the `DEFAULT_AI_PROMPTS` entry `{"title": company_profile.TITLE, "prompt": company_profile.PROMPT}` directly after the Business Cards entry (with a one-line comment like the neighbours); `import company_profile` next to `import business_cards` in streamlit_app.py. Check the Pre-Scan tab's rendering branch that renders JSON sections (search `business_cards.TITLE` near the Pre-Scan if/elif, ≈ line 8393): add a branch so "Company Profile" shows as a readable key/value list (use `overview_page`-independent simple HTML: `<ul>` of `<b>Label</b>: value`, escaped) instead of raw JSON.

- [ ] **Step 5: Routines** — `docs/routines/moat-cards-backfill.md`: add "Company Profile" as a fourth set (after Business), same flow: `tickers_missing_section("Company Profile", requires=...)` — use the same `requires` as the Business set, fill with the prompt from `get_prescan_prompts`, save with `save_prescan_section`; the 10-per-run cap stays shared across all sets. `docs/routines/aspirant-weekly.md`: in the pre-scan step, add "Company Profile" after "Business Cards" in the list of sections to fill. Read both files first and follow their existing structure and wording.

- [ ] **Step 6: Run** — `python3 -m pytest tests/test_company_profile.py tests/test_business_cards.py test_mcp_server.py -q` PASS; `cd lazytheta-mcp-cloudrun && python3 -m pytest -q` PASS; ruff clean.

- [ ] **Step 7: Commit** — `git add company_profile.py tests/test_company_profile.py mcp_server.py Dockerfile streamlit_app.py docs/routines/moat-cards-backfill.md docs/routines/aspirant-weekly.md && git commit -m "Company Profile section: prompt, validation, MCP, routines"`

---

### Task 5: The Overview tab — `overview_page.py` + wiring in `streamlit_app.py`

**Files:**
- Create: `overview_page.py`
- Create: `tests/test_overview_page.py`
- Modify: `streamlit_app.py` (tab list ≈ 5159; new `with _tab_overview:` block; global CSS block near the other `.st-key-qc_*` rules ≈ 2820-2850; a cached loader for statements and price history)
- Modify: `tests/test_business_cards.py`, `tests/test_moat_cards.py`, `tests/test_risk_cards.py` (tab-list asserts)

**Interfaces:**
- Consumes: `overview_metrics.compute/fmt_*` (Task 1); `price_history.load_series` (Task 2); `overview_chart.*` (Task 3); `company_profile.TITLE`, `parse_company_profile` (Task 4); `question_cards.esc/css/section_html`.
- Produces:
  - `overview_page.profile_panel_html(profile: dict | None, market_cap_m: float | None) -> str`
  - `overview_page.mission_html(profile: dict | None) -> str` (`""` when no profile)
  - `overview_page.metrics_html(metrics: dict) -> str` — the four columns.

- [ ] **Step 1: Tests** — `tests/test_overview_page.py`:
  - `profile_panel_html(GOOD_PROFILE, 343190.0)` contains `SECTOR`, `Communication Services`, `$343.2B`, `Asset-light`, a difficulty chip with `Moderate`, `1997`, `16,000`, both tags, and no raw `$` (dollar signs appear as `&#36;`).
  - `profile_panel_html(None, 510.0)` contains `$510M` → as `&#36;510M` and the text `Company profile not filled yet`.
  - `mission_html(GOOD_PROFILE)` contains `MISSION` and `To entertain the world.`; `mission_html(None) == ""`.
  - `metrics_html(overview_metrics.compute(...))` (build inputs as in test_overview_metrics or a small inline fixture) contains the headings `Profitability`, `Financial Health`, `Growth`, `Valuation`, `Shareholder Returns`, the sub-label `LATEST FISCAL YEAR (FY2026)`, `3Y`, `5Y`, `10Y`, a `×` multiple, an em dash for a None value, starts with `<`, contains no `\n` inside `<style>` and no raw `$`.
  - Tab-order test: in `streamlit_app.py` source, `'["Overview", "Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF"'` is present.
  - Update the three existing tab-list asserts (`tests/test_business_cards.py:197-199`, `tests/test_moat_cards.py:316-318`, `tests/test_risk_cards.py:94-96`) to the new list prefix `'["Overview", "Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF"'`, keeping each test's intent (Business before Moat, etc.).

- [ ] **Step 2: Implement `overview_page.py`** — layout per spec, in the house style:
  - Profile panel: two-column grid of label/value pairs (label: 11px uppercase letter-spaced `var(--text-muted)`; value: 14px `var(--text)`), order Sector, Industry, Market cap, Capital type, Difficulty (chip: small rounded box with a coloured square — Easy green, Moderate `var(--accent)`, Hard red), Founded, Employees (`{:,}`), then TAGS as chips (rounded 8px, `var(--qc-inner)` background). Values None → `—`. No profile → only Market cap + the muted line from the spec.
  - Mission: label `MISSION` + italic sentence.
  - Metrics: CSS grid of 3 columns ≥ 900px wide, 1 column below (`@media (max-width: 900px)`): column 1 = Profitability + Financial Health, column 2 = Growth, column 3 = Valuation + Shareholder Returns. Each group: bold title + muted uppercase sub-label (`LATEST FISCAL YEAR (FY{fy})`, `COMPOUND ANNUAL GROWTH`, `AT CURRENT PRICE`, none for Financial Health / Shareholder Returns), then rows `label … value` with a hairline bottom border (`color-mix(in srgb, var(--text) 10%, transparent)`). Growth rows: `Revenue  3Y  +12.6%` (horizon as a muted small label). Formatting via `overview_metrics.fmt_*` (money for cash/debt, `fmt_mult` for D/E, EBIT/Interest and valuation, `fmt_pct` for margins, `fmt_pct(signed=True)` for growth and yields). Emit CSS once per function via `question_cards.css(...)`, escape everything with `question_cards.esc`.

- [ ] **Step 3: Wire into `streamlit_app.py`**
  - Tabs: `(_tab_overview, _tab_notes, _tab_business, ...) = st.tabs(["Overview", "Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF", "Reverse DCF", "Peer Comparison", "Dividend", "History"])`.
  - Cached loaders at module level near the other `@st.cache_data` helpers:
    ```python
    @st.cache_data(ttl=86400, show_spinner=False)
    def _overview_statements(ticker):
        try:
            inc = fetch_income_statement(ticker, n_years=11)
        except Exception as e:
            logger.warning("income statement for %s failed: %s", ticker, e)
            inc = None
        try:
            cf = fetch_cashflow_statement(ticker, n_years=11)
        except Exception as e:
            logger.warning("cash flow statement for %s failed: %s", ticker, e)
            cf = None
        return inc, cf
    ```
    and a price-history loader keyed on `(ticker, since_iso)` with `ttl=3600` that calls `price_history.load_series(_sb_client, [ticker, "SPY"], since)` — `_sb_client` is not hashable, so read it from `st.session_state["supabase_client"]` inside the function (the way other cached readers in the file do; check `_screener_snapshot` ≈ line 12942 for the pattern) and return `{}` on any exception (log a warning). Import `fetch_income_statement`, `fetch_cashflow_statement` from gather_data alongside the existing gather_data imports.
  - `with _tab_overview:` inside `st.container(key="qc_overview_section")`:
    1. `st.markdown('<div class="qc-label">Overview</div>', unsafe_allow_html=True)`
    2. Profile: parse `cfg.get('ai_notes', {}).get(company_profile.TITLE)` with `parse_company_profile` inside try/except (invalid → treat as None). Market cap = `live_price × latest fund["shares"] / 1e6` using the fundamentals the page already loads (`_cached_fundamentals(ticker)` + `apply_fundamentals_overrides(...)` as the Fundamentals tab does — reuse, do not fetch twice; if the Fundamentals tab's fetch lives inside its own `with` block, hoist the cached call so both tabs use the same result).
    3. `left, right = st.columns([2, 3])`: left → `overview_page.profile_panel_html(...)`; right → `st.segmented_control("Range", overview_chart.RANGES, default=overview_chart.DEFAULT_RANGE, key=f"ov_range_{ticker}", label_visibility="collapsed")` (None selection → default), then load series since `today − 10 years`, `with_live_point` on the stock series with `live_price`, `aligned_pct` from `range_start`, header via `header_html`, `st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})`; no data → `st.caption("No price history for this listing yet.")`. Theme dict: `{"accent": T["accent"], "bench": "#5b6cff"}` — use the page's existing theme object (the Business tab passes `T`; reuse the same variable).
    4. `overview_page.mission_html(profile)` (skip when empty).
    5. `overview_page.metrics_html(overview_metrics.compute(fund, inc, cf, live_price))`.
  - CSS (global block, one rule per line like its neighbours): `.st-key-qc_overview_section{...}` identical to `.st-key-qc_revenue_section` (white section, accent top border, 24px radius, shadow, padding).

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_overview_page.py tests/test_business_cards.py tests/test_moat_cards.py tests/test_risk_cards.py -q` PASS; full suite `python3 -m pytest -q --ignore=lazytheta-mcp-cloudrun --ignore=tastytrade-mcp-cloudrun` (only the known `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer` may fail); ruff clean; `python3 -c "import ast;ast.parse(open('streamlit_app.py').read())"`.

- [ ] **Step 5: Commit** — `git add overview_page.py tests/test_overview_page.py streamlit_app.py tests/test_business_cards.py tests/test_moat_cards.py tests/test_risk_cards.py && git commit -m "Overview tab: profile, price chart vs S&P 500, key figures"`

---

## Rollout (controller, after merge)

1. Supabase: apply `docs/sql/price_history.sql` via `apply_migration` (name `price_history`).
2. `scripts/deploy_price_job.sh`, then `gcloud run jobs execute price-history --region europe-west4 --wait`; check row counts per ticker (~2500 for 10 years) and SPY present.
3. Guarded SQL insert of `company_profile.PROMPT` after "Business Cards" in the owner's `user_prefs.prefs.ai_prompts` (skip if present).
4. Push `main`; MCP `gcloud run deploy lazytheta-mcp --source . --region europe-west4 --project stock-analysis-489016`.
5. Fill VEEV's "Company Profile" via `save_prescan_section`.
6. Ask the user to Reboot Streamlit; check VEEV → Overview.
