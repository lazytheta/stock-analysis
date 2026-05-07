"""One-shot force-refresh for the entire watchlist.

Loads Supabase credentials from claude_desktop_config.json (lazytheta-dcf
MCP server). For every ticker on the watchlist:
  1. Load config
  2. Run _auto_fill_valuation_inputs (yfinance forward_eps, ttm_ebitda)
  3. Run _auto_fill_peer_market_data (yfinance fwd_pe + real ev_ebitda per peer)
  4. Run multi-lens orchestrator
  5. Persist updated cfg back to Supabase

Why this exists: the in-app "Refresh all" button skips tickers with summaries
calculated < 7 days ago (stale-only mode). The Force-refresh-all UI link was
removed in Phase 2-A. This script is a one-shot equivalent for the
"recompute everything regardless of freshness" use case.
"""

from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Load creds from claude_desktop_config.json
CFG_PATH = Path("/Users/administrator/Library/Application Support/Claude/claude_desktop_config.json")
mcp_cfg = json.loads(CFG_PATH.read_text())
env = mcp_cfg["mcpServers"]["lazytheta-dcf"]["env"]
os.environ["SUPABASE_URL"] = env["SUPABASE_URL"]
os.environ["SUPABASE_SERVICE_KEY"] = env["SUPABASE_SERVICE_KEY"]
os.environ["LAZYTHETA_USER_ID"] = env["LAZYTHETA_USER_ID"]

# Project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config_store
import valuation_lenses
from auto_fetch import auto_fill_peer_market_data, auto_fill_valuation_inputs
from supabase import create_client

USER_ID = os.environ["LAZYTHETA_USER_ID"]
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def refresh_one(ticker: str) -> tuple[str, dict | None, str | None]:
    """Returns (ticker, summary_dict_or_None, error_msg_or_None)."""
    try:
        cfg = config_store.load_config(client, ticker, user_id=USER_ID)
        if cfg is None:
            return ticker, None, "not on watchlist"
        cfg.setdefault("ticker", ticker)
        auto_fill_valuation_inputs(cfg)
        auto_fill_peer_market_data(cfg)
        summary = valuation_lenses.calculate_multi_lens_valuation(cfg, scenario_grid=False)
        cfg["valuation_summary"] = summary
        config_store.save_config(client, ticker, cfg, user_id=USER_ID)
        return ticker, summary, None
    except Exception as e:
        return ticker, None, f"{type(e).__name__}: {e}"


def main():
    t0 = time.time()
    entries = config_store.list_watchlist(client, user_id=USER_ID)
    tickers = [e["ticker"] for e in entries]
    print(f"Force-refreshing {len(tickers)} tickers in parallel (6 workers)...")

    computed: list[str] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(refresh_one, t): t for t in tickers}
        for i, future in enumerate(as_completed(futures), 1):
            ticker, summary, err = future.result()
            if err:
                errors.append(f"{ticker}: {err}")
                print(f"  [{i:2}/{len(tickers)}] {ticker:6} ERROR — {err}")
            else:
                # Count only forward-looking lenses to match the watchlist UI
                # (reverse_dcf and dividend are computed but not surfaced as "active").
                _counted = ("dcf", "multiples", "historical")
                _ls = summary["lenses"] or {}
                lens_count = sum(1 for k in _counted if _ls.get(k) is not None)
                fv_mid = summary["weighted_fv_mid"]
                print(f"  [{i:2}/{len(tickers)}] {ticker:6} ok — fv_mid=${fv_mid:>8.2f}  lenses={lens_count}")
                computed.append(ticker)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s · computed {len(computed)} · errors {len(errors)}")
    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
