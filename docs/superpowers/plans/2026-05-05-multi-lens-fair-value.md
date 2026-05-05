# Multi-Lens Fair Value Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Trading Multiples + Reverse-DCF as first-class lenses alongside DCF, plus enriched watchlist output.

**Architecture:** New pure module `valuation_lenses.py` holds all lens math + orchestrator. New tiny module `scorecard_utils.py` shared between Streamlit and MCP. `config_store.list_watchlist` parses summary from existing JSON column (no migration). New MCP tool wires it to Claude Desktop.

**Tech Stack:** Python 3.11, pytest, ruff, FastMCP, Supabase Python client.

**Spec:** `docs/superpowers/specs/2026-05-05-multi-lens-fair-value-design.md`

---

## File Map

| Path | Purpose | Action |
|------|---------|--------|
| `scorecard_utils.py` | Parse `ai_notes['Scorecard']` JSON → `{verdict, phase}` | Create |
| `valuation_lenses.py` | Pure lens functions + orchestrator | Create |
| `streamlit_app.py` | `_parse_scorecard_json` becomes thin re-export | Modify |
| `config_store.py` | Extend `_AI_NOTES_GUARDED_KEYS`; enrich `list_watchlist` | Modify |
| `mcp_server.py` | Add `calculate_multi_lens_valuation` tool | Modify |
| `tests/conftest.py` | Put project root on `sys.path` for `tests/` discovery | Create |
| `tests/test_multi_lens.py` | Unit + acceptance tests | Create |

---

## Test Fixtures (referenced by many tasks)

These helpers live at the **top** of `tests/test_multi_lens.py`. Tasks below assume they exist; first task to need them creates them, later tasks just use them.

```python
"""Tests for multi-lens fair value (Phase 1)."""
from unittest.mock import MagicMock

import pytest


def make_cfg(**overrides):
    cfg = {
        "company": "Test Co",
        "ticker": "TEST",
        "stock_price": 100.0,
        "equity_market_value": 100_000,
        "debt_market_value": 10_000,
        "risk_free_rate": 0.04,
        "erp": 0.05,
        "credit_spread": 0.01,
        "tax_rate": 0.21,
        "sector_betas": [("Sector", 1.0, 1.0)],
        "base_revenue": 50_000,
        "revenue_growth": [0.05] * 5,
        "op_margins": [0.20] * 5,
        "terminal_growth": 0.025,
        "terminal_margin": 0.18,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.02,
        "shares_outstanding": 1_000,
        "buyback_rate": 0.0,
        "margin_of_safety": 0.20,
        "cash_bridge": 5_000,
        "securities": 0,
        "bull_growth_adj": 0.02,
        "bear_growth_adj": -0.04,
        "bull_margin_adj": 0.02,
        "bear_margin_adj": -0.02,
        "peers": [],
    }
    cfg.update(overrides)
    return cfg


def make_peer(**overrides):
    p = {
        "ticker": "PEER1",
        "name": "Peer Co",
        "ev_revenue": 5.0,
        "ev_ebitda": 12.0,
        "pe": 20.0,
        "fwd_pe": 18.0,
        "op_margin": 0.20,
        "rev_growth": 0.05,
        "roic": 0.15,
    }
    p.update(overrides)
    return p


SAMPLE_VALUATION_INPUTS = {
    "forward_eps": 5.0,
    "historical_fwd_pe": 20.0,
    "ttm_ebitda": 12_000.0,
    "target_dividend_yield": 0.02,
    "current_dividend": 2.0,
    "expected_dividend_growth": 0.07,
}
```

---

### Task 1: Test infrastructure (conftest)

**Files:**
- Create: `tests/conftest.py`

- [ ] **Step 1: Create conftest to put repo root on sys.path**

```python
"""Pytest config for tests/ subfolder — ensures project root is importable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 2: Verify pytest discovers tests/ folder**

Run: `python3 -m pytest tests/ -v --collect-only`
Expected: `no tests ran` (empty), no import errors.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: pytest conftest for tests/ subfolder"
```

---

### Task 2: scorecard_utils.py (extract + parse helper)

**Files:**
- Create: `scorecard_utils.py`
- Create: `tests/test_multi_lens.py` (initial: imports + scorecard tests + the `make_cfg`/`make_peer`/`SAMPLE_VALUATION_INPUTS` fixtures shown above)

- [ ] **Step 1: Write failing tests for scorecard parsing**

Create `tests/test_multi_lens.py` with the fixture block from the **Test Fixtures** section above, then append:

```python
from scorecard_utils import parse_scorecard, parse_scorecard_json


# ---------------------------------------------------------------- scorecard

def test_parse_scorecard_json_fenced():
    raw = """
Some preamble.

```json
{"verdict": "deep_dive", "phase": {"number": 5, "name": "Capital Return"}}
```

trailing text
"""
    assert parse_scorecard_json(raw) == {
        "verdict": "deep_dive",
        "phase": {"number": 5, "name": "Capital Return"},
    }


def test_parse_scorecard_json_unfenced():
    raw = '{"verdict": "pass"}'
    assert parse_scorecard_json(raw) == {"verdict": "pass"}


def test_parse_scorecard_json_empty():
    assert parse_scorecard_json("") is None
    assert parse_scorecard_json(None) is None


def test_parse_scorecard_returns_verdict_and_phase():
    ai_notes = {
        "Scorecard": '```json\n{"verdict":"revisit","phase":{"number":4,"name":"Op. Lev."}}\n```'
    }
    assert parse_scorecard(ai_notes) == {"verdict": "revisit", "phase": 4}


def test_parse_scorecard_no_section_returns_nones():
    assert parse_scorecard({}) == {"verdict": None, "phase": None}
    assert parse_scorecard({"Other": "x"}) == {"verdict": None, "phase": None}


def test_parse_scorecard_section_unparseable_returns_nones():
    assert parse_scorecard({"Scorecard": "not json"}) == {"verdict": None, "phase": None}
```

