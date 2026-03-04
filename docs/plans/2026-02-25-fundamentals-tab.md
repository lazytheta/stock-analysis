# Fundamentals Tab Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a "Fundamentals" tab to the DCF editor showing 7 historical financial health metrics as trend charts, plus FCF Yield column on the watchlist overview.

**Architecture:** New `fetch_fundamentals(ticker)` function in `gather_data.py` fetches yfinance data (primary) supplemented by EDGAR XBRL (fallback). A new `_tab_fundamentals` section in `streamlit_app.py` renders 7 Plotly line charts in a 3-row + 1-row layout. FCF Yield also added to watchlist overview table.

**Tech Stack:** yfinance (financials/balance_sheet/cashflow), EDGAR XBRL (existing `parse_financials`), Plotly (existing `go.Figure`/`go.Scatter`), Streamlit

---

### Task 1: Add `fetch_fundamentals()` to `gather_data.py`

**Files:**
- Modify: `gather_data.py` (append new function at end of file, before any `if __name__` block)

**Step 1: Add the function**

Add at the end of `gather_data.py` (after the existing `fetch_peer_data` function which ends around line 1004):

```python
def fetch_fundamentals(ticker, n_years=10):
    """Fetch historical fundamentals combining yfinance and EDGAR XBRL.

    Returns dict with yearly lists:
        years, revenue, operating_income, net_income, cost_of_revenue,
        fcf, total_debt, total_equity, cash, shares, capex, depreciation
    All dollar values in millions.
    """
    import yfinance as yf

    data = {}  # year -> {metric: value}
    M = 1_000_000

    # ── yfinance (primary) ──
    try:
        t = yf.Ticker(ticker)
        inc = t.income_stmt  # columns = dates, rows = line items
        bs = t.balance_sheet
        cf = t.cashflow

        def _yf_val(df, labels, col):
            for label in labels:
                if label in df.index:
                    v = df.loc[label, col]
                    if v is not None and v == v:  # not NaN
                        return float(v)
            return None

        if inc is not None and not inc.empty:
            for col in inc.columns:
                yr = col.year
                if yr not in data:
                    data[yr] = {}
                d = data[yr]
                d['revenue'] = _yf_val(inc, ['Total Revenue', 'Revenue'], col)
                d['operating_income'] = _yf_val(inc, ['Operating Income', 'EBIT'], col)
                d['net_income'] = _yf_val(inc, ['Net Income', 'Net Income Common Stockholders'], col)
                d['cost_of_revenue'] = _yf_val(inc, ['Cost Of Revenue'], col)
                d['tax_provision'] = _yf_val(inc, ['Tax Provision', 'Income Tax Expense'], col)
                d['pretax_income'] = _yf_val(inc, ['Pretax Income'], col)

        if bs is not None and not bs.empty:
            for col in bs.columns:
                yr = col.year
                if yr not in data:
                    data[yr] = {}
                d = data[yr]
                d['total_equity'] = _yf_val(bs, ['Stockholders Equity', 'Total Stockholders Equity', 'Total Equity Gross Minority Interest'], col)
                d['total_debt'] = _yf_val(bs, ['Total Debt', 'Long Term Debt And Capital Lease Obligation'], col)
                d['cash'] = _yf_val(bs, ['Cash And Cash Equivalents', 'Cash Cash Equivalents And Short Term Investments'], col)
                d['shares'] = _yf_val(bs, ['Ordinary Shares Number', 'Share Issued'], col)

        if cf is not None and not cf.empty:
            for col in cf.columns:
                yr = col.year
                if yr not in data:
                    data[yr] = {}
                d = data[yr]
                d['cfo'] = _yf_val(cf, ['Operating Cash Flow', 'Cash Flow From Continuing Operating Activities'], col)
                d['capex'] = _yf_val(cf, ['Capital Expenditure', 'Purchase Of PPE'], col)

    except Exception:
        pass  # Fall through to EDGAR

    # ── EDGAR fallback for older years ──
    try:
        cik = get_cik(ticker)
        facts = fetch_company_facts(cik)
        edgar = parse_financials(facts, n_years=n_years)

        for i, yr in enumerate(edgar['years']):
            if yr not in data:
                data[yr] = {}
            d = data[yr]
            # Only fill what yfinance didn't provide
            if d.get('revenue') is None and i < len(edgar['revenue']):
                d['revenue'] = edgar['revenue'][i]  # already in millions
            if d.get('operating_income') is None and i < len(edgar['operating_income']):
                d['operating_income'] = edgar['operating_income'][i]
            if d.get('net_income') is None and i < len(edgar['net_income']):
                d['net_income'] = edgar['net_income'][i]
            if d.get('cost_of_revenue') is None and i < len(edgar['cost_of_revenue']):
                d['cost_of_revenue'] = edgar['cost_of_revenue'][i]
            if d.get('shares') is None and i < len(edgar['shares']):
                d['shares'] = edgar['shares'][i] * M  # edgar is in millions, convert back
            if d.get('tax_provision') is None and i < len(edgar['tax_provision']):
                d['tax_provision'] = edgar['tax_provision'][i]
            if d.get('pretax_income') is None and i < len(edgar['pretax_income']):
                d['pretax_income'] = edgar['pretax_income'][i]
            # EDGAR doesn't have FCF directly, but has cash/debt
            if d.get('cash') is None and i < len(edgar['cash']):
                cash_val = edgar['cash'][i]
                st_inv = edgar['st_investments'][i] if i < len(edgar['st_investments']) else 0
                d['cash'] = (cash_val + st_inv) * M  # convert back from millions
    except Exception:
        pass

    # ── Build aligned output ──
    years = sorted(data.keys())

    def _safe(yr, key, divisor=M):
        v = data[yr].get(key)
        if v is None:
            return 0
        return round(v / divisor, 0) if divisor != 1 else v

    result = {
        'years': years,
        'revenue': [_safe(y, 'revenue') for y in years],
        'operating_income': [_safe(y, 'operating_income') for y in years],
        'net_income': [_safe(y, 'net_income') for y in years],
        'cost_of_revenue': [_safe(y, 'cost_of_revenue') for y in years],
        'tax_provision': [_safe(y, 'tax_provision') for y in years],
        'pretax_income': [_safe(y, 'pretax_income') for y in years],
        'total_equity': [_safe(y, 'total_equity') for y in years],
        'total_debt': [_safe(y, 'total_debt') for y in years],
        'cash': [_safe(y, 'cash') for y in years],
        'shares': [_safe(y, 'shares', 1) for y in years],  # raw count
        'capex': [_safe(y, 'capex') for y in years],
        'cfo': [_safe(y, 'cfo') for y in years],
    }
    # Compute FCF = CFO + CapEx (capex is already negative from yfinance)
    result['fcf'] = [
        (cfo + capex) if cfo and capex else 0
        for cfo, capex in zip(result['cfo'], result['capex'])
    ]
    return result
```

