# Thesis Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two new prescan prompts (Key Debates, Red Flags), a Thesis tab that renders them, and a button that merges their triggers into the Pre-mortem.

**Architecture:** One pure module `thesis_cards.py` (prompts, parsers, HTML, merge) following `risk_cards.py`; registered in `prescan_prompts.DEFAULT_AI_PROMPTS`; streamlit_app adds the tab and the button.

**Tech Stack:** Python 3, Streamlit, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-11-28-thesis-tab-design.md`; prompt text verbatim from `/Users/administrator/Downloads/thesis-page-prompts.md`.

## Global Constraints
- Titles exactly `"Key Debates"` and `"Red Flags"` (the `{prior:...}` keys).
- Trigger limits from `premortem.py`: label ≤40, when ≤160, ignore item ≤160, state in `STATES`.
- HTML text always through `question_cards.esc`.
- `ruff check .` and `pytest test_tastytrade_api.py test_ibkr_api.py` pass before each commit.
- Commit locally only; push only when the owner asks.

## Review Focus
1. Section saved as prose / truncated JSON → tab shows a "could not read" notice for that half, the other half still renders (test in Task 1/3).
2. Only one of the two sections exists → tab renders what exists; button merges only that block (Task 3/2).
3. AI trigger longer than 40/160 chars → merge truncates rather than storing something `validation_errors` would refuse (Task 2).
4. Pressing the button twice → second press adds nothing, no second log line (Task 2).
5. Pre-mortem still legacy string or None → merge works via `premortem.normalize` (Task 2).

---

### Task 1: Prompts + parsers, registered in the library

**Files:**
- Create: `thesis_cards.py`, `test_thesis_cards.py`
- Modify: `prescan_prompts.py` (import, two entries after `management_cards`, Investment Summary prior blocks)

**Interfaces:**
- Produces: `DEBATES_TITLE`, `FLAGS_TITLE`, `DEBATES_PROMPT`, `FLAGS_PROMPT` (str);
  `parse_debates(content: str) -> dict`, `parse_red_flags(content: str) -> dict` (raise `ValueError`);
  `CHECKS: tuple[tuple[str, str], ...]` = (key, plain label) for the ten checks in spec order.

- [ ] Step 1: tests
```python
def test_library_order():
    titles = [p["title"] for p in prescan_prompts.DEFAULT_AI_PROMPTS]
    i = titles.index
    assert i("Management Cards") < i("Red Flags") < i("Key Debates") < i("Investment Summary")

def test_summary_reads_thesis():
    inv = next(p for p in DEFAULT_AI_PROMPTS if p["title"] == "Investment Summary")["prompt"]
    assert "{prior:Key Debates}" in inv and "{prior:Red Flags}" in inv

def test_prompts_carry_placeholders_and_style():
    assert "{prior:Management Cards}" in FLAGS_PROMPT and "{prior:Red Flags}" in DEBATES_PROMPT
    assert prompt_style.HOW_TO_WRITE in DEBATES_PROMPT and prompt_style.HOW_TO_WRITE in FLAGS_PROMPT

def test_parse_debates_fenced():   # sample JSON from the spec file, wrapped in ```json
    d = parse_debates(SAMPLE_DEBATES)
    assert d["debates"][0]["lean"] == "open" and d["conviction_ceiling"]["level"] == "starter"

def test_parse_debates_rejects_bad_lean_and_prose():
    with pytest.raises(ValueError): parse_debates("Just prose")
    with pytest.raises(ValueError): parse_debates(SAMPLE_DEBATES.replace('"open"', '"maybe"'))

def test_parse_red_flags_status_and_unknown_check():
    f = parse_red_flags(SAMPLE_FLAGS)
    assert f["verdict"]["level"] == "Watch" and len(f["checks"]) == 10
    with pytest.raises(ValueError): parse_red_flags(SAMPLE_FLAGS.replace('"yellow"', '"purple"'))
```
- [ ] Step 2: run `python3 -m pytest test_thesis_cards.py -v` → FAIL (import).
- [ ] Step 3: implement. Prompts = spec text with its own HOW TO WRITE block replaced by `prompt_style.HOW_TO_WRITE` plus the prompt's extra lines ("Never pad to three"; "Limited disclosure is a finding…"; "Report only the checks that decide…"). Parser: reuse fenced-JSON extraction like `question_cards.parse` (`_FENCE`); validate enums — lean {bull,bear,open}, answerable {yes,partly,no}, ceiling {full,normal,starter,none}, verdict {Clean,Watch,Red flag}, status {green,yellow,red,n.a.}; `debates` 1–3 items with non-empty `question`; `premortem` optional, missing keys → empty lists. Unknown check keys raise. Investment Summary: add `### Key Debates\n{prior:Key Debates}` and `### Red Flags\n{prior:Red Flags}` before the `---`.
- [ ] Step 4: tests pass; `ruff check .`.
- [ ] Step 5: commit `feat(thesis): Key Debates and Red Flags prompts`.

