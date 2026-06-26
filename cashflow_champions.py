"""Cashflow Champions — the Liontrust "Cashflow Solution" two-ratio screen.

Inspired by James Inglis-Jones & Samantha Gleave (see Obsidian note
"Stock Market Maestros"). A company is ranked on two ratios, combined, and the
top 20% of the eligible universe are the "Champions":

    Cash Return on Assets  = operating cash flow / total assets   (quality, high = good)
    Price-to-Cash-Flow     = market cap / operating cash flow      (value,  low  = good)

"Cashflow" here is OPERATING cash flow (CFO), not FCF — per the book.

This module has two clearly separated halves:
  • Pure ranking logic (`rank_universe`, `_percentiles`) — no network, fully
    unit-testable on a synthetic universe.
  • A batch fetch/compute pipeline (`compute_champions`) over the ~550-name
    union of the S&P 500, Nasdaq-100 and Dow 30. EDGAR fundamentals per ticker,
    a disk cache keyed by ticker+fiscal-year, a configurable concurrency cap,
    and partial-failure handling that records a reason and continues.

The heavy batch is meant to run where EDGAR/Yahoo are reachable (locally or a
scheduled job), writing a snapshot to Supabase that the Streamlit page reads.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime

import gather_data

# ── Paths & sources ───────────────────────────────────────────────────────────

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UNIVERSE_PATH = os.path.join(_DATA_DIR, "champions_universe.json")
_CACHE_DIR = os.path.join(_DATA_DIR, "champions_cache")

SEC_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SP500_CSV_URL = (
    "https://raw.githubusercontent.com/datasets/"
    "s-and-p-500-companies/main/data/constituents.csv"
)
NASDAQ100_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"
DOW30_WIKI_URL = "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average"

# SIC codes 6000–6999 are finance/insurance/real-estate. Their asset bases
# dwarf operating cash flow, so Cash ROA is not comparable — excluded by default.
_FINANCIAL_SIC_LO, _FINANCIAL_SIC_HI = 6000, 6999

# A large-cap going concern priced below ~2× its annual operating cash flow does
# not occur in practice — a P/CF this low means the market cap is wrong (an
# uncaught multi-class share undercount). Treat as a data-quality exclusion so a
# bad number can't pollute the ranking, rather than silently trusting it.
_MIN_PLAUSIBLE_PCF = 2.0


def _norm(ticker: str) -> str:
    """Normalise a ticker for cross-source matching (BRK.B / BRK-B → BRKB)."""
    return ticker.upper().replace(".", "").replace("-", "").strip()


# ── Universe snapshot (checked-in, refreshable) ────────────────────────────────

def refresh_universe(today: str | None = None) -> dict:
    """Re-pull the three index constituent lists + the SEC ticker→CIK/exchange
    map, build the union, and write data/champions_universe.json with an as-of
    date. Free sources, no API key. Returns the snapshot dict."""
    import csv
    import io
    import re

    today = today or date.today().isoformat()

    def _get(url, headers=None):
        return gather_data._http_get(url, headers or {"User-Agent": "Mozilla/5.0"})

    # SEC exchange file: authoritative ticker → (cik, name, exchange)
    cte = json.loads(_get(SEC_EXCHANGE_URL, gather_data.EDGAR_HEADERS))
    fields = cte["fields"]
    ci, ni, ti, ei = (fields.index(f) for f in ("cik", "name", "ticker", "exchange"))
    by_norm: dict[str, dict] = {}
    for row in cte["data"]:
        tk = row[ti]
        if not tk:
            continue
        by_norm[_norm(tk)] = {
            "ticker": tk.upper(), "name": row[ni],
            "cik": int(row[ci]), "exchange": row[ei],
        }

    # S&P 500 (datahub CSV; carries CIK too)
    sp_rows = list(csv.DictReader(io.StringIO(_get(SP500_CSV_URL).decode("utf-8", "ignore"))))
    sp500 = sorted({r["Symbol"].upper() for r in sp_rows if r.get("Symbol")})

    # Nasdaq-100 & Dow 30 (Wikipedia constituents table, regex-parsed, validated
    # against the SEC ticker set so junk rows can't slip in)
    valid = set(by_norm)

    def _wiki_constituents(url):
        html = _get(url).decode("utf-8", "ignore")
        m = re.search(r'id="constituents".*?>(.*?)</table>', html, re.S)
        body = m.group(1) if m else html
        out = []
        for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S):
            for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S):
                text = re.sub(r"<[^>]+>", "", cell).strip().replace("&amp;", "&")
                if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", text) and _norm(text) in valid:
                    out.append(text.upper())
                    break
        return sorted(set(out))

    nasdaq100 = _wiki_constituents(NASDAQ100_WIKI_URL)
    dow30 = _wiki_constituents(DOW30_WIKI_URL)

    membership: dict[str, set] = {}
    for idx_name, syms in (("sp500", sp500), ("nasdaq100", nasdaq100), ("dow30", dow30)):
        for s in syms:
            membership.setdefault(_norm(s), set()).add(idx_name)

    constituents = []
    unresolved = []
    for nk, indices in sorted(membership.items()):
        meta = by_norm.get(nk)
        if not meta:
            unresolved.append(nk)
            continue
        constituents.append({
            **meta, "indices": sorted(indices),
        })

    snapshot = {
        "as_of": today,
        "sources": {
            "sp500": {"url": SP500_CSV_URL, "as_of": today, "count": len(sp500)},
            "nasdaq100": {"url": NASDAQ100_WIKI_URL, "as_of": today, "count": len(nasdaq100)},
            "dow30": {"url": DOW30_WIKI_URL, "as_of": today, "count": len(dow30)},
            "sec_exchange": {"url": SEC_EXCHANGE_URL, "as_of": today},
        },
        "unresolved": sorted(unresolved),
        "count": len(constituents),
        "constituents": constituents,
    }
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(UNIVERSE_PATH, "w") as f:
        json.dump(snapshot, f, indent=2)
    return snapshot


def load_universe() -> dict:
    with open(UNIVERSE_PATH) as f:
        return json.load(f)


# ── Pure ranking logic (no network — unit-tested on synthetic data) ─────────────

def is_financial(sic) -> bool:
    try:
        return _FINANCIAL_SIC_LO <= int(sic) <= _FINANCIAL_SIC_HI
    except (TypeError, ValueError):
        return False


def _percentiles(values: list[float]) -> list[float]:
    """Percentile rank of each value in [0,1], higher value → higher percentile.
    Ties share the average rank. Single element → 1.0."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    order = sorted(range(n), key=lambda i: values[i])
    # average-rank to handle ties deterministically
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0  # 0-based average position
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return [r / (n - 1) for r in ranks]


