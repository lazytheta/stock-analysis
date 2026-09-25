"""
Watchlist config storage — Supabase with per-user RLS isolation.

All public functions take an authenticated Supabase client as the first
parameter.  Row Level Security on the database handles user isolation
automatically, but we include user_id explicitly in inserts/upserts so
the RLS WITH CHECK clause is satisfied.
"""

import logging
import time
from datetime import UTC

logger = logging.getLogger(__name__)

# Transport-level blips (a dropped keep-alive connection surfacing as
# httpx.RemoteProtocolError, connection resets, timeouts) are transient and
# worth retrying — they should not crash a watchlist that loads ~25 configs in
# parallel. Matched by class name OR message so we stay httpx-version- and
# import-free here.
_TRANSIENT_MARKERS = (
    "RemoteProtocolError", "ConnectError", "ConnectTimeout", "ReadTimeout",
    "ReadError", "WriteError", "PoolTimeout", "ConnectionError",
    "Server disconnected", "Connection reset", "connection was closed",
)


def _is_transient(exc):
    blob = f"{type(exc).__name__}: {exc}"
    return any(m in blob for m in _TRANSIENT_MARKERS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_id(client):
    """Get user_id, cached per-request via session_state."""
    import streamlit as st
    if "_user_id" not in st.session_state:
        st.session_state["_user_id"] = str(client.auth.get_user().user.id)
    return st.session_state["_user_id"]


def _prepare_for_json(obj):
    """Convert tuples to lists recursively for JSON serialization."""
    if isinstance(obj, dict):
        return {k: _prepare_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_prepare_for_json(item) for item in obj]
    return obj


def _restore_tuples(cfg):
    """Convert lists back to tuples for sector_betas and debt_breakdown."""
    if 'sector_betas' in cfg:
        cfg['sector_betas'] = [tuple(item) for item in cfg['sector_betas']]
    if 'debt_breakdown' in cfg:
        cfg['debt_breakdown'] = [tuple(item) for item in cfg['debt_breakdown']]
    return cfg


# ---------------------------------------------------------------------------
# Watchlist config CRUD
# ---------------------------------------------------------------------------

# Compute-only guarded keys: populated by code paths, never legitimately
# emptied by user intent. Empty/null here = caller bug → restore from DB.
# (Caught the AI-Research-Section wipe and the MSFT valuation_summary=None
# incident.)
_GUARDED_KEYS_RESTORE_EMPTY = (
    "ai_notes",
    "valuation_inputs",
    "valuation_summary",
    "robustness",
    # The cached EDGAR slice the watchlist renders from. Losing it is not
    # data loss — it refetches — but the refetch costs a 5 MB companyfacts
    # download per ticker, so a caller passing an empty one silently makes
    # the page slow again.
    "fund_slice",
)

# User-intent guarded keys: empty value is a legitimate user action
# (removing the last peer, reverting to default lens weights, clearing
# all SOTP segments). Only trigger DB recovery when the key is entirely
# missing — never when the caller explicitly passes []/{}.
_GUARDED_KEYS_RESTORE_MISSING_ONLY = (
    "peers",
    "lens_weights",
)

# Backward-compat alias — the union of both sets, used by callers that
# only need to know "is this key guarded at all?".
_AI_NOTES_GUARDED_KEYS = _GUARDED_KEYS_RESTORE_EMPTY + _GUARDED_KEYS_RESTORE_MISSING_ONLY

# Keys a caller deletes by leaving them out. Everything else is merged: a
# partial save keeps whatever it didn't mention, because every config field is
# a deliberate input and dropping forty of them to change two is never intended.
# These three are the exception — derived caches and overrides whose *absence*
# is the signal:
#   wacc_per_year / terminal_wacc  the DCF editor persists them only when
#       manually overridden and pops them otherwise, so they fall back to a
#       live compute. Merging them back resurrects the frozen-WACC drift fixed
#       in 2026-07 (a stale rate surviving an rf/ERP change).
#   roce_metric_override           cleared to fall back to the computed metric.
_DELETABLE_BY_OMISSION = (
    "wacc_per_year",
    "terminal_wacc",
    "roce_metric_override",
)


# ---------------------------------------------------------------------------
# Assumption log
# ---------------------------------------------------------------------------

ASSUMPTION_LOG_KEY = "assumption_log"

# Enough to cover years of revisions at a handful a year, and small enough that
# the log stays a rounding error next to ai_notes.
ASSUMPTION_LOG_MAX = 60

# The fields that constitute a thesis. A change in any of them is a revision
# worth recording; anything else about the config is not.
_ASSUMPTION_FIELDS = (
    "base_year", "base_revenue", "base_op_margin",
    "revenue_growth", "op_margins", "terminal_growth", "terminal_margin",
)


def append_assumption_snapshot(stored_cfg, new_cfg, today=None):
    """Return the assumption log with the incoming thesis appended if it changed.

    Rebuilding a DCF overwrites base_year and the growth path, so what you
    assumed a year ago is gone the moment you revise — and with it any way to
    ask whether the business delivered. This keeps a dated copy of each thesis
    as it is set.

    Appends only on a real revision. save_config also runs on every valuation
    refresh, and fair value drifts with the risk-free rate and the share price
    without anyone changing their mind; logging that would bury the handful of
    genuine rethinks under hundreds of rows. fv_mid rides along on each entry
    so the log can also show whether your own valuation creeps up to meet the
    price, but it never triggers one.
    """
    from datetime import date

    snapshot = {k: new_cfg.get(k) for k in _ASSUMPTION_FIELDS}
    if all(v is None for v in snapshot.values()):
        return list(stored_cfg.get(ASSUMPTION_LOG_KEY) or []) \
            if isinstance(stored_cfg.get(ASSUMPTION_LOG_KEY), list) else []

    log = stored_cfg.get(ASSUMPTION_LOG_KEY)
    if not isinstance(log, list):
        # A save that raises loses the user's actual edit, and the log is the
        # least important thing in the config.
        log = []
    log = list(log)

    if log and all(log[-1].get(k) == snapshot[k] for k in _ASSUMPTION_FIELDS):
        return log

    snapshot["as_of"] = today or date.today().isoformat()
    snapshot["fv_mid"] = ((new_cfg.get("valuation_summary") or {})
                          .get("weighted_fv_mid"))

    # Revisions on the same day collapse to the last one. Dragging the growth
    # path in the editor and saving between tweaks is tuning, not a sequence of
    # theses; only the version settled on is one.
    if log and log[-1].get("as_of") == snapshot["as_of"]:
        log[-1] = snapshot
    else:
        log.append(snapshot)
    return log[-ASSUMPTION_LOG_MAX:]


def save_config(client, ticker, cfg, user_id=None):
    """Upsert a DCF config dict to Supabase.

    Defends against silent data loss for guarded keys, with two policies:

    - **Compute-only keys** (`ai_notes`, `valuation_inputs`,
      `valuation_summary`): missing key OR empty/null value → restore
      from DB. These are populated by code paths; an empty value is
      almost always a caller bug. Caught the AI-Research-Section wipe
      and the MSFT `valuation_summary: None` incident.

    - **User-intent keys** (`peers`, `lens_weights`): only missing key
      → restore from DB. Empty list / empty dict is a legitimate user
      action (remove the last peer; revert to default weights) and must
      be persisted as-is. Caught the Disney "Ginny" peer that kept
      reappearing because the guard treated `peers: []` as caller-forgot.

    Both paths log a WARNING with a short call-stack so the offending caller
    can be identified.
    """
    from datetime import datetime

    ticker = ticker.upper()
    if user_id is None:
        user_id = _get_user_id(client)

    def _is_empty(v):
        # None, empty dict, empty list, empty str → treat as "absent" for
        # guard purposes. Numeric 0 / False are not relevant to guarded keys.
        return v is None or (isinstance(v, (dict, list, str)) and len(v) == 0)

    # Every absent key is a candidate for restore, not just a hand-picked few:
    # a save that omits a field means "I didn't touch this", not "delete it".
    # The guarded-empty keys additionally recover when present but empty, since
    # for those an empty value is almost always a caller bug rather than intent.
    # Geen try/except hier. load_config meldt "geen rij" zelf al als None
    # (PGRST116 / "0 rows") en retryt transiënte fouten; wat er dan nog uit
    # komt is een echte storing. Die werd hier tot 2026-09-11 platgeslagen
    # tot existing = None, waarna de merge werd overgeslagen en upsert de
    # hele config verving door de paar velden die de aanroeper meestuurde:
    # één Supabase-hik tijdens een deel-save wiste veertig velden, met
    # alleen "treating as new ticker" in de log. Een save die faalt is
    # herstelbaar; een save die stil afknipt niet.
    existing = load_config(client, ticker, user_id=user_id)

    if existing:
        cfg = dict(cfg)
        preserved, restored_empty = [], []
        for k, v in existing.items():
            if k in _DELETABLE_BY_OMISSION:
                continue                       # absence is the signal
            if k not in cfg:
                cfg[k] = v                     # caller didn't touch it
                preserved.append(k)
            elif k in _GUARDED_KEYS_RESTORE_EMPTY and _is_empty(cfg[k]) \
                    and not _is_empty(v):
                cfg[k] = v                     # empty here is a caller bug
                restored_empty.append(k)
        if restored_empty:
            import traceback
            stack = "".join(traceback.format_stack(limit=6)[:-1])
            logger.warning(
                "save_config(%s): restored %s from DB (caller passed empty).\n"
                "Call stack:\n%s", ticker, restored_empty, stack,
            )
        if preserved:
            logger.info("save_config(%s): merged %d untouched key(s) from DB: %s",
                        ticker, len(preserved), preserved)

    # Record the thesis as it is being written. Here rather than in the app or
    # the MCP: save_config is the one door every write goes through, so no
    # caller can revise a growth path without the previous one being kept.
    try:
        _log = append_assumption_snapshot(existing or {}, cfg)
        if _log:
            cfg[ASSUMPTION_LOG_KEY] = _log
    except Exception as _e:
        # Never let bookkeeping cost the user their actual edit.
        logger.warning("save_config(%s): assumption log skipped: %s", ticker, _e)

    data = _prepare_for_json(cfg)

    row = {
        "user_id": user_id,
        "ticker": ticker,
        "company": cfg.get('company', ticker),
        "stock_price": cfg.get('stock_price', 0),
        "updated_at": datetime.now(UTC).isoformat(),
        "config": data,
    }
    client.table("watchlist_configs").upsert(row).execute()


def load_config(client, ticker, user_id=None):
    """Load a DCF config dict. Returns dict or None.

    PostgREST raises APIError PGRST116 ("0 rows") when the row doesn't
    exist yet — happens for brand-new tickers being saved for the first
    time (save_config calls us defensively for guarded-keys restore).
    Older postgrest-py versions raise this even from .maybe_single().
    We match by error STRING (not attribute) so this fix is robust
    across postgrest-py releases without dependency on which fields
    APIError exposes.
    """
    ticker = ticker.upper()
    last_exc = None
    for attempt in range(3):
        query = (
            client.table("watchlist_configs")
            .select("config")
            .eq("ticker", ticker)
        )
        if user_id is not None:
            query = query.eq("user_id", user_id)
        try:
            resp = query.maybe_single().execute()
        except Exception as e:
            if "PGRST116" in str(e) or "0 rows" in str(e):
                return None
            # Retry transient transport blips before giving up so one dropped
            # connection doesn't crash the whole (parallel) watchlist load.
            last_exc = e
            if attempt < 2 and _is_transient(e):
                logger.warning("load_config(%s): transient error, retry %d/2: %s",
                               ticker, attempt + 1, e)
                time.sleep(0.25 * (attempt + 1))
                continue
            raise
        # Een rij zonder config is geen config. maybe_single() geeft bij nul
        # rijen data=None; een rij met config=NULL zou corrupt zijn en mag
        # als "niets opgeslagen" lezen, zodat een save hem gewoon vult.
        if resp and resp.data and resp.data.get("config") is not None:
            return _restore_tuples(resp.data["config"])
        return None
    raise last_exc  # unreachable: loop always returns or raises


def load_all_configs(client, user_id=None, include_ai_notes=True):
    """Every config for a user in one round-trip: {TICKER: cfg}.

    The watchlist page used to call load_config once per ticker — 64 separate
    Supabase requests, six at a time, for 2.5 MB that a single query returns.
    The payload was never the problem; 64 round-trips of latency were, and the
    page repeats that every 30 seconds as the cache expires.

    Shapes each config exactly as load_config does (tuples restored), so the
    two are interchangeable.

    ``include_ai_notes=False`` reads a view with that one key stripped: it is
    79% of the payload (2.5 MB of 3.1 MB across a 77-name list) and the row
    rendering reads it zero times. Only for display paths — a config loaded
    this way must never be handed to save_config as if it were complete.
    save_config restores keys the caller omits, so it would survive, but
    relying on that to cover a knowingly partial read is how the prescan
    sections got wiped once already.
    """
    source = "watchlist_configs" if include_ai_notes else "watchlist_configs_no_notes"
    query = client.table(source).select("ticker, config")
    if user_id is not None:
        query = query.eq("user_id", user_id)
    resp = query.execute()
    rows = (resp.data if resp else None) or []
    return {
        row["ticker"].upper(): _restore_tuples(row["config"])
        for row in rows if row.get("ticker") and row.get("config")
    }


def list_watchlist(client, user_id=None, tickers=None):
    """Return list of dicts with ticker metadata + valuation summary.

    Each entry has these keys (always present; values may be None):
        ticker, company, updated, stock_price,
        fv_low, fv_mid, fv_high, buy_price, current_vs_mid, lens_count,
        verdict, phase

    Configs without ``valuation_summary`` show only base fields populated;
    run ``calculate_multi_lens_valuation`` to populate the rest.
    """
    from scorecard_utils import resolve_verdict

    # Select only what the listing renders. ai_notes is ~80% of the config
    # payload (2.0 MB of 2.5 MB across a 64-name watchlist) and exactly one of
    # its keys is read here — the Scorecard, for the verdict and phase — so
    # pulling the whole column dragged every prescan section into a query that
    # runs on page load. 2.5 MB → ~300 kB.
    query = (
        client.table("watchlist_configs")
        .select("ticker, company, stock_price, updated_at, "
                "config->valuation_summary, config->robustness, "
                "config->isin, config->quote_venue, "
                "config->category, config->dcf_placeholder, "
                "config->promoted_by, config->promoted_at, "
                "config->ai_notes->Scorecard")
    )
    if user_id is not None:
        query = query.eq("user_id", user_id)
    # `tickers` narrows the query to the names the caller will actually render.
    # The Portfolio page needs a fair value for ten holdings and was pulling
    # all eighty-one rows to find them. An empty list is not "no filter": it
    # means the caller has nothing to look up, so answer that without a query.
    if tickers is not None:
        tickers = [t.upper() for t in tickers]
        if not tickers:
            return []
        query = query.in_("ticker", tickers)
    resp = query.execute()
    if not (resp and resp.data):
        return []

    # Forward-looking lenses counted in the watchlist row's "{N} lenses"
    # display. Single source of truth in valuation_lenses.FORWARD_LENS_KEYS.
    # reverse_dcf is computed and stored but excluded — it anchors at current
    # price (see 2026-05-07 reverse-dcf-demote spec).
    from valuation_lenses import FORWARD_LENS_KEYS
    _COUNTED_LENSES = FORWARD_LENS_KEYS

    out = []
    for row in resp.data:
        summary = row.get("valuation_summary") or {}
        lenses = summary.get("lenses") or {}
        lens_count = sum(1 for k in _COUNTED_LENSES if lenses.get(k) is not None)

        # resolve_verdict reads cfg["ai_notes"]["Scorecard"] and
        # cfg["robustness"]; rebuild just that shape from the narrow select.
        _vp = resolve_verdict({
            "ai_notes": {"Scorecard": row.get("Scorecard")} if row.get("Scorecard") else None,
            "robustness": row.get("robustness"),
        })

        out.append({
            "ticker": row["ticker"],
            "company": row.get("company", row["ticker"]),
            "updated": row.get("updated_at", ""),
            "stock_price": row.get("stock_price", 0),
            # De beurs kent geen tickers. Zonder isin is er voor deze naam geen
            # tweede koersbron, en dat moet de aanroeper kunnen zien.
            "isin": row.get("isin"),
            "quote_venue": row.get("quote_venue"),
            "fv_low":  summary.get("weighted_fv_low"),
            "fv_mid":  summary.get("weighted_fv_mid"),
            "fv_high": summary.get("weighted_fv_high"),
            "buy_price": summary.get("buy_price"),
            "current_vs_mid": summary.get("current_vs_mid"),
            "lens_count": lens_count,
            "verdict": _vp["verdict"],
            "phase":   _vp["phase"],
            "category": row.get("category") or "Uncategorized",
            "dcf_placeholder": bool(row.get("dcf_placeholder")),
            "promoted_by": row.get("promoted_by"),
            "promoted_at": row.get("promoted_at"),
        })
    return out


def remove_from_watchlist(client, ticker):
    """Remove a ticker from the user's watchlist."""
    ticker = ticker.upper()
    client.table("watchlist_configs").delete().eq("ticker", ticker).execute()


# ---------------------------------------------------------------------------
# User preferences (wheel strategy settings, stored in Supabase)
# ---------------------------------------------------------------------------

_DEFAULT_PREFS = {
    "delta_min": 0.20,
    "delta_max": 0.35,
    "dte_min": 25,
    "dte_max": 45,
}


def load_user_prefs(client, user_id=None):
    """Load user wheel preferences from Supabase. Returns dict with defaults for missing keys.

    user_id is optional — when provided (e.g. from the MCP server with a
    service-role key), the row is filtered explicitly. When None we rely on
    RLS to scope to the authenticated user (Streamlit context)."""
    prefs = dict(_DEFAULT_PREFS)
    try:
        query = client.table("user_prefs").select("prefs")
        if user_id is not None:
            query = query.eq("user_id", user_id)
        resp = query.maybe_single().execute()
        if resp and resp.data and resp.data.get("prefs"):
            prefs.update(resp.data["prefs"])
    except Exception as e:
        logger.debug("user_prefs read failed (may not exist yet): %s", e)
    return prefs


def save_user_prefs(client, prefs, user_id=None):
    """Save user wheel preferences to Supabase (upsert)."""
    from datetime import datetime

    if user_id is None:
        user_id = _get_user_id(client)
    try:
        client.table("user_prefs").upsert({
            "user_id": user_id,
            "prefs": prefs,
            "updated_at": datetime.now(UTC).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning("Failed to save user prefs: %s", e)


# ---------------------------------------------------------------------------
# User credentials (Tastytrade refresh tokens, etc.)
# ---------------------------------------------------------------------------

def save_credential(client, service_name, value):
    """Upsert a credential (e.g. Tastytrade refresh token) for the current user."""
    from datetime import datetime

    user_id = _get_user_id(client)
    client.table("user_credentials").upsert({
        "user_id": user_id,
        "service_name": service_name,
        "credential": value,
        "updated_at": datetime.now(UTC).isoformat(),
    }).execute()


def load_credential(client, service_name, user_id=None):
    """Load a stored credential. Returns the credential string or None.

    user_id is optional but load-bearing off Streamlit. The app passes a
    user-scoped client and RLS narrows the query for it; the MCP server runs
    with the service-role key, which bypasses RLS entirely. Without an explicit
    filter that query matches every user's row, and maybe_single() then raises
    rather than handing one back — which is the right failure, but only because
    the filter is there to be forgotten once.
    """
    try:
        query = (
            client.table("user_credentials")
            .select("credential")
            .eq("service_name", service_name)
        )
        if user_id is not None:
            query = query.eq("user_id", user_id)
        resp = query.maybe_single().execute()
        if resp and resp.data:
            return resp.data["credential"]
    except Exception as e:
        logger.debug("credential read failed for %s: %s", service_name, e)
    return None


def delete_credential(client, service_name):
    """Delete a stored credential."""
    client.table("user_credentials").delete().eq("service_name", service_name).execute()


# ---------------------------------------------------------------------------
# IBKR credential bundle
# ---------------------------------------------------------------------------

IBKR_CREDENTIAL_KEYS = [
    "ibkr_flex_token",
    "ibkr_flex_query_id",
]


def save_ibkr_credentials(client, creds):
    """Save all IBKR credentials. creds is a dict with keys matching IBKR_CREDENTIAL_KEYS."""
    for key in IBKR_CREDENTIAL_KEYS:
        if creds.get(key):
            save_credential(client, key, creds[key])


def load_ibkr_credentials(client, user_id=None):
    """Load all IBKR credentials. Returns dict or None if not connected."""
    result = {}
    for key in IBKR_CREDENTIAL_KEYS:
        val = load_credential(client, key, user_id=user_id)
        if val:
            result[key] = val
    if "ibkr_flex_token" in result and "ibkr_flex_query_id" in result:
        return result
    return None


def delete_ibkr_credentials(client):
    """Delete all IBKR credentials."""
    for key in IBKR_CREDENTIAL_KEYS:
        try:
            delete_credential(client, key)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Trading 212 credential bundle
# ---------------------------------------------------------------------------

T212_CREDENTIAL_KEYS = [
    "t212_api_key",
    "t212_api_secret",
]


def save_t212_credentials(client, creds):
    """Save all T212 credentials. creds is a dict with keys matching T212_CREDENTIAL_KEYS."""
    for key in T212_CREDENTIAL_KEYS:
        if creds.get(key):
            save_credential(client, key, creds[key])


def load_t212_credentials(client, user_id=None):
    """Load all T212 credentials, or None when the pair is incomplete.

    Basic auth needs both halves; half a pair would fail later as a confusing
    401 rather than "not connected".
    """
    result = {}
    for key in T212_CREDENTIAL_KEYS:
        val = load_credential(client, key, user_id=user_id)
        if val:
            result[key] = val
    if "t212_api_key" in result and "t212_api_secret" in result:
        return result
    return None


def delete_t212_credentials(client):
    """Delete all T212 credentials."""
    for key in T212_CREDENTIAL_KEYS:
        try:
            delete_credential(client, key)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Page view analytics
# ---------------------------------------------------------------------------

def log_page_view(client, page_name):
    """Log a page view. Silently ignores errors."""
    try:
        user_id = _get_user_id(client)
        client.table("page_views").insert({
            "user_id": user_id,
            "page": page_name,
        }).execute()
    except Exception as e:
        logger.warning("page_view insert failed: %s", e)
