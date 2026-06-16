"""Prasad robustness table — pure scoring + weakest-link verdict.

No I/O. Data axes (ROCE, net debt) are fed from the fundamentals headline
(mcp_server._compute_fundamentals_headline); the 4 qualitative axes come from
the AI 'Robustness' prescan section. See
docs/superpowers/specs/2026-06-02-prescan-robustness-table-design.md
"""
from scorecard_utils import parse_scorecard, parse_scorecard_json

# key -> (label, is_deal_breaker, source)
AXES = (
    ("roce",       "ROCE (5–10y)",           True,  "data"),
    ("net_debt",   "Net debt",               True,  "data"),
    ("customers",  "Customer/supplier base", False, "ai"),
    ("barriers",   "Competitive barriers",   False, "ai"),
    ("management", "Management",             True,  "ai"),
    ("industry",   "Industry change",        False, "ai"),
)
DEAL_BREAKERS = tuple(k for k, _, db, _ in AXES if db)
AI_AXES = tuple(k for k, _, _, src in AXES if src == "ai")
BANDS = ("robust", "mid", "fragile")
_VERDICT_MAP = {"robust": "deep_dive", "borderline": "revisit", "fragile": "pass"}
ROCE_GATE = 20  # Prasad's quality gate: never "robust" below this ROCE %


def band_for_roce(pct):
    """≥20% robust, 12–<20% mid, <12% fragile, None → fragile.
    Never green under 20% — the Prasad quality gate."""
    if pct is None:
        return "fragile"
    if pct >= ROCE_GATE:
        return "robust"
    if pct >= 12:
        return "mid"
    return "fragile"


def phased_roce_axis(headline, phase):
    """Phase-aware ROCE/capital-returns axis (see
    specs/2026-06-16-phase-aware-roce-gate-design).

    Returns ``{"band", "metric", "value", "basis"}`` where the band reflects
    whether the company cleared its *phase-appropriate* bar — and ``metric`` /
    ``value`` name the test that actually applied (e.g. "Rule of 40" 44, not a
    misleading GAAP ROCE) so the table states *why* it passed for that phase.

    A passing early-phase name is 'robust' **for its phase** (green); the
    verdict step caps phases 1–3 at conditional so it never becomes a clean
    deep_dive. Phase 1 → 'n/a' (defer). Anti-dodge: when a phase's primary
    input is unmeasurable, fall back to the strict GAAP gate. Phase 5's 20%
    bar is never lowered.
    """
    avg = headline.get("avg_roce_pct")
    metric = headline.get("roce_metric", "ROCE")
    if phase == 1:
        return {"band": "n/a", "metric": metric, "value": avg,
                "basis": "phase 1 — too early to judge capital returns"}
    if phase == 6:
        return {"band": "fragile", "metric": metric, "value": avg,
                "basis": "phase 6 — decline / avoid"}
    if phase == 2:
        r40 = headline.get("rule_of_40_pct")
        iroic = headline.get("incremental_roic_pct")
        if r40 is None:  # primary signal unmeasurable → strict gate
            return {"band": band_for_roce(avg), "metric": metric, "value": avg,
                    "basis": "phase 2 — Rule of 40 unavailable → strict 20% gate"}
        # Rule of 40 is primary; incremental ROIC is a noisy secondary that
        # only fails it when *measurably* ≤ 0. Pass → robust FOR ITS PHASE.
        passed = r40 >= 40 and (iroic is None or iroic > 0)
        return {"band": "robust" if passed else "fragile",
                "metric": "Rule of 40", "value": r40,
                "basis": f"phase 2 — Rule of 40 {'≥' if passed else '<'} 40"
                         + ("" if iroic is None else f", incr. ROIC {iroic:.0f}%")}
    if phase == 3:
        latest = headline.get("roce_latest_pct")
        iroic = headline.get("incremental_roic_pct")
        if latest is None and iroic is None:
            return {"band": band_for_roce(avg), "metric": metric, "value": avg,
                    "basis": "phase 3 — inputs unavailable → strict 20% gate"}
        strong_roce = latest is not None and latest >= 10 and bool(headline.get("roce_rising"))
        strong_roic = iroic is not None and iroic > 20
        if strong_roce and strong_roic:
            band = "robust"
        elif strong_roce or strong_roic:
            band = "mid"
        else:
            band = "fragile"
        # Name the metric that carried it (incr. ROIC if that's the strong leg)
        if strong_roic and not strong_roce:
            return {"band": band, "metric": "Incr. ROIC", "value": iroic,
                    "basis": "phase 3 — incremental ROIC > 20%"}
        return {"band": band, "metric": "ROCE (latest)", "value": latest,
                "basis": "phase 3 — ROCE ≥ 10% and rising"}
    if phase == 4:
        if avg is None:
            band = "fragile"
        elif avg >= ROCE_GATE:
            band = "robust"
        elif avg >= 15:
            band = "mid"
        else:
            band = "fragile"
        return {"band": band, "metric": metric, "value": avg,
                "basis": "phase 4 — ROCE ≥ 15% (climbing to 20)"}
    # phase 5, None, or unrecognized → strict Prasad gate
    return {"band": band_for_roce(avg), "metric": metric, "value": avg,
            "basis": "phase 5 — ROCE ≥ 20% (Prasad gate)"}


