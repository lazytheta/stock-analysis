# Thesis tab — Key Debates + Red Flags (2026-11-28)

## Intent
Between the prescan ("what is this business?") and the Pre-mortem ("when do I
act?") sits a Thesis page: what decides the investment, and what could blow it
up. Source: owner's `thesis-page-prompts.md`. Both prompts end with a
`premortem` block so their triggers reach the Pre-mortem tab without retyping.

## Approach
One sensible way: the card-module pattern (`risk_cards.py`): prompt + parser +
HTML in one pure module, rendered by streamlit_app. A separate store or MCP
tool would duplicate the prompt library. Owner picked: triggers move to the
Pre-mortem through a button on the Thesis tab (not automatic, not manual MCP).

## Design
- `thesis_cards.py` (not `thesis.py`, taken by the DCF grade):
  `DEBATES_TITLE="Key Debates"`, `FLAGS_TITLE="Red Flags"`, both prompts
  (shared `prompt_style.HOW_TO_WRITE` + their own extra rules), `parse_debates`,
  `parse_red_flags` (ValueError on bad shape), `tab_html(debates, flags, theme)`,
  `merge_into_premortem(pm, *blocks) -> (new_pm, added)`.
- Prompt order: Management Cards → Red Flags → Key Debates → Investment Summary
  (Red Flags reads Management Cards, which runs after Risk). Investment Summary
  default gains `{prior:Key Debates}` and `{prior:Red Flags}`.
- Tab "Thesis" right after Pre-mortem: conviction ceiling + capped_by, Red Flags
  verdict + decided_by; one card per debate (question, lean, answerable,
  bull/bear, settles_it, precedent, next_research); downside test; non-issues;
  ten checks as green/yellow/red rows; could_not_check; button.
- Merge: adds sell/add triggers and ignore strings only; skips a trigger whose
  label or `when` (case-insensitive) already exists; status, action,
  discipline and existing triggers untouched; one log entry
  "N items from Thesis added". Button shows what is new first; disabled when
  nothing is new. Saves via full `load_config` → `save_config`.

## Non-goals
Conviction ceiling does not write discipline. No new MCP tool. Summary tab
unchanged.

## Rollout
Back up `user_prefs.ai_prompts` → update owner's saved Investment Summary;
redeploy MCP (merge_defaults seeds the new prompts); Streamlit Reboot.
