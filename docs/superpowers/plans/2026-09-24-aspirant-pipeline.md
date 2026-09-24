# Aspirant-pijplijn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Een wekelijkse cloud-routine die nieuwe Screener-namen als categorie Aspirant in de watchlist zet, de pre-scan invult, en alleen bij Wide moat met een volledig ingevulde DCF doorzet naar Uncategorized.

**Architecture:** De regels (categorieën, "is dit een placeholder-DCF", "is de moat Wide", "mag dit door") staan als pure functies in een nieuwe module `aspirant.py`. De MCP (`mcp_server.py` + Cloud Run-handler) krijgt vier tools die die regels server-side afdwingen. De Streamlit-watchlist krijgt de categorie Aspirant, een NO-knop en een "by Claude"-label. De routine zelf is een prompt in de repo, aangemaakt met `/schedule`.

**Tech Stack:** Python 3.11, Streamlit 1.54, Supabase (postgrest-py), FastMCP (stdio) + Starlette JSON-RPC handler (Cloud Run), pytest met mocks.

**Spec:** `docs/superpowers/specs/2026-09-24-aspirant-pipeline-design.md`

## Global Constraints

- Python 3.11: geen backslash binnen een f-string-expressie.
- Lint vóór commit: `python3 -m ruff check .` moet slagen.
- Tests vóór commit: `python3 -m pytest -q test_*.py tests` — alles groen behalve de bekende `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer` (faalde al vóór dit werk).
- `config_store.save_config` **voegt samen**: een weggelaten sleutel blijft staan. Een markering opheffen = expliciet `False` zetten, nooit `pop`.
- Categorieën, exact: `Uncategorized`, `Yes`, `Maybe`, `Watch Later`, `No`, `Aspirant`.
- Risk-free rate in de MCP: `gather_data.RISK_FREE_RATE_DEFAULT` (geen live fetch), zoals `_build_dcf_config_impl`.
- Geen peers/multiples in de routine; watchlist-fair-value is DCF-only.
- Commits eindigen met `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| Bestand | Rol |
|---|---|
| `aspirant.py` (nieuw) | Pure regels: `CATEGORIES`, `curves_are_flat`, `moat_label`, `promotion_blockers` |
| `gather_data.py` | Nieuw: `build_base_config(ticker, stock_price=0)` — de EDGAR-route van `run_analysis` zonder UI |
| `config_store.py` | `list_watchlist` geeft `category`, `dcf_placeholder`, `promoted_by`, `promoted_at` mee |
| `mcp_server.py` | Nieuwe impls + `@mcp.tool()`s; save/calculate/refresh respecteren `dcf_placeholder` |
| `lazytheta-mcp-cloudrun/mcp_handler.py` | Vier nieuwe tools in `TOOLS` + dispatch |
| `streamlit_app.py` | Categorie Aspirant, NO-knop, "by Claude"-label, Refresh all slaat placeholders over |
| `docs/routines/aspirant-weekly.md` (nieuw) | De routine-prompt |
| `tests/test_aspirant.py` (nieuw) | Tests voor `aspirant.py`, `build_base_config` en de MCP-impls |

---

### Task 1: Pure regels in `aspirant.py`

**Files:**
- Create: `aspirant.py`
- Test: `tests/test_aspirant.py`

**Interfaces:**
- Produces:
  - `CATEGORIES: tuple[str, ...]` = `("Yes", "Aspirant", "Maybe", "Watch Later", "No", "Uncategorized")` (volgorde = weergavevolgorde in de watchlist)
  - `curves_are_flat(cfg: dict) -> bool`
  - `moat_label(ai_notes: dict | None) -> str | None` — `"Wide"`, `"Narrow"`, `"None"` of `None`
  - `promotion_blockers(cfg: dict) -> list[str]` — lege lijst = mag door

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_aspirant.py
import pytest

import aspirant


def _cfg(**over):
    cfg = {
        "category": "Aspirant",
        "dcf_placeholder": False,
        "equity_market_value": 1000.0,
        "sector_betas": [["Software", 1.1, 1.0]],
        "revenue_growth": [0.12, 0.10, 0.08, 0.06, 0.04],
        "op_margins": [0.30, 0.31, 0.32, 0.32, 0.32],
        "valuation_summary": {"weighted_fv_mid": 100.0},
        "ai_notes": {"Moat": "**Moat: Wide 🛡️ · Stable ➡️ · 4/5**\n\nText."},
    }
    cfg.update(over)
    return cfg


def test_categories_include_aspirant_and_the_existing_five():
    assert set(aspirant.CATEGORIES) == {
        "Uncategorized", "Yes", "Maybe", "Watch Later", "No", "Aspirant"}


def test_flat_curves_are_the_build_config_placeholders():
    assert aspirant.curves_are_flat({"revenue_growth": [0.025] * 5, "op_margins": [0.2] * 5})
    assert not aspirant.curves_are_flat({"revenue_growth": [0.1, 0.08], "op_margins": [0.2, 0.2]})
    assert not aspirant.curves_are_flat({"revenue_growth": [0.03, 0.03], "op_margins": [0.2, 0.25]})
    assert aspirant.curves_are_flat({})  # geen curves = niets ingevuld


def test_moat_label_reads_the_verdict_line():
    assert aspirant.moat_label({"Moat": "**Moat: Wide 🛡️ · Stable ➡️ · 4/5**\n\nx"}) == "Wide"
    assert aspirant.moat_label({"Moat": "**Moat: Narrow · Eroding · 2/5**\n\nx"}) == "Narrow"
    assert aspirant.moat_label({"Moat": "Some old free-form report"}) is None
    assert aspirant.moat_label(None) is None


def test_a_complete_wide_aspirant_has_no_blockers():
    assert aspirant.promotion_blockers(_cfg()) == []


@pytest.mark.parametrize("over, fragment", [
    ({"category": "Maybe"}, "not an Aspirant"),
    ({"ai_notes": {"Moat": "**Moat: Narrow · Stable · 3/5**"}}, "Wide"),
    ({"ai_notes": {}}, "Wide"),
    ({"dcf_placeholder": True}, "placeholder"),
    ({"equity_market_value": 0}, "equity_market_value"),
    ({"sector_betas": [["A", 1.1, 1.1]]}, "sector_betas"),
    ({"valuation_summary": None}, "valuation_summary"),
])
def test_each_missing_piece_blocks_promotion(over, fragment):
    blockers = aspirant.promotion_blockers(_cfg(**over))
    assert any(fragment in b for b in blockers), blockers
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest -q tests/test_aspirant.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'aspirant'`

