# SOTP MCP-Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the SOTP valuation lens fully editable from the remote MCP — add three new tools (`update_sotp_segments`, `remove_sotp_segment`, `set_sotp_corporate_overhead`) plus a description update on `update_lens_weights` so LLM clients can drive SOTP without the Streamlit UI.

**Architecture:** Each new tool follows the existing `_*_impl` pattern in `mcp_server.py` (load via `config_store.load_config` → mutate `cfg["sotp"]` → save via `config_store.save_config` → return JSON summary). Wire layer in `lazytheta-mcp-cloudrun/mcp_handler.py` adds tool definition + async wrapper + dispatcher entry. No changes to `valuation_lenses.py` — the SOTP lens already reads from `cfg["sotp"]`. No changes to `config_store.py` — `sotp` is already a guarded key (`_GUARDED_KEYS_RESTORE_MISSING_ONLY`, line 66) so empty `segments: []` persists as legitimate user intent.

**Tech Stack:** Python 3.x · Starlette · pytest · Supabase Python SDK · existing MCP stack (JSON-RPC 2024-11-05).

**Spec:** `docs/superpowers/specs/2026-05-13-sotp-mcp-tools-design.md`

**Key precondition (verified during planning):** `valuation_lenses.DEFAULT_LENS_WEIGHTS` already contains `"sotp": 0.00` (line 22), and `_update_lens_weights_impl` computes its whitelist from those keys (`mcp_server.py:453`). So **the existing implementation already accepts `sotp` as a valid weight** — only the tool description in `mcp_handler.py` mentions an outdated list. Task 5 only updates the description (no implementation/whitelist change).

---

## File Structure

| File | Role | Action |
|---|---|---|
| `mcp_server.py` | Core impl of all `_*_impl` functions | Modify — add 3 new functions |
| `lazytheta-mcp-cloudrun/mcp_handler.py` | MCP wire layer (tool defs + dispatch) | Modify — add 3 tool defs, 3 wrappers, 3 dispatch entries, 1 description tweak |
| `test_mcp_server.py` | Unit tests for `_*_impl` | Modify — add ~12 tests |
| `lazytheta-mcp-cloudrun/test_app.py` | Dispatcher/wire tests | Modify — add 3 dispatcher tests, update tool-count test |

---

## Task 1: `_update_sotp_segments_impl` — happy paths

**Files:**
- Modify: `mcp_server.py` (append after `_update_lens_weights_impl` ~line 475)
- Test: `test_mcp_server.py` (append at end)

- [ ] **Step 1: Write three failing tests**

Append to `test_mcp_server.py`:

```python
# ---------------------------------------------------------------------------
# update_sotp_segments
# ---------------------------------------------------------------------------


def _make_sotp_fake_storage(initial_sotp=None):
    """Helper: build a fake Supabase storage with one TEST ticker."""
    cfg = {"company": "Test", "ticker": "TEST"}
    if initial_sotp is not None:
        cfg["sotp"] = dict(initial_sotp)
    return {"TEST": cfg}


def _patch_sotp_storage(monkeypatch, storage):
    """Helper: wire load_config/save_config to the in-memory storage."""
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: dict(storage[t.upper()])
            if t.upper() in storage else None,
    )
    monkeypatch.setattr(
        mcp_server.config_store, "save_config",
        lambda c, t, cfg, user_id=None: storage.update({t.upper(): dict(cfg)}),
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")


def test_update_sotp_segments_adds_new_segment(monkeypatch):
    """Calling with a new segment name appends it to cfg.sotp.segments."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()  # no sotp key yet
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl(
        "TEST",
        [{"name": "AWS", "ev_mid": 800000, "rationale": "8x EV/EBITDA"}],
    )
    result = _json.loads(result_json)

    assert result["segment_count"] == 1
    saved = storage["TEST"]["sotp"]["segments"]
    assert len(saved) == 1
    assert saved[0]["name"] == "AWS"
    assert saved[0]["ev_mid"] == 800000
    assert saved[0]["rationale"] == "8x EV/EBITDA"


def test_update_sotp_segments_merges_existing_by_name(monkeypatch):
    """Existing segment matched by name (case-insensitive) gets partial-merged;
    other fields and other segments stay intact."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [
            {"name": "AWS", "ev_mid": 800000, "ev_low": 700000,
             "rationale": "old rationale"},
            {"name": "Retail", "ev_mid": 200000},
        ],
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._update_sotp_segments_impl(
        "TEST",
        [{"name": "aws", "rationale": "updated rationale"}],  # lowercase match
    )

    segs = {s["name"]: s for s in storage["TEST"]["sotp"]["segments"]}
    assert segs["AWS"]["ev_mid"] == 800000  # untouched
    assert segs["AWS"]["ev_low"] == 700000  # untouched
    assert segs["AWS"]["rationale"] == "updated rationale"  # merged
    assert segs["Retail"]["ev_mid"] == 200000  # other segment untouched


def test_update_sotp_segments_mixed_new_and_update(monkeypatch):
    """A single call can both update an existing segment and add a new one."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._update_sotp_segments_impl(
        "TEST",
        [
            {"name": "AWS", "ev_mid": 900000},  # update
            {"name": "Advertising", "ev_mid": 150000},  # new
        ],
    )

    segs = storage["TEST"]["sotp"]["segments"]
    assert len(segs) == 2
    by_name = {s["name"]: s for s in segs}
    assert by_name["AWS"]["ev_mid"] == 900000
    assert by_name["Advertising"]["ev_mid"] == 150000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_update_sotp_segments_adds_new_segment test_mcp_server.py::test_update_sotp_segments_merges_existing_by_name test_mcp_server.py::test_update_sotp_segments_mixed_new_and_update -v`

