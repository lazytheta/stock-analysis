# Phase-Aware ROCE Gate — Design

**Date:** 2026-06-16
**Status:** Approved — open questions resolved (see end). Implementing.
**Topic:** Make the robustness ROCE deal-breaker gate phase-aware, so early-phase
compounders aren't auto-stamped `fragile (pass)` purely because GAAP ROCE < 20%.

## Context & Problem

The robustness engine ([[2026-06-02-prescan-robustness-table-design]]) applies a
**uniform Prasad gate**: ROCE < 20% is never green, and ROCE is a deal-breaker —
a red ROCE band forces `fragile → pass`. That's correct for a *mature* business
(Prasad scores established compounders), but wrong for an early-phase business
still building its capital efficiency.

Concrete case: **NET (Cloudflare)** — high growth, Rule of 40 ≈ 43, adjusted ROCE
~7% and rising (flattered *down* by ~$4.6B cash/investments in the denominator),
prescan verdict REVISIT. The engine returns `fragile (pass) – deal-breaker roce`
regardless of phase, because the GAAP 20% gate is applied uniformly.

The desired rule is **phase-aware**: the bar a company must clear depends on where
it is in its life cycle. This was already articulated as a manual prescan overlay;
this spec moves it **into the engine** (we own `robustness.py` + `mcp_server.py`),
so the verdict is computed consistently instead of amended by hand per ticker.

**This spec covers the ROCE gate only.** Net-debt and the qualitative axes are
unchanged.

## Goal

Replace the single 20% ROCE gate with a **phase-tiered** gate that:
- relaxes the *level* required at earlier phases but never removes the requirement
  *that a phase-appropriate bar be cleared*,
- never lowers the 20% bar for mature (phase 5) businesses,
- treats a phase-1 business as "too early to judge" (defer) rather than `fragile`,
- preserves the weakest-link, Type-1-averse philosophy,
- has hard anti-rationalization guards so the gate can't be dodged by re-phasing.

## The phase-aware rule

`phase` is an integer 1–6, already parsed server-side from `ai_notes['Scorecard']`
(`scorecard_utils` verdict/phase helper). The engine **reads** it; it does not set
or infer it.

| Phase | Meaning | Test | ROCE band outcome |
|------:|---------|------|-------------------|
| 1 | Pre-product / pre-scale | none — too early | `n/a` → not a deal-breaker; verdict capped at `revisit` (defer), never `deep_dive` |
| 2 | Hyper-growth, not yet profitable | **Rule of 40 ≥ 40** AND **incremental ROIC > 0** | pass → `robust` *for its phase* (axis labelled "Rule of 40 N", verdict step caps at conditional); fail → `fragile` |
| 3 | Scaling into profitability | **ROCE ≥ 10% (latest yr) and rising** OR **incremental ROIC > 20%** | both strong → `robust`; one met → `mid`; neither → `fragile` |
| 4 | Maturing | **GAAP ROCE ≥ 15%** (target rising to 20) | ≥20 `robust`; 15–<20 `mid`; <15 `fragile` |
| 5 | Mature compounder | **GAAP ROCE ≥ 20%**, multi-year | current Prasad gate: ≥20 `robust`; 12–<20 `mid`; <12 `fragile` |
| 6 | Declining / avoid | — | `fragile` |

**Naming the pass (review revision):** a phase-2 company that clears Rule of 40 is
`robust` **for its phase** — the ROCE axis is labelled with the metric that actually
applied ("Rule of 40 44", not a misleading GAAP ROCE) so the table states *why* it
passed. Collapsing every passing early-phase name to a generic `mid` was wrong — it
read as mediocrity rather than "cleared its phase bar".

**Verdict cap:** `derive_verdict` then caps phases **1–3** so they never reach a clean
`deep_dive` on capital efficiency alone — a phase-2/3 name that would otherwise be
`robust` is downgraded to `borderline` (`revisit` / "conditional"), with a reason that
*names the cleared test*. Phase 1 defers via `n/a`. Phase 4 can still earn `robust`
(it is effectively mature). This keeps "never a clean pass for early phases" while
making the axis informative.

## Metric definitions (computed in `_compute_fundamentals_headline`)

All from the per-year `fund` arrays already available. Added to the `headline` dict
so `compute_data_axes` can read them.

- **Rule of 40** = `revenue_cagr_3y_pct + fcf_margin_pct`
  - `revenue_cagr_3y_pct` = **3-year revenue CAGR** (%), over the last 4 usable
    revenue points (steadier than YoY). `((rev_last / rev_base) ** (1/years) − 1) × 100`.
  - `fcf_margin_pct` = `fcf_last / revenue_last × 100` (latest year where both exist).
