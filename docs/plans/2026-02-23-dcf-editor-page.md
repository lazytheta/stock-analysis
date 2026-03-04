# DCF Editor Page — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the inline watchlist editor with a full DCF editor page, navigated via query params, exposing all key assumptions from the Excel model.

**Architecture:** The existing `elif page == "Watchlist"` block (lines 1234-1483 in `streamlit_app.py`) gets split into two views: an overview (simplified, no inline editor) and an editor (new function `_dcf_editor(ticker)`). Navigation uses `st.query_params["edit"]` to switch between views. All editable fields update the JSON config via `save_config()`, and the Excel is generated from that config.

**Tech Stack:** Streamlit (existing), `config_store.py` (existing), `dcf_calculator.py` (existing), `dcf_template.py` (existing)

---

### Task 1: Refactor watchlist overview — remove inline editor, add click-to-edit

**Files:**
- Modify: `streamlit_app.py:1234-1483` (the entire `elif page == "Watchlist"` block)

**Step 1: Replace the watchlist page block**

Replace lines 1234-1483 with a routing structure. The overview table keeps the same columns but removes the toggle arrow + inline editor. Instead, clicking the ticker navigates to the editor. The remove button stays on each row.

```python
elif page == "Watchlist":

    st.markdown(
        "<style>.block-container { max-width: 1100px; margin: auto; }</style>",
        unsafe_allow_html=True,
    )

    # ── Route: editor or overview ──
    edit_ticker = st.query_params.get("edit")
    if edit_ticker:
        _dcf_editor(edit_ticker.upper())
    else:
        _watchlist_overview()
```

**Step 2: Write `_watchlist_overview()` function**

Place this ABOVE the `elif page == "Watchlist"` block (e.g. after `_build_excel_bytes`). This is the existing overview table, simplified: no toggle arrows, no inline editor. The ticker cell becomes a link/button to the editor.

