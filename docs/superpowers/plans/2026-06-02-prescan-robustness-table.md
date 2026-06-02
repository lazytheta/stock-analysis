# Pre-Scan Robustness Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Prasad-style robustness table at the top of the Pre-Scan tab that scores 6 axes on a most↔least-robust continuum and derives a weakest-link verdict that becomes the authoritative watchlist verdict.

**Architecture:** A pure `robustness.py` module does all scoring (band thresholds + weakest-link verdict, no I/O). Data axes (ROCE, net debt) come from the existing `_compute_fundamentals_headline`; the 4 qualitative axes come from a new "Robustness" prescan section (JSON-in-markdown, parsed like the Scorecard). The merged table + derived verdict are cached in `cfg['robustness']`; `list_watchlist` prefers that verdict.

**Tech Stack:** Python 3.14, Streamlit, Supabase (JSONB configs), pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-06-02-prescan-robustness-table-design.md`

---

## File Structure

**New:**
- `robustness.py` — pure scoring + verdict logic (band thresholds, axis merge, weakest-link derivation).
- `tests/test_robustness.py` — unit tests for the above.

**Modified:**
- `streamlit_app.py` — add the "Robustness" prompt to `DEFAULT_AI_PROMPTS`; add `_render_robustness_table` + override editor; render it at the top of the Pre-Scan tab.
- `config_store.py` — guard `robustness`; make `list_watchlist` prefer the robustness verdict.
- `scorecard_utils.py` — add `resolve_verdict(cfg)` helper.
- `mcp_server.py` — add `_set_robustness_impl` + `set_robustness` tool.
- `lazytheta-mcp-cloudrun/mcp_handler.py` — register the `set_robustness` tool.

---

## Task 1: Band thresholds (robustness.py core)

**Files:**
- Create: `robustness.py`
- Test: `tests/test_robustness.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_robustness.py
import robustness


def test_band_for_roce_gate():
    # never green under 20% (the Prasad gate)
    assert robustness.band_for_roce(25.0) == "robust"
    assert robustness.band_for_roce(20.0) == "robust"
    assert robustness.band_for_roce(19.9) == "mid"
    assert robustness.band_for_roce(12.0) == "mid"
    assert robustness.band_for_roce(11.9) == "fragile"
    assert robustness.band_for_roce(None) == "fragile"


def test_band_for_net_debt():
    assert robustness.band_for_net_debt(-0.5) == "robust"   # net cash
    assert robustness.band_for_net_debt(1.0) == "robust"
    assert robustness.band_for_net_debt(1.5) == "mid"
    assert robustness.band_for_net_debt(2.0) == "mid"
    assert robustness.band_for_net_debt(2.5) == "fragile"
    # EBITDA ratio unknown → fall back to net-debt sign
    assert robustness.band_for_net_debt(None, net_debt_m=-100.0) == "robust"
    assert robustness.band_for_net_debt(None, net_debt_m=500.0) == "mid"
    assert robustness.band_for_net_debt(None, net_debt_m=None) == "mid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_robustness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'robustness'`

- [ ] **Step 3: Write minimal implementation**

```python
# robustness.py
"""Prasad robustness table — pure scoring + weakest-link verdict.

No I/O. Data axes (ROCE, net debt) are fed from the fundamentals headline
(mcp_server._compute_fundamentals_headline); the 4 qualitative axes come from
the AI 'Robustness' prescan section. See
docs/superpowers/specs/2026-06-02-prescan-robustness-table-design.md
"""
from scorecard_utils import parse_scorecard_json

# key -> (label, is_deal_breaker, source)
AXES = (
    ("roce",       "ROCE (5–10y)",          True,  "data"),
    ("net_debt",   "Net debt",               True,  "data"),
    ("customers",  "Customer/supplier base", False, "ai"),
    ("barriers",   "Competitive barriers",   False, "ai"),
    ("management", "Management",             True,  "ai"),
    ("industry",   "Industry change",        False, "ai"),
)
DEAL_BREAKERS = tuple(k for k, _, db, _ in AXES if db)
AI_AXES = tuple(k for k, _, _, src in AXES if src == "ai")
BANDS = ("robust", "mid", "fragile")
_VERDICT_MAP = {"robust": "deep_dive", "borderline": "revisit", "fragile": "pass"}


def band_for_roce(pct, metric="ROCE"):
    """≥20% robust, 12–<20% mid, <12% fragile, None → fragile.
    Never green under 20% — the Prasad quality gate."""
    if pct is None:
        return "fragile"
    if pct >= 20:
        return "robust"
    if pct >= 12:
        return "mid"
    return "fragile"


def band_for_net_debt(nd_ebitda, net_debt_m=None):
    """net cash or ≤1x EBITDA → robust, 1–2x → mid, >2x → fragile.
    If the EBITDA ratio is unknown, fall back to net-debt sign."""
    if nd_ebitda is None:
        if net_debt_m is None:
            return "mid"
        return "robust" if net_debt_m <= 0 else "mid"
    if nd_ebitda <= 1:
        return "robust"
    if nd_ebitda <= 2:
        return "mid"
    return "fragile"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_robustness.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add robustness.py tests/test_robustness.py
git commit -m "feat(robustness): band thresholds for ROCE + net-debt axes"
```