**Step 2: Verify with a quick test**

Run:
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from gather_data import fetch_fundamentals
f = fetch_fundamentals('AAPL')
print('Years:', f['years'])
print('Revenue:', f['revenue'])
print('FCF:', f['fcf'])
print('Total Equity:', f['total_equity'])
print('Shares:', [s/1e9 for s in f['shares']])
"
```

Expected: Years from ~2015-2025, revenue and FCF in millions, equity in millions, shares as raw counts.

**Step 3: Commit**

```bash
git add gather_data.py
git commit -m "feat: add fetch_fundamentals() for historical financial metrics"
```

---

### Task 2: Add Fundamentals tab to `_dcf_editor` in `streamlit_app.py`

**Files:**
- Modify: `streamlit_app.py:817` (tabs definition)
- Modify: `streamlit_app.py:1678` (after `_tab_peers` block, before action buttons)

**Step 1: Update tabs line**

At line 817, change:
```python
_tab_dcf, _tab_rdcf, _tab_peers = st.tabs(["DCF", "Reverse DCF", "Peer Comparison"])
```
to:
```python
_tab_dcf, _tab_rdcf, _tab_peers, _tab_fundamentals = st.tabs(["DCF", "Reverse DCF", "Peer Comparison", "Fundamentals"])
```

**Step 2: Add import**

At the top imports section (around line 15-20 where `gather_data` imports are), add `fetch_fundamentals`:

Find the line with `from gather_data import` and add `fetch_fundamentals` to it.

**Step 3: Add `_tab_fundamentals` block**

Insert after line 1678 (after `_tab_peers` block ends, before `# ── Action buttons ──` on line 1679):