- [ ] **Step 2: Run tests — should fail with ImportError**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: `ModuleNotFoundError: No module named 'scorecard_utils'`

- [ ] **Step 3: Create `scorecard_utils.py`**

```python
"""Shared scorecard parser. Used by streamlit_app.py renderer and the MCP
watchlist enrichment. Single source of truth for the JSON-in-markdown format
the Scorecard pre-scan section uses."""

import json
import re


def parse_scorecard_json(raw: str | None) -> dict | None:
    """Extract a JSON dict from a markdown answer.

    Accepts either a fenced ```json ... ``` block or a raw JSON object
    in the text. Returns the parsed dict, or None on any failure.
    """
    if not raw:
        return None

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    payload = m.group(1) if m else None

    if payload is None:
        start = raw.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(raw)):
                ch = raw[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        payload = raw[start:i + 1]
                        break

    if payload is None:
        return None

    try:
        return json.loads(payload)
    except Exception:
        pass

    try:
        fixed = re.sub(
            r'"((?:[^"\\]|\\.)*)"',
            lambda mm: '"' + mm.group(1).replace("\n", "\\n").replace("\r", "") + '"',
            payload,
            flags=re.DOTALL,
        )
        return json.loads(fixed)
    except Exception:
        return None


def parse_scorecard(ai_notes: dict | None) -> dict:
    """Pull verdict (str) and phase (int) out of ai_notes['Scorecard'].

    Returns {"verdict": str|None, "phase": int|None}. Never raises.
    """
    if not isinstance(ai_notes, dict):
        return {"verdict": None, "phase": None}

    raw = ai_notes.get("Scorecard")
    if not isinstance(raw, str):
        return {"verdict": None, "phase": None}

    parsed = parse_scorecard_json(raw)
    if not isinstance(parsed, dict):
        return {"verdict": None, "phase": None}

    verdict = parsed.get("verdict")
    if not isinstance(verdict, str):
        verdict = None

    phase_obj = parsed.get("phase") or {}
    phase_num = None
    if isinstance(phase_obj, dict):
        n = phase_obj.get("number")
        if isinstance(n, int):
            phase_num = n
        elif isinstance(n, str) and n.isdigit():
            phase_num = int(n)

    return {"verdict": verdict, "phase": phase_num}
```

- [ ] **Step 4: Run tests — all 6 should pass**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scorecard_utils.py tests/test_multi_lens.py
git commit -m "feat: scorecard_utils module with shared JSON parser"
```

---

### Task 3: streamlit_app.py uses scorecard_utils

**Files:**
- Modify: `streamlit_app.py:180-221` (replace local `_parse_scorecard_json` with import)

- [ ] **Step 1: Replace the local parser with an import**

In `streamlit_app.py`, replace lines 180-221 (the entire `_parse_scorecard_json` function) with:

```python
from scorecard_utils import parse_scorecard_json as _parse_scorecard_json
```

Place the import near the top of the file with other imports (alongside other relative-style imports), and delete the original function body. The name `_parse_scorecard_json` is kept so existing call sites elsewhere in the file are unchanged.

- [ ] **Step 2: Verify Streamlit app still imports cleanly**

Run: `python3 -c "import streamlit_app"`
Expected: no errors (any Streamlit runtime warnings about session state are normal).

- [ ] **Step 3: Run lint**

Run: `python3 -m ruff check streamlit_app.py scorecard_utils.py tests/test_multi_lens.py`
Expected: All checks passed.

- [ ] **Step 4: Run regression suite**

Run: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py -v`
Expected: 81 + 6 = 87 passed.

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py
git commit -m "refactor: streamlit_app uses shared scorecard_utils parser"
```

---

### Task 4: Extend `_AI_NOTES_GUARDED_KEYS` in config_store

**Files:**
- Modify: `config_store.py:49`

- [ ] **Step 1: Write failing test for guarded-keys preservation**

Append to `tests/test_multi_lens.py`:

```python
import config_store


def test_save_config_preserves_valuation_keys():
    """save_config must merge in valuation_inputs/valuation_summary/lens_weights
    from the existing DB row when the caller's cfg omits them."""
    existing = {
        "company": "X",
        "ai_notes": {"foo": "bar"},
        "peers": [{"ticker": "P"}],
        "valuation_inputs": {"forward_eps": 5.0},
        "valuation_summary": {"weighted_fv_mid": 80.0},
        "lens_weights": {"dcf": 0.5},
    }
    new_cfg = {"company": "X", "stock_price": 100}

    captured = {}

    def upsert(row):
        captured["row"] = row
        return MagicMock(execute=lambda: None)

    fake_table = MagicMock()
    fake_table.upsert = upsert

    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    # Patch load_config to return our existing row
    import config_store as cs
    orig_load = cs.load_config
    cs.load_config = lambda c, t, user_id=None: existing
    try:
        cs.save_config(fake_client, "TEST", new_cfg, user_id="u1")
    finally:
        cs.load_config = orig_load

    saved = captured["row"]["config"]
    assert saved["valuation_inputs"] == {"forward_eps": 5.0}
    assert saved["valuation_summary"] == {"weighted_fv_mid": 80.0}
    assert saved["lens_weights"] == {"dcf": 0.5}
    assert saved["ai_notes"] == {"foo": "bar"}
    assert saved["peers"] == [{"ticker": "P"}]