def phased_roce_band(headline, phase):
    """Thin wrapper: just the band from phased_roce_axis."""
    return phased_roce_axis(headline, phase)["band"]


def band_for_net_debt(nd_ebitda, net_debt_m=None):
    """net cash or ≤1x EBITDA → robust, 1–2x → mid, >2x → fragile.
    If the EBITDA ratio is unknown, fall back to net-debt sign."""
    if nd_ebitda is None:
        if net_debt_m is None:
            return "mid"
        return "robust" if net_debt_m <= 0 else "mid"
    if nd_ebitda <= 1:
        return "robust"
    if nd_ebitda <= 2:
        return "mid"
    return "fragile"


def _apply_disney_exception(axes):
    """Industry 🔴 + barriers 🟢 → soften industry to 🟡 (a strong moat makes a
    fast-moving industry predictable enough — per the user's DIS note).
    Returns a copy; does not mutate the input."""
    axes = {k: dict(v) for k, v in axes.items()}
    ind, bar = axes.get("industry", {}), axes.get("barriers", {})
    if ind.get("band") == "fragile" and bar.get("band") == "robust":
        ind["band"] = "mid"
        ind["note"] = (ind.get("note", "") + " [softened: strong moat]").strip()
        axes["industry"] = ind
    return axes


def derive_verdict(axes):
    """Weakest-link, Type-1-averse verdict from merged axes (overrides already
    applied). Returns {'verdict','verdict_mapped','verdict_reason'}.
    First matching rule wins."""
    axes = _apply_disney_exception(axes)

    def band(k):
        return axes.get(k, {}).get("band", "mid")

    # Phase 1 — too early to judge capital returns. ROCE is 'n/a' (not a red
    # deal-breaker); defer rather than pass, and never robust.
    if band("roce") == "n/a":
        return {"verdict": "borderline", "verdict_mapped": _VERDICT_MAP["borderline"],
                "verdict_reason": "phase 1 — too early to judge capital returns (defer)"}

    red_db = [k for k in DEAL_BREAKERS if band(k) == "fragile"]
    noncrit_red = sum(1 for k, _, db, _ in AXES if not db and band(k) == "fragile")

    if red_db:
        verdict, reason = "fragile", f"deal-breaker red: {', '.join(red_db)}"
    elif band("roce") != "robust":
        _ph = axes.get("roce", {}).get("phase")
        if _ph in (2, 3, 4):
            reason = f"phase {_ph} — capital returns conditional (below the mature 20% gate)"
        else:
            reason = f"ROCE below the {ROCE_GATE}% gate"
        verdict = "borderline"
    elif any(band(k) == "mid" for k in DEAL_BREAKERS) or noncrit_red >= 2:
        amber_db = [k for k in DEAL_BREAKERS if band(k) == "mid"]
        reason = (f"deal-breaker amber: {', '.join(amber_db)}" if amber_db
                  else "two or more non-critical axes fragile")
        verdict = "borderline"
    else:
        verdict, reason = "robust", "all deal-breakers green"

    # Phase cap: an early-phase (2–3) name that cleared its phase-appropriate bar
    # is conditional at best — never a clean deep_dive (it has not yet proven a
    # mature 20%+ ROCE). Name the test it passed rather than collapsing to a
    # generic 'mid'. (Phase 1 already returned above; phase 4 can earn robust.)
    _rax = axes.get("roce", {})
    _ph = _rax.get("phase")
    if verdict == "robust" and _ph in (2, 3):
        _m, _v = _rax.get("metric", "phase bar"), _rax.get("value")
        _vs = f"{_v:.0f}" if isinstance(_v, (int, float)) else "—"
        verdict = "borderline"
        reason = (f"phase {_ph} — {_m} {_vs} cleared; conditional "
                  "(not yet a proven 20%+ mature ROCE)")

    return {"verdict": verdict, "verdict_mapped": _VERDICT_MAP[verdict],
            "verdict_reason": reason}


