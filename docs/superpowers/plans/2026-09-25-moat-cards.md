# Moat Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A "Moat" tab on the ticker page with two summary cards from the existing Moat Analysis and five flip cards (question, three-state answer, one-line summary; back side: three points) from a new JSON pre-scan section "Moat Cards".

**Architecture:** All rules and HTML live in a new pure module `moat_cards.py` (sources, parser, renderers). The MCP validates "Moat Cards" on save and gains `tickers_missing_section`. The prompt text lives in `moat_cards.py` and is referenced from `DEFAULT_AI_PROMPTS`; a script adds it to the owner's prompt library. Streamlit gets one new tab that only calls `moat_cards` renderers.

**Tech Stack:** Python 3.11, Streamlit 1.54 (`st.markdown(..., unsafe_allow_html=True)`), Supabase via `config_store`, FastMCP + Cloud Run JSON-RPC handler, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-moat-cards-design.md`

## Global Constraints

- Python 3.11: no backslash inside an f-string expression.
- `python3 -m ruff check .` must pass; `python3 -m pytest -q test_*.py tests` all green except the known `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer`; `(cd lazytheta-mcp-cloudrun && python3 -m pytest -q)` green.
- Section title, exact: `Moat Cards`. Source keys and order, exact: `switching_costs`, `network_effects`, `intangible_assets`, `low_cost`, `counter_positioning`.
- `pick` ∈ {0,1,2} (0 red, 1 yellow, 2 green — higher = stronger moat); `direction` ∈ {`widening`, `stable`, `narrowing`}; exactly three points, each with non-empty `label` and `text`.
- `mcp_server.py` imports `moat_cards` at top level → `moat_cards.py` must be in the root `Dockerfile` COPY list (enforced by `tests/test_dockerfile_modules.py`). `moat_cards` must NOT import `prescan_render` at module level (the Cloud Run image does not ship it); import it inside the render functions.
- No JavaScript. Flip = hidden checkbox inside a `<label>` + CSS.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File Structure

| File | Role |
|---|---|
| `moat_cards.py` (new) | `TITLE`, `SOURCES`, `DIRECTIONS`, `PROMPT`, `parse_moat_cards`, renderers |
| `tests/test_moat_cards.py` (new) | Tests for parser, renderers, MCP bits, prompt wiring |
| `mcp_server.py` | Validate on save; `tickers_missing_section` impl + tool |
| `lazytheta-mcp-cloudrun/mcp_handler.py` + `test_app.py` | New tool wiring; tool count 32 → 33 |
| `Dockerfile` | COPY `moat_cards.py` |
| `streamlit_app.py` | `DEFAULT_AI_PROMPTS` entry; new "Moat" tab |
| `scripts/add_moat_cards_prompt.py` (new) | Adds the prompt to a user's library once |
| `docs/routines/moat-cards-backfill.md` (new) | Backfill routine prompt |

---

### Task 1: `moat_cards.py` — sources, prompt and parser

**Files:** Create `moat_cards.py`; Test `tests/test_moat_cards.py`

**Interfaces — Produces:**
- `TITLE = "Moat Cards"`
- `SOURCES: tuple[tuple[str, str, str, tuple[str, str, str]], ...]` — `(key, name, question, options)`
- `DIRECTIONS = ("widening", "stable", "narrowing")`
- `PROMPT: str`
- `parse_moat_cards(content: str) -> dict` — returns `{"cards": [ {source, pick, direction, summary, points:[{label,text}×3]} ×5 ]}`; raises `ValueError` with a readable reason.

- [ ] **Step 1: failing tests**

```python
# tests/test_moat_cards.py
import json

import pytest

import moat_cards


