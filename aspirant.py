"""Rules for the aspirant pipeline: Screener names on their way into the watchlist.

Pure functions, no I/O, so the MCP server and the Streamlit page apply the
same rules and the tests can pin them without Supabase.
"""

import re

# Display order of the watchlist groups. Aspirant sits under Yes: new names to
# look at, above the piles already decided.
CATEGORIES = ("Yes", "Aspirant", "Maybe", "Watch Later", "No", "Uncategorized")


def curves_are_flat(cfg):
    """True while growth and margin curves are still build_config's placeholders.

    build_config writes growth = terminal growth and margin = last real margin
    for every year. A curve someone actually filled in varies somewhere.
    """
    for key in ("revenue_growth", "op_margins"):
        curve = [round(float(v), 6) for v in (cfg.get(key) or [])]
        if len(set(curve)) > 1:
            return False
    return True


# The Moat prompt opens with "**Moat: Wide 🛡️ · Stable ➡️ · 4/5**". Only that
# line decides; prescan_render.parse_verdict_section also wants the bullets
# below it (it builds a card), and a gate must not fail on formatting.
_MOAT_LINE = re.compile(r"^\*\*Moat:\s*([A-Za-z]+)", re.M)


def moat_label(ai_notes):
    """"Wide" / "Narrow" / "None" from the Moat section's verdict line, else None."""
    if not isinstance(ai_notes, dict):
        return None
    text = ai_notes.get("Moat") or ""
    m = _MOAT_LINE.search(text)
    if not m or m.start() > 400:   # the verdict opens the section
        return None
    label = m.group(1).title()
    return label if label in ("Wide", "Narrow", "None") else None


def promotion_blockers(cfg):
    """Why this config may not leave Aspirant yet; [] when it may."""
    out = []
    if cfg.get("category") != "Aspirant":
        out.append("not an Aspirant")
    if moat_label(cfg.get("ai_notes")) != "Wide":
        out.append("Moat verdict is not Wide")
    if cfg.get("dcf_placeholder"):
        out.append("DCF is still the placeholder (growth/margin curves are flat)")
    try:
        emv = float(cfg.get("equity_market_value") or 0)
    except (TypeError, ValueError):
        emv = 0
    if emv <= 0:
        out.append("equity_market_value missing")
    try:
        weights = sum(float(e[2]) for e in cfg.get("sector_betas") or [])
    except (TypeError, IndexError, ValueError):
        weights = 0
    if abs(weights - 1.0) > 0.001:
        out.append("sector_betas weights do not sum to 1.0")
    if not cfg.get("valuation_summary"):
        out.append("valuation_summary missing (run calculate_multi_lens_valuation)")
    return out
