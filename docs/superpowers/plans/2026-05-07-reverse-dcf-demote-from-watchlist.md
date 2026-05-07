# Demote Reverse DCF from Watchlist Lens — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Reverse DCF from the watchlist lens-dots, football field, and weighted-FV calculation, while keeping it intact on the per-ticker detail page and keeping `summary["lenses"]["reverse_dcf"]` populated for MCP/API consumers.

**Architecture:** Four small changes — set `DEFAULT_LENS_WEIGHTS["reverse_dcf"]` to 0.0; drop `"reverse_dcf"` from the order lists in `_render_lens_dots` and `_render_football_field`; restrict `lens_count` in `config_store.list_watchlist` to the three forward-looking lenses. The orchestrator still calls `compute_reverse_dcf_lens` and stores its output, so MCP / detail-page consumers keep working.

**Tech Stack:** Python 3.x, Streamlit, pytest. No new dependencies. No DB migration.

**Spec:** `docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md`

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `valuation_lenses.py` | Multi-lens orchestrator + per-lens compute fns | `DEFAULT_LENS_WEIGHTS["reverse_dcf"]: 0.10 → 0.0` |
| `streamlit_app.py` | Watchlist UI rendering | Drop `"reverse_dcf"` from `_render_lens_dots` order list (line 122) and `_render_football_field` lens_order list (line ~246) |
| `config_store.py` | Watchlist persistence + listing | Restrict `lens_count` in `list_watchlist` to `("dcf", "multiples", "historical")` |
| `scripts/force_refresh_all.py` | CLI batch-refresh utility | Mirror `lens_count` restriction in the progress log |
| `tests/test_multi_lens.py` | Multi-lens orchestrator tests | Update default-weights assertion + `lens_count` fixture/assertion |
| `tests/test_watchlist_ui.py` | Watchlist UI tests | Update lens-dots tests (4→3 dots) + football-field test (drop "Reverse DCF" assertion) |

---

## Task 1: Default lens weight zeroed for reverse_dcf

**Why first:** All other behaviour flows from the weight default. Test-first lets us catch downstream impact early.

**Files:**
- Modify: `valuation_lenses.py:16-22` (`DEFAULT_LENS_WEIGHTS` dict)
- Modify: `tests/test_multi_lens.py:743-750` (`test_default_lens_weights_post_split`)

- [ ] **Step 1: Update the failing test**

Replace the body of `test_default_lens_weights_post_split` in `tests/test_multi_lens.py`:

```python
def test_default_lens_weights_post_split():
    assert valuation_lenses.DEFAULT_LENS_WEIGHTS == {
        "dcf":         0.30,
        "multiples":   0.30,
        "historical":  0.30,
        "reverse_dcf": 0.0,
        "dividend":    0.00,
    }
```

