# Business Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Business" tab that looks like the inspiration example: Business overview + Customer profile panels, a Revenue section (segment bar/table + world map by region), and four Business-quality flip cards — fed by a new JSON pre-scan section "Business Cards".

**Architecture:** Generalise the optional block in `question_cards.CardSet` (Moat's `trend`) into named blocks with a point count; add a flat text-panel helper. New `business_cards.py` (definition, prompt, parser incl. revenue validation, HTML for overview + quality). New `business_revenue.py` (region→country mapping, segment HTML, Plotly map figure, legend). Streamlit adds the tab; the Revenue section is a keyed `st.container` styled like `.qc-section`.

**Tech Stack:** Python 3.11, Streamlit 1.54, Plotly 6.5 (`plotly.express.data.gapminder` for countries), pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-business-tab-design.md`

## Global Constraints

- Python 3.11: no backslash inside an f-string expression.
- `python3 -m ruff check .` passes; `python3 -m pytest -q test_*.py tests` all green except the known `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer`; `(cd lazytheta-mcp-cloudrun && python3 -m pytest -q)` green.
- No module-level import of `prescan_render` or `plotly` in `question_cards`, `moat_cards`, `risk_cards`, `business_cards`, `business_revenue` (Cloud Run image ships neither `prescan_render` nor necessarily plotly; `mcp_server` imports the card modules for parsing). Every root module `mcp_server` imports must be in the root `Dockerfile` COPY line (tests/test_dockerfile_modules.py).
- All HTML single-line and `$`-safe via `question_cards.esc` / `css` / `bold`; no raw model text.
- Moat and Risk behaviour unchanged: `tests/test_moat_cards.py`, `tests/test_risk_cards.py`, `tests/test_question_cards.py` stay green (only the tab-list assertions may be updated in Task 4).
- Section title exact `Business Cards`; card keys/order exact `predictability`, `pricing_power`, `recession`, `competitive_position`; pick 0 worst (red) … 2 best (green).
- Region vocabulary exact: `US`, `Canada`, `North America`, `Latin America`, `Europe`, `EMEA`, `Middle East & Africa`, `Asia Pacific`, `China`, `Japan`, `India`, `Rest of world`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: Engine — named optional blocks + text panels

**Files:** Modify `question_cards.py`, `moat_cards.py`; Test `tests/test_question_cards.py`.

**Produces:**
- `CardSet(title, items, directions, blocks=())` where `blocks` is a tuple of `(name, n_points)`; Moat uses `blocks=(("trend", 3),)`. Replace the old `trend: bool` field (update `moat_cards.MOAT`, `risk_cards.RISK` — Risk has `blocks=()`).
- `parse(cs, content)` returns `{"cards": [...], <name>: block_or_None for each block}`; a block is `{"summary": non-empty, "points": exactly n_points}`; blocks not declared are ignored. Error messages name the block (`"trend: ..."`, `"overview: ..."`). `_points` takes the expected count.
- `text_panel_html(title, lead_html, points) -> str` — flat panel (`.ms-card`-like background `var(--qc-inner, var(--bg-secondary))`, radius 16px, padding 18px 22px) with a small-caps title, one lead paragraph and a `<ul>` of `<b>label.</b> text` items; height NOT fixed (grows with text; the row stretches both to equal height).
- `text_row_html(left_html, right_html) -> str` — two panels in a grid row, `align-items:stretch` (CSS class `.tp-row`, one column under 760px).
- Add `.tp-*` CSS to `SUMMARY_STYLE` (single line when emitted via `css`).

- [ ] **Step 1: failing tests** (append to `tests/test_question_cards.py`):
```python
BS = qc.CardSet("Block Cards", CS.items, directions=None, blocks=(("overview", 4),))


def _block(n):
    return {"summary": "Lead $1.", "points": [{"label": f"B{i}", "text": f"t{i}"} for i in range(n)]}


def test_named_block_is_optional_and_checked_for_its_point_count():
    assert qc.parse(BS, json.dumps(_p()))["overview"] is None
    assert len(qc.parse(BS, json.dumps(_p(overview=_block(4))))["overview"]["points"]) == 4
    with pytest.raises(ValueError, match="overview"):
        qc.parse(BS, json.dumps(_p(overview=_block(3))))


def test_undeclared_blocks_are_ignored():
    out = qc.parse(CS, json.dumps(_p(overview=_block(1))))
    assert "overview" not in out


def test_text_row_has_two_panels_with_bullets_and_is_markdown_safe():
    left = qc.text_panel_html("Business overview", qc.bold("A **bold** $5 lead."),
                              _block(4)["points"])
    html = qc.text_row_html(left, left)
    assert html.count('class="tp-panel"') == 2 and html.count("<li") == 8
    assert "<b>bold</b>" in html and "$" not in html and "\n" not in html
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement; update `moat_cards.MOAT` to `blocks=(("trend", 3),)` and `risk_cards.RISK` to `blocks=()`; `moat_cards.parse_moat_cards` keeps returning `{"cards", "trend"}`.
- [ ] **Step 4:** `pytest tests/test_question_cards.py tests/test_moat_cards.py tests/test_risk_cards.py` green without editing the Moat/Risk tests; ruff; full suite; cloudrun.
- [ ] **Step 5:** commit `Kaartenmotor: benoemde optionele blokken + tekstpanelen`.

---

### Task 2: `business_cards.py` — definition, prompt, parser, overview + quality HTML

**Files:** Create `business_cards.py`, `tests/test_business_cards.py`.

**Consumes:** Task 1 engine. **Produces:** `TITLE = "Business Cards"`, `ITEMS`, `BUSINESS: CardSet` (`directions=None`, `blocks=(("overview", 4), ("profile", 4))`), `REGIONS` (tuple, exact vocabulary), `PROMPT`, `parse_business_cards(content) -> dict` (adds validated `"revenue"` or None), `overview_section_html(business_analysis, content, theme) -> str`, `quality_section_html(content, theme) -> str`.

Revenue validation (in `parse_business_cards`, after `qc.parse`): optional key `revenue`; when present:
- `period`: non-empty string; `total_musd`: number > 0; `growth_pct`: number or null;
- `segments`: list ≥ 1 of `{name: non-empty, revenue_musd: number ≥ 0, growth_pct: number|null}`; sum of `revenue_musd` within ±3% of `total_musd`;
- `regions`: list (may be empty) of `{region: in REGIONS, label: optional string (default = region), share_pct: number ≥ 0}`; no duplicate `region`; when non-empty, shares sum to 95–105;
- errors raise `ValueError("revenue: ...")`. Numbers: accept int/float, reject bool and strings.

Overview section: one `qc.section_html("Business", qc.text_row_html(overview_panel, profile_panel))`, where each panel carries its own title ("BUSINESS OVERVIEW", "CUSTOMER PROFILE"), as in the example. Overview panel = Business Cards `overview` block, else fallback from the Business Analysis verdict (`prescan_render.parse_verdict_section`: `bold(v["summary"])` + `v["bullets"]`), else "Not filled yet." Profile panel = `profile` block, else "Not filled yet.".

Quality section: `qc.section_html("Business quality", qc.grid_html(BUSINESS, cards, theme))` or the notice.

```python
ITEMS = (
    ("predictability", "Revenue predictability", "How predictable is revenue?",
     ("Unpredictable", "Modest", "Predictable")),
    ("pricing_power", "Pricing power", "Can the company raise prices?",
     ("No", "Sometimes", "Easily")),
    ("recession", "Demand resilience", "How recession-proof is it?",
     ("Weak", "Okay", "Strong")),
    ("competitive_position", "Competitive position", "What is their competitive position?",
     ("Weak", "Average", "Dominant")),
)
```

PROMPT (write it in the style of `moat_cards.PROMPT` / `risk_cards.PROMPT`): context `{prior:Business Analysis}`, `{prior:Moat Analysis}`, `{prior:Key Metrics}`; asks for:
- `overview`: one sentence on what the company sells and how it earns, + exactly four points (label = two to five words, text = one line) on products/plans, revenue model, what makes the model distinctive;
- `profile`: one sentence on who the buyer is and what they pay from, + exactly four points on customer types (who, where, why they buy);
- `revenue`: from the latest 10-K segment/geography note, in USD millions: `period` (e.g. "FY2026"), `total_musd`, `growth_pct` (vs prior year), `segments` (name, revenue_musd, growth_pct), `regions` (region from the fixed list — map the company's own names onto it, e.g. UCAN → "North America", APAC → "Asia Pacific", "International" → "Rest of world" — plus the company's own name as `label`, and `share_pct`). If the company reports no geography split, `regions: []`;
- the four `cards` (0 = worst … 2 = best), summary one sentence, exactly three points;
- output only a fenced JSON block; include the region list verbatim.

- [ ] **Step 1: tests** `tests/test_business_cards.py`: items/title; parse accepts a full valid payload (write a fixture with overview/profile 4 points, revenue with 2 segments summing to total and 3 regions summing to 100); refuses: segments not summing (±3%), region not in vocabulary, duplicate region, shares summing to 80, string number, bool number; revenue absent → `"revenue"` is None; overview section renders both titles and 4+4 bullets from the blocks; falls back to the Business Analysis verdict text when blocks are absent (use a verdict-shaped text with three bullets); quality section renders 4 cards or the notice; prompt contains all three priors, every item key and every region name; HTML has no `$` and no newline; no module-level `prescan_render` import.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** tests + ruff + full suite. **Step 5:** commit `Business Cards: definitie, prompt, parser met omzetvalidatie, overview- en qualitysecties`.

---

### Task 3: `business_revenue.py` — segment panel, world map, legend

**Files:** Create `business_revenue.py`, `tests/test_business_revenue.py`.

**Consumes:** a validated `revenue` dict (Task 2 shape). **Produces:**
- `REGION_ORDER` (specific → broad): `("US", "Canada", "China", "Japan", "India", "North America", "Latin America", "Europe", "Middle East & Africa", "EMEA", "Asia Pacific", "Rest of world")`.
- `countries_by_region(regions: list[str]) -> dict[str, list[str]]` — ISO-3 codes per listed region. Universe = `plotly.express.data.gapminder()` unique `(iso_alpha, continent)` plus `_EXTRA = {"RUS": "Europe", "UKR": "Europe", "BLR": "Europe", "KAZ": "Asia", "UZB": "Asia", "TKM": "Asia", "KGZ": "Asia", "TJK": "Asia", "AZE": "Asia", "GEO": "Asia", "ARM": "Asia", "MDA": "Europe", "EST": "Europe", "LVA": "Europe", "LTU": "Europe", "GRL": "Americas", "PNG": "Oceania", "GUY": "Americas", "SUR": "Americas", "BLZ": "Americas", "SSD": "Africa", "ESH": "Africa"}` (add any not already present). Middle East set: `{"SAU","ARE","QAT","KWT","OMN","BHR","YEM","IRQ","IRN","ISR","JOR","LBN","SYR","TUR","AFG"}` — these count as Middle East, not Asia/Europe. Region membership: US={USA}; Canada={CAN}; China={CHN}; Japan={JPN}; India={IND}; North America={USA,CAN}; Latin America=Americas−{USA,CAN,GRL}; Europe=continent Europe−Middle East set; Middle East & Africa=Africa ∪ Middle East set; EMEA=Europe ∪ Africa ∪ Middle East set; Asia Pacific=(Asia ∪ Oceania)−Middle East set; Rest of world=everything. Assign each country to the FIRST listed region in `REGION_ORDER` that contains it (so a listed "China" takes CHN away from "Asia Pacific", and "Rest of world" only gets what nothing else took). Import plotly inside the function.
- `segments_panel_html(revenue, theme) -> str` — flat panel (qc styles): small-caps "BY SEGMENT", right-aligned total (`$3.20B` formatting: ≥1000 → B with 2 decimals, else M) with `▲ 16.3% · 1-YR` (red ▼ when negative, omitted when null); a horizontal stacked bar (one colour per segment from a fixed 6-colour palette in the site's green/beige/brown family, share % inside when ≥ 8%); a table: Segment (colour square + name) · Share · Revenue · 1-YR growth (green ▲ / red ▼ / "—").
- `geography_figure(revenue, theme)` — Plotly `go.Figure` choropleth (`locationmode="ISO-3"`, discrete colour per region via one trace per region with a single-colour `colorscale`, `showscale=False`), `geo=dict(showframe=False, showcoastlines=False, projection_type="natural earth", bgcolor="rgba(0,0,0,0)", showland=True, landcolor=<light neutral>, lataxis_range=[-58, 85])`, transparent paper, margins 0, height 320. Returns None when `regions` is empty.
- `geography_header_html(revenue, theme)` and `geography_legend_html(revenue, theme)` — "BY GEOGRAPHY" + total; legend dots `label · 44%` in the same colours as the map.
- Region colour: fixed palette in `REGION_COLORS` keyed by position in the company's list (so the biggest region gets the strongest green, following the example).

- [ ] **Step 1: tests**: `countries_by_region(["North America","Europe","Asia Pacific","Rest of world"])` → USA in North America, DEU in Europe, JPN in Asia Pacific, BRA in Rest of world, SAU in Rest of world; with `["US","China","Asia Pacific"]` → CHN in China not Asia Pacific; EMEA contains DEU, ZAF and SAU; Latin America excludes USA/CAN; segments panel shows the total "$3.20B", the growth "16.3%", every segment name and a table row per segment, no `$` raw/newline; figure has one trace per listed region and None for empty regions; legend shows each label with its share; module import does not import plotly (check `sys.modules`).
- [ ] **Steps 2-5:** FAIL → implement → green + ruff + full suite → commit `Business-omzet: segmentpaneel, wereldkaart per regio en legenda`.

---

### Task 4: Integration — MCP, Dockerfile, prompt library, Business tab, Pre-Scan, docs

**Files:** Modify `mcp_server.py`, `Dockerfile`, `streamlit_app.py`, `docs/routines/moat-cards-backfill.md`, `docs/routines/aspirant-weekly.md`, `tests/test_moat_cards.py` + `tests/test_risk_cards.py` (tab-list strings only), `tests/test_business_cards.py`.

- [ ] **Step 1: tests** (append to `tests/test_business_cards.py`): MCP refuses malformed Business Cards and accepts valid (same pattern as `tests/test_risk_cards.py::test_mcp_refuses_malformed_risk_cards_and_accepts_valid`); `DEFAULT_AI_PROMPTS` has "Business Cards" right after "Key Metrics"; source contains `'["Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", "DCF"'`, `business_cards.overview_section_html(`, `business_cards.quality_section_html(`, `business_revenue.geography_figure(`. Update the two tab-list assertions in the Moat/Risk tests to the new list.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3: implement**
  1. `mcp_server.py`: `import business_cards`; add `business_cards.TITLE: business_cards.parse_business_cards` to `_CARD_PARSERS`. `business_revenue` is NOT imported by mcp_server.
  2. `Dockerfile`: add `business_cards.py`.
  3. `streamlit_app.py`: `import business_cards` and `import business_revenue` with the other local imports; `DEFAULT_AI_PROMPTS` entry `{"title": business_cards.TITLE, "prompt": business_cards.PROMPT}` directly after the "Key Metrics" dict; tabs `["Pre-Scan", "Business", "Moat", "Risk", "Fundamentals", ...]` with `_tab_business` right after `_tab_notes` in the unpacking; Business tab body:
     ```python
     # Business: overview and customer profile, revenue by segment and region,
     # then the four business-quality cards. Read-only, like Moat and Risk.
     with _tab_business:
         _bnotes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else {}
         _bcontent = _bnotes.get(business_cards.TITLE)
         st.markdown(business_cards.overview_section_html(
             _bnotes.get("Business Analysis") or "", _bcontent, T), unsafe_allow_html=True)
         try:
             _brev = business_cards.parse_business_cards(_bcontent)["revenue"] if _bcontent else None
         except ValueError:
             _brev = None
         with st.container(key="qc_revenue_section"):
             st.markdown('<div class="qc-label">Revenue</div>', unsafe_allow_html=True)
             if _brev:
                 _rl, _rr = st.columns(2)
                 with _rl:
                     st.markdown(business_revenue.segments_panel_html(_brev, T),
                                 unsafe_allow_html=True)
                 with _rr:
                     st.markdown(business_revenue.geography_header_html(_brev, T),
                                 unsafe_allow_html=True)
                     _fig = business_revenue.geography_figure(_brev, T)
                     if _fig is not None:
                         st.plotly_chart(_fig, use_container_width=True,
                                         config={"displayModeBar": False})
                         st.markdown(business_revenue.geography_legend_html(_brev, T),
                                     unsafe_allow_html=True)
                     else:
                         st.caption("No geographic split reported.")
             else:
                 st.caption("No revenue breakdown yet. It comes with the \"Business Cards\" section.")
         st.markdown(business_cards.quality_section_html(_bcontent, T), unsafe_allow_html=True)
     ```
     Add CSS in the app's global style block (next to `[class*="st-key-tabcard_"]`): `.st-key-qc_revenue_section { background: var(--card); border-top: 3px solid var(--accent); border-radius: 24px; box-shadow: var(--shadow); padding: 20px 24px 24px; margin: 0 0 18px; }` and make `.qc-label` available globally (same rule as in `question_cards.STYLE`) or inline the label style. The geography column's panel look (flat inner background) may be done by `st.container(key="qc_revenue_geo")` with `background: color-mix(in srgb, var(--text) 4%, var(--card)); border-radius:16px; padding:16px 18px`.
     Pre-Scan: render "Business Cards" via `business_cards.quality_section_html(_content, T)` in the existing `if/elif` chain.
  4. `docs/routines/moat-cards-backfill.md`: add Business Cards as the third set (`requires="Business Analysis"`), same budget rule (10 total, skip when 0 remaining); for Business Cards also call SEC-MCP `GetRevenueBreakdown` once per ticker if the connector is available, else use the 10-K via filings; mention the region list lives in the prompt. Keep the filename.
  5. `docs/routines/aspirant-weekly.md` step 2b: Business Cards needs Business Analysis, Moat Analysis and Key Metrics saved first (library order already does this); refusals handled like the other card sets.
- [ ] **Step 4:** tests + `py_compile streamlit_app.py` + ruff + full suite + cloudrun.
- [ ] **Step 5:** commit `Business-tab: overview, omzet per segment en regio, business quality; MCP, prompt en routines`.

After merge the controller: guarded SQL insert of the prompt after "Key Metrics" BEFORE the Streamlit reboot, push, Cloud Run deploy, fill VEEV's Business Cards, preview check, ask the user to reboot.
