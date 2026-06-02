"""Prasad robustness table — pure scoring + weakest-link verdict.

No I/O. Data axes (ROCE, net debt) are fed from the fundamentals headline
(mcp_server._compute_fundamentals_headline); the 4 qualitative axes come from
the AI 'Robustness' prescan section. See
docs/superpowers/specs/2026-06-02-prescan-robustness-table-design.md
"""
from scorecard_utils import parse_scorecard_json

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

    red_db = [k for k in DEAL_BREAKERS if band(k) == "fragile"]
    noncrit_red = sum(1 for k, _, db, _ in AXES if not db and band(k) == "fragile")

    if red_db:
        verdict, reason = "fragile", f"deal-breaker red: {', '.join(red_db)}"
    elif band("roce") != "robust":
        verdict, reason = "borderline", f"ROCE below the {ROCE_GATE}% gate"
    elif any(band(k) == "mid" for k in DEAL_BREAKERS) or noncrit_red >= 2:
        amber_db = [k for k in DEAL_BREAKERS if band(k) == "mid"]
        reason = (f"deal-breaker amber: {', '.join(amber_db)}" if amber_db
                  else "two or more non-critical axes fragile")
        verdict = "borderline"
    else:
        verdict, reason = "robust", "all deal-breakers green"

    return {"verdict": verdict, "verdict_mapped": _VERDICT_MAP[verdict],
            "verdict_reason": reason}


def compute_data_axes(headline):
    """Two data-driven axes from a fundamentals headline dict
    (mcp_server._compute_fundamentals_headline)."""
    roce_pct = headline.get("avg_roce_pct")
    metric = headline.get("roce_metric", "ROCE")
    nd_ebitda = headline.get("latest_net_debt_ebitda")
    nd_m = headline.get("latest_adjusted_net_debt_m")
    return {
        "roce": {"band": band_for_roce(roce_pct), "value": roce_pct,
                 "metric": metric, "source": "data"},
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
    """Data axes + AI axes (no overrides). Missing AI axes default to 'mid'."""
    axes = dict(compute_data_axes(headline))
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