```

- [ ] **Step 2: Run test — should fail (keys not preserved yet)**

Run: `python3 -m pytest tests/test_multi_lens.py::test_save_config_preserves_valuation_keys -v`
Expected: FAIL — `KeyError: 'valuation_inputs'` (the saved cfg only has `company` and `stock_price`).

- [ ] **Step 3: Extend the guarded keys tuple**

In `config_store.py`, replace line 49:

```python
_AI_NOTES_GUARDED_KEYS = ("ai_notes", "peers")
```

with:

```python
_AI_NOTES_GUARDED_KEYS = (
    "ai_notes",
    "peers",
    "valuation_inputs",
    "valuation_summary",
    "lens_weights",
)
```

- [ ] **Step 4: Run test — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py::test_save_config_preserves_valuation_keys -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config_store.py tests/test_multi_lens.py
git commit -m "feat: guard valuation_inputs/summary/lens_weights from silent wipes"
```

---

### Task 5: valuation_lenses skeleton — module + dividend stub + DEFAULT_LENS_WEIGHTS

**Files:**
- Create: `valuation_lenses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_multi_lens.py`:

```python
import valuation_lenses


def test_default_lens_weights():
    assert valuation_lenses.DEFAULT_LENS_WEIGHTS == {
        "dcf": 0.30,
        "multiples": 0.40,
        "reverse_dcf": 0.10,
        "dividend": 0.00,
    }


def test_dividend_lens_returns_none():
    assert valuation_lenses.compute_dividend_lens(make_cfg()) is None
```

- [ ] **Step 2: Run tests — should fail with ModuleNotFoundError**

Run: `python3 -m pytest tests/test_multi_lens.py::test_default_lens_weights tests/test_multi_lens.py::test_dividend_lens_returns_none -v`
Expected: `ModuleNotFoundError: No module named 'valuation_lenses'`

- [ ] **Step 3: Create the skeleton module**

```python
"""Multi-lens fair value engine (Phase 1).

Pure functions: take a config dict, return a lens-result dict (or None).
No Supabase, no network, no streamlit imports — fully testable.
"""

import logging
import statistics
from datetime import datetime, timezone

import dcf_calculator

logger = logging.getLogger(__name__)


DEFAULT_LENS_WEIGHTS = {
    "dcf": 0.30,
    "multiples": 0.40,
    "reverse_dcf": 0.10,
    "dividend": 0.00,
}


def compute_dividend_lens(cfg):
    """Phase 2 placeholder.

    TODO Phase 2: Gordon Growth + yield mean-reversion using
    valuation_inputs.target_dividend_yield, current_dividend,
    expected_dividend_growth.
    """
    return None
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py::test_default_lens_weights tests/test_multi_lens.py::test_dividend_lens_returns_none -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: valuation_lenses module skeleton + dividend stub"
```

---

### Task 6: DCF lens — basic mode (`scenario_grid=False`)

**Files:**
- Modify: `valuation_lenses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_multi_lens.py`:

```python
def test_dcf_lens_basic_returns_band_around_intrinsic():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=False)
    base = lens["details"]["base_intrinsic"]
    assert lens["fv_mid"] == pytest.approx(base, rel=1e-9)
    assert lens["fv_low"] == pytest.approx(base * 0.85, rel=1e-9)
    assert lens["fv_high"] == pytest.approx(base * 1.15, rel=1e-9)
    assert lens["details"]["scenarios"] is None
    assert lens["details"]["wacc"] > 0


def test_dcf_lens_basic_intrinsic_positive_for_sample_cfg():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg)
    assert lens["fv_mid"] > 0
    assert lens["fv_low"] < lens["fv_mid"] < lens["fv_high"]
```

- [ ] **Step 2: Run tests — should fail (no `compute_dcf_lens`)**

Run: `python3 -m pytest tests/test_multi_lens.py -k dcf_lens_basic -v`
Expected: FAIL — `AttributeError: module 'valuation_lenses' has no attribute 'compute_dcf_lens'`.

- [ ] **Step 3: Implement basic DCF lens**

Append to `valuation_lenses.py`:

```python
def compute_dcf_lens(cfg, scenario_grid=False):
    """DCF lens. Always returns a result — never None."""
    wacc = dcf_calculator.compute_wacc(cfg)
    base = dcf_calculator.compute_intrinsic_value(cfg, wacc=wacc)
    base_intrinsic = base["intrinsic_value"]

    if not scenario_grid:
        return {
            "fv_low": base_intrinsic * 0.85,
            "fv_mid": base_intrinsic,
            "fv_high": base_intrinsic * 1.15,
            "details": {
                "wacc": wacc,
                "base_intrinsic": base_intrinsic,
                "scenarios": None,
            },
        }

    # Scenario grid path — implemented in next task
    raise NotImplementedError("scenario_grid implemented in Task 7")
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py -k dcf_lens_basic -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: DCF lens (±15% band mode)"
```

---

### Task 7: DCF lens — scenario grid mode

**Files:**
- Modify: `valuation_lenses.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_multi_lens.py`:

```python
def test_dcf_lens_scenario_grid_uses_bull_bear_adjustments():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=True)
    assert lens["details"]["scenarios"] is not None
    scenarios = lens["details"]["scenarios"]
    assert len(scenarios) == 16  # 4 growth offsets * 4 margin offsets
    base = lens["details"]["base_intrinsic"]
    assert lens["fv_mid"] == pytest.approx(base, rel=1e-9)
    assert lens["fv_low"] == min(scenarios)
    assert lens["fv_high"] == max(scenarios)
    assert lens["fv_low"] < lens["fv_high"]


def test_dcf_lens_scenario_grid_default_adjustments_when_missing():
    cfg = make_cfg()
    for key in ("bull_growth_adj", "bear_growth_adj",
                "bull_margin_adj", "bear_margin_adj"):
        cfg.pop(key, None)
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=True)
    assert len(lens["details"]["scenarios"]) == 16
```

- [ ] **Step 2: Run tests — should fail with NotImplementedError**

Run: `python3 -m pytest tests/test_multi_lens.py -k scenario_grid -v`
Expected: FAIL — `NotImplementedError`.

- [ ] **Step 3: Implement the scenario grid**

In `valuation_lenses.py`, replace the `raise NotImplementedError(...)` line with:

```python
    bull_g = cfg.get("bull_growth_adj", 0.02)
    bear_g = cfg.get("bear_growth_adj", -0.04)
    bull_m = cfg.get("bull_margin_adj", 0.02)
    bear_m = cfg.get("bear_margin_adj", -0.02)

    growth_offsets = [bear_g, bear_g / 2, bull_g / 2, bull_g]
    margin_offsets = [bear_m, bear_m / 2, bull_m / 2, bull_m]

    scenarios = []
    base_growth = list(cfg["revenue_growth"])
    base_margins = list(cfg["op_margins"])
    base_terminal_margin = cfg.get("terminal_margin", base_margins[-1])

    for g_off in growth_offsets:
        for m_off in margin_offsets:
            scen_cfg = dict(cfg)
            scen_cfg["revenue_growth"] = [g + g_off for g in base_growth]
            scen_cfg["op_margins"] = [m + m_off for m in base_margins]
            scen_cfg["terminal_margin"] = base_terminal_margin + m_off
            try:
                scen_wacc = dcf_calculator.compute_wacc(scen_cfg)
                price = dcf_calculator.compute_intrinsic_value(
                    scen_cfg, wacc=scen_wacc
                )["intrinsic_value"]
                scenarios.append(price)
            except Exception as e:
                logger.info(
                    "DCF scenario grid: skipping (g_off=%.3f, m_off=%.3f): %s",
                    g_off, m_off, e,
                )

    if not scenarios:
        scenarios = [base_intrinsic]

    return {
        "fv_low": min(scenarios),
        "fv_mid": base_intrinsic,
        "fv_high": max(scenarios),
        "details": {
            "wacc": wacc,
            "base_intrinsic": base_intrinsic,
            "scenarios": scenarios,
        },
    }
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py -k scenario_grid -v`
Expected: 2 passed.

- [ ] **Step 5: Run full test file**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: all tests so far pass (≥ 12 tests).

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: DCF lens scenario grid (4x4 bull/bear)"
```

---

### Task 8: Reverse-DCF lens

**Files:**
- Modify: `valuation_lenses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_multi_lens.py`:

```python
def test_reverse_dcf_lens_anchors_at_stock_price():
    cfg = make_cfg(stock_price=100.0)
    lens = valuation_lenses.compute_reverse_dcf_lens(cfg)
    assert lens["fv_low"] == 100.0
    assert lens["fv_mid"] == 100.0
    assert lens["fv_high"] == 100.0
    assert "implied_growth" in lens["details"]
    assert "implied_margin" in lens["details"]
    assert isinstance(lens["details"]["implied_growth"], float)
```

- [ ] **Step 2: Run test — should fail**

Run: `python3 -m pytest tests/test_multi_lens.py::test_reverse_dcf_lens_anchors_at_stock_price -v`
Expected: `AttributeError: module 'valuation_lenses' has no attribute 'compute_reverse_dcf_lens'`.

- [ ] **Step 3: Implement the reverse DCF lens**

Append to `valuation_lenses.py`:

```python
def compute_reverse_dcf_lens(cfg):
    """Reverse DCF. Single anchor at current price — answers 'what's priced in'.

    Always returns a result given a valid config (stock_price > 0). The lens
    isn't a fair-value estimate; its low weight reflects that.
    """
    reverse = dcf_calculator.compute_reverse_dcf(cfg)
    fv = cfg["stock_price"]
    return {
        "fv_low": fv,
        "fv_mid": fv,
        "fv_high": fv,
        "details": {
            "implied_growth": reverse["implied_growth"],
            "implied_margin": reverse["implied_margin"],
        },
    }
```

- [ ] **Step 4: Run test — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py::test_reverse_dcf_lens_anchors_at_stock_price -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: reverse DCF lens (current-price anchor)"
```

---

### Task 9: Multiples lens

**Files:**
- Modify: `valuation_lenses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_multi_lens.py`:

```python
def test_multiples_lens_returns_none_when_no_inputs():
    cfg = make_cfg()  # no valuation_inputs, empty peers
    assert valuation_lenses.compute_multiples_lens(cfg) is None


def test_multiples_lens_own_pe_only():
    cfg = make_cfg(
        valuation_inputs={"forward_eps": 5.0, "historical_fwd_pe": 20.0},
    )
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is not None
    # Only own_pe anchor (5.0 * 20.0 = 100.0); no peer/ev_ebitda data
    assert lens["fv_mid"] == pytest.approx(100.0)
    assert lens["fv_low"] == pytest.approx(100.0)
    assert lens["fv_high"] == pytest.approx(100.0)
    assert lens["details"]["fwd_pe_own"] == pytest.approx(100.0)
    assert lens["details"]["fwd_pe_peer_median"] is None
    assert lens["details"]["ev_ebitda_peer_median"] is None
    assert any("fwd_pe_peer" in s for s in lens["details"]["skipped"])
    assert any("ev_ebitda_peer" in s for s in lens["details"]["skipped"])


def test_multiples_lens_peer_pe_and_ev_ebitda():
    peers = [
        make_peer(ticker="P1", fwd_pe=18.0, ev_ebitda=10.0,
                  op_margin=0.18, rev_growth=0.04),
        make_peer(ticker="P2", fwd_pe=20.0, ev_ebitda=12.0,
                  op_margin=0.20, rev_growth=0.05),
        make_peer(ticker="P3", fwd_pe=22.0, ev_ebitda=14.0,
                  op_margin=0.22, rev_growth=0.06),
    ]
    cfg = make_cfg(
        peers=peers,
        valuation_inputs=dict(SAMPLE_VALUATION_INPUTS),
    )
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is not None
    # Median fwd_pe = 20.0, median ev_ebitda = 12.0, forward_eps=5.0
    expected_pe_median = 20.0 * 5.0  # = 100
    expected_ev_median = (12.0 * 12_000.0 - (10_000 - 5_000 - 0)) / 1_000  # = (144000-5000)/1000 = 139
    assert lens["details"]["fwd_pe_peer_median"] == pytest.approx(expected_pe_median)
    assert lens["details"]["ev_ebitda_peer_median"] == pytest.approx(expected_ev_median)
    # closest_peer must be one of the peer tickers
    assert lens["details"]["closest_peer"] in {"P1", "P2", "P3"}
    # fv range spans all anchors
    assert lens["fv_low"] < lens["fv_high"]
    assert lens["fv_low"] <= lens["fv_mid"] <= lens["fv_high"]


def test_multiples_lens_partial_inputs_skips_components():
    cfg = make_cfg(
        peers=[make_peer(fwd_pe=None, ev_ebitda=12.0)],
        valuation_inputs={"ttm_ebitda": 12_000.0},  # only ev/ebitda usable
    )
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is not None
    assert lens["details"]["fwd_pe_own"] is None
    assert lens["details"]["fwd_pe_peer_median"] is None
    assert lens["details"]["ev_ebitda_peer_median"] is not None
```

