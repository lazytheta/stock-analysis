# Key Ratios Tab Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a fifth "Key Ratios" tab with 8 Morningstar-style HTML tables showing historical financial ratios (returns, margins, capital structure, growth, valuation, dividends, per-share, supplementary).

**Architecture:** Extend `fetch_fundamentals()` in `gather_data.py` with 9 new metrics + a new `fetch_historical_prices()` function. Add `_tab_key_ratios` tab in `streamlit_app.py` that renders 8 HTML table sections from the data. All tables follow the same style as the existing Operating Leverage table.

**Tech Stack:** Python, Streamlit, EDGAR XBRL, Yahoo Finance chart API, Plotly (not used — tables only)

**Design doc:** `docs/plans/2026-02-25-key-ratios-tab-design.md`

---

### Task 1: Extend fetch_fundamentals() with new metrics

**Files:**
- Modify: `gather_data.py` — `fetch_fundamentals()` function (~lines 1636-1844)

**Context:** `fetch_fundamentals()` collects financial data into `data_by_year` dict, first from yfinance then EDGAR XBRL fallback. It has a `metrics` list that controls which keys get assembled into the result. We need to add 9 new keys.

**Step 1: Add new keys to the metrics list**

In `gather_data.py`, find the `metrics` list (~line 1656):
```python
metrics = [
    "revenue", "operating_income", "net_income", "cost_of_revenue",
    "tax_provision", "pretax_income",
    "total_equity", "total_debt", "cash", "shares",
    "capex", "cfo",
]
```

Change to:
```python
metrics = [
    "revenue", "operating_income", "net_income", "cost_of_revenue",
    "tax_provision", "pretax_income",
    "total_equity", "total_debt", "cash", "shares",
    "capex", "cfo",
    "total_assets", "current_liabilities", "goodwill", "intangibles",
    "ppe", "da", "gross_profit", "eps", "dividends_per_share",
]
```

**Step 2: Add yfinance balance sheet extraction for new fields**

Find the balance sheet extraction block (~line 1711) that has:
```python
for label, key in [
    ("Stockholders Equity", "total_equity"),
    ("Total Debt", "total_debt"),
    ("Cash And Cash Equivalents", "cash"),
    ("Ordinary Shares Number", "shares"),
]:
```

Change to:
```python
for label, key in [
    ("Stockholders Equity", "total_equity"),
    ("Total Debt", "total_debt"),
    ("Cash And Cash Equivalents", "cash"),
    ("Ordinary Shares Number", "shares"),
    ("Total Assets", "total_assets"),
    ("Current Liabilities", "current_liabilities"),
    ("Goodwill", "goodwill"),
    ("Intangible Assets", "intangibles"),
    ("Net PPE", "ppe"),
]:
```

Keep the existing `if key == "shares"` special handling for raw count. Add similar handling for `total_assets`, `current_liabilities`, `goodwill`, `intangibles`, `ppe` — they all get divided by M like the other balance sheet items.

**Step 3: Add yfinance income statement extraction for new fields**

Find the income statement extraction block (~line 1689) and add after the existing items:
```python
("Gross Profit", "gross_profit"),
("Diluted EPS", "eps"),
```

For `eps`: do NOT divide by M — it's already a per-share dollar value. Add special handling similar to `shares`:
```python
if key == "eps":
    d[key] = v  # per-share value, no conversion
else:
    d[key] = round(v / M, 0)
```

**Step 4: Add yfinance cashflow extraction for D&A**

Find the cashflow extraction block (~line 1734) and add:
```python
("Depreciation And Amortization", "da"),
```

D&A is in dollars, divide by M like the others.

**Step 5: Add EDGAR XBRL fallback tags for new metrics**

Find the `_extra_tags` dict (~line 1783) and add these entries:

```python
"total_assets": ["Assets"],
"current_liabilities": ["LiabilitiesCurrent"],
"goodwill": ["Goodwill"],
"intangibles": ["IntangibleAssetsNetExcludingGoodwill"],
"ppe": ["PropertyPlantAndEquipmentNet",
        "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
"da": ["DepreciationDepletionAndAmortization",
       "DepreciationAndAmortization"],
"gross_profit": ["GrossProfit"],
```

These all use `unit_key="USD"` (default) and get divided by M.

**Step 6: Add EDGAR XBRL fallback for per-share metrics**

