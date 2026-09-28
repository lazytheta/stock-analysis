# Growth Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Growth" tab (after Moat): Growth Analysis + Consensus cards from a new Claude-authored "Growth Cards" section, a revenue & earnings chart with YoY labels and a CAGR table from EDGAR, and two growth question flip cards.

**Architecture:** `growth_cards.py` defines the card set, prompt and validation (pattern: `business_cards.py`); `growth_page.py` renders the sections and the figure (pattern: `phase_page.py`); `streamlit_app.py` wires the tab; MCP validates the section on save; routine docs fill it.

**Tech Stack:** Python 3.11+, Streamlit 1.54, Plotly 6.5, pytest.

**Spec:** `docs/superpowers/specs/2026-09-28-growth-tab-design.md`

## Global Constraints

- Tab order: `["Overview", "Pre-Scan", "Business", "Phase", "Moat", "Growth", "Risk", "Fundamentals", "DCF", "Reverse DCF", "Peer Comparison", "Dividend", "History"]`.
- Section title exactly `"Growth Cards"`. Card items exactly: `("industry", "Industry Growth", "Is the industry growing?", ("No", "Slowly", "Yes"))`, `("optionality", "Optionality", "Can new offerings drive growth?", ("Unlikely", "Possible", "Likely"))` — match the tuple shape `question_cards` uses.
- Score labels: `{1: "Weak", 2: "Below average", 3: "Average", 4: "Strong", 5: "Exceptional"}`; tone red ≤ 2, yellow 3, green ≥ 4.
- Validation rules exactly as the spec's "Data" section.
- House style / Streamlit HTML rules: `question_cards.section_html`, flat inner cards (`--qc-inner`), CSS on one line via `question_cards.css`, `question_cards.esc` everything, HTML starts with a tag, missing → `—`, never crash (each render path in try/except → caption).
- Chart ranges `("5Y", "10Y")`, default `"5Y"`; y-axis ticks readable like the Phase chart (reuse `phase_page`'s tick helper rather than copying it — make it importable if it is private).
- Tests offline; ruff clean; known unrelated failure `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer`.

---

### Task 1: `growth_cards.py` + MCP + prompt + Pre-Scan + routines

**Files:** Create `growth_cards.py`, `tests/test_growth_cards.py`; modify `mcp_server.py` (`_CARD_PARSERS` + import), `Dockerfile` (MCP COPY list: add `growth_cards.py`), `streamlit_app.py` (import; `DEFAULT_AI_PROMPTS` entry directly after the `company_profile.TITLE` entry; Pre-Scan JSON-section rendering: add a branch for Growth Cards like Business Cards — show analysis + cards via the Task-2-independent helpers in growth_cards or a simple escaped list), `docs/routines/moat-cards-backfill.md` (fifth set "Growth Cards", requires "Long-Term Potential", same shared budget of 10; consensus via SEC-MCP `GetAnalystEstimates`, omit the block when unavailable, never estimate), `docs/routines/aspirant-nightly.md` (Growth Cards in the re-fetch list after Company Profile; same consensus rule).

**Interfaces (produces):** `TITLE = "Growth Cards"`, `GROWTH: question_cards.CardSet`, `SCORE_LABELS`, `PROMPT: str` (placeholders `{company}`, `{ticker}`, `{prior:Long-Term Potential}`, `{prior:Business Analysis}`, `{prior:Key Metrics}`; instructs: consensus from filings-grade source / SEC connector `GetAnalystEstimates` for next fiscal year revenue and EPS growth, analyst count, source + date; omit `consensus` when not available; never estimate; output only a fenced JSON block matching the spec example), `parse_growth_cards(content: str) -> dict` returning `{"analysis": {...}, "consensus": {...} | None, "cards": [...]}` or raising `ValueError`.

- [ ] Read `business_cards.py` (PROMPT style, how it wraps `question_cards.parse` and validates an extra block) and mirror it.
- [ ] Tests: valid full JSON parses; consensus omitted/null → None; consensus with both growth figures null → error; analysts 0, bool, "23" → error; growth pct 1500 → error; score 0/6/True → error; analysis missing → error; cards wrong order → error; prompt contains all placeholders and "never estimate"; `mcp_server._CARD_PARSERS[TITLE] is parse_growth_cards`; DEFAULT_AI_PROMPTS order company_profile.TITLE before growth_cards.TITLE (source index test like tests/test_company_profile.py); Dockerfile contains growth_cards.py.
- [ ] Implement; run `tests/test_growth_cards.py`, `test_mcp_server.py`, `cd lazytheta-mcp-cloudrun && python3 -m pytest -q`, ruff. Commit `Growth Cards section: prompt, validation, MCP, routines`.

### Task 2: `growth_page.py`

**Files:** Create `growth_page.py`, `tests/test_growth_page.py`; possibly modify `phase_page.py` only to expose its money-tick helper publicly (keep its tests green).

**Interfaces (produces):**
- `growth_section_html(content: str | None, theme) -> str` — `section_html("Growth", …)`: two equal-height flat cards, Growth Analysis (meter via `question_cards.dial_box_html(score, 5, label, tone)` or the pattern `risk_cards`/`moat_cards` use for their summary cards; lead + 3 bold-label bullets) and Consensus (four label/value pairs: "Revenue growth next FY" signed pct one decimal, "EPS growth next FY", "Analysts", "Fiscal year"; source as small muted line; absent → "No analyst consensus available."). Invalid/missing content → the spec's notice text (single full-width card).
- `revenue_earnings_series(fund, years: int) -> tuple[list[int], list, list]` (last N fiscal years with revenue; revenue and net_income $M).
- `yoy_labels(values) -> list[str]` — `""` for the first point or when previous ≤ 0 or either None, else signed one-decimal pct.
- `revenue_earnings_figure(years, revenue, earnings, theme) -> Figure` — two lines + markers (Revenue `theme["accent"]`, Earnings `#5b6cff`), `text=yoy_labels(...)`, `textposition="top center"`, readable $ ticks (shared helper), height 340, transparent, legend top-left.
- `cagr_table_html(fund) -> str` — small table (Revenue, Earnings × 3Y/5Y/10Y) using `overview_metrics`' CAGR at the fiscal year (net_income for Earnings), `—` when not computable; same borderless style as the Overview growth table.
- `questions_section_html(content, theme) -> str` — `section_html("Growth questions", question_cards.grid_html(GROWTH, cards, theme))`; invalid/missing → notice.
- [ ] Tests for each (score tones/labels, consensus None line, notice text, yoy labels incl. negatives/None, figure traces + text, cagr table dashes, escaping/no raw `$`/one-line style/starts with `<`). Implement, run, ruff, commit `Growth: page HTML, chart, CAGR table`.

### Task 3: Wire the tab

**Files:** `streamlit_app.py` (tab tuple: `"Growth"` after `"Moat"`, variable `_tab_growth`; block after the Moat block; CSS `.st-key-qc_growth_chart` identical to `.st-key-qc_phase_chart`), tab-list asserts in `tests/test_overview_page.py`, `tests/test_business_cards.py`, `tests/test_moat_cards.py`, `tests/test_risk_cards.py`, `tests/test_phase_tab_wiring.py`; create `tests/test_growth_tab_wiring.py`.

- [ ] Block: `_gcontent = notes.get(growth_cards.TITLE)` (same ai_notes dict pattern); `growth_page.growth_section_html(_gcontent, T)` in try/except → caption "Growth analysis unavailable right now."; `with st.container(key="qc_growth_chart")`: qc-label "Revenue & earnings", segmented_control `("5Y","10Y")` key `f"gr_range_{ticker}"` (None → "5Y"), figure + `cagr_table_html(fund)` below it; exception → "No revenue history available."; then `questions_section_html(_gcontent, T)` in try/except. Use the hoisted `fund`.
- [ ] Update tab asserts; wiring tests (tab list, keys, CSS rule equality, calls present). Full suite once, ruff, AST parse. Commit `Growth tab: wire into the ticker page`.

## Rollout (controller)
Guarded SQL insert of `growth_cards.PROMPT` after "Company Profile"; merge; push; MCP deploy; fill VEEV's Growth Cards; ask the user to Reboot.
