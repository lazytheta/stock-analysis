# Sum-of-the-Parts (SOTP) Lens — Design

**Status:** Draft for review
**Date:** 2026-05-13
**Author:** Arjan + Claude

## Goal

Extend the multi-lens valuation framework with a **Sum-of-the-Parts (SOTP)** lens. For multi-segment businesses (AMZN, GOOGL, DIS), aggregate DCF averages business segments with structurally different margin/growth profiles. SOTP values each segment separately and aggregates Enterprise Values + corporate adjustments → equity → intrinsic per share.

Methodology rationale: see `portfolio-vault/Concepts/SOTP-Valuation.md`.

## Scope — Phase 1 only

Single-phase delivery: **manual per-segment EV input with low/mid/high per segment**. No per-segment DCF engine. The lens computes:
```
SUM(segment_EVs_low/mid/high) + non_operating_assets - debt - minority - pension + cash = equity_low/mid/high
equity_low/mid/high / shares = SOTP_FV_low/mid/high
```

**Scope:** 3-4 hours, single sprint.

### Phase 2 — not planned

Per-segment DCF (segment-level revenue/margin paths, sector betas, terminal aannames) was originally considered as Phase 2. **Decision 2026-05-13:** not planned. Manual EV input with per-segment low/mid/high gives sufficient flexibility for the current portfolio scale. If reproducibility-on-market-moves or in-tool stress-testing becomes a real pain point after 2-3 months of usage, revisit.

## Non-Goals

- Per-segment DCF engine (decided against — see "Scope" above)
- Auto-fetching segment financials from SEC filings (user inputs EVs based on own analysis)
- Intercompany elimination logic (assume user-supplied EVs are clean)
- UI for editing segment data via a graphical canvas (use Streamlit table editor)
- Backfill of all existing watchlist tickers — only AMZN ships with seeded data
- Football field bar for SOTP range — defer; lens-dots only for now
- Auto-derived corporate overhead capitalization (manual input only, default $0)

## Architecture

```
stock-analysis/
├── valuation_lenses.py        ← MODIFIED: add sotp_lens() function
├── dcf_calculator.py          ← UNCHANGED (no per-segment DCF in MVP)
├── config_store.py            ← MODIFIED: guard new sotp_segments key on upsert
├── mcp_server.py              ← MODIFIED: calculate_multi_lens_valuation routes through SOTP if present
├── streamlit_app.py           ← MODIFIED: editor UI for segments + hero card pill
└── tests/test_sotp_lens.py    ← NEW: 4-5 acceptance tests
```

## Data model

### New optional config key

```python
config["sotp"] = {
    "segments": [
        {
            "name": str,                # e.g. "AWS"
            "ev_low": float,            # Bear-case Enterprise Value contribution in $M
            "ev_mid": float,            # Base-case Enterprise Value (REQUIRED — primary value)
            "ev_high": float,           # Bull-case Enterprise Value
            "rationale": str,           # free text — how we got to these EVs
            # Optional metadata (display only, not used in calc):
            "revenue": float | None,
            "operating_margin": float | None,
            "implied_multiple_mid": float | None,
        },
        ...
    ],
    "corporate_overhead_ev_adjustment": float,   # manual input, negative = subtract; default 0
    "calculated_at": str | None,                 # ISO timestamp, set by lens
}
```

### Why per-segment low/mid/high (not a blanket ±X%)

A blanket percentage range (e.g. ±10%) is methodologically lazy: it assumes equal uncertainty across all segments. In reality, segments have very different confidence profiles. Example for AMZN:

- **AWS** ±5% range (peer multiples are solid, segment financials disclosed, multi-year track record)
- **Retail** ±15% range (margin sensitivity to consumer cycle, mix-shift)
- **Advertising** ±25% range (growth × margin uncertainty compounds, less peer-comparable)

Per-segment range respects this divergence and produces more honest aggregate ranges. The user can choose to enter symmetric `(ev_mid × 0.9, ev_mid, ev_mid × 1.1)` if they truly don't have segment-level confidence info, but the option to be more precise exists.

### Why a top-level `sotp` key (not nested in `valuation_summary`)
The segment definitions are user-supplied inputs (like `peers` or `valuation_inputs`); they persist between calculations. `valuation_summary` is recalculated each run. Keep them separate.

### Bridge inputs already present
The lens reuses existing config keys for the bridge:
- `cash` (latest year used)
- `securities` / `st_investments`
- `debt_market_value` or `debt_breakdown`
- `minority_interest`
- `unfunded_pension`
- `equity_investments` (treated as non-operating asset, added to EV-sum)
- `shares_outstanding`

## Calculation logic

