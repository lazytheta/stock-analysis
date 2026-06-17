# Detail-page Dividend Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new `Dividend` tab to the per-ticker detail page in `streamlit_app.py:_dcf_editor`, mirroring the Reverse DCF tab structure but driven by the multi-lens Dividend output (DDM + Yield Mean-Reversion).

**Architecture:** Three pure helper functions (`_ddm_at`, `_dividend_conclusion`, `_render_dividend_sensitivity_matrix`) are unit-tested in isolation; the tab body weaves them into Streamlit widgets. No changes to `compute_dividend_lens` — the tab reads from the stored `valuation_summary` blob and supports interactive sensitivity exploration via an adjust-ranges expander.

**Tech Stack:** Streamlit, pure-Python helpers, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-11-detail-page-dividend-tab-design.md`

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `streamlit_app.py` | Per-ticker detail page (`_dcf_editor`) | Add 3 helper functions (~120 LOC) + new tab body (~150 LOC). Extend `st.tabs(...)` call. |
| `tests/test_dividend_tab.py` | Unit tests for the 3 helper functions | NEW — ~10-15 tests |

5 tasks, single feature branch `feature/dividend-tab`. No DB, no MCP, no Cloud Run changes.

---

## Task 1: `_ddm_at` helper

**Why first:** the matrix renderer (Task 3) calls this per cell. Pure math, no Streamlit, easy to test.

**Files:**
- Modify: `streamlit_app.py` (add helper near other render-style helpers, around lines 200-300)
- Create: `tests/test_dividend_tab.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dividend_tab.py`:

```python
"""Tests for the per-ticker Dividend tab helpers in streamlit_app."""

import math

import pytest


def test_ddm_at_basic_math():
    """Two-stage DDM: 5y explicit growth + Gordon terminal, discounted at ke.
    
    Hand-computed for ttm=$4.00, g=0.06, ke=0.08, g_term=0.025, stage1_years=5:
      Year 1: D=4.24, PV=4.24/1.08 = 3.926
      Year 2: D=4.4944, PV=4.4944/1.08^2 = 3.852
      Year 3: D=4.7641, PV=4.7641/1.08^3 = 3.780
      Year 4: D=5.0499, PV=5.0499/1.08^4 = 3.708
      Year 5: D=5.3529, PV=5.3529/1.08^5 = 3.640
      Stage 1 PV ≈ 18.906
      Terminal: D5 * 1.025 / (0.08 - 0.025) = 5.486/0.055 = 99.751
      PV(Terminal) = 99.751 / 1.08^5 ≈ 67.872
      DDM FV ≈ 86.78
    """
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.06, ke=0.08, g_term=0.025, stage1_years=5
    )
    assert fv == pytest.approx(86.78, abs=0.5)


def test_ddm_at_returns_inf_when_ke_le_g_term():
    """ke ≤ g_term → Gordon perpetuity blows up; return inf so the matrix
    can render '—' for these cells."""
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.06, ke=0.020, g_term=0.025, stage1_years=5
    )
    assert math.isinf(fv)


def test_ddm_at_zero_growth_still_computes():
    """g=0 is mathematically valid (perpetuity at current dividend); should
    NOT blow up, just give a smaller FV."""
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.0, ke=0.08, g_term=0.025, stage1_years=5
    )
    assert fv > 0
    assert math.isfinite(fv)


def test_ddm_at_high_growth_above_lens_cap():
    """The matrix is exploratory — user can widen the slider beyond the
    lens's 15% cap. _ddm_at must compute regardless (no cap)."""
    import streamlit_app
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.20, ke=0.10, g_term=0.025, stage1_years=5
    )
    assert fv > 0
    assert math.isfinite(fv)