Expected: 3× FAIL with `AttributeError: module 'mcp_server' has no attribute '_update_sotp_segments_impl'`

- [ ] **Step 3: Implement `_update_sotp_segments_impl`**

Append to `mcp_server.py` after `_update_lens_weights_impl` (after line 475):

```python
def _update_sotp_segments_impl(ticker: str, segments: list,
                                user_id: str | None = None) -> str:
    """Core logic for update_sotp_segments. Upsert-by-name with partial merge.

    For each input segment, match `name` against existing cfg.sotp.segments
    using case-insensitive trim. Match found → merge supplied (non-None)
    fields into the existing segment. No match → append as new segment
    (requires ev_mid > 0).

    Initialises cfg["sotp"] = {"segments": [], "corporate_overhead_ev_adjustment": 0}
    if not yet present. Other segments are untouched.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    if not isinstance(segments, list) or not segments:
        return json.dumps({
            "error": "segments must be a non-empty list",
        })

    allowed_fields = {
        "name", "ev_mid", "ev_low", "ev_high",
        "revenue", "operating_margin", "implied_multiple_mid", "rationale",
    }
    numeric_fields = {
        "ev_mid", "ev_low", "ev_high", "revenue",
        "operating_margin", "implied_multiple_mid",
    }
    nonneg_fields = {"ev_mid", "ev_low", "ev_high"}

    sotp = cfg.setdefault("sotp", {})
    existing = list(sotp.get("segments") or [])

    def _norm(n):
        return (n or "").strip().lower()

    for idx, inp in enumerate(segments):
        if not isinstance(inp, dict):
            return json.dumps({
                "error": f"segment[{idx}] must be an object",
            })
        name = (inp.get("name") or "").strip()
        if not name:
            return json.dumps({
                "error": f"segment[{idx}] missing required 'name'",
            })
        for k, v in inp.items():
            if k not in allowed_fields:
                return json.dumps({
                    "error": f"segment '{name}': unknown field '{k}'. "
                             f"Allowed: {sorted(allowed_fields)}",
                })
            if k in numeric_fields and v is not None:
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    return json.dumps({
                        "error": f"segment '{name}': field '{k}' must be "
                                 f"a number, got {type(v).__name__}={v!r}",
                    })
                if k in nonneg_fields and v < 0:
                    return json.dumps({
                        "error": f"segment '{name}': field '{k}' must be "
                                 f">= 0, got {v}",
                    })

        match_idx = next(
            (i for i, s in enumerate(existing)
             if _norm(s.get("name")) == _norm(name)),
            None,
        )
        if match_idx is None:
            ev_mid = inp.get("ev_mid")
            if not isinstance(ev_mid, (int, float)) or isinstance(ev_mid, bool) \
                    or ev_mid <= 0:
                return json.dumps({
                    "error": f"new segment '{name}' requires ev_mid > 0",
                })
            new_seg = {k: v for k, v in inp.items()
                       if k in allowed_fields and v is not None}
            new_seg["name"] = name  # use trimmed name
            existing.append(new_seg)
        else:
            merged = dict(existing[match_idx])
            for k, v in inp.items():
                if k in allowed_fields and v is not None:
                    merged[k] = v
            existing[match_idx] = merged

    sotp["segments"] = existing
    cfg["sotp"] = sotp

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({
        "ticker": ticker.upper(),
        "sotp": sotp,
        "segment_count": len(existing),
    }, default=str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_update_sotp_segments_adds_new_segment test_mcp_server.py::test_update_sotp_segments_merges_existing_by_name test_mcp_server.py::test_update_sotp_segments_mixed_new_and_update -v`

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add mcp_server.py test_mcp_server.py
git commit -m "feat(mcp): add _update_sotp_segments_impl with upsert-by-name semantics

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `_update_sotp_segments_impl` — error paths