- [ ] **Step 3: Implement `aspirant.py`**

```python
"""Rules for the aspirant pipeline: Screener names on their way into the watchlist.

Pure functions, no I/O, so the MCP server and the Streamlit page apply the
same rules and the tests can pin them without Supabase.
"""

import re

# Display order of the watchlist groups. Aspirant sits under Yes: new names to
# look at, above the piles already decided.
CATEGORIES = ("Yes", "Aspirant", "Maybe", "Watch Later", "No", "Uncategorized")


def curves_are_flat(cfg):
    """True while growth and margin curves are still build_config's placeholders.

    build_config writes growth = terminal growth and margin = last real margin
    for every year. A curve someone actually filled in varies somewhere.
    """
    for key in ("revenue_growth", "op_margins"):
        curve = [round(float(v), 6) for v in (cfg.get(key) or [])]
        if len(set(curve)) > 1:
            return False
    return True


# The Moat prompt opens with "**Moat: Wide 🛡️ · Stable ➡️ · 4/5**". Only that
# line decides; prescan_render.parse_verdict_section also wants the bullets
# below it (it builds a card), and a gate must not fail on formatting.
_MOAT_LINE = re.compile(r"^\*\*Moat:\s*([A-Za-z]+)", re.M)


def moat_label(ai_notes):
    """"Wide" / "Narrow" / "None" from the Moat section's verdict line, else None."""
    if not isinstance(ai_notes, dict):
        return None
    text = ai_notes.get("Moat") or ""
    m = _MOAT_LINE.search(text)
    if not m or m.start() > 400:   # the verdict opens the section
        return None
    label = m.group(1).title()
    return label if label in ("Wide", "Narrow", "None") else None


def promotion_blockers(cfg):
    """Why this config may not leave Aspirant yet; [] when it may."""
    out = []
    if cfg.get("category") != "Aspirant":
        out.append("not an Aspirant")
    if moat_label(cfg.get("ai_notes")) != "Wide":
        out.append("Moat verdict is not Wide")
    if cfg.get("dcf_placeholder"):
        out.append("DCF is still the placeholder (growth/margin curves are flat)")
    try:
        emv = float(cfg.get("equity_market_value") or 0)
    except (TypeError, ValueError):
        emv = 0
    if emv <= 0:
        out.append("equity_market_value missing")
    try:
        weights = sum(float(e[2]) for e in cfg.get("sector_betas") or [])
    except (TypeError, IndexError, ValueError):
        weights = 0
    if abs(weights - 1.0) > 0.001:
        out.append("sector_betas weights do not sum to 1.0")
    if not cfg.get("valuation_summary"):
        out.append("valuation_summary missing (run calculate_multi_lens_valuation)")
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest -q tests/test_aspirant.py`
Expected: PASS (11 tests)

- [ ] **Step 5: Lint and commit**