- [ ] **Step 2: Run tests — should fail**

Run: `python3 -m pytest tests/test_multi_lens.py -k multiples -v`
Expected: `AttributeError: module 'valuation_lenses' has no attribute 'compute_multiples_lens'`.

- [ ] **Step 3: Implement the multiples lens**

Append to `valuation_lenses.py`:

```python
def _closest_peer_ticker(peers, target_op_margin, target_rev_growth):
    """Return the ticker of the peer with smallest weighted Euclidean
    distance on (op_margin, rev_growth). Informational only.
    """
    best_ticker, best_score = None, float("inf")
    for p in peers:
        om = p.get("op_margin")
        rg = p.get("rev_growth")
        if om is None or rg is None:
            continue
        score = (om - target_op_margin) ** 2 + (rg - target_rev_growth) ** 2
        if score < best_score:
            best_score = score
            best_ticker = p.get("ticker")
    return best_ticker


def compute_multiples_lens(cfg):
    """Trading-multiples lens with three independent sub-anchors:

    A) own historical forward P/E × forward_eps
    B) peer-set forward P/E (median, min, max) × forward_eps
    C) peer-set EV/EBITDA (median, min, max) × ttm_ebitda - net_debt → /shares

    Sub-anchors silently skipped when their inputs are missing. Lens fully
    returns None when all three skip.
    """
    inputs = cfg.get("valuation_inputs") or {}
    peers = cfg.get("peers") or []

    fv_anchors = []
    details = {
        "fwd_pe_own": None,
        "fwd_pe_peer_median": None,
        "ev_ebitda_peer_median": None,
        "closest_peer": None,
        "skipped": [],
    }

    forward_eps = inputs.get("forward_eps")
    historical_fwd_pe = inputs.get("historical_fwd_pe")
    ttm_ebitda = inputs.get("ttm_ebitda")

    # A) own historical forward P/E
    if forward_eps and historical_fwd_pe:
        own_fv = historical_fwd_pe * forward_eps
        fv_anchors.append(own_fv)
        details["fwd_pe_own"] = own_fv
    else:
        reason = "fwd_pe_own (forward_eps or historical_fwd_pe missing)"
        details["skipped"].append(reason)
        logger.info("Multiples lens: skipping %s", reason)

    # B) peer fwd P/E
    peer_fwd_pes = [p["fwd_pe"] for p in peers if p.get("fwd_pe")]
    if peer_fwd_pes and forward_eps:
        median_pe = statistics.median(peer_fwd_pes)
        fv_low_p = min(peer_fwd_pes) * forward_eps
        fv_mid_p = median_pe * forward_eps
        fv_high_p = max(peer_fwd_pes) * forward_eps
        fv_anchors.extend([fv_low_p, fv_mid_p, fv_high_p])
        details["fwd_pe_peer_median"] = fv_mid_p
        # informational closest peer
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
    peer_ev_ebitdas = [p["ev_ebitda"] for p in peers if p.get("ev_ebitda")]
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
        logger.info("Multiples lens fully skipped (no anchors)")
        return None

    return {
        "fv_low": min(fv_anchors),
        "fv_mid": sum(fv_anchors) / len(fv_anchors),
        "fv_high": max(fv_anchors),
        "details": details,
    }
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py -k multiples -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: trading-multiples lens (own P/E + peer median + EV/EBITDA)"
```

---

### Task 10: Orchestrator (`calculate_multi_lens_valuation`)

**Files:**
- Modify: `valuation_lenses.py`

- [ ] **Step 1: Write failing tests (acceptance tests 1 & 2)**

Append to `tests/test_multi_lens.py`:

```python
def test_dcf_only_fallback_when_no_valuation_inputs():
    """Acceptance #1: config without valuation_inputs → DCF-only summary,
    weights renormalized to 1.0."""
    cfg = make_cfg()  # no inputs, no peers with multiples
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)
    lenses = summary["lenses"]
    assert lenses["dcf"] is not None
    assert lenses["multiples"] is None
    assert lenses["reverse_dcf"] is not None  # always active
    # Active = dcf + reverse_dcf, weights renormalized
    assert lenses["dcf"]["weight_normalized"] + lenses["reverse_dcf"]["weight_normalized"] \
        == pytest.approx(1.0)
    # weighted_fv must lie between dcf & reverse_dcf
    mids = [lenses["dcf"]["fv_mid"], lenses["reverse_dcf"]["fv_mid"]]
    assert min(mids) <= summary["weighted_fv_mid"] <= max(mids)


def test_all_lenses_active_weighted_in_range():
    """Acceptance #2: full config → 3 active lenses, weighted FV in [min,max]
    of individual lens mids."""
    peers = [
        make_peer(ticker="P1", fwd_pe=18.0, ev_ebitda=10.0),
        make_peer(ticker="P2", fwd_pe=20.0, ev_ebitda=12.0),
        make_peer(ticker="P3", fwd_pe=22.0, ev_ebitda=14.0),
    ]
    cfg = make_cfg(
        peers=peers,
        valuation_inputs=dict(SAMPLE_VALUATION_INPUTS),
    )
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)
    lenses = summary["lenses"]
    active = [n for n in ("dcf", "multiples", "reverse_dcf") if lenses[n] is not None]
    assert active == ["dcf", "multiples", "reverse_dcf"]

    mids = [lenses[n]["fv_mid"] for n in active]
    assert min(mids) <= summary["weighted_fv_mid"] <= max(mids)
    assert summary["buy_price"] == pytest.approx(
        summary["weighted_fv_mid"] * (1 - cfg["margin_of_safety"]), rel=1e-9
    )
    # current_vs_mid signed correctly
    expected_cvm = (cfg["stock_price"] - summary["weighted_fv_mid"]) / summary["weighted_fv_mid"]
    assert summary["current_vs_mid"] == pytest.approx(expected_cvm, rel=1e-3)
    # weights sum to 1.0
    total_norm = sum(lenses[n]["weight_normalized"] for n in active)
    assert total_norm == pytest.approx(1.0)
    # dividend lens stays None
    assert lenses["dividend"] is None


def test_lens_weights_override_from_config():
    cfg = make_cfg(
        peers=[make_peer(fwd_pe=20.0, ev_ebitda=12.0)],
        valuation_inputs=dict(SAMPLE_VALUATION_INPUTS),
        lens_weights={"dcf": 0.5, "multiples": 0.5, "reverse_dcf": 0.0, "dividend": 0.0},
    )
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)
    # reverse_dcf has weight 0 → normalized 0 → drops out of weighted FV
    assert summary["lenses"]["reverse_dcf"]["weight_normalized"] == 0.0
    assert summary["lenses"]["dcf"]["weight_normalized"] == pytest.approx(0.5)
    assert summary["lenses"]["multiples"]["weight_normalized"] == pytest.approx(0.5)
```

- [ ] **Step 2: Run tests — should fail**

Run: `python3 -m pytest tests/test_multi_lens.py -k "fallback or all_lenses or weights_override" -v`
Expected: `AttributeError: module 'valuation_lenses' has no attribute 'calculate_multi_lens_valuation'`.

- [ ] **Step 3: Implement the orchestrator**

Append to `valuation_lenses.py`:

```python
def calculate_multi_lens_valuation(cfg, scenario_grid=False):
    """Run all lenses and return the valuation_summary dict.

    Pure function — does not mutate cfg, does not persist anywhere. Caller
    is responsible for storing the summary back to the config.
    """
    lenses = {
        "dcf":         compute_dcf_lens(cfg, scenario_grid=scenario_grid),
        "multiples":   compute_multiples_lens(cfg),
        "reverse_dcf": compute_reverse_dcf_lens(cfg),
        "dividend":    compute_dividend_lens(cfg),
    }

    weights_cfg = cfg.get("lens_weights") or DEFAULT_LENS_WEIGHTS
    active_names = [n for n, l in lenses.items() if l is not None]
    raw = {n: weights_cfg.get(n, DEFAULT_LENS_WEIGHTS.get(n, 0.0)) for n in active_names}
    total = sum(raw.values()) or 1.0
    norm = {n: w / total for n, w in raw.items()}

    for n in active_names:
        lenses[n]["weight"] = raw[n]
        lenses[n]["weight_normalized"] = norm[n]

    weighted_low = sum(lenses[n]["fv_low"] * norm[n] for n in active_names)
    weighted_mid = sum(lenses[n]["fv_mid"] * norm[n] for n in active_names)
    weighted_high = sum(lenses[n]["fv_high"] * norm[n] for n in active_names)

    mos = cfg.get("margin_of_safety", 0.20)
    price = cfg["stock_price"]
    cvm = ((price - weighted_mid) / weighted_mid) if weighted_mid else 0.0

    return {
        "calculated_at": datetime.now(timezone.utc).isoformat(),
        "stock_price": price,
        "scenario_grid": scenario_grid,
        "lenses": lenses,
        "weighted_fv_low":  round(weighted_low, 2),
        "weighted_fv_mid":  round(weighted_mid, 2),
        "weighted_fv_high": round(weighted_high, 2),
        "current_vs_mid":   round(cvm, 4),
        "buy_price":        round(weighted_mid * (1 - mos), 2),
    }
```

- [ ] **Step 4: Run tests — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py -k "fallback or all_lenses or weights_override" -v`
Expected: 3 passed.

- [ ] **Step 5: Run full test file**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add valuation_lenses.py tests/test_multi_lens.py
git commit -m "feat: multi-lens orchestrator with weight renormalization"
```

---

### Task 11: Enrich `config_store.list_watchlist`

**Files:**
- Modify: `config_store.py:117-140` (the `list_watchlist` function)