**Files:**
- Test: `test_mcp_server.py` (append)

- [ ] **Step 1: Write four failing tests**

Append to `test_mcp_server.py`:

```python
def test_update_sotp_segments_unknown_ticker_returns_error(monkeypatch):
    """Unknown ticker → error JSON."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._update_sotp_segments_impl(
        "UNKNOWN", [{"name": "AWS", "ev_mid": 100}]
    )
    assert "error" in _json.loads(result_json)


def test_update_sotp_segments_empty_list_returns_error(monkeypatch):
    """Empty segments list → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage({"segments": [{"name": "AWS", "ev_mid": 100}]})
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl("TEST", [])
    assert "error" in _json.loads(result_json)
    # unchanged
    assert storage["TEST"]["sotp"]["segments"] == [{"name": "AWS", "ev_mid": 100}]


def test_update_sotp_segments_new_segment_without_ev_mid_returns_error(monkeypatch):
    """New segment without ev_mid > 0 → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl(
        "TEST", [{"name": "AWS", "rationale": "no ev"}]
    )
    body = _json.loads(result_json)
    assert "error" in body
    assert "AWS" in body["error"]
    # no sotp written
    assert "sotp" not in storage["TEST"] or not storage["TEST"]["sotp"].get("segments")


def test_update_sotp_segments_negative_ev_returns_error(monkeypatch):
    """Negative EV value → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._update_sotp_segments_impl(
        "TEST", [{"name": "AWS", "ev_mid": -100}]
    )
    body = _json.loads(result_json)
    assert "error" in body
    assert "ev_mid" in body["error"]
```

- [ ] **Step 2: Run tests to verify they fail** (well, they may already pass — error paths were implemented in Task 1; if so, this is just validation)

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -k "test_update_sotp_segments_unknown_ticker or test_update_sotp_segments_empty_list or test_update_sotp_segments_new_segment_without_ev_mid or test_update_sotp_segments_negative_ev" -v`

Expected: 4 PASS (already implemented in Task 1).

If any FAIL, fix `_update_sotp_segments_impl` to match the test expectations before continuing.

- [ ] **Step 3: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add test_mcp_server.py
git commit -m "test(mcp): cover error paths for _update_sotp_segments_impl

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `_remove_sotp_segment_impl`

**Files:**
- Modify: `mcp_server.py` (append after Task 1's function)
- Test: `test_mcp_server.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `test_mcp_server.py`:

```python
# ---------------------------------------------------------------------------
# remove_sotp_segment
# ---------------------------------------------------------------------------


def test_remove_sotp_segment_removes_existing(monkeypatch):
    """Removing an existing segment leaves the rest intact."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [
            {"name": "AWS", "ev_mid": 800000},
            {"name": "Retail", "ev_mid": 200000},
        ],
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._remove_sotp_segment_impl("TEST", "AWS")

    segs = storage["TEST"]["sotp"]["segments"]
    assert len(segs) == 1
    assert segs[0]["name"] == "Retail"


def test_remove_sotp_segment_case_insensitive(monkeypatch):
    """Name match is case-insensitive."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._remove_sotp_segment_impl("TEST", "aws")

    assert storage["TEST"]["sotp"]["segments"] == []


def test_remove_sotp_segment_missing_name_is_noop(monkeypatch):
    """Removing a non-existing name is a no-op, not an error."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
    })
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._remove_sotp_segment_impl("TEST", "NonExistent")
    result = _json.loads(result_json)
    assert "error" not in result
    assert storage["TEST"]["sotp"]["segments"] == [{"name": "AWS", "ev_mid": 800000}]


def test_remove_sotp_segment_no_sotp_dict_is_noop(monkeypatch):
    """Removing from a cfg that has no sotp key is a no-op."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage()  # no sotp
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._remove_sotp_segment_impl("TEST", "AWS")
    result = _json.loads(result_json)
    assert "error" not in result


def test_remove_sotp_segment_unknown_ticker_returns_error(monkeypatch):
    """Unknown ticker → error JSON."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._remove_sotp_segment_impl("UNKNOWN", "AWS")
    assert "error" in _json.loads(result_json)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -k "test_remove_sotp_segment" -v`