```bash
python3 -m ruff check aspirant.py tests/test_aspirant.py
git add aspirant.py tests/test_aspirant.py
git commit -m "Aspirant: pure regels voor categorie, placeholder-DCF, moat en doorzetten

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `gather_data.build_base_config`

**Files:**
- Modify: `gather_data.py` (nieuwe functie onderaan, na `build_config`)
- Test: `tests/test_aspirant.py`

**Interfaces:**
- Consumes: bestaande `gather_data`-functies `get_cik`, `fetch_company_submissions`, `resolve_sector_betas`, `fetch_company_facts`, `parse_financials`, `apply_adr_share_ratio`, `fetch_stock_price`, `synthetic_credit_rating`, `build_config`, `fetch_fundamentals`; `scorecard_utils.slim_fundamentals`; constants `RISK_FREE_RATE_DEFAULT`, `MARGIN_OF_SAFETY_DEFAULT`, `TERMINAL_GROWTH_DEFAULT`
- Produces: `build_base_config(ticker: str, stock_price: float = 0) -> dict` — raises `ValueError` met een leesbare reden

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_aspirant.py (toevoegen)
from unittest.mock import patch


def _patch_edgar(gd, price=0.0):
    fin = {"years": [2021, 2022, 2023, 2024, 2025], "shares": [100.0] * 5,
           "operating_income": [50.0] * 5, "interest_expense_latest": 1.0}
    return [
        patch.object(gd, "get_cik", return_value="0000000001"),
        patch.object(gd, "fetch_company_submissions",
                     return_value={"name": "Test Corp", "sic": "7372",
                                   "sicDescription": "Software"}),
        patch.object(gd, "resolve_sector_betas", return_value=[("Software", 1.1, 1.0)]),
        patch.object(gd, "fetch_company_facts", return_value={}),
        patch.object(gd, "parse_financials", return_value=fin),
        patch.object(gd, "fetch_stock_price", return_value=(price, 0, 0)),
        patch.object(gd, "synthetic_credit_rating", return_value=("AA", 0.01)),
        patch.object(gd, "build_config", side_effect=lambda **kw: {
            "stock_price": kw["stock_price"], "base_revenue": 10.0,
            "base_year": 2025, "company": kw["company_name"]}),
        patch.object(gd, "fetch_fundamentals", return_value={}),
    ]


def test_build_base_config_uses_the_given_price_and_skips_yahoo():
    import contextlib
    import gather_data as gd
    with contextlib.ExitStack() as stack:
        mocks = [stack.enter_context(p) for p in _patch_edgar(gd, price=0.0)]
        cfg = gd.build_base_config("TST", stock_price=42.0)
    assert cfg["stock_price"] == 42.0
    yahoo = mocks[5]
    yahoo.assert_not_called()


def test_build_base_config_without_any_price_says_so():
    import contextlib
    import gather_data as gd
    with contextlib.ExitStack() as stack:
        for p in _patch_edgar(gd, price=0.0):
            stack.enter_context(p)
        with pytest.raises(ValueError, match="stock_price"):
            gd.build_base_config("TST")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest -q tests/test_aspirant.py -k build_base_config`
Expected: FAIL — `AttributeError: module 'gather_data' has no attribute 'build_base_config'`

- [ ] **Step 3: Implement**

```python
def build_base_config(ticker, stock_price=0):
    """The watchlist's add-a-ticker route without Streamlit: facts only.

    Same steps as run_analysis in streamlit_app.py (EDGAR lookup, sector betas,
    financials, price, credit spread, build_config, fund_slice), minus the
    progress UI and minus a live Treasury fetch: the rate is the harmonised
    RISK_FREE_RATE_DEFAULT, as in the MCP's build_dcf_config.

    stock_price > 0 is used as given. The MCP runs on Cloud Run, where Yahoo
    blocks the IP range, so the caller brings the price from another source;
    Yahoo is only the fallback.
    """
    ticker = ticker.upper()
    cik = get_cik(ticker)
    submissions = fetch_company_submissions(cik)
    company_name = submissions.get("name", ticker)
    sic_code = int(submissions.get("sic", 0) or 0)
    sic_desc = submissions.get("sicDescription", "")
    sector_betas = resolve_sector_betas(sic_code, sic_desc)

    financials = parse_financials(fetch_company_facts(cik), n_years=6, ticker=ticker)
    if financials.get("shares"):
        financials["shares"] = apply_adr_share_ratio(financials["shares"], ticker)

    if not stock_price or stock_price <= 0:
        stock_price, _, _ = fetch_stock_price(ticker)
    if not stock_price or stock_price <= 0:
        raise ValueError(f"No price for {ticker}: pass stock_price (Yahoo is "
                         f"unreachable from this server)")

    oi = financials["operating_income"][-1] if financials.get("operating_income") else 0
    credit_rating, credit_spread = synthetic_credit_rating(
        oi, financials.get("interest_expense_latest", 0))
    shares = financials.get("shares") or []
    market_cap = round(stock_price * shares[-1], 0) if shares and shares[-1] else 0

    cfg = build_config(
        ticker=ticker, financials=financials, stock_price=stock_price,
        market_cap=market_cap, shares_yahoo=0,
        risk_free_rate=RISK_FREE_RATE_DEFAULT, sector_betas=sector_betas,
        credit_spread=credit_spread, credit_rating=credit_rating, peers=[],
        company_name=company_name, margin_of_safety=MARGIN_OF_SAFETY_DEFAULT,
        terminal_growth=TERMINAL_GROWTH_DEFAULT, sector_margin=None,
    )
    if (cfg.get("base_revenue") or 0) <= 0:
        raise ValueError(f"{company_name} has no revenue data; no DCF possible")
    if (cfg.get("base_year") or 0) < 2018:
        raise ValueError(f"{company_name}'s latest filing is too old for a DCF")

    try:
        from scorecard_utils import slim_fundamentals
        _slice = slim_fundamentals(fetch_fundamentals(ticker, n_years=10))
        if _slice:
            cfg["fund_slice"] = _slice
    except Exception as e:  # the slice is a cache; never cost the add for it
        print(f"  WARNING: fund_slice for {ticker} failed: {e}")
    return cfg
```

