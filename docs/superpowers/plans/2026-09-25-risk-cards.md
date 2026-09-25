# Risk Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shared question-card engine (`question_cards.py`) extracted from `moat_cards.py`, a Risk card set (`risk_cards.py`), and a "Risk" tab on the ticker page (two equal summary cards + four flip cards), with MCP validation and backfill.

**Architecture:** `question_cards.py` owns everything generic (escaping, CSS, `CardSet`, parser, flip card, grid, notice, summary-card helpers). `moat_cards.py` and `risk_cards.py` are thin definitions + their own summary rows. `mcp_server` validates by title through a `{title: parser}` map. Streamlit adds one tab.

**Tech Stack:** Python 3.11, Streamlit 1.54 markdown HTML, pytest, FastMCP/Cloud Run.

**Spec:** `docs/superpowers/specs/2026-09-25-risk-cards-design.md`

## Global Constraints

- Python 3.11: no backslash inside an f-string expression.
- `python3 -m ruff check .` passes; `python3 -m pytest -q test_*.py tests` all green except the known `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer`; `(cd lazytheta-mcp-cloudrun && python3 -m pytest -q)` green.
- No module-level import of `prescan_render` in `question_cards`, `moat_cards` or `risk_cards` (Cloud Run image does not ship it); new modules imported by `mcp_server` must be in the root `Dockerfile` COPY list (enforced by `tests/test_dockerfile_modules.py`).
- All HTML text goes through the `$`-safe escape; all CSS is emitted on one line (Streamlit markdown breaks on multi-line `<style>` and reads `$…$` as LaTeX).
- Moat behaviour and `moat_cards` public API unchanged: every existing test in `tests/test_moat_cards.py` stays green without edits, except the tab-order assertion updated in Task 3.
- Section title exact `Risk Cards`; keys/order exact `concentration`, `disruption`, `outside_forces`, `financial_health`; pick 0 = riskiest (red) … 2 = safest (green).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

### Task 1: Extract `question_cards.py`; `moat_cards.py` becomes a thin definition

**Files:** Create `question_cards.py`, `tests/test_question_cards.py`; Modify `moat_cards.py`.

**Produces (in `question_cards.py`):**
- `esc(text) -> str` — html-escape + `$` → `&#36;` (the current `moat_cards._esc`)
- `css(*blocks) -> str` — one-line style (current `_css`)
- `bold(text) -> str` — escape then `**x**` → `<b>x</b>` (current `_bold`)
- `STYLE`, `SUMMARY_STYLE` — moved verbatim from `moat_cards`
- `BANDS = ("red", "yellow", "green")`, `ARROW`, `BACK_BG`, `BACK_TEXT` — moved
- `@dataclass(frozen=True) class CardSet: title: str; items: tuple; directions: tuple | None; trend: bool` — `items` rows are `(key, name, question, options)`
- `parse(cs: CardSet, content) -> dict` — the current `parse_moat_cards` logic generalised: fenced/raw JSON; `cards` must be the `cs.items` keys in order; pick 0/1/2 (bool refused); `direction` required and checked against `cs.directions` only when `cs.directions` is not None (and then present in the output; otherwise omitted); summary non-empty; exactly three points; `trend` accepted and validated only when `cs.trend` (else a present `trend` key is ignored). Returns `{"cards": [...], "trend": trend_or_None}`. Error messages keep the current wording (tests match on "five sources" for Moat: use `f"cards must be the {len(cs.items)} sources in order: ..."` and keep the word "five" for Moat by spelling the number: 4 → "four", 5 → "five").
- `flip_card_html(cs, card, theme) -> str` — the current Moat flip card; the front direction tag is rendered only when `card` has a `direction`.
- `grid_html(cs, cards, theme) -> str` — `css(STYLE)` + `<div class="mc-grid">` of flip cards.
- `notice_html(title, theme) -> str` — `No {title} yet. Ask Claude via the MCP to fill the "{title}" pre-scan section for this ticker.`
- `summary_card_html(title, box_html, lead_html, points, theme) -> str` — the current `_summary_card`.
- `dial_box_html(score, out_of, label, tone) -> str` — the current SVG dial + label box.
- `word_box_html(glyph, word, tone) -> str` — the current direction box (big glyph + small caps word).
- `summary_row_html(left_html, right_html) -> str` — `css(STYLE, SUMMARY_STYLE)` + `<div class="ms-row">{left}{right}</div>`.