def _card(source, pick=2, direction="stable"):
    return {"source": source, "pick": pick, "direction": direction,
            "summary": f"{source} summary.",
            "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}


def _payload(**over):
    cards = [_card(k) for k, *_ in moat_cards.SOURCES]
    for i, c in enumerate(cards):
        c.update(over.get(i, {}))
    return {"cards": cards}


def test_sources_are_the_five_in_order():
    assert [s[0] for s in moat_cards.SOURCES] == [
        "switching_costs", "network_effects", "intangible_assets",
        "low_cost", "counter_positioning"]
    assert all(len(s[3]) == 3 for s in moat_cards.SOURCES)


def test_parse_accepts_raw_and_fenced_json():
    raw = json.dumps(_payload())
    assert len(moat_cards.parse_moat_cards(raw)["cards"]) == 5
    fenced = "```json\n" + raw + "\n```"
    assert moat_cards.parse_moat_cards(fenced)["cards"][0]["source"] == "switching_costs"


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p["cards"].pop(), "five sources"),
    (lambda p: p["cards"].reverse(), "five sources"),
    (lambda p: p["cards"][0].update(pick=3), "pick"),
    (lambda p: p["cards"][0].update(pick=True), "pick"),
    (lambda p: p["cards"][1].update(direction="up"), "direction"),
    (lambda p: p["cards"][2].update(summary=" "), "summary"),
    (lambda p: p["cards"][3]["points"].pop(), "three points"),
    (lambda p: p["cards"][4]["points"][0].update(text=""), "three points"),
])
def test_parse_refuses_malformed_cards(mutate, fragment):
    p = _payload()
    mutate(p)
    with pytest.raises(ValueError, match=fragment):
        moat_cards.parse_moat_cards(json.dumps(p))


def test_parse_refuses_non_json():
    with pytest.raises(ValueError, match="JSON"):
        moat_cards.parse_moat_cards("**Moat: Wide**")


def test_prompt_uses_prior_moat_analysis_and_names_every_source():
    assert "{prior:Moat Analysis}" in moat_cards.PROMPT
    for key, *_ in moat_cards.SOURCES:
        assert key in moat_cards.PROMPT
```

- [ ] **Step 2:** `python3 -m pytest -q tests/test_moat_cards.py` → FAIL (`No module named 'moat_cards'`)

- [ ] **Step 3: implement `moat_cards.py`**

```python
"""Moat Cards: five question cards per ticker, from the Moat Analysis.

The questions and their answer scales live here, not in the model's output,
so every ticker's cards are comparable and a typo in a prompt cannot rename a
scale. The model only picks an answer, a direction and writes the text.

No module-level import of prescan_render: mcp_server imports this module for
the parser, and the Cloud Run image does not ship prescan_render.
"""

import json
import re

TITLE = "Moat Cards"

# (key, source name, question, answers from weakest to strongest)
SOURCES = (
    ("switching_costs", "Switching costs", "How hard is it to switch?",
     ("Easy", "Moderate", "Hard")),
    ("network_effects", "Network effects", "Does scale help customers?",
     ("No", "Somewhat", "Yes")),
    ("intangible_assets", "Intangible assets", "Does the brand or IP earn a premium?",
     ("No", "Some", "Strong")),
    ("low_cost", "Low-cost production", "Is there a cost advantage?",
     ("No", "Some", "Large")),
    ("counter_positioning", "Counter-positioning", "Would copying hurt incumbents?",
     ("No", "Partly", "Yes")),
)
DIRECTIONS = ("widening", "stable", "narrowing")

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)

PROMPT = """You are turning an existing moat analysis of **{company} ({ticker})** into five
question cards. Base every call on the analysis below and on reported numbers;
do not contradict its verdict.

{prior:Moat Analysis}

For each of the five moat sources, in exactly this order, decide:
- switching_costs: How hard is it to switch? pick 0 = Easy, 1 = Moderate, 2 = Hard
- network_effects: Does scale help customers? pick 0 = No, 1 = Somewhat, 2 = Yes
- intangible_assets: Does the brand or IP earn a premium? pick 0 = No, 1 = Some, 2 = Strong
- low_cost: Is there a cost advantage? pick 0 = No, 1 = Some, 2 = Large
- counter_positioning: Would copying the model hurt incumbents? pick 0 = No, 1 = Partly, 2 = Yes
  (only when incumbents would harm themselves by copying; being different is not enough)

direction: "widening", "stable" or "narrowing" - is this source getting stronger or weaker.
summary: ONE sentence for the front of the card, with the fact that decides the pick.
points: EXACTLY three, each {"label": two to four words, "text": one line with a number or
a fact from the filings}. For an absent source, say what you looked for and why it is absent.

Output ONLY a fenced JSON block, nothing before or after:

```json
{"cards": [
  {"source": "switching_costs", "pick": 1, "direction": "stable",
   "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
]}
```
The array holds all five sources, in the order listed above.
"""


