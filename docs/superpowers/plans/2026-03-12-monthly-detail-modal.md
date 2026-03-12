# Monthly Detail Modal Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clickable monthly detail modal to the Results page that shows premium collected, net P/L, benchmark comparison, leaders by premium, and leaders/laggards by P/L.

**Architecture:** Add a `fetch_benchmark_monthly_returns()` function to `tastytrade_api.py` that reuses the existing Yahoo Finance monthly chart data. Add helper functions and a `@st.dialog` modal to `streamlit_app.py`. Replace the static HTML month cards with Streamlit buttons that trigger the dialog.

**Tech Stack:** Streamlit (`@st.dialog`), existing theme system (`T` dict), Yahoo Finance chart API, Tastytrade trade data from `cost_basis`.

---

## Chunk 1: Benchmark Monthly Returns

### Task 1: Add `fetch_benchmark_monthly_returns()` to tastytrade_api.py

**Files:**
- Modify: `tastytrade_api.py:541-609` (add new function near existing `_fetch_yearly_returns`)

- [ ] **Step 1: Add `_fetch_monthly_returns` helper**

Add this function right after `_fetch_yearly_returns` (after line 578) in `tastytrade_api.py`:

```python
def _fetch_monthly_returns(symbol):
    """Fetch monthly returns for a Yahoo Finance symbol.

    Returns:
        Dict of {(year, month): return_pct}, e.g. {(2025, 1): 2.3, (2025, 2): -1.1}.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=10y&interval=1mo"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10) as resp:
            data = json.loads(resp.read())
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        from datetime import datetime as _dt
        month_close = {}
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            dt = _dt.utcfromtimestamp(ts)
            month_close[(dt.year, dt.month)] = close

        periods = sorted(month_close.keys())
        returns = {}
        for i in range(1, len(periods)):
            prev = periods[i - 1]
            cur = periods[i]
            prev_close = month_close[prev]
            if prev_close > 0:
                returns[cur] = round((month_close[cur] - prev_close) / prev_close * 100, 1)
        return returns
    except Exception as e:
        logger.debug("Monthly returns fetch failed for %s: %s", symbol, e)
        return {}
```

- [ ] **Step 2: Add `fetch_benchmark_monthly_returns` public function**

Add right after the new `_fetch_monthly_returns`:

```python
MONTHLY_BENCHMARKS = {
    "S&P 500": "%5EGSPC",
    "Nasdaq": "%5ENDX",
}


def fetch_benchmark_monthly_returns():
    """Fetch monthly returns for benchmarks.

    Returns:
        Dict of {benchmark_name: {(year, month): return_pct}}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_fetch_monthly_returns, symbol): name
            for name, symbol in MONTHLY_BENCHMARKS.items()
        }
        for future in as_completed(futures):
            name = futures[future]
            results[name] = future.result()
    return results
```

- [ ] **Step 3: Verify import works**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -c "from tastytrade_api import fetch_benchmark_monthly_returns; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add tastytrade_api.py
git commit -m "feat: add fetch_benchmark_monthly_returns for monthly benchmark data"
```

---

## Chunk 2: Monthly Detail Dialog in streamlit_app.py

### Task 2: Add helper functions for monthly trade aggregation

**Files:**
- Modify: `streamlit_app.py` (add helpers before the Results page section, around line 5530)

- [ ] **Step 1: Add `_fmt_k` helper for formatting dollar amounts**

Add before the Results page section (around line 5530):

```python
def _fmt_k(val):
    """Format dollar amount: $1,234 -> '$1.2K', $500 -> '$500'."""
    sign = "+" if val > 0 else "-" if val < 0 else ""
    av = abs(val)
    if av >= 1000:
        return f"{sign}${av / 1000:.1f}K"
    return f"{sign}${av:,.0f}"