def test_ddm_at_matches_compute_dividend_lens_baseline():
    """At the baseline (g=cfg.dividend_5y_cagr, ke=compute_cost_of_equity),
    _ddm_at must equal compute_dividend_lens's stored ddm_fv."""
    import streamlit_app
    import valuation_lenses
    import dcf_calculator

    cfg = {
        "stock_price": 100.0,
        "equity_market_value": 1000, "debt_market_value": 100,
        "sector_betas": [("Sector", 1.0, 1.0)],
        "tax_rate": 0.21, "risk_free_rate": 0.04, "erp": 0.05,
        "credit_spread": 0.01, "terminal_growth": 0.025,
        "valuation_inputs": {
            "ttm_dividend": 4.00,
            "dividend_5y_cagr": 0.06,
            "median_5y_yield": 0.030,
        },
    }
    lens = valuation_lenses.compute_dividend_lens(cfg)
    assert lens is not None
    expected_ddm_fv = lens["details"]["ddm_fv"]

    ke = dcf_calculator.compute_cost_of_equity(cfg)
    fv = streamlit_app._ddm_at(
        ttm=4.00, g=0.06, ke=ke, g_term=0.025, stage1_years=5
    )
    assert fv == pytest.approx(expected_ddm_fv, abs=0.01)
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_dividend_tab.py -v`
Expected: FAIL — `AttributeError: module 'streamlit_app' has no attribute '_ddm_at'`

- [ ] **Step 3: Implement `_ddm_at`**

In `streamlit_app.py`, find a sensible location near the other small render helpers (e.g. right after `_render_football_field`, around line 300-310). Add:

```python
def _ddm_at(ttm: float, g: float, ke: float, g_term: float,
            stage1_years: int = 5) -> float:
    """Two-stage DDM valuation at explicit assumptions.

    Computes PV of stage-1 dividends (D₀ × (1+g)ⁿ discounted at ke for
    n=1..stage1_years) plus PV of Gordon terminal value at end of stage 1.

    Returns float("inf") when ke ≤ g_term (Gordon doesn't converge) so
    callers can render the cell as "—" without raising. No growth cap —
    the lens's 15% cap is upstream; the matrix is exploratory.
    """
    if ke <= g_term:
        return float("inf")

    pv_stage1 = 0.0
    d = ttm
    for n in range(1, stage1_years + 1):
        d = d * (1 + g)
        pv_stage1 += d / ((1 + ke) ** n)

    terminal_value = d * (1 + g_term) / (ke - g_term)
    pv_terminal = terminal_value / ((1 + ke) ** stage1_years)
    return pv_stage1 + pv_terminal
```

- [ ] **Step 4: Run the targeted tests**

Run: `python3 -m pytest tests/test_dividend_tab.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run the broader test suite to confirm no regressions**

Run: `python3 -m pytest tests/test_multi_lens.py tests/test_watchlist_ui.py -v`
Expected: All previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git checkout -b feature/dividend-tab
git add streamlit_app.py tests/test_dividend_tab.py
git commit -m "$(cat <<'EOF'
feat(streamlit): add _ddm_at helper for Dividend tab sensitivity matrix

Pure DDM math at explicit assumptions: 5y explicit growth + Gordon
terminal, discounted at cost of equity. Returns inf when ke ≤ g_term
so the matrix renders "—" for degenerate cells. No growth cap — the
lens's 15% cap is upstream; the matrix is exploratory.

5 tests covering basic math, ke ≤ g_term, zero growth, high growth,
and baseline equivalence with compute_dividend_lens.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_dividend_conclusion` helper

**Files:**
- Modify: `streamlit_app.py` (add helper next to `_ddm_at`)
- Modify: `tests/test_dividend_tab.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dividend_tab.py`:

```python
def test_dividend_conclusion_undervalued():
    """lens_mid >= price × 1.10 → undervaluation wording."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=181.0, price=155.0)
    assert "$181" in s
    assert "$155" in s
    assert "undervaluation" in s.lower()
    # Percent shown with one decimal (16.8% above)
    assert "%" in s


def test_dividend_conclusion_overvalued():
    """lens_mid <= price × 0.90 → overvaluation wording."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=181.0, price=220.0)
    assert "$181" in s
    assert "$220" in s
    assert "overvaluation" in s.lower()


