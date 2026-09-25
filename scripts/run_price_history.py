"""Cloud Run Job: add the latest Nasdaq daily closes to Supabase price_history."""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import price_history


def main():
    logging.basicConfig(level=logging.INFO)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"],
                           os.environ["SUPABASE_SERVICE_KEY"])
    result = price_history.run(client)
    print(f"price_history: {result}")
    if result["errors"] and len(result["errors"]) == result["tickers"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