@dataclass
class ChampRow:
    ticker: str
    name: str = ""
    exchange: str = ""
    indices: list = field(default_factory=list)
    sic: int | None = None
    cfo: float | None = None           # operating cash flow, $M
    total_assets: float | None = None  # $M
    market_cap: float | None = None    # $M
    fiscal_year: int | None = None
    status: str = "ok"                 # ok | failed | excluded
    reason: str | None = None
    # filled by rank_universe:
    cash_roa: float | None = None
    price_to_cf: float | None = None
    cash_roa_pct: float | None = None
    value_pct: float | None = None
    composite: float | None = None
    rank: int | None = None
    is_champion: bool = False


def rank_universe(rows: list[ChampRow], exclude_financials: bool = True,
                  top_pct: float = 0.20) -> dict:
    """Compute the two ratios, percentile-rank the eligible names, combine into a
    composite score, and flag the top `top_pct` as Champions.

    Mutates and returns the rows (with ranking fields filled) plus a summary.
    Ineligible names stay in the list with status/reason set — never dropped.
    """
    eligible: list[ChampRow] = []
    excluded_financials = 0
    for r in rows:
        if r.status == "failed":
            continue
        if exclude_financials and is_financial(r.sic):
            r.status, r.reason = "excluded", "financial"
            excluded_financials += 1
            continue
        if r.cfo is None or r.total_assets is None or r.market_cap is None:
            r.status, r.reason = "excluded", "missing_data"
            continue
        if r.cfo <= 0:
            r.status = "excluded"
            r.reason = "negative_cfo"
            continue
        if r.total_assets <= 0 or r.market_cap <= 0:
            r.status, r.reason = "excluded", "non_positive_input"
            continue
        r.cash_roa = r.cfo / r.total_assets
        r.price_to_cf = r.market_cap / r.cfo
        if r.price_to_cf < _MIN_PLAUSIBLE_PCF:
            r.status, r.reason = "excluded", "data_quality"
            r.cash_roa = r.price_to_cf = None
            continue
        r.status, r.reason = "ok", None
        eligible.append(r)

    if eligible:
        roa_pct = _percentiles([r.cash_roa for r in eligible])
        # value: cheaper (low P/CF) = higher cashflow yield = better → rank yield
        yield_pct = _percentiles([r.cfo / r.market_cap for r in eligible])
        for r, rp, vp in zip(eligible, roa_pct, yield_pct, strict=True):
            r.cash_roa_pct = rp
            r.value_pct = vp
            r.composite = (rp + vp) / 2.0
        eligible.sort(key=lambda r: r.composite, reverse=True)
        n_champ = max(1, math.ceil(len(eligible) * top_pct))
        for i, r in enumerate(eligible, start=1):
            r.rank = i
            r.is_champion = i <= n_champ

    summary = {
        "requested": len(rows),
        "ranked": len(eligible),
        "champions": sum(1 for r in eligible if r.is_champion),
        "failed": sum(1 for r in rows if r.status == "failed"),
        "excluded": sum(1 for r in rows if r.status == "excluded"),
        "excluded_financials": excluded_financials,
        "top_pct": top_pct,
        "failures": [
            {"ticker": r.ticker, "reason": r.reason}
            for r in rows if r.status == "failed"
        ],
    }
    return {"rows": rows, "summary": summary}