```python
def sotp_lens(config: dict) -> dict | None:
    """Compute SOTP fair value with per-segment low/mid/high EVs.

    Returns None if no sotp.segments present.
    Per-segment range aggregates to total range; bridge items are point estimates.
    """
    sotp = config.get("sotp") or {}
    segments = sotp.get("segments") or []
    if not segments:
        return None

    def _seg_sum(key: str) -> float:
        return sum(float(s.get(key, s.get("ev_mid", 0))) for s in segments)

    total_ev_low = _seg_sum("ev_low")
    total_ev_mid = _seg_sum("ev_mid")
    total_ev_high = _seg_sum("ev_high")

    corp_adj = float(sotp.get("corporate_overhead_ev_adjustment", 0))
    equity_inv = float(config.get("equity_investments", 0))

    # Bridge items are point estimates (cash/debt are known, not ranged)
    cash_latest = (config.get("cash") or [0])[-1]
    securities_latest = (config.get("st_investments") or [0])[-1]
    debt = float(config.get("debt_market_value", 0))
    minority = float(config.get("minority_interest", 0))
    pension = float(config.get("unfunded_pension", 0))
    bridge_delta = equity_inv + corp_adj + cash_latest + securities_latest - debt - minority - pension

    shares = float(config.get("shares_outstanding", 1)) or 1

    fv_low = (total_ev_low + bridge_delta) / shares
    fv_mid = (total_ev_mid + bridge_delta) / shares
    fv_high = (total_ev_high + bridge_delta) / shares

    return {
        "fv_low": fv_low,
        "fv_mid": fv_mid,
        "fv_high": fv_high,
        "details": {
            "total_ev_low": total_ev_low,
            "total_ev_mid": total_ev_mid,
            "total_ev_high": total_ev_high,
            "segment_count": len(segments),
            "segments": [
                {
                    "name": s.get("name"),
                    "ev_low": s.get("ev_low"),
                    "ev_mid": s.get("ev_mid"),
                    "ev_high": s.get("ev_high"),
                    "pct_of_total_mid": (float(s.get("ev_mid", 0)) / total_ev_mid) if total_ev_mid else 0,
                }
                for s in segments
            ],
            "bridge_delta": bridge_delta,
            "equity_value_mid": total_ev_mid + bridge_delta,
            "shares": shares,
        },
    }
```

### Range methodology
Per-segment low/mid/high aggregates by summing. This respects that different segments have different confidence intervals (AWS ±5%, Advertising ±25%). Bridge items (cash, debt, etc.) are point estimates — they're known accounting values, not ranged. Result: aggregate range is driven entirely by segment-EV uncertainty, which is the right place for it.

If a user enters only `ev_mid` without `ev_low`/`ev_high`, the function falls back to `ev_mid` for the missing values (range collapses to zero — fv_low = fv_mid = fv_high). Acceptable default; UI should warn.

### Edge cases
- **No segments defined** → return `None`, lens skipped (default weight 0 for this ticker).
- **One segment** → still works mathematically, but logically should be DCF only. Acceptable to allow.
- **Negative segment EV** → allowed (loss-making segment).
- **Shares = 0** → fallback to 1 to avoid divide-by-zero. Should never happen with real data.

## Multi-lens integration

### Lens weights — default per ticker

The existing `lens_weights` config key already supports per-ticker overrides. SOTP defaults:

```python
DEFAULT_LENS_WEIGHTS = {
    "dcf": 0.50,
    "multiples": 0.25,
    "historical": 0.25,
    "dividend": 0.0,
    "reverse_dcf": 0.0,
    "sotp": 0.0,   # NEW — default off; per-ticker override for multi-segment names
}
```

Per-ticker SOTP-active config (proposed for AMZN):
```python
config["lens_weights"] = {
    "dcf": 0.30,
    "multiples": 0.20,
    "historical": 0.15,
    "sotp": 0.35,
    "dividend": 0.0,
    "reverse_dcf": 0.0,
}
```

### Weight normalization
Existing `calculate_multi_lens_valuation()` normalizes weights to sum=1 over lenses that returned non-null FV. No change needed — SOTP plugs in cleanly.

## UI changes (Streamlit)

### Editor: new section in ticker detail page

Below the DCF inputs editor, a collapsible **SOTP Segments** section:

```
▼ SOTP Segments (optional, for multi-segment businesses)

[+] Add segment

| Name       | EV Low     | EV Mid     | EV High    | % of mid | Rationale                          | [del] |
|------------|------------|------------|------------|----------|------------------------------------|-------|
| AWS        | 850,000    | 950,000    | 1,100,000  | 73%      | 18-22x forward EV/EBITDA on $47B  | 🗑️    |
| Retail     | 170,000    | 200,000    | 230,000    | 15%      | 0.7-0.9x revenue, consumer cycle  | 🗑️    |
| Ads        | 110,000    | 150,000    | 200,000    | 12%      | Growth × margin uncertainty       | 🗑️    |
+---------------------------------------------------------------------------------------+
| Total segment EV:  Low $1,130,000M  ·  Mid $1,300,000M  ·  High $1,530,000M           |
| + Equity investments: $25,000M (Anthropic)                                             |
| + Cash: $101,800M  ·  + Securities: $41,300M                                           |
| − Debt: $119,100M  ·  − Minority: $0  ·  − Pension: $0                                 |
| = Equity Value:    Low $1,179,000M ·  Mid $1,349,000M ·  High $1,579,000M             |
| / Shares (10,757M)                                                                     |
| = SOTP Fair Value: Low $109.59 · Mid $125.39 · High $146.79                            |
+---------------------------------------------------------------------------------------+

Corporate overhead EV adjustment: $0 (optional, negative to subtract)
```