### Task 2: Merge into the Pre-mortem

**Files:** Modify `thesis_cards.py`, `test_thesis_cards.py`

**Interfaces:**
- Consumes: `premortem.normalize`, `premortem.MAX_LABEL/MAX_WHEN/MAX_ITEM`.
- Produces: `premortem_blocks(debates: dict|None, flags: dict|None) -> list[dict]`;
  `merge_into_premortem(pm, blocks: list[dict], today: str) -> tuple[dict, dict]` returning (new pm dict, `{"sell": [...], "add": [...], "ignore": [...]}` of what was added).

- [ ] Step 1: tests
```python
def test_merge_adds_only_new_and_keeps_the_rest():
    pm = {"status": "intact", "action": "hold", "discipline": ["max 5%"],
          "sell": [{"label": "Take rate", "when": "x", "state": "watch"}], "log": []}
    blocks = [{"sell": [{"label": "take rate", "when": "y"}, {"label": "Churn", "when": "z"}],
               "add": [], "ignore": ["capex noise"]}]
    new, added = merge_into_premortem(pm, blocks, "2026-11-28")
    assert [t["label"] for t in new["sell"]] == ["Take rate", "Churn"]
    assert new["status"] == "intact" and new["discipline"] == ["max 5%"]
    assert new["log"][0] == {"date": "2026-11-28", "text": "2 items from Thesis added"}

def test_merge_twice_adds_nothing():
    new, _ = merge_into_premortem(None, BLOCKS, "2026-11-28")
    again, added = merge_into_premortem(new, BLOCKS, "2026-11-29")
    assert added == {"sell": [], "add": [], "ignore": []} and len(again["log"]) == 1

def test_merge_trims_to_limits_and_validates():
    long = [{"sell": [{"label": "L" * 60, "when": "W " * 120}], "add": [], "ignore": ["i" * 200]}]
    new, _ = merge_into_premortem("legacy note", long, "2026-11-28")
    assert premortem.validation_errors(new) == []
```
- [ ] Step 2: FAIL.
- [ ] Step 3: implement. Duplicate = same label OR same `when`, case-insensitive, stripped. Trim with `premortem._first_sentence`-style cut (word boundary + "…"). State forced to "watch". Log line text `"{n} items from Thesis added"` only when n>0. Never touch status/action/discipline.
- [ ] Step 4: pass; ruff. Step 5: commit `feat(thesis): merge triggers into pre-mortem`.

### Task 3: Tab HTML

**Files:** Modify `thesis_cards.py`, `test_thesis_cards.py`

**Interfaces:** Produces `tab_html(debates_raw: str|None, flags_raw: str|None, theme) -> str` (parses internally; a ValueError half becomes a notice).

- [ ] Step 1: tests: both missing → contains "No Key Debates" and "No Red Flags"; prose Red Flags + valid debates → debate question present and "could not be read"; `<script>` in a question is escaped; ten check rows present with their status class.
- [ ] Step 2: FAIL. Step 3: implement with the `pm2`-style card look (own `th-` CSS prefix, theme vars); order: ceiling + verdict strip, debate cards, downside test, non-issues, checks, could_not_check.
- [ ] Step 4: pass; ruff. Step 5: commit `feat(thesis): tab html`.

### Task 4: Wire the tab and the button

**Files:** Modify `streamlit_app.py` (~4038 tabs list; new `with _tab_thesis:` block near the Pre-mortem block ~4396)

- [ ] Step 1: tab list becomes `["Pre-mortem", "Thesis", "Summary", ...]`, unpack `_tab_thesis`.
- [ ] Step 2: render `thesis_cards.tab_html(ai_notes.get("Key Debates"), ai_notes.get("Red Flags"), T)` inside try/except → caption "Thesis unavailable right now.".
- [ ] Step 3: compute `blocks` (parse errors → skip that half) and a dry-run `merge_into_premortem(cfg.get("premortem"), blocks, today)`; show the new labels in a caption; `st.button("Add to Pre-mortem", key=f"thesis_pm_{ticker}", disabled=not any(added.values()))`. On click: `load_config` the full config fresh, merge again on that, `save_config`, update in-memory `cfg["premortem"]`, `st.success`, `st.rerun()`.
- [ ] Step 4: `python3 -c "import streamlit_app"` imports cleanly; ruff; run the app locally on one ticker with sections if available (else verify the empty state).
- [ ] Step 5: commit `feat(thesis): Thesis tab with add-to-pre-mortem`.

### Task 5: Rollout
- [ ] Back up owner's `user_prefs.ai_prompts` (Supabase, table `ai_prompts_backup` as before); replace only the Investment Summary prompt text with the new default; leave others.
- [ ] Push (owner asks) → redeploy MCP to Cloud Run; verify `get_prescan_prompts` lists Red Flags and Key Debates in order.
- [ ] Remind owner: Streamlit Reboot. Note in Notion Sessielog 42 and memory.