- [ ] **Step 1: Write failing test (acceptance #3)**

Append to `tests/test_multi_lens.py`:

```python
def test_list_watchlist_enriched_shape():
    """Acceptance #3: list_watchlist returns dicts with all new fields,
    None when missing rather than absent."""
    summary = {
        "weighted_fv_low": 60.0,
        "weighted_fv_mid": 80.0,
        "weighted_fv_high": 100.0,
        "buy_price": 64.0,
        "current_vs_mid": 0.10,
        "lenses": {"dcf": {}, "multiples": {}, "reverse_dcf": {}, "dividend": None},
    }
    rows = [
        {
            "ticker": "WITH",
            "company": "With Co",
            "stock_price": 90.0,
            "updated_at": "2026-05-05T00:00:00Z",
            "config": {
                "valuation_summary": summary,
                "ai_notes": {
                    "Scorecard": '```json\n{"verdict":"deep_dive","phase":{"number":3,"name":"S"}}\n```'
                },
            },
        },
        {
            "ticker": "WITHOUT",
            "company": "Without Co",
            "stock_price": 50.0,
            "updated_at": "2026-05-05T00:00:00Z",
            "config": {},  # no valuation_summary, no ai_notes
        },
    ]

    fake_resp = MagicMock(data=rows)
    fake_query = MagicMock()
    fake_query.eq.return_value = fake_query
    fake_query.execute.return_value = fake_resp
    fake_table = MagicMock()
    fake_table.select.return_value = fake_query
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    out = config_store.list_watchlist(fake_client, user_id="u1")
    expected_keys = {
        "ticker", "company", "updated", "stock_price",
        "fv_low", "fv_mid", "fv_high", "buy_price",
        "current_vs_mid", "lens_count", "verdict", "phase",
    }
    for row in out:
        assert set(row.keys()) == expected_keys

    with_row = next(r for r in out if r["ticker"] == "WITH")
    assert with_row["fv_mid"] == 80.0
    assert with_row["fv_low"] == 60.0
    assert with_row["fv_high"] == 100.0
    assert with_row["buy_price"] == 64.0
    assert with_row["current_vs_mid"] == 0.10
    assert with_row["lens_count"] == 3  # dcf, multiples, reverse_dcf (dividend None)
    assert with_row["verdict"] == "deep_dive"
    assert with_row["phase"] == 3

    without_row = next(r for r in out if r["ticker"] == "WITHOUT")
    assert without_row["fv_mid"] is None
    assert without_row["fv_low"] is None
    assert without_row["fv_high"] is None
    assert without_row["buy_price"] is None
    assert without_row["current_vs_mid"] is None
    assert without_row["lens_count"] == 0
    assert without_row["verdict"] is None
    assert without_row["phase"] is None
```

- [ ] **Step 2: Run test — should fail**

Run: `python3 -m pytest tests/test_multi_lens.py::test_list_watchlist_enriched_shape -v`
Expected: FAIL — keys missing or `select` was called with the old short field list.

- [ ] **Step 3: Modify `list_watchlist`**

In `config_store.py`, replace the existing `list_watchlist` function (lines 117-140) with:

```python
def list_watchlist(client, user_id=None):
    """Return list of dicts with ticker metadata + valuation summary.

    Each entry has these keys (always present; values may be None):
        ticker, company, updated, stock_price,
        fv_low, fv_mid, fv_high, buy_price, current_vs_mid, lens_count,
        verdict, phase

    Configs without ``valuation_summary`` show only base fields populated;
    run ``calculate_multi_lens_valuation`` to populate the rest.
    """
    from scorecard_utils import parse_scorecard

    query = (
        client.table("watchlist_configs")
        .select("ticker, company, stock_price, updated_at, config")
    )
    if user_id is not None:
        query = query.eq("user_id", user_id)
    resp = query.execute()
    if not (resp and resp.data):
        return []

    out = []
    for row in resp.data:
        cfg = row.get("config") or {}
        summary = cfg.get("valuation_summary") or {}
        lenses = summary.get("lenses") or {}
        lens_count = sum(1 for v in lenses.values() if v)

        scorecard = parse_scorecard(cfg.get("ai_notes"))

        out.append({
            "ticker": row["ticker"],
            "company": row.get("company", row["ticker"]),
            "updated": row.get("updated_at", ""),
            "stock_price": row.get("stock_price", 0),
            "fv_low":  summary.get("weighted_fv_low"),
            "fv_mid":  summary.get("weighted_fv_mid"),
            "fv_high": summary.get("weighted_fv_high"),
            "buy_price": summary.get("buy_price"),
            "current_vs_mid": summary.get("current_vs_mid"),
            "lens_count": lens_count,
            "verdict": scorecard["verdict"],
            "phase":   scorecard["phase"],
        })
    return out
```

- [ ] **Step 4: Run test — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py::test_list_watchlist_enriched_shape -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config_store.py tests/test_multi_lens.py
git commit -m "feat: list_watchlist returns multi-lens valuation summary"
```

---

### Task 12: New MCP tool `calculate_multi_lens_valuation`

**Files:**
- Modify: `mcp_server.py` (insert new impl + tool decorator)

- [ ] **Step 1: Write failing test (acceptance #4 round-trip)**

Append to `tests/test_multi_lens.py`:

```python
def test_round_trip_calculate_and_persist(monkeypatch):
    """Acceptance #4: compute → save → list shows the same fv_mid."""
    import mcp_server

    # In-memory "Supabase": one config row keyed by ticker
    storage = {
        "TEST": {
            "company": "Test Co",
            "ticker": "TEST",
            "stock_price": 100.0,
            "ai_notes": {},
            "peers": [],
            **make_cfg(),
        },
    }

    def fake_load(client, ticker, user_id=None):
        return dict(storage[ticker.upper()])

    def fake_save(client, ticker, cfg, user_id=None):
        storage[ticker.upper()] = dict(cfg)

    def fake_list(client, user_id=None):
        out = []
        from scorecard_utils import parse_scorecard
        for tk, cfg in storage.items():
            summary = cfg.get("valuation_summary") or {}
            lenses = summary.get("lenses") or {}
            lens_count = sum(1 for v in lenses.values() if v)
            sc = parse_scorecard(cfg.get("ai_notes"))
            out.append({
                "ticker": tk,
                "company": cfg.get("company", tk),
                "updated": "",
                "stock_price": cfg.get("stock_price", 0),
                "fv_low": summary.get("weighted_fv_low"),
                "fv_mid": summary.get("weighted_fv_mid"),
                "fv_high": summary.get("weighted_fv_high"),
                "buy_price": summary.get("buy_price"),
                "current_vs_mid": summary.get("current_vs_mid"),
                "lens_count": lens_count,
                "verdict": sc["verdict"],
                "phase": sc["phase"],
            })
        return out

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server.config_store, "list_watchlist", fake_list)
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    import json as _json
    result_json = mcp_server._calculate_multi_lens_valuation_impl("TEST", scenario_grid=False)
    result = _json.loads(result_json)
    expected_mid = result["weighted_fv_mid"]

    # round trip via list_watchlist
    listed = _json.loads(mcp_server._get_watchlist_impl())
    test_row = next(r for r in listed if r["ticker"] == "TEST")
    assert test_row["fv_mid"] == expected_mid
    assert test_row["lens_count"] >= 1