def test_dividend_conclusion_fairly_priced():
    """0.90 × price ≤ lens_mid ≤ 1.10 × price → fairly priced wording."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=181.0, price=182.0)
    assert "$181" in s
    assert "$182" in s
    assert "fairly priced" in s.lower()


def test_dividend_conclusion_boundary_at_10pct_above():
    """Exactly +10% → still within fairly-priced band (inclusive)."""
    import streamlit_app
    # 110.0 / 100.0 = 1.10 exactly
    s = streamlit_app._dividend_conclusion(lens_mid=110.0, price=100.0)
    assert "fairly priced" in s.lower()


def test_dividend_conclusion_just_above_10pct():
    """Just past +10% → undervaluation."""
    import streamlit_app
    s = streamlit_app._dividend_conclusion(lens_mid=110.01, price=100.0)
    assert "undervaluation" in s.lower()


def test_dividend_conclusion_threshold_constant_is_10pct():
    """The threshold lives as a module-level constant for tunability."""
    import streamlit_app
    assert streamlit_app._DIVIDEND_FAIR_THRESHOLD == 0.10
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_dividend_tab.py -k "dividend_conclusion or DIVIDEND_FAIR" -v`
Expected: FAIL — `AttributeError: module 'streamlit_app' has no attribute '_dividend_conclusion'`.

- [ ] **Step 3: Implement `_dividend_conclusion`**

In `streamlit_app.py`, immediately after `_ddm_at` (from Task 1), add:

```python
_DIVIDEND_FAIR_THRESHOLD = 0.10


def _dividend_conclusion(lens_mid: float, price: float) -> str:
    """Return one of three conclusion-sentence variants comparing the
    Dividend lens midpoint to the current stock price.

    Threshold: ±10% around price → "fairly priced". Above → undervaluation
    signal. Below → overvaluation signal.

    Returned string is plain text (no HTML); the caller wraps it for
    styling via st.markdown with unsafe_allow_html.
    """
    upper = price * (1 + _DIVIDEND_FAIR_THRESHOLD)
    lower = price * (1 - _DIVIDEND_FAIR_THRESHOLD)

    if lens_mid > upper:
        pct = (lens_mid / price - 1) * 100
        return (
            f"Lens midpoint ${lens_mid:.0f} is {pct:.1f}% above current "
            f"${price:.0f} — potential undervaluation signal."
        )
    if lens_mid < lower:
        pct = (1 - lens_mid / price) * 100
        return (
            f"Lens midpoint ${lens_mid:.0f} is {pct:.1f}% below current "
            f"${price:.0f} — overvaluation signal."
        )
    return (
        f"Lens midpoint ${lens_mid:.0f} ≈ current ${price:.0f} — "
        f"fairly priced."
    )
```

- [ ] **Step 4: Run the targeted tests**

Run: `python3 -m pytest tests/test_dividend_tab.py -k "dividend_conclusion or DIVIDEND_FAIR" -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Run the full test_dividend_tab.py**

Run: `python3 -m pytest tests/test_dividend_tab.py -v`
Expected: 11 total tests PASS (5 from Task 1 + 6 new).

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py tests/test_dividend_tab.py
git commit -m "$(cat <<'EOF'
feat(streamlit): add _dividend_conclusion helper for Dividend tab

Returns one of three wording variants comparing the Dividend lens
midpoint to the current stock price:
- lens_mid > price × 1.10 → undervaluation signal
- lens_mid < price × 0.90 → overvaluation signal
- otherwise → fairly priced

Threshold lives as _DIVIDEND_FAIR_THRESHOLD = 0.10 for tunability.
6 tests covering all three branches plus exact ±10% boundary.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `_render_dividend_sensitivity_matrix` helper

**Why:** the most visual piece. Pure HTML string output, no Streamlit dependency. Uses `_ddm_at` from Task 1.

**Files:**
- Modify: `streamlit_app.py` (add helper next to `_ddm_at` and `_dividend_conclusion`)
- Modify: `tests/test_dividend_tab.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dividend_tab.py`:

```python
def _matrix_theme():
    """Minimal theme dict for matrix tests."""
    return {
        "border_medium": "#ccc",
        "card": "#fafafa",
        "text": "#111",
        "text_muted": "#666",
        "accent": "#6e8a76",
    }


def test_render_dividend_sensitivity_matrix_dimensions():
    """g_range has 5 steps, ke_range has 3 steps → matrix has 5 rows + 3 data cols."""
    import streamlit_app
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.08, 0.01),   # 0.04, 0.05, 0.06, 0.07, 0.08 → 5 rows
        ke_range=(0.07, 0.09, 0.01),  # 0.07, 0.08, 0.09             → 3 cols
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.06,
        baseline_ke=0.08,
        theme=_matrix_theme(),
    )
    # 5 data rows (each starts with <tr> after thead's <tr>)
    assert html.count("<tr>") == 6  # 1 header + 5 data rows
    # 5 rows × 3 data cols = 15 data <td>; plus 5 row-header <td> = 20 total
    # (header row uses <th>)
    assert html.count("<td") >= 20


def test_render_dividend_sensitivity_matrix_baseline_highlighted():
    """The cell at (baseline_g, baseline_ke) gets a highlight class/style."""
    import streamlit_app
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.08, 0.01),
        ke_range=(0.07, 0.09, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.06,
        baseline_ke=0.08,
        theme=_matrix_theme(),
    )
    # Highlighted cell carries the accent color in its inline style + bold
    # weight. We don't pin exact bytes — just verify both signals appear in
    # a single <td> tag.
    import re
    cells = re.findall(r"<td[^>]*>.*?</td>", html, flags=re.DOTALL)
    highlighted = [
        c for c in cells
        if "#6e8a76" in c and ("bold" in c or "font-weight:700" in c)
    ]
    assert len(highlighted) == 1


def test_render_dividend_sensitivity_matrix_degenerate_cell_renders_dash():
    """Cells where ke ≤ g_term render '—' (Gordon doesn't converge)."""
    import streamlit_app
    # ke range includes values below g_term=0.025 → those cells get "—"
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.06, 0.01),
        ke_range=(0.01, 0.04, 0.01),  # includes 0.01, 0.02, 0.03 (≤ g_term)
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.05,
        baseline_ke=0.03,
        theme=_matrix_theme(),
    )
    # At least one "—" should appear (for ke=0.01 and ke=0.02)
    assert "—" in html