```python
def _watchlist_overview():
    st.markdown("## Watchlist")
    st.markdown(
        '<p style="color: #86868b; font-size: 1.05rem; line-height: 1.6; max-width: 560px;">'
        'Track intrinsic value vs market price for your watchlist. '
        'Click a ticker to edit the full DCF model.'
        '</p>',
        unsafe_allow_html=True,
    )

    # Red hover effect for delete buttons
    st.markdown("""<style>
    button[data-testid="stBaseButton-secondary"]:has(span[data-testid="stIconMaterial"]):hover {
        background: #fee2e2 !important;
        border-color: #ef4444 !important;
        color: #dc2626 !important;
    }
    </style>""", unsafe_allow_html=True)

    # ── Add ticker ──
    st.markdown("")
    wl_add_col1, wl_add_col2 = st.columns([3, 1])
    with wl_add_col1:
        wl_ticker = st.text_input(
            "Add ticker",
            placeholder="e.g. AAPL",
            label_visibility="collapsed",
            key="wl_ticker_input",
        )
    with wl_add_col2:
        wl_add = st.button("Add to Watchlist", use_container_width=True, type="primary")

    if wl_add and wl_ticker:
        ticker_clean = wl_ticker.strip().upper()
        try:
            _, wl_cfg, _ = run_analysis(
                ticker_clean,
                peer_mode="Auto-discover",
                manual_peers="",
                margin_of_safety=MARGIN_OF_SAFETY_DEFAULT,
                terminal_growth=TERMINAL_GROWTH_DEFAULT,
                n_peers=6,
            )
            save_config(ticker_clean, wl_cfg)
            st.success(f"{ticker_clean} added to watchlist")
            st.rerun()
        except Exception as e:
            st.error(f"Could not analyse {ticker_clean}: {e}")

    # ── Overview table ──
    watchlist = list_watchlist()
    if not watchlist:
        st.info("Your watchlist is empty. Add a ticker above or use 'Add to Watchlist' on the DCF page.")
        return

    @st.cache_data(ttl=300)
    def _fetch_price_cached(t):
        try:
            p, _, _ = fetch_stock_price(t)
            return p
        except Exception:
            return 0.0

    rows = []
    for item in watchlist:
        t = item['ticker']
        cfg_wl = load_config(t)
        if cfg_wl is None:
            continue
        live_price = _fetch_price_cached(t)
        if live_price > 0:
            cfg_wl['stock_price'] = live_price
        val = compute_intrinsic_value(cfg_wl)
        upside = (val['intrinsic_value'] / live_price - 1) if live_price > 0 else 0
        rows.append({
            'ticker': t,
            'company': cfg_wl.get('company', t),
            'price': live_price,
            'intrinsic': val['intrinsic_value'],
            'buy_price': val['buy_price'],
            'upside': upside,
            'wacc': val['wacc'],
        })

    rows.sort(key=lambda r: r['upside'], reverse=True)

    # Header
    hdr = st.columns([0.15, 1, 2.5, 1.1, 1.1, 1.1, 1, 1, 0.3])
    for col, label in zip(hdr, ["", "Ticker", "Company", "Price", "Intrinsic", "Buy Price", "Upside", "WACC", ""]):
        col.markdown(f"**{label}**")

    # Rows — ticker is a button that navigates to editor
    for row in rows:
        t = row['ticker']
        up_color = "green" if row['upside'] > 0 else "red"
        cols = st.columns([0.15, 1, 2.5, 1.1, 1.1, 1.1, 1, 1, 0.3], vertical_alignment="center")
        with cols[0]:
            if st.button("", key=f"wl_edit_{t}", icon=":material/edit:"):
                st.query_params["edit"] = t
                st.rerun()
        cols[1].markdown(f"**{t}**")
        cols[2].markdown(row['company'])
        cols[3].markdown(f"${row['price']:.2f}")
        cols[4].markdown(f"${row['intrinsic']:.2f}")
        cols[5].markdown(f"${row['buy_price']:.2f}")
        cols[6].markdown(f":{up_color}[{row['upside']:+.1%}]")
        cols[7].markdown(f"{row['wacc']:.1%}")
        with cols[8]:
            if st.button("", key=f"wl_rm_row_{t}", icon=":material/close:"):
                remove_from_watchlist(t)
                st.rerun()

    st.markdown("")
```

**Step 3: Verify the overview renders correctly**

Run: `streamlit run streamlit_app.py` and navigate to Watchlist. Confirm the overview table renders without inline editors, and the edit button sets `?edit=TICKER` in the URL.

**Step 4: Commit**

```bash
git add streamlit_app.py
git commit -m "Refactor watchlist: remove inline editor, add edit button navigation"
```

---

### Task 2: Build DCF editor page — header, valuation summary, and action buttons

**Files:**
- Modify: `streamlit_app.py` — add `_dcf_editor(ticker)` function above the page routing block

**Step 1: Write the editor function skeleton with header and actions**