Check before running: `build_config`'s exact keyword names in `gather_data.py:2302` — the call above mirrors `run_analysis` (`streamlit_app.py` step 7). If a keyword differs, follow `build_config`'s signature.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest -q tests/test_aspirant.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
python3 -m ruff check gather_data.py tests/test_aspirant.py
git add gather_data.py tests/test_aspirant.py
git commit -m "gather_data.build_base_config: de add-ticker-route zonder Streamlit, koers meegeven

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `list_watchlist` geeft categorie en markeringen mee

**Files:**
- Modify: `config_store.py` (`list_watchlist`, select + output dict)
- Test: `tests/test_aspirant.py`

**Interfaces:**
- Produces: elk item van `list_watchlist` heeft ook `category` (default `"Uncategorized"`), `dcf_placeholder` (bool), `promoted_by`, `promoted_at`.

- [ ] **Step 1: Write the failing test**

```python
def test_list_watchlist_carries_category_and_markers():
    from unittest.mock import MagicMock
    from config_store import list_watchlist
    client = MagicMock()
    resp = MagicMock()
    resp.data = [{"ticker": "ABC", "company": "Abc", "stock_price": 1, "updated_at": "",
                  "category": "Aspirant", "dcf_placeholder": True,
                  "promoted_by": None, "promoted_at": None},
                 {"ticker": "OLD", "company": "Old", "stock_price": 1, "updated_at": ""}]
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = resp
    out = {e["ticker"]: e for e in list_watchlist(client, user_id="u")}
    assert out["ABC"]["category"] == "Aspirant" and out["ABC"]["dcf_placeholder"] is True
    assert out["OLD"]["category"] == "Uncategorized" and out["OLD"]["dcf_placeholder"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest -q tests/test_aspirant.py -k list_watchlist`
Expected: FAIL — `KeyError: 'category'`

- [ ] **Step 3: Implement** — in `list_watchlist`, extend the select string and the output dict:

```python
        .select("ticker, company, stock_price, updated_at, "
                "config->valuation_summary, config->robustness, "
                "config->isin, config->quote_venue, "
                "config->category, config->dcf_placeholder, "
                "config->promoted_by, config->promoted_at, "
                "config->ai_notes->Scorecard")
```

and in the `out.append({...})` dict, after `"phase": _vp["phase"],`:

```python
            "category": row.get("category") or "Uncategorized",
            "dcf_placeholder": bool(row.get("dcf_placeholder")),
            "promoted_by": row.get("promoted_by"),
            "promoted_at": row.get("promoted_at"),
```

- [ ] **Step 4: Run tests** — `python3 -m pytest -q tests/test_aspirant.py test_mcp_server.py` → PASS

- [ ] **Step 5: Commit**

```bash
python3 -m ruff check config_store.py
git add config_store.py tests/test_aspirant.py
git commit -m "list_watchlist: categorie, dcf_placeholder en promoted_* meegeven

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: MCP-tools en de placeholder-bewaking

**Files:**
- Modify: `mcp_server.py` (nieuwe impls na `_save_prescan_section_impl`; wijzigingen in `_save_to_watchlist_impl`, `_calculate_multi_lens_valuation_impl`, `_refresh_all_valuations_impl`; nieuwe `@mcp.tool()`s na `save_prescan_section`)
- Modify: `lazytheta-mcp-cloudrun/mcp_handler.py` (vier `_tool_*`-functies, vier `TOOLS`-entries, vier dispatch-regels)
- Test: `tests/test_aspirant.py`

**Interfaces:**
- Consumes: `aspirant.CATEGORIES`, `aspirant.curves_are_flat`, `aspirant.promotion_blockers` (Task 1); `gather_data.build_base_config` (Task 2); `config_store.list_watchlist` met `category` (Task 3)
- Produces (all return `str`, JSON or message; all take `user_id: str | None = None`):
  - `_get_screener_candidates_impl(limit=5, user_id=None)`
  - `_add_aspirant_impl(ticker, stock_price=0, user_id=None)`
  - `_promote_aspirant_impl(ticker, user_id=None)`
  - `_set_category_impl(ticker, category, user_id=None)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_aspirant.py (toevoegen)
import json
from unittest.mock import MagicMock


@pytest.fixture
def mcp(monkeypatch):
    import mcp_server
    client = MagicMock()
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: client)
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")
    store = {}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(t.upper(), {**store.get(t.upper(), {}), **cfg}))
    monkeypatch.setattr(mcp_server.config_store, "list_watchlist",
                        lambda c, user_id=None, tickers=None: [{"ticker": t} for t in store])
    return mcp_server, client, store


