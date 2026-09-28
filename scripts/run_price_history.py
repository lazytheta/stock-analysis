"""Cloud Run Job: add the latest daily closes to Supabase price_history
(Nasdaq for US tickers, Boerse Frankfurt for non-US tickers with an ISIN),
then upsert Nasdaq earnings-surprise rows into earnings_history.

Exits non-zero only when every price ticker failed; an earnings failure is
logged but never fails the job."""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import earnings_history
import price_history


def main():
    logging.basicConfig(level=logging.INFO)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"],
                           os.environ["SUPABASE_SERVICE_KEY"])
    result = price_history.run(client)
    print(f"price_history: {result}")
    try:
        print(f"earnings_history: {earnings_history.run(client)}")
    except Exception as e:
        logging.getLogger(__name__).warning(
            "earnings history run failed: %s: %s", type(e).__name__, e)
    if result["errors"] and len(result["errors"]) == result["tickers"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
