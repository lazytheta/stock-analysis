# Pre-Scan Robustness Table — Design

**Date:** 2026-06-02
**Status:** Approved (pending spec review)
**Topic:** Surface Pulak Prasad's robustness judgment as the headline of the Pre-Scan page.

## Context & Problem

The Pre-Scan tab generates 11 AI sections (Business Phase, Business, Moat, Long-Term,
Key Metrics, Risk, Valuation, Price & Sentiment, SaaSpocalypse, Investment Summary,
Scorecard). The substantive *judgment* of a stock doesn't come through: the page leads
with descriptive sections and the final verdict (`deep_dive`/`revisit`/`pass`) is generic.

The user wants the assessment framed the way Prasad does in *What I Learned About Investing
from Darwin* (notes: `portfolio-vault/Strategie/Boeken/`). Prasad's order is **risk first,
quality second, valuation last**, and his central tool is the **robustness table** (ch. 3):
score a business on a *most ↔ least robust* continuum across several axes, optimizing hard
against Type-1 errors (buying a bad business → permanent capital loss).

**This spec covers the robustness table only** — the one thing the user most misses.

## Goal

Add a **Robustness Table** at the top of the Pre-Scan tab that:
- scores 6 Prasad axes on a most↔least-robust continuum,
- fills the two "honest signal" axes (ROCE, net debt) from data and the four qualitative
  axes from AI judgment, with manual override,
- derives a headline verdict via a **weakest-link (Type-1-averse)** rule,
- becomes the authoritative `verdict` shown in the watchlist.

## The 6 Axes

| Axis | Source | 🟢 robust | 🟡 mid | 🔴 fragile | Deal-breaker |
|------|--------|-----------|--------|-----------|:---:|
| **ROCE** (5–10y avg) | DATA (`avg_roce_pct`) | ≥20% | 12–<20% | <12% | **yes (gate)** |
| **Net debt** | DATA (debt − cash/securities/ST-inv) | net cash or <1× EBITDA | 1–2× | >2× | **yes** |
| Customer/supplier base | AI (← Risk: Concentration) | fragmented | some concentration | highly concentrated | no |
| Competitive barriers | AI (← Moat Analysis) | wide/widening | narrow/stable | none/eroding | no |
| **Management** | AI (new judgment) | stable, honest signals | mixed | dubious | **yes** |
| Industry change speed | AI (← Risk: Disruption + SaaSpocalypse) | slow | moderate | fast | no (caution) |

Notes:
- **ROCE is the gate.** Under 20% is *never* green (per user). ROE-fallback applies for
  float businesses (banks/payment networks) exactly as the watchlist row build already does
  (`mcp_server.py` headline metrics); the table labels the metric `ROCE` vs `ROE`.
- **Net debt** = `debt_market_value − (cash_bridge + securities + st_investments[-1])`.
  EBITDA from `valuation_inputs.ttm_ebitda`. If EBITDA unavailable, fall back to net-cash
  sign only (net cash → green, net debt → amber, large gross debt with no EBITDA → amber).
- The four qualitative axes are produced by a new **"Robustness" prescan prompt** that reads
  prior sections via `{prior:...}` substitution and returns structured JSON (band + note per
  axis). Bands: `robust` | `mid` | `fragile`.

## Verdict Rule (weakest-link, Type-1-averse)

Deal-breaker axes: **ROCE, Net debt, Management**.

Algorithm (first match wins):
1. Any deal-breaker axis = 🔴 → **Fragile** → `pass`.
2. ROCE not green (i.e. <20%, so amber or red) → capped at **Borderline** → `revisit`
   (the Prasad 20% gate: a sub-20% ROCE business is never "Robust").
3. Any *other* deal-breaker amber, OR ≥2 non-critical axes red → **Borderline** → `revisit`.
4. Otherwise (all deal-breakers green, mostly green) → **Robust** → `deep_dive`.