- **Incremental ROIC** (best-effort, noisy — included per user choice):
  - `NOPAT_t = EBIT_t × (1 − effective_tax_rate)`, tax rate from `tax_provision/pretax_income`, clamped to [0, 0.35], fallback 0.21.
  - `InvestedCapital_t = total_debt_t + total_equity_t − cash_t`.
  - `incr_roic = ΔNOPAT / ΔInvestedCapital` over the last **3 deltas** (i.e. latest
    minus the point 3 years back — sum-of-deltas telescopes to endpoints — to damp
    single-year noise). Require ≥4 usable years; else `None`.
  - Guard: if `ΔInvestedCapital ≤ 0` (capital shrank — buybacks/cash build) → `None`
    (don't report a sign-flipped artifact).
- **ROCE level + trend** (phase-3 gate) — uses the **same headline ROCE basis**
  `EBIT/(TA−CL)` (cash KEPT — one consistent ROCE definition everywhere; no separate
  cash-excluded "adjusted" view, per review). Cash-heavy growers that look weak on the
  level still have the **incremental-ROIC > 20%** branch as their route to pass.
  - `roce_latest_pct` = latest-year `EBIT/(TA−CL)`.
  - `roce_rising` = `roce_latest > roce_3y_ago` (or > earliest within window if <4y).

## Band logic (`robustness.py`)

`band_for_roce(headline, phase)` replaces the current `band_for_roce(pct)`:

```
phase is None or >= 5 or phase == 6 handling:
    None / 5  -> current 20/12 gate on headline avg_roce_pct
    6         -> 'fragile'
phase == 4:   >=20 robust, >=15 mid, else fragile     (on GAAP avg_roce_pct)
phase == 3:   strong = (roce_latest >= 10 and roce_rising); roic = (incr_roic > 20)
              both -> robust; either -> mid; neither -> fragile
              (if both inputs None -> strict GAAP gate)
phase == 2:   rule_of_40 is primary; incr_roic only fails it if measurably <= 0.
              r40 is None              -> strict GAAP gate (primary unmeasurable)
              r40 >= 40 and (incr_roic is None or incr_roic > 0) -> 'mid' (capped)
              else                     -> 'fragile'
              (rationale: an unmeasurable noisy secondary must not slam an
               otherwise-passing name back to the 20% gate — NET has no debt
               data so incr_roic is None, but Rule of 40 ≈ 44 is a clear pass)
phase == 1:   'n/a'
```

Missing-input fallback (anti-dodge): if the inputs a phase needs are `None`
(e.g. phase 2 but `incr_roic` uncomputable), **fall back to the strict GAAP 20%
gate** rather than passing by default. A company can't earn a soft pass by having
unmeasurable metrics.

## Verdict logic changes (`derive_verdict`)

1. ROCE band `n/a` (phase 1): exclude `roce` from the `red_db` deal-breaker check;
   force `verdict = borderline`, `reason = "phase 1 — too early to judge capital returns (defer)"`.
   Never allow `robust` at phase 1.
2. ROCE band `mid` at phase 2: existing rule (`band("roce") != "robust" → borderline`)
   already yields `revisit`. Tag the reason `"phase 2 — conditional (Rule of 40 + incr. ROIC)"`.
3. All other phases: unchanged weakest-link flow; only the *band* feeding it is phase-tiered.

## Anti-rationalization guards (explicit, per user)

- **Phase is read, not chosen by the gate.** It comes from the Scorecard prescan
  (set before, by the analyst/AI), so you can't re-phase inside the verdict step to
  dodge a gate.
- **Unknown/missing phase → strict 20% gate** (default to the hardest bar).
- **Phase 5's 20% bar is never lowered.** The tiers only relax *earlier* phases.
- **The requirement never disappears** — every phase (except 1, defer) must clear
  *some* bar; failing the phase-appropriate test still yields `fragile`.
- **Phases 1–3 can never reach `deep_dive` on capital efficiency alone** (capped at
  `revisit`/conditional), keeping Type-1 aversion intact.

## Files to change

- `mcp_server.py::_compute_fundamentals_headline` — add `rule_of_40_pct`,
  `fcf_margin_pct`, `revenue_growth_pct`, `incremental_roic_pct`, `adjusted_roce_pct`,
  `adjusted_roce_rising` to the headline dict.
- `robustness.py` — phase-aware `band_for_roce`; thread `phase` through
  `compute_data_axes(headline, phase)`, `merge_base_axes(headline, ai_notes)` (parse
  phase here), `build_table`; phase-1 handling in `derive_verdict`.
- `scorecard_utils.py` — reuse/expose the existing phase parser for `merge_base_axes`.
- No `set_robustness` signature change — phase comes from stored Scorecard, not a new arg.

## Testing (`test_robustness.py` / `test_mcp_server.py`)

- Per-phase band table: phase 5 NET-like (ROCE 7%) → fragile; phase 2 NET-like
  (R40=43, incr ROIC>0) → mid → verdict revisit; phase 3 adj ROCE 10% rising → mid;
  phase 4 ROCE 16% → mid, 21% → robust; phase 1 → n/a → revisit (not pass).
- Anti-dodge: phase 2 with `incr_roic=None` → strict gate → fragile if ROCE<20.
- incr_roic guards: shrinking invested capital → None; <3 usable years → None.
- Verdict integration: phase-1 n/a not counted as red deal-breaker.

## Migration after deploy

Phase-aware verdicts only materialize when `build_table` re-runs. After deploy,
re-run `set_robustness` (or a refresh path) for early-phase tickers (NET and any
phase 1–4 names) so their stored `cfg['robustness'].verdict` updates. Mature
(phase 5) names are unaffected (same 20% gate).

## Resolved decisions (review 2026-06-16)

1. **Rule of 40 growth basis** → **3-year revenue CAGR** (steadier than YoY).
2. **No cash-excluded "adjusted ROCE"** → keep **one** ROCE definition everywhere
   (`EBIT/(TA−CL)`, cash kept). The phase-3 cash-heavy-grower concern is covered by
   the incremental-ROIC > 20% OR-branch, not by a second denominator. Avoids the
   "which ROCE?" inconsistency.
3. **Phase-1 verdict** → reuse **`revisit`** (defer) with a clear reason string; no
   new verdict band.
4. **Incremental ROIC window** → **3 deltas** with the noise guards above.