def compute_data_axes(headline, phase=None):
    """Two data-driven axes from a fundamentals headline dict
    (mcp_server._compute_fundamentals_headline). The ROCE band is phase-aware
    when ``phase`` is supplied (defaults to the strict gate)."""
    rax = phased_roce_axis(headline, phase)
    nd_ebitda = headline.get("latest_net_debt_ebitda")
    nd_m = headline.get("latest_adjusted_net_debt_m")
    return {
        "roce": {"band": rax["band"], "value": rax["value"], "metric": rax["metric"],
                 "basis": rax["basis"], "phase": phase, "source": "data"},
        "net_debt": {"band": band_for_net_debt(nd_ebitda, nd_m), "value": nd_ebitda,
                     "net_debt_m": nd_m, "unit": "x_ebitda", "source": "data"},
    }


def parse_ai_axes(ai_notes):
    """Extract the 4 qualitative axes from ai_notes['Robustness'] JSON.
    Returns {} when absent/unparseable (callers default missing axes to 'mid')."""
    if not isinstance(ai_notes, dict):
        return {}
    data = parse_scorecard_json(ai_notes.get("Robustness"))
    if not isinstance(data, dict):
        return {}
    axes = data.get("axes") if isinstance(data.get("axes"), dict) else data
    out = {}
    for key in AI_AXES:
        entry = axes.get(key) if isinstance(axes, dict) else None
        if isinstance(entry, dict) and entry.get("band") in BANDS:
            out[key] = {"band": entry["band"], "note": entry.get("note", ""), "source": "ai"}
    return out


def merge_base_axes(headline, ai_notes):
    """Data axes + AI axes (no overrides). Missing AI axes default to 'mid'.
    The business phase is read from the Scorecard section (set by the prescan,
    never inferred here) and drives the phase-aware ROCE band."""
    phase = parse_scorecard(ai_notes).get("phase")
    axes = dict(compute_data_axes(headline, phase))
    ai = parse_ai_axes(ai_notes)
    for key in AI_AXES:
        axes[key] = ai.get(key, {"band": "mid", "note": "", "source": "ai"})
    return axes


def resolve(base_axes, overrides=None):
    """Apply user band overrides to base axes, then derive the verdict.
    Returns (effective_axes, verdict_dict). Needs no fundamentals — used by the
    override editor."""
    overrides = overrides or {}
    axes = {k: dict(v) for k, v in base_axes.items()}
    for key, band in overrides.items():
        if key in axes and band in BANDS:
            axes[key] = {**axes[key], "band": band, "source": "override"}
    return axes, derive_verdict(axes)


def build_table(headline, ai_notes, overrides=None):
    """Full robustness view-model: base axes, effective axes, verdict.
    Caller stamps computed_at."""
    base = merge_base_axes(headline, ai_notes)
    effective, verdict = resolve(base, overrides)
    return {"axes_base": base, "axes": effective,
            "overrides": dict(overrides or {}), **verdict}