---

## Task 2: Weakest-link verdict derivation

**Files:**
- Modify: `robustness.py`
- Test: `tests/test_robustness.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_robustness.py
def _axes(**bands):
    """Build an axes dict; unspecified axes default to 'robust'."""
    out = {}
    for key, _, _, src in robustness.AXES:
        out[key] = {"band": bands.get(key, "robust"), "source": src}
    return out


def test_verdict_all_robust():
    v = robustness.derive_verdict(_axes())
    assert v["verdict"] == "robust"
    assert v["verdict_mapped"] == "deep_dive"


def test_verdict_management_red_is_fragile():
    v = robustness.derive_verdict(_axes(management="fragile"))
    assert v["verdict"] == "fragile"
    assert v["verdict_mapped"] == "pass"


def test_verdict_roce_gate_caps_at_borderline():
    # ROCE mid (12–20%) with everything else green → cannot be robust
    v = robustness.derive_verdict(_axes(roce="mid"))
    assert v["verdict"] == "borderline"
    assert v["verdict_mapped"] == "revisit"
    assert "gate" in v["verdict_reason"].lower()


def test_verdict_net_debt_amber_is_borderline():
    v = robustness.derive_verdict(_axes(net_debt="mid"))
    assert v["verdict"] == "borderline"


def test_verdict_disney_exception_softens_industry():
    # industry red + barriers green → softened to mid → stays robust
    v = robustness.derive_verdict(_axes(industry="fragile", barriers="robust"))
    assert v["verdict"] == "robust"


def test_verdict_two_noncritical_reds_is_borderline():
    # customers + industry red, barriers mid (no Disney softening) → borderline
    v = robustness.derive_verdict(_axes(customers="fragile", industry="fragile", barriers="mid"))
    assert v["verdict"] == "borderline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_robustness.py -q`
Expected: FAIL — `AttributeError: module 'robustness' has no attribute 'derive_verdict'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to robustness.py
def _apply_disney_exception(axes):
    """Industry \U0001f534 + barriers \U0001f7e2 → soften industry to \U0001f7e1 (a strong moat makes a
    fast-moving industry predictable enough — per the user's DIS note).
    Returns a deep-ish copy; does not mutate the input."""
    axes = {k: dict(v) for k, v in axes.items()}
    ind, bar = axes.get("industry", {}), axes.get("barriers", {})
    if ind.get("band") == "fragile" and bar.get("band") == "robust":
        ind["band"] = "mid"
        ind["note"] = (ind.get("note", "") + " [softened: strong moat]").strip()
        axes["industry"] = ind
    return axes


def derive_verdict(axes):
    """Weakest-link, Type-1-averse verdict from merged axes (overrides already
    applied). Returns {'verdict','verdict_mapped','verdict_reason'}.
    First matching rule wins."""
    axes = _apply_disney_exception(axes)

    def band(k):
        return axes.get(k, {}).get("band", "mid")

    red_db = [k for k in DEAL_BREAKERS if band(k) == "fragile"]
    noncrit_red = sum(1 for k, _, db, _ in AXES if not db and band(k) == "fragile")

    if red_db:
        verdict, reason = "fragile", f"deal-breaker red: {', '.join(red_db)}"
    elif band("roce") != "robust":
        verdict, reason = "borderline", "ROCE below the 20% gate"
    elif any(band(k) == "mid" for k in DEAL_BREAKERS) or noncrit_red >= 2:
        amber_db = [k for k in DEAL_BREAKERS if band(k) == "mid"]
        reason = (f"deal-breaker amber: {', '.join(amber_db)}" if amber_db
                  else "two or more non-critical axes fragile")
        verdict = "borderline"
    else:
        verdict, reason = "robust", "all deal-breakers green"

    return {"verdict": verdict, "verdict_mapped": _VERDICT_MAP[verdict],
            "verdict_reason": reason}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_robustness.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add robustness.py tests/test_robustness.py
git commit -m "feat(robustness): weakest-link verdict + Disney exception"
```

