# Dark Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a sidebar dark mode toggle to the Streamlit app, migrating all 283 hardcoded colour references to CSS custom properties and a Python theme dict.

**Architecture:** A `THEME` dict at the top of `streamlit_app.py` holds light/dark palettes. CSS `:root` variables are generated from the active theme. All CSS selectors use `var(--token)`. Inline f-string styles read from `THEME[mode]`. Plotly charts use the same dict.

**Tech Stack:** Streamlit (session_state, st.toggle), CSS custom properties, Plotly

---

### Task 1: Add theme infrastructure

**Files:**
- Modify: `streamlit_app.py:1-57` (imports and page config area)

**Step 1: Add THEME dict and session state initialisation after page config (line 54)**

Insert after line 54 (`)`), before line 56 (`# ── Custom CSS ──`):

```python
# ── Theme ──
if 'dark_mode' not in st.session_state:
    st.session_state.dark_mode = False

THEME = {
    'light': {
        'bg':             '#fafaf8',
        'bg_secondary':   '#f5f4f0',
        'card':           '#fff',
        'card_alt':       '#f9f9fb',
        'text':           '#1d1d1f',
        'text_muted':     '#86868b',
        'border':         'rgba(0,0,0,0.04)',
        'border_medium':  '#d2d2d7',
        'border_light':   '#e8e8ed',
        'shadow':         '0 1px 3px rgba(0,0,0,0.04)',
        'shadow_hover':   '0 2px 8px rgba(0,0,0,0.06)',
        'accent':         '#81b29a',
        'accent_hover':   '#6fa88a',
        'accent_light':   'rgba(129,178,154,0.06)',
        'accent_fill':    'rgba(129,178,154,0.15)',
        'accent_focus':   'rgba(129,178,154,0.2)',
        'red':            '#e07a5f',
        'red_light':      'rgba(224,122,95,0.15)',
        'pill_bg':        'rgba(255,255,255,0.7)',
        'pill_border':    'rgba(255,255,255,0.5)',
        'scrollbar':      '#c4c4c6',
        'grid':           '#f0f0f2',
        'input_bg':       '#fafafa',
        'info_bg':        '#f7f8fa',
        'noise_opacity':  '0.03',
        'divider':        'rgba(0,0,0,0.06)',
        'separator':      'rgba(128,128,128,0.25)',
        'row_alt':        '#f9f9fb',
        'spinner_border': '#e5e5ea',
        'overlay_bg':     '#fafaf8',
        'delete_bg':      '#fee2e2',
        'delete_border':  '#ef4444',
        'delete_text':    '#dc2626',
        'chart_font':     '#1d1d1f',
        'chart_grid':     '#f0f0f2',
        'chart_paper':    'rgba(0,0,0,0)',
        'chart_plot':     'rgba(0,0,0,0)',
        'chart_zero':     '#d2d2d7',
        'tv_bg':          'rgba(0,0,0,0.03)',
    },
    'dark': {
        'bg':             '#1c1c1e',
        'bg_secondary':   '#2c2c2e',
        'card':           '#2c2c2e',
        'card_alt':       '#3a3a3c',
        'text':           '#f5f5f7',
        'text_muted':     '#98989d',
        'border':         'rgba(255,255,255,0.06)',
        'border_medium':  '#48484a',
        'border_light':   '#3a3a3c',
        'shadow':         '0 1px 3px rgba(0,0,0,0.3)',
        'shadow_hover':   '0 2px 8px rgba(0,0,0,0.4)',
        'accent':         '#81b29a',
        'accent_hover':   '#93c4ac',
        'accent_light':   'rgba(129,178,154,0.12)',
        'accent_fill':    'rgba(129,178,154,0.25)',
        'accent_focus':   'rgba(129,178,154,0.3)',
        'red':            '#e07a5f',
        'red_light':      'rgba(224,122,95,0.25)',
        'pill_bg':        'rgba(255,255,255,0.08)',
        'pill_border':    'rgba(255,255,255,0.12)',
        'scrollbar':      '#48484a',
        'grid':           '#3a3a3c',
        'input_bg':       '#3a3a3c',
        'info_bg':        '#2c2c2e',
        'noise_opacity':  '0.015',
        'divider':        'rgba(255,255,255,0.08)',
        'separator':      'rgba(128,128,128,0.25)',
        'row_alt':        '#252527',
        'spinner_border': '#48484a',
        'overlay_bg':     '#1c1c1e',
        'delete_bg':      'rgba(220,38,38,0.15)',
        'delete_border':  '#ef4444',
        'delete_text':    '#f87171',
        'chart_font':     '#f5f5f7',
        'chart_grid':     '#3a3a3c',
        'chart_paper':    'rgba(0,0,0,0)',
        'chart_plot':     'rgba(0,0,0,0)',
        'chart_zero':     '#48484a',
        'tv_bg':          'rgba(255,255,255,0.04)',
    },
}

_mode = 'dark' if st.session_state.dark_mode else 'light'
T = THEME[_mode]
```