# ── Batch fetch pipeline (network; cache + concurrency + partial failure) ───────

def _cache_path(ticker: str) -> str:
    return os.path.join(_CACHE_DIR, f"{_norm(ticker)}.json")


def _read_cache(ticker: str, max_age_days: int) -> dict | None:
    path = _cache_path(ticker)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            blob = json.load(f)
        fetched = datetime.fromisoformat(blob["fetched_at"])
        age_days = (datetime.now(UTC) - fetched).total_seconds() / 86400
        if age_days > max_age_days:
            return None
        return blob
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


def _write_cache(ticker: str, blob: dict) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    blob = {**blob, "fetched_at": datetime.now(UTC).isoformat()}
    with open(_cache_path(ticker), "w") as f:
        json.dump(blob, f)


# Share-count tags, in order of preference. Diluted weighted-average shares are
# reported every quarter and are split-adjusted, so the latest observation is the
# current count — this is what keeps market cap right through a stock split. The
# dei cover-page tag is the last resort.
_SHARE_TAGS = [
    ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
    ("us-gaap", "WeightedAverageNumberOfSharesOutstandingBasic"),
    ("us-gaap", "CommonStockSharesOutstanding"),
    ("dei", "EntityCommonStockSharesOutstanding"),
]


def _latest_shares(facts: dict):
    """Latest reported share count and its period-end date. Returns
    (shares, end) or (None, None). Note: for some multi-class issuers (e.g. V)
    the current count is dimensional and absent from the flat companyfacts feed,
    so the only value here is a decade-old one — the caller rejects stale ends."""
    for tax, tag in _SHARE_TAGS:
        try:
            arr = facts["facts"][tax][tag]["units"]["shares"]
        except (KeyError, TypeError):
            continue
        if arr:
            obs = max(arr, key=lambda u: (u.get("end", ""), u.get("filed", "")))
            return obs.get("val"), obs.get("end")
    return None, None


def _extract_inputs(facts: dict):
    """Pull the screen's three inputs from one companyfacts document: latest-year
    CFO and total assets ($M), and a current, split-adjusted share count. A share
    count older than the CFO fiscal year (multi-class issuers whose current count
    is dimensional and missing from the feed) is rejected → market cap unknown."""
    cfo = dict(gather_data._try_tags(
        facts, ["NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByOperatingActivities"]))
    assets = dict(gather_data._try_tags(facts, ["Assets"]))
    fiscal_year = max(cfo) if cfo else (max(assets) if assets else None)
    cfo_m = round(cfo[max(cfo)] / 1_000_000, 1) if cfo else None
    assets_m = round(assets[max(assets)] / 1_000_000, 1) if assets else None

    shares, sh_end = _latest_shares(facts)
    if shares and sh_end and fiscal_year:
        try:
            if int(sh_end[:4]) < fiscal_year - 1:  # stale (decade-old fallback)
                shares = None
        except ValueError:
            pass
    # A few filers report the share count already scaled to millions (e.g. MCD:
    # 713.5 = 713.5M). No index constituent has 1–1,000,000 raw shares, so a
    # value in that gap is a millions-scaled figure → restore the raw count.
    if shares and 0 < shares < 1_000_000:
        shares *= 1_000_000
    return fiscal_year, cfo_m, assets_m, shares