Implementation: standard `st.data_editor` table for rows + computed total row underneath.

### Hero card pill (when SOTP lens has FV)

Add an "SOTP FV" pill to the hero card row, between Multi-lens pills and Verdict:

```
... | Multi-lens FV $X ($low–$high) | ML Buy $Y | ML Upside ±Z% | SOTP FV $W | Verdict ...
```

Color rules: same as other FV pills (no color emphasis unless upside computed).

### Lens-dots / football field
For Phase 1, SOTP appears as a 5th lens-dot in the watchlist row. Football-field bar implementation deferred to Phase 2 (needs segment-level range data).

## AMZN seed data

To ship Phase 1 with a working example, seed AMZN config:

```python
config["sotp"] = {
    "segments": [
        {
            "name": "AWS",
            "ev_low": 850000,
            "ev_mid": 950000,
            "ev_high": 1100000,
            "rationale": "18-22x forward EV/EBITDA on $47B FY26E EBITDA; consistent with MSFT Azure-implied multiples",
            "revenue": 108000,
            "operating_margin": 0.37,
            "implied_multiple_mid": 20.2,
        },
        {
            "name": "Retail (NA + Intl)",
            "ev_low": 170000,
            "ev_mid": 200000,
            "ev_high": 230000,
            "rationale": "0.7-0.9x revenue on $250B segment; WMT 0.9x / TGT 0.7x bracket; consumer-cycle sensitivity",
            "revenue": 250000,
            "operating_margin": 0.04,
        },
        {
            "name": "Advertising",
            "ev_low": 110000,
            "ev_mid": 150000,
            "ev_high": 200000,
            "rationale": "2.0-3.6x revenue on $56B FY24 ads; META-light multiple; growth × margin uncertainty compounds widest range",
            "revenue": 56000,
            "operating_margin": 0.50,
        },
    ],
    "corporate_overhead_ev_adjustment": -25000,
    "calculated_at": "2026-05-13T00:00:00Z",
}
config["lens_weights"] = {"dcf": 0.30, "multiples": 0.20, "historical": 0.15, "sotp": 0.35, "dividend": 0.0, "reverse_dcf": 0.0}
```

Expected SOTP FV with seed data: Low ~$110 · Mid ~$125 · High ~$147. Note: mid is close to aggregate DCF $124 — suggests current AMZN frontmatter `fair_value_sotp: 216` used different (more bullish) segment EVs. User will reconcile during seed.

## Acceptance criteria

1. `valuation_lenses.sotp_lens()` returns expected FV given AMZN seed data, ±1% tolerance
2. `calculate_multi_lens_valuation("AMZN")` includes SOTP in lens output when sotp.segments populated
3. `calculate_multi_lens_valuation("NFLX")` does NOT include SOTP (no segments) — lens skipped, weights renormalize
4. Streamlit editor: add/edit/remove segments persists via `save_config`
5. Hero card shows "SOTP FV $X" pill when lens returns mid
6. Ruff: zero new errors
7. Tests: 4 new in `test_sotp_lens.py`, all green

## Decisions (resolved 2026-05-13)

1. **Corporate overhead adjustment:** Manual input only ($M to subtract from EV-sum). No auto-derived overhead capitalization — confirmed by user as "vaak een getal waar we niets mee kunnen".

2. **Range methodology:** Per-segment user-supplied `ev_low` / `ev_mid` / `ev_high`. Not a blanket ±X%. Different segments have structurally different uncertainty profiles.

3. **AMZN frontmatter `fair_value_sotp: 216`:** Replace with SOTP lens output once live. The hardcoded frontmatter value goes away.

4. **Lens-weights when SOTP activated:** Explicit per-ticker config — no auto-rebalance. SOTP isn't applicable to most tickers; user opts in per ticker.

5. **Phase 2 (per-segment DCF):** Not planned. If reproducibility-on-market-moves or in-tool stress-testing becomes a pain point after 2-3 months of usage, revisit.

## Estimated effort

| Task | Hours |
|---|---|
| Implement `sotp_lens()` with low/mid/high in valuation_lenses.py + tests | 1.0 |
| Wire into `calculate_multi_lens_valuation` MCP tool | 0.3 |
| `config_store.py` guard for new key on upsert | 0.2 |
| Streamlit editor UI (segments table with low/mid/high + bridge display) | 1.5 |
| Hero card pill addition | 0.2 |
| AMZN seed data + verification | 0.3 |
| **Total** | **3.5 hours** |

## References

- Methodology doc: `portfolio-vault/Concepts/SOTP-Valuation.md`
- Existing multi-lens design: `docs/superpowers/specs/2026-05-05-multi-lens-fair-value-design.md`
- AMZN prescan + DCF rebuild context: `portfolio-vault/Research-Log/2026-05-12 AMZN prescan + DCF rebuild.md`
- Damodaran SOTP write-ups: `aswathdamodaran.blogspot.com` (general reference)