def test_candidates_skip_names_already_listed_and_sort_by_roce(mcp):
    m, client, store = mcp
    store["BBB"] = {"category": "No"}
    snap = MagicMock()
    snap.data = [{"computed_at": "2026-09-22", "rows": [
        {"ticker": "AAA", "name": "A", "avg_roce": 0.25, "net_debt": -1, "passes": True},
        {"ticker": "BBB", "name": "B", "avg_roce": 0.40, "net_debt": -1, "passes": True},
        {"ticker": "CCC", "name": "C", "avg_roce": 0.35, "net_debt": -1, "passes": True},
        {"ticker": "DDD", "name": "D", "avg_roce": 0.90, "net_debt": 5, "passes": False},
    ]}]
    client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = snap
    out = json.loads(m._get_screener_candidates_impl(limit=5))
    assert [c["ticker"] for c in out["candidates"]] == ["CCC", "AAA"]


def test_add_aspirant_refuses_an_existing_config(mcp, monkeypatch):
    m, _, store = mcp
    store["MSFT"] = {"category": "Yes", "revenue_growth": [0.1, 0.08]}
    called = MagicMock()
    monkeypatch.setattr(m.gather_data, "build_base_config", called)
    out = m._add_aspirant_impl("msft", stock_price=400)
    assert "already" in out
    called.assert_not_called()
    assert store["MSFT"]["category"] == "Yes"


def test_add_aspirant_stores_category_and_placeholder(mcp, monkeypatch):
    m, _, store = mcp
    monkeypatch.setattr(m.gather_data, "build_base_config",
                        lambda t, stock_price=0: {"stock_price": stock_price, "company": "New"})
    m._add_aspirant_impl("new", stock_price=12.5)
    assert store["NEW"]["category"] == "Aspirant"
    assert store["NEW"]["dcf_placeholder"] is True
    assert store["NEW"]["stock_price"] == 12.5
    assert store["NEW"]["aspirant_added"]


def test_save_to_watchlist_lifts_the_placeholder_once_curves_vary(mcp):
    m, _, store = mcp
    base = {"equity_market_value": 1.0, "sector_betas": [["S", 1.0, 1.0]],
            "dcf_placeholder": True}
    m._save_to_watchlist_impl("X", {**base, "revenue_growth": [0.03, 0.03], "op_margins": [0.2, 0.2]})
    assert store["X"]["dcf_placeholder"] is True
    m._save_to_watchlist_impl("X", {**base, "revenue_growth": [0.1, 0.05], "op_margins": [0.2, 0.2]})
    assert store["X"]["dcf_placeholder"] is False


def test_calculate_refuses_a_placeholder(mcp):
    m, _, store = mcp
    store["X"] = {"dcf_placeholder": True}
    assert "placeholder" in m._calculate_multi_lens_valuation_impl("X")


def test_promote_refuses_until_complete_then_moves_to_uncategorized(mcp):
    m, _, store = mcp
    store["X"] = {"category": "Aspirant", "dcf_placeholder": True, "ai_notes": {}}
    assert "Wide" in m._promote_aspirant_impl("X")
    store["X"] = {"category": "Aspirant", "dcf_placeholder": False,
                  "equity_market_value": 5.0, "sector_betas": [["S", 1.0, 1.0]],
                  "valuation_summary": {"weighted_fv_mid": 1},
                  "ai_notes": {"Moat": "**Moat: Wide · Stable · 4/5**\n\nx"}}
    out = m._promote_aspirant_impl("X")
    assert "Uncategorized" in out
    assert store["X"]["category"] == "Uncategorized"
    assert store["X"]["promoted_by"] == "claude"


def test_set_category_refuses_unknown(mcp):
    m, _, store = mcp
    store["X"] = {"category": "Aspirant"}
    assert "Unknown category" in m._set_category_impl("X", "Nope")
    m._set_category_impl("X", "No")
    assert store["X"]["category"] == "No"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest -q tests/test_aspirant.py -k "candidates or aspirant_ or save_to_watchlist or calculate or promote or set_category"`
Expected: FAIL — `AttributeError: ... has no attribute '_get_screener_candidates_impl'` (and the save/calculate tests fail on the placeholder asserts)

- [ ] **Step 3: Implement in `mcp_server.py`**

Add `import aspirant` next to the other module imports (after `import notifications`).

In `_save_to_watchlist_impl`, directly before `user_id = user_id or USER_ID`:

```python
    # A filled-in DCF lifts the aspirant's placeholder marker. Set to False
    # rather than popped: save_config merges, so an absent key would survive.
    if cfg.get("dcf_placeholder") and not aspirant.curves_are_flat(cfg):
        cfg = {**cfg, "dcf_placeholder": False}
```

In `_calculate_multi_lens_valuation_impl`, directly after the `if cfg is None:` block:

```python
    if cfg.get("dcf_placeholder"):
        return json.dumps({"error": f"{ticker.upper()} still has the placeholder "
                                    f"DCF (flat curves); fill it in and save first"})
```

In `_refresh_all_valuations_impl`, change the `targets` line:

```python
    loaded = {t: c for t, c in loaded.items() if not c.get("dcf_placeholder")}
    targets = list(loaded.keys()) if force else [t for t, c in loaded.items() if _is_stale(c)]
