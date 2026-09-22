"""Run the quality screen over the index universe and store one snapshot.

Draait als Cloud Run Job, maandelijks via Cloud Scheduler (zie
scripts/deploy_screener_job.sh), of lokaal met de hand. EDGAR is vanaf beide
bereikbaar; de pagina leest alleen snapshots. Nodig: SUPABASE_URL +
SUPABASE_SERVICE_KEY in de omgeving, behalve bij --dry-run.

    python3 scripts/run_screener.py [--refresh-universe] [--limit N] [--dry-run]

--refresh-universe haalt de indexlijsten opnieuw op vóór de screen. Lukt dat
niet (stockanalysis.com blokkeert wel eens een cloud-IP), dan draait de screen
op het universum dat bij de code zit en zegt de log dat.
"""
import argparse
import contextlib
import io
import json
import os
import pathlib
import sys
from datetime import UTC, datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import gather_data
from screener import compute_screener

UNIVERSE_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "champions_universe.json"


def _fetch(ticker):
    return gather_data.fetch_fundamentals(ticker, n_years=10)


def load_universe(refresh: bool) -> dict:
    """Het universum, vers als dat kan en anders het meegeleverde bestand.

    Een mislukte refresh is geen reden om niet te screenen: de lijsten
    veranderen een paar namen per kwartaal, de cijfers erachter elk jaar.
    Maar het moet wél in de log staan, anders leest een oud universum als een
    vers universum.
    """
    if refresh:
        try:
            from cashflow_champions import refresh_universe
            snap = refresh_universe()
            print(f"universum ververst: {snap['count']} namen per {snap['as_of']}")
            return snap
        except Exception as e:
            print(f"WAARSCHUWING: universum verversen mislukt ({type(e).__name__}: {e}); "
                  f"screen draait op het opgeslagen universum", file=sys.stderr)
    return json.loads(UNIVERSE_PATH.read_text())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-universe", action="store_true")
    ap.add_argument("--limit", type=int, default=0,
                    help="alleen de eerste N namen (rooktest)")
    ap.add_argument("--dry-run", action="store_true",
                    help="niet naar Supabase schrijven")
    args = ap.parse_args(argv)

    universe = load_universe(args.refresh_universe)
    if args.limit:
        universe = dict(universe, constituents=universe["constituents"][:args.limit])
    print(f"universum {universe['as_of']}: {len(universe['constituents'])} namen")

    done = {"n": 0}

    def fetch(t):
        out = _fetch(t)
        done["n"] += 1
        if done["n"] % 25 == 0:
            print(f"  {done['n']} opgehaald...", file=sys.stderr)
        return out

    # One redirect around the whole batch: fetch_fundamentals narrates every
    # filing, and a per-thread redirect_stdout swaps the global stdout — not
    # thread-safe, and it swallowed the final summary on the first run.
    with contextlib.redirect_stdout(io.StringIO()):
        result = compute_screener(universe, fetch=fetch, max_workers=5)
    s = result["summary"]
    print(f"\n{s['total']} gescreend, {s['passes']} geslaagd")
    for idx, e in sorted(s["per_index"].items()):
        print(f"  {idx:10} {e['passes']:>3} van {e['total']}")
    print("  afgevallen:", s["reasons"])

    if args.dry_run:
        print("dry-run: niets opgeslagen")
        return

    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"],
                       os.environ["SUPABASE_SERVICE_KEY"])
    sb.table("screener_snapshots").insert({
        "computed_at": datetime.now(UTC).isoformat(),
        "universe_as_of": result["universe_as_of"],
        "summary": s,
        "rows": result["rows"],
    }).execute()
    print("snapshot opgeslagen")


if __name__ == "__main__":
    main()
