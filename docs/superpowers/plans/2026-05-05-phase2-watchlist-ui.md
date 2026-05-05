# Phase 2-A: Watchlist UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface multi-lens fair value (Phase 1) in the Streamlit watchlist with a richer Fair Value cell + a parallel "Refresh all valuations" button.

**Architecture:** All changes in `streamlit_app.py` `_watchlist_overview` and its row helpers. Five new private helpers. Tests for the data-shaping helpers and the refresh handler in a new `tests/test_watchlist_ui.py`.

**Tech Stack:** Streamlit, ThreadPoolExecutor, pytest. No new libraries.

**Spec:** `docs/superpowers/specs/2026-05-05-phase2-watchlist-ui-design.md`

---

## File Map

| Path | Purpose | Action |
|------|---------|--------|
| `streamlit_app.py` | Add 5 helpers, modify `_render_wl_header` + `_render_wl_row`, add refresh button + handler, add status line | Modify |
| `tests/test_watchlist_ui.py` | Unit tests for helpers + refresh handler | Create |

The five new helpers, all private to `streamlit_app.py`:

1. `_format_relative_time(iso_or_none) -> str` — pure: ISO timestamp → "3 days ago" / "just now" / "never"
2. `_range_bar_marker_position(price, low, high) -> tuple[float, bool]` — pure: returns `(percent_0_to_100, past_high_flag)` — clamped
3. `_render_fv_cell(row, theme) -> str` — returns HTML string for the Fair Value cell
4. `_render_lens_dots(lenses_dict, theme) -> str` — returns HTML string for the 3-dot lens indicator
5. `_refresh_stale_valuations(client, cfgs, user_id, force=False) -> dict` — runs the orchestrator parallel, returns `{"computed": [...], "errors": [...], "skipped": [...]}`. Pure-ish (mutates Supabase via save_config). Testable with mocks.

The row builder loop already loads cfgs and computes legacy DCF intrinsic. We extend it to read `cfg["valuation_summary"]` when present, otherwise keep computing legacy. The new `Fair Value` cell HTML replaces the existing single-line "Intrinsic" markdown.

---

### Task 1: Test scaffolding (`tests/test_watchlist_ui.py`)

**Files:**
- Create: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Create the bare test file with one always-pass test**

```python
"""Tests for Phase 2-A watchlist UI helpers."""
from datetime import datetime, timedelta, timezone

import pytest


def test_scaffold_present():
    """Sanity: the test file is discovered and runs."""
    assert True
```

- [ ] **Step 2: Verify pytest discovers it**

Run: `python3 -m pytest tests/test_watchlist_ui.py -v`
Expected: 1 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_watchlist_ui.py
git commit -m "test: scaffold tests/test_watchlist_ui.py"
```

---

### Task 2: `_format_relative_time` helper

**Files:**
- Modify: `streamlit_app.py` (add helper near other top-level utilities, e.g. after `sanitize_ticker` at line 60)
- Modify: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_watchlist_ui.py`:

```python
import streamlit_app


def test_format_relative_time_none():
    assert streamlit_app._format_relative_time(None) == "never"
    assert streamlit_app._format_relative_time("") == "never"


def test_format_relative_time_just_now():
    now = datetime.now(timezone.utc)
    iso = now.isoformat()
    assert streamlit_app._format_relative_time(iso) == "just now"


def test_format_relative_time_minutes():
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert streamlit_app._format_relative_time(past.isoformat()) == "5 minutes ago"


def test_format_relative_time_hours():
    past = datetime.now(timezone.utc) - timedelta(hours=3)
    assert streamlit_app._format_relative_time(past.isoformat()) == "3 hours ago"


def test_format_relative_time_days():
    past = datetime.now(timezone.utc) - timedelta(days=4)
    assert streamlit_app._format_relative_time(past.isoformat()) == "4 days ago"


def test_format_relative_time_future_treated_as_just_now():
    """Clock skew: future timestamps treated as current."""
    future = datetime.now(timezone.utc) + timedelta(hours=2)
    assert streamlit_app._format_relative_time(future.isoformat()) == "just now"


def test_format_relative_time_unparseable():
    """Garbage input → 'unknown' (don't crash)."""
    assert streamlit_app._format_relative_time("not a timestamp") == "unknown"
```