```

New impls, after `_save_prescan_section_impl`:

```python
def _get_screener_candidates_impl(limit=5, user_id: str | None = None):
    """Passing names from the latest Screener run that are not on the list yet."""
    user_id = user_id or USER_ID
    client = get_supabase_client()
    resp = (client.table("screener_snapshots")
            .select("computed_at, rows")
            .order("created_at", desc=True).limit(1).execute())
    if not (resp and resp.data):
        return json.dumps({"candidates": [], "computed_at": None})
    snap = resp.data[0]
    listed = {e["ticker"].upper()
              for e in config_store.list_watchlist(client, user_id=user_id)}
    rows = [r for r in snap.get("rows") or []
            if r.get("passes") and (r.get("ticker") or "").upper() not in listed]
    rows.sort(key=lambda r: r.get("avg_roce") or 0, reverse=True)
    return json.dumps({
        "computed_at": snap.get("computed_at"),
        "candidates": [{"ticker": r["ticker"], "company": r.get("name"),
                        "sector": r.get("sector"), "avg_roce": r.get("avg_roce"),
                        "net_debt": r.get("net_debt")}
                       for r in rows[:max(int(limit), 0)]],
    }, default=str)


def _add_aspirant_impl(ticker, stock_price=0, user_id: str | None = None):
    """Put a new name on the list as Aspirant; never touch an existing one."""
    from datetime import date
    user_id = user_id or USER_ID
    ticker = ticker.upper()
    client = get_supabase_client()
    if config_store.load_config(client, ticker, user_id=user_id) is not None:
        return f"{ticker} is already on the watchlist; not overwritten."
    try:
        cfg = gather_data.build_base_config(ticker, stock_price=stock_price or 0)
    except ValueError as e:
        return json.dumps({"error": str(e)})
    cfg.update({"category": "Aspirant", "dcf_placeholder": True,
                "aspirant_added": date.today().isoformat()})
    config_store.save_config(client, ticker, cfg, user_id=user_id)
    return f"Added {ticker} as Aspirant."


def _promote_aspirant_impl(ticker, user_id: str | None = None):
    """Aspirant -> Uncategorized, only with a Wide moat and a filled-in DCF."""
    from datetime import date
    user_id = user_id or USER_ID
    ticker = ticker.upper()
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker} not on watchlist"})
    blockers = aspirant.promotion_blockers(cfg)
    if blockers:
        return json.dumps({"error": f"{ticker} not promoted: " + "; ".join(blockers)})
    config_store.save_config(client, ticker, {
        **cfg, "category": "Uncategorized", "promoted_by": "claude",
        "promoted_at": date.today().isoformat()}, user_id=user_id)
    return f"Promoted {ticker} to Uncategorized."


def _set_category_impl(ticker, category, user_id: str | None = None):
    """Move a name to one of the watchlist categories (e.g. No to reject)."""
    user_id = user_id or USER_ID
    ticker = ticker.upper()
    if category not in aspirant.CATEGORIES:
        return json.dumps({"error": f"Unknown category {category!r}; use one of "
                                    f"{', '.join(aspirant.CATEGORIES)}"})
    client = get_supabase_client()
    cfg = config_store.load_config(client, ticker, user_id=user_id)
    if cfg is None:
        return json.dumps({"error": f"{ticker} not on watchlist"})
    config_store.save_config(client, ticker, {**cfg, "category": category,
                                              "promoted_by": None}, user_id=user_id)
    return f"{ticker} → {category}."
```

MCP tool wrappers, after the `save_prescan_section` tool:

```python
@mcp.tool()
def get_screener_candidates(limit: int = 5) -> str:
    """Names that pass the latest Screener run and are not on the watchlist
    yet (in any category), highest average ROCE first.

    Returns JSON {computed_at, candidates: [{ticker, company, sector,
    avg_roce, net_debt}]}.
    """
    try:
        return _get_screener_candidates_impl(limit)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def add_aspirant(ticker: str, stock_price: float = 0) -> str:
    """Add a NEW name to the watchlist in category Aspirant, with a facts-only
    base config (EDGAR) marked dcf_placeholder. Refuses if the ticker already
    has a config — it never overwrites. Pass stock_price: Yahoo is blocked on
    this server.
    """
    try:
        return _add_aspirant_impl(ticker, stock_price)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def promote_aspirant(ticker: str) -> str:
    """Move an Aspirant to Uncategorized. Refused unless the Moat section's
    verdict is Wide, the DCF is filled in (no placeholder, equity_market_value,
    sector_betas weights sum to 1.0) and a valuation_summary exists.
    """
    try:
        return _promote_aspirant_impl(ticker)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def set_category(ticker: str, category: str) -> str:
    """Set a watchlist name's category: Yes, Aspirant, Maybe, Watch Later, No
    or Uncategorized. "No" is how an aspirant is rejected.
    """
    try:
        return _set_category_impl(ticker, category)
    except Exception as e:
        return json.dumps({"error": str(e)})
```

- [ ] **Step 4: Wire the Cloud Run handler** — in `lazytheta-mcp-cloudrun/mcp_handler.py`, add after `_tool_delete_price_alert`:

```python
async def _tool_get_screener_candidates(user_id: str, args: dict) -> Any:
    return mcp_server._get_screener_candidates_impl(args.get("limit", 5), user_id=user_id)


async def _tool_add_aspirant(user_id: str, args: dict) -> Any:
    return mcp_server._add_aspirant_impl(
        args["ticker"], stock_price=args.get("stock_price", 0), user_id=user_id)


