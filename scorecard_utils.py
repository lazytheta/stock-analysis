"""Shared scorecard parser. Used by streamlit_app.py renderer and the MCP
watchlist enrichment. Single source of truth for the JSON-in-markdown format
the Scorecard pre-scan section uses."""

import json
import re

# Average (TA−CL)/TA below this = a genuine float business (current
# liabilities — customer deposits, settlement balances — fund 75%+ of the
# asset base), where capital employed is too small for ROCE to be meaningful;
# fall back to ROE. Settlement networks (V/MA) and goodwill-heavy or
# buyback-thin names sit well above this and stay on ROCE.
FLOAT_CE_TA_THRESHOLD = 0.25


def compute_roce_metric(fund, cfg=None):
    """Single source of truth for the watchlist/detail/MCP quality metric.

    Returns ``(metric, avg_value)`` where ``metric`` is ``'ROCE'`` or ``'ROE'``:
      • ROCE = avg of EBIT / (Total Assets − Current Liabilities). Goodwill and
        cash are NOT subtracted (vault methodology 2026-06-12).
      • ROE  = avg of Net Income / Total Equity.

    Metric selection:
      1. Manual override wins: ``cfg['roce_metric_override']`` in
         {'ROCE','ROE'} forces that metric (use 'ROE' to flag a genuine
         float business that the auto-test doesn't catch).
      2. Auto fallback: ROE when avg (TA−CL)/TA < FLOAT_CE_TA_THRESHOLD,
         else ROCE.
    ``avg_value`` is None when the chosen metric has no computable years.
    """
    oi_w = fund.get("operating_income") or []
    ta_w = fund.get("total_assets") or []
    cl_w = fund.get("current_liabilities") or []
    ni_w = fund.get("net_income") or []
    eq_w = fund.get("total_equity") or []
    n = len(fund.get("years") or oi_w)

    roce_pcts, ce_ta_ratios = [], []
    for i in range(n):
        oi_v = oi_w[i] if i < len(oi_w) else None
        ta_v = ta_w[i] if i < len(ta_w) else None
        cl_v = cl_w[i] if i < len(cl_w) else None
        if oi_v is not None and ta_v and ta_v > 0 and cl_v is not None:
            ce = ta_v - cl_v
            ce_ta_ratios.append(max(ce, 0) / ta_v)
            if ce > 0:
                roce_pcts.append(oi_v / ce * 100)

    roe_pcts = []
    for i in range(n):
        ni_v = ni_w[i] if i < len(ni_w) else None
        eq_v = eq_w[i] if i < len(eq_w) else None
        if ni_v is not None and eq_v and eq_v > 0:
            roe_pcts.append(ni_v / eq_v * 100)

    avg_ce_ta = (sum(ce_ta_ratios) / len(ce_ta_ratios)) if ce_ta_ratios else 1.0
    override = (cfg or {}).get("roce_metric_override")
    if override in ("ROCE", "ROE"):
        metric = override
    else:
        metric = "ROE" if (ce_ta_ratios and avg_ce_ta < FLOAT_CE_TA_THRESHOLD) else "ROCE"

    pcts = roe_pcts if metric == "ROE" else roce_pcts
    avg_value = (sum(pcts) / len(pcts)) if pcts else None
    return metric, avg_value