- [ ] **Step 2: Run tests — should fail**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k format_relative_time -v`
Expected: FAIL with `AttributeError: module 'streamlit_app' has no attribute '_format_relative_time'`.

- [ ] **Step 3: Implement helper**

In `streamlit_app.py`, after the `sanitize_ticker` function (around line 67), add:

```python
def _format_relative_time(iso_or_none: str | None) -> str:
    """Convert an ISO-8601 UTC string to "3 days ago" / "just now" / "never"."""
    if not iso_or_none:
        return "never"
    from datetime import datetime, timezone
    try:
        ts = datetime.fromisoformat(iso_or_none.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return "unknown"
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        m = secs // 60
        return f"{m} minute{'s' if m != 1 else ''} ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = secs // 86400
    return f"{d} day{'s' if d != 1 else ''} ago"
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k format_relative_time -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "feat: _format_relative_time helper for watchlist refresh status"
```

---

### Task 3: `_range_bar_marker_position` helper

**Files:**
- Modify: `streamlit_app.py`
- Modify: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_watchlist_ui.py`:

```python
def test_range_bar_marker_in_range():
    """Price between low and high → percent in (0, 100), not past_high."""
    pct, past = streamlit_app._range_bar_marker_position(80, 60, 100)
    assert pct == 50.0
    assert past is False


def test_range_bar_marker_at_low():
    pct, past = streamlit_app._range_bar_marker_position(60, 60, 100)
    assert pct == 0.0
    assert past is False


def test_range_bar_marker_at_high():
    pct, past = streamlit_app._range_bar_marker_position(100, 60, 100)
    assert pct == 100.0
    assert past is False


def test_range_bar_marker_below_low_clamps_to_one():
    """Price below low → 1% (just visible at left edge), not past_high."""
    pct, past = streamlit_app._range_bar_marker_position(40, 60, 100)
    assert pct == 1.0
    assert past is False


def test_range_bar_marker_above_high_clamps_to_99_and_flags_past_high():
    pct, past = streamlit_app._range_bar_marker_position(150, 60, 100)
    assert pct == 99.0
    assert past is True


def test_range_bar_marker_low_equals_high_returns_50():
    """Degenerate range — center the marker, no past_high."""
    pct, past = streamlit_app._range_bar_marker_position(80, 80, 80)
    assert pct == 50.0
    assert past is False


def test_range_bar_marker_invalid_inputs_return_50():
    """Missing/zero inputs → safe center fallback."""
    pct, past = streamlit_app._range_bar_marker_position(0, 60, 100)
    assert pct == 50.0
    pct, past = streamlit_app._range_bar_marker_position(80, 0, 100)
    assert pct == 50.0
```

- [ ] **Step 2: Run tests — should fail**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k range_bar_marker -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement helper**

In `streamlit_app.py`, after `_format_relative_time`, add:

```python
def _range_bar_marker_position(price: float, low: float, high: float) -> tuple[float, bool]:
    """Where on a 0-100% bar should the price marker sit?

    Returns (percent, past_high_flag).
    - percent: clamped to [1, 99] when out of range so the marker stays visible
    - past_high_flag: True when price > high (caller may color the marker red)
    - returns (50.0, False) for degenerate or missing inputs
    """
    if not price or not low or not high:
        return 50.0, False
    if high <= low:
        return 50.0, False
    raw = (price - low) / (high - low) * 100.0
    if raw < 0:
        return 1.0, False
    if raw > 100:
        return 99.0, True
    return raw, False
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k range_bar_marker -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "feat: _range_bar_marker_position helper for FV cell"
```

---

### Task 4: `_render_lens_dots` helper

**Files:**
- Modify: `streamlit_app.py`
- Modify: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_watchlist_ui.py`:

```python
def test_render_lens_dots_all_active():
    lenses = {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    # Three filled dots
    assert html.count('class="ld-on"') == 3
    assert 'class="ld-off"' not in html
    assert "3 lenses" in html


def test_render_lens_dots_dcf_only():
    lenses = {"dcf": {}, "multiples": None, "reverse_dcf": None, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 1
    assert html.count('class="ld-off"') == 2
    assert "DCF only" in html


def test_render_lens_dots_dcf_plus_reverse():
    lenses = {"dcf": {}, "multiples": None, "reverse_dcf": {}, "dividend": None}
    html = streamlit_app._render_lens_dots(lenses, theme={"text_muted": "#888"})
    assert html.count('class="ld-on"') == 2
    assert "DCF + reverse" in html


def test_render_lens_dots_empty_dict():
    """No lenses at all → 'no lenses' label, all grey."""
    html = streamlit_app._render_lens_dots({}, theme={"text_muted": "#888"})
    assert 'class="ld-on"' not in html
    assert html.count('class="ld-off"') == 3
    assert "no lenses" in html
```

- [ ] **Step 2: Run tests — should fail**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k lens_dots -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement helper**

In `streamlit_app.py`, after `_range_bar_marker_position`, add:

```python
def _render_lens_dots(lenses: dict, theme: dict) -> str:
    """Render the 3-dot lens indicator + summary label.

    Three dots in order: DCF · Multiples · Reverse DCF.
    Filled green when active (lens dict is truthy), grey when None/missing.
    Label after the dots: '3 lenses', 'DCF + reverse', 'DCF only', 'no lenses'.
    """
    order = ["dcf", "multiples", "reverse_dcf"]
    actives = [name for name in order if lenses.get(name)]
    parts = []
    for name in order:
        cls = "ld-on" if lenses.get(name) else "ld-off"
        parts.append(f'<span class="{cls}"></span>')

    if not actives:
        label = "no lenses"
    elif len(actives) == 3:
        label = "3 lenses"
    elif actives == ["dcf"]:
        label = "DCF only"
    elif actives == ["dcf", "reverse_dcf"]:
        label = "DCF + reverse"
    elif actives == ["multiples"]:
        label = "multiples only"
    elif actives == ["multiples", "reverse_dcf"]:
        label = "multiples + reverse"
    elif actives == ["reverse_dcf"]:
        label = "reverse only"
    else:
        # any remaining 2-active combo, e.g. ["dcf", "multiples"]
        label = " + ".join(a.replace("_", " ") for a in actives)

    color = theme.get("text_muted", "#888")
    return (
        f'<div style="font-size:0.7rem;color:{color};margin-top:1px">'
        f'{"".join(parts)} {label}</div>'
    )
```

Also add the supporting CSS classes near the top of `_watchlist_overview` (we'll wire them up properly in Task 6 when the row layout changes; for now it's enough that the helper exists). Actual CSS wiring happens in Task 6.

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k lens_dots -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "feat: _render_lens_dots helper"
```

---

### Task 5: `_render_fv_cell` helper

**Files:**
- Modify: `streamlit_app.py`
- Modify: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_watchlist_ui.py`:

```python
def _theme_stub():
    return {"text": "#eee", "text_muted": "#888", "accent": "#6e8a76"}


def test_render_fv_cell_full_summary():
    """With a complete valuation_summary, render mid + range + bar + dots."""
    summary = {
        "weighted_fv_low": 60.0,
        "weighted_fv_mid": 80.0,
        "weighted_fv_high": 100.0,
        "lenses": {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None},
    }
    html = streamlit_app._render_fv_cell(
        price=70.0, summary=summary, legacy_intrinsic=None, theme=_theme_stub()
    )
    assert "$80" in html              # mid
    assert "$60" in html              # low
    assert "$100" in html             # high
    assert "range-bar" in html        # bar present
    assert 'class="ld-on"' in html    # lens dots present


def test_render_fv_cell_legacy_fallback():
    """Without summary, fall back to legacy_intrinsic + 'single-lens' badge."""
    html = streamlit_app._render_fv_cell(
        price=72.0, summary=None, legacy_intrinsic=95.0, theme=_theme_stub()
    )
    assert "$95" in html
    assert "single-lens" in html
    assert "range-bar" not in html
    assert "Refresh all" in html


def test_render_fv_cell_neither_summary_nor_legacy():
    """Defensive: both missing → em-dash placeholder."""
    html = streamlit_app._render_fv_cell(
        price=72.0, summary=None, legacy_intrinsic=None, theme=_theme_stub()
    )
    assert "—" in html


def test_render_fv_cell_marker_past_high_red_tinted():
    summary = {
        "weighted_fv_low": 60.0, "weighted_fv_mid": 80.0, "weighted_fv_high": 100.0,
        "lenses": {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None},
    }
    html = streamlit_app._render_fv_cell(
        price=200.0, summary=summary, legacy_intrinsic=None, theme=_theme_stub()
    )
    assert "left:99%" in html.replace(" ", "")  # marker clamped to 99
    # red tint applied — implementation uses inline color override or extra class
    assert "#d96a5a" in html or "past-high" in html
```

- [ ] **Step 2: Run tests — should fail**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k fv_cell -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement helper**

In `streamlit_app.py`, after `_render_lens_dots`, add:

```python
def _fmt_fv_dollar(x: float) -> str:
    """Format a dollar value for the FV cell — integer if >= 100, else 2dp."""
    if x is None:
        return "—"
    if abs(x) >= 100:
        return f"${x:.0f}"
    return f"${x:.2f}"


def _render_fv_cell(price: float, summary: dict | None,
                     legacy_intrinsic: float | None, theme: dict) -> str:
    """Return HTML for the Fair Value cell.

    Three render modes:
    - summary present → bold mid · (low–high) · range-bar with marker · lens-dots
    - summary missing, legacy_intrinsic present → bold mid · 'single-lens' badge · hint
    - both missing → em-dash
    """
    text = theme.get("text", "#eee")
    muted = theme.get("text_muted", "#888")

    if summary:
        low = summary.get("weighted_fv_low")
        mid = summary.get("weighted_fv_mid")
        high = summary.get("weighted_fv_high")
        lenses = summary.get("lenses") or {}
        if mid is None or low is None or high is None:
            return f'<span style="color:{muted}">—</span>'

        pct, past_high = _range_bar_marker_position(price, low, high)
        marker_color = "#d96a5a" if past_high else "#fff"

        return (
            f'<div>'
            f'<strong style="color:{text}">{_fmt_fv_dollar(mid)}</strong> '
            f'<span style="color:{muted};font-size:0.78rem">'
            f'({_fmt_fv_dollar(low)}–{_fmt_fv_dollar(high)})</span>'
            f'<div class="range-bar" style="position:relative;height:6px;'
            f'background:linear-gradient(90deg,#6cc07055,#d8a44855,#d96a5a55);'
            f'border-radius:3px;margin:4px 0 2px 0;min-width:110px">'
            f'<div style="position:absolute;top:-3px;width:2px;height:12px;'
            f'background:{marker_color};box-shadow:0 0 2px rgba(0,0,0,0.6);'
            f'left:{pct:.1f}%"></div>'
            f'</div>'
            f'{_render_lens_dots(lenses, theme)}'
            f'</div>'
        )

    if legacy_intrinsic is not None:
        return (
            f'<div>'
            f'<strong style="color:{text}">{_fmt_fv_dollar(legacy_intrinsic)}</strong> '
            f'<span style="font-size:0.65rem;color:{muted};background:#33333355;'
            f'padding:1px 5px;border-radius:3px;margin-left:4px">single-lens</span>'
            f'<div style="font-size:0.72rem;color:{muted};margin-top:4px">'
            f'DCF intrinsic only · run "Refresh all" to compute multi-lens</div>'
            f'</div>'
        )

    return f'<span style="color:{muted}">—</span>'
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k fv_cell -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "feat: _render_fv_cell helper with summary/legacy/empty modes"
```

---

### Task 6: Wire `_render_fv_cell` into the row layout

**Files:**
- Modify: `streamlit_app.py:3472-3533` (the `_render_wl_header` and `_render_wl_row` functions inside `_watchlist_overview`)

This task changes column ratios + cell rendering. No new tests — visual verification.

- [ ] **Step 1: Read current header & row code**

Confirm the lines are 3472-3533 (may have shifted due to earlier helper additions). Use `grep -n "def _render_wl_header\|def _render_wl_row" streamlit_app.py` to locate.

- [ ] **Step 2: Update column ratios in both header and row**

The column ratio tuple appears twice (once per function). Change BOTH occurrences:

Old:
```python
[0.3, 1.0, 1.6, 0.8, 0.8, 0.8, 0.7, 0.6, 0.7, 0.7, 0.3]
```

New:
```python
[0.3, 1.0, 1.6, 0.8, 1.5, 0.8, 0.7, 0.6, 0.7, 0.7, 0.3]
```

(Position 4 — the Fair Value column — widened from `0.8` to `1.5`.)

- [ ] **Step 3: Update header labels**

In `_render_wl_header`, replace:

```python
_wl_hdr = ["", "Ticker", "Company", "Price", "Intrinsic", "Buy Price", "Upside", "P/E", "FCF Yield", "Earnings", ""]
```

with:

```python
_wl_hdr = ["", "Ticker", "Company", "Price", "Fair Value", "Buy", "Upside", "P/E", "FCF Yield", "Earnings", ""]
```

- [ ] **Step 4: Update the FV cell render in `_render_wl_row`**

Find:

```python
        cols[3].markdown(f"${row['price']:.2f}")
        cols[4].markdown(f"${row['intrinsic']:.2f}")
        cols[5].markdown(f"${row['buy_price']:.2f}")
        cols[6].markdown(f":{up_color}[{row['upside']:+.1%}]")
```

Replace with:

```python
        cols[3].markdown(f"${row['price']:.2f}")
        cols[4].markdown(
            _render_fv_cell(
                price=row['price'],
                summary=row.get('valuation_summary'),
                legacy_intrinsic=row.get('intrinsic'),
                theme=T,
            ),
            unsafe_allow_html=True,
        )
        cols[5].markdown(f"${row['buy_price']:.2f}")
        cols[6].markdown(f":{up_color}[{row['upside']:+.1%}]")
```

- [ ] **Step 5: Quick smoke verification (no automated test)**

Run: `python3 -c "import streamlit_app"`
Expected: no `ImportError`/`AttributeError` (Streamlit-runtime warnings about session state are pre-existing and ignored).

- [ ] **Step 6: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: wire _render_fv_cell into watchlist row layout"
```

---

### Task 7: Populate `row['valuation_summary']` and recompute upside

**Files:**
- Modify: `streamlit_app.py` row builder loop inside `_watchlist_overview` (lines ~3411-3458, the `for t, cfg_wl in _wl_configs.items()` block)

The existing loop builds rows from cfgs but doesn't yet thread `valuation_summary` into the row dict. We do that, AND recompute `upside` from `fv_mid` when summary is present.

- [ ] **Step 1: Modify the row builder loop**

Find the section that computes upside:

```python
            upside = (_wl_intrinsic / live_price - 1) if live_price > 0 else 0
```

Replace the upside line (and add summary extraction) so the surrounding block looks like this:

```python
            # Multi-lens summary (Phase 1) preferred over single-DCF intrinsic
            summary = cfg_wl.get('valuation_summary')
            if summary and summary.get('weighted_fv_mid') and live_price > 0:
                upside = summary['weighted_fv_mid'] / live_price - 1
                _wl_buy = summary.get('buy_price', _wl_buy)
            else:
                upside = (_wl_intrinsic / live_price - 1) if live_price > 0 else 0
```

Then update the row dict at the bottom of the loop. Find:

```python
        rows.append({
            'ticker': t,
            'company': cfg_wl.get('company', t),
            'notes': cfg_wl.get('notes', ''),
            'category': cfg_wl.get('category', 'Uncategorized'),
            'price': live_price,
            'intrinsic': _wl_intrinsic,
            'buy_price': _wl_buy,
            'upside': upside,
            'pe': pe,
            'fcf_yield': fcf_yield_val,
        })
```

Add `valuation_summary` to the dict:

```python
        rows.append({
            'ticker': t,
            'company': cfg_wl.get('company', t),
            'notes': cfg_wl.get('notes', ''),
            'category': cfg_wl.get('category', 'Uncategorized'),
            'price': live_price,
            'intrinsic': _wl_intrinsic,
            'buy_price': _wl_buy,
            'upside': upside,
            'pe': pe,
            'fcf_yield': fcf_yield_val,
            'valuation_summary': cfg_wl.get('valuation_summary'),
        })
```

- [ ] **Step 2: Smoke verification**

Run: `python3 -c "import streamlit_app"`
Expected: no errors.

Run: `python3 -m pytest tests/test_watchlist_ui.py test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py -v 2>&1 | tail -5`
Expected: all tests still pass (we haven't broken anything).

- [ ] **Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: thread valuation_summary into watchlist rows; recompute upside from fv_mid"
```

---

### Task 8: `_refresh_stale_valuations` handler

**Files:**
- Modify: `streamlit_app.py`
- Modify: `tests/test_watchlist_ui.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_watchlist_ui.py`:

```python
from unittest.mock import MagicMock, patch


def test_refresh_filters_to_stale_only():
    """Configs without summary OR with summary > 7 days old are stale; fresh ones are skipped."""
    now = datetime.now(timezone.utc)
    cfgs = {
        "FRESH": {"valuation_summary": {"calculated_at": now.isoformat(),
                                          "weighted_fv_mid": 50.0}},
        "OLD": {"valuation_summary": {"calculated_at": (now - timedelta(days=10)).isoformat(),
                                        "weighted_fv_mid": 50.0}},
        "EMPTY": {},
    }

    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote") as mock_calc, \
         patch.object(streamlit_app, "save_config") as mock_save:
        mock_calc.return_value = {"calculated_at": now.isoformat(), "weighted_fv_mid": 99.0}
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1", force=False
        )
    assert set(result["computed"]) == {"OLD", "EMPTY"}
    assert result["skipped"] == ["FRESH"]
    assert result["errors"] == []


def test_refresh_force_includes_fresh():
    now = datetime.now(timezone.utc)
    cfgs = {
        "FRESH": {"valuation_summary": {"calculated_at": now.isoformat(),
                                          "weighted_fv_mid": 50.0}},
    }
    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote") as mock_calc, \
         patch.object(streamlit_app, "save_config") as mock_save:
        mock_calc.return_value = {"calculated_at": now.isoformat(), "weighted_fv_mid": 99.0}
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1", force=True
        )
    assert result["computed"] == ["FRESH"]
    assert result["skipped"] == []


def test_refresh_one_ticker_error_others_succeed():
    now = datetime.now(timezone.utc)
    cfgs = {"GOOD": {}, "BAD": {}}

    def fake_calc(cfg):
        if cfg.get("ticker") == "BAD":
            raise ValueError("boom")
        return {"calculated_at": now.isoformat(), "weighted_fv_mid": 50.0}

    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote", side_effect=fake_calc), \
         patch.object(streamlit_app, "save_config"):
        # Ensure cfgs have ticker so the side_effect can branch
        cfgs["GOOD"]["ticker"] = "GOOD"
        cfgs["BAD"]["ticker"] = "BAD"
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1"
        )
    assert "GOOD" in result["computed"]
    assert any("BAD" in e for e in result["errors"])


def test_refresh_unparseable_calculated_at_treated_as_stale():
    cfgs = {
        "WEIRD": {"valuation_summary": {"calculated_at": "garbage",
                                          "weighted_fv_mid": 50.0}},
    }
    with patch.object(streamlit_app, "calculate_multi_lens_valuation_remote") as mock_calc, \
         patch.object(streamlit_app, "save_config"):
        mock_calc.return_value = {"calculated_at": datetime.now(timezone.utc).isoformat(),
                                  "weighted_fv_mid": 99.0}
        result = streamlit_app._refresh_stale_valuations(
            client=MagicMock(), cfgs=cfgs, user_id="u1"
        )
    assert result["computed"] == ["WEIRD"]
```

- [ ] **Step 2: Run tests — should fail**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k refresh -v`
Expected: FAIL with `AttributeError` for both `_refresh_stale_valuations` and `calculate_multi_lens_valuation_remote`.

- [ ] **Step 3: Add a thin wrapper around `valuation_lenses.calculate_multi_lens_valuation`**

The handler needs to call the orchestrator with mutation isolation. Add to `streamlit_app.py` near other top-level imports/utilities (after `_render_fv_cell`):

```python
def calculate_multi_lens_valuation_remote(cfg: dict) -> dict:
    """Thin wrapper so tests can monkey-patch this name without touching
    the pure orchestrator."""
    import valuation_lenses
    return valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid=False)
```

- [ ] **Step 4: Implement the handler**

After `calculate_multi_lens_valuation_remote`, add:

```python
def _refresh_stale_valuations(client, cfgs: dict, user_id: str | None = None,
                               force: bool = False, max_workers: int = 6) -> dict:
    """Run the multi-lens orchestrator across stale tickers in parallel.

    Stale = no valuation_summary OR calculated_at older than 7 days OR unparseable.
    Returns {"computed": [...], "errors": [...], "skipped": [...]}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timedelta, timezone

    threshold = datetime.now(timezone.utc) - timedelta(days=7)

    def _is_stale(cfg):
        s = cfg.get("valuation_summary") if isinstance(cfg, dict) else None
        if not s:
            return True
        ts_str = s.get("calculated_at")
        if not ts_str:
            return True
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            return True
        return ts < threshold

    targets = list(cfgs.keys()) if force else [t for t, c in cfgs.items() if _is_stale(c)]
    skipped = [t for t in cfgs.keys() if t not in targets]

    computed = []
    errors = []

    def _refresh_one(ticker):
        cfg = dict(cfgs[ticker])
        cfg.setdefault("ticker", ticker)
        summary = calculate_multi_lens_valuation_remote(cfg)
        cfg["valuation_summary"] = summary
        save_config(client, ticker, cfg, user_id=user_id)
        return ticker

    if not targets:
        return {"computed": computed, "errors": errors, "skipped": skipped}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_refresh_one, t): t for t in targets}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                future.result()
                computed.append(ticker)
            except Exception as e:
                logger.warning("Refresh failed for %s: %s", ticker, e)
                errors.append(f"{ticker}: {e}")

    return {"computed": computed, "errors": errors, "skipped": skipped}
```

- [ ] **Step 5: Run tests — should pass**

Run: `python3 -m pytest tests/test_watchlist_ui.py -k refresh -v`
Expected: 4 passed.

- [ ] **Step 6: Run full test_watchlist_ui.py suite**

Run: `python3 -m pytest tests/test_watchlist_ui.py -v`
Expected: ~26 passed (1 scaffold + 7 relative_time + 7 marker + 4 lens_dots + 4 fv_cell + 4 refresh = 27).

- [ ] **Step 7: Commit**

```bash
git add streamlit_app.py tests/test_watchlist_ui.py
git commit -m "feat: _refresh_stale_valuations parallel handler"
```

---

### Task 9: Refresh button + status line in `_watchlist_overview`

**Files:**
- Modify: `streamlit_app.py:3310-3360` (the section that renders Add-to-Watchlist + reads watchlist)

This task wires the handler into the page UI. No new tests — visual verification.

- [ ] **Step 1: Locate the Add-to-Watchlist section**

Verify with: `grep -n "Add to Watchlist\|wl_add_col" streamlit_app.py | head`

- [ ] **Step 2: Replace the existing `wl_add_col1, wl_add_col2 = st.columns(...)` block**

Find:

```python
    # ── Add ticker ──
    st.markdown("")
    wl_add_col1, wl_add_col2 = st.columns([3, 1], vertical_alignment="center")
    with wl_add_col1:
        wl_ticker = st.text_input(
            "Add ticker",
            placeholder="e.g. AAPL",
            label_visibility="collapsed",
            key="wl_ticker_input",
        )
    with wl_add_col2:
        wl_add = st.button("Add to Watchlist", use_container_width=True, type="primary")
```

Replace with:

```python
    # ── Add ticker + Refresh ──
    st.markdown("")
    wl_add_col1, wl_add_col2, wl_add_col3 = st.columns([3, 1, 1], vertical_alignment="center")
    with wl_add_col1:
        wl_ticker = st.text_input(
            "Add ticker",
            placeholder="e.g. AAPL",
            label_visibility="collapsed",
            key="wl_ticker_input",
        )
    with wl_add_col2:
        wl_add = st.button("Add to Watchlist", use_container_width=True, type="primary")
    with wl_add_col3:
        wl_refresh = st.button(
            "↻ Refresh all",
            use_container_width=True,
            help="Recompute multi-lens fair value for tickers without a recent summary.",
        )
```

- [ ] **Step 3: Add the refresh handler immediately after, before `# ── Overview table ──`**

Place the following code immediately before the line `# ── Overview table ──` (around line 3358):

```python
    if wl_refresh:
        # Need cfgs first; load just the lightweight watchlist metadata, then full configs
        _wl_meta = list_watchlist(_sb_client)
        _wl_tickers_for_refresh = [item["ticker"] for item in _wl_meta]
        from concurrent.futures import ThreadPoolExecutor as _Pool

        def _load_one_for_refresh(t):
            c = load_config(_sb_client, t)
            return (t, c) if c is not None else None

        with _Pool(max_workers=6) as _pool:
            _refresh_cfgs = {
                r[0]: r[1] for r in _pool.map(_load_one_for_refresh, _wl_tickers_for_refresh) if r
            }

        if not _refresh_cfgs:
            st.info("Watchlist is empty — nothing to refresh.")
        else:
            _force = st.session_state.pop("_wl_force_refresh", False)
            _bar = st.progress(0.0, text="Computing valuations...")
            _result = _refresh_stale_valuations(
                _sb_client, _refresh_cfgs,
                user_id=st.session_state["user"]["id"], force=_force,
            )
            _bar.empty()
            _total = len(_refresh_cfgs)
            _done = len(_result["computed"])
            _err = len(_result["errors"])
            _skip = len(_result["skipped"])
            if _err:
                st.warning(
                    f"Refreshed {_done}/{_total}. {_err} errors, {_skip} skipped (still fresh). "
                    f"Errors: {', '.join(_result['errors'][:5])}"
                )
            elif _done == 0:
                st.success(f"All {_skip} tickers already fresh.")
            else:
                st.success(f"Refreshed {_done} ticker{'s' if _done != 1 else ''}, {_skip} already fresh.")
            st.cache_data.clear()
            st.rerun()
```

- [ ] **Step 4: Add status line just before the refresh handler block**

Right after the buttons row and before the refresh handler, insert a small caption summarizing recency:

```python
    # Refresh status hint (cheap; uses list_watchlist which is already cached upstream)
    _wl_for_status = list_watchlist(_sb_client)
    _summaries = []
    _total_for_status = len(_wl_for_status)
    for _row in _wl_for_status:
        # list_watchlist returns enriched dict; fv_mid != None implies summary present
        if _row.get("fv_mid") is not None:
            _summaries.append(_row)
    _last_refreshed_iso = None
    if _summaries:
        # We don't have calculated_at in the enriched list_watchlist output;
        # fall back to the row's `updated` (when save_config last touched it).
        # That's good enough for "last refreshed" UX even if it's slightly imprecise.
        _last_refreshed_iso = max(r.get("updated") or "" for r in _summaries) or None
    st.caption(
        f"Last refresh: {_format_relative_time(_last_refreshed_iso)} · "
        f"{len(_summaries)} of {_total_for_status} tickers have multi-lens summaries"
    )
```

- [ ] **Step 5: Add a small "Force refresh all" link below the buttons**

Right after the status caption from Step 4, add:

```python
    if st.button("↳ Force refresh all (ignore freshness)", key="wl_force_refresh_link",
                  type="tertiary", help="Recompute every ticker even if recently refreshed."):
        st.session_state["_wl_force_refresh"] = True
        st.rerun()
```

(Streamlit ≥ 1.34 supports `type="tertiary"`. If the deployed version is older, fall back to `type="secondary"`.)

- [ ] **Step 6: Smoke verification**

Run: `python3 -c "import streamlit_app"`
Expected: no errors.

Run: `python3 -m pytest tests/test_watchlist_ui.py -v 2>&1 | tail -5`
Expected: 27 passed.

- [ ] **Step 7: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: refresh-all button + status line in watchlist overview"
```

---

### Task 10: Local visual review + lint + regression

**Files:**
- None (testing/verification only)

- [ ] **Step 1: Start dev server in background**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m streamlit run streamlit_app.py --server.headless=true --server.port=8503 &
```

(If Streamlit Cloud secrets aren't configured locally, you may see a Supabase auth wall. That's expected. Verify the page builds without `ImportError`/`AttributeError`.)

- [ ] **Step 2: Visit http://localhost:8503/?page=Watchlist (or whatever the URL is)**

Open in a browser, sign in if needed. Expected: watchlist renders. Tickers without summary show legacy fallback. Click "↻ Refresh all" — progress bar appears, tickers update with multi-lens data after completion.

If you don't have working credentials locally, skip this step and rely on the unit tests + production smoke test post-merge.

- [ ] **Step 3: Stop the server**

```bash
pkill -f "streamlit run streamlit_app.py" 2>/dev/null || true
```

- [ ] **Step 4: Lint over the whole repo**

Run: `python3 -m ruff check .`
Expected: All checks pass on new files (`tests/test_watchlist_ui.py`) and on the streamlit_app.py changes. Pre-existing violations in `config_store.py` (UP017/RUF019) and other parts of `streamlit_app.py` are not Phase-2A's responsibility — leave them.

If new violations were introduced by this branch, fix them. Re-run until clean (modulo pre-existing).

- [ ] **Step 5: Full regression suite**

Run:
```bash
cd /Users/administrator/Documents/github/stock-analysis
python3 -m pytest test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py tests/test_watchlist_ui.py -v
```
Expected: all tests pass. Total count = 81 (existing) + 25 (multi_lens) + 27 (watchlist_ui) = 133.

- [ ] **Step 6: Commit (only if you fixed any lint issues)**

If Step 4 required edits, commit them:
```bash
git add streamlit_app.py
git commit -m "style: lint fixes for Phase 2-A"
```

If no edits were needed, skip this step (no commit).

---

## Summary of Commits (target sequence)

1. `test: scaffold tests/test_watchlist_ui.py`
2. `feat: _format_relative_time helper for watchlist refresh status`
3. `feat: _range_bar_marker_position helper for FV cell`
4. `feat: _render_lens_dots helper`
5. `feat: _render_fv_cell helper with summary/legacy/empty modes`
6. `feat: wire _render_fv_cell into watchlist row layout`
7. `feat: thread valuation_summary into watchlist rows; recompute upside from fv_mid`
8. `feat: _refresh_stale_valuations parallel handler`
9. `feat: refresh-all button + status line in watchlist overview`
10. (optional) `style: lint fixes for Phase 2-A`

9-10 commits. Implementation should take ~1.5h via subagents, given each task is small and most logic is in pure helpers with good test coverage.
