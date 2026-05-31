"""
Watchlist config storage — Supabase with per-user RLS isolation.

All public functions take an authenticated Supabase client as the first
parameter.  Row Level Security on the database handles user isolation
automatically, but we include user_id explicitly in inserts/upserts so
the RLS WITH CHECK clause is satisfied.
"""

import logging
from datetime import UTC

logger = logging.getLogger(__name__)


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
)

# User-intent guarded keys: empty value is a legitimate user action
# (removing the last peer, reverting to default lens weights, clearing
# all SOTP segments). Only trigger DB recovery when the key is entirely
# missing — never when the caller explicitly passes []/{}.
_GUARDED_KEYS_RESTORE_MISSING_ONLY = (
    "peers",
    "lens_weights",
    "sotp",
)

# Backward-compat alias — the union of both sets, used by callers that
# only need to know "is this key guarded at all?".
_AI_NOTES_GUARDED_KEYS = _GUARDED_KEYS_RESTORE_EMPTY + _GUARDED_KEYS_RESTORE_MISSING_ONLY


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

    needs_recovery = [
        k for k in _GUARDED_KEYS_RESTORE_EMPTY
        if k not in cfg or _is_empty(cfg[k])
    ] + [
        k for k in _GUARDED_KEYS_RESTORE_MISSING_ONLY
        if k not in cfg
    ]
    if needs_recovery:
        existing = load_config(client, ticker, user_id=user_id)
        if existing:
            preserved = []
            for k in needs_recovery:
                # Only restore from DB when the DB value is itself non-empty;
                # never replace a meaningful new value with a stale one.
                if k in existing and not _is_empty(existing[k]):
                    cfg = dict(cfg)
                    cfg[k] = existing[k]
                    preserved.append(k)
            if preserved:
                import traceback
                stack = "".join(traceback.format_stack(limit=6)[:-1])
                logger.warning(
                    "save_config(%s): preserved %s from DB (caller passed missing/empty).\n"
                    "Call stack:\n%s",
                    ticker, preserved, stack,
                )

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
        raise
    if resp and resp.data:
        return _restore_tuples(resp.data["config"])
    return None


def list_watchlist(client, user_id=None):
    """Return list of dicts with ticker metadata + valuation summary.

    Each entry has these keys (always present; values may be None):
        ticker, company, updated, stock_price,
        fv_low, fv_mid, fv_high, buy_price, current_vs_mid, lens_count,
        verdict, phase

    Configs without ``valuation_summary`` show only base fields populated;
    run ``calculate_multi_lens_valuation`` to populate the rest.
    """
    from scorecard_utils import parse_scorecard

    query = (
        client.table("watchlist_configs")
        .select("ticker, company, stock_price, updated_at, config")
    )
    if user_id is not None:
        query = query.eq("user_id", user_id)
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
        cfg = row.get("config") or {}
        summary = cfg.get("valuation_summary") or {}
        lenses = summary.get("lenses") or {}
        lens_count = sum(1 for k in _COUNTED_LENSES if lenses.get(k) is not None)

        scorecard = parse_scorecard(cfg.get("ai_notes"))

        out.append({
            "ticker": row["ticker"],
            "company": row.get("company", row["ticker"]),
            "updated": row.get("updated_at", ""),
            "stock_price": row.get("stock_price", 0),
            "fv_low":  summary.get("weighted_fv_low"),
            "fv_mid":  summary.get("weighted_fv_mid"),
            "fv_high": summary.get("weighted_fv_high"),
            "buy_price": summary.get("buy_price"),
            "current_vs_mid": summary.get("current_vs_mid"),
            "lens_count": lens_count,
            "verdict": scorecard["verdict"],
            "phase":   scorecard["phase"],
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


def load_credential(client, service_name):
    """Load a stored credential. Returns the credential string or None."""
    try:
        resp = (
            client.table("user_credentials")
            .select("credential")
            .eq("service_name", service_name)
            .maybe_single()
            .execute()
        )
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


def load_ibkr_credentials(client):
    """Load all IBKR credentials. Returns dict or None if not connected."""
    result = {}
    for key in IBKR_CREDENTIAL_KEYS:
        val = load_credential(client, key)
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