Expected: 5× FAIL with `AttributeError: module 'mcp_server' has no attribute '_remove_sotp_segment_impl'`

- [ ] **Step 3: Implement `_remove_sotp_segment_impl`**

Append to `mcp_server.py` after `_update_sotp_segments_impl`:

```python
def _remove_sotp_segment_impl(ticker: str, name: str,
                               user_id: str | None = None) -> str:
    """Core logic for remove_sotp_segment. Case-insensitive name match.
    Idempotent — removing a non-existent name is a no-op, not an error.
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    target = (name or "").strip().lower()
    if not target:
        return json.dumps({"error": "name must be a non-empty string"})

    sotp = cfg.get("sotp") or {}
    segments = list(sotp.get("segments") or [])
    new_segments = [s for s in segments
                    if (s.get("name") or "").strip().lower() != target]

    if len(new_segments) != len(segments):
        sotp["segments"] = new_segments
        cfg["sotp"] = sotp
        config_store.save_config(client, ticker, cfg, user_id=user_id)

    return json.dumps({
        "ticker": ticker.upper(),
        "sotp": cfg.get("sotp") or {"segments": []},
        "segment_count": len(new_segments),
        "removed": len(segments) - len(new_segments),
    }, default=str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -k "test_remove_sotp_segment" -v`

Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add mcp_server.py test_mcp_server.py
git commit -m "feat(mcp): add _remove_sotp_segment_impl (case-insensitive, idempotent)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `_set_sotp_corporate_overhead_impl`