```

- [ ] **Step 2: Run test — should fail**

Run: `python3 -m pytest tests/test_multi_lens.py::test_round_trip_calculate_and_persist -v`
Expected: `AttributeError: module 'mcp_server' has no attribute '_calculate_multi_lens_valuation_impl'`.

- [ ] **Step 3: Add the impl + decorated tool to `mcp_server.py`**

In `mcp_server.py`, **add an import** at the top of the imports block (next to the existing `import dcf_calculator` line, ~line 61):

```python
import valuation_lenses
```

Then add this impl below `_calculate_valuation_impl` (around line 218):

```python
def _calculate_multi_lens_valuation_impl(ticker, scenario_grid=False):
    """Core logic for calculate_multi_lens_valuation: load cfg, run all
    lenses, persist summary, return JSON."""
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=USER_ID)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not on watchlist"})

    summary = valuation_lenses.calculate_multi_lens_valuation(
        cfg, scenario_grid=scenario_grid
    )
    cfg["valuation_summary"] = summary
    config_store.save_config(client, ticker, cfg, user_id=USER_ID)
    return json.dumps(summary, default=str)
```

Then add the decorated tool below `calculate_valuation` (around line 317):

```python
@mcp.tool()
def calculate_multi_lens_valuation(ticker: str, scenario_grid: bool = False) -> str:
    """Run multi-lens fair value (DCF + Trading Multiples + Reverse DCF)
    for a watchlist ticker and persist the summary to Supabase.

    Use this after editing valuation_inputs or peers to refresh the
    fair value estimate. The result is also surfaced via get_watchlist().

    Args:
        ticker: Stock ticker symbol (e.g. "ABT")
        scenario_grid: If True, run a 4x4 bull/bear DCF scenario grid for
            the DCF lens fv_low/fv_high. Default False uses ±15% bands
            around the base intrinsic.

    Returns:
        JSON valuation_summary dict. See spec for schema.
    """
    try:
        return _calculate_multi_lens_valuation_impl(ticker, scenario_grid)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Run test — should pass**

Run: `python3 -m pytest tests/test_multi_lens.py::test_round_trip_calculate_and_persist -v`
Expected: PASS.

- [ ] **Step 5: Run full test file**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py tests/test_multi_lens.py
git commit -m "feat: MCP tool calculate_multi_lens_valuation with persistence"
```

---

### Task 13: Regression test — `calculate_valuation` unchanged + lint + full suite

**Files:**
- Modify: `tests/test_multi_lens.py` (add acceptance test #5)

- [ ] **Step 1: Write the no-regression test**

Append to `tests/test_multi_lens.py`:

```python
def test_calculate_valuation_impl_shape_unchanged():
    """Acceptance #5: existing single-DCF calculate_valuation() output
    keys/shape unchanged by this change."""
    import json as _json
    import mcp_server

    cfg = make_cfg()
    out = _json.loads(mcp_server._calculate_valuation_impl(cfg))
    expected_keys = {
        "wacc", "intrinsic_value", "buy_price", "enterprise_value",
        "equity_value", "tv_pct", "implied_growth", "implied_margin",
        "market_price", "valuation_basis",
    }
    # closest_growth/margin are added when reverse closest is found — optional
    assert expected_keys.issubset(set(out.keys()))
    assert isinstance(out["wacc"], float)
    assert isinstance(out["intrinsic_value"], float)
    assert out["valuation_basis"] == "nominal"
```

- [ ] **Step 2: Run the new test — should pass directly (no code changes)**

Run: `python3 -m pytest tests/test_multi_lens.py::test_calculate_valuation_impl_shape_unchanged -v`
Expected: PASS.

- [ ] **Step 3: Run full multi-lens test file**

Run: `python3 -m pytest tests/test_multi_lens.py -v`
Expected: all tests pass.

- [ ] **Step 4: Run lint over the whole repo**

Run: `python3 -m ruff check .`
Expected: All checks passed!

If failures show up in the new files, fix them inline (most likely culprits: unused imports, line length). Re-run until clean.

- [ ] **Step 5: Run full regression suite (existing IBKR + tastytrade tests)**

Run: `python3 -m pytest test_tastytrade_api.py test_ibkr_api.py tests/test_multi_lens.py -v`
Expected: 81 (existing) + multi-lens count, all PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_multi_lens.py
git commit -m "test: regression test for calculate_valuation shape"
```

---

## Summary of Commits (target sequence)

1. `test: pytest conftest for tests/ subfolder`
2. `feat: scorecard_utils module with shared JSON parser`
3. `refactor: streamlit_app uses shared scorecard_utils parser`
4. `feat: guard valuation_inputs/summary/lens_weights from silent wipes`
5. `feat: valuation_lenses module skeleton + dividend stub`
6. `feat: DCF lens (±15% band mode)`
7. `feat: DCF lens scenario grid (4x4 bull/bear)`
8. `feat: reverse DCF lens (current-price anchor)`
9. `feat: trading-multiples lens (own P/E + peer median + EV/EBITDA)`
10. `feat: multi-lens orchestrator with weight renormalization`
11. `feat: list_watchlist returns multi-lens valuation summary`
12. `feat: MCP tool calculate_multi_lens_valuation with persistence`
13. `test: regression test for calculate_valuation shape`

13 commits, ~13 logical units. After Task 13 the work is shippable.
