"""
Watchlist config storage — JSON-based persistence for DCF configs.

Stores each ticker's config as configs/watchlist/TICKER.json
with a configs/watchlist/_index.json for quick listing.
"""

import json
import os
from datetime import datetime

WATCHLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs", "watchlist")


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


def save_config(ticker, cfg):
    """Save a DCF config dict to JSON and update the index."""
    ticker = ticker.upper()
    _ensure_dir()

    data = _prepare_for_json(cfg)
    with open(_ticker_path(ticker), 'w') as f:
        json.dump(data, f, indent=2)

    index = _load_index()
    index[ticker] = {
        'company': cfg.get('company', ticker),
        'updated': datetime.now().isoformat(),
        'stock_price': cfg.get('stock_price', 0),
    }
    _save_index(index)


def load_config(ticker):
    """Load a DCF config dict from JSON. Returns dict or None."""
    path = _ticker_path(ticker.upper())
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        cfg = json.load(f)
    return _restore_tuples(cfg)


def list_watchlist():
    """Return list of dicts with ticker metadata from the index.

    Each entry: {ticker, company, updated, stock_price}
    """
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
    """Remove a ticker's JSON config and update the index."""
    ticker = ticker.upper()
    path = _ticker_path(ticker)
    if os.path.exists(path):
        os.remove(path)

    index = _load_index()
    index.pop(ticker, None)
    _save_index(index)