def _fetch_one(item: dict, max_cache_age_days: int) -> dict:
    """Fetch the inputs for one ticker. Returns a plain dict (cache-shaped).
    Raises on hard failure so the caller can record a reason and continue."""
    ticker = item["ticker"]
    cached = _read_cache(ticker, max_cache_age_days)
    if cached is not None:
        cached["from_cache"] = True
        return cached

    cik = item.get("cik") or gather_data.get_cik(ticker)
    facts = gather_data.fetch_company_facts(cik)
    fiscal_year, cfo, total_assets, shares = _extract_inputs(facts)

    # SIC for the financials filter (cached inside the same blob)
    sic = None
    try:
        sub = gather_data.fetch_company_submissions(cik)
        sic = int(sub.get("sic")) if str(sub.get("sic", "")).isdigit() else None
    except Exception:
        sic = None

    # Price → market cap ($M), using the split-aware share count.
    price = _fetch_price(ticker)
    market_cap = (price * shares / 1_000_000) if (price and shares) else None

    blob = {
        "ticker": ticker, "fiscal_year": fiscal_year, "sic": sic,
        "cfo": cfo, "total_assets": total_assets, "shares": shares,
        "price": price, "market_cap": market_cap, "from_cache": False,
    }
    _write_cache(ticker, blob)
    return blob


def _fetch_price(ticker: str) -> float | None:
    """Latest close from the Yahoo chart API (works locally; blocked on some
    cloud IPs — that's why this runs as a batch). Returns None on failure."""
    try:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
               f"?range=5d&interval=1d")
        data = gather_data._http_get_json(url, gather_data.YAHOO_HEADERS)
        result = data["chart"]["result"][0]
        meta = result.get("meta", {})
        px = meta.get("regularMarketPrice")
        if px:
            return float(px)
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c]
        return float(closes[-1]) if closes else None
    except Exception:
        return None


def compute_champions(tickers: list[str] | None = None, *,
                      exclude_financials: bool = True, top_pct: float = 0.20,
                      concurrency: int = 5, max_cache_age_days: int = 30,
                      progress=None) -> dict:
    """Fetch inputs for the universe and rank them. `tickers=None` → the full
    checked-in union (S&P 500 ∪ Nasdaq-100 ∪ Dow 30).

    Robust at scale: bounded concurrency, disk cache (skips re-fetch within
    max_cache_age_days → resumable), and per-ticker failures recorded with a
    reason instead of aborting the run. `progress(done, total, ticker)` is
    called as each ticker completes (optional).
    """
    universe = load_universe()
    meta_by_norm = {_norm(c["ticker"]): c for c in universe["constituents"]}
    if tickers is None:
        items = list(universe["constituents"])
    else:
        items = [meta_by_norm.get(_norm(t), {"ticker": t.upper(), "indices": []})
                 for t in tickers]

    # Pre-seed gather_data.get_cik from the snapshot so 550 fetches don't each
    # re-download the ~1 MB company_tickers file. One contained optimisation.
    _install_cik_cache(universe)

    rows: list[ChampRow] = []
    total = len(items)
    done = 0
    lock = threading.Lock()

    def work(item):
        ticker = item["ticker"]
        try:
            blob = _fetch_one(item, max_cache_age_days)
            return ChampRow(
                ticker=ticker, name=item.get("name", ""),
                exchange=item.get("exchange", ""), indices=item.get("indices", []),
                sic=blob.get("sic"), cfo=blob.get("cfo"),
                total_assets=blob.get("total_assets"),
                market_cap=blob.get("market_cap"),
                fiscal_year=blob.get("fiscal_year"),
            )
        except Exception as e:  # delisted, no EDGAR data, timeout, parse error…
            return ChampRow(ticker=ticker, name=item.get("name", ""),
                            exchange=item.get("exchange", ""),
                            indices=item.get("indices", []),
                            status="failed", reason=f"{type(e).__name__}: {e}"[:200])

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        futures = {ex.submit(work, it): it for it in items}
        for fut in as_completed(futures):
            row = fut.result()
            rows.append(row)
            with lock:
                done += 1
            if progress:
                progress(done, total, row.ticker)

    result = rank_universe(rows, exclude_financials=exclude_financials, top_pct=top_pct)
    result["universe_as_of"] = universe.get("as_of")
    result["computed_at"] = datetime.now(UTC).isoformat()
    return result