def test_render_dividend_sensitivity_matrix_uses_theme_colors():
    """Theme dict's colors flow into the rendered HTML (not hardcoded)."""
    import streamlit_app
    theme = {
        "border_medium": "#aabbcc",
        "card": "#112233",
        "text": "#ffffff",
        "text_muted": "#888888",
        "accent": "#ff00aa",
    }
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.04, 0.06, 0.01),
        ke_range=(0.07, 0.09, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.05,
        baseline_ke=0.08,
        theme=theme,
    )
    # Each themed color should appear at least once in the HTML
    assert "#aabbcc" in html  # border
    assert "#112233" in html  # card bg
    assert "#888888" in html  # muted text
    assert "#ff00aa" in html  # accent on highlighted cell


def test_render_dividend_sensitivity_matrix_cell_values_match_ddm_at():
    """Each cell's $ value is _ddm_at at that (g, ke). Sanity-check one cell."""
    import streamlit_app
    html = streamlit_app._render_dividend_sensitivity_matrix(
        ttm=4.0,
        g_range=(0.05, 0.06, 0.01),
        ke_range=(0.08, 0.09, 0.01),
        g_term=0.025,
        stage1_years=5,
        baseline_g=0.05,
        baseline_ke=0.08,
        theme=_matrix_theme(),
    )
    # Compute the expected value for one cell (g=0.06, ke=0.08)
    expected = streamlit_app._ddm_at(
        ttm=4.0, g=0.06, ke=0.08, g_term=0.025, stage1_years=5
    )
    # The dollar-formatted version (matches _fmt_fv_dollar: int if >=100, 2dp else)
    expected_str = (
        f"${expected:.0f}" if abs(expected) >= 100 else f"${expected:.2f}"
    )
    assert expected_str in html