**Files:**
- Modify: `mcp_server.py` (append after Task 3's function)
- Test: `test_mcp_server.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `test_mcp_server.py`:

```python
# ---------------------------------------------------------------------------
# set_sotp_corporate_overhead
# ---------------------------------------------------------------------------


def test_set_sotp_corporate_overhead_writes_value(monkeypatch):
    """Set the overhead value on a cfg that already has sotp segments."""
    import mcp_server

    storage = _make_sotp_fake_storage({
        "segments": [{"name": "AWS", "ev_mid": 800000}],
        "corporate_overhead_ev_adjustment": 0,
    })
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._set_sotp_corporate_overhead_impl("TEST", -5000)

    assert storage["TEST"]["sotp"]["corporate_overhead_ev_adjustment"] == -5000
    # segments untouched
    assert storage["TEST"]["sotp"]["segments"] == [{"name": "AWS", "ev_mid": 800000}]


def test_set_sotp_corporate_overhead_initialises_sotp_dict(monkeypatch):
    """If cfg has no sotp dict, the call creates one with segments: []."""
    import mcp_server

    storage = _make_sotp_fake_storage()  # no sotp
    _patch_sotp_storage(monkeypatch, storage)

    mcp_server._set_sotp_corporate_overhead_impl("TEST", -2500)

    saved = storage["TEST"]["sotp"]
    assert saved["corporate_overhead_ev_adjustment"] == -2500
    assert saved.get("segments") == []


def test_set_sotp_corporate_overhead_non_number_returns_error(monkeypatch):
    """Non-numeric value → error JSON, no write."""
    import json as _json
    import mcp_server

    storage = _make_sotp_fake_storage({"corporate_overhead_ev_adjustment": 0})
    _patch_sotp_storage(monkeypatch, storage)

    result_json = mcp_server._set_sotp_corporate_overhead_impl("TEST", "abc")
    body = _json.loads(result_json)
    assert "error" in body
    assert storage["TEST"]["sotp"]["corporate_overhead_ev_adjustment"] == 0


def test_set_sotp_corporate_overhead_unknown_ticker_returns_error(monkeypatch):
    """Unknown ticker → error JSON."""
    import json as _json
    import mcp_server

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: object())
    monkeypatch.setattr(
        mcp_server.config_store, "load_config",
        lambda c, t, user_id=None: None,
    )
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    result_json = mcp_server._set_sotp_corporate_overhead_impl("UNKNOWN", -100)
    assert "error" in _json.loads(result_json)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -k "test_set_sotp_corporate_overhead" -v`

Expected: 4× FAIL with `AttributeError: module 'mcp_server' has no attribute '_set_sotp_corporate_overhead_impl'`

- [ ] **Step 3: Implement `_set_sotp_corporate_overhead_impl`**

Append to `mcp_server.py` after `_remove_sotp_segment_impl`:

```python
def _set_sotp_corporate_overhead_impl(ticker: str, value: float,
                                       user_id: str | None = None) -> str:
    """Core logic for set_sotp_corporate_overhead. Scalar setter for
    cfg["sotp"]["corporate_overhead_ev_adjustment"]. Initialises cfg["sotp"]
    with segments: [] if not yet present.

    Typical magnitudes are negative ($M, e.g. -5000 for $5B of unallocated
    corporate overhead capitalized into the bridge).
    """
    user_id = user_id or USER_ID
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker.upper()} not found on watchlist"})

    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return json.dumps({
            "error": f"value must be a number, got {type(value).__name__}={value!r}",
        })

    sotp = cfg.get("sotp")
    if not isinstance(sotp, dict):
        sotp = {"segments": []}
    sotp["corporate_overhead_ev_adjustment"] = float(value)
    cfg["sotp"] = sotp

    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return json.dumps({
        "ticker": ticker.upper(),
        "sotp": sotp,
    }, default=str)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py -k "test_set_sotp_corporate_overhead" -v`

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add mcp_server.py test_mcp_server.py
git commit -m "feat(mcp): add _set_sotp_corporate_overhead_impl

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Wire all three tools into the MCP handler + update `update_lens_weights` description

**Files:**
- Modify: `lazytheta-mcp-cloudrun/mcp_handler.py`

- [ ] **Step 1: Add async tool wrappers**

In `mcp_handler.py`, insert after `_tool_update_dcf_scenario_adjustments` (around line 107):

```python
async def _tool_update_sotp_segments(user_id: str, args: dict) -> Any:
    return mcp_server._update_sotp_segments_impl(
        ticker=args["ticker"],
        segments=args["segments"],
        user_id=user_id,
    )


async def _tool_remove_sotp_segment(user_id: str, args: dict) -> Any:
    return mcp_server._remove_sotp_segment_impl(
        ticker=args["ticker"],
        name=args["name"],
        user_id=user_id,
    )


async def _tool_set_sotp_corporate_overhead(user_id: str, args: dict) -> Any:
    return mcp_server._set_sotp_corporate_overhead_impl(
        ticker=args["ticker"],
        value=args["value"],
        user_id=user_id,
    )
```

- [ ] **Step 2: Add tool definitions to `TOOLS`**

In `mcp_handler.py`, insert into the `TOOLS` list AFTER the `update_dcf_scenario_adjustments` entry (ending around line 295) and BEFORE the `get_prescan_prompts` entry. Insert these three blocks:

```python
    {
        "name": "update_sotp_segments",
        "description": (
            "Upsert SOTP segments for a watchlist ticker. For each input "
            "segment, match by 'name' (case-insensitive) against existing "
            "cfg.sotp.segments: match → partial merge of supplied fields; "
            "no match → append as new segment (requires ev_mid > 0). Other "
            "segments are untouched. Allowed segment fields: name (required), "
            "ev_mid (required for new), ev_low, ev_high, revenue, "
            "operating_margin (0-1 decimal), implied_multiple_mid, rationale. "
            "All EV values in $M, non-negative. To remove a segment, use "
            "remove_sotp_segment instead. Call calculate_multi_lens_valuation "
            "afterwards to see the new SOTP lens output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "segments": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "ev_mid": {"type": "number"},
                            "ev_low": {"type": "number"},
                            "ev_high": {"type": "number"},
                            "revenue": {"type": "number"},
                            "operating_margin": {"type": "number"},
                            "implied_multiple_mid": {"type": "number"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["name"],
                    },
                },
            },
            "required": ["ticker", "segments"],
        },
    },
    {
        "name": "remove_sotp_segment",
        "description": (
            "Remove one SOTP segment by name (case-insensitive) from a "
            "watchlist ticker. Idempotent — no error if the name doesn't "
            "exist. Removing the last segment is allowed and persisted as "
            "an empty list (treated as legitimate user intent by the "
            "config-store guard)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["ticker", "name"],
        },
    },
    {
        "name": "set_sotp_corporate_overhead",
        "description": (
            "Set cfg.sotp.corporate_overhead_ev_adjustment for a watchlist "
            "ticker ($M, typically negative — e.g. -5000 for $5B of "
            "unallocated corporate overhead capitalized into the SOTP "
            "bridge). Initialises cfg.sotp with segments: [] if not yet "
            "present."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "value": {"type": "number"},
            },
            "required": ["ticker", "value"],
        },
    },