**`moat_cards.py` after the change:** keeps `TITLE`, `SOURCES`, `DIRECTIONS`, `PROMPT`; defines `MOAT = CardSet(TITLE, SOURCES, DIRECTIONS, trend=True)`; `parse_moat_cards = lambda content: question_cards.parse(MOAT, content)` (write it as a `def`); `flip_card_html(card, theme)` delegates; `sources_row_html` stays (Moat-only); `summary_row_html(moat_analysis, cards_content, theme)` rebuilt from the helpers with identical output semantics; `cards_section_html` = notice or `sources_row_html` + `grid_html`. Keep `_esc`/`_css` names as aliases if any test imports them (check).

- [ ] **Step 1:** Write `tests/test_question_cards.py`:
```python
import json
import pytest
import question_cards as qc

CS = qc.CardSet("Test Cards",
                (("a", "A", "Q a?", ("Lo", "Mid", "Hi")),
                 ("b", "B", "Q b?", ("Lo", "Mid", "Hi"))),
                directions=None, trend=False)
THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}


def _p(**over):
    cards = [{"source": k, "pick": 1, "summary": f"{k} $1 to $2.",
              "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}
             for k, *_ in CS.items]
    return {"cards": cards, **over}


def test_parse_without_directions_needs_no_direction_and_ignores_trend():
    out = qc.parse(CS, json.dumps(_p(trend={"x": 1})))
    assert "direction" not in out["cards"][0] and out["trend"] is None


def test_parse_counts_the_items_in_words():
    p = _p(); p["cards"].pop()
    with pytest.raises(ValueError, match="two sources"):
        qc.parse(CS, json.dumps(p))


def test_flip_card_without_direction_has_no_direction_tag():
    card = qc.parse(CS, json.dumps(_p()))["cards"][0]
    html = qc.flip_card_html(CS, card, THEME)
    assert "mc-tag" in html                      # back-side chosen-option tag
    front = html.split("mc-back")[0]
    assert "↗" not in front and "→" not in front.replace("Details →", "")


def test_grid_and_notice_are_single_line_and_dollar_safe():
    cards = qc.parse(CS, json.dumps(_p()))["cards"]
    html = qc.grid_html(CS, cards, THEME)
    assert "\n" not in html and "$" not in html and html.count('class="mc-card"') == 2
    assert "Test Cards" in qc.notice_html("Test Cards", THEME)


def test_question_cards_does_not_import_prescan_render_at_load():
    import importlib, sys
    for n in ("question_cards", "prescan_render"):
        sys.modules.pop(n, None)
    importlib.import_module("question_cards")
    assert "prescan_render" not in sys.modules
```
- [ ] **Step 2:** run → FAIL (no module).
- [ ] **Step 3:** Create `question_cards.py` by moving code from `moat_cards.py` as specified; refactor `moat_cards.py` onto it. Number words: `{2: "two", 3: "three", 4: "four", 5: "five"}`.
- [ ] **Step 4:** `python3 -m pytest -q tests/test_question_cards.py tests/test_moat_cards.py` → all PASS with **no edits** to `tests/test_moat_cards.py`. Then ruff + full suite + cloudrun suite. Add `question_cards.py` to the root `Dockerfile` COPY line (the Dockerfile test fails otherwise, since `moat_cards` imports it — check whether the test follows transitive imports; add it regardless).
- [ ] **Step 5:** commit `Kaartenmotor question_cards.py uit moat_cards gehaald (Moat ongewijzigd)`.

---

### Task 2: `risk_cards.py`

**Files:** Create `risk_cards.py`, `tests/test_risk_cards.py`.

**Consumes:** `question_cards` (Task 1). **Produces:** `TITLE = "Risk Cards"`, `ITEMS`, `RISK: CardSet`, `PROMPT`, `parse_risk_cards(content)`, `cards_section_html(content, theme)`, `summary_row_html(risk_analysis, saas_text, theme) -> str | None`.