---

## Task 3: Axis assembly (data + AI + overrides)

**Files:**
- Modify: `robustness.py`
- Test: `tests/test_robustness.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_robustness.py
import json


def test_compute_data_axes_from_headline():
    headline = {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                "latest_net_debt_ebitda": -0.4, "latest_adjusted_net_debt_m": -20000.0}
    axes = robustness.compute_data_axes(headline)
    assert axes["roce"]["band"] == "robust"
    assert axes["roce"]["value"] == 30.0
    assert axes["net_debt"]["band"] == "robust"


def test_parse_ai_axes_from_json_section():
    ai_notes = {"Robustness": json.dumps({"axes": {
        "customers": {"band": "robust", "note": "millions of advertisers"},
        "barriers": {"band": "robust", "note": "wide moat"},
        "management": {"band": "mid", "note": "founder control"},
        "industry": {"band": "fragile", "note": "fast AI shifts"},
    }})}
    ai = robustness.parse_ai_axes(ai_notes)
    assert ai["management"]["band"] == "mid"
    assert ai["industry"]["band"] == "fragile"


def test_parse_ai_axes_missing_returns_empty():
    assert robustness.parse_ai_axes({}) == {}
    assert robustness.parse_ai_axes({"Robustness": "no json here"}) == {}


def test_build_table_merges_and_derives():
    headline = {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                "latest_net_debt_ebitda": -0.4, "latest_adjusted_net_debt_m": -20000.0}
    ai_notes = {"Robustness": json.dumps({"axes": {
        "customers": {"band": "robust"}, "barriers": {"band": "robust"},
        "management": {"band": "robust"}, "industry": {"band": "fragile"}}})}
    table = robustness.build_table(headline, ai_notes)
    # industry red + barriers green → Disney softens → robust
    assert table["verdict"] == "robust"
    assert table["axes_base"]["industry"]["band"] == "fragile"  # base unchanged


def test_build_table_override_wins():
    headline = {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                "latest_net_debt_ebitda": -0.4, "latest_adjusted_net_debt_m": -20000.0}
    ai_notes = {"Robustness": json.dumps({"axes": {
        "customers": {"band": "robust"}, "barriers": {"band": "robust"},
        "management": {"band": "robust"}, "industry": {"band": "robust"}}})}
    table = robustness.build_table(headline, ai_notes, overrides={"management": "fragile"})
    assert table["axes"]["management"]["band"] == "fragile"
    assert table["axes"]["management"]["source"] == "override"
    assert table["verdict"] == "fragile"


def test_resolve_reapplies_overrides_without_headline():
    base = {k: {"band": "robust", "source": s} for k, _, _, s in robustness.AXES}
    effective, verdict = robustness.resolve(base, {"net_debt": "fragile"})
    assert effective["net_debt"]["band"] == "fragile"
    assert verdict["verdict"] == "fragile"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_robustness.py -q`
