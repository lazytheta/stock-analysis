"""
Watchlist config storage — Supabase with per-user RLS isolation.

All public functions take an authenticated Supabase client as the first
parameter.  Row Level Security on the database handles user isolation
automatically, but we include user_id explicitly in inserts/upserts so
the RLS WITH CHECK clause is satisfied.
"""

import logging

import streamlit as st

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_id(client):
    """Get user_id, cached per-request via session_state."""
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

def save_config(client, ticker, cfg):
    """Upsert a DCF config dict to Supabase."""
    from datetime import datetime, timezone

    ticker = ticker.upper()
    data = _prepare_for_json(cfg)
    user_id = _get_user_id(client)

    row = {
        "user_id": user_id,
        "ticker": ticker,
        "company": cfg.get('company', ticker),
        "stock_price": cfg.get('stock_price', 0),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "config": data,
    }
    client.table("watchlist_configs").upsert(row).execute()


def load_config(client, ticker):
    """Load a DCF config dict. Returns dict or None."""
    ticker = ticker.upper()
    resp = (
        client.table("watchlist_configs")
        .select("config")
        .eq("ticker", ticker)
        .single()
        .execute()
    )
    if resp and resp.data:
        return _restore_tuples(resp.data["config"])
    return None


def list_watchlist(client):
    """Return list of dicts with ticker metadata.

    Each entry: {ticker, company, updated, stock_price}
    RLS automatically scopes to the current user.
    """
    resp = (
        client.table("watchlist_configs")
        .select("ticker, company, stock_price, updated_at")
        .execute()
    )
    if resp and resp.data:
        return [
            {
                'ticker': row['ticker'],
                'company': row.get('company', row['ticker']),
                'updated': row.get('updated_at', ''),
                'stock_price': row.get('stock_price', 0),
            }
            for row in resp.data
        ]
    return []


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


def load_user_prefs(client):
    """Load user wheel preferences from Supabase. Returns dict with defaults for missing keys."""
    prefs = dict(_DEFAULT_PREFS)
    try:
        resp = (
            client.table("user_prefs")
            .select("prefs")
            .single()
            .execute()
        )
        if resp and resp.data and resp.data.get("prefs"):
            prefs.update(resp.data["prefs"])
    except Exception as e:
        logger.debug("user_prefs read failed (may not exist yet): %s", e)
    return prefs


def save_user_prefs(client, prefs):
    """Save user wheel preferences to Supabase (upsert)."""
    from datetime import datetime, timezone

    user_id = _get_user_id(client)
    try:
        client.table("user_prefs").upsert({
            "user_id": user_id,
            "prefs": prefs,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.warning("Failed to save user prefs: %s", e)


# ---------------------------------------------------------------------------
# User credentials (Tastytrade refresh tokens, etc.)
# ---------------------------------------------------------------------------

def save_credential(client, service_name, value):
    """Upsert a credential (e.g. Tastytrade refresh token) for the current user."""
    from datetime import datetime, timezone

    user_id = _get_user_id(client)
    client.table("user_credentials").upsert({
        "user_id": user_id,
        "service_name": service_name,
        "credential": value,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def load_credential(client, service_name):
    """Load a stored credential. Returns the credential string or None."""
    try:
        resp = (
            client.table("user_credentials")
            .select("credential")
            .eq("service_name", service_name)
            .single()
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
    "ibkr_consumer_key",
    "ibkr_access_token",
    "ibkr_access_token_secret",
    "ibkr_encryption_key",
    "ibkr_signing_key",
]


def save_ibkr_credentials(client, creds):
    """Save all IBKR credentials. creds is a dict with keys matching IBKR_CREDENTIAL_KEYS."""
    for key in IBKR_CREDENTIAL_KEYS:
        if key in creds and creds[key]:
            save_credential(client, key, creds[key])


def load_ibkr_credentials(client):
    """Load all IBKR credentials. Returns dict or None if not connected."""
    result = {}
    for key in IBKR_CREDENTIAL_KEYS:
        val = load_credential(client, key)
        if val:
            result[key] = val
    if "ibkr_consumer_key" in result and "ibkr_access_token" in result:
        return result
    return None


def delete_ibkr_credentials(client):
    """Delete all IBKR credentials."""
    for key in IBKR_CREDENTIAL_KEYS:
        try:
            delete_credential(client, key)
        except Exception:
            pass