```python
def _dcf_editor(ticker):
    """Full DCF editor page for a single ticker."""
    cfg = load_config(ticker)
    if cfg is None:
        st.error(f"No config found for {ticker}")
        return

    # ── Back button ──
    if st.button("← Watchlist", key="editor_back"):
        del st.query_params["edit"]
        st.rerun()

    # ── Live price ──
    @st.cache_data(ttl=300)
    def _price(t):
        try:
            p, _, _ = fetch_stock_price(t)
            return p
        except Exception:
            return 0.0

    live_price = _price(ticker)
    if live_price > 0:
        cfg['stock_price'] = live_price

    # ── Valuation summary ──
    val = compute_intrinsic_value(cfg)
    upside = (val['intrinsic_value'] / live_price - 1) if live_price > 0 else 0
    up_color = "#81b29a" if upside >= 0 else "#e07a5f"
    up_sign = "+" if upside >= 0 else ""

    st.markdown(
        f'<div class="hero-card">'
        f'<p class="hero-label">{cfg.get("company", ticker)}</p>'
        f'<p class="hero-value" style="font-size:2rem">{ticker}</p>'
        f'<div class="stat-row">'
        f'<span class="stat-pill">Price <b>${live_price:.2f}</b></span>'
        f'<span class="stat-pill">Intrinsic Value <b>${val["intrinsic_value"]:.2f}</b></span>'
        f'<span class="stat-pill">Buy Price <b>${val["buy_price"]:.2f}</b></span>'
        f'<span class="stat-pill">Upside <b style="color:{up_color}">{up_sign}{upside:.1%}</b></span>'
        f'<span class="stat-pill">WACC <b>{val["wacc"]:.1%}</b></span>'
        f'<span class="stat-pill">EV <b>${val["enterprise_value"]:,.0f}M</b></span>'
        f'<span class="stat-pill">Equity Value <b>${val["equity_value"]:,.0f}M</b></span>'
        f'<span class="stat-pill">TV %% of EV <b>{val["tv_pct"]:.0%}</b></span>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Sections below (Tasks 3-7 add content here) ──
    # ... placeholder for now ...

    # ── Action buttons (bottom) ──
    st.markdown("---")
    btn1, btn2, btn3 = st.columns(3)
    with btn1:
        if st.button("Save", key="ed_save", use_container_width=True, type="primary"):
            save_config(ticker, cfg)
            st.success(f"{ticker} saved")
            st.rerun()
    with btn2:
        excel_bytes = _build_excel_bytes(cfg)
        st.download_button(
            label="Download Excel",
            data=excel_bytes,
            file_name=f"{ticker}_DCF.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="ed_dl",
        )
    with btn3:
        if st.button("Remove from Watchlist", key="ed_remove", use_container_width=True):
            remove_from_watchlist(ticker)
            del st.query_params["edit"]
            st.rerun()
```

**Step 2: Verify basic editor renders**

Run the app, go to Watchlist, click edit on a ticker. Confirm the header, valuation summary, and action buttons render. Back button should return to overview.

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "Add DCF editor page skeleton with header, valuation, and actions"
```

---

### Task 3: Add WACC inputs section

**Files:**
- Modify: `streamlit_app.py` — inside `_dcf_editor()`, after the hero card

**Step 1: Add WACC inputs section**

Insert this after the hero card markdown and before the action buttons:

```python
    # ── Section: WACC Inputs ──
    st.markdown("#### WACC Inputs")
    w1, w2, w3, w4 = st.columns(4)
    with w1:
        cfg['risk_free_rate'] = st.number_input(
            "Risk-Free Rate %", value=cfg.get('risk_free_rate', 0.04) * 100,
            step=0.1, format="%.2f", key="ed_rfr",
        ) / 100
    with w2:
        cfg['erp'] = st.number_input(
            "Equity Risk Premium %", value=cfg.get('erp', 0.055) * 100,
            step=0.1, format="%.2f", key="ed_erp",
        ) / 100
    with w3:
        cfg['credit_spread'] = st.number_input(
            "Credit Spread %", value=cfg.get('credit_spread', 0.01) * 100,
            step=0.1, format="%.2f", key="ed_cs",
        ) / 100
    with w4:
        cfg['tax_rate'] = st.number_input(
            "Tax Rate %", value=cfg.get('tax_rate', 0.21) * 100,
            step=0.5, format="%.1f", key="ed_tax",
        ) / 100

    ev1, ev2 = st.columns(2)
    with ev1:
        cfg['equity_market_value'] = st.number_input(
            "Equity Market Value ($M)", value=cfg.get('equity_market_value', 0),
            step=1000, key="ed_eq_val",
        )
    with ev2:
        cfg['debt_market_value'] = st.number_input(
            "Debt Market Value ($M)", value=cfg.get('debt_market_value', 0),
            step=100, key="ed_debt_val",
        )

    # Sector betas table
    betas = cfg.get('sector_betas', [])
    if betas:
        st.markdown("**Sector Betas**")
        for i, (name, beta, weight) in enumerate(betas):
            bc1, bc2, bc3 = st.columns(3)
            with bc1:
                new_name = st.text_input("Sector", value=name, key=f"ed_bn_{i}")
            with bc2:
                new_beta = st.number_input(
                    "Unlevered Beta", value=float(beta), step=0.01,
                    format="%.2f", key=f"ed_bb_{i}",
                )
            with bc3:
                new_weight = st.number_input(
                    "Revenue Weight", value=float(weight), step=0.05,
                    format="%.2f", key=f"ed_bw_{i}",
                )
            betas[i] = (new_name, new_beta, new_weight)
        cfg['sector_betas'] = betas

    # Show computed WACC
    computed_wacc = compute_wacc(cfg)
    st.markdown(f"**Computed WACC: {computed_wacc:.2%}**")