def parse_moat_cards(content):
    """The validated cards, or ValueError saying what is wrong."""
    text = (content or "").strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON ({e.msg})") from None
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        raise ValueError('expected an object {"cards": [...]}')
    keys = [s[0] for s in SOURCES]
    got = [c.get("source") if isinstance(c, dict) else None for c in cards]
    if got != keys:
        raise ValueError("cards must be the five sources in order: " + ", ".join(keys))

    out = []
    for c in cards:
        src = c["source"]
        pick = c.get("pick")
        if isinstance(pick, bool) or pick not in (0, 1, 2):
            raise ValueError(f"{src}: pick must be 0, 1 or 2")
        direction = str(c.get("direction") or "").strip().lower()
        if direction not in DIRECTIONS:
            raise ValueError(f"{src}: direction must be one of {', '.join(DIRECTIONS)}")
        summary = str(c.get("summary") or "").strip()
        if not summary:
            raise ValueError(f"{src}: summary is empty")
        points = c.get("points")
        ok = isinstance(points, list) and len(points) == 3 and all(
            isinstance(p, dict) and str(p.get("label") or "").strip()
            and str(p.get("text") or "").strip() for p in points)
        if not ok:
            raise ValueError(f"{src}: needs exactly three points, each with label and text")
        out.append({"source": src, "pick": pick, "direction": direction,
                    "summary": summary,
                    "points": [{"label": str(p["label"]).strip(),
                                "text": str(p["text"]).strip()} for p in points]})
    return {"cards": out}
```

- [ ] **Step 4:** `python3 -m pytest -q tests/test_moat_cards.py` → PASS
- [ ] **Step 5:** ruff + full suite, then commit `Moat Cards: bronnen, prompt en parser`.

---

### Task 2: MCP — validate on save, `tickers_missing_section`, Cloud Run wiring

**Files:** Modify `mcp_server.py`, `lazytheta-mcp-cloudrun/mcp_handler.py`, `lazytheta-mcp-cloudrun/test_app.py`, `Dockerfile`; Test `tests/test_moat_cards.py`

**Interfaces — Consumes:** `moat_cards.TITLE`, `moat_cards.parse_moat_cards` (Task 1); `config_store.load_all_configs(client, user_id=None, include_ai_notes=True) -> {TICKER: cfg}`.
**Produces:** `_tickers_missing_section_impl(title, requires="", limit=10, user_id=None) -> str` (JSON `{"tickers": [...], "remaining": int}`); tool `tickers_missing_section`.

- [ ] **Step 1: failing tests** (append to `tests/test_moat_cards.py`)

```python
from unittest.mock import MagicMock


@pytest.fixture
def mcp(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")
    store = {}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(
                            t.upper(), {**store.get(t.upper(), {}), **cfg}))
    monkeypatch.setattr(mcp_server.config_store, "load_all_configs",
                        lambda c, user_id=None, include_ai_notes=True: dict(store))
    return mcp_server, store


def test_save_refuses_malformed_moat_cards(mcp):
    m, store = mcp
    store["X"] = {"ai_notes": {}}
    out = m._save_prescan_section_impl("X", "Moat Cards", '{"cards": []}')
    assert "error" in out and "Moat Cards" in out["error"]
    assert "Moat Cards" not in store["X"]["ai_notes"]


def test_save_accepts_valid_moat_cards(mcp):
    m, store = mcp
    store["X"] = {"ai_notes": {}}
    m._save_prescan_section_impl("X", "Moat Cards", json.dumps(_payload()))
    assert "Moat Cards" in store["X"]["ai_notes"]


def test_other_sections_are_not_validated(mcp):
    m, store = mcp
    store["X"] = {"ai_notes": {}}
    m._save_prescan_section_impl("X", "Moat Analysis", "free text")
    assert store["X"]["ai_notes"]["Moat Analysis"] == "free text"


def test_tickers_missing_section(mcp):
    m, store = mcp
    store.update({
        "AAA": {"ai_notes": {"Moat Analysis": "x"}},
        "BBB": {"ai_notes": {"Moat Analysis": "x", "Moat Cards": "{}"}},
        "CCC": {"ai_notes": {}},
        "DDD": {"ai_notes": {"Moat Analysis": "y"}},
    })
    out = json.loads(m._tickers_missing_section_impl(
        "Moat Cards", requires="Moat Analysis", limit=1))
    assert out == {"tickers": ["AAA"], "remaining": 2}