```

- [ ] **Step 2: Add `_aggregate_month_trades` helper**

Add right after `_fmt_k`:

```python
def _aggregate_month_trades(cost_basis, year, month):
    """Aggregate trade data for a specific month from cost_basis.

    Returns dict with:
        premium_total: float — total premium from CSP/CC (Sell to Open)
        premium_trades: int — count of premium trades
        leaders_premium: list of dicts — top tickers by premium
        leaders_pl: list of dicts — top tickers by P/L
        laggards_pl: list of dicts — worst tickers by P/L
    """
    from datetime import datetime

    ticker_data = defaultdict(lambda: {
        "cc": 0.0, "put": 0.0, "net_pl": 0.0,
        "premium": 0.0, "premium_trades": 0, "contracts": 0,
        "dte_sum": 0.0, "dte_count": 0, "collateral_sum": 0.0,
    })

    for ticker, data in cost_basis.items():
        for t in data.get("trades", []):
            td = t["date"]
            if hasattr(td, "year"):
                t_year, t_month = td.year, td.month
            else:
                dt = datetime.strptime(str(td)[:10], "%Y-%m-%d")
                t_year, t_month = dt.year, dt.month
            if t_year != year or t_month != month:
                continue

            label = t.get("label", "")
            nv = t.get("net_value", 0.0)
            td_obj = ticker_data[ticker]
            td_obj["net_pl"] += nv

            # CC / PUT breakdown
            if label in ("CC", "BTC CC"):
                td_obj["cc"] += nv
            elif label in ("CSP", "BTC CSP"):
                td_obj["put"] += nv

            # Premium tracking (Sell to Open only)
            if label in ("CSP", "CC"):
                td_obj["premium"] += nv
                td_obj["premium_trades"] += 1
                td_obj["contracts"] += abs(int(t.get("quantity", 0)))

                # DTE from option symbol
                strike, exp_str, cp = _parse_option_symbol(t.get("symbol"))
                if exp_str and hasattr(td, "year"):
                    try:
                        exp_dt = datetime.strptime(exp_str, "%d-%m-%Y")
                        trade_dt = datetime(td.year, td.month, td.day) if hasattr(td, "day") else datetime.strptime(str(td)[:10], "%Y-%m-%d")
                        dte = (exp_dt - trade_dt).days
                        if dte > 0:
                            td_obj["dte_sum"] += dte
                            td_obj["dte_count"] += 1
                            # Collateral for ROC: CSP = strike×100, CC = price×100
                            if strike and strike > 0:
                                if label == "CSP":
                                    td_obj["collateral_sum"] += strike * 100
                                else:  # CC
                                    price = t.get("price", 0)
                                    td_obj["collateral_sum"] += abs(price) * 100 if price else strike * 100
                    except (ValueError, TypeError):
                        pass

    # Build premium leaders (top 5 by premium, descending — premium is positive for STO)
    premium_list = []
    for ticker, d in ticker_data.items():
        if d["premium"] <= 0:
            continue
        avg_dte = int(d["dte_sum"] / d["dte_count"]) if d["dte_count"] > 0 else 0
        est_roc = 0.0
        if d["collateral_sum"] > 0 and avg_dte > 0:
            est_roc = (d["premium"] / d["collateral_sum"]) * (365 / avg_dte) * 100
        premium_list.append({
            "ticker": ticker, "trades": d["premium_trades"],
            "contracts": d["contracts"], "avg_dte": avg_dte,
            "est_roc": round(est_roc, 1), "premiums": d["premium"],
        })
    premium_list.sort(key=lambda x: x["premiums"], reverse=True)

    # Build P/L leaders and laggards (top/bottom 5)
    pl_list = [{"ticker": t, "cc": d["cc"], "put": d["put"], "net_pl": d["net_pl"]}
               for t, d in ticker_data.items() if d["net_pl"] != 0]
    pl_list.sort(key=lambda x: x["net_pl"], reverse=True)

    total_premium = sum(d["premium"] for d in ticker_data.values())
    total_premium_trades = sum(d["premium_trades"] for d in ticker_data.values())

    return {
        "premium_total": total_premium,
        "premium_trades": total_premium_trades,
        "leaders_premium": premium_list[:5],
        "leaders_pl": [x for x in pl_list[:5] if x["net_pl"] > 0],
        "laggards_pl": [x for x in pl_list[-5:] if x["net_pl"] < 0],
    }
```

- [ ] **Step 3: Verify helpers parse correctly**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -c "
from streamlit_app import _fmt_k
print(_fmt_k(42200))   # expect +$42.2K
print(_fmt_k(-11200))  # expect -$11.2K
print(_fmt_k(500))     # expect +$500
print('OK')
"`

Note: This may fail due to Streamlit import side effects. If so, just verify visually in the next task.

