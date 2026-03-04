# Design Upgrade: "Refined with Edge"

**Date:** 2026-02-26
**Status:** Approved

## Goal

Elevate the existing Apple-inspired design to a distinctive, premium financial interface. Keep the structure and green palette, but add typographic character, subtle depth, micro-animations, and textural warmth so the app feels alive and memorable.

## Scope

Pure CSS changes to `streamlit_app.py` (main style block lines 57-595) and `.streamlit/config.toml`. No structural/Python logic changes.

## Changes

### 1. Typography — Dual Font System

**Replace** Inter with DM Serif Display (headers) + DM Sans (body/UI).

- Google Fonts import: `DM+Serif+Display:ital@0;1` + `DM+Sans:wght@400;500;600;700`
- `h1, h2, h3`: `'DM Serif Display', Georgia, serif` — weight 400 (serif doesn't need bold)
- `body, labels, inputs, pills`: `'DM Sans', -apple-system, sans-serif`
- Hero `.hero-value`: stays sans-serif (DM Sans 700) for number legibility
- Letter-spacing on headings: `-0.01em` (serifs need less tightening than sans)

### 2. Color Refinements

| Token | Old | New | Notes |
|-------|-----|-----|-------|
| Page background | `#FFFFFF` | `#fafaf8` | Warmer off-white |
| Secondary background | `#f7f8fa` | `#f5f4f0` | Warmer neutral |
| Gold accent | — | `#c9a96e` | Premium highlights |
| Card shadow | `none` | `0 1px 3px rgba(0,0,0,0.04)` | Ultra-subtle depth |
| Card hover shadow | — | `0 4px 12px rgba(0,0,0,0.06)` | Lift on hover |

Update `config.toml`:
```toml
backgroundColor = "#fafaf8"
secondaryBackgroundColor = "#f5f4f0"
```

### 3. Animations & Micro-Interactions

**Page load — staggered fade-in:**
```css
@keyframes fadeInUp {
    from { opacity: 0; transform: translateY(12px); }
    to { opacity: 1; transform: translateY(0); }
}
```
Apply to: `.hero-card`, `[data-testid="stMetric"]`, `.stat-pill`, `.portfolio-card`, `[data-testid="stExpander"]`
Each with incremental `animation-delay` (0s, 0.05s, 0.1s, ...) via `:nth-child()`.
Duration: `0.4s ease-out`, `animation-fill-mode: both`.

**Card hover — lift:**
```css
.portfolio-card, .performer-block, [data-testid="stExpander"] {
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}
/* hover: transform: translateY(-2px); box-shadow intensifies */
```

**Hero value — count-up:**
Not possible in pure CSS. Skip or add later with minimal JS.

**Sidebar active indicator:**
Smooth background transition on radio labels.

### 4. Card & Component Upgrades

**Hero card:**
- Add subtle top-border accent: `border-top: 3px solid #81b29a`
- Shadow: `0 1px 3px rgba(0,0,0,0.04)`

**Stat pills — frosted glass:**
```css
.stat-pill {
    background: rgba(255,255,255,0.7);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    border: 1px solid rgba(255,255,255,0.5);
}
```

**Portfolio cards — hover accent:**
```css
.portfolio-card:hover {
    border-left: 3px solid #81b29a;
}
```

**Custom scrollbar:**
```css
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #c4c4c6; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #81b29a; }
```

### 5. Background Texture

Subtle noise overlay on body via inline SVG data URI (opacity ~3%):
```css
body::before {
    content: "";
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 0;
    opacity: 0.03;
    background-image: url("data:image/svg+xml,...tiny-noise-pattern...");
}
```

### 6. Additional Polish

- **Focus states**: all interactive elements get `outline: 2px solid #81b29a; outline-offset: 2px` on `:focus-visible`
- **Separator consistency**: standardize all dividers to `border-color: rgba(0,0,0,0.06)`
- **Loading spinner**: change from gray/black to green accent (`border-top-color: #81b29a`)

## Implementation Notes

- All changes are in the main `<style>` block (lines 57-595) and `config.toml`
- Some inline styles in Python f-strings may need hex color updates (`#fff` → keep, backgrounds already handled by CSS)
- Test on Chrome, Safari, Firefox — `backdrop-filter` has broad support but verify
- The `animation-delay` stagger uses `:nth-child()` which works on Streamlit's column/metric containers