```

- [ ] **Step 2: Run the failing tests**

Run: `python3 -m pytest tests/test_dividend_tab.py -k "sensitivity_matrix" -v`
Expected: FAIL — `AttributeError: module 'streamlit_app' has no attribute '_render_dividend_sensitivity_matrix'`.

- [ ] **Step 3: Implement `_render_dividend_sensitivity_matrix`**

In `streamlit_app.py`, immediately after `_dividend_conclusion`, add:

```python
def _render_dividend_sensitivity_matrix(
    ttm: float,
    g_range: tuple[float, float, float],
    ke_range: tuple[float, float, float],
    g_term: float,
    stage1_years: int,
    baseline_g: float,
    baseline_ke: float,
    theme: dict,
) -> str:
    """Render a DDM sensitivity matrix as an HTML <table>.

    Rows = growth (g₁), columns = cost of equity (ke), cells = DDM FV.
    Cells where ke ≤ g_term render "—". The cell closest to
    (baseline_g, baseline_ke) gets a highlighted border + bold weight.

    Pure function: returns HTML string. Caller wraps in st.markdown with
    unsafe_allow_html=True. Theme dict provides border/card/text/accent colors.
    """
    g_min, g_max, g_step = g_range
    ke_min, ke_max, ke_step = ke_range

    # Generate axis lists (use small epsilon to include the max)
    def _arange(lo, hi, step):
        out = []
        v = lo
        # Allow tiny float drift past hi
        while v <= hi + step * 0.5:
            out.append(round(v, 6))
            v += step
        return out

    g_values = _arange(g_min, g_max, g_step)
    ke_values = _arange(ke_min, ke_max, ke_step)

    # Find the cell closest to (baseline_g, baseline_ke) — used for highlight.
    closest_g = min(g_values, key=lambda v: abs(v - baseline_g))
    closest_ke = min(ke_values, key=lambda v: abs(v - baseline_ke))

    hdr_style = (
        f"background:{theme['card']};color:{theme['text_muted']};"
        f"font-size:0.7rem;font-weight:600;padding:6px 8px;"
        f"text-align:center;position:sticky;top:0;z-index:1"
    )
    row_hdr_style = (
        f"background:{theme['card']};color:{theme['text']};"
        f"font-size:0.75rem;font-weight:600;padding:6px 8px;"
        f"text-align:left;position:sticky;left:0;z-index:1"
    )

    html = (
        f'<div style="overflow-x:auto;border:1px solid {theme["border_medium"]};'
        f'border-radius:12px;background:{theme["card"]}">'
        f'<table style="border-collapse:collapse;width:100%;font-size:0.75rem">'
    )

    # Header row
    html += f'<thead><tr><th style="{hdr_style};text-align:left">g \\\\ ke</th>'
    for ke in ke_values:
        html += f'<th style="{hdr_style}">{ke:.2%}</th>'
    html += "</tr></thead><tbody>"

    # Data rows
    for g in g_values:
        html += f'<tr><td style="{row_hdr_style}">{g:.1%}</td>'
        for ke in ke_values:
            fv = _ddm_at(ttm=ttm, g=g, ke=ke, g_term=g_term,
                         stage1_years=stage1_years)
            if fv == float("inf"):
                cell_text = "—"
                cell_style = (
                    f"padding:6px 8px;text-align:right;"
                    f"color:{theme['text_muted']};"
                )
            else:
                cell_text = _fmt_fv_dollar(fv)
                is_baseline = (g == closest_g and ke == closest_ke)
                if is_baseline:
                    cell_style = (
                        f"padding:6px 8px;text-align:right;"
                        f"color:{theme['accent']};font-weight:700;"
                        f"border:2px solid {theme['accent']};"
                    )
                else:
                    cell_style = (
                        f"padding:6px 8px;text-align:right;"
                        f"color:{theme['text']};"
                    )
            html += f'<td style="{cell_style}">{cell_text}</td>'
        html += "</tr>"

    html += "</tbody></table></div>"
    return html
```

Note: the test for `cell_values_match_ddm_at` uses the project's existing
`_fmt_fv_dollar` helper. That function already exists in `streamlit_app.py`
(it's used by `_render_fv_cell`). No need to re-implement.

- [ ] **Step 4: Run the targeted tests**

Run: `python3 -m pytest tests/test_dividend_tab.py -k "sensitivity_matrix" -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Run the full test_dividend_tab.py + watchlist UI to confirm no regressions**

Run: `python3 -m pytest tests/test_dividend_tab.py tests/test_watchlist_ui.py -v`
Expected: 16 tests in test_dividend_tab.py PASS; all watchlist_ui tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py tests/test_dividend_tab.py
git commit -m "$(cat <<'EOF'
feat(streamlit): add _render_dividend_sensitivity_matrix helper

Pure HTML-string renderer for the Dividend tab's sensitivity matrix.
Rows = growth (g₁), columns = cost of equity (ke), cells = DDM FV
(via _ddm_at). Degenerate cells where ke ≤ g_term render "—". The
cell at the baseline assumptions is highlighted with accent border +
bold weight.

Theme dict drives all colors so dark mode works. 5 tests covering
dimensions, baseline highlight, degenerate cells, theme propagation,
and cell-value equivalence with _ddm_at.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire the Dividend tab into `_dcf_editor`

**Why:** Tasks 1-3 built the helpers. This task adds the actual `_tab_dividend:` block to the per-ticker editor and threads all four edge cases through.

**Files:**
- Modify: `streamlit_app.py` (extend `st.tabs(...)` call at line 4176; add tab body block)

- [ ] **Step 1: Extend the `st.tabs(...)` call**

In `streamlit_app.py` at line 4176, the current call is:

```python
_tab_notes, _tab_dcf, _tab_rdcf, _tab_peers, _tab_fundamentals = st.tabs(
    ["Pre-Scan", "DCF", "Reverse DCF", "Peer Comparison", "Fundamentals"]
)
```

Replace with:

```python
_tab_notes, _tab_dcf, _tab_rdcf, _tab_peers, _tab_dividend, _tab_fundamentals = st.tabs(
    ["Pre-Scan", "DCF", "Reverse DCF", "Peer Comparison", "Dividend", "Fundamentals"]
)
```

- [ ] **Step 2: Locate the insertion point for the tab body**

The new `_tab_dividend:` block must come BEFORE the `_tab_fundamentals:` block (and ideally AFTER the `_tab_peers:` block) so the tab body order matches the tab declaration order.