```

- [ ] **Step 3: Add dispatch entries to `TOOL_HANDLERS`**

In `mcp_handler.py`, update `TOOL_HANDLERS` (around line 336) by adding three entries before `"get_prescan_prompts"`:

```python
    "update_sotp_segments": _tool_update_sotp_segments,
    "remove_sotp_segment": _tool_remove_sotp_segment,
    "set_sotp_corporate_overhead": _tool_set_sotp_corporate_overhead,
```

- [ ] **Step 4: Update `update_lens_weights` description to mention sotp**

In `mcp_handler.py`, find the `update_lens_weights` tool definition (around line 245-268). Replace the description string:

Old:
```python
        "description": (
            "Override one or more lens weights for a watchlist ticker. "
            "Valid keys: dcf, multiples, historical, reverse_dcf, dividend. "
            "Specified keys merge into cfg.lens_weights; unspecified keys "
            "retain their value (or fall back to DEFAULT_LENS_WEIGHTS). "
            "Orchestrator renormalizes active weights to 1.0 at compute "
            "time, so partial overrides like {dcf: 0.6} work. Empty dict "
            "resets to defaults."
        ),
```

New:
```python
        "description": (
            "Override one or more lens weights for a watchlist ticker. "
            "Valid keys: dcf, multiples, historical, reverse_dcf, dividend, "
            "sotp. Specified keys merge into cfg.lens_weights; unspecified "
            "keys retain their value (or fall back to DEFAULT_LENS_WEIGHTS). "
            "Orchestrator renormalizes active weights to 1.0 at compute "
            "time, so partial overrides like {dcf: 0.6} work. Empty dict "
            "resets to defaults. SOTP defaults to 0.00 — opt-in per ticker "
            "by setting sotp: 0.10+ once segments are defined."
        ),
```

Also update the inline doc for the `weights` property in the same tool's `inputSchema` (the `description` field inside `properties.weights`):

Old:
```python
                    "description": (
                        "Dict mapping lens keys (dcf, multiples, historical, "
                        "reverse_dcf, dividend) to non-negative floats"
                    ),
```

New:
```python
                    "description": (
                        "Dict mapping lens keys (dcf, multiples, historical, "
                        "reverse_dcf, dividend, sotp) to non-negative floats"
                    ),