```

**Step 2: Verify WACC section renders and edits work**

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "Add WACC inputs section to DCF editor"
```

---

### Task 4: Add 10-year projections section

**Files:**
- Modify: `streamlit_app.py` — inside `_dcf_editor()`, after WACC section

**Step 1: Add projections section**

```python
    # ── Section: 10-Year Projections ──
    st.markdown("#### 10-Year Projections")
    base_year = cfg.get('base_year', 2025)
    growth = list(cfg.get('revenue_growth', []))
    margins = list(cfg.get('op_margins', []))
    n_years = len(growth)

    st.markdown(
        f'<div class="stat-row">'
        f'<span class="stat-pill">Base Year <b>{base_year}</b></span>'
        f'<span class="stat-pill">Base Revenue <b>${cfg.get("base_revenue", 0):,}M</b></span>'
        f'<span class="stat-pill">Base Op Income <b>${cfg.get("base_oi", 0):,}M</b></span>'
        f'<span class="stat-pill">Base Op Margin <b>{cfg.get("base_op_margin", 0):.1%}</b></span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    proj_cols = st.columns(3)
    with proj_cols[0]:
        st.markdown("**Year**")
        for i in range(n_years):
            st.markdown(f"<div style='height:52px;display:flex;align-items:center'>{base_year + i + 1}</div>", unsafe_allow_html=True)
    with proj_cols[1]:
        st.markdown("**Revenue Growth %**")
        for i in range(n_years):
            growth[i] = st.number_input(
                f"Growth {base_year + i + 1}", value=growth[i] * 100,
                step=0.5, format="%.1f", key=f"ed_g_{i}",
                label_visibility="collapsed",
            ) / 100
    with proj_cols[2]:
        st.markdown("**Operating Margin %**")
        for i in range(n_years):
            margins[i] = st.number_input(
                f"Margin {base_year + i + 1}", value=margins[i] * 100,
                step=0.5, format="%.1f", key=f"ed_m_{i}",
                label_visibility="collapsed",
            ) / 100
    cfg['revenue_growth'] = growth
    cfg['op_margins'] = margins
```

**Step 2: Commit**

```bash
git add streamlit_app.py
git commit -m "Add 10-year projections section to DCF editor"
```

---

### Task 5: Add terminal, shares, and scenario sections

**Files:**
- Modify: `streamlit_app.py` — inside `_dcf_editor()`, after projections

**Step 1: Add terminal & assumptions**