After the existing shares fallback block (~line 1811), add new fallback blocks for `eps` and `dividends_per_share` which use `unit_key="USD/shares"`:

```python
# EPS: separate fallback with unit_key="USD/shares"
_eps_tags = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
eps_data = _try_tags(facts, _eps_tags, n_years, unit_key="USD/shares")
for yr_val, val in eps_data:
    if yr_val in data_by_year:
        d = data_by_year[yr_val]
        if "eps" not in d or d["eps"] is None:
            d["eps"] = val  # already per-share dollar value

# Dividends per share: separate fallback with unit_key="USD/shares"
_dps_tags = ["CommonStockDividendsPerShareDeclared",
             "CommonStockDividendsPerShareCashPaid"]
dps_data = _try_tags(facts, _dps_tags, n_years, unit_key="USD/shares")
for yr_val, val in dps_data:
    if yr_val in data_by_year:
        d = data_by_year[yr_val]
        if "dividends_per_share" not in d or d["dividends_per_share"] is None:
            d["dividends_per_share"] = val
```

**Step 7: Add gross_profit computation fallback**

After the FCF computation block (~line 1843), add a computation for gross_profit from revenue - cost_of_revenue when the direct value is missing:

```python
# Compute gross_profit from revenue - cost_of_revenue if not directly available
for i in range(len(result["gross_profit"])):
    if result["gross_profit"][i] is None and result["revenue"][i] is not None and result["cost_of_revenue"][i] is not None:
        result["gross_profit"][i] = round(result["revenue"][i] - result["cost_of_revenue"][i], 0)
```

**Step 8: Verify**

Run: `python3 -c "from gather_data import fetch_fundamentals; f = fetch_fundamentals('AMZN', 12); print('Keys:', sorted(f.keys())); print('total_assets:', f['total_assets']); print('eps:', f['eps']); print('da:', f['da']); print('gross_profit:', f['gross_profit'])"`

Expected: All new keys present with 10 years of data.

**Step 9: Commit**

```bash
git add gather_data.py
git commit -m "feat: extend fetch_fundamentals with 9 new metrics for Key Ratios tab"
```

---

### Task 2: Add fetch_historical_prices() function

**Files:**
- Modify: `gather_data.py` — add new function after `fetch_stock_price()` (~line 457)

**Step 1: Implement fetch_historical_prices**

Add this function after `fetch_stock_price()`:

```python
def fetch_historical_prices(ticker, years):
    """Fetch historical year-end stock prices from Yahoo Finance chart API.

    Args:
        ticker: Stock ticker symbol
        years: List of years to get prices for

    Returns:
        Dict mapping year to year-end closing price, e.g. {2020: 150.0, 2021: 175.0}
    """
    if not years:
        return {}

    n_years = max(len(years) + 2, 12)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1mo&range={n_years}y"
    )
    try:
        data = _http_get_json(url, YAHOO_HEADERS)
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        # Group by year, take last available month's close per year
        year_prices = {}
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            dt = datetime.datetime.fromtimestamp(ts)
            year_prices[dt.year] = close  # overwrites earlier months, keeps latest

        # Filter to requested years only
        return {yr: round(year_prices[yr], 2) for yr in years if yr in year_prices}

    except Exception as e:
        print(f"[Yahoo] Warning: Historical prices fetch failed: {e}")
        return {}
```

Also add `import datetime` at the top if not already present.

**Step 2: Verify**

Run: `python3 -c "from gather_data import fetch_historical_prices; p = fetch_historical_prices('AMZN', [2016,2017,2018,2019,2020,2021,2022,2023,2024,2025]); print(p)"`

Expected: Dict with year-end prices for each year.

**Step 3: Commit**

```bash
git add gather_data.py
git commit -m "feat: add fetch_historical_prices for year-end stock prices"
```

---

### Task 3: Add Key Ratios tab in streamlit_app.py

**Files:**
- Modify: `streamlit_app.py`
  - Tab definition line (~line 827)
  - Add new tab block after `_tab_fundamentals`
  - Add `fetch_historical_prices` to imports (~line 40)

**Step 1: Add import**

Find the import line (~line 40) that imports from `gather_data` and add `fetch_historical_prices`:
```python
from gather_data import ..., fetch_historical_prices
```

**Step 2: Add 5th tab**