Find the line in `streamlit_app.py` that starts the Fundamentals tab body:

```bash
grep -n "with _tab_fundamentals:" streamlit_app.py
```

Insert the new `_tab_dividend:` block immediately above that line.

- [ ] **Step 3: Add the tab body**

Insert this block above `with _tab_fundamentals:`:

```python
    with _tab_dividend:
        st.markdown("#### Dividend Lens")

        # Locate the lens output in the stored summary.
        _summary = cfg.get("valuation_summary") or {}
        _lenses = _summary.get("lenses") or {}
        _div_lens = _lenses.get("dividend")
        _inputs = cfg.get("valuation_inputs") or {}
        _ttm = _inputs.get("ttm_dividend") or 0.0
        _price = cfg.get("stock_price") or 0.0

        # ── Edge case: no stored summary at all ────────────────────
        if not _summary:
            st.info(
                "Run **Refresh All** on the watchlist (or call "
                "`calculate_multi_lens_valuation` via the MCP) to compute "
                "the Dividend lens for this ticker first."
            )

        # ── Edge case: non-payer (lens skipped due to ttm=0) ───────
        elif _ttm <= 0:
            st.info(
                f"**{ticker}** doesn't pay dividends — Dividend lens not "
                f"applicable. Use the `update_valuation_inputs` MCP tool "
                f"to inject a target dividend if you want scenario analysis."
            )

        # ── Edge case: lens computed but skipped (e.g. <3y history) ─
        elif _div_lens is None:
            st.warning(
                f"Dividend lens skipped for {ticker}. Likely reason: "
                f"insufficient dividend history (need ≥3y) or "
                f"`cost_of_equity ≤ terminal_growth`. Re-run Refresh All "
                f"after adjusting inputs."
            )

        else:
            # ── Read lens details ───────────────────────────────────
            _details = _div_lens.get("details") or {}
            _baseline_g = _details.get("growth_rate_stage1") or 0.0
            _baseline_ke = _details.get("cost_of_equity") or 0.0
            _g_term_used = _details.get("terminal_growth") or 0.025
            _stage1_years = _details.get("stage1_years") or 5
            _ddm_fv = _details.get("ddm_fv") or 0.0
            _yield_mr_fv = _details.get("yield_mr_fv")
            _median_yield = _details.get("median_5y_yield")

            # ── Edge case: Gordon blow-up (ke <= g_term) ────────────
            if _baseline_ke <= _g_term_used:
                st.warning(
                    f"Cost of equity ({_baseline_ke:.2%}) ≤ terminal "
                    f"growth ({_g_term_used:.2%}) — DDM formula doesn't "
                    f"converge for these assumptions. Adjust the DCF "
                    f"editor's risk-free rate, ERP, or terminal growth."
                )
            else:
                # ── Adjust ranges expander ──────────────────────────
                _div_g_range = (
                    0.0, 0.12, 0.01,
                )  # default 0% → 12% step 1%
                _div_ke_range = (
                    max(0.0, _baseline_ke - 0.02),
                    _baseline_ke + 0.02,
                    0.005,
                )  # default ke ±2% step 0.5%

                with st.expander("Adjust ranges"):
                    _dc1, _dc2 = st.columns(2)
                    with _dc1:
                        st.markdown("**Growth rate (g₁)**")
                        _dg_min = st.number_input(
                            "Min %", value=0.0,
                            step=1.0, format="%.0f",
                            key="div_gmin",
                        ) / 100
                        _dg_max = st.number_input(
                            "Max %", value=12.0,
                            step=1.0, format="%.0f",
                            key="div_gmax",
                        ) / 100
                        _dg_step = st.number_input(
                            "Step %", value=1.0,
                            step=0.5, format="%.1f",
                            key="div_gstep",
                        ) / 100
                        if _dg_step > 0 and _dg_max > _dg_min:
                            _div_g_range = (_dg_min, _dg_max, _dg_step)
                    with _dc2:
                        st.markdown("**Cost of equity (ke)**")
                        _dke_min = st.number_input(
                            "Min %", value=max(0.0, _baseline_ke * 100 - 2),
                            step=0.5, format="%.1f",
                            key="div_kemin",
                        ) / 100
                        _dke_max = st.number_input(
                            "Max %", value=_baseline_ke * 100 + 2,
                            step=0.5, format="%.1f",
                            key="div_kemax",
                        ) / 100
                        _dke_step = st.number_input(
                            "Step %", value=0.5,
                            step=0.1, format="%.1f",
                            key="div_kestep",
                        ) / 100
                        if _dke_step > 0 and _dke_max > _dke_min:
                            _div_ke_range = (_dke_min, _dke_max, _dke_step)

                # ── Two FV cards side-by-side ───────────────────────
                _card_border = (
                    f'border-top:1px solid {T["border_medium"]};'
                    f'border-right:1px solid {T["border_medium"]};'
                    f'border-bottom:1px solid {T["border_medium"]};'
                    f'border-left:3px solid {T["accent"]}'
                )

                _dc1, _dc2 = st.columns(2)
                with _dc1:
                    st.markdown(
                        f'<div style="{_card_border};border-radius:12px;'
                        f'padding:20px;text-align:center;'
                        f'background:{T["card"]};box-shadow:{T["shadow"]}">'
                        f'<div style="color:{T["text_muted"]};font-size:0.75rem;'
                        f'text-transform:uppercase;letter-spacing:0.05em;'
                        f'font-weight:600">DDM Fair Value</div>'
                        f'<div style="font-size:1.8rem;font-weight:700;'
                        f'margin:8px 0;color:{T["text"]}">{_fmt_fv_dollar(_ddm_fv)}</div>'
                        f'<div style="color:{T["text_muted"]};font-size:0.85rem">'
                        f'{_baseline_g:.1%} growth · ke {_baseline_ke:.1%} · '
                        f'terminal {_g_term_used:.1%}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                with _dc2:
                    if _yield_mr_fv is not None and _median_yield is not None:
                        _y_card_body = (
                            f'<div style="font-size:1.8rem;font-weight:700;'
                            f'margin:8px 0;color:{T["text"]}">'
                            f'{_fmt_fv_dollar(_yield_mr_fv)}</div>'
                            f'<div style="color:{T["text_muted"]};'
                            f'font-size:0.85rem">'
                            f'${_ttm:.2f} TTM / '
                            f'{_median_yield:.2%} historic median yield</div>'
                        )
                    else:
                        _y_card_body = (
                            f'<div style="font-size:1.4rem;font-weight:700;'
                            f'margin:8px 0;color:{T["text_muted"]}">'
                            f'Insufficient history</div>'
                            f'<div style="color:{T["text_muted"]};'
                            f'font-size:0.85rem">Needs ≥3y of dividend data</div>'
                        )
                    st.markdown(
                        f'<div style="{_card_border};border-radius:12px;'
                        f'padding:20px;text-align:center;'
                        f'background:{T["card"]};box-shadow:{T["shadow"]}">'
                        f'<div style="color:{T["text_muted"]};font-size:0.75rem;'
                        f'text-transform:uppercase;letter-spacing:0.05em;'
                        f'font-weight:600">Yield Mean-Reversion</div>'
                        f'{_y_card_body}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                # ── Conclusion ──────────────────────────────────────
                if _yield_mr_fv is not None:
                    _lens_mid = (_ddm_fv + _yield_mr_fv) / 2.0
                else:
                    _lens_mid = _ddm_fv
                _conclusion = _dividend_conclusion(
                    lens_mid=_lens_mid, price=_price
                )
                st.markdown(
                    f'<div style="color:{T["text_muted"]};font-size:0.85rem;'
                    f'text-align:center;margin:12px 0 16px">{_conclusion}</div>',
                    unsafe_allow_html=True,
                )

                # ── Sensitivity matrix ──────────────────────────────
                st.markdown(
                    f"**Sensitivity Matrix** — TTM ${_ttm:.2f} · "
                    f"baseline g {_baseline_g:.1%} × ke {_baseline_ke:.2%}"
                )
                _matrix_html = _render_dividend_sensitivity_matrix(
                    ttm=_ttm,
                    g_range=_div_g_range,
                    ke_range=_div_ke_range,
                    g_term=_g_term_used,
                    stage1_years=_stage1_years,
                    baseline_g=_baseline_g,
                    baseline_ke=_baseline_ke,
                    theme=T,
                )
                st.markdown(_matrix_html, unsafe_allow_html=True)
```

