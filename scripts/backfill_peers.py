"""Backfill peers for watchlist tickers that were added without peer discovery.

For each ticker with `cfg["peers"] == []`:
  1. Look up CIK via SEC EDGAR
  2. Read SIC code from company submissions
  3. Run gather_data.find_peers + fetch_peer_data
  4. Set cfg["peers"] = result
  5. Save back

Run-once script. Existing peer data is never overwritten.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

CFG_PATH = Path("/Users/administrator/Library/Application Support/Claude/claude_desktop_config.json")
mcp_cfg = json.loads(CFG_PATH.read_text())
env = mcp_cfg["mcpServers"]["lazytheta-dcf"]["env"]
for k, v in env.items():
    os.environ[k] = v

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import config_store
import gather_data
from supabase import create_client

USER_ID = os.environ["LAZYTHETA_USER_ID"]
client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def _sic_for(ticker: str) -> tuple[str, str] | None:
    """Returns (sic_code, sic_description) or None on failure."""
    try:
        cik = gather_data.get_cik(ticker)
        time.sleep(0.2)
        subs = gather_data.fetch_company_submissions(cik)
        sic = subs.get("sic", "")
        sic_desc = subs.get("sicDescription", "")
        if sic:
            return sic, sic_desc
    except Exception as e:
        print(f"  SIC lookup failed for {ticker}: {e}")
    return None


def backfill_one(ticker: str) -> tuple[str, int, str | None]:
    """Returns (ticker, n_peers_added, error_msg)."""
    try:
        cfg = config_store.load_config(client, ticker, user_id=USER_ID)
        if cfg is None:
            return ticker, 0, "not on watchlist"
        if cfg.get("peers"):
            return ticker, len(cfg["peers"]), None  # already has peers — no-op

        sic = _sic_for(ticker)
        if not sic:
            return ticker, 0, "no SIC code"
        sic_code, _ = sic
        market_cap = cfg.get("equity_market_value", 0)
        if market_cap <= 0:
            return ticker, 0, "no market cap in config"

        # find + fetch
        peer_tickers = gather_data.find_peers(
            sic_code=int(sic_code),
            target_ticker=ticker,
            target_market_cap=market_cap,
        )
        if not peer_tickers:
            return ticker, 0, "no peer candidates from SIC"

        peers = gather_data.fetch_peer_data(peer_tickers)
        cfg["peers"] = peers
        config_store.save_config(client, ticker, cfg, user_id=USER_ID)
        return ticker, len(peers), None
    except Exception as e:
        return ticker, 0, f"{type(e).__name__}: {e}"


def main():
    entries = config_store.list_watchlist(client, user_id=USER_ID)
    targets = []
    for e in entries:
        cfg = config_store.load_config(client, e["ticker"], user_id=USER_ID)
        if cfg and not cfg.get("peers"):
            targets.append(e["ticker"])

    if not targets:
        print("All tickers already have peers — nothing to do.")
        return

    print(f"Backfilling peers for {len(targets)} tickers (sequential, SEC rate-limited):")
    print(f"  {', '.join(targets)}\n")

    t0 = time.time()
    results = []
    for i, ticker in enumerate(targets, 1):
        print(f"  [{i:2}/{len(targets)}] {ticker} ...", end=" ", flush=True)
        ticker, n, err = backfill_one(ticker)
        if err:
            print(f"ERROR — {err}")
        else:
            print(f"{n} peers added")
        results.append((ticker, n, err))

    elapsed = time.time() - t0
    ok = sum(1 for _, n, e in results if e is None and n > 0)
    fail = sum(1 for _, _, e in results if e)
    print(f"\nDone in {elapsed:.1f}s · backfilled {ok} · errors {fail}")


if __name__ == "__main__":
    main()