```python
    with _tab_fundamentals:
        st.markdown("#### Fundamentals")

        @st.cache_data(ttl=300, show_spinner="Loading fundamentals...")
        def _cached_fundamentals(t):
            return fetch_fundamentals(t)

        fund = _cached_fundamentals(ticker)
        _yrs = fund['years']
        _n = len(_yrs)

        # ── Chart helper ──
        _CHART_COLORS = {
            'primary': '#81b29a',
            'secondary': '#e07a5f',
            'accent': '#3d405b',
            'tertiary': '#f2cc8f',
        }

        def _base_layout(fig, height=280):
            fig.update_layout(
                margin=dict(t=10, b=20, l=50, r=20),
                height=height,
                font=dict(
                    family="-apple-system, BlinkMacSystemFont, 'Inter', sans-serif",
                    color="#1d1d1f",
                ),
                paper_bgcolor='rgba(0,0,0,0)',
                plot_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(gridcolor='#f0f0f2', dtick=1),
                yaxis=dict(gridcolor='#f0f0f2'),
                legend=dict(
                    orientation="h", yanchor="top", y=-0.15,
                    xanchor="center", x=0.5, font=dict(size=11),
                ),
                hovermode="x unified",
            )
            return fig

        def _pct_growth(values):
            """Compute YoY % growth. First element is None."""
            result = [None]
            for i in range(1, len(values)):
                if values[i - 1] and values[i - 1] != 0:
                    result.append((values[i] / values[i - 1]) - 1)
                else:
                    result.append(None)
            return result

        # ── Row 1: Operating Leverage + Margins ──
        _r1c1, _r1c2 = st.columns(2)

        with _r1c1:
            st.markdown("**Operating Leverage**")
            rev_g = _pct_growth(fund['revenue'])
            oi_g = _pct_growth(fund['operating_income'])
            if _n >= 3:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[r * 100 if r is not None else None for r in rev_g[1:]],
                    name='Revenue Growth',
                    line=dict(color=_CHART_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>Rev Growth</extra>',
                ))
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[o * 100 if o is not None else None for o in oi_g[1:]],
                    name='OI Growth',
                    line=dict(color=_CHART_COLORS['accent'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>OI Growth</extra>',
                ))
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
                # DOL annotation
                dol_vals = []
                for r, o in zip(rev_g[1:], oi_g[1:]):
                    if r and o and r != 0:
                        dol_vals.append(f"{o / r:.1f}x")
                    else:
                        dol_vals.append("—")
                st.caption(f"DOL (OI Growth / Rev Growth): {', '.join(f'{_yrs[i+1]}: {dol_vals[i]}' for i in range(len(dol_vals)))}")
            else:
                st.info("Insufficient data for Operating Leverage (need 3+ years)")

        with _r1c2:
            st.markdown("**Margins**")
            if _n >= 3:
                rev = fund['revenue']
                gross_margin = [(rev[i] - fund['cost_of_revenue'][i]) / rev[i] * 100 if rev[i] else None for i in range(_n)]
                op_margin = [fund['operating_income'][i] / rev[i] * 100 if rev[i] else None for i in range(_n)]
                fcf_margin = [fund['fcf'][i] / rev[i] * 100 if rev[i] else None for i in range(_n)]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=gross_margin, name='Gross',
                    line=dict(color=_CHART_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>Gross Margin</extra>',
                ))
                fig.add_trace(go.Scatter(
                    x=_yrs, y=op_margin, name='Operating',
                    line=dict(color=_CHART_COLORS['accent'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>Op Margin</extra>',
                ))
                fig.add_trace(go.Scatter(
                    x=_yrs, y=fcf_margin, name='FCF',
                    line=dict(color=_CHART_COLORS['tertiary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>FCF Margin</extra>',
                ))
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for Margins (need 3+ years)")

        # ── Row 2: ROIC + FCF Conversion ──
        _r2c1, _r2c2 = st.columns(2)

        with _r2c1:
            st.markdown("**ROIC**")
            if _n >= 3:
                roic_vals = []
                for i in range(_n):
                    oi = fund['operating_income'][i]
                    eq = fund['total_equity'][i]
                    debt = fund['total_debt'][i]
                    cash_val = fund['cash'][i]
                    tp = fund['tax_provision'][i]
                    pti = fund['pretax_income'][i]
                    tax_rate = tp / pti if pti and pti != 0 else 0.21
                    nopat = oi * (1 - tax_rate) if oi else 0
                    ic = eq + debt - cash_val if (eq or debt) else 0
                    roic_vals.append(nopat / ic * 100 if ic > 0 else None)

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=roic_vals, name='ROIC',
                    line=dict(color=_CHART_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>ROIC</extra>',
                ))
                # WACC reference line from DCF config
                wacc_pct = val.get('wacc', 0) * 100
                if wacc_pct > 0:
                    fig.add_hline(
                        y=wacc_pct, line_dash="dash",
                        line_color=_CHART_COLORS['secondary'],
                        annotation_text=f"WACC {wacc_pct:.1f}%",
                        annotation_position="top right",
                    )
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for ROIC (need 3+ years)")

        with _r2c2:
            st.markdown("**FCF Conversion**")
            if _n >= 3:
                conv = [fund['fcf'][i] / fund['net_income'][i] * 100
                        if fund['net_income'][i] and fund['net_income'][i] != 0 else None
                        for i in range(_n)]
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=conv, name='FCF / Net Income',
                    line=dict(color=_CHART_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.0f}%<extra>FCF Conversion</extra>',
                ))
                fig.add_hline(y=100, line_dash="dash", line_color=_CHART_COLORS['accent'],
                              annotation_text="100%", annotation_position="top right")
                fig.add_hline(y=70, line_dash="dot", line_color=_CHART_COLORS['secondary'],
                              annotation_text="70%", annotation_position="bottom right")
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for FCF Conversion (need 3+ years)")

        # ── Row 3: Revenue per Share Growth + Debt/FCF ──
        _r3c1, _r3c2 = st.columns(2)

        with _r3c1:
            st.markdown("**Revenue per Share Growth**")
            if _n >= 3:
                rps = [fund['revenue'][i] * 1e6 / fund['shares'][i]
                       if fund['shares'][i] and fund['shares'][i] > 0 else 0
                       for i in range(_n)]
                rps_g = _pct_growth(rps)
                rev_g_clean = _pct_growth(fund['revenue'])
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[r * 100 if r is not None else None for r in rev_g_clean[1:]],
                    name='Revenue Growth',
                    line=dict(color=_CHART_COLORS['primary'], width=2.5),
                    hovertemplate='%{y:.1f}%<extra>Rev Growth</extra>',
                ))
                fig.add_trace(go.Scatter(
                    x=_yrs[1:], y=[r * 100 if r is not None else None for r in rps_g[1:]],
                    name='Rev/Share Growth',
                    line=dict(color=_CHART_COLORS['accent'], width=2.5, dash='dash'),
                    hovertemplate='%{y:.1f}%<extra>Rev/Share Growth</extra>',
                ))
                fig.update_yaxes(ticksuffix='%')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for Revenue per Share (need 3+ years)")

        with _r3c2:
            st.markdown("**Debt / FCF**")
            if _n >= 3:
                debt_fcf = []
                for i in range(_n):
                    fcf_val = fund['fcf'][i]
                    debt_val = fund['total_debt'][i]
                    if fcf_val and fcf_val > 0:
                        debt_fcf.append(debt_val / fcf_val)
                    else:
                        debt_fcf.append(None)
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=_yrs, y=debt_fcf, name='Debt/FCF',
                    line=dict(color=_CHART_COLORS['accent'], width=2.5),
                    hovertemplate='%{y:.1f}x<extra>Debt/FCF</extra>',
                ))
                # Color zone reference lines
                fig.add_hline(y=3, line_dash="dash", line_color=_CHART_COLORS['primary'],
                              annotation_text="3x", annotation_position="top right")
                fig.add_hline(y=5, line_dash="dash", line_color=_CHART_COLORS['secondary'],
                              annotation_text="5x", annotation_position="top right")
                fig.update_yaxes(ticksuffix='x')
                _base_layout(fig)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Insufficient data for Debt/FCF (need 3+ years)")

        # ── Row 4: FCF Yield (full width) ──
        st.markdown("**FCF Yield**")
        if _n >= 2 and live_price > 0:
            fcf_per_share = []
            fcf_yield = []
            for i in range(_n):
                sh = fund['shares'][i]
                if sh and sh > 0 and fund['fcf'][i]:
                    fps = fund['fcf'][i] * 1e6 / sh
                    fcf_per_share.append(fps)
                    fcf_yield.append(fps / live_price * 100)
                else:
                    fcf_per_share.append(None)
                    fcf_yield.append(None)

            # Current FCF Yield prominent display
            current_fy = fcf_yield[-1] if fcf_yield[-1] is not None else 0
            fy_color = '#81b29a' if current_fy > 3 else ('#e07a5f' if current_fy < 1 else '#1d1d1f')
            st.markdown(
                f'<div style="text-align:center;padding:8px 0">'
                f'<span style="font-size:2rem;font-weight:700;color:{fy_color}">{current_fy:.1f}%</span>'
                f'<span style="color:#86868b;font-size:0.9rem;margin-left:8px">current FCF Yield</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=_yrs, y=fcf_yield, name='FCF Yield',
                line=dict(color=_CHART_COLORS['primary'], width=2.5),
                fill='tozeroy', fillcolor='rgba(129,178,154,0.15)',
                hovertemplate='%{y:.1f}%<extra>FCF Yield</extra>',
            ))
            fig.update_yaxes(ticksuffix='%')
            _base_layout(fig, height=250)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Insufficient data for FCF Yield")
```