```python
"""Risk Cards: four question cards per ticker, built on the Risk Analysis and
the SaaSpocalypse Resistance sections. Answers run from riskiest (0, red) to
safest (2, green). No module-level import of prescan_render."""

import question_cards as qc

TITLE = "Risk Cards"
ITEMS = (
    ("concentration", "Concentration", "How diversified are revenues?",
     ("Concentrated", "Moderate", "Diversified")),
    ("disruption", "Disruption", "Is disruption a threat?", ("Yes", "Some risk", "No")),
    ("outside_forces", "Outside forces", "How much is outside their control?",
     ("A lot", "Some", "Very little")),
    ("financial_health", "Financial health", "How healthy are the financials?",
     ("Weak", "Mixed", "Strong")),
)
RISK = qc.CardSet(TITLE, ITEMS, directions=None, trend=False)

PROMPT = """You are turning the existing risk work on **{company} ({ticker})** into four
question cards. Base every call on the analyses below and on reported numbers;
do not contradict them.

{prior:Risk Analysis}

{prior:SaaSpocalypse Resistance}

For each question, in exactly this order, pick an answer (0 = riskiest, 2 = safest):
- concentration: How diversified are revenues? pick 0 = Concentrated, 1 = Moderate, 2 = Diversified
  (customers, products and geographies; a customer at 10%+ of revenue counts against)
- disruption: Is disruption a threat? pick 0 = Yes, 1 = Some risk, 2 = No
  (technology, AI, new business models that could make the product obsolete)
- outside_forces: How much is outside their control? pick 0 = A lot, 1 = Some, 2 = Very little
  (regulation, government pricing, currencies, commodity inputs, rates and credit)
- financial_health: How healthy are the financials? pick 0 = Weak, 1 = Mixed, 2 = Strong
  (interest cover, debt versus cash flow, liquidity)

summary: ONE sentence for the front of the card, with the fact that decides the pick.
points: EXACTLY three, each {"label": two to four words, "text": one line with a number or
a fact from the filings}.

Output ONLY a fenced JSON block, nothing before or after:

```json
{"cards": [
  {"source": "concentration", "pick": 2, "summary": "...",
   "points": [{"label": "...", "text": "..."}, {"label": "...", "text": "..."},
              {"label": "...", "text": "..."}]}
]}
```
The array holds all four questions, in the order listed above.
"""


def parse_risk_cards(content):
    return qc.parse(RISK, content)


def cards_section_html(content, theme):
    try:
        cards = parse_risk_cards(content)["cards"] if content else None
    except ValueError:
        cards = None
    return qc.grid_html(RISK, cards, theme) if cards else qc.notice_html(TITLE, theme)


def summary_row_html(risk_analysis, saas_text, theme):
    """EXECUTION RISK (Risk Analysis) and AI EXPOSURE (SaaSpocalypse Resistance)
    as two equal cards; None when neither is in verdict form."""
    from prescan_render import band_tone, parse_verdict_section
    left = _verdict_card("EXECUTION RISK", parse_verdict_section(risk_analysis or ""),
                         band_tone, theme)
    right = _verdict_card("AI EXPOSURE", parse_verdict_section(saas_text or ""),
                          band_tone, theme)
    if left is None and right is None:
        return None
    missing = qc.summary_card_html(..., "Not in the verdict format yet.", [], theme)  # see below
    return qc.summary_row_html(left or missing_left, right or missing_right)
```
Implement `_verdict_card(title, v, band_tone, theme)`: `None` when `v` is None; box = `qc.dial_box_html(v["score"], v["out_of"], v["label"], tone)` when `v["score"] is not None`, else `qc.word_box_html("●", v["label"], tone)`; `tone = band_tone(v["label"]) or theme["text_muted"]`; lead = `qc.bold(v["summary"])`; points = `v["bullets"][:3]`. For a missing side build `qc.summary_card_html(title, qc.word_box_html("–", "—", theme["text_muted"]), "Not in the verdict format yet.", [], theme)`.

