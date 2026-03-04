# Dark Mode Design

**Date:** 2026-02-26
**Status:** Approved

## Summary

Add a dark mode toggle to the Streamlit app. Users can switch between light and dark via a sidebar toggle. Dark theme uses Apple-style dark grey (#1c1c1e) — soft, low-contrast, easy on the eyes.

## Approach

CSS custom properties + Python theme dict (Approach A). All 137 hardcoded colour references in the CSS `<style>` block are migrated to `var(--token)` variables. Inline styles in Python f-strings read from a Python `THEME` dict keyed by mode.

## Colour Palette

| Token | Light | Dark |
|-------|-------|------|
| `--bg` | `#fafaf8` | `#1c1c1e` |
| `--bg-secondary` | `#f5f4f0` | `#2c2c2e` |
| `--card` | `#fff` | `#2c2c2e` |
| `--card-alt` | `#f9f9fb` | `#3a3a3c` |
| `--text` | `#1d1d1f` | `#f5f5f7` |
| `--text-muted` | `#86868b` | `#98989d` |
| `--border` | `rgba(0,0,0,0.04)` | `rgba(255,255,255,0.06)` |
| `--shadow` | `0 1px 3px rgba(0,0,0,0.04)` | `0 1px 3px rgba(0,0,0,0.3)` |
| `--accent` | `#81b29a` | `#81b29a` |
| `--accent-hover` | `#6fa88a` | `#93c4ac` |
| `--red` | `#e07a5f` | `#e07a5f` |
| `--pill-bg` | `rgba(255,255,255,0.7)` | `rgba(255,255,255,0.08)` |
| `--scrollbar` | `#c4c4c6` | `#48484a` |
| `--noise-opacity` | `0.03` | `0.015` |
| `--row-alt` | `#f9f9fb` | `#252527` |
| `--input-bg` | `#fafafa` | `#3a3a3c` |

## Toggle

- `st.toggle("Dark mode")` in sidebar, top position
- State stored in `st.session_state.dark_mode` (default: False)
- `config.toml` stays on light defaults; dark overrides via CSS vars

## Architecture

1. **Python colour dict** at top of file: `THEME = {"light": {...}, "dark": {...}}`
2. **CSS `:root`** block sets variables based on `st.session_state.dark_mode`
3. **All CSS selectors** use `var(--token)` instead of hardcoded hex values
4. **Inline f-string styles** in Python code read from `THEME[mode][key]`
5. **Plotly/Altair charts** use `paper_bgcolor` and `plot_bgcolor` from the same dict

## Scope

- ~137 CSS colour references migrated to variables
- ~40-50 inline style references in Python f-strings made dynamic
- Chart colour schemes updated
- `config.toml` unchanged (CSS overrides at runtime)
