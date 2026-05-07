# Phase 2-D: Split Multiples Lens + Football Field UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the Multiples lens into peer-only `multiples` + own-history `historical`, update default weights, refactor lens-dots, add a `_render_football_field` popover UI, wire the trigger into the watchlist row.

**Architecture:** New `compute_historical_lens(cfg)` in `valuation_lenses.py` consumes the existing valuation_inputs (no auto-fetch changes). `compute_multiples_lens(cfg)` is stripped to peer-only sub-anchors. `DEFAULT_LENS_WEIGHTS` becomes 5 keys. `streamlit_app.py` gains a CSS-only `_render_football_field` helper rendering 4 horizontal range bars with current-price and weighted-mid markers; a 📊 button opens an `st.popover` with that HTML.

**Tech Stack:** Python 3.11, pytest, ruff, Streamlit (`st.popover` requires ≥1.32 — we run 1.54).

**Spec:** `docs/superpowers/specs/2026-05-07-phase2d-split-lens-football-field-design.md`

---

## File Map

| Path | Purpose | Action |
|------|---------|--------|
| `valuation_lenses.py` | Add `compute_historical_lens`, strip own-history sub-anchors from `compute_multiples_lens`, update `DEFAULT_LENS_WEIGHTS`, add historical lens to orchestrator | Modify |
| `streamlit_app.py` | Refactor `_render_lens_dots`, add `_render_football_field`, wire popover trigger into watchlist row | Modify |
| `tests/test_multi_lens.py` | New historical-lens tests, update tests that asserted old multiples-merged structure | Modify |
| `tests/test_watchlist_ui.py` | New football-field rendering tests, update lens-dots tests | Modify |

No new files. No schema changes outside the cfg JSONB.

---

### Task 1: `compute_historical_lens` — happy path

**Files:**
- Modify: `valuation_lenses.py` (add new function, locate position with `grep -n "^def compute_multiples_lens" valuation_lenses.py`)
- Modify: `tests/test_multi_lens.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_multi_lens.py`:

```python
def test_historical_lens_uses_all_three_subanchors():
    """All inputs present → 3 anchors collected (A + A.2 + D)."""
    cfg = make_cfg(
        valuation_inputs={
            "forward_eps": 5.0,
            "historical_fwd_pe": 20.0,             # → A: 100
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,                         # → A.2: 100
            "historical_ev_ebitda": 15.0,
            "ttm_ebitda": 10_000.0,                 # → D: depends on net_debt + shares
        },
    )
    # net_debt = 10_000 - 5_000 - 0 = 5_000
    # D fv = (15.0 * 10_000 - 5_000) / 1_000 = 145.0
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    assert lens["details"]["fwd_pe_own"] == pytest.approx(100.0)
    assert lens["details"]["historical_trailing_pe_fv"] == pytest.approx(100.0)
    assert lens["details"]["historical_ev_ebitda_fv"] == pytest.approx(145.0)
    # 3 anchors collected: 100, 100, 145
    assert lens["fv_low"] == pytest.approx(100.0)
    assert lens["fv_high"] == pytest.approx(145.0)
    # Mid is the mean: (100 + 100 + 145) / 3 = 115
    assert lens["fv_mid"] == pytest.approx(115.0, abs=0.5)
```

- [ ] **Step 2: Run test — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py::test_historical_lens_uses_all_three_subanchors -v`
Expected: `AttributeError: module 'valuation_lenses' has no attribute 'compute_historical_lens'`.

- [ ] **Step 3: Implement `compute_historical_lens`**

In `valuation_lenses.py`, locate `compute_multiples_lens` with `grep -n "^def compute_multiples_lens" valuation_lenses.py`. Add `compute_historical_lens` directly **before** it:

```python
def compute_historical_lens(cfg):
    """Time-series 'own history' lens. Three sub-anchors:

    A   own historical forward P/E × forward_eps        (manual: requires historical_fwd_pe)
    A.2 own historical trailing P/E × ttm_eps           (auto-fetched in Phase 2-B.2)
    D   own historical EV/EBITDA × ttm_ebitda - net_debt (auto-fetched in Phase 2-B.2)

    Sub-anchors silently skipped when their inputs are missing. Lens fully
    returns None when all three skip.
    """
    inputs = cfg.get("valuation_inputs") or {}

    forward_eps = inputs.get("forward_eps")
    historical_fwd_pe = inputs.get("historical_fwd_pe")
    historical_trailing_pe = inputs.get("historical_trailing_pe")
    historical_ev_ebitda = inputs.get("historical_ev_ebitda")
    ttm_eps = inputs.get("ttm_eps")
    ttm_ebitda = inputs.get("ttm_ebitda")

    fv_anchors = []
    details = {
        "fwd_pe_own": None,
        "historical_trailing_pe_fv": None,
        "historical_ev_ebitda_fv": None,
        "skipped": [],
    }

    # A) own forward P/E (manual)
    if forward_eps and historical_fwd_pe:
        own_fv = historical_fwd_pe * forward_eps
        fv_anchors.append(own_fv)
        details["fwd_pe_own"] = own_fv
    else:
        reason = "fwd_pe_own (forward_eps or historical_fwd_pe missing)"
        details["skipped"].append(reason)
        logger.info("Historical lens: skipping %s", reason)

    # A.2) own historical trailing P/E × ttm_eps
    if historical_trailing_pe and ttm_eps and ttm_eps > 0:
        own_trailing_fv = historical_trailing_pe * ttm_eps
        fv_anchors.append(own_trailing_fv)
        details["historical_trailing_pe_fv"] = own_trailing_fv
    else:
        reason = "historical_trailing_pe (no historical_trailing_pe or ttm_eps)"
        details["skipped"].append(reason)
        logger.info("Historical lens: skipping %s", reason)

    # D) own historical EV/EBITDA × ttm_ebitda - net_debt → /shares
    if historical_ev_ebitda and ttm_ebitda:
        net_debt = (
            cfg.get("debt_market_value", 0.0)
            - cfg.get("cash_bridge", 0.0)
            - cfg.get("securities", 0.0)
        )
        shares = cfg.get("shares_outstanding") or 1.0
        own_evebitda_fv = (historical_ev_ebitda * ttm_ebitda - net_debt) / shares
        fv_anchors.append(own_evebitda_fv)
        details["historical_ev_ebitda_fv"] = own_evebitda_fv
    else:
        reason = "historical_ev_ebitda (no historical_ev_ebitda or ttm_ebitda)"
        details["skipped"].append(reason)
        logger.info("Historical lens: skipping %s", reason)

    if not fv_anchors:
        logger.info("Historical lens fully skipped (no anchors)")
        return None

    return {
        "fv_low": min(fv_anchors),
        "fv_mid": sum(fv_anchors) / len(fv_anchors),
        "fv_high": max(fv_anchors),
        "details": details,
    }