- [ ] **Step 4: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add streamlit_app.py
git commit -m "feat: add _fmt_k and _aggregate_month_trades helpers for monthly modal"
```

---

### Task 3: Add the `@st.dialog` monthly detail modal

**Files:**
- Modify: `streamlit_app.py` (add dialog function after the helpers from Task 2)

- [ ] **Step 1: Import `fetch_benchmark_monthly_returns` at top of file**

Find the existing import block from `tastytrade_api` (around line 46) and add the new function:

```python
# Find the line that imports from tastytrade_api and add fetch_benchmark_monthly_returns to it
```

Search for `from tastytrade_api import` and add `fetch_benchmark_monthly_returns` to the import list.

- [ ] **Step 2: Add the dialog function**

Add right after `_aggregate_month_trades`:

```python
@st.dialog("Monthly Detail", width="large")
def _show_month_detail(year, month, cost_basis, nl_all, transfers, monthly_returns, T):
    """Render monthly detail modal with premium, P/L, benchmarks, and leaderboards."""
    import pandas as pd
    from collections import defaultdict

    MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_label = f"{MONTH_NAMES[month]} {year}"

    agg = _aggregate_month_trades(cost_basis, year, month)

    # ── Net P/L from net_liq ──
    mo_ret_pct = monthly_returns.get(year, {}).get(month, 0.0)
    net_pl_dollar = 0.0
    if nl_all:
        df = pd.DataFrame(nl_all)
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time")
        mo_data = df[(df["time"].dt.year == year) & (df["time"].dt.month == month)]
        if not mo_data.empty:
            end_val = mo_data["close"].iloc[-1]
            # Start = last close of previous month
            prev = df[df["time"] < mo_data["time"].iloc[0]]
            start_val = prev["close"].iloc[-1] if not prev.empty else end_val
            yr_transfers = transfers.get(year, {})
            mo_dep = yr_transfers.get("months", {}).get(month, 0) if isinstance(yr_transfers, dict) else 0
            net_pl_dollar = end_val - start_val - mo_dep

    # ── Benchmark monthly returns (cached) ──
    if "benchmark_monthly" not in st.session_state:
        try:
            from tastytrade_api import fetch_benchmark_monthly_returns
            st.session_state["benchmark_monthly"] = fetch_benchmark_monthly_returns()
        except Exception:
            st.session_state["benchmark_monthly"] = {}
    bench = st.session_state["benchmark_monthly"]

    # ── Hero Cards ──
    c1, c2, c3 = st.columns(3)
    prem_cls = "pf-green" if agg["premium_total"] >= 0 else "pf-red"
    pl_cls = "pf-green" if net_pl_dollar >= 0 else "pf-red"
    ret_cls = "pf-green" if mo_ret_pct >= 0 else "pf-red"

    with c1:
        st.markdown(
            f'<div class="portfolio-card" style="display:block;text-align:left;border-left:3px solid {T["accent"]}">'
            f'<div style="font-size:0.8rem;color:{T["text_muted"]}">&#x1f4b0; Premium Collected</div>'
            f'<div class="pf-val {prem_cls}" style="font-size:1.5rem;font-weight:700;margin:4px 0">{_fmt_k(agg["premium_total"])}</div>'
            f'<div style="font-size:0.8rem;color:{T["text_muted"]}">{agg["premium_trades"]} trades</div>'
            f'</div>', unsafe_allow_html=True)
    with c2:
        st.markdown(
            f'<div class="portfolio-card" style="display:block;text-align:left;border-left:3px solid {T["accent"]}">'
            f'<div style="font-size:0.8rem;color:{T["text_muted"]}">&#x1f4c8; Net P/L</div>'
            f'<div class="pf-val {pl_cls}" style="font-size:1.5rem;font-weight:700;margin:4px 0">{_fmt_k(net_pl_dollar)}</div>'
            f'<div style="font-size:0.8rem;color:{T["text_muted"]}"><span class="pf-val {ret_cls}">{mo_ret_pct:+.1f}%</span> return</div>'
            f'</div>', unsafe_allow_html=True)
    with c3:
        bench_html = (
            f'<div class="portfolio-card" style="display:block;text-align:left;border-left:3px solid {T["accent"]}">'
            f'<div style="font-size:0.8rem;color:{T["text_muted"]}">&#x2696; Benchmark</div>'
            f'<div style="margin-top:6px">'
            f'<div style="display:flex;justify-content:space-between;padding:3px 0">'
            f'<span style="background:{T["accent"]}33;padding:1px 8px;border-radius:4px;font-weight:600">Portfolio</span>'
            f'<span class="pf-val {ret_cls}">{mo_ret_pct:+.1f}%</span></div>'
        )
        for bname, bdata in bench.items():
            b_ret = bdata.get((year, month), 0.0)
            b_cls = "pf-green" if b_ret >= 0 else "pf-red"
            bench_html += (
                f'<div style="display:flex;justify-content:space-between;padding:3px 0">'
                f'<span>{bname}</span>'
                f'<span class="pf-val {b_cls}">{b_ret:+.1f}%</span></div>'
            )
        bench_html += '</div></div>'
        st.markdown(bench_html, unsafe_allow_html=True)

    st.markdown("")

    # ── Leaders by Premium ──
    if agg["leaders_premium"]:
        rows = ""
        for lp in agg["leaders_premium"]:
            dte_str = f'{lp["avg_dte"]}d' if lp["avg_dte"] > 0 else "—"
            roc_cls = "pf-green" if lp["est_roc"] > 0 else ""
            prem_display = _fmt_k(lp["premiums"])
            rows += (
                f'<tr style="border-bottom:1px solid {T["border"]}">'
                f'<td style="padding:8px;font-weight:600">{lp["ticker"]}</td>'
                f'<td style="text-align:right;padding:8px">{lp["trades"]}</td>'
                f'<td style="text-align:right;padding:8px">{lp["contracts"]}</td>'
                f'<td style="text-align:right;padding:8px">{dte_str}</td>'
                f'<td style="text-align:right;padding:8px"><span class="pf-val {roc_cls}">{lp["est_roc"]:.1f}%</span></td>'
                f'<td style="text-align:right;padding:8px"><span class="pf-val pf-green">{prem_display}</span></td>'
                f'</tr>'
            )
        st.markdown(
            f'<div class="portfolio-card" style="display:block;border-left:3px solid {T["accent"]};padding:16px">'
            f'<div style="font-weight:600;margin-bottom:10px">&#x1f3c6; Leaders — By Premium</div>'
            f'<table style="width:100%;border-collapse:collapse;font-size:0.85rem">'
            f'<tr style="color:{T["text_muted"]};border-bottom:1px solid {T["border"]}">'
            f'<th style="text-align:left;padding:8px">Ticker</th>'
            f'<th style="text-align:right;padding:8px">Trades</th>'
            f'<th style="text-align:right;padding:8px">Contracts</th>'
            f'<th style="text-align:right;padding:8px">Avg DTE</th>'
            f'<th style="text-align:right;padding:8px">Est. ROC</th>'
            f'<th style="text-align:right;padding:8px">Premiums</th>'
            f'</tr>{rows}</table></div>',
            unsafe_allow_html=True)

    # ── Leaders & Laggards by P/L ──
    if agg["leaders_pl"] or agg["laggards_pl"]:
        def _pl_table(items, color_label, color):
            if not items:
                return f'<div style="color:{T["text_muted"]}">—</div>'
            rows = ""
            for it in items:
                cc_cls = "pf-green" if it["cc"] >= 0 else "pf-red"
                put_cls = "pf-green" if it["put"] >= 0 else "pf-red"
                pl_cls = "pf-green" if it["net_pl"] >= 0 else "pf-red"
                rows += (
                    f'<tr style="border-bottom:1px solid {T["border"]}">'
                    f'<td style="padding:6px;font-weight:600">{it["ticker"]}</td>'
                    f'<td style="text-align:right;padding:6px"><span class="pf-val {cc_cls}">{_fmt_k(it["cc"])}</span></td>'
                    f'<td style="text-align:right;padding:6px"><span class="pf-val {put_cls}">{_fmt_k(it["put"])}</span></td>'
                    f'<td style="text-align:right;padding:6px"><span class="pf-val {pl_cls}">{_fmt_k(it["net_pl"])}</span></td>'
                    f'</tr>'
                )
            return (
                f'<div style="color:{color};font-weight:600;margin-bottom:6px">{color_label}</div>'
                f'<table style="width:100%;border-collapse:collapse;font-size:0.8rem">'
                f'<tr style="color:{T["text_muted"]};border-bottom:1px solid {T["border"]}">'
                f'<th style="text-align:left;padding:6px">Ticker</th>'
                f'<th style="text-align:right;padding:6px">CC</th>'
                f'<th style="text-align:right;padding:6px">PUT</th>'
                f'<th style="text-align:right;padding:6px">Net P/L</th>'
                f'</tr>{rows}</table>'
            )

        left = _pl_table(agg["leaders_pl"], "Leaders", T["accent"])
        right = _pl_table(agg["laggards_pl"], "Laggards", T["red"])

        st.markdown(
            f'<div class="portfolio-card" style="display:block;border-left:3px solid {T["accent"]};padding:16px">'
            f'<div style="font-weight:600;margin-bottom:10px">&#x26a1; Leaders &amp; Laggards — By P/L</div>'
            f'<div style="display:flex;gap:16px">'
            f'<div style="flex:1">{left}</div>'
            f'<div style="flex:1">{right}</div>'
            f'</div></div>',
            unsafe_allow_html=True)