```python
    # ── Section: Terminal & Assumptions ──
    st.markdown("#### Terminal & Assumptions")
    ta1, ta2, ta3 = st.columns(3)
    with ta1:
        cfg['terminal_growth'] = st.number_input(
            "Terminal Growth %", value=cfg.get('terminal_growth', 0.03) * 100,
            step=0.5, format="%.1f", key="ed_tg",
        ) / 100
        cfg['terminal_margin'] = st.number_input(
            "Terminal Margin %", value=cfg.get('terminal_margin', 0.30) * 100,
            step=0.5, format="%.1f", key="ed_tm",
        ) / 100
    with ta2:
        cfg['sales_to_capital'] = st.number_input(
            "Sales-to-Capital", value=cfg.get('sales_to_capital', 1.0),
            step=0.05, format="%.2f", key="ed_stc",
        )
        cfg['sbc_pct'] = st.number_input(
            "SBC %", value=cfg.get('sbc_pct', 0.004) * 100,
            step=0.1, format="%.1f", key="ed_sbc",
        ) / 100
    with ta3:
        cfg['buyback_rate'] = st.number_input(
            "Buyback Rate %", value=cfg.get('buyback_rate', 0.0) * 100,
            step=0.5, format="%.1f", key="ed_bb",
        ) / 100
        cfg['margin_of_safety'] = st.slider(
            "Margin of Safety", 0, 50,
            value=int(cfg.get('margin_of_safety', 0.20) * 100),
            step=5, format="%d%%", key="ed_mos",
        ) / 100
```

**Step 2: Add shares & equity bridge**

```python
    # ── Section: Shares & Equity Bridge ──
    st.markdown("#### Shares & Equity Bridge")
    sb1, sb2, sb3 = st.columns(3)
    with sb1:
        cfg['shares_outstanding'] = st.number_input(
            "Shares Outstanding (M)", value=cfg.get('shares_outstanding', 0),
            step=10, key="ed_shares",
        )
    with sb2:
        cfg['cash_bridge'] = st.number_input(
            "Cash ($M)", value=cfg.get('cash_bridge', 0),
            step=100, key="ed_cash",
        )
    with sb3:
        cfg['securities'] = st.number_input(
            "Short-term Securities ($M)", value=cfg.get('securities', 0),
            step=100, key="ed_sec",
        )
```

**Step 3: Add scenario adjustments**

```python
    # ── Section: Scenario Adjustments ──
    st.markdown("#### Scenario Adjustments")
    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown("**Bull Case**")
        cfg['bull_growth_adj'] = st.number_input(
            "Growth Adjustment %", value=cfg.get('bull_growth_adj', 0.02) * 100,
            step=0.5, format="%.1f", key="ed_bull_g",
        ) / 100
        cfg['bull_margin_adj'] = st.number_input(
            "Margin Adjustment %", value=cfg.get('bull_margin_adj', 0.05) * 100,
            step=0.5, format="%.1f", key="ed_bull_m",
        ) / 100
    with sc2:
        st.markdown("**Bear Case**")
        cfg['bear_growth_adj'] = st.number_input(
            "Growth Adjustment %", value=cfg.get('bear_growth_adj', -0.02) * 100,
            step=0.5, format="%.1f", key="ed_bear_g",
        ) / 100
        cfg['bear_margin_adj'] = st.number_input(
            "Margin Adjustment %", value=cfg.get('bear_margin_adj', -0.05) * 100,
            step=0.5, format="%.1f", key="ed_bear_m",
        ) / 100
```

**Step 4: Commit**

```bash
git add streamlit_app.py
git commit -m "Add terminal, shares, and scenario sections to DCF editor"
```

---

### Task 6: Add read-only peer comparison and historical data sections

**Files:**
- Modify: `streamlit_app.py` — inside `_dcf_editor()`, after scenario section

**Step 1: Add peer comparison**

```python
    # ── Section: Peer Comparison (read-only) ──
    peers = cfg.get('peers', [])
    if peers:
        st.markdown("#### Peer Comparison")
        peer_html = '<div class="portfolio-cards">'
        for p in peers:
            peer_html += (
                f'<div class="portfolio-card" style="justify-content:center;text-align:center">'
                f'<span class="pf-ticker">{p.get("ticker", "")}</span>'
                f'<div class="pf-cell"><span class="pf-label">EV/Rev</span>'
                f'<span class="pf-val">{p.get("ev_revenue", 0):.1f}x</span></div>'
                f'<div class="pf-cell"><span class="pf-label">EV/EBITDA</span>'
                f'<span class="pf-val">{p.get("ev_ebitda", 0):.1f}x</span></div>'
                f'<div class="pf-cell"><span class="pf-label">P/E</span>'
                f'<span class="pf-val">{p.get("pe", 0):.1f}x</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Op Margin</span>'
                f'<span class="pf-val">{p.get("op_margin", 0):.1%}</span></div>'
                f'<div class="pf-cell"><span class="pf-label">Rev Growth</span>'
                f'<span class="pf-val">{p.get("rev_growth", 0):.1%}</span></div>'
                f'</div>'
            )
        peer_html += '</div>'
        st.markdown(peer_html, unsafe_allow_html=True)
```