async def _tool_promote_aspirant(user_id: str, args: dict) -> Any:
    return mcp_server._promote_aspirant_impl(args["ticker"], user_id=user_id)


async def _tool_set_category(user_id: str, args: dict) -> Any:
    return mcp_server._set_category_impl(args["ticker"], args["category"], user_id=user_id)
```

append to `TOOLS`:

```python
    {
        "name": "get_screener_candidates",
        "description": ("Names passing the latest Screener run that are not on the "
                        "watchlist yet (any category), highest average ROCE first."),
        "inputSchema": {"type": "object",
                        "properties": {"limit": {"type": "integer"}}},
    },
    {
        "name": "add_aspirant",
        "description": ("Add a NEW name in category Aspirant with a facts-only base "
                        "config marked dcf_placeholder. Refuses an existing ticker; "
                        "never overwrites. Pass stock_price (Yahoo is blocked here)."),
        "inputSchema": {"type": "object",
                        "properties": {"ticker": {"type": "string"},
                                       "stock_price": {"type": "number"}},
                        "required": ["ticker"]},
    },
    {
        "name": "promote_aspirant",
        "description": ("Aspirant -> Uncategorized. Refused unless the Moat verdict is "
                        "Wide and the DCF is filled in with a valuation_summary."),
        "inputSchema": {"type": "object",
                        "properties": {"ticker": {"type": "string"}},
                        "required": ["ticker"]},
    },
    {
        "name": "set_category",
        "description": ("Set a watchlist category: Yes, Aspirant, Maybe, Watch Later, "
                        "No, Uncategorized. 'No' rejects an aspirant."),
        "inputSchema": {"type": "object",
                        "properties": {"ticker": {"type": "string"},
                                       "category": {"type": "string"}},
                        "required": ["ticker", "category"]},
    },
```

and to the dispatch dict (after `"delete_price_alert": _tool_delete_price_alert,`):

```python
    "get_screener_candidates": _tool_get_screener_candidates,
    "add_aspirant": _tool_add_aspirant,
    "promote_aspirant": _tool_promote_aspirant,
    "set_category": _tool_set_category,
```

- [ ] **Step 5: Run all tests**

Run: `python3 -m pytest -q test_*.py tests && (cd lazytheta-mcp-cloudrun && python3 -m pytest -q)`
Expected: all PASS except the known dividend test. If a Cloud Run test asserts an exact tool count, raise it by 4.

- [ ] **Step 6: Lint and commit**

```bash
python3 -m ruff check .
git add mcp_server.py lazytheta-mcp-cloudrun/mcp_handler.py tests/test_aspirant.py
git commit -m "MCP: get_screener_candidates, add_aspirant, promote_aspirant, set_category + placeholder-bewaking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Watchlist-pagina

**Files:**
- Modify: `streamlit_app.py` — `_categories` (≈ regel 4623), Status-pills (≈ regel 5016), rij-weergave (≈ regels 4576 en 4800), Refresh-all-handler (≈ regel 4395), `_default_open` (≈ regel 4950)
- Test: `tests/test_aspirant.py`

**Interfaces:**
- Consumes: `aspirant.CATEGORIES`; `config_store.save_config`; config keys `category`, `dcf_placeholder`, `promoted_by`, `promoted_at`

- [ ] **Step 1: Write the failing test** (source-level, like `tests/test_watchlist_ui.py`)

```python
def test_watchlist_page_uses_the_shared_category_list():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert "_categories = list(aspirant.CATEGORIES)" in src
    assert "_cat_options = [\"Uncategorized\", *[c for c in aspirant.CATEGORIES" in src
    assert "if not c.get(\"dcf_placeholder\")" in src   # Refresh all skips placeholders
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest -q tests/test_aspirant.py -k watchlist_page`
Expected: FAIL (assertion)

- [ ] **Step 3: Implement**

1. Add `import aspirant` to the module imports at the top of `streamlit_app.py`.
2. Replace `_categories = ["Yes", "Maybe", "Watch Later", "No", "Uncategorized"]` with:
   ```python
   _categories = list(aspirant.CATEGORIES)
   ```
3. Replace `_cat_options = ["Uncategorized", "Yes", "Maybe", "Watch Later", "No"]` with:
   ```python
   _cat_options = ["Uncategorized", *[c for c in aspirant.CATEGORIES if c != "Uncategorized"]]
   ```
   and in the same block, when the category changes, clear the Claude label:
   ```python
   if _new_cat and _new_cat != _cur_cat:
       cfg['category'] = _new_cat
       cfg['promoted_by'] = None
       save_config(_sb_client, ticker, cfg)
       st.rerun()
   ```
4. Refresh all: directly after `_refresh_cfgs = load_all_configs(_sb_client)` add:
   ```python
   # An aspirant's config is facts plus flat placeholder curves; a fair value
   # from it would read as a verdict. Its DCF gets filled in first.
   _refresh_cfgs = {t: c for t, c in _refresh_cfgs.items() if not c.get("dcf_placeholder")}
   ```
5. Row dict (≈ regel 4576): add
   ```python
   'promoted_by': cfg_wl.get('promoted_by'),
   'promoted_at': cfg_wl.get('promoted_at'),
   ```