```

- [ ] **Step 3: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add streamlit_app.py
git commit -m "feat: add _show_month_detail dialog with hero cards, leaders, and laggards"
```

---

### Task 4: Replace static month cards with clickable buttons

**Files:**
- Modify: `streamlit_app.py:5985-6017` (the Returns section month card loop)

- [ ] **Step 1: Replace the HTML month cards with Streamlit buttons**

The current code (lines 5991-6017) builds `returns_html` with `<details>` and static month cards. Replace the inner month card loop so each month becomes a `st.button` that triggers the dialog.

Replace the entire `with col_ret:` block (lines 5985-6017) with:

```python
        with col_ret:
            st.markdown(
                f'<div class="section-title-bar">Returns &nbsp;<span style="font-weight:400;font-size:0.85rem;color:{T["text_muted"]}">'
                f'Cumulative: <span class="pf-val{total_ret_cls}" style="font-size:0.85rem">{total_return:+.1f}%</span>'
                f'</span></div>',
                unsafe_allow_html=True,
            )
            for yr in sorted(yearly_returns, reverse=True):
                yr_ret = yearly_returns[yr]
                yr_color = T['accent'] if yr_ret >= 0 else T['red']
                with st.expander(f"{yr} — {yr_ret:+.1f}%"):
                    for mo in range(1, 13):
                        mo_ret = monthly_returns.get(yr, {}).get(mo)
                        if mo_ret is None:
                            continue
                        mo_cls = "pf-green" if mo_ret >= 0 else "pf-red"
                        mo_color = T['accent'] if mo_ret >= 0 else T['red']
                        col_name, col_val, col_btn = st.columns([2, 3, 1])
                        with col_name:
                            st.markdown(f"**{MONTH_NAMES[mo]}**")
                        with col_val:
                            st.markdown(
                                f'<span class="pf-val {mo_cls}" style="font-size:1rem">{mo_ret:+.1f}%</span>',
                                unsafe_allow_html=True,
                            )
                        with col_btn:
                            if st.button("🔍", key=f"mo_{yr}_{mo}", help=f"Detail {MONTH_NAMES[mo]} {yr}"):
                                _show_month_detail(yr, mo, cost_basis, nl_all, transfers, monthly_returns, T)
```