```

- [ ] **Step 4: Run test — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py::test_historical_lens_uses_all_three_subanchors -v`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check valuation_lenses.py tests/test_multi_lens.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: compute_historical_lens with 3 own-history sub-anchors"
```

---

### Task 2: Historical lens edge cases

**Files:**
- Modify: `tests/test_multi_lens.py`

- [ ] **Step 1: Append failing tests**

```python
def test_historical_lens_returns_none_when_no_inputs():
    """Empty valuation_inputs → all three sub-anchors skip → lens returns None."""
    cfg = make_cfg()  # default has empty valuation_inputs
    assert valuation_lenses.compute_historical_lens(cfg) is None


def test_historical_lens_only_a2_active():
    """Only A.2 inputs present → lens returns single-anchor result."""
    cfg = make_cfg(
        valuation_inputs={"historical_trailing_pe": 30.0, "ttm_eps": 5.0},
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    assert lens["details"]["fwd_pe_own"] is None
    assert lens["details"]["historical_trailing_pe_fv"] == pytest.approx(150.0)
    assert lens["details"]["historical_ev_ebitda_fv"] is None
    # Single anchor → fv_low == fv_mid == fv_high
    assert lens["fv_low"] == lens["fv_mid"] == lens["fv_high"] == pytest.approx(150.0)


def test_historical_lens_only_d_active():
    """Only D (own EV/EBITDA) inputs present → lens returns single-anchor."""
    cfg = make_cfg(
        valuation_inputs={"historical_ev_ebitda": 20.0, "ttm_ebitda": 5_000.0},
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    # net_debt = 5_000, shares = 1_000 (defaults)
    # fv = (20.0 * 5_000 - 5_000) / 1_000 = 95.0
    assert lens["details"]["historical_ev_ebitda_fv"] == pytest.approx(95.0)
    assert lens["fv_mid"] == pytest.approx(95.0)
```

- [ ] **Step 2: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py -k historical_lens -v`
Expected: 4 passed (1 happy path from Task 1 + 3 edge cases).

- [ ] **Step 3: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check tests/test_multi_lens.py`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add tests/test_multi_lens.py
git commit -m "test: pin historical lens edge cases (skip / single-anchor)"
```

---

### Task 3: Strip own-history sub-anchors from `compute_multiples_lens`

**Files:**
- Modify: `valuation_lenses.py` — `compute_multiples_lens` body
- Modify: `tests/test_multi_lens.py` — update tests that asserted old multiples-merged structure

- [ ] **Step 1: Locate the existing function**

Run: `cd /Users/administrator/Documents/github/stock-analysis && grep -n "^def compute_multiples_lens" valuation_lenses.py`

Read the body and identify these blocks to remove:
- Variable extractions: `historical_fwd_pe`, `historical_trailing_pe`, `historical_ev_ebitda`, `ttm_eps` (keep `forward_eps` and `ttm_ebitda`)
- Sub-anchor A block (`# A) own historical forward P/E`)
- Sub-anchor A.2 block (`# A.2) own historical trailing P/E × ttm_eps`)
- Sub-anchor D block (`# D) own historical EV/EBITDA × ttm_ebitda - net_debt → /shares`)
- `details` keys to remove: `fwd_pe_own`, `historical_trailing_pe_fv`, `historical_ev_ebitda_fv`

Sub-anchors B (peer fwd P/E) and C (peer EV/EBITDA) STAY.

- [ ] **Step 2: Replace `compute_multiples_lens` with stripped version**

Replace the entire function body with:

```python
def compute_multiples_lens(cfg):
    """Peer-relative multiples lens. Two sub-anchors:

    B) peer-set forward P/E (median, min, max, Tukey-filtered) × forward_eps
    C) peer-set EV/EBITDA (median, min, max, Tukey-filtered) × ttm_ebitda - net_debt → /shares

    Own-history sub-anchors (A, A.2, D) live in compute_historical_lens.
    Sub-anchors silently skipped when their inputs are missing. Lens returns
    None when both peer sub-anchors skip.
    """
    inputs = cfg.get("valuation_inputs") or {}
    peers = cfg.get("peers") or []

    forward_eps = inputs.get("forward_eps")
    ttm_ebitda = inputs.get("ttm_ebitda")

    fv_anchors = []
    details = {
        "fwd_pe_peer_median": None,
        "ev_ebitda_peer_median": None,
        "closest_peer": None,
        "skipped": [],
    }

    # B) peer fwd P/E
    peer_fwd_pe_pairs = [(p["ticker"], p["fwd_pe"]) for p in peers if p.get("fwd_pe")]
    peer_fwd_pes_raw = [v for _, v in peer_fwd_pe_pairs]
    peer_fwd_pes, removed_idx = _tukey_filter(peer_fwd_pes_raw)
    details["peer_fwd_pe_outliers_removed"] = [peer_fwd_pe_pairs[i][0] for i in removed_idx]
    if peer_fwd_pes and forward_eps:
        median_pe = statistics.median(peer_fwd_pes)
        fv_low_p = min(peer_fwd_pes) * forward_eps
        fv_mid_p = median_pe * forward_eps
        fv_high_p = max(peer_fwd_pes) * forward_eps
        fv_anchors.extend([fv_low_p, fv_mid_p, fv_high_p])
        details["fwd_pe_peer_median"] = fv_mid_p
        avg_growth = sum(cfg.get("revenue_growth", [0.0])) / max(
            len(cfg.get("revenue_growth", [0.0])), 1
        )
        avg_margin = sum(cfg.get("op_margins", [0.0])) / max(
            len(cfg.get("op_margins", [0.0])), 1
        )
        details["closest_peer"] = _closest_peer_ticker(peers, avg_margin, avg_growth)
    else:
        reason = "fwd_pe_peer (no peers with fwd_pe or no forward_eps)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)

    # C) peer EV/EBITDA
    peer_ev_ebitda_pairs = [(p["ticker"], p["ev_ebitda"]) for p in peers if p.get("ev_ebitda")]
    peer_ev_ebitdas_raw = [v for _, v in peer_ev_ebitda_pairs]
    peer_ev_ebitdas, removed_idx_ev = _tukey_filter(peer_ev_ebitdas_raw)
    details["peer_ev_ebitda_outliers_removed"] = [peer_ev_ebitda_pairs[i][0] for i in removed_idx_ev]
    if peer_ev_ebitdas and ttm_ebitda:
        net_debt = (
            cfg.get("debt_market_value", 0.0)
            - cfg.get("cash_bridge", 0.0)
            - cfg.get("securities", 0.0)
        )
        shares = cfg.get("shares_outstanding") or 1.0
        median_ev = statistics.median(peer_ev_ebitdas)
        fv_low_e = (min(peer_ev_ebitdas) * ttm_ebitda - net_debt) / shares
        fv_mid_e = (median_ev * ttm_ebitda - net_debt) / shares
        fv_high_e = (max(peer_ev_ebitdas) * ttm_ebitda - net_debt) / shares
        fv_anchors.extend([fv_low_e, fv_mid_e, fv_high_e])
        details["ev_ebitda_peer_median"] = fv_mid_e
    else:
        reason = "ev_ebitda_peer (no peers with ev_ebitda or no ttm_ebitda)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)

    if not fv_anchors:
        logger.info("Multiples lens fully skipped (no peer anchors)")
        return None

    return {
        "fv_low": min(fv_anchors),
        "fv_mid": sum(fv_anchors) / len(fv_anchors),
        "fv_high": max(fv_anchors),
        "details": details,
    }
```

- [ ] **Step 3: Update existing tests that referenced old multiples-merged structure**

Find the affected tests:

```bash
cd /Users/administrator/Documents/github/stock-analysis
grep -n "test_multiples_lens_uses_historical_trailing_pe\|test_multiples_lens_uses_historical_ev_ebitda\|details\[.historical_trailing_pe_fv.\]\|details\[.historical_ev_ebitda_fv.\]\|details\[.fwd_pe_own.\]" tests/test_multi_lens.py
```

You should find these tests calling assertions on the OLD multiples lens details:

1. **`test_multiples_lens_uses_historical_trailing_pe`** — currently asserts `lens["details"]["historical_trailing_pe_fv"] == 100.0` for the multiples lens. RENAME the test to `test_historical_lens_uses_historical_trailing_pe` and change `compute_multiples_lens` → `compute_historical_lens` so the assertion now tests the new lens.

2. **`test_multiples_lens_uses_historical_ev_ebitda`** — same pattern. RENAME to `test_historical_lens_uses_historical_ev_ebitda` and change `compute_multiples_lens` → `compute_historical_lens`.

3. **`test_multiples_lens_returns_none_when_no_inputs`** — change assertion. With the split, an empty valuation_inputs no longer returns None for multiples (it skips both peer sub-anchors but only because peers are also empty). Update test to assert that with no peers AND no inputs, the multiples lens returns None (which it should because peer anchors require peers).

For test #1 and #2, after the rename, change the function-under-test to `compute_historical_lens` since those tests verify own-history sub-anchor behavior:

Use grep on each test name with `-A 30` to see the body, then edit. For #1:

```python
def test_historical_lens_uses_historical_trailing_pe():
    """Sub-anchor A.2 of historical lens: historical_trailing_pe × ttm_eps."""
    cfg = make_cfg(
        valuation_inputs={
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,
        },
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    assert lens["details"]["historical_trailing_pe_fv"] == pytest.approx(100.0)
    assert lens["fv_low"] == pytest.approx(100.0)
    assert lens["fv_mid"] == pytest.approx(100.0)
    assert lens["fv_high"] == pytest.approx(100.0)
```

For #2:

```python
def test_historical_lens_uses_historical_ev_ebitda():
    """Sub-anchor D of historical lens."""
    cfg = make_cfg(
        valuation_inputs={
            "historical_ev_ebitda": 15.0,
            "ttm_ebitda": 10_000.0,
        },
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    assert lens["details"]["historical_ev_ebitda_fv"] == pytest.approx(145.0)
```

For #3 (`test_multiples_lens_returns_none_when_no_inputs`) — keep the test name; with the new peer-only multiples lens, default `make_cfg()` has empty peers list so the lens returns None. The test should still pass without changes:

```python
def test_multiples_lens_returns_none_when_no_inputs():
    cfg = make_cfg()  # no valuation_inputs, empty peers
    assert valuation_lenses.compute_multiples_lens(cfg) is None
```

Verify by reading the existing test body — if it already looks like the above, no change needed.

- [ ] **Step 4: Run multi_lens tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py -v 2>&1 | tail -15`
Expected: all tests pass. Tests that previously asserted the old details should now exist under their renamed forms and pass.

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check valuation_lenses.py tests/test_multi_lens.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "refactor: strip own-history sub-anchors from compute_multiples_lens"
```

---

### Task 4: Wire `compute_historical_lens` into orchestrator + update `DEFAULT_LENS_WEIGHTS`

**Files:**
- Modify: `valuation_lenses.py`
- Modify: `tests/test_multi_lens.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_multi_lens.py`:

```python
def test_orchestrator_includes_historical_lens():
    """Full config produces a valuation_summary with 4 active lenses
    (dcf, multiples, historical, reverse_dcf), dividend stays None."""
    peers = [
        make_peer(ticker="P1", fwd_pe=18.0, ev_ebitda=10.0),
        make_peer(ticker="P2", fwd_pe=20.0, ev_ebitda=12.0),
        make_peer(ticker="P3", fwd_pe=22.0, ev_ebitda=14.0),
    ]
    cfg = make_cfg(
        peers=peers,
        valuation_inputs={
            **dict(SAMPLE_VALUATION_INPUTS),
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,
            "historical_ev_ebitda": 15.0,
        },
    )
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)

    lenses = summary["lenses"]
    assert lenses["dcf"] is not None
    assert lenses["multiples"] is not None
    assert lenses["historical"] is not None
    assert lenses["reverse_dcf"] is not None
    assert lenses["dividend"] is None  # Phase 2-C stub


def test_default_lens_weights_post_split():
    assert valuation_lenses.DEFAULT_LENS_WEIGHTS == {
        "dcf":         0.30,
        "multiples":   0.30,
        "historical":  0.30,
        "reverse_dcf": 0.10,
        "dividend":    0.00,
    }
```

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py -k "orchestrator_includes_historical_lens or default_lens_weights_post_split" -v`
Expected: FAIL on both — orchestrator doesn't call `compute_historical_lens`, weights still have old shape.

- [ ] **Step 3: Update `DEFAULT_LENS_WEIGHTS`**

Find the existing `DEFAULT_LENS_WEIGHTS = {...}` block in `valuation_lenses.py` (locate with `grep -n "DEFAULT_LENS_WEIGHTS" valuation_lenses.py`). Replace with:

```python
DEFAULT_LENS_WEIGHTS = {
    "dcf":         0.30,
    "multiples":   0.30,
    "historical":  0.30,
    "reverse_dcf": 0.10,
    "dividend":    0.00,
}
```

- [ ] **Step 4: Update `calculate_multi_lens_valuation` orchestrator**

Find the orchestrator with `grep -n "^def calculate_multi_lens_valuation" valuation_lenses.py`. The body starts with:

```python
    lenses = {
        "dcf":         compute_dcf_lens(cfg, scenario_grid=scenario_grid),
        "multiples":   compute_multiples_lens(cfg),
        "reverse_dcf": compute_reverse_dcf_lens(cfg),
        "dividend":    compute_dividend_lens(cfg),
    }
```

Replace with (insert `historical` between multiples and reverse_dcf):

```python
    lenses = {
        "dcf":         compute_dcf_lens(cfg, scenario_grid=scenario_grid),
        "multiples":   compute_multiples_lens(cfg),
        "historical":  compute_historical_lens(cfg),
        "reverse_dcf": compute_reverse_dcf_lens(cfg),
        "dividend":    compute_dividend_lens(cfg),
    }
```

The renormalization logic in the orchestrator already handles arbitrary lens counts via `active_names = [n for n, l in lenses.items() if l is not None]`. No further changes needed.

- [ ] **Step 5: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py -k "orchestrator_includes_historical_lens or default_lens_weights_post_split" -v`
Expected: 2 passed.

- [ ] **Step 6: Run full test_multi_lens.py**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_multi_lens.py -v 2>&1 | tail -15`
Expected: all pass. The pre-existing `test_all_lenses_active_weighted_in_range` from Phase 1 may fail because it expected 3 active lenses (was: dcf, multiples, reverse_dcf). It now expects 4 — verify the test still passes given the cfg uses both `valuation_inputs` and peers; if it fails because of the lens count assertion, update the assertion to expect 4 active.

If `test_all_lenses_active_weighted_in_range` fails:

Find with `grep -n "test_all_lenses_active_weighted_in_range" tests/test_multi_lens.py`. The test checks `active = [n for n in (...) if lenses[n] is not None]`. Update the tuple to include `"historical"`:

```python
    active = [n for n in ("dcf", "multiples", "historical", "reverse_dcf") if lenses[n] is not None]
    assert active == ["dcf", "multiples", "historical", "reverse_dcf"]
```

(Note: in this test the cfg has `valuation_inputs=dict(SAMPLE_VALUATION_INPUTS)` which does NOT include the historical fields. To make `historical` actually fire, extend the test's valuation_inputs OR adjust the assertion to allow historical=None. Simpler: just expand the inputs to include the historical fields in this test.)

If you need to expand the inputs:

```python
    cfg = make_cfg(
        peers=peers,
        valuation_inputs={
            **dict(SAMPLE_VALUATION_INPUTS),
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,
        },
    )
```

That makes `historical` active via sub-anchor A.2.

- [ ] **Step 7: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check valuation_lenses.py tests/test_multi_lens.py`
Expected: clean.

- [ ] **Step 8: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: orchestrator includes historical lens; default weights split 5-way"
```

---

### Task 5: Refactor `_render_lens_dots` to flexible "{N} lenses" label

**Files:**
- Modify: `streamlit_app.py` — `_render_lens_dots` (locate with `grep -n "^def _render_lens_dots" streamlit_app.py`)
- Modify: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_watchlist_ui.py`:

```python
def test_render_lens_dots_4_active_after_split():
    """After Phase 2-D, four lenses can be active. Label scales generically."""
    lenses = {
        "dcf": {}, "multiples": {}, "historical": {}, "reverse_dcf": {},
        "dividend": None,
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 4
    assert "4 lenses" in html


def test_render_lens_dots_zero_active():
    """No lenses active → 'no lenses' label, all dots grey."""
    lenses = {
        "dcf": None, "multiples": None, "historical": None, "reverse_dcf": None,
        "dividend": None,
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert "no lenses" in html


def test_render_lens_dots_omits_dividend_from_display():
    """Dividend stub never renders a dot, even if non-None.
    (Future Phase 2-C will revisit if dividend dot should appear.)"""
    lenses = {
        "dcf": {}, "multiples": None, "historical": None, "reverse_dcf": None,
        "dividend": {"fv_mid": 50.0},  # hypothetical: dividend with value
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    # Only DCF dot shown, dividend ignored from display
    assert html.count('class="ld-on"') == 1
    assert "1 lens" in html
```

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -k "lens_dots_4_active or lens_dots_zero_active or lens_dots_omits_dividend" -v`
Expected: FAIL — current `_render_lens_dots` has if-elif soup with explicit combinations and only iterates `["dcf", "multiples", "reverse_dcf"]`.

- [ ] **Step 3: Refactor `_render_lens_dots`**

Locate the existing function with `grep -n "^def _render_lens_dots" streamlit_app.py`. Read the full body (it's an if-elif soup with combinations like "DCF only", "DCF + reverse", etc.).

Replace the entire function with:

```python
def _render_lens_dots(lenses: dict, theme: dict) -> str:
    """Render N dots showing which lenses are active + a generic count label.

    Order: dcf · multiples · historical · reverse_dcf. Dividend stub is
    deliberately omitted from display (always greyed out under Phase 2 scope).

    Each lens key maps to a non-None lens dict (active, green dot) or None
    (skipped, grey dot). Label: "{N} lens" or "{N} lenses" or "no lenses".
    """
    order = ["dcf", "multiples", "historical", "reverse_dcf"]
    actives = [name for name in order if lenses.get(name) is not None]

    parts = []
    for name in order:
        cls = "ld-on" if lenses.get(name) is not None else "ld-off"
        parts.append(f'<span class="{cls}"></span>')

    n = len(actives)
    if n == 0:
        label = "no lenses"
    elif n == 1:
        label = "1 lens"
    else:
        label = f"{n} lenses"

    color = theme.get("text_muted", "#888")
    return (
        f'<div style="font-size:0.7rem;color:{color};margin-top:1px">'
        f'{"".join(parts)} {label}</div>'
    )
```

- [ ] **Step 4: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -k lens_dots -v 2>&1 | tail -10`
Expected: all lens-dots tests pass. Some pre-existing tests assert specific labels from the old if-elif soup ("DCF only", "DCF + reverse", etc.) — those will fail now because the new label is generic. Update them.

- [ ] **Step 5: Update pre-existing lens-dots tests**

Find tests that asserted old label strings:

```bash
cd /Users/administrator/Documents/github/stock-analysis
grep -n "DCF only\|DCF + reverse\|3 lenses" tests/test_watchlist_ui.py
```

For each occurrence:
- `assert "DCF only" in html` → `assert "1 lens" in html`
- `assert "DCF + reverse" in html` → `assert "2 lenses" in html`
- `assert "3 lenses" in html` → keep (still valid for 3 active out of 4)

Use `Read` + `Edit` for each test. There should be ~3-4 affected.

- [ ] **Step 6: Re-run watchlist UI tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -v 2>&1 | tail -15`
Expected: all pass.

- [ ] **Step 7: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check streamlit_app.py tests/test_watchlist_ui.py`
Expected: no NEW violations.

- [ ] **Step 8: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "refactor: _render_lens_dots uses generic '{N} lenses' label"
```

---

### Task 6: New `_render_football_field` helper

**Files:**
- Modify: `streamlit_app.py` — add helper near `_render_fv_cell`
- Modify: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_watchlist_ui.py`:

```python
def test_render_football_field_renders_all_active_lenses():
    """Full summary → HTML contains 4 bar elements + price/mid/buy markers."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 80.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 120.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":         {"fv_low": 90.0,  "fv_mid": 100.0, "fv_high": 110.0},
            "multiples":   {"fv_low": 70.0,  "fv_mid": 95.0,  "fv_high": 130.0},
            "historical":  {"fv_low": 95.0,  "fv_mid": 105.0, "fv_high": 115.0},
            "reverse_dcf": {"fv_low": 100.0, "fv_mid": 100.0, "fv_high": 100.0},
            "dividend":    None,
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    # 4 lens labels in the HTML
    assert "DCF" in html
    assert "Multiples" in html
    assert "Historical" in html
    assert "Reverse DCF" in html
    # Markers for current price, mid, buy
    assert "$100" in html or "100.00" in html  # current price
    # Class hooks for the bars
    assert html.count("ff-bar") >= 4


def test_render_football_field_handles_missing_lens():
    """Lens=None → bar greyed out with '(skipped)' label."""
    summary = {
        "stock_price": 100.0,
        "weighted_fv_low": 90.0,
        "weighted_fv_mid": 100.0,
        "weighted_fv_high": 110.0,
        "buy_price": 80.0,
        "lenses": {
            "dcf":         {"fv_low": 90.0, "fv_mid": 100.0, "fv_high": 110.0},
            "multiples":   None,
            "historical":  {"fv_low": 95.0, "fv_mid": 105.0, "fv_high": 115.0},
            "reverse_dcf": {"fv_low": 100.0, "fv_mid": 100.0, "fv_high": 100.0},
            "dividend":    None,
        },
    }
    html = streamlit_app._render_football_field(summary, theme=_theme_stub())
    assert "(skipped)" in html


def test_render_football_field_handles_no_summary():
    """Empty/None summary → returns a placeholder (no crash)."""
    assert streamlit_app._render_football_field(None, theme=_theme_stub()) != ""
    assert streamlit_app._render_football_field({}, theme=_theme_stub()) != ""
```

`_theme_stub` is already defined in `tests/test_watchlist_ui.py` from Phase 2-A.

- [ ] **Step 2: Run tests — should fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -k "football_field" -v`
Expected: FAIL — `AttributeError: module 'streamlit_app' has no attribute '_render_football_field'`.

- [ ] **Step 3: Implement `_render_football_field`**

In `streamlit_app.py`, locate `_render_fv_cell` (with `grep -n "^def _render_fv_cell" streamlit_app.py`). Add `_render_football_field` directly after it ends:

```python
def _render_football_field(summary: dict | None, theme: dict) -> str:
    """Render a football-field HTML block: one horizontal range bar per lens
    + vertical markers for current price, weighted mid, and buy price.

    Used inside an st.popover triggered from the watchlist row. Pure CSS;
    width fixes at ~600px so the popover sizes naturally.
    """
    text = theme.get("text", "#eee")
    muted = theme.get("text_muted", "#888")
    accent = theme.get("accent", "#6e8a76")
    accent_hover = theme.get("accent_hover", "#5a7561")

    if not summary or not isinstance(summary, dict) or not summary.get("lenses"):
        return (
            f'<div style="color:{muted};font-size:0.85rem;padding:12px">'
            f'No valuation summary available — run "Refresh all" or '
            f'<code>calculate_multi_lens_valuation</code>.'
            f'</div>'
        )

    lens_order = [
        ("dcf", "DCF"),
        ("multiples", "Multiples"),
        ("historical", "Historical"),
        ("reverse_dcf", "Reverse DCF"),
    ]
    lenses = summary.get("lenses") or {}

    # Compute the global x-axis range across all active lens fv_low/high + current price
    price = summary.get("stock_price") or 0.0
    mid = summary.get("weighted_fv_mid") or 0.0
    buy = summary.get("buy_price") or 0.0

    all_values = [price, mid, buy]
    for key, _ in lens_order:
        lens = lenses.get(key)
        if lens:
            all_values.extend([lens.get("fv_low") or 0, lens.get("fv_high") or 0])
    all_values = [v for v in all_values if v]
    if not all_values:
        return f'<div style="color:{muted};font-size:0.85rem">No valuation data.</div>'
    g_min, g_max = min(all_values), max(all_values)
    span = max(g_max - g_min, 1e-9)
    # 5% padding on each side
    pad = span * 0.05
    g_min -= pad
    g_max += pad
    span = g_max - g_min

    def _x(v: float) -> float:
        return ((v - g_min) / span) * 100.0

    # Build per-lens bars
    bar_rows = []
    for key, label in lens_order:
        lens = lenses.get(key)
        if lens is None:
            bar_rows.append(
                f'<div class="ff-row"><div class="ff-label">{label}</div>'
                f'<div class="ff-bar" style="background:#33333322"></div>'
                f'<div class="ff-range-label" style="color:{muted}">(skipped)</div>'
                f'</div>'
            )
            continue
        low = lens.get("fv_low") or 0
        mid_l = lens.get("fv_mid") or 0
        high = lens.get("fv_high") or 0
        x_low, x_high = _x(low), _x(high)
        width = max(x_high - x_low, 0.5)  # min 0.5% so single-anchor lens (low==high) still visible
        bar_rows.append(
            f'<div class="ff-row">'
            f'<div class="ff-label">{label}</div>'
            f'<div class="ff-bar">'
            f'<div class="ff-range" style="left:{x_low:.1f}%;width:{width:.1f}%"></div>'
            f'</div>'
            f'<div class="ff-range-label" style="color:{text}">${low:.0f} — ${high:.0f}</div>'
            f'</div>'
        )

    # Vertical markers (price, mid, buy) — drawn as absolute-positioned lines
    # spanning the full bars-area height
    markers_html = (
        f'<div class="ff-marker" style="left:{_x(price):.2f}%;background:#fff" '
        f'  title="Current price ${price:.2f}"></div>'
        f'<div class="ff-marker" style="left:{_x(mid):.2f}%;background:{accent}" '
        f'  title="Weighted Mid ${mid:.2f}"></div>'
        f'<div class="ff-marker" style="left:{_x(buy):.2f}%;background:{accent_hover}" '
        f'  title="Buy ${buy:.2f}"></div>'
    )

    # CSS lives inline at the top of the rendered block (popover is iframe-isolated
    # otherwise — but Streamlit's st.popover renders inline so global CSS works)
    css = f'''<style>
.ff-container {{ position:relative; width:100%; max-width:560px; padding:12px 4px; }}
.ff-row {{ display:flex; align-items:center; gap:10px; margin-bottom:6px; font-size:0.78rem; }}
.ff-label {{ width:88px; color:{text}; font-weight:500; }}
.ff-bar {{
  position:relative; flex:1; height:14px;
  background:linear-gradient(90deg,#6cc07033,#d8a44833,#d96a5a33);
  border-radius:3px; overflow:hidden;
}}
.ff-range {{
  position:absolute; top:0; bottom:0;
  background:linear-gradient(90deg,#6cc070,#d8a448,#d96a5a);
  border-radius:3px; opacity:0.85;
}}
.ff-range-label {{ width:120px; font-size:0.72rem; }}
.ff-markers {{
  position:absolute; top:36px; left:98px; right:130px; bottom:24px; pointer-events:none;
}}
.ff-marker {{
  position:absolute; top:0; bottom:0; width:2px;
  box-shadow:0 0 2px rgba(0,0,0,0.6);
}}
.ff-legend {{
  display:flex; gap:14px; padding-top:10px; font-size:0.72rem; color:{muted};
}}
.ff-legend-dot {{
  display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:4px;
}}
</style>'''

    legend_html = (
        f'<div class="ff-legend">'
        f'<span><span class="ff-legend-dot" style="background:#fff"></span>Price ${price:.2f}</span>'
        f'<span><span class="ff-legend-dot" style="background:{accent}"></span>Mid ${mid:.2f}</span>'
        f'<span><span class="ff-legend-dot" style="background:{accent_hover}"></span>Buy ${buy:.2f}</span>'
        f'</div>'
    )

    return (
        f'{css}'
        f'<div class="ff-container">'
        f'{"".join(bar_rows)}'
        f'<div class="ff-markers">{markers_html}</div>'
        f'{legend_html}'
        f'</div>'
    )
```

- [ ] **Step 4: Run tests — should pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -k football_field -v`
Expected: 3 passed.

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check streamlit_app.py tests/test_watchlist_ui.py`
Expected: no new violations.

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "feat: _render_football_field helper with per-lens bars + markers"
```

---

### Task 7: Wire popover trigger into watchlist row

**Files:**
- Modify: `streamlit_app.py` — `_render_wl_row` (locate with `grep -n "^def _render_wl_row" streamlit_app.py | head` — note that this might be defined inside `_watchlist_overview`)

- [ ] **Step 1: Locate `_render_wl_row`**

Run: `cd /Users/administrator/Documents/github/stock-analysis && grep -n "def _render_wl_row" streamlit_app.py`

Confirm with `Read` that the function exists. Find the section that renders the Fair Value cell (`cols[4]`) — this is where we'll add the popover trigger.

Look for:

```python
        cols[4].markdown(
            _render_fv_cell(...),
            unsafe_allow_html=True,
        )
```

- [ ] **Step 2: Replace the Fair Value cell render with popover-aware version**

Find the existing block:

```python
        cols[4].markdown(
            _render_fv_cell(
                price=row['price'],
                summary=row.get('valuation_summary'),
                legacy_intrinsic=row.get('intrinsic'),
                theme=T,
            ),
            unsafe_allow_html=True,
        )
```

Replace with:

```python
        with cols[4]:
            st.markdown(
                _render_fv_cell(
                    price=row['price'],
                    summary=row.get('valuation_summary'),
                    legacy_intrinsic=row.get('intrinsic'),
                    theme=T,
                ),
                unsafe_allow_html=True,
            )
            # Football-field popover trigger — only show when there's a summary
            if row.get('valuation_summary'):
                with st.popover("📊", use_container_width=False, help="Open valuation breakdown"):
                    st.markdown(
                        _render_football_field(row['valuation_summary'], theme=T),
                        unsafe_allow_html=True,
                    )
```

`st.popover` is supported in Streamlit ≥1.32 (we run 1.54). The popover body inherits the page theme; `_render_football_field`'s inline CSS still applies because Streamlit renders popovers inline (not in an iframe).

- [ ] **Step 3: Smoke-import streamlit_app**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -c "import streamlit_app; print('OK' if hasattr(streamlit_app, '_render_football_field') else 'MISSING')"`
Expected: prints `OK` (any pre-existing `KeyError: supabase_client` warnings are not new).

- [ ] **Step 4: Run watchlist UI tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest tests/test_watchlist_ui.py -v 2>&1 | tail -10`
Expected: all pass (no test changes in this task; we added integration code that doesn't have a unit-testable signature).

- [ ] **Step 5: Lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check streamlit_app.py`
Expected: no new violations.

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: wire football-field popover trigger into watchlist row"
```

---

### Task 8: Force-refresh + lint + full regression

**Files:**
- None (verification only)

- [ ] **Step 1: Lint over the whole repo**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check .`
Expected: All checks pass on `valuation_lenses.py`, `streamlit_app.py`, `tests/test_multi_lens.py`, `tests/test_watchlist_ui.py`. Pre-existing violations elsewhere are not Phase-2-D's responsibility.

- [ ] **Step 2: Full regression suite**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m pytest test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py tests/test_watchlist_ui.py tests/test_market_data.py 2>&1 | tail -3
```
Expected: ~186 tests passed (was 178; net +8 new in this PR, may be slightly different depending on rename behavior).

- [ ] **Step 3: Force-refresh production data**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 scripts/force_refresh_all.py 2>&1 | tail -25
```
Expected: all 21 tickers refresh successfully. Each saved cfg now has `valuation_summary.lenses["historical"]` populated.

- [ ] **Step 4: Spot-check MSFT**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -c "
import os, json
env = json.load(open('/Users/administrator/Library/Application Support/Claude/claude_desktop_config.json'))['mcpServers']['lazytheta-dcf']['env']
for k, v in env.items(): os.environ[k] = v
import config_store
from supabase import create_client
client = create_client(env['SUPABASE_URL'], env['SUPABASE_SERVICE_KEY'])
cfg = config_store.load_config(client, 'MSFT', user_id=env['LAZYTHETA_USER_ID'])
lenses = (cfg.get('valuation_summary') or {}).get('lenses', {})
for n in ('dcf','multiples','historical','reverse_dcf','dividend'):
    print(f'{n}:', 'None' if lenses.get(n) is None else f\"fv_mid={lenses[n].get('fv_mid')}\")
"
```
Expected: 4 active lenses (dcf, multiples, historical, reverse_dcf) with non-None fv_mid; dividend = None.

- [ ] **Step 5: Commit (only if you fixed any lint issues in Step 1)**

If Step 1 required edits, commit them:
```bash
git add valuation_lenses.py streamlit_app.py tests/
git commit -m "style: lint fixes for Phase 2-D"
```

If no edits were needed, skip this step.

---

## Summary of Commits (target sequence)

1. `feat: compute_historical_lens with 3 own-history sub-anchors`
2. `test: pin historical lens edge cases (skip / single-anchor)`
3. `refactor: strip own-history sub-anchors from compute_multiples_lens`
4. `feat: orchestrator includes historical lens; default weights split 5-way`
5. `refactor: _render_lens_dots uses generic '{N} lenses' label`
6. `feat: _render_football_field helper with per-lens bars + markers`
7. `feat: wire football-field popover trigger into watchlist row`
8. (optional) `style: lint fixes for Phase 2-D`

7-8 commits. Implementation should take ~60 minutes via subagents — most tasks are mechanical (code-relocation + small additions), Tasks 6-7 have the most novel HTML/CSS work.