**Step 2: Add historical data**

```python
    # ── Section: Historical Data (read-only) ──
    ic_years = cfg.get('ic_years', [])
    if ic_years:
        st.markdown("#### Historical Data")

        # Income statement
        st.markdown("**Income Statement ($M)**")
        hist_rows = [
            ("Revenue", cfg.get('hist_revenue', [])),
            ("Operating Income", cfg.get('hist_operating_income', [])),
            ("Net Income", cfg.get('hist_net_income', [])),
            ("Cost of Revenue", cfg.get('hist_cost_of_revenue', [])),
            ("SBC", cfg.get('hist_sbc_values', [])),
            ("Shares (M)", cfg.get('hist_shares', [])),
        ]
        hist_header = "| |" + "|".join(str(y) for y in ic_years) + "|\n"
        hist_header += "|---|" + "|".join("---:" for _ in ic_years) + "|\n"
        hist_body = ""
        for label, vals in hist_rows:
            if vals:
                hist_body += f"| **{label}** |" + "|".join(f"{v:,}" for v in vals) + "|\n"
        if hist_body:
            st.markdown(hist_header + hist_body)

        # Balance sheet
        st.markdown("**Balance Sheet ($M)**")
        bs_rows = [
            ("Current Assets", cfg.get('current_assets', [])),
            ("Cash", cfg.get('cash', [])),
            ("ST Investments", cfg.get('st_investments', [])),
            ("Operating Cash", cfg.get('operating_cash', [])),
            ("Current Liabilities", cfg.get('current_liabilities', [])),
            ("ST Debt", cfg.get('st_debt', [])),
            ("ST Leases", cfg.get('st_leases', [])),
            ("Net PP&E", cfg.get('net_ppe', [])),
            ("Goodwill & Intang.", cfg.get('goodwill_intang', [])),
        ]
        bs_header = "| |" + "|".join(str(y) for y in ic_years) + "|\n"
        bs_header += "|---|" + "|".join("---:" for _ in ic_years) + "|\n"
        bs_body = ""
        for label, vals in bs_rows:
            if vals:
                bs_body += f"| **{label}** |" + "|".join(f"{v:,}" for v in vals) + "|\n"
        if bs_body:
            st.markdown(bs_header + bs_body)
```

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "Add peer comparison and historical data sections to DCF editor"
```

---

### Task 7: Final integration — wire Save to collect all edited fields

**Files:**
- Modify: `streamlit_app.py` — the Save button in `_dcf_editor()`

**Step 1: Update Save button**

Since we mutate `cfg` directly throughout the function (each `number_input` writes back to `cfg[key]`), the Save button at the bottom already has the updated config. Just verify the save call uses the mutated `cfg`:

```python
    if st.button("Save", key="ed_save", use_container_width=True, type="primary"):
        save_config(ticker, cfg)
        st.success(f"{ticker} saved")
        st.rerun()
```

No change needed — the cfg dict is mutated in place by all sections above.

**Step 2: End-to-end test**

1. Open Watchlist
2. Click edit on a ticker
3. Change revenue growth year 1 from e.g. 15% to 20%
4. Change risk-free rate
5. Click Save
6. Verify the hero card updates with new valuation
7. Click Download Excel — open it, verify the changed values appear
8. Click Back — verify overview shows updated intrinsic value

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "Complete DCF editor page with full parameter editing"
```
