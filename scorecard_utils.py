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

ROCE_CEILING = 100.0  # per-year ROCE cap (%) for CE≤0 / capital-light names

# Years of history the quality metric averages over. Owned by the helper, not
# by the caller: the detail page fetches 11 years for its Key Ratios tables
# (streamlit_app.py, since 2026-03-04) while the watchlist, the stored slice
# and the MCP fetch 10. When compute_roce_metric was later pointed at whichever
# `fund` happened to be in scope, the detail page silently averaged an extra
# year and the same ticker read 51.7% on its own page and 47.1% everywhere
# else. Pinning the window here makes the answer independent of how much
# history the caller happened to download.
ROCE_WINDOW_YEARS = 10


def window_start(fund, window=ROCE_WINDOW_YEARS):
    """First index of the trailing `window` years in `fund`'s series.

    0 when there is less history than the window. Series in a fundamentals
    dict are index-aligned and equal-length (gather_data pads with None), so
    one start index is valid for every key.
    """
    n = len(fund.get("years") or fund.get("operating_income") or [])
    return max(0, n - window)


def _at(fund, key, i):
    """Series element at year i, or 0.0 when the series or element is absent/None."""
    seq = fund.get(key) or []
    v = seq[i] if i < len(seq) else None
    return v if v is not None else 0.0


def non_operating_cash(fund, i):
    """The idle money at year i: cash + short-term investments.

    Short-term investments belong here, not just cash. A company parks its war
    chest wherever it earns most — MSFT holds $55.9bn in short-term investments
    against $20.9bn of cash, so stripping only "cash" would leave three
    quarters of the pile sitting in capital employed.

    Long-term investments are left in. They are a deliberate allocation of
    capital rather than a place to keep money that is between uses, and the
    question this metric asks is what the business earns on the capital it has
    chosen to commit.
    """
    return _at(fund, "cash", i) + _at(fund, "short_term_investments", i)


def capital_employed(fund, i):
    """(TA − CL) − cash − short-term investments at year i.

    Goodwill stays in. Stripping it was what blew this metric up in June 2026:
    an acquisitive name like AVGO carries ~60% goodwill, the denominator
    collapsed towards nothing and ROCE read 213%. Goodwill is also the part of
    the question worth asking — whether the acquisitions earn back what was
    paid for them.

    Cash comes out because EBIT sits above the financial result: the interest
    the cash earns is not in the numerator, so charging the denominator for it
    penalises the balance sheet without crediting the income. It also stops a
    quality gate from punishing prudence — every idle euro used to drag ROCE
    down, pushing cash-rich compounders like Hermès and Nintendo towards the
    20% bar for holding a buffer, which is strength rather than grounds for
    rejection.

    No netting against debt and no lease adjustment. An earlier basis subtracted
    max(0, marketables − debt), which made the answer depend on the shape of the
    liability side: MSFT's finance leases exceeded its marketables so nothing
    came off at all, while Hermès, with almost no debt, had its whole cash pile
    stripped. One formula, two behaviours, and the difference read as a bug.

    May be ≤ 0 when the cash exceeds TA − CL, which roce_for_year caps.
    """
    return (_at(fund, "total_assets", i)
            - _at(fund, "current_liabilities", i)
            - non_operating_cash(fund, i))


def roce_for_year(fund, i):
    """Per-year ROCE = EBIT / capital_employed, with a ceiling cap.

    Returns (pct, capped). (None, False) when EBIT/TA/CL are unavailable.
    CE ≤ 0 → the cash exceeds what the operation ties up → ceiling (a pass),
    year retained rather than dropped, because dropping it would take the most
    capital-light year out of the mean and penalise the best names.
    """
    oi_seq = fund.get("operating_income") or []
    ta_seq = fund.get("total_assets") or []
    cl_seq = fund.get("current_liabilities") or []
    oi_v = oi_seq[i] if i < len(oi_seq) else None
    ta_v = ta_seq[i] if i < len(ta_seq) else None
    cl_v = cl_seq[i] if i < len(cl_seq) else None
    if oi_v is None or ta_v is None or cl_v is None:
        return (None, False)
    ce = capital_employed(fund, i)
    if ce <= 0:
        return (ROCE_CEILING, True)
    pct = oi_v / ce * 100
    if pct > ROCE_CEILING:
        return (ROCE_CEILING, True)
    # No floor here. A single year keeps whatever it actually was, however
    # ugly: this is the series the chart draws and the record of what happened.
    # The clamp belongs on the mean — see compute_roce_metric.
    return (pct, False)