6. In the row renderer, after the company name is written, add the label when present:
   ```python
   if row.get('promoted_by') == 'claude' and row.get('promoted_at'):
       st.caption(f"by Claude · {date.fromisoformat(row['promoted_at']):%-d %b}")
   ```
7. In the row renderer's remove-column (≈ regel 4800, the `wl_rm_row_` button), for rows in category Aspirant render a NO button before the close button:
   ```python
   if row.get('category') == 'Aspirant':
       if st.button("NO", key=f"wl_no_row_{t}", help="Reject: move to No"):
           _cfg_no = load_config(_sb_client, t)
           if _cfg_no is not None:
               _cfg_no['category'] = 'No'
               save_config(_sb_client, t, _cfg_no)
           st.cache_data.clear()
           st.rerun()
   ```
   Place it in the same column as the close button; if the column is too narrow, render both as icon buttons (`icon=":material/block:"` for NO).

- [ ] **Step 4: Run tests and look at the page**

Run: `python3 -m pytest -q test_*.py tests` → PASS (except the known one).
Run the app locally (`python3 -m streamlit run streamlit_app.py --server.port 8599 --server.headless true`), open Watchlist, and check: an Aspirant group appears once a config has `category: "Aspirant"`; the NO button moves it to No; the editor pills show Aspirant. To create a test aspirant locally without the MCP: `python3 -c "import mcp_server as m; print(m._add_aspirant_impl('ROL', stock_price=50))"` with the local Supabase env vars set — then remove it again via the row's close button.

- [ ] **Step 5: Commit**

```bash
python3 -m ruff check streamlit_app.py
git add streamlit_app.py tests/test_aspirant.py
git commit -m "Watchlist: categorie Aspirant, NO-knop, 'by Claude'-label, Refresh all slaat placeholders over

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Routine-prompt, deploy en eerste run

**Files:**
- Create: `docs/routines/aspirant-weekly.md`

- [ ] **Step 1: Write the routine prompt**

```markdown
# Aspirant weekly — routine prompt

You run weekly for the LazyTheta watchlist. Use the LazyTheta connector and
the SEC connector only. Work through the steps; one name failing never stops
the next.

1. Call `get_screener_candidates(limit=5)`. If `candidates` is empty, stop
   without doing anything else.
2. For each candidate:
   a. Get the price with the SEC connector's `GetLiveQuote`. Call
      `add_aspirant(ticker, stock_price)`. If it returns an error or says the
      name already exists, skip the name and note why. Never call
      `save_to_watchlist` for a name you did not add in this run.
   b. Call `get_prescan_prompts(ticker)`. Answer every prompt thoroughly from
      filings and your own analysis, and save each with
      `save_prescan_section(ticker, title, content)`. The Moat section must
      open with its verdict line exactly in the form
      `**Moat: <Wide|Narrow|None> <emoji> · <Stable|Eroding|Widening> <emoji> · <n>/5**`.
   c. If the Moat verdict is not Wide, the name stays an Aspirant. Move on.
   d. If the Moat verdict is Wide, fill in the full DCF: `get_config(ticker)`,
      then set revenue_growth and op_margins year by year with a short
      rationale, terminal_growth, sector_betas as [name, unlevered_beta,
      revenue_weight] with weights summing to 1.0, and equity_market_value
      ($M). Rules: nominal basis, CAPM/WACC, no SBC adjustments, margin of
      safety 20%, no peers. Save with `save_to_watchlist`. Then
      `update_dcf_scenario_adjustments`, `set_robustness`, `set_premortem`,
      `calculate_multi_lens_valuation(ticker)`, and finally
      `promote_aspirant(ticker)`. If promote refuses, note the reason; the
      name stays an Aspirant.
3. Finish with one `add_reminder` for today with the summary: which names
   were added, which were promoted (with fair value and buy price), which were
   skipped and why.
```

- [ ] **Step 2: Commit and push everything**

```bash
git add docs/routines/aspirant-weekly.md
git commit -m "Routine-prompt voor de wekelijkse aspirant-run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git -c credential.helper='!gh auth git-credential' push origin main
git ls-remote origin main   # must equal git rev-parse HEAD
```

- [ ] **Step 3: Deploy**

1. Streamlit Cloud deploys `main` automatically. Because `gather_data.py`, `config_store.py` and the new `aspirant.py` changed, the user must do **Manage app → Reboot** — say so explicitly.
2. MCP: `gcloud run deploy lazytheta-mcp --source . --region europe-west4 --project stock-analysis-489016` from the repo root. Verify the new revision serves 100% traffic and that `tools/list` contains the four new tools.

- [ ] **Step 4: Create the routine**

Use `/schedule`: repo `lazytheta/stock-analysis`, weekly Monday 07:00 Europe/Amsterdam, connectors LazyTheta Remote MCP and SEC, prompt = contents of `docs/routines/aspirant-weekly.md`.

- [ ] **Step 5: First run together**

Trigger one run manually. Check with the user: the Aspirant group in the watchlist, the pre-scan of each new name, any promoted name's DCF, and the reminder. Adjust the prompt if needed before the first scheduled Monday.
