"""
Watchlist config storage — Supabase + local JSON persistence for DCF configs.

Stores each ticker's config as configs/watchlist/TICKER.json locally,
with Supabase as the primary remote store for persistence across deploys.

Reads:  Supabase first, fallback to local file.
Writes: always local + best-effort Supabase.
Without Supabase secrets: works on local files only.
"""

import json
import os
from datetime import datetime, timezone

WATCHLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs", "watchlist")

_supabase_client = None


def _get_secret(name):
    """Read a secret from Streamlit secrets or environment variables."""
    try:
        import streamlit as st
        val = st.secrets.get(name)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(name)


def _get_client():
    """Return a lazy-initialized Supabase client, or None if secrets are missing."""
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_ANON_KEY")
    if not url or not key:
        return None

    try:
        from supabase import create_client
        _supabase_client = create_client(url, key)
        return _supabase_client
    except Exception:
        return None


def _supabase_safe(fn):
    """Call fn() and return its result, or None on any error."""
    try:
        return fn()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Local file helpers (unchanged)
# ---------------------------------------------------------------------------

def _ensure_dir():
    os.makedirs(WATCHLIST_DIR, exist_ok=True)


def _index_path():
    return os.path.join(WATCHLIST_DIR, "_index.json")


def _ticker_path(ticker):
    return os.path.join(WATCHLIST_DIR, f"{ticker.upper()}.json")


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


def _load_index():
    path = _index_path()
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {}


def _save_index(index):
    _ensure_dir()
    with open(_index_path(), 'w') as f:
        json.dump(index, f, indent=2)


# ---------------------------------------------------------------------------
# Public API (same signatures as before)
# ---------------------------------------------------------------------------

def save_config(ticker, cfg):
    """Save a DCF config dict to local JSON + Supabase upsert."""
    ticker = ticker.upper()
    _ensure_dir()

    data = _prepare_for_json(cfg)

    # Always write locally
    with open(_ticker_path(ticker), 'w') as f:
        json.dump(data, f, indent=2)

    index = _load_index()
    index[ticker] = {
        'company': cfg.get('company', ticker),
        'updated': datetime.now().isoformat(),
        'stock_price': cfg.get('stock_price', 0),
    }
    _save_index(index)

    # Best-effort Supabase upsert
    client = _get_client()
    if client:
        row = {
            "ticker": ticker,
            "company": cfg.get('company', ticker),
            "stock_price": cfg.get('stock_price', 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "config": data,
        }
        _supabase_safe(lambda: client.table("watchlist_configs").upsert(row).execute())


def load_config(ticker):
    """Load a DCF config dict. Supabase first, fallback to local file. Returns dict or None."""
    ticker = ticker.upper()

    # Try Supabase first
    client = _get_client()
    if client:
        resp = _supabase_safe(
            lambda: client.table("watchlist_configs")
            .select("config")
            .eq("ticker", ticker)
            .single()
            .execute()
        )
        if resp and resp.data:
            cfg = resp.data["config"]
            return _restore_tuples(cfg)

    # Fallback to local file
    path = _ticker_path(ticker)
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        cfg = json.load(f)
    return _restore_tuples(cfg)


def list_watchlist():
    """Return list of dicts with ticker metadata.

    Each entry: {ticker, company, updated, stock_price}
    Supabase first (lightweight query), fallback to local index.
    """
    client = _get_client()
    if client:
        resp = _supabase_safe(
            lambda: client.table("watchlist_configs")
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

    # Fallback to local index
    index = _load_index()
    result = []
    for ticker, meta in index.items():
        result.append({
            'ticker': ticker,
            'company': meta.get('company', ticker),
            'updated': meta.get('updated', ''),
            'stock_price': meta.get('stock_price', 0),
        })
    return result


def remove_from_watchlist(ticker):
    """Remove a ticker from local files + Supabase."""
    ticker = ticker.upper()

    # Remove local file
    path = _ticker_path(ticker)
    if os.path.exists(path):
        os.remove(path)

    # Update local index
    index = _load_index()
    index.pop(ticker, None)
    _save_index(index)

    # Best-effort Supabase delete
    client = _get_client()
    if client:
        _supabase_safe(
            lambda: client.table("watchlist_configs")
            .delete()
            .eq("ticker", ticker)
            .execute()
        )


# ---------------------------------------------------------------------------
# User preferences (wheel strategy settings, persisted locally)
# ---------------------------------------------------------------------------

_PREFS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs", "user_prefs.json")

_DEFAULT_PREFS = {
    "delta_min": 0.20,
    "delta_max": 0.35,
    "dte_min": 25,
    "dte_max": 45,
}


def load_user_prefs():
    """Load user wheel preferences. Returns dict with defaults for missing keys."""
    prefs = dict(_DEFAULT_PREFS)
    try:
        if os.path.exists(_PREFS_PATH):
            with open(_PREFS_PATH, 'r') as f:
                prefs.update(json.load(f))
    except Exception:
        pass
    return prefs


def save_user_prefs(prefs):
    """Save user wheel preferences to local JSON."""
    _ensure_dir()
    try:
        with open(_PREFS_PATH, 'w') as f:
            json.dump(prefs, f, indent=2)
    except Exception:
        pass