**Disney exception:** if Industry-change = 🔴 but Competitive-barriers = 🟢, soften
Industry-change to 🟡 before applying the rule (a strong moat makes a fast-moving industry
predictable enough — per the user's note on DIS).

Label → verdict mapping: **Robust → `deep_dive`**, **Borderline → `revisit`**, **Fragile → `pass`**.

## Integration

- Robustness table renders **at the top** of the Pre-Scan tab (above the existing section cards).
- The derived `verdict` becomes authoritative: the watchlist row reads
  `cfg['robustness']['verdict_mapped']` when present, else falls back to
  `parse_scorecard(ai_notes)` (current behavior). **Phase** still comes from the Phase
  analysis / Scorecard.
- The existing **Scorecard** card stays as a secondary detail view; it no longer owns the verdict.

## Data Model

New structured field `cfg['robustness']`:

```jsonc
{
  "axes": {
    "roce":        {"band": "robust", "value": 30.1, "metric": "ROCE", "note": "...", "source": "data"},
    "net_debt":    {"band": "robust", "value": -0.4,  "unit": "x_ebitda", "note": "...", "source": "data"},
    "customers":   {"band": "robust", "note": "...", "source": "ai"},
    "barriers":    {"band": "robust", "note": "...", "source": "ai"},
    "management":  {"band": "mid",    "note": "...", "source": "ai"},
    "industry":    {"band": "fragile","note": "...", "source": "ai"}
  },
  "overrides":     {"management": "robust"},     // user band overrides, optional
  "verdict":        "borderline",                // robust | borderline | fragile
  "verdict_mapped": "revisit",                   // deep_dive | revisit | pass
  "verdict_reason": "ROCE 30% but management mixed → capped at Borderline",
  "computed_at":    "2026-06-02T..."
}
```

Data axes recomputed on render/refresh; AI axes persisted from the prompt; overrides win over
both. `verdict`/`verdict_mapped`/`verdict_reason` are always derived by `robustness.py`, never
hand-edited.

## Modules

- **`robustness.py`** (new): axis definitions + band thresholds; `compute_data_axes(cfg)`
  (ROCE via existing helper + ROE fallback, net debt); `derive_verdict(axes, overrides)`
  (weakest-link incl. Disney exception); pure functions, fully unit-tested.
- **"Robustness" prescan prompt** added to `DEFAULT_AI_PROMPTS` (`streamlit_app.py`),
  emitting fenced JSON for the 4 qualitative axes. Parsed with the existing
  `scorecard_utils.parse_scorecard_json` (reused, not duplicated).
- **`scorecard_utils`** (or `config_store.list_watchlist`): prefer
  `cfg['robustness']['verdict_mapped']` over the Scorecard verdict.
- **`_render_robustness_table(cfg, theme)`** (`streamlit_app.py`): continuum bars + colored
  dots + verdict banner, rendered at the top of the Pre-Scan tab; plus a small override
  editor (set band per axis).
- **MCP**: a `set_robustness` write path (or fold the AI axes into `save_prescan_section`
  under a reserved title) so the assistant can populate qualitative axes headlessly.

## Storage Caveat

`cfg['robustness']` must survive saves: add it to the guarded keys in `config_store.save_config`
(`_GUARDED_KEYS_RESTORE_EMPTY` family) so a partial save can't wipe it, consistent with
`ai_notes`/`valuation_summary`. Targeted writes prefer `jsonb_set` (see memory note).

## Testing

- `robustness.py`: band thresholds (ROCE 20/12 boundaries, never-green-under-20%), net-debt
  computation incl. missing-EBITDA fallback, ROE fallback for float businesses.
- `derive_verdict`: weakest-link edge cases — single red deal-breaker → fragile; amber ROCE
  caps at borderline; Disney exception (industry red + barriers green → softened); all-green
  → robust.
- JSON parsing of the Robustness prompt output (reuse parse_scorecard_json tests as a model).
- Watchlist verdict prefers robustness over Scorecard when present.

## Out of Scope (deliberate)

Type-1/Type-2 explainer, standalone honest-vs-cheap signal tags, and a five-year-rule widget
are **not** built now — the user specifically wants the table. They can be added later as
separate sections.

## Open Questions

- Continuum dot position: derive purely from band (3 positions) for v1, or let the AI return a
  finer 0–1 position? **Default: band-only for v1** (simpler, testable); revisit if too coarse.
- Net-debt EBITDA source when `ttm_ebitda` missing — sign-only fallback is specified; confirm
  acceptable during implementation.