- [ ] **Step 1:** `tests/test_risk_cards.py`:
```python
import json
import pytest
import risk_cards

THEME = {"text": "#222", "text_muted": "#888", "divider": "#ddd", "bg_secondary": "#f5f3ee"}
RISK_TEXT = ("**Risk: Medium 🟡 · Competition**\n\nThe damage would come from CRM.\n\n"
             "- **CRM contest**: Salesforce.\n- **Concentration**: 10.1%.\n"
             "- **Disruption**: AI agents.\n\n**What would change this:** growth.")
SAAS_TEXT = ("**AI exposure: Resilient 🟡 · 2/4**\n\nAI is both a tool and a threat.\n\n"
             "- **Liability**: regulated.\n- **Network**: data.\n- **Model**: seats.\n\n"
             "**Where it would break:** seats.")


def _p():
    return {"cards": [{"source": k, "pick": 2, "summary": f"{k} summary",
                       "points": [{"label": f"L{i}", "text": f"T{i}"} for i in range(3)]}
                      for k, *_ in risk_cards.ITEMS]}


def test_items_and_title():
    assert risk_cards.TITLE == "Risk Cards"
    assert [i[0] for i in risk_cards.ITEMS] == [
        "concentration", "disruption", "outside_forces", "financial_health"]


def test_parse_accepts_four_and_refuses_three():
    assert len(risk_cards.parse_risk_cards(json.dumps(_p()))["cards"]) == 4
    p = _p(); p["cards"].pop()
    with pytest.raises(ValueError, match="four sources"):
        risk_cards.parse_risk_cards(json.dumps(p))


def test_cards_section_renders_four_or_the_notice():
    assert risk_cards.cards_section_html(json.dumps(_p()), THEME).count('class="mc-card"') == 4
    assert "Risk Cards" in risk_cards.cards_section_html(None, THEME)


def test_summary_row_shows_both_verdicts():
    html = risk_cards.summary_row_html(RISK_TEXT, SAAS_TEXT, THEME)
    assert html.count('class="ms-card"') == 2
    assert "EXECUTION RISK" in html and "MEDIUM" in html
    assert "AI EXPOSURE" in html and "RESILIENT" in html and "CRM contest" in html
    assert "\n" not in html and "$" not in html


def test_summary_row_with_one_missing_side_and_with_none():
    html = risk_cards.summary_row_html(RISK_TEXT, "free text", THEME)
    assert "Not in the verdict format yet." in html
    assert risk_cards.summary_row_html("x", "y", THEME) is None


def test_prompt_uses_both_priors_and_names_every_item():
    assert "{prior:Risk Analysis}" in risk_cards.PROMPT
    assert "{prior:SaaSpocalypse Resistance}" in risk_cards.PROMPT
    for k, *_ in risk_cards.ITEMS:
        assert k in risk_cards.PROMPT
```
First confirm with one call that `prescan_render.parse_verdict_section` accepts `RISK_TEXT` and `SAAS_TEXT` (it needs bullets; `**What would change this:**` is the footer); adjust the TEXT, not the parser, if needed.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** tests + ruff + full suite. **Step 5:** commit `Risk Cards: vier vraagkaarten + samenvattingsrij`.

---

### Task 3: MCP validation, Dockerfile, prompt library entry, Risk tab, Pre-Scan, backfill doc

**Files:** Modify `mcp_server.py`, `Dockerfile`, `streamlit_app.py`, `docs/routines/moat-cards-backfill.md`, `docs/routines/aspirant-weekly.md`, `tests/test_moat_cards.py` (tab-order assertion only), `tests/test_risk_cards.py`.

- [ ] **Step 1: tests** (append to `tests/test_risk_cards.py`):
```python
from unittest.mock import MagicMock


def test_mcp_refuses_malformed_risk_cards_and_accepts_valid(monkeypatch):
    import mcp_server
    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    store = {"X": {"ai_notes": {}}}
    monkeypatch.setattr(mcp_server.config_store, "load_config",
                        lambda c, t, user_id=None: store.get(t.upper()))
    monkeypatch.setattr(mcp_server.config_store, "save_config",
                        lambda c, t, cfg, user_id=None: store.__setitem__(t.upper(), cfg))
    out = mcp_server._save_prescan_section_impl("X", "Risk Cards", '{"cards": []}', user_id="u")
    assert "error" in out and "Risk Cards" in out["error"]
    mcp_server._save_prescan_section_impl("X", "Risk Cards", json.dumps(_p()), user_id="u")
    assert "Risk Cards" in store["X"]["ai_notes"]


def test_default_prompts_put_risk_cards_right_after_risk_analysis():
    import streamlit_app
    titles = [p["title"] for p in streamlit_app.DEFAULT_AI_PROMPTS]
    assert titles[titles.index("Risk Analysis") + 1] == "Risk Cards"


def test_ticker_page_has_a_risk_tab_after_moat():
    src = open("streamlit_app.py", encoding="utf-8").read()
    assert '["Pre-Scan", "Moat", "Risk", "Fundamentals", "DCF"' in src
    assert "risk_cards.summary_row_html(" in src and "risk_cards.cards_section_html(" in src
```
and in `tests/test_moat_cards.py::test_ticker_page_has_a_moat_tab_after_pre_scan` change only the tab-list string to `'["Pre-Scan", "Moat", "Risk", "Fundamentals", "DCF"'`.

- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3: implement**
  1. `mcp_server.py`: `import risk_cards` (and `question_cards` if needed); replace the Moat-only check in `_save_prescan_section_impl` with a map:
     ```python
     _CARD_PARSERS = {moat_cards.TITLE: moat_cards.parse_moat_cards,
                      risk_cards.TITLE: risk_cards.parse_risk_cards}
     ...
         parser = _CARD_PARSERS.get(title.strip())
         if parser is not None:
             try:
                 parser(content)
             except ValueError as e:
                 return {"error": f"{title.strip()} not saved: {e}"}
     ```
     (define `_CARD_PARSERS` at module level after the imports). Keep the existing error text shape so `test_save_refuses_malformed_moat_cards` stays green.
  2. `Dockerfile`: add `risk_cards.py` (and `question_cards.py` if Task 1 did not).
  3. `streamlit_app.py`: `import risk_cards`; `DEFAULT_AI_PROMPTS` entry `{"title": risk_cards.TITLE, "prompt": risk_cards.PROMPT}` directly after the "Risk Analysis" dict; tabs → `["Pre-Scan", "Moat", "Risk", "Fundamentals", ...]` with `_tab_risk` in the unpacking right after `_tab_moat`; Risk tab block after the Moat block:
     ```python
     # Risk: Risk Analysis and SaaSpocalypse Resistance as two summary cards, then
     # the four question cards from "Risk Cards". Read-only, like Moat.
     with _tab_risk:
         _rnotes = cfg.get('ai_notes') if isinstance(cfg.get('ai_notes'), dict) else {}
         _rsum = risk_cards.summary_row_html(
             _rnotes.get("Risk Analysis") or "",
             _rnotes.get("SaaSpocalypse Resistance") or "", T)
         if _rsum:
             st.markdown(_rsum, unsafe_allow_html=True)
         else:
             st.caption("No Risk Analysis in the verdict format yet.")
         st.markdown("##### Risk questions")
         st.markdown(risk_cards.cards_section_html(_rnotes.get(risk_cards.TITLE), T),
                     unsafe_allow_html=True)
     ```
     Pre-Scan: extend the existing `_card = (moat_cards.cards_section_html(...) if _title == moat_cards.TITLE else _verdict_card_html(...))` so `risk_cards.TITLE` renders `risk_cards.cards_section_html(_content, T)`.
  4. `docs/routines/moat-cards-backfill.md`: generalise to "Question cards backfill": first `tickers_missing_section(title="Moat Cards", requires="Moat Analysis", limit=10)`, then with the remaining budget (10 minus names done) `tickers_missing_section(title="Risk Cards", requires="Risk Analysis", limit=<remaining>)`; per name take the prompt with that exact title from `get_prescan_prompts`, answer it, save with that title; for Risk Cards call `get_fundamentals` for the numbers. Keep the "Before enabling" note, now naming both prompts. Keep the filename (the live routine reads it).
  5. `docs/routines/aspirant-weekly.md` step 2b: add "Risk Cards" to the prompts that need a re-fetch after the sections they reference ("Risk Analysis" and "SaaSpocalypse Resistance"); say that "Risk Cards" refusals are handled like "Moat Cards".
- [ ] **Step 4:** tests + `py_compile streamlit_app.py` + ruff + full suite + cloudrun suite.
- [ ] **Step 5:** commit `Risk-tab, validatie Risk Cards in de MCP, prompt in de standaardbibliotheek, backfill voor beide kaartsets`.

After merge the controller: adds the prompt to the user's library (SQL after "Risk Analysis"), pushes, redeploys Cloud Run, fills VEEV's Risk Cards, and tells the user to reboot Streamlit (module changes).