**Step 4: Verify by running the app**

```bash
python3 -m streamlit run streamlit_app.py --server.headless true
```

Open browser, go to Watchlist, click edit on a ticker, click "Fundamentals" tab.

**Step 5: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: add Fundamentals tab with 7 financial health metrics"
```

---

### Task 3: Add FCF Yield column to watchlist overview

**Files:**
- Modify: `streamlit_app.py:700-714` (row data computation)
- Modify: `streamlit_app.py:719-720` (header columns)
- Modify: `streamlit_app.py:729-744` (row rendering)

**Step 1: Add FCF Yield to row data**

At line 703 (inside the `try` block, after `pe = ...`), add:

```python
            # FCF Yield from most recent year
            fcf_list = cfg_wl.get('hist_fcf', [])
            sh_out = cfg_wl.get('shares_outstanding', 0)
            if fcf_list and sh_out and live_price > 0:
                fcf_yield_val = (fcf_list[-1] * 1e6 / sh_out) / live_price
            else:
                fcf_yield_val = None
```

At line ~713 inside the `rows.append({...})` dict, add:
```python
            'fcf_yield': fcf_yield_val,
```

**Step 2: Update header**

At line 719, change:
```python
    hdr = st.columns([0.4, 1.4, 2.5, 1.1, 1.1, 1.1, 1, 1, 0.3])
    _wl_hdr = ["", "Ticker", "Company", "Price", "Intrinsic", "Buy Price", "Upside", "P/E", ""]