Expected: FAIL — `AttributeError: module 'robustness' has no attribute 'compute_data_axes'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to robustness.py
def compute_data_axes(headline):
    """Two data-driven axes from a fundamentals headline dict
    (mcp_server._compute_fundamentals_headline)."""
    roce_pct = headline.get("avg_roce_pct")
    metric = headline.get("roce_metric", "ROCE")
    nd_ebitda = headline.get("latest_net_debt_ebitda")
    nd_m = headline.get("latest_adjusted_net_debt_m")
    return {
        "roce": {"band": band_for_roce(roce_pct, metric), "value": roce_pct,
                 "metric": metric, "source": "data"},
        "net_debt": {"band": band_for_net_debt(nd_ebitda, nd_m), "value": nd_ebitda,
                     "net_debt_m": nd_m, "unit": "x_ebitda", "source": "data"},
    }


def parse_ai_axes(ai_notes):
    """Extract the 4 qualitative axes from ai_notes['Robustness'] JSON.
    Returns {} when absent/unparseable (callers default missing axes to 'mid')."""
    if not isinstance(ai_notes, dict):
        return {}
    data = parse_scorecard_json(ai_notes.get("Robustness"))
    if not isinstance(data, dict):
        return {}
    axes = data.get("axes") if isinstance(data.get("axes"), dict) else data
    out = {}
    for key in AI_AXES:
        entry = axes.get(key) if isinstance(axes, dict) else None
        if isinstance(entry, dict) and entry.get("band") in BANDS:
            out[key] = {"band": entry["band"], "note": entry.get("note", ""), "source": "ai"}
    return out


def merge_base_axes(headline, ai_notes):
    """Data axes + AI axes (no overrides). Missing AI axes default to 'mid'."""
    axes = dict(compute_data_axes(headline))
    ai = parse_ai_axes(ai_notes)
    for key in AI_AXES:
        axes[key] = ai.get(key, {"band": "mid", "note": "", "source": "ai"})
    return axes


def resolve(base_axes, overrides=None):
    """Apply user band overrides to base axes, then derive the verdict.
    Returns (effective_axes, verdict_dict). Needs no fundamentals — used by the
    override editor."""
    overrides = overrides or {}
    axes = {k: dict(v) for k, v in base_axes.items()}
    for key, band in overrides.items():
        if key in axes and band in BANDS:
            axes[key] = {**axes[key], "band": band, "source": "override"}
    return axes, derive_verdict(axes)


def build_table(headline, ai_notes, overrides=None):
    """Full robustness view-model: base axes, effective axes, verdict.
    Caller stamps computed_at."""
    base = merge_base_axes(headline, ai_notes)
    effective, verdict = resolve(base, overrides)
    return {"axes_base": base, "axes": effective,
            "overrides": dict(overrides or {}), **verdict}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_robustness.py -q`
Expected: PASS (14 tests)

- [ ] **Step 5: Run ruff + commit**

```bash
python3 -m ruff check robustness.py tests/test_robustness.py
git add robustness.py tests/test_robustness.py
git commit -m "feat(robustness): assemble data+AI axes, overrides, build_table"
```

---

## Task 4: Guard `robustness` + watchlist verdict preference

**Files:**
- Modify: `config_store.py:54-58` (guarded keys), `config_store.py:219-240` (`list_watchlist`)
- Modify: `scorecard_utils.py` (add `resolve_verdict`)
- Test: `tests/test_multi_lens.py` (has scorecard tests already)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_multi_lens.py
def test_resolve_verdict_prefers_robustness():
    import scorecard_utils
    cfg = {
        "ai_notes": {"Scorecard": '```json\n{"verdict":"deep_dive","phase":{"number":5}}\n```'},
        "robustness": {"verdict_mapped": "pass", "verdict": "fragile"},
    }
    out = scorecard_utils.resolve_verdict(cfg)
    assert out["verdict"] == "pass"      # robustness wins
    assert out["phase"] == 5             # phase still from Scorecard