def _install_cik_cache(universe: dict) -> None:
    cik_map = {_norm(c["ticker"]): c["cik"] for c in universe["constituents"] if c.get("cik")}
    _orig = gather_data.get_cik

    def cached_get_cik(ticker):
        hit = cik_map.get(_norm(ticker))
        return hit if hit is not None else _orig(ticker)

    gather_data.get_cik = cached_get_cik


# ── Snapshot serialisation for storage ─────────────────────────────────────────

def to_snapshot(result: dict) -> dict:
    """Flatten a compute_champions result into a JSON-serialisable snapshot
    (one document) suitable for storing in Supabase and reading on the page."""
    return {
        "computed_at": result.get("computed_at"),
        "universe_as_of": result.get("universe_as_of"),
        "summary": result["summary"],
        "rows": [asdict(r) for r in result["rows"]],
    }


# ── Supabase storage (global, one row per run) ──────────────────────────────────
# The champions ranking is universe-wide (identical for every user), so it lives
# in a single global table: the batch writes it (service role), the page reads it.

def store_champions_snapshot(snapshot: dict, client=None) -> None:
    """Insert a computed snapshot. Defaults to the service-role client used by
    the rest of the batch tooling (SUPABASE_URL + SUPABASE_SERVICE_KEY)."""
    if client is None:
        import mcp_server
        client = mcp_server.get_supabase_client()
    client.table("champions_snapshots").insert({
        "computed_at": snapshot.get("computed_at"),
        "universe_as_of": snapshot.get("universe_as_of"),
        "summary": snapshot.get("summary"),
        "rows": snapshot.get("rows"),
    }).execute()


def load_latest_snapshot(client) -> dict | None:
    """Most recent stored snapshot, or None if the table is empty."""
    resp = (client.table("champions_snapshots").select("*")
            .order("computed_at", desc=True).limit(1).execute())
    data = resp.data or []
    return data[0] if data else None


if __name__ == "__main__":  # pragma: no cover
    import argparse

    p = argparse.ArgumentParser(description="Cashflow Champions batch")
    p.add_argument("--refresh-universe", action="store_true",
                   help="Re-pull the index constituent lists and rewrite the snapshot")
    p.add_argument("--compute", action="store_true", help="Run the ranking over the universe")
    p.add_argument("--store", action="store_true", help="Write the snapshot to Supabase")
    p.add_argument("--limit", type=int, default=None, help="Only the first N tickers (debug)")
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--max-cache-age-days", type=int, default=30)
    args = p.parse_args()

    if args.refresh_universe:
        snap = refresh_universe()
        print(f"Universe refreshed: {snap['count']} names "
              f"(SP500 {snap['sources']['sp500']['count']}, "
              f"Nasdaq100 {snap['sources']['nasdaq100']['count']}, "
              f"Dow30 {snap['sources']['dow30']['count']}) as of {snap['as_of']}")
        if snap["unresolved"]:
            print(f"  unresolved (no SEC CIK): {snap['unresolved']}")

    if args.compute:
        uni = load_universe()
        tks = [c["ticker"] for c in uni["constituents"]]
        if args.limit:
            tks = tks[:args.limit]

        def _prog(done, total, tk):
            if done % 25 == 0 or done == total:
                print(f"  {done}/{total} … last={tk}")

        t0 = time.time()
        res = compute_champions(tks, concurrency=args.concurrency,
                                max_cache_age_days=args.max_cache_age_days,
                                progress=_prog)
        dt = time.time() - t0
        s = res["summary"]
        print(f"\nDone in {dt:.0f}s — ranked {s['ranked']}, champions {s['champions']}, "
              f"failed {s['failed']}, excluded {s['excluded']} "
              f"(financials {s['excluded_financials']})")
        champs = [r for r in res["rows"] if r.is_champion]
        champs.sort(key=lambda r: r.rank)
        print("\nTop Champions:")
        for r in champs[:25]:
            print(f"  #{r.rank:>3} {r.ticker:<6} CashROA={r.cash_roa:.3f} "
                  f"P/CF={r.price_to_cf:.1f} score={r.composite:.3f}")

        if args.store:
            store_champions_snapshot(to_snapshot(res))
            print("Stored snapshot to Supabase.")