- [ ] **Step 4: Run the test suite to confirm no regressions in the helpers**

Run: `python3 -m pytest tests/test_dividend_tab.py tests/test_multi_lens.py tests/test_watchlist_ui.py -v`
Expected: 16 dividend-tab tests + 51 multi-lens + 35 watchlist-ui tests all PASS.

- [ ] **Step 5: Local smoke — verify the Streamlit page renders without error**

```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m streamlit run streamlit_app.py --server.port 8501 --server.headless true &
sleep 8
# Hit the home page to trigger any module-load errors
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8501/
# Expected: 200
kill %1
```

If 500 or a Python traceback shows, fix the error and re-run. Common issues: import order (helpers must be defined BEFORE the line ~4176 where the tab block uses them; the helpers belong near the top of the file with the other `_render_*` helpers).

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py
git commit -m "$(cat <<'EOF'
feat(streamlit): add Dividend tab to per-ticker detail page

New 5th tab between Peer Comparison and Fundamentals. Reads the
dividend lens output from cfg["valuation_summary"]["lenses"]["dividend"]
and renders:
- Adjust ranges expander (growth + ke)
- Two FV cards side-by-side (DDM + Yield Mean-Reversion)
- Conclusion sentence (via _dividend_conclusion helper)
- Sensitivity matrix (growth × ke → DDM FV, via _render_dividend_sensitivity_matrix)