def test_resolve_verdict_falls_back_to_scorecard():
    import scorecard_utils
    cfg = {"ai_notes": {"Scorecard": '```json\n{"verdict":"revisit","phase":{"number":3}}\n```'}}
    out = scorecard_utils.resolve_verdict(cfg)
    assert out["verdict"] == "revisit"
    assert out["phase"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_multi_lens.py -k resolve_verdict -q`
Expected: FAIL — `AttributeError: module 'scorecard_utils' has no attribute 'resolve_verdict'`

- [ ] **Step 3: Write minimal implementation**

Add to `scorecard_utils.py` (after `parse_scorecard`):

```python
def resolve_verdict(cfg):
    """Single source of truth for a ticker's verdict + phase.

    The robustness table (cfg['robustness']['verdict_mapped']) is authoritative
    when present; otherwise fall back to the Scorecard section. Phase always
    comes from the Scorecard. Never raises.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    sc = parse_scorecard(cfg.get("ai_notes"))
    rob = cfg.get("robustness")
    verdict = sc["verdict"]
    if isinstance(rob, dict) and rob.get("verdict_mapped"):
        verdict = rob["verdict_mapped"]
    return {"verdict": verdict, "phase": sc["phase"]}
```

In `config_store.py`, add `"robustness"` to the compute-only guarded keys:

```python
_GUARDED_KEYS_RESTORE_EMPTY = (
    "ai_notes",
    "valuation_inputs",
    "valuation_summary",
    "robustness",
)
```

In `config_store.py` `list_watchlist`, replace the per-row verdict/phase lines (currently `scorecard = parse_scorecard(cfg.get("ai_notes"))` and `"verdict": scorecard["verdict"], "phase": scorecard["phase"]`) with:

```python
        from scorecard_utils import resolve_verdict
        _vp = resolve_verdict(cfg)
```
and in the appended dict:
```python
            "verdict": _vp["verdict"],
            "phase":   _vp["phase"],
```
(Remove the now-unused `parse_scorecard` import line at the top of `list_watchlist` if it is no longer referenced.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_multi_lens.py -k resolve_verdict -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Run ruff + full multi_lens + commit**

```bash
python3 -m ruff check config_store.py scorecard_utils.py
python3 -m pytest tests/test_multi_lens.py -q
git add config_store.py scorecard_utils.py tests/test_multi_lens.py
git commit -m "feat(robustness): guard cfg['robustness'] + watchlist verdict preference"
```

---

## Task 5: "Robustness" prescan prompt

**Files:**
- Modify: `streamlit_app.py:930` (`DEFAULT_AI_PROMPTS` list — add a new entry)
- Test: `tests/test_robustness.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_robustness.py
def test_default_prompts_include_robustness():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert "Robustness" in titles
    entry = next(p for p in streamlit_app.DEFAULT_AI_PROMPTS if p["title"] == "Robustness")
    # must instruct a JSON object with the 4 qualitative axis keys
    for key in ("customers", "barriers", "management", "industry"):
        assert key in entry["prompt"]
    assert "{prior:Moat Analysis}" in entry["prompt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_robustness.py -k default_prompts -q`
Expected: FAIL — `StopIteration` / assertion error (no "Robustness" entry)

- [ ] **Step 3: Write minimal implementation**

Add this dict as the FIRST element of `DEFAULT_AI_PROMPTS` in `streamlit_app.py` (so risk/quality framing leads, per Prasad's order):

```python
    {
        "title": "Robustness",
        "prompt": (
            "You are scoring **{company} ({ticker})** on Pulak Prasad's robustness "
            "framework (risk first). Judge ONLY these four qualitative axes; the ROCE "
            "and net-debt axes are computed from data elsewhere — do not output them.\n\n"
            "For each axis pick a band: \"robust\" (most robust pole), \"mid\", or "
            "\"fragile\" (least robust pole), and a one-line note grounded in the prior "
            "analysis.\n\n"
            "- **customers**: customer & supplier base — robust = highly fragmented (no "
            "dependence on any single party); fragile = concentrated.\n"
            "- **barriers**: competitive barriers / moat — robust = wide/widening; "
            "fragile = none/eroding.\n"
            "- **management**: stability & honesty of management/governance — robust = "
            "stable, honest signals, clean capital allocation; fragile = dubious, "
            "serial acquirer, turnaround.\n"
            "- **industry**: pace of industry change — robust = slow-changing/predictable; "
            "fragile = fast-changing.\n\n"
            "Use the prior sections as evidence:\n"
            "Moat: {prior:Moat Analysis}\n\n"
            "Risk: {prior:Risk Analysis}\n\n"
            "Disruption resilience: {prior:SaaSpocalypse Resistance}\n\n"
            "Business: {prior:Business Analysis}\n\n"
            "Respond with ONLY a fenced JSON block, no prose:\n"
            "```json\n"
            "{\n"
            '  "axes": {\n'
            '    "customers":  {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "barriers":   {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "management": {"band": "robust|mid|fragile", "note": "..."},\n'
            '    "industry":   {"band": "robust|mid|fragile", "note": "..."}\n'
            "  }\n"
            "}\n"
            "```"
        ),
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_robustness.py -k default_prompts -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add streamlit_app.py tests/test_robustness.py
git commit -m "feat(robustness): add Robustness prescan prompt (4 qualitative axes)"
```

---

## Task 6: MCP `set_robustness` tool

**Files:**
- Modify: `mcp_server.py` (add `_set_robustness_impl` near `_save_prescan_section_impl` ~1207, and a `@mcp.tool()` wrapper near the other prescan tools ~1270)
- Modify: `lazytheta-mcp-cloudrun/mcp_handler.py` (register tool + schema, mirroring `get_prescan_sections` ~154 and the `TOOLS` dict ~531)
- Test: `tests/test_mcp_server_user_id.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_mcp_server_user_id.py
def test_set_robustness_impl_builds_and_persists(monkeypatch):
    import mcp_server
    saved = {}

    fake_cfg = {"company": "Meta", "ai_notes": {}, "robustness": {"overrides": {"management": "fragile"}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config", lambda *a, **k: fake_cfg)
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, **k: saved.update(cfg))
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(mcp_server.gather_data, "fetch_fundamentals", lambda *a, **k: {"years": [2025]})
    monkeypatch.setattr(mcp_server.gather_data, "apply_fundamentals_overrides", lambda f, o: f)
    monkeypatch.setattr(mcp_server, "_compute_fundamentals_headline",
                        lambda fund, cfg: {"avg_roce_pct": 30.0, "roce_metric": "ROCE",
                                           "latest_net_debt_ebitda": -0.4,
                                           "latest_adjusted_net_debt_m": -20000.0})

    axes = {"customers": {"band": "robust"}, "barriers": {"band": "robust"},
            "management": {"band": "robust"}, "industry": {"band": "robust"}}
    out = mcp_server._set_robustness_impl("META", axes, user_id="u1")

    assert "robustness" in saved
    # stored AI axes land in ai_notes['Robustness']
    assert "Robustness" in saved["ai_notes"]
    # override (management=fragile) wins over the passed 'robust' → fragile verdict
    assert saved["robustness"]["verdict_mapped"] == "pass"
    assert "META" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mcp_server_user_id.py -k set_robustness -q`
Expected: FAIL — `AttributeError: module 'mcp_server' has no attribute '_set_robustness_impl'`

- [ ] **Step 3: Write minimal implementation**

Add to `mcp_server.py` (after `_save_prescan_section_impl`):

```python
def _set_robustness_impl(ticker, axes, user_id: str | None = None):
    """Store the 4 qualitative robustness axes (band + note each) as the
    'Robustness' ai_notes section, recompute the data axes (ROCE/net debt) from
    fundamentals, and persist the merged table + weakest-link verdict to
    cfg['robustness']. Existing user overrides are preserved."""
    import json as _json
    from datetime import UTC, datetime

    import robustness

    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return {"error": f"{ticker.upper()} not on watchlist"}

    ai_notes = cfg.get("ai_notes") if isinstance(cfg.get("ai_notes"), dict) else {}
    ai_notes["Robustness"] = "```json\n" + _json.dumps({"axes": axes}, ensure_ascii=False) + "\n```"

    try:
        fund_raw = gather_data.fetch_fundamentals(ticker, n_years=10)
        fund = gather_data.apply_fundamentals_overrides(
            fund_raw, cfg.get("fundamentals_overrides") or {})
        headline = _compute_fundamentals_headline(fund, cfg)
    except Exception as e:
        return {"error": f"fundamentals fetch failed: {e}"}

    overrides = (cfg.get("robustness") or {}).get("overrides") or {}
    table = robustness.build_table(headline, ai_notes, overrides)
    table["computed_at"] = datetime.now(UTC).isoformat()

    cfg["ai_notes"] = ai_notes
    cfg["robustness"] = table
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return (f"Saved {ticker.upper()} robustness → {table['verdict']} "
            f"({table['verdict_mapped']}): {table['verdict_reason']}.")
```

Add the tool wrapper (near the other prescan `@mcp.tool()`s):

```python
@mcp.tool()
def set_robustness(ticker: str, axes: dict) -> str:
    """Set the 4 qualitative robustness axes for a watchlist ticker and
    recompute the Prasad robustness verdict.

    Args:
        ticker: Stock ticker (e.g. "META").
        axes: dict of the four qualitative axes, each {"band": "robust|mid|
            fragile", "note": "..."}. Keys: customers, barriers, management,
            industry. ROCE and net-debt axes are computed from data — omit them.

    Returns:
        A status string with the derived verdict (robust/borderline/fragile)
        and reason. ROCE/net-debt axes + verdict are computed server-side.
    """
    try:
        return _set_robustness_impl(ticker, axes)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

In `lazytheta-mcp-cloudrun/mcp_handler.py`, add the handler (near `_tool_get_prescan_sections`):

```python
async def _tool_set_robustness(user_id: str, args: dict) -> Any:
    return mcp_server._set_robustness_impl(args["ticker"], args["axes"], user_id=user_id)
```

add to the tool-schema list (mirroring the `save_prescan_section` schema entry):

```python
    {
        "name": "set_robustness",
        "description": "Set the 4 qualitative robustness axes (customers, barriers, "
                       "management, industry) for a ticker; ROCE/net-debt + verdict "
                       "are computed server-side.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "axes": {"type": "object"},
            },
            "required": ["ticker", "axes"],
        },
    },
```

and register it in the `TOOLS` dispatch dict:

```python
    "set_robustness": _tool_set_robustness,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mcp_server_user_id.py -k set_robustness -q`
Expected: PASS

- [ ] **Step 5: Run ruff + commit**

```bash
python3 -m ruff check mcp_server.py lazytheta-mcp-cloudrun/mcp_handler.py
git add mcp_server.py lazytheta-mcp-cloudrun/mcp_handler.py tests/test_mcp_server_user_id.py
git commit -m "feat(robustness): MCP set_robustness tool (local + cloudrun)"
```

---

## Task 7: Render the robustness table + override editor

**Files:**
- Modify: `streamlit_app.py` — add `_render_robustness_table` (near `_render_football_field` ~250) and call it at the top of the Pre-Scan tab (~7283, right after `_company_name = cfg.get('company', ticker)`).
- Test: manual (visual) — Streamlit render functions are HTML-string builders; add one string-level unit test, then verify in the running app.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_robustness.py
def test_render_robustness_table_html_contains_axes_and_verdict():
    import streamlit_app
    cfg = {
        "robustness": {
            "axes": {
                "roce":       {"band": "robust", "value": 30.0, "metric": "ROCE"},
                "net_debt":   {"band": "robust", "value": -0.4, "unit": "x_ebitda"},
                "customers":  {"band": "robust", "note": "fragmented"},
                "barriers":   {"band": "robust", "note": "wide moat"},
                "management": {"band": "mid", "note": "founder control"},
                "industry":   {"band": "fragile", "note": "fast AI"},
            },
            "verdict": "borderline", "verdict_mapped": "revisit",
            "verdict_reason": "deal-breaker amber: management",
        }
    }
    html = streamlit_app._render_robustness_table(cfg, theme={"text": "#111", "text_muted": "#888"})
    assert "ROCE" in html
    assert "Management" in html
    assert "BORDERLINE" in html.upper()
    # one row per axis
    assert html.count('class="rb-row"') == 6
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_robustness.py -k render_robustness -q`
Expected: FAIL — `AttributeError: module 'streamlit_app' has no attribute '_render_robustness_table'`

- [ ] **Step 3: Write minimal implementation**

Add to `streamlit_app.py` (near `_render_football_field`):

```python
def _render_robustness_table(cfg: dict, theme: dict) -> str:
    """Render the Prasad robustness table: one continuum row per axis +
    a verdict banner. Pure HTML-string builder (no Streamlit calls)."""
    import robustness as _rob

    rob = (cfg or {}).get("robustness") or {}
    axes = rob.get("axes") or {}
    if not axes:
        return (f'<div style="color:{theme.get("text_muted", "#888")};font-size:0.85rem">'
                'Robustness not yet assessed — run the Robustness section.</div>')

    text = theme.get("text", "#111")
    muted = theme.get("text_muted", "#888")
    band_color = {"robust": "#3a9d5d", "mid": "#d6a72e", "fragile": "#d05a4a"}
    band_pos = {"robust": 8.0, "mid": 50.0, "fragile": 92.0}   # dot % on the continuum
    band_dot = {"robust": "\U0001f7e2", "mid": "\U0001f7e1", "fragile": "\U0001f534"}

    def _val_label(key, ax):
        if key == "roce" and ax.get("value") is not None:
            return f'{ax["value"]:.0f}% {ax.get("metric", "ROCE")}'
        if key == "net_debt":
            v = ax.get("value")
            if v is not None:
                return "net cash" if v <= 0 else f'{v:.1f}× EBITDA'
            return ax.get("note", "") or "—"
        return ax.get("note", "") or "—"

    rows = []
    for key, label, _db, _src in _rob.AXES:
        ax = axes.get(key, {"band": "mid"})
        band = ax.get("band", "mid")
        color = band_color.get(band, muted)
        left = band_pos.get(band, 50.0)
        rows.append(
            f'<div class="rb-row" style="display:flex;align-items:center;gap:8px;'
            f'margin:3px 0;font-size:0.8rem">'
            f'<div style="width:150px;color:{text}">{band_dot.get(band, "")} {label}</div>'
            f'<div style="flex:1;position:relative;height:6px;background:#8884;'
            f'border-radius:3px">'
            f'<div style="position:absolute;left:{left:.0f}%;top:-3px;width:12px;height:12px;'
            f'border-radius:50%;background:{color};transform:translateX(-50%)"></div></div>'
            f'<div style="width:150px;color:{muted};text-align:right">{_val_label(key, ax)}</div>'
            f'</div>'
        )

    v = rob.get("verdict", "?").upper()
    vmap = {"ROBUST": "#3a9d5d", "BORDERLINE": "#d6a72e", "FRAGILE": "#d05a4a"}
    vcolor = vmap.get(v, muted)
    banner = (
        f'<div style="margin-top:8px;padding:8px 12px;border-radius:8px;'
        f'background:{vcolor}22;border:1px solid {vcolor};color:{text};font-size:0.85rem">'
        f'<b style="color:{vcolor}">{v}</b> — {rob.get("verdict_reason", "")}</div>'
    )
    head = (f'<div style="display:flex;justify-content:space-between;color:{muted};'
            f'font-size:0.72rem;margin-bottom:2px"><span>ROBUSTNESS</span>'
            f'<span>most ◀───▶ least</span></div>')
    return f'<div style="margin:6px 0 14px">{head}{"".join(rows)}{banner}</div>'
```

Wire it into the Pre-Scan tab. After `_company_name = cfg.get('company', ticker)` (~7284), add:

```python
        st.markdown(_render_robustness_table(cfg, T), unsafe_allow_html=True)

        # Override editor: adjust any axis band; re-derive verdict + persist.
        _rob_state = cfg.get("robustness") or {}
        if _rob_state.get("axes_base"):
            import robustness as _rob_mod
            with st.expander("Adjust robustness bands"):
                _ov = dict(_rob_state.get("overrides") or {})
                _changed = False
                for _k, _lbl, _db, _src in _rob_mod.AXES:
                    _cur = (_rob_state["axes"].get(_k) or {}).get("band", "mid")
                    _new = st.selectbox(
                        _lbl, _rob_mod.BANDS, index=_rob_mod.BANDS.index(_cur),
                        key=f"rob_ov_{ticker}_{_k}")
                    _base_band = (_rob_state["axes_base"].get(_k) or {}).get("band", "mid")
                    if _new != _base_band:
                        _ov[_k] = _new
                    elif _k in _ov:
                        del _ov[_k]
                    if _new != _cur:
                        _changed = True
                if _changed and st.button("Save bands", key=f"rob_save_{ticker}"):
                    _eff, _verdict = _rob_mod.resolve(_rob_state["axes_base"], _ov)
                    cfg["robustness"] = {**_rob_state, "axes": _eff,
                                         "overrides": _ov, **_verdict}
                    save_config(_sb_client, ticker, cfg)
                    st.session_state["_wl_config_dirty"] = True
                    st.rerun()
```

- [ ] **Step 4: Run test + verify in the app**

Run: `python3 -m pytest tests/test_robustness.py -q`
Expected: PASS (all robustness tests, incl. render)

Then launch the app and open a ticker with a populated `robustness` (e.g. set it once via the MCP `set_robustness` tool or seed META), open the Pre-Scan tab, and confirm: the table renders at the top with 6 axis rows + a verdict banner; the "Adjust robustness bands" expander changes the verdict on save.

- [ ] **Step 5: Run ruff + full suite + commit**

```bash
python3 -m ruff check streamlit_app.py robustness.py
python3 -m pytest tests/test_robustness.py tests/test_multi_lens.py test_tastytrade_api.py test_ibkr_api.py -q
git add streamlit_app.py tests/test_robustness.py
git commit -m "feat(robustness): render table + override editor at top of Pre-Scan"
```

---

## Self-Review

**Spec coverage:**
- 6 axes + data/AI sources + bands → Tasks 1, 3, 5 ✓
- ROCE gate (never green <20%, caps verdict) → Task 1 `band_for_roce` + Task 2 rule ✓
- Net-debt computation incl. missing-EBITDA fallback → Task 1 `band_for_net_debt` ✓
- Weakest-link verdict + deal-breakers + Disney exception → Task 2 ✓
- Label mapping + becomes authoritative watchlist verdict → Task 4 ✓
- `cfg['robustness']` data model + guarded storage → Tasks 3, 4, 6 ✓
- New Robustness prompt → Task 5 ✓
- Render at top of Pre-Scan + override editor → Task 7 ✓
- ROE fallback for float businesses → reused from `_compute_fundamentals_headline` (Tasks 3, 6) ✓
- Testing plan → tests in every task ✓

**Placeholder scan:** No TBD/TODO; all code blocks complete.

**Type consistency:** `band` values `robust|mid|fragile` consistent across all tasks; `verdict` `robust|borderline|fragile`; `verdict_mapped` `deep_dive|revisit|pass`; axis keys (`roce, net_debt, customers, barriers, management, industry`) consistent; `build_table`/`resolve`/`derive_verdict`/`merge_base_axes` signatures match their callers in Tasks 6 and 7.

**Out of scope (per spec):** Type-1/2 explainer, honest-vs-cheap tags, five-year-rule widget — not in any task, by design.