```

- [ ] **Step 2:** run → FAIL.

- [ ] **Step 3: implement**
  1. `mcp_server.py`: add `import moat_cards` next to `import aspirant`.
  2. In `_save_prescan_section_impl`, directly after the `if not title.strip():` block:
     ```python
         # Moat Cards are structured: a malformed block would render as a broken
         # tab, so it is refused here with the reason rather than stored.
         if title.strip() == moat_cards.TITLE:
             try:
                 moat_cards.parse_moat_cards(content)
             except ValueError as e:
                 return {"error": f"Moat Cards not saved: {e}"}
     ```
  3. New impl after `_save_prescan_section_impl`:
     ```python
     def _tickers_missing_section_impl(title, requires="", limit=10,
                                       user_id: str | None = None):
         """Watchlist tickers without pre-scan section `title` (and, when
         `requires` is given, that do have that section), alphabetical."""
         user_id = user_id or USER_ID
         client = get_supabase_client()
         cfgs = config_store.load_all_configs(client, user_id=user_id)
         missing = []
         for t, cfg in sorted(cfgs.items()):
             notes = cfg.get("ai_notes") if isinstance(cfg.get("ai_notes"), dict) else {}
             if str(notes.get(title) or "").strip():
                 continue
             if requires and not str(notes.get(requires) or "").strip():
                 continue
             missing.append(t)
         n = max(int(limit or 10), 0)
         return json.dumps({"tickers": missing[:n], "remaining": len(missing) - len(missing[:n])})
     ```
  4. Tool wrapper after `save_prescan_section`'s tool:
     ```python
     @mcp.tool()
     def tickers_missing_section(title: str, requires: str = "", limit: int = 10) -> str:
         """Watchlist tickers that lack pre-scan section `title` — e.g. "Moat Cards"
         with requires="Moat Analysis" for a backfill. Returns JSON
         {tickers: [...], remaining: n}.
         """
         try:
             return _tickers_missing_section_impl(title, requires, limit)
         except Exception as e:
             return json.dumps({"error": str(e)})
     ```
  5. Cloud Run `mcp_handler.py`: handler
     ```python
     async def _tool_tickers_missing_section(user_id: str, args: dict) -> Any:
         return mcp_server._tickers_missing_section_impl(
             args["title"], requires=args.get("requires") or "",
             limit=args.get("limit") or 10, user_id=user_id)
     ```
     `TOOLS` entry `{"name": "tickers_missing_section", "description": "Watchlist tickers lacking a pre-scan section (optionally requiring another), for backfills.", "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "requires": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["title"]}}`; dispatch `"tickers_missing_section": _tool_tickers_missing_section,`.
  6. `lazytheta-mcp-cloudrun/test_app.py`: tool count 32 → 33, add the name to the expected set, update the docstring count.
  7. `Dockerfile`: append `moat_cards.py` to the COPY line.

- [ ] **Step 4:** run `tests/test_moat_cards.py`, `tests/test_dockerfile_modules.py`, full suite, cloudrun suite → PASS.
- [ ] **Step 5:** commit `MCP: Moat Cards valideren bij opslaan + tickers_missing_section`.

---

### Task 3: Prompt in the library

**Files:** Modify `streamlit_app.py` (`DEFAULT_AI_PROMPTS`, ≈ line 1269; insert after the "Moat Analysis" entry that ends ≈ line 1636); Create `scripts/add_moat_cards_prompt.py`; Test `tests/test_moat_cards.py`

**Interfaces — Consumes:** `moat_cards.TITLE`, `moat_cards.PROMPT`; `config_store.load_user_prefs(client, user_id=None) -> dict`, `config_store.save_user_prefs(client, prefs, user_id=None)`.
**Produces:** `scripts/add_moat_cards_prompt.insert_prompt(library: list[dict]) -> tuple[list[dict], bool]`.

- [ ] **Step 1: failing tests**

```python
def test_default_prompts_put_moat_cards_right_after_moat_analysis():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert titles[titles.index("Moat Analysis") + 1] == "Moat Cards"
    entry = streamlit_app.DEFAULT_AI_PROMPTS[titles.index("Moat Cards")]
    assert entry["prompt"] is moat_cards.PROMPT


def test_insert_prompt_is_idempotent_and_keeps_the_rest():
    from scripts.add_moat_cards_prompt import insert_prompt
    lib = [{"title": "Business Analysis", "prompt": "a"},
           {"title": "Moat Analysis", "prompt": "b"},
           {"title": "Risk Analysis", "prompt": "c"}]
    new, changed = insert_prompt(lib)
    assert changed and [p["title"] for p in new] == [
        "Business Analysis", "Moat Analysis", "Moat Cards", "Risk Analysis"]
    again, changed2 = insert_prompt(new)
    assert not changed2 and again == new
    assert lib[1]["prompt"] == "b"            # input untouched
```

If `import streamlit_app` is too heavy in this test suite, follow how existing tests import it (e.g. `tests/test_track_record.py` does `import streamlit_app`).

- [ ] **Step 2:** run → FAIL.

- [ ] **Step 3: implement**
  1. `streamlit_app.py`: `import moat_cards` with the other local imports; in `DEFAULT_AI_PROMPTS` insert right after the Moat Analysis dict:
     ```python
         {
             # Five question cards for the Moat tab, built on the analysis above.
             "title": moat_cards.TITLE,
             "prompt": moat_cards.PROMPT,
         },
     ```
  2. `scripts/add_moat_cards_prompt.py` (ensure `scripts/` is importable in tests — add an empty `scripts/__init__.py` only if one is needed and absent):
     ```python
     """Add the "Moat Cards" prompt to a user's pre-scan library, once.

     Dry run by default; --apply writes. Inserts right after "Moat Analysis"
     (or at the end if that prompt is missing) and leaves every other prompt
     as it is.

         SUPABASE_URL=... SUPABASE_SERVICE_KEY=... \
             python3 scripts/add_moat_cards_prompt.py --user-id <uuid> [--apply]
     """
     import argparse
     import os
     import sys

     sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

     import moat_cards  # noqa: E402


     def insert_prompt(library):
         """(new_library, changed). Never mutates the input."""
         lib = [dict(p) for p in (library or [])]
         titles = [p.get("title") for p in lib]
         if moat_cards.TITLE in titles:
             return lib, False
         entry = {"title": moat_cards.TITLE, "prompt": moat_cards.PROMPT}
         at = titles.index("Moat Analysis") + 1 if "Moat Analysis" in titles else len(lib)
         lib.insert(at, entry)
         return lib, True


     def main():
         ap = argparse.ArgumentParser(description=__doc__)
         ap.add_argument("--user-id", required=True)
         ap.add_argument("--apply", action="store_true")
         args = ap.parse_args()
         from supabase import create_client
         import config_store
         client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
         prefs = config_store.load_user_prefs(client, user_id=args.user_id)
         new, changed = insert_prompt(prefs.get("ai_prompts") or [])
         if not changed:
             print("Moat Cards already in the library; nothing to do.")
             return
         print("Would insert Moat Cards at position",
               [p["title"] for p in new].index(moat_cards.TITLE) + 1, "of", len(new))
         if args.apply:
             prefs["ai_prompts"] = new
             config_store.save_user_prefs(client, prefs, user_id=args.user_id)
             print("Saved.")


     if __name__ == "__main__":
         main()
     ```
  Check `config_store.load_user_prefs` / `save_user_prefs` signatures before relying on them; if `save_user_prefs` merges or replaces, keep whichever preserves all other prefs keys.

- [ ] **Step 4:** tests + ruff + full suite → PASS. Do NOT run the script against Supabase (the controller does that with the user).
- [ ] **Step 5:** commit `Moat Cards-prompt in de standaardbibliotheek + script om hem toe te voegen`.

---

### Task 4: Renderers in `moat_cards.py`

**Files:** Modify `moat_cards.py`; Test `tests/test_moat_cards.py`

**Interfaces — Consumes:** `prescan_render.parse_verdict_section(content) -> dict|None` (keys `label`, `qualifiers`, `score`, `out_of`, `summary`, `bullets`, `footer_label`, `footer_text`), `prescan_render.band_tone(label) -> str|None`, `prescan_render.three_state_html(band, labels=None, size=44, theme=None) -> str` — all imported INSIDE the functions.
**Produces:**
- `STYLE: str` — the `<style>` block for grid + flip.
- `flip_card_html(card: dict, theme: dict) -> str`
- `sources_row_html(cards: list[dict], theme: dict) -> str`
- `direction_card_html(moat_analysis: str, theme: dict) -> str | None`
- `cards_section_html(content: str | None, theme: dict) -> str` — STYLE + sources row + grid of five, or a one-line notice when content is missing/invalid.

`theme` keys used: `text`, `text_muted`, `divider`, `bg_secondary`.

- [ ] **Step 1: failing tests**

```python
THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}


def test_flip_card_shows_pick_direction_and_three_points_on_the_back():
    card = moat_cards.parse_moat_cards(json.dumps(_payload(**{0: {"pick": 1, "direction": "widening"}})))["cards"][0]
    html = moat_cards.flip_card_html(card, THEME)
    assert 'type="checkbox"' in html and 'class="mc-flip"' in html
    assert "How hard is it to switch?" in html
    assert html.count('data-active="1"') == 1           # one lit answer
    assert "MODERATE" in html.upper() and "widening" in html.lower()
    assert all(f"L{i}" in html and f"T{i}" in html for i in range(3))
    assert "Back to summary" in html


def test_sources_row_has_a_dot_per_source():
    cards = moat_cards.parse_moat_cards(json.dumps(_payload()))["cards"]
    html = moat_cards.sources_row_html(cards, THEME)
    for _key, name, *_ in moat_cards.SOURCES:
        assert name.upper() in html.upper()


def test_direction_card_reads_the_verdict_line():
    text = ("**Moat: Wide 🛡️ · Narrowing ↘️ · 4/5**\n\nX has a **wide moat**.\n\n"
            "- **A**: one\n- **B**: two\n- **C**: three\n\n**Weakest link:** pricing.")
    html = moat_cards.direction_card_html(text, THEME)
    assert "NARROWING" in html.upper() and "pricing" in html
    assert moat_cards.direction_card_html("free text", THEME) is None


def test_cards_section_without_data_shows_a_notice_not_a_crash():
    for content in (None, "", "not json"):
        html = moat_cards.cards_section_html(content, THEME)
        assert "Moat Cards" in html and "mc-card" not in html


def test_cards_section_with_data_renders_five_cards_and_the_style():
    html = moat_cards.cards_section_html(json.dumps(_payload()), THEME)
    assert html.count('class="mc-card"') == 5 and "<style>" in html
```

Before finalizing, confirm with one quick call that `parse_verdict_section` accepts the direction-card test text (it needs bullets and returns `qualifiers`); adjust the test text, not the parser, if needed.

- [ ] **Step 2:** run → FAIL.

- [ ] **Step 3: implement** (append to `moat_cards.py`)

```python
from html import escape as _esc

_BANDS = ("red", "yellow", "green")
_ARROW = {"widening": "↗", "stable": "→", "narrowing": "↘"}
_BACK_BG = "#2b2b2f"
_BACK_TEXT = "#f2f0ea"

STYLE = """<style>
.mc-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin-top:14px}
@media (max-width:760px){.mc-grid{grid-template-columns:1fr}}
.mc-card{display:block;perspective:1200px;cursor:pointer;height:270px;margin:0}
.mc-flip{display:none}
.mc-inner{position:relative;width:100%;height:100%;transition:transform .5s ease;
  transform-style:preserve-3d}
.mc-flip:checked + .mc-inner{transform:rotateY(180deg)}
.mc-face{position:absolute;inset:0;backface-visibility:hidden;-webkit-backface-visibility:hidden;
  border-radius:16px;padding:16px 20px;box-sizing:border-box;overflow:hidden;
  display:flex;flex-direction:column}
.mc-back{transform:rotateY(180deg)}
.mc-q{font-size:.7rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase}
.mc-tag{font-size:.66rem;font-weight:700;letter-spacing:.05em;padding:2px 8px;
  border-radius:9px;text-transform:uppercase;white-space:nowrap}
.mc-foot{margin-top:auto;font-size:.8rem;font-weight:600;text-align:center;padding-top:8px}
</style>"""


def _source(key):
    return next(s for s in SOURCES if s[0] == key)


def flip_card_html(card, theme):
    from prescan_render import band_tone, three_state_html
    _key, _name, question, options = _source(card["source"])
    tone = band_tone(_BANDS[card["pick"]])
    arrow = _ARROW[card["direction"]]
    tag = (f'<span class="mc-tag" style="background:{tone}22;color:{tone}">'
           f'{_esc(card["direction"])} {arrow}</span>')
    front = (
        f'<div class="mc-face" style="background:{theme["bg_secondary"]};color:{theme["text"]}">'
        f'<div style="display:flex;justify-content:space-between;gap:8px">'
        f'<span class="mc-q" style="color:{theme["text_muted"]}">{_esc(question)}</span>{tag}</div>'
        f'<div style="margin:16px 0 12px">'
        f'{three_state_html(_BANDS[card["pick"]], labels=options, size=40, theme=theme)}</div>'
        f'<div style="border-top:1px solid {theme["divider"]};padding-top:10px;'
        f'font-size:.86rem;line-height:1.45;text-align:center">{_esc(card["summary"])}</div>'
        f'<div class="mc-foot" style="color:{theme["text"]}">Details →</div></div>')
    points = "".join(
        f'<div style="border-left:3px solid {tone};padding:1px 0 1px 10px;margin:0 0 9px;'
        f'font-size:.84rem;line-height:1.45"><b>{_esc(p["label"])}</b>: {_esc(p["text"])}</div>'
        for p in card["points"])
    back = (
        f'<div class="mc-face mc-back" style="background:{_BACK_BG};color:{_BACK_TEXT}">'
        f'<div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:12px">'
        f'<span class="mc-q" style="color:{tone}">{_esc(_name)}</span>'
        f'<span class="mc-tag" style="background:{tone}33;color:{tone}">'
        f'{_esc(options[card["pick"]])}</span></div>{points}'
        f'<div class="mc-foot" style="color:rgba(242,240,234,.6);text-align:left;'
        f'font-weight:500">Back to summary ↩</div></div>')
    return (f'<label class="mc-card"><input type="checkbox" class="mc-flip">'
            f'<div class="mc-inner">{front}{back}</div></label>')


def sources_row_html(cards, theme):
    from prescan_render import band_tone
    by_key = {c["source"]: c for c in cards}
    cells = []
    for key, name, *_ in SOURCES:
        c = by_key.get(key)
        tone = band_tone(_BANDS[c["pick"]]) if c else None
        dot = (f'background:{tone}' if c and c["pick"] > 0
               else f'border:1.5px solid {theme["text_muted"]}')
        cells.append(
            f'<div style="text-align:center;flex:1;min-width:90px">'
            f'<div style="width:12px;height:12px;border-radius:50%;margin:0 auto 6px;{dot}"></div>'
            f'<div class="mc-q" style="color:{theme["text_muted"]}">{_esc(name)}</div></div>')
    return (f'<div style="display:flex;flex-wrap:wrap;gap:8px;background:{theme["bg_secondary"]};'
            f'border-radius:14px;padding:14px 10px;margin-top:6px">{"".join(cells)}</div>')


def direction_card_html(moat_analysis, theme):
    """Direction from the Moat Analysis verdict line, with its weakest link."""
    from prescan_render import band_tone, parse_verdict_section
    v = parse_verdict_section(moat_analysis or "")
    if not v:
        return None
    word = next((q for q in v["qualifiers"]
                 if q.strip().lower() in ("widening", "stable", "narrowing")), "")
    if not word:
        return None
    tone = band_tone({"widening": "green", "stable": "yellow",
                      "narrowing": "red"}[word.strip().lower()])
    foot = ""
    if v["footer_text"]:
        foot = (f'<div style="margin-top:12px;font-size:.86rem;color:{theme["text_muted"]}">'
                f'<b style="color:{theme["text"]}">{_esc(v["footer_label"] or "Weakest link")}:</b> '
                f'{_esc(v["footer_text"])}</div>')
    return (f'<div style="background:{theme["bg_secondary"]};border-radius:16px;padding:20px 22px">'
            f'<div style="display:flex;gap:18px;align-items:center">'
            f'<div style="background:{_BACK_BG};border-radius:14px;padding:18px 20px;'
            f'text-align:center;min-width:132px">'
            f'<div style="font-size:2rem;color:{tone}">{_ARROW[word.strip().lower()]}</div>'
            f'<div style="color:rgba(255,255,255,.72);font-size:.7rem;font-weight:700;'
            f'letter-spacing:.08em">{_esc(word.upper())}</div></div>'
            f'<div style="font-size:.95rem;line-height:1.5;color:{theme["text"]}">'
            f'Is the moat getting stronger or weaker?</div></div>{foot}</div>')


def cards_section_html(content, theme):
    """Style + sources row + the five flip cards, or a one-line notice."""
    try:
        cards = parse_moat_cards(content)["cards"] if content else None
    except ValueError:
        cards = None
    if not cards:
        return (f'<div style="color:{theme["text_muted"]};font-size:.9rem;padding:14px 2px">'
                f'No Moat Cards yet. Ask Claude via the MCP to fill the "Moat Cards" '
                f'pre-scan section for this ticker.</div>')
    grid = "".join(flip_card_html(c, theme) for c in cards)
    return f'{STYLE}{sources_row_html(cards, theme)}<div class="mc-grid">{grid}</div>'
```
Keep `from html import escape as _esc` and the constants near the top of the module with the other imports/constants (ruff E402).

- [ ] **Step 4:** tests + ruff + full suite → PASS.
- [ ] **Step 5:** commit `Moat Cards: kaarten die omdraaien, bronnenrij en richtingkaart`.

---

### Task 5: The "Moat" tab

**Files:** Modify `streamlit_app.py` (tabs tuple ≈ line 5096; `import moat_cards` already added in Task 3); Test `tests/test_moat_cards.py`

**Interfaces — Consumes:** `_verdict_card_html(content, title="") -> str|None` (existing, streamlit_app.py ≈ 9212), `moat_cards.direction_card_html`, `moat_cards.cards_section_html`, theme dict `T`.

- [ ] **Step 1: failing test** (source-level, like `tests/test_watchlist_ui.py`)

```python
def test_ticker_page_has_a_moat_tab_after_pre_scan():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert '["Pre-Scan", "Moat", "Fundamentals", "DCF"' in src
    assert "moat_cards.cards_section_html(" in src
    assert "moat_cards.direction_card_html(" in src
```

- [ ] **Step 2:** run → FAIL.

- [ ] **Step 3: implement** — change the tabs unpacking to
```python
    (_tab_notes, _tab_moat, _tab_fundamentals, _tab_dcf, _tab_rdcf, _tab_peers,
     _tab_dividend, _tab_history) = st.tabs(
        ["Pre-Scan", "Moat", "Fundamentals", "DCF", "Reverse DCF", "Peer Comparison",
         "Dividend", "History"])
```
and add, right after that statement:
```python
    # Moat: the Moat Analysis as two summary cards, then the five question cards
    # from the "Moat Cards" section. Read-only; Pre-Scan stays the place to edit.
    with _tab_moat:
        _notes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else {}
        _moat_text = _notes.get("Moat Analysis") or ""
        _mc_left, _mc_right = st.columns(2)
        with _mc_left:
            st.markdown("##### Moat size")
            _size = _verdict_card_html(_moat_text, "Moat Analysis")
            if _size:
                st.markdown(_size, unsafe_allow_html=True)
            else:
                st.caption("No Moat Analysis in the verdict format yet.")
        with _mc_right:
            st.markdown("##### Moat direction")
            _dir = moat_cards.direction_card_html(_moat_text, T)
            if _dir:
                st.markdown(_dir, unsafe_allow_html=True)
            else:
                st.caption("No direction in the Moat Analysis verdict yet.")
        st.markdown("##### Moat sources")
        st.markdown(moat_cards.cards_section_html(_notes.get(moat_cards.TITLE), T),
                    unsafe_allow_html=True)
```
- [ ] **Step 4:** tests + `python3 -m py_compile streamlit_app.py` + ruff + full suite → PASS. Report where the tab renders; the controller does the visual check.
- [ ] **Step 5:** commit `Tickerpagina: tab Moat met samenvatting en vraagkaarten`.

---

### Task 6: Backfill routine prompt

**Files:** Create `docs/routines/moat-cards-backfill.md`

- [ ] **Step 1:** write:

```markdown
# Moat Cards backfill — routine prompt

You fill the "Moat Cards" pre-scan section for LazyTheta watchlist tickers that
have a Moat Analysis but no Moat Cards yet. Use only the Lazy-Theta-Remote-MCP
connector. Do not modify, commit or push anything in the repository.

1. Call `tickers_missing_section(title="Moat Cards", requires="Moat Analysis", limit=10)`.
   If `tickers` is empty, stop and print "Nothing left to backfill."
2. For each ticker:
   a. Call `get_prescan_prompts(ticker)` and take the prompt titled "Moat Cards"
      (its {prior:Moat Analysis} is already filled in).
   b. Call `get_fundamentals(ticker)` for the numbers you cite.
   c. Answer the prompt exactly as it asks: one fenced JSON block, five cards in
      the listed order.
   d. Save with `save_prescan_section(ticker, "Moat Cards", <the JSON block>)`.
      If the server refuses, fix what the error names and save once more; if it
      refuses again, note the ticker and the reason and move on.
3. Print a summary: tickers filled, tickers skipped with reasons, and `remaining`.
```
- [ ] **Step 2:** commit `Routine-prompt voor het bijvullen van Moat Cards`.

Deploy, the Supabase prompt-library script, and creating the backfill routine are done by the controller with the user after merge.