(Only `reverse_dcf` value changes from `0.10` to `0.0`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_multi_lens.py::test_default_lens_weights_post_split -v`
Expected: FAIL — assertion mismatch on `reverse_dcf` (still `0.10` in production code).

- [ ] **Step 3: Update the production default**

In `valuation_lenses.py`, change the dict at lines 16-22:

```python
DEFAULT_LENS_WEIGHTS = {
    "dcf":         0.30,
    "multiples":   0.30,
    "historical":  0.30,
    "reverse_dcf": 0.0,    # anchors at current price by definition; not a true valuation
    "dividend":    0.00,
}
```

- [ ] **Step 4: Run the targeted test to verify it passes**

Run: `python3 -m pytest tests/test_multi_lens.py::test_default_lens_weights_post_split -v`
Expected: PASS.

- [ ] **Step 5: Run the full multi-lens suite to confirm no regressions elsewhere**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: All tests PASS.

The orchestrator renormalises active lens weights, so DCF-only and all-active flows still produce `weight_normalized` summing to 1.0 (reverse_dcf contributes 0). `test_dcf_only_fallback_when_no_valuation_inputs`, `test_all_lenses_active_weighted_in_range`, and `test_lens_weights_override_from_config` all still hold. If any unexpectedly fails, stop and investigate before proceeding.

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "$(cat <<'EOF'
refactor(lenses): drop reverse_dcf from weighted FV (weight 0.0)

Reverse DCF anchors at current stock price by construction; weighting
it 10% pulled weighted_fv_mid 10% toward market price — circular bias.
The lens is still computed and stored in summary["lenses"]["reverse_dcf"]
so MCP queries and the detail-page reverse-DCF section keep working.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Drop reverse_dcf from lens-dots renderer

**Files:**
- Modify: `streamlit_app.py:113-142` (`_render_lens_dots`)
- Modify: `tests/test_watchlist_ui.py:99-128, 290-310` (lens-dots tests)

- [ ] **Step 1: Update the failing tests**

In `tests/test_watchlist_ui.py`, replace these test bodies:

```python
def test_render_lens_dots_all_active():
    """All forward-looking lenses active → 3 filled dots, '3 lenses' label.
    (reverse_dcf intentionally not rendered — it anchors at price.)"""
    lenses = {"dcf": {}, "multiples": {}, "historical": {}, "reverse_dcf": {}, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 3
    assert 'class="ld-off"' not in html
    assert "3 lenses" in html


def test_render_lens_dots_dcf_only():
    """Only DCF active → 1 filled dot, 2 grey dots, '1 lens' label."""
    lenses = {"dcf": {}, "multiples": None, "historical": None, "reverse_dcf": None, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1
    assert html.count('class="ld-off"') == 2
    assert "1 lens" in html


def test_render_lens_dots_dcf_plus_historical():
    """DCF + Historical active → 2 filled dots, '2 lenses' label.
    (Replaces test_render_lens_dots_dcf_plus_reverse — reverse_dcf no longer renders.)"""
    lenses = {"dcf": {}, "multiples": None, "historical": {}, "reverse_dcf": None, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 2
    assert "2 lenses" in html


def test_render_lens_dots_empty_dict():
    """No lenses at all → 'no lenses' label, all 3 dots grey."""
    html = streamlit_app._render_lens_dots({}, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert html.count('class="ld-off"') == 3
    assert "no lenses" in html
```

Then delete `test_render_lens_dots_dcf_plus_reverse` (lines 116-120) — its scenario (DCF + reverse_dcf only) no longer makes sense, replaced by the dcf+historical variant above.

Then replace `test_render_lens_dots_4_active_after_split` (lines 302-310) with:

```python
def test_render_lens_dots_3_active_after_demote():
    """After Reverse-DCF demotion, max 3 lenses render as dots."""
    lenses = {
        "dcf": {}, "multiples": {}, "historical": {}, "reverse_dcf": {},
        "dividend": None,
    }
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 3
    assert "3 lenses" in html
```

`test_render_lens_dots_zero_active`, `test_render_lens_dots_omits_dividend_from_display`, and `test_render_lens_dots_empty_dict_is_active_not_inactive` keep working as-is — they all already produce ≤3 dots and don't depend on reverse_dcf rendering.

- [ ] **Step 2: Run lens-dots tests to verify they fail**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k "lens_dots" -v`
Expected: FAIL — production code still renders 4 dots including reverse_dcf.

- [ ] **Step 3: Update the production renderer**

In `streamlit_app.py`, modify `_render_lens_dots` (lines 113-142). The change is one line — the `order` list:

```python
def _render_lens_dots(lenses: dict, theme: dict) -> str:
    """Render N dots showing which forward-looking lenses are active + a count label.

    Order: dcf · multiples · historical. Reverse DCF is intentionally not
    rendered — it anchors at current price by definition (see
    docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md).
    Dividend stub is also omitted (always greyed out under Phase 2 scope).

    Each lens key maps to a non-None lens dict (active, green dot) or None
    (skipped, grey dot). Label: "{N} lens" or "{N} lenses" or "no lenses".
    """
    order = ["dcf", "multiples", "historical"]
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

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k "lens_dots" -v`
Expected: All `lens_dots` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "$(cat <<'EOF'
ui(watchlist): drop reverse_dcf from lens-dots row

Lens-dots row now shows max 3 dots (dcf · multiples · historical).
Reverse DCF is no longer surfaced in the watchlist row — it remains
available on the per-ticker detail page via its dedicated section.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Drop reverse_dcf from football field renderer

**Files:**
- Modify: `streamlit_app.py:222-…` (`_render_football_field`, specifically the `lens_order` list around line 246)
- Modify: `tests/test_watchlist_ui.py:336-362` (`test_render_football_field_renders_all_active_lenses`)

- [ ] **Step 1: Update the failing test**

In `tests/test_watchlist_ui.py`, replace `test_render_football_field_renders_all_active_lenses`:

```python
def test_render_football_field_renders_all_active_lenses():
    """Full summary → HTML contains 3 forward-lens bars (DCF, Multiples,
    Historical) + price marker. Reverse DCF intentionally absent — its
    bar would overlap with the Price marker."""
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
    # 3 forward-lens labels in the HTML
    assert "DCF" in html
    assert "Multiples" in html
    assert "Historical" in html
    # Reverse DCF intentionally absent
    assert "Reverse DCF" not in html
    # Markers for current price
    assert "$100" in html or "100.00" in html
    # Class hooks for the bars — exactly 3
    assert html.count("ff-bar") == 3
```

`test_render_football_field_handles_missing_lens` still works as-is — its summary already has `multiples: None`, which still triggers a "(skipped)" bar, and the reverse_dcf entry is now ignored (one fewer bar rendered).

- [ ] **Step 2: Run football-field tests to verify they fail**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k "football_field" -v`
Expected: FAIL on `test_render_football_field_renders_all_active_lenses` — production still renders the Reverse DCF bar.

- [ ] **Step 3: Update the production renderer**

In `streamlit_app.py`, in `_render_football_field` (around line 242-247), update the `lens_order` list:

```python
    lens_order = [
        ("dcf", "DCF"),
        ("multiples", "Multiples"),
        ("historical", "Historical"),
        # "reverse_dcf" intentionally omitted — its bar would overlap the
        # Price marker (lens always returns fv = stock_price). See
        # docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md.
    ]
```

Also remove `"reverse_dcf"` from the docstring at the top of `_render_football_field` if it explicitly lists the lens names. If the docstring is generic ("one bar per lens"), leave it.

- [ ] **Step 4: Run the targeted tests to verify they pass**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k "football_field" -v`
Expected: All `football_field` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "$(cat <<'EOF'
ui(watchlist): drop reverse_dcf bar from football field

Football field tooltip now shows 3 forward-lens bars; the Reverse DCF
bar would overlap the Price marker by construction (lens fv = price)
so dropping it is purely visual cleanup. Detail page Reverse DCF
section is unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Restrict lens_count in list_watchlist to forward-looking lenses

**Files:**
- Modify: `config_store.py:140-186` (`list_watchlist`, specifically lines 167-168 where `lens_count` is computed)
- Modify: `scripts/force_refresh_all.py:81` (CLI batch-refresh log line uses the same pattern)
- Modify: `tests/test_multi_lens.py:462-533` (`test_list_watchlist_enriched_shape`, specifically the assertion at line 521)

- [ ] **Step 1: Update the failing test**

In `tests/test_multi_lens.py`, update the fixture summary at line 473 so it includes a `historical` entry, and update the lens_count assertion at line 521. Replace:

```python
        "lenses": {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None},
```

with:

```python
        "lenses": {"dcf": {}, "multiples": {}, "historical": {}, "reverse_dcf": {}, "dividend": None},
```

And replace line 521:

```python
    assert with_row["lens_count"] == 3  # dcf, multiples, reverse_dcf (dividend None)
```

with:

```python
    assert with_row["lens_count"] == 3  # dcf + multiples + historical (reverse_dcf and dividend excluded from count)
```

The `WITHOUT` row's `lens_count == 0` assertion (line 531) still holds.

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `python3 -m pytest tests/test_multi_lens.py::test_list_watchlist_enriched_shape -v`
Expected: FAIL — under the current implementation the count is 4 (dcf + multiples + historical + reverse_dcf, since the existing code counts all non-None lens values).

- [ ] **Step 3: Update the production lens_count computation**

In `config_store.py`, modify lines 163-186 (the `for row in resp.data:` loop). Replace lines 165-168:

```python
    for row in resp.data:
        cfg = row.get("config") or {}
        summary = cfg.get("valuation_summary") or {}
        lenses = summary.get("lenses") or {}
        lens_count = sum(1 for v in lenses.values() if v is not None)
```

with:

```python
    # Forward-looking lenses surfaced in the watchlist row.
    # reverse_dcf is computed and stored, but excluded from the count
    # because it anchors at current price (see
    # docs/superpowers/specs/2026-05-07-reverse-dcf-demote-from-watchlist-design.md).
    _COUNTED_LENSES = ("dcf", "multiples", "historical")

    for row in resp.data:
        cfg = row.get("config") or {}
        summary = cfg.get("valuation_summary") or {}
        lenses = summary.get("lenses") or {}
        lens_count = sum(1 for k in _COUNTED_LENSES if lenses.get(k) is not None)
```

(Move the `_COUNTED_LENSES` tuple above the loop so it's defined once per call.)

- [ ] **Step 4: Run the targeted test to verify it passes**

Run: `python3 -m pytest tests/test_multi_lens.py::test_list_watchlist_enriched_shape -v`
Expected: PASS.

- [ ] **Step 5: Update the same lens_count pattern in the CLI batch script**

In `scripts/force_refresh_all.py`, replace line 81:

```python
                lens_count = sum(1 for v in (summary["lenses"] or {}).values() if v is not None)
```

with:

```python
                # Count only forward-looking lenses to match the watchlist UI
                # (reverse_dcf and dividend are computed but not surfaced as "active").
                _counted = ("dcf", "multiples", "historical")
                _ls = summary["lenses"] or {}
                lens_count = sum(1 for k in _counted if _ls.get(k) is not None)
```

Then run the script's --help (it has no test coverage, so this is a smoke check that it still parses):

Run: `python3 scripts/force_refresh_all.py --help` (if the script supports `--help`; otherwise just `python3 -c "import scripts.force_refresh_all"` to confirm it imports cleanly).
Expected: exits cleanly without `SyntaxError` / `NameError`.

- [ ] **Step 6: Commit**

```bash
git add config_store.py scripts/force_refresh_all.py tests/test_multi_lens.py
git commit -m "$(cat <<'EOF'
fix(watchlist): cap lens_count at 3 forward-looking lenses

list_watchlist's lens_count now counts only dcf + multiples + historical,
matching the lens-dots renderer. reverse_dcf and dividend stay out of the
count even when populated in valuation_summary. The same fix is applied
to scripts/force_refresh_all.py so the batch-refresh log matches the UI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Final verification — full test suite + ruff + manual smoke

**Why:** Tasks 1-4 each touched a different module. Confirm the full app still passes lint and tests, and that the watchlist row in a running Streamlit instance shows 3 dots / 3 bars as expected.

**Files:** none new — verification only.

- [ ] **Step 1: Run ruff lint**

Run: `python3 -m ruff check .`
Expected: PASS (no errors).

- [ ] **Step 2: Run the full pytest suite**

Run: `python3 -m pytest -v`
Expected: All tests PASS. The CLAUDE.md-mandated `test_tastytrade_api.py test_ibkr_api.py` (81 tests) plus `tests/test_multi_lens.py`, `tests/test_watchlist_ui.py`, and `tests/test_market_data.py` should all be green.

- [ ] **Step 3: Manual smoke — start Streamlit locally and inspect watchlist row**

Run: `streamlit run streamlit_app.py`
Open the browser to the watchlist tab. For at least one ticker that has a `valuation_summary` with all four lenses populated (e.g. MSFT, AAPL):
- Lens-dots row shows exactly **3 dots** (no 4th).
- Label says "3 lenses" (not "4 lenses").
- Hover the "details ›" pill: tooltip's football field shows exactly **3 horizontal bars** (DCF, Multiples, Historical) + the Price marker. No "Reverse DCF" bar.

Then click into the ticker (open the detail page) and scroll to the Reverse DCF section:
- The "Market implies" / "Your base case" cards still render.
- The sensitivity matrix still renders.
- The conclusion sentence still renders.
- (If anything in this section renders blank or errors, stop and investigate — Task 4 is supposed to leave the detail page completely untouched.)

If any of these checks fail, stop and report which one failed before proceeding to finish the branch.

- [ ] **Step 4: No commit — verification only**

Tasks 1-4 are already committed. If the smoke check passed, the branch is ready to finish via the `superpowers:finishing-a-development-branch` skill.

---

## Notes for the implementer

- **No worktree needed unless you want one.** The change is small (4 commits across 4 files) and we're branching from `main`. If you prefer to work in an isolated worktree, use the `using-git-worktrees` skill first.
- **DB / migrations:** none. Existing Supabase `valuation_summary` blobs remain valid. The next user-triggered "Refresh all" in the app will re-render with the new layout.
- **Stale data on first view:** before "Refresh all" runs, a watchlist row's `weighted_fv_mid` reflects the old (slightly biased) figure. Lens-dots and football field render the new layout immediately because they're driven by UI code, not stored data. This is expected and acceptable — don't try to invalidate the stored summaries.
- **Don't touch:** `dcf_calculator.compute_reverse_dcf`, the detail-page Reverse DCF section in `streamlit_app.py:4733+`, or the orchestrator's `compute_reverse_dcf_lens` function. They all stay exactly as they are.