```

- [ ] **Step 5: Sanity-check no syntax errors**

Run: `cd /Users/administrator/Documents/github/stock-analysis/lazytheta-mcp-cloudrun && python3 -c "import mcp_handler; print(len(mcp_handler.TOOLS), 'tools'); print(sorted(mcp_handler.TOOL_HANDLERS.keys()))"`

Expected: prints `16 tools` and the sorted list includes `remove_sotp_segment`, `set_sotp_corporate_overhead`, `update_sotp_segments`.

- [ ] **Step 6: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add lazytheta-mcp-cloudrun/mcp_handler.py
git commit -m "feat(mcp): wire SOTP tools (segments/remove/overhead) + sotp in lens_weights doc

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Dispatcher tests for the three new tools

**Files:**
- Test: `lazytheta-mcp-cloudrun/test_app.py` (modify existing test + append new tests)

- [ ] **Step 1: Update `test_tools_list_returns_13_tools` → 16 tools**

In `lazytheta-mcp-cloudrun/test_app.py`, replace the test (around line 546). Rename function and update assertions:

Old:
```python
def test_tools_list_returns_13_tools():
    """tools/list returns the full set of 13 tools (added
    update_dcf_scenario_adjustments on 2026-05-13)."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    token = sign_jwt({"type": "access_token", "user_id": "u"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    assert len(tools) == 13
    names = {t["name"] for t in tools}
    assert names == {
        "build_dcf_config", "calculate_valuation", "calculate_multi_lens_valuation",
        "refresh_all_valuations", "save_to_watchlist", "get_config",
        "get_watchlist", "update_valuation_inputs", "update_lens_weights",
        "update_dcf_scenario_adjustments",
        "get_prescan_prompts", "get_prescan_sections", "save_prescan_section",
    }
```

New:
```python
def test_tools_list_returns_16_tools():
    """tools/list returns the full set of 16 tools (added 3 SOTP tools on
    2026-05-13: update_sotp_segments, remove_sotp_segment,
    set_sotp_corporate_overhead)."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app

    token = sign_jwt({"type": "access_token", "user_id": "u"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    assert len(tools) == 16
    names = {t["name"] for t in tools}
    assert names == {
        "build_dcf_config", "calculate_valuation", "calculate_multi_lens_valuation",
        "refresh_all_valuations", "save_to_watchlist", "get_config",
        "get_watchlist", "update_valuation_inputs", "update_lens_weights",
        "update_dcf_scenario_adjustments",
        "update_sotp_segments", "remove_sotp_segment", "set_sotp_corporate_overhead",
        "get_prescan_prompts", "get_prescan_sections", "save_prescan_section",
    }
```

- [ ] **Step 2: Add dispatcher tests for the three new tools**

Append to `lazytheta-mcp-cloudrun/test_app.py` at the very end of the file:

```python
def test_tools_call_update_sotp_segments_passes_args(monkeypatch):
    """update_sotp_segments routes ticker, segments, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, segments, user_id=None):
        captured.update({"ticker": ticker, "segments": segments, "user_id": user_id})
        return '{"segment_count": 1}'
    monkeypatch.setattr(mcp_server, "_update_sotp_segments_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "update_sotp_segments",
                "arguments": {
                    "ticker": "AMZN",
                    "segments": [{"name": "AWS", "ev_mid": 800000}],
                },
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {
        "ticker": "AMZN",
        "segments": [{"name": "AWS", "ev_mid": 800000}],
        "user_id": "jwt-uid",
    }


def test_tools_call_remove_sotp_segment_passes_args(monkeypatch):
    """remove_sotp_segment routes ticker, name, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, name, user_id=None):
        captured.update({"ticker": ticker, "name": name, "user_id": user_id})
        return '{"removed": 1}'
    monkeypatch.setattr(mcp_server, "_remove_sotp_segment_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "remove_sotp_segment",
                "arguments": {"ticker": "AMZN", "name": "AWS"},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {"ticker": "AMZN", "name": "AWS", "user_id": "jwt-uid"}


def test_tools_call_set_sotp_corporate_overhead_passes_args(monkeypatch):
    """set_sotp_corporate_overhead routes ticker, value, user_id to the impl."""
    from starlette.testclient import TestClient
    from mcp_auth import sign_jwt
    from main import app
    import mcp_server

    captured = {}
    def fake_impl(ticker, value, user_id=None):
        captured.update({"ticker": ticker, "value": value, "user_id": user_id})
        return '{"set": true}'
    monkeypatch.setattr(mcp_server, "_set_sotp_corporate_overhead_impl", fake_impl)

    token = sign_jwt({"type": "access_token", "user_id": "jwt-uid"}, ttl_seconds=60)
    client = TestClient(app)
    r = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "method": "tools/call",
            "id": 1,
            "params": {
                "name": "set_sotp_corporate_overhead",
                "arguments": {"ticker": "AMZN", "value": -5000},
            },
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    assert captured == {"ticker": "AMZN", "value": -5000, "user_id": "jwt-uid"}
```

- [ ] **Step 3: Run all dispatcher tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis/lazytheta-mcp-cloudrun && python3 -m pytest test_app.py -v`

Expected: all tests PASS (the renamed `test_tools_list_returns_16_tools` + the three new dispatcher tests + all pre-existing tests still green).

- [ ] **Step 4: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add lazytheta-mcp-cloudrun/test_app.py
git commit -m "test(mcp): dispatcher tests for SOTP tools, tool count 13→16

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Integration test — SOTP round-trip through `calculate_multi_lens_valuation`

**Files:**
- Test: `test_mcp_server.py` (append)

- [ ] **Step 1: Write the integration test**

Append to `test_mcp_server.py`:

```python
# ---------------------------------------------------------------------------
# Integration: SOTP round-trip
# ---------------------------------------------------------------------------


def test_sotp_round_trip_segments_then_calculate_multi_lens(monkeypatch):
    """After update_sotp_segments, calculate_multi_lens_valuation returns a
    populated 'sotp' lens with fv_low/mid/high."""
    import json as _json
    import mcp_server
    import valuation_lenses

    # Minimal cfg with the bridge inputs the SOTP lens needs
    storage = {
        "AMZN": {
            "ticker": "AMZN",
            "company": "Amazon",
            "shares_outstanding": 10000,    # 10,000 M shares
            "cash": [50000],                # $50B
            "st_investments": [0],
            "debt_market_value": 60000,     # $60B
            "minority_interest": 0,
            "unfunded_pension": 0,
            "equity_investments": 0,
            "lens_weights": {"sotp": 1.0, "dcf": 0, "multiples": 0,
                              "historical": 0, "reverse_dcf": 0, "dividend": 0},
        },
    }
    _patch_sotp_storage(monkeypatch, storage)

    # Step 1: add two segments via the tool
    mcp_server._update_sotp_segments_impl(
        "AMZN",
        [
            {"name": "AWS", "ev_mid": 800000, "ev_low": 700000, "ev_high": 900000},
            {"name": "Retail", "ev_mid": 200000, "ev_low": 150000, "ev_high": 250000},
        ],
    )

    # Step 2: compute lenses on the resulting cfg
    cfg = storage["AMZN"]
    lens_out = valuation_lenses.compute_sotp_lens(cfg)

    assert lens_out is not None
    assert lens_out["fv_mid"] > 0
    assert lens_out["fv_low"] <= lens_out["fv_mid"] <= lens_out["fv_high"]
    assert lens_out["details"]["segment_count"] == 2
```

- [ ] **Step 2: Run the test**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py::test_sotp_round_trip_segments_then_calculate_multi_lens -v`

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
cd /Users/administrator/Documents/github/stock-analysis
git add test_mcp_server.py
git commit -m "test: SOTP round-trip — update_sotp_segments feeds compute_sotp_lens

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Final verification — ruff + full test suite

- [ ] **Step 1: Ruff lint**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m ruff check .`

Expected: `All checks passed!` (or no errors). If lint errors appear, fix them in the offending files and re-run.

- [ ] **Step 2: Required broker-API test suite (CLAUDE.md mandate)**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v`

Expected: 81 passed.

- [ ] **Step 3: MCP impl + dispatcher tests**

Run: `cd /Users/administrator/Documents/github/stock-analysis && python3 -m pytest test_mcp_server.py lazytheta-mcp-cloudrun/test_app.py -v`

Expected: all pass, including all new SOTP tests.

- [ ] **Step 4: No commit needed** (verification only, no code changes). If any test failed at Step 2 or 3, fix the root cause and re-run before declaring done.

---

## Acceptance Criteria (mirrored from spec)

1. ✅ Vanuit Claude Code MCP-sessie kan een gebruiker voor een watchlist-ticker:
   - Een nieuw segment toevoegen met één tool-call → Task 1.
   - Een bestaand segment partial-updaten zonder andere velden te verliezen → Task 1.
   - Een segment verwijderen → Task 3.
   - Corporate overhead instellen → Task 4.
   - SOTP-lens activeren via `update_lens_weights({sotp: 0.15})` → Task 5 (description + already-working backend).
2. ✅ `calculate_multi_lens_valuation` na SOTP-update levert correcte `sotp` lens output → Task 7.
3. ✅ Nieuwe tests in `test_mcp_server.py` (impl) en `lazytheta-mcp-cloudrun/test_app.py` (dispatcher) slagen → Tasks 1–7.
4. ✅ Bestaande 81 tests in `test_tastytrade_api.py` + `test_ibkr_api.py` blijven groen → Task 8.
5. ✅ Ruff lint passeert → Task 8.