def parse_scorecard_json(raw: str | None) -> dict | None:
    """Extract a JSON dict from a markdown answer.

    Accepts either a fenced ```json ... ``` block or a raw JSON object
    in the text. Returns the parsed dict, or None on any failure.
    """
    if not raw:
        return None

    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    payload = m.group(1) if m else None

    if payload is None:
        start = raw.find("{")
        if start != -1:
            depth = 0
            for i in range(start, len(raw)):
                ch = raw[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        payload = raw[start:i + 1]
                        break

    if payload is None:
        return None

    try:
        return json.loads(payload)
    except Exception:
        pass

    try:
        fixed = re.sub(
            r'"((?:[^"\\]|\\.)*)"',
            lambda mm: '"' + mm.group(1).replace("\n", "\\n").replace("\r", "") + '"',
            payload,
            flags=re.DOTALL,
        )
        return json.loads(fixed)
    except Exception:
        return None


def parse_scorecard(ai_notes: dict | None) -> dict:
    """Pull verdict (str) and phase (int) out of ai_notes['Scorecard'].

    Returns {"verdict": str|None, "phase": int|None}. Never raises.
    """
    if not isinstance(ai_notes, dict):
        return {"verdict": None, "phase": None}

    raw = ai_notes.get("Scorecard")
    if not isinstance(raw, str):
        return {"verdict": None, "phase": None}

    parsed = parse_scorecard_json(raw)
    if not isinstance(parsed, dict):
        return {"verdict": None, "phase": None}

    verdict = parsed.get("verdict")
    if not isinstance(verdict, str):
        verdict = None

    phase_raw = parsed.get("phase")
    phase_num = None
    if isinstance(phase_raw, int):
        # compact form: {"phase": 3}
        phase_num = phase_raw
    elif isinstance(phase_raw, dict):
        # canonical form: {"phase": {"number": 3, ...}}
        n = phase_raw.get("number")
        if isinstance(n, int):
            phase_num = n
        elif isinstance(n, str) and n.isdigit():
            phase_num = int(n)
    elif isinstance(phase_raw, str) and phase_raw.isdigit():
        # very compact: {"phase": "3"}
        phase_num = int(phase_raw)

    return {"verdict": verdict, "phase": phase_num}


def resolve_verdict(cfg):
    """Single source of truth for a ticker's verdict + phase.

    The robustness table (cfg['robustness']['verdict_mapped']) is authoritative
    when present; otherwise fall back to the Scorecard section. Phase always
    comes from the Scorecard. Never raises.
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    sc = parse_scorecard(cfg.get("ai_notes"))
    rob = cfg.get("robustness")
    verdict = sc["verdict"]
    if isinstance(rob, dict) and rob.get("verdict_mapped"):
        verdict = rob["verdict_mapped"]
    return {"verdict": verdict, "phase": sc["phase"]}


# Brand forms that must survive title-casing intact (internal capitals/acronyms).
_COMPANY_KEEP = {"AT&T", "NVIDIA", "AECOM", "PNC", "KLA", "PTC", "DTE", "NXP"}
# Title-cased word -> corrected brand spelling.
_COMPANY_FIXUPS = {
    "Mercadolibre": "MercadoLibre",
    "Pepsico": "PepsiCo",
    "Abbvie": "AbbVie",
    "Powerschool": "PowerSchool",
    "Lvmh": "LVMH",
}
# Connector words rendered lowercase when not the first word.
_COMPANY_CONNECTORS = {"and", "of", "the", "for", "on", "in"}


def prettify_company_name(name):
    """Display-format an issuer name without mutating stored data.

    EDGAR returns names in all-caps (e.g. "TAIWAN SEMICONDUCTOR MANUFACTURING
    CO LTD"). Title-case only names that are predominantly uppercase; leave
    already-cased names (e.g. "AbbVie Inc.") untouched. Known brand acronyms
    are preserved and a few common mis-cased forms are fixed up. Never raises.
    """
    if not isinstance(name, str) or not name.strip():
        return name

    letters = [c for c in name if c.isalpha()]
    # Already mixed/lower case -> assume it's nicely formatted, leave as-is.
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.7:
        return name

    out = []
    for i, word in enumerate(name.split()):
        if word in _COMPANY_KEEP:
            out.append(word)
            continue
        tc = word.capitalize()
        tc = _COMPANY_FIXUPS.get(tc, tc)
        if i > 0 and tc.lower().strip(".,") in _COMPANY_CONNECTORS:
            tc = tc.lower()
        out.append(tc)
    return " ".join(out)