Handles 4 edge cases: no summary stored (info banner), non-payer
(info banner), lens skipped for non-ttm reasons (warning banner),
Gordon blow-up at baseline (warning banner).

Pure Streamlit work — no changes to compute_dividend_lens, MCP, or
Cloud Run. The 3 helpers were unit-tested in earlier commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Final verification — ruff + pytest + manual smoke

**Files:** none new.

- [ ] **Step 1: Ruff lint on touched files**

Run: `python3 -m ruff check streamlit_app.py tests/test_dividend_tab.py`
Expected: same baseline (pre-existing 16 errors in streamlit_app.py — confirm no new errors introduced by this branch).

If new errors appear: fix them in a follow-up commit on the same branch before merging.

- [ ] **Step 2: Full test suite**

Run: `python3 -m pytest tests/ test_mcp_server.py -v`
Expected: all previously passing tests still pass + 16 new dividend-tab tests pass.

- [ ] **Step 3: Manual smoke on a dividend-paying ticker**

Push the branch to a deployed Streamlit Cloud (after merging to main) and verify:

1. Open lazytheta.io
2. Click the edit pencil on **PEP** or **MSFT** (known dividend payers)
3. Click the **Dividend** tab
4. Verify:
   - Both FV cards render with sensible $ values (not $0, not None)
   - Conclusion sentence renders below the cards
   - Sensitivity matrix renders with the baseline cell highlighted (accent border)
   - "Adjust ranges" expander opens and changing values re-renders the matrix

- [ ] **Step 4: Manual smoke on a non-payer**

1. Open the editor for **ABNB** (or another non-payer)
2. Click the **Dividend** tab
3. Verify: empty-state info banner appears, no cards or matrix shown

- [ ] **Step 5: Manual smoke on a recent dividend initiator (if available)**

If you have a ticker with <3y of dividend history (e.g. GOOG started 2024):
1. Click the **Dividend** tab
2. Verify: DDM card renders normally, Yield-MR card shows "Insufficient history", matrix still renders

If no such ticker is on the watchlist, this check can be skipped — the test
`test_render_dividend_sensitivity_matrix_*` covers the matrix correctness;
the empty-state HTML is straightforward.

- [ ] **Step 6: Merge to main + push**

Once the manual smoke passes:

```bash
git checkout main
git pull
git merge --ff-only feature/dividend-tab
git push origin main
git branch -d feature/dividend-tab
```

Streamlit Cloud auto-redeploys on push to main (~1-2 min). After redeploy,
the Dividend tab is live on lazytheta.io.

---

## Notes for the implementer

- **Helper functions go near the top of `streamlit_app.py`**, in the same general area as `_render_lens_dots`, `_render_fv_cell`, `_render_football_field` (around lines 110-300). They need to be defined before `_dcf_editor` (line 4102+) consumes them.
- **`_fmt_fv_dollar` is already defined** in `streamlit_app.py` — `_render_dividend_sensitivity_matrix` uses it for cell formatting.
- **`T` is the module-level theme dict** at `streamlit_app.py:2336` — accessible inside the tab body without import.
- **No Cloud Run redeploy needed** — this is pure Streamlit UI. Push to main → Streamlit Cloud auto-redeploys.
- **No DB migration** — the tab reads from existing `valuation_summary` blobs; no schema changes.
- **The "no summary" branch is the most likely edge case** for users who added a ticker recently. Be friendly in the banner copy.
- **Manual smoke is the validation** for the tab body itself (Task 4 step 5 + Task 5 steps 3-5). Streamlit-runtime mocking is too brittle to be worth automating for a UI tab.