```
to:
```python
    hdr = st.columns([0.4, 1.4, 2.2, 1.0, 1.0, 1.0, 0.9, 0.8, 0.9, 0.3])
    _wl_hdr = ["", "Ticker", "Company", "Price", "Intrinsic", "Buy Price", "Upside", "P/E", "FCF Yield", ""]
```

**Step 3: Update row rendering**

At line 729, change column widths to match:
```python
        cols = st.columns([0.4, 1.4, 2.2, 1.0, 1.0, 1.0, 0.9, 0.8, 0.9, 0.3], vertical_alignment="center")
```

After line 744 (`cols[7].markdown(...)` for P/E), add before the `with cols[8]:` delete button:
```python
        cols[8].markdown(f"{row['fcf_yield']:.1%}" if row['fcf_yield'] else "—")
```

Update the delete button from `cols[8]` to `cols[9]`:
```python
        with cols[9]:
            if st.button("", key=f"wl_rm_row_{t}", icon=":material/close:"):
```

**Step 4: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: add FCF Yield column to watchlist overview"
```

---

### Task 4: Push to live and verify

**Step 1: Also commit the IBIT categorization fix and PPE fallback from earlier**

```bash
git add gather_data.py tastytrade_api.py
git commit -m "fix: ETF sector categorization fallback, PPE XBRL tag fallback"
```

**Step 2: Push everything**

```bash
git push
```

**Step 3: Verify on Streamlit Cloud**

Wait 1-2 minutes for redeploy, then verify:
1. Watchlist overview shows FCF Yield column
2. Click a ticker → Fundamentals tab shows 7 charts
3. IBIT categorized as "Digital Assets" in allocation