def compute_roce_metric(fund, cfg=None):
    """Single source of truth for the watchlist/detail/MCP quality metric.

    Returns ``(metric, avg_value)`` where ``metric`` is ``'ROCE'`` or ``'ROE'``:
      • ROCE = avg of EBIT / ((TA − CL) − cash − short-term investments),
        per roce_for_year, capped at ROCE_CEILING. Goodwill stays in the
        denominator (vault methodology, revised 2026-08-27).
      • ROE  = avg of Net Income / Total Equity.

    Two denominators, deliberately, and they must not be conflated:

      value    (TA − CL) − cash − short-term investments
      float test   (TA − CL) / TA, cash INCLUDED

    The float test decides whether ROCE means anything for this filer at all —
    whether current liabilities fund so much of the asset base that there is
    barely any capital employed to speak of. Cash is part of that asset base,
    so it belongs in that ratio. Run the test on the cash-stripped denominator
    instead and every cash-rich name drops under the 25% threshold and gets
    reclassified as a float business, which is precisely the misclassification
    the 2026-06-16 fix existed to end.

    Both averages are arithmetic means of the per-year values (not a pooled
    sum(EBIT)/sum(CE)) over the trailing ROCE_WINDOW_YEARS years, regardless
    of how many years the caller fetched.

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
    start = window_start(fund)

    roce_pcts, ce_ta_ratios = [], []
    for i in range(start, n):
        oi_v = oi_w[i] if i < len(oi_w) else None
        ta_v = ta_w[i] if i < len(ta_w) else None
        cl_v = cl_w[i] if i < len(cl_w) else None
        # Float test on TA − CL with the cash still in it. See the docstring:
        # stripping cash here would flip every cash-rich name to ROE.
        if oi_v is not None and ta_v and ta_v > 0 and cl_v is not None:
            ce_orig = ta_v - cl_v
            ce_ta_ratios.append(max(ce_orig, 0) / ta_v)
        # ROCE value uses the excess-liquidity-adjusted CE, with ceiling cap.
        pct, _capped = roce_for_year(fund, i)
        if pct is not None:
            roce_pcts.append(pct)

    roe_pcts = []
    for i in range(start, n):
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

    # Floor the headline figure, not the years behind it. Stripping cash can
    # leave a loss-making company with a sliver of capital employed, and one
    # such year drags the mean somewhere meaningless: SE's worst year was
    # −32,967% and its ten-year average came out at −3,314%. The band is the
    # same either way — fragile is fragile — but this number is read by people,
    # and one that absurd reads as a broken metric rather than a bad business.
    #
    # ROCE only. A deeply negative ROE means a real loss against real equity,
    # with no shrinking denominator behind it to distrust.
    if metric == "ROCE" and avg_value is not None:
        avg_value = max(avg_value, -ROCE_CEILING)
    return metric, avg_value


# Everything the watchlist row computes from EDGAR, and nothing else. The page
# needs three numbers per ticker — FCF yield, the quality metric and its
# average — and was downloading a ~5 MB companyfacts file per name to get
# them: 380 MB and 13 seconds for a 77-name list, repeated every time Streamlit
# Cloud restarted and emptied the in-memory cache.
#
# The slice stores the INPUTS, not the answers. FCF yield divides by the live
# price and compute_roce_metric reads overrides out of the config, so a stored
# answer would be stale the moment the price moved or an override changed.
# Storing the series keeps every calculation exactly where it was.
WATCHLIST_FUND_KEYS = (
    # De munt van de cijfers: een IFRS-filer zonder dollarvertaling (Ferrari)
    # levert EUR, en wie deelt door een dollarkoers moet dat weten.
    "currency",
    "years",
    # compute_roce_metric
    "operating_income", "total_assets", "current_liabilities",
    "net_income", "total_equity",
    # capital_employed, via roce_for_year. Debt, leases and long-term
    # investments no longer enter the denominator; slices stored before
    # 2026-08-27 still carry them, harmlessly, because nothing reads them.
    "cash", "short_term_investments",
    # trailing FCF yield. shares_latest is the cover-page count from the most
    # recent filing — the one on the same basis as the price, and the only one
    # that survives a split between annual reports.
    "fcf", "shares", "shares_latest",
)


def slim_fundamentals(fund: dict | None) -> dict | None:
    """The watchlist-relevant slice of a fundamentals dict, or None.

    Roughly a kilobyte per ticker instead of five megabytes. Absent keys stay
    absent rather than becoming empty lists: the difference between "the filer
    reports no debt" and "we never asked" is one this codebase has paid for
    before, and an empty list here would read as the former.
    """
    if not fund:
        return None
    out = {k: fund[k] for k in WATCHLIST_FUND_KEYS if fund.get(k)}
    return out or None


def slice_is_usable(sl: dict | None) -> bool:
    """Whether a stored slice can stand in for a live fetch.

    A slice without years is not a shorter answer, it is no answer — treating
    it as one would blank a ticker's metrics while looking like data.
    """
    return bool(sl) and bool(sl.get("years"))


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