- [ ] **Step 2: Verify the app loads without errors**

Open http://localhost:8501, navigate to the Results page. Expand a year and verify:
- Month rows show name, return %, and a 🔍 button
- Clicking the button opens the modal with all sections

- [ ] **Step 3: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add streamlit_app.py
git commit -m "feat: replace static month cards with clickable buttons that open monthly detail modal"
```

---

### Task 5: Cache benchmark data and add `defaultdict` import guard

**Files:**
- Modify: `streamlit_app.py` (ensure `defaultdict` import is available where `_aggregate_month_trades` uses it)

- [ ] **Step 1: Verify `defaultdict` is imported at module level**

Check if `from collections import defaultdict` exists at the top of `streamlit_app.py`. If not, add it.

- [ ] **Step 2: Test full flow end-to-end**

1. Open http://localhost:8501
2. Navigate to Results page
3. Expand a year in the Returns section
4. Click 🔍 on any month
5. Verify modal shows:
   - Hero cards (Premium Collected, Net P/L, Benchmark)
   - Leaders by Premium table
   - Leaders & Laggards by P/L tables
6. Close modal, verify app still works

- [ ] **Step 3: Final commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add streamlit_app.py tastytrade_api.py
git commit -m "feat: complete monthly detail modal with all sections"
```