**Step 2: Add sidebar toggle**

Find the sidebar setup code (search for `st.sidebar`). Add the toggle at the very top of the sidebar, before any other sidebar content:

```python
with st.sidebar:
    st.toggle("Dark mode", key="dark_mode")
```

If the sidebar already uses `with st.sidebar:`, add the toggle as the first line inside.

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: add dark mode theme infrastructure and sidebar toggle"
```

---

### Task 2: Generate CSS variables from theme dict

**Files:**
- Modify: `streamlit_app.py:56-57` (the `st.markdown("""<style>` line)

**Step 1: Replace the static `st.markdown("""<style>` opening with dynamic CSS variable injection**

Replace:
```python
# ── Custom CSS ──
st.markdown("""
<style>
```

With:
```python
# ── Custom CSS ──
st.markdown(f"""
<style>
:root {{
    --bg: {T['bg']};
    --bg-secondary: {T['bg_secondary']};
    --card: {T['card']};
    --card-alt: {T['card_alt']};
    --text: {T['text']};
    --text-muted: {T['text_muted']};
    --border: {T['border']};
    --border-medium: {T['border_medium']};
    --border-light: {T['border_light']};
    --shadow: {T['shadow']};
    --shadow-hover: {T['shadow_hover']};
    --accent: {T['accent']};
    --accent-hover: {T['accent_hover']};
    --accent-light: {T['accent_light']};
    --accent-fill: {T['accent_fill']};
    --accent-focus: {T['accent_focus']};
    --red: {T['red']};
    --red-light: {T['red_light']};
    --pill-bg: {T['pill_bg']};
    --pill-border: {T['pill_border']};
    --scrollbar: {T['scrollbar']};
    --grid: {T['grid']};
    --input-bg: {T['input_bg']};
    --info-bg: {T['info_bg']};
    --noise-opacity: {T['noise_opacity']};
    --divider: {T['divider']};
    --row-alt: {T['row_alt']};
    --spinner-border: {T['spinner_border']};
    --overlay-bg: {T['overlay_bg']};
}}
```

Note: the rest of the `<style>` block continues after — this just prepends the `:root` variables. The closing `</style>` tag remains where it was.

**Step 2: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: inject CSS custom properties from theme dict"
```

---

### Task 3: Migrate CSS `<style>` block — backgrounds and cards

**Files:**
- Modify: `streamlit_app.py` — the `<style>` block (lines ~60-766)

**Step 1: Replace all card/surface backgrounds**

Within the CSS `<style>` block only, make these replacements. The pattern is: find the hardcoded hex, replace with the CSS variable.

| Find (in CSS only) | Replace with |
|---|---|
| `background: #fff` | `background: var(--card)` |
| `background: #fff !important` | `background: var(--card) !important` |
| `background: #f5f4f0 !important` | `background: var(--bg-secondary) !important` |
| `background: #f9f9fb` → (only in alternating rows) | `background: var(--row-alt)` |
| `background: rgba(255,255,255,0.7)` | `background: var(--pill-bg)` |
| `border: 1px solid rgba(255,255,255,0.5)` | `border: 1px solid var(--pill-border)` |
| `background: #ffeadc` (line 748, overlay) | `background: var(--overlay-bg)` |

**Step 2: Replace all text colours in CSS**

| Find | Replace with |
|---|---|
| `color: #1d1d1f` | `color: var(--text)` |
| `color: #1d1d1f !important` | `color: var(--text) !important` |
| `color: #86868b` | `color: var(--text-muted)` |

**Step 3: Replace accent colours in CSS**

| Find | Replace with |
|---|---|
| `background-color: #81b29a !important` | `background-color: var(--accent) !important` |
| `background-color: #6fa88a !important` | `background-color: var(--accent-hover) !important` |
| `background-color: rgba(129,178,154,0.06) !important` | `background-color: var(--accent-light) !important` |
| `color: #81b29a` (in CSS) | `color: var(--accent)` |
| `border-color: #81b29a` | `border-color: var(--accent)` |
| `border-top: 3px solid #81b29a` | `border-top: 3px solid var(--accent)` |
| `outline: 2px solid #81b29a !important` | `outline: 2px solid var(--accent) !important` |
| `background: #81b29a` | `background: var(--accent)` |
| `background: #e07a5f` | `background: var(--red)` |

**Step 4: Replace borders, shadows, scrollbar in CSS**

| Find | Replace with |
|---|---|
| `box-shadow: 0 1px 3px rgba(0,0,0,0.04)` | `box-shadow: var(--shadow)` |
| `box-shadow: 0 2px 8px rgba(0,0,0,0.06)` | `box-shadow: var(--shadow-hover)` |
| `border: 1px solid #d2d2d7` or `border-color: #d2d2d7` | use `var(--border-medium)` |
| `rgba(0,0,0,0.06)` (dividers / hr) | `var(--divider)` |
| `background: transparent` (scrollbar track) | keep as-is |
| `background: #c4c4c6` (scrollbar thumb) | `background: var(--scrollbar)` |
| `rgba(129,178,154,0.2)` (focus shadow) | `var(--accent-focus)` |
| `#e5e5ea` (spinner border) | `var(--spinner-border)` |

**Step 5: Replace noise opacity**

Line ~78: change `opacity: 0.03;` to `opacity: var(--noise-opacity);`

**Step 6: Verify the app still renders correctly in light mode**

Run: `cd /Users/administrator/Documents/github/stock-analysis && streamlit run streamlit_app.py`

Open in browser, check that the light mode looks identical to before.

**Step 7: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: migrate CSS style block to CSS custom properties"
```

---

### Task 4: Migrate inline Python f-string colours — DCF section (lines ~800-1850)

**Files:**
- Modify: `streamlit_app.py` lines ~800-1850

**Step 1: Replace hardcoded colours with T[] dict lookups**

Throughout the DCF section Python f-strings, apply these substitutions:

| Find in f-strings | Replace with |
|---|---|
| `#1d1d1f` | `{T['text']}` |
| `#86868b` | `{T['text_muted']}` |
| `#81b29a` | `{T['accent']}` |
| `#e07a5f` | `{T['red']}` |
| `#fff` (in inline styles) | `{T['card']}` |
| `#f9f9fb` | `{T['row_alt']}` |
| `#d2d2d7` | `{T['border_medium']}` |
| `#e8e8ed` | `{T['border_light']}` |
| `rgba(0,0,0,0.03)` | `{T['tv_bg']}` |
| `rgba(128,128,128,0.25)` | `{T['separator']}` |
| `#fee2e2` (delete bg) | `{T['delete_bg']}` |
| `#ef4444` (delete border) | `{T['delete_border']}` |
| `#dc2626` (delete text) | `{T['delete_text']}` |
| `rgba(129,178,154,0.15)` | `{T['accent_fill']}` |
| `rgba(224,122,95,0.15)` | `{T['red_light']}` |

**Important:** Many lines use ternary expressions like:
```python
f"color:{'#81b29a' if val >= 0 else '#e07a5f'}"
```
These become:
```python
f"color:{T['accent'] if val >= 0 else T['red']}"
```

**Step 2: Verify app loads without errors**

Run: `streamlit run streamlit_app.py` — check DCF page renders.

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: migrate DCF section inline colours to theme dict"
```

---

### Task 5: Migrate inline colours — Peer comparison & Outlook (lines ~1650-1850)

**Files:**
- Modify: `streamlit_app.py` lines ~1650-1850

**Step 1: Migrate peer comparison table styling**

Lines with `background-color: #81b29a; color: white` (highlighted peer row), `rgba(129,178,154,0.15)` (undervalued), `rgba(224,122,95,0.15)` (overvalued) — replace with T[] lookups.

The legend HTML (lines ~1733-1735) with inline `background:#81b29a`, `border:1px solid #81b29a`, `border:1px solid #e07a5f` — replace all with T[] lookups.

**Step 2: Migrate outlook table (lines ~1788-1825)**

Replace `#d2d2d7` with `{T['border_medium']}`, `#f9f9fb` with `{T['row_alt']}`, `#e8e8ed` with `{T['border_light']}`.

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: migrate peer comparison and outlook colours to theme dict"
```

---

### Task 6: Migrate Plotly chart colours

**Files:**
- Modify: `streamlit_app.py` lines ~1878-1903 (_COLORS dict and _base_layout)

**Step 1: Replace _COLORS dict and _base_layout**

Replace:
```python
_COLORS = {
    'primary': '#81b29a',
    'secondary': '#e07a5f',
    'accent': '#3d405b',
    'tertiary': '#f2cc8f',
}
```

With:
```python
_COLORS = {
    'primary': T['accent'],
    'secondary': T['red'],
    'accent': '#3d405b',
    'tertiary': '#f2cc8f',
}
```

Replace in `_base_layout`:
```python
color="#1d1d1f",
```
→ `color=T['chart_font'],`

```python
paper_bgcolor='rgba(0,0,0,0)',
plot_bgcolor='rgba(0,0,0,0)',
xaxis=dict(gridcolor='#f0f0f2', dtick=1),
yaxis=dict(gridcolor='#f0f0f2'),
```
→
```python
paper_bgcolor=T['chart_paper'],
plot_bgcolor=T['chart_plot'],
xaxis=dict(gridcolor=T['chart_grid'], dtick=1),
yaxis=dict(gridcolor=T['chart_grid']),
```

**Step 2: Migrate all other Plotly chart configs**

Search for remaining Plotly colour references (~lines 4028-4760). Apply the same pattern:
- `#1d1d1f` font color → `T['chart_font']`
- `rgba(0,0,0,0)` backgrounds → `T['chart_paper']` / `T['chart_plot']`
- `#f0f0f2` grid → `T['chart_grid']`
- `#d2d2d7` zero line → `T['chart_zero']`
- `rgba(129,178,154,0.18)` fill → `T['accent_fill']`

Portfolio colour arrays (`#81b29a`, `#3d405b`, `#e07a5f`, etc.) stay unchanged — these are decorative data series colours.

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: migrate Plotly chart colours to theme dict"
```

---

### Task 7: Migrate inline colours — Financial tables (lines ~2200-2700)

**Files:**
- Modify: `streamlit_app.py` lines ~2200-2700 (Key Ratios, Balance Sheet, Income Statement, Cash Flow)

**Step 1: Replace table header/label colours**

All instances of:
- `#86868b` → `{T['text_muted']}`
- `#1d1d1f` → `{T['text']}`
- `#d2d2d7` → `{T['border_medium']}`
- `#f9f9fb` (alternating row bg) → `{T['row_alt']}`
- `#f0f0f2` (row separator) → `{T['grid']}`

These appear in the `_render_row` helpers and table header HTML across all four financial statement sections.

**Step 2: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: migrate financial table colours to theme dict"
```

---

### Task 8: Migrate inline colours — Tastytrade / Portfolio section (lines ~2700-4956)

**Files:**
- Modify: `streamlit_app.py` lines ~2700-4956

**Step 1: Replace all remaining hardcoded colours**

Apply the standard substitutions across the entire Tastytrade/portfolio section:

| Pattern | Replacement |
|---|---|
| `#86868b` | `{T['text_muted']}` |
| `#1d1d1f` | `{T['text']}` |
| `#81b29a` (static) | `{T['accent']}` |
| `#e07a5f` (static) | `{T['red']}` |
| `#d2d2d7` | `{T['border_medium']}` |
| `#f7f8fa` | `{T['info_bg']}` |
| `#f0f0f2` | `{T['grid']}` |
| `#f2cc8f` | keep as-is (decorative/status colour) |
| `#fafafa` | `{T['input_bg']}` |
| `rgba(0,0,0,0.06)` | `{T['divider']}` |
| `#fee2e2` | `{T['delete_bg']}` |

Conditional ternaries like `T['accent'] if val >= 0 else T['red']` apply throughout.

**Step 2: Handle the HTML input element (line ~4320)**

Replace:
```python
'background:#fafafa;'
```
→ `f'background:{T["input_bg"]};'`

**Step 3: Commit**

```bash
git add streamlit_app.py
git commit -m "feat: migrate Tastytrade/portfolio colours to theme dict"
```

---

### Task 9: Visual testing and polish

**Files:**
- Modify: `streamlit_app.py` (any remaining fixes)

**Step 1: Test light mode**

Run `streamlit run streamlit_app.py`. Verify every page/tab looks identical to before:
- DCF page (hero card, stat pills, comparison table, outlook table)
- Financial statements (all 4 tables)
- Tastytrade portfolio (performance charts, Greeks, margin)

**Step 2: Test dark mode**

Click the "Dark mode" toggle. Verify:
- Background switches to dark grey
- All cards have dark backgrounds
- Text is readable (light on dark)
- Charts have correct grid/font colours
- Accent green and red are still visible
- No white "flashes" from missed colour references

**Step 3: Fix any remaining hardcoded colours**

Search for any remaining hardcoded hex values that were missed:
```bash
grep -n '#fff\|#1d1d1f\|#86868b\|#fafafa\|#f9f9fb\|#f0f0f2\|#d2d2d7\|#e8e8ed\|#e5e5ea\|#ffeadc\|#f7f8fa\|#f5f4f0\|#fafaf8' streamlit_app.py
```

Any remaining matches should be migrated or confirmed as intentionally static.

**Step 4: Commit final polish**

```bash
git add streamlit_app.py
git commit -m "feat: dark mode visual polish and remaining colour fixes"
```

---

### Task 10: Update config.toml for dark mode compatibility

**Files:**
- Modify: `.streamlit/config.toml`

**Step 1: Verify config.toml**

The `config.toml` stays on light defaults. Streamlit's theme config only applies as a baseline — our CSS custom properties override everything. No changes needed unless dark mode session state should also update Streamlit's internal theme. Since CSS vars handle all visual styling, `config.toml` can stay as-is.

**Step 2: Final commit**

```bash
git add -A
git commit -m "feat: complete dark mode with sidebar toggle

Adds Apple-style dark grey theme with CSS custom properties.
Toggle in sidebar switches between light and dark mode.
All 283 colour references migrated to theme-aware variables."
```