Find the tabs line (~line 827):
```python
_tab_dcf, _tab_rdcf, _tab_peers, _tab_fundamentals = st.tabs(["DCF", "Reverse DCF", "Peer Comparison", "Fundamentals"])
```

Change to:
```python
_tab_dcf, _tab_rdcf, _tab_peers, _tab_fundamentals, _tab_key_ratios = st.tabs(["DCF", "Reverse DCF", "Peer Comparison", "Fundamentals", "Key Ratios"])
```

**Step 3: Add Key Ratios tab block**

After the entire `with _tab_fundamentals:` block (ends before `# ── Action buttons ──`), add the Key Ratios tab. Find `# ── Action buttons ──` and insert above it:

```python
    with _tab_key_ratios:
        st.markdown("#### Key Ratios")

        fund = _cached_fundamentals(ticker)
        _yrs = fund['years']
        _n = len(_yrs)

        # Reuse live_price from earlier in _dcf_editor
        # Fetch historical year-end prices for valuation metrics
        @st.cache_data(ttl=300, show_spinner="Loading historical prices...")
        def _cached_hist_prices(t, yrs):
            return fetch_historical_prices(t, yrs)

        _hist_prices = _cached_hist_prices(ticker, _yrs)

        # ── Table helper ──
        _kr_cell = 'text-align:right;padding:5px 10px;font-size:0.85rem'
        _kr_hdr = 'text-align:right;padding:5px 10px;font-size:0.85rem;color:#86868b'
        _kr_label = 'text-align:left;padding:5px 10px;font-size:0.85rem;font-weight:600;color:#1d1d1f;white-space:nowrap'
        _kr_avg_style = f'{_kr_cell};font-weight:600;border-left:2px solid #d2d2d7'
        _kr_section = 'text-align:left;padding:8px 10px 4px;font-size:0.85rem;font-weight:700;color:#1d1d1f'

        def _kr_table_start():
            html = (
                '<div style="overflow-x:auto">'
                '<table style="width:100%;border-collapse:collapse">'
                '<thead><tr>'
                f'<th style="{_kr_hdr};text-align:left"></th>'
            )
            for yr in _yrs:
                html += f'<th style="{_kr_hdr}">{yr}</th>'
            html += f'<th style="{_kr_hdr};border-left:2px solid #d2d2d7">Avg</th>'
            html += '</tr></thead><tbody>'
            return html

        def _kr_row(label, vals, fmt='pct1', color_mode='none'):
            """Render one table row.

            fmt: 'pct1' = 12.3%, 'pct0' = 12%, 'dec1' = 1.2, 'dec2' = 1.23,
                 'dollar2' = $1.23, 'num0' = 1,234, 'num_m' = 1,234 (millions)
            color_mode: 'none', 'sign' (green pos / red neg), 'improvement' (green if > prev year)
            """
            html = f'<tr style="border-top:1px solid #f0f0f2"><td style="{_kr_label}">{label}</td>'
            valid_vals = []
            for i, v in enumerate(vals):
                if v is None:
                    html += f'<td style="{_kr_cell}">—</td>'
                    continue
                valid_vals.append(v)
                # Format value
                if fmt == 'pct1':
                    txt = f'{v:.1f}%'
                elif fmt == 'pct0':
                    txt = f'{v:.0f}%'
                elif fmt == 'dec1':
                    txt = f'{v:.1f}'
                elif fmt == 'dec2':
                    txt = f'{v:.2f}'
                elif fmt == 'dollar2':
                    txt = f'${v:.2f}'
                elif fmt == 'num0':
                    txt = f'{v:,.0f}'
                elif fmt == 'num_m':
                    txt = f'{v:,.0f}'
                else:
                    txt = str(v)
                # Color
                style = _kr_cell
                if color_mode == 'sign':
                    if v > 0:
                        style += ';color:#81b29a'
                    elif v < 0:
                        style += ';color:#e07a5f'
                elif color_mode == 'improvement' and i > 0:
                    prev = vals[i - 1]
                    if prev is not None and v > prev:
                        style += ';color:#81b29a'
                    elif prev is not None and v < prev:
                        style += ';color:#e07a5f'
                html += f'<td style="{style}">{txt}</td>'
            # Avg column
            avg = sum(valid_vals) / len(valid_vals) if valid_vals else None
            if avg is not None:
                if fmt == 'pct1':
                    avg_txt = f'{avg:.1f}%'
                elif fmt == 'pct0':
                    avg_txt = f'{avg:.0f}%'
                elif fmt == 'dec1':
                    avg_txt = f'{avg:.1f}'
                elif fmt == 'dec2':
                    avg_txt = f'{avg:.2f}'
                elif fmt == 'dollar2':
                    avg_txt = f'${avg:.2f}'
                elif fmt == 'num0' or fmt == 'num_m':
                    avg_txt = f'{avg:,.0f}'
                else:
                    avg_txt = str(avg)
                html += f'<td style="{_kr_avg_style}">{avg_txt}</td>'
            else:
                html += f'<td style="{_kr_avg_style}">—</td>'
            html += '</tr>'
            return html

        def _kr_separator():
            """Blank separator row."""
            cols = _n + 2  # label + years + avg
            return f'<tr><td colspan="{cols}" style="padding:4px"></td></tr>'

        def _pct_change(vals):
            """Compute YoY percentage growth from absolute values."""
            result = []
            for i in range(len(vals)):
                if i == 0 or vals[i] is None or vals[i - 1] is None or vals[i - 1] == 0:
                    result.append(None)
                else:
                    result.append((vals[i] / vals[i - 1] - 1) * 100)
            return result

        if _n < 2:
            st.info("Insufficient data for Key Ratios (need 2+ years)")
        else:
            # ── Pre-compute derived series ──
            rev = fund['revenue']
            oi = fund['operating_income']
            ni = fund['net_income']
            eq = fund['total_equity']
            debt = fund['total_debt']
            cash_v = fund['cash']
            ta = fund['total_assets']
            cl = fund['current_liabilities']
            gw = fund['goodwill']
            intang = fund['intangibles']
            ppe_v = fund['ppe']
            da_v = fund['da']
            gp = fund['gross_profit']
            eps_v = fund['eps']
            dps = fund['dividends_per_share']
            shares = fund['shares']
            cfo_v = fund['cfo']
            capex_v = fund['capex']
            fcf_v = fund['fcf']
            tp = fund['tax_provision']
            pti = fund['pretax_income']

            # ── 1. Returns ──
            st.markdown("**Returns**")
            roa = [ni[i] / ta[i] * 100 if ni[i] is not None and ta[i] else None for i in range(_n)]
            roe = [ni[i] / eq[i] * 100 if ni[i] is not None and eq[i] else None for i in range(_n)]
            roic = []
            for i in range(_n):
                if oi[i] is not None and pti[i] and pti[i] != 0:
                    tax_rate = (tp[i] / pti[i]) if tp[i] is not None else 0.21
                    nopat = oi[i] * (1 - tax_rate)
                    ic = (eq[i] or 0) + (debt[i] or 0) - (cash_v[i] or 0)
                    roic.append(nopat / ic * 100 if ic > 0 else None)
                else:
                    roic.append(None)
            roce = [oi[i] / (ta[i] - (cl[i] or 0)) * 100
                    if oi[i] is not None and ta[i] and (ta[i] - (cl[i] or 0)) > 0
                    else None for i in range(_n)]
            rotc = [oi[i] / (ta[i] - (cl[i] or 0) - (gw[i] or 0) - (intang[i] or 0)) * 100
                    if oi[i] is not None and ta[i] and (ta[i] - (cl[i] or 0) - (gw[i] or 0) - (intang[i] or 0)) > 0
                    else None for i in range(_n)]

            html = _kr_table_start()
            html += _kr_row('Return on Assets', roa, 'pct1')
            html += _kr_row('Return on Equity', roe, 'pct1')
            html += _kr_row('Return on Invested Capital', roic, 'pct1')
            html += _kr_row('Return on Capital Employed', roce, 'pct1')
            html += _kr_row('Return on Tangible Capital', rotc, 'pct1')
            html += '</tbody></table></div>'
            st.markdown(html, unsafe_allow_html=True)

            # ── 2. Margins ──
            st.markdown("")
            st.markdown("**Margins as % of Revenue**")
            gross_m = [gp[i] / rev[i] * 100 if gp[i] is not None and rev[i] else None for i in range(_n)]
            ebitda_m = [(oi[i] + (da_v[i] or 0)) / rev[i] * 100
                        if oi[i] is not None and rev[i] else None for i in range(_n)]
            op_m = [oi[i] / rev[i] * 100 if oi[i] is not None and rev[i] else None for i in range(_n)]
            pretax_m = [pti[i] / rev[i] * 100 if pti[i] is not None and rev[i] else None for i in range(_n)]
            net_m = [ni[i] / rev[i] * 100 if ni[i] is not None and rev[i] else None for i in range(_n)]
            fcf_m = [fcf_v[i] / rev[i] * 100 if fcf_v[i] is not None and rev[i] else None for i in range(_n)]

            html = _kr_table_start()
            html += _kr_row('Gross Margin', gross_m, 'pct1')
            html += _kr_row('EBITDA Margin', ebitda_m, 'pct1')
            html += _kr_row('Operating Margin', op_m, 'pct1')
            html += _kr_row('Pretax Margin', pretax_m, 'pct1')
            html += _kr_row('Net Margin', net_m, 'pct1')
            html += _kr_row('FCF Margin', fcf_m, 'pct1')
            html += '</tbody></table></div>'
            st.markdown(html, unsafe_allow_html=True)

            # ── 3. Capital Structure ──
            st.markdown("")
            st.markdown("**Capital Structure**")
            a2e = [ta[i] / eq[i] if ta[i] and eq[i] and eq[i] != 0 else None for i in range(_n)]
            e2a = [eq[i] / ta[i] if eq[i] is not None and ta[i] else None for i in range(_n)]
            d2e = [(debt[i] or 0) / eq[i] if eq[i] and eq[i] != 0 else None for i in range(_n)]
            d2a = [(debt[i] or 0) / ta[i] if ta[i] else None for i in range(_n)]

            html = _kr_table_start()
            html += _kr_row('Assets to Equity', a2e, 'dec1')
            html += _kr_row('Equity to Assets', e2a, 'dec1')
            html += _kr_row('Debt to Equity', d2e, 'dec1')
            html += _kr_row('Debt to Assets', d2a, 'dec1')
            html += '</tbody></table></div>'
            st.markdown(html, unsafe_allow_html=True)

            # ── 4. Year-Over-Year Growth ──
            st.markdown("")
            st.markdown("**Year-Over-Year Growth**")
            ebitda_abs = [(oi[i] or 0) + (da_v[i] or 0) if oi[i] is not None else None for i in range(_n)]
            eps_growth = _pct_change(eps_v)
            shares_growth = _pct_change(shares)

            html = _kr_table_start()
            html += _kr_row('Revenue', _pct_change(rev), 'pct1', 'sign')
            html += _kr_row('Gross Profit', _pct_change(gp), 'pct1', 'sign')
            html += _kr_row('EBITDA', _pct_change(ebitda_abs), 'pct1', 'sign')
            html += _kr_row('Operating Income', _pct_change(oi), 'pct1', 'sign')
            html += _kr_row('Pretax Income', _pct_change(pti), 'pct1', 'sign')
            html += _kr_row('Net Income', _pct_change(ni), 'pct1', 'sign')
            html += _kr_row('Diluted EPS', eps_growth, 'pct1', 'sign')
            html += _kr_separator()
            html += _kr_row('Diluted Shares', shares_growth, 'pct1', 'sign')
            html += _kr_separator()
            html += _kr_row('PP&E', _pct_change(ppe_v), 'pct1', 'sign')
            html += _kr_row('Total Assets', _pct_change(ta), 'pct1', 'sign')
            html += _kr_row('Equity', _pct_change(eq), 'pct1', 'sign')
            html += _kr_separator()
            html += _kr_row('Cash from Operations', _pct_change(cfo_v), 'pct1', 'sign')
            html += _kr_row('Capital Expenditures', _pct_change(capex_v), 'pct1', 'sign')
            html += _kr_row('Free Cash Flow', _pct_change(fcf_v), 'pct1', 'sign')
            html += '</tbody></table></div>'
            st.markdown(html, unsafe_allow_html=True)

            # ── 5. Valuation Metrics ──
            st.markdown("")
            st.markdown("**Valuation Metrics**")
            if _hist_prices:
                mkt_cap = []
                pe = []
                pb = []
                ps = []
                for i in range(_n):
                    yr = _yrs[i]
                    price = _hist_prices.get(yr)
                    sh = shares[i]
                    if price and sh and sh > 0:
                        mc = price * sh / 1e6  # to millions
                        mkt_cap.append(round(mc, 0))
                        pe.append(price / eps_v[i] if eps_v[i] and eps_v[i] > 0 else None)
                        bvps = eq[i] * 1e6 / sh if eq[i] else None
                        pb.append(price / bvps if bvps and bvps > 0 else None)
                        rps = rev[i] * 1e6 / sh if rev[i] else None
                        ps.append(price / rps if rps and rps > 0 else None)
                    else:
                        mkt_cap.append(None)
                        pe.append(None)
                        pb.append(None)
                        ps.append(None)

                html = _kr_table_start()
                html += _kr_row('Market Capitalization', mkt_cap, 'num0')
                html += _kr_row('Price-to-Earnings', pe, 'dec2')
                html += _kr_row('Price-to-Book', pb, 'dec2')
                html += _kr_row('Price-to-Sales', ps, 'dec2')
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)
            else:
                st.info("Insufficient data for Valuation Metrics")

            # ── 6. Dividends ──
            st.markdown("")
            st.markdown("**Dividends**")
            has_dividends = any(d is not None and d > 0 for d in dps)
            if has_dividends:
                payout = [dps[i] / eps_v[i] * 100 if dps[i] is not None and eps_v[i] and eps_v[i] > 0 else None
                          for i in range(_n)]
                html = _kr_table_start()
                html += _kr_row('Dividends per Share', dps, 'dollar2')
                html += _kr_row('Payout Ratio', payout, 'pct1')
                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)
            else:
                st.caption("No dividend history available")

            # ── 7. Per-Share Items ──
            st.markdown("")
            st.markdown("**Per-Share Items**")
            def _per_share(vals):
                return [vals[i] * 1e6 / shares[i] if vals[i] is not None and shares[i] and shares[i] > 0 else None
                        for i in range(_n)]

            rev_ps = _per_share(rev)
            ebitda_ps = _per_share(ebitda_abs)
            oi_ps = _per_share(oi)
            fcf_ps = _per_share(fcf_v)
            bv_ps = _per_share(eq)
            tbv = [(eq[i] or 0) - (gw[i] or 0) - (intang[i] or 0) if eq[i] is not None else None for i in range(_n)]
            tbv_ps = _per_share(tbv)

            html = _kr_table_start()
            html += _kr_row('Revenue', rev_ps, 'dec2')
            html += _kr_row('EBITDA', ebitda_ps, 'dec2')
            html += _kr_row('Operating Income', oi_ps, 'dec2')
            html += _kr_row('Diluted EPS', eps_v, 'dec2')
            html += _kr_row('Free Cash Flow', fcf_ps, 'dec2')
            html += _kr_row('Book Value', bv_ps, 'dec2')
            html += _kr_row('Tangible Book Value', tbv_ps, 'dec2')
            html += '</tbody></table></div>'
            st.markdown(html, unsafe_allow_html=True)

            # ── 8. Supplementary Items ──
            st.markdown("")
            st.markdown("**Supplementary Items**")
            bv_abs = eq  # already in millions
            tbv_abs = tbv  # already in millions

            html = _kr_table_start()
            html += _kr_row('Free Cash Flow', fcf_v, 'num_m')
            html += _kr_row('Book Value', bv_abs, 'num_m')
            html += _kr_row('Tangible Book Value', tbv_abs, 'num_m')
            html += '</tbody></table></div>'
            st.markdown(html, unsafe_allow_html=True)
```

**Step 4: Verify syntax**

Run: `python3 -c "import py_compile; py_compile.compile('streamlit_app.py', doraise=True)"`
Expected: No errors.

**Step 5: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: add Key Ratios tab with 8 Morningstar-style ratio tables"
```

---

### Task 4: Push to live

**Step 1: Push**

```bash
git push
```

**Step 2: Restart local Streamlit**

```bash
# Find and kill existing stock-analysis Streamlit process
ps aux | grep streamlit | grep stock-analysis | grep -v grep | awk '{print $2}' | xargs kill
sleep 2
nohup python3 -m streamlit run streamlit_app.py --server.headless true > /dev/null 2>&1 &
```

**Step 3: Verify in browser**

Open the app, navigate to a ticker (e.g. AMZN), click "Key Ratios" tab. Verify:
- All 8 sections render with data
- Numbers look reasonable (ROA ~10-20% for tech, margins match known values)
- Avg column present
- No Python errors in console
