#!/usr/bin/env python3
"""One-time migration: upload existing watchlist JSON configs to Supabase."""

import glob
import json
import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_store import save_config, WATCHLIST_DIR


def main():
    pattern = os.path.join(WATCHLIST_DIR, "*.json")
    files = [f for f in glob.glob(pattern) if not f.endswith("_index.json")]

    if not files:
        print("No config files found in", WATCHLIST_DIR)
        return

    for path in sorted(files):
        ticker = os.path.splitext(os.path.basename(path))[0].upper()
        with open(path, 'r') as f:
            cfg = json.load(f)
        print(f"Migrating {ticker} ...", end=" ")
        save_config(ticker, cfg)
        print("done")

    print(f"\nMigrated {len(files)} config(s) to Supabase.")


if __name__ == "__main__":
    main()
