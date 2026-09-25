# Live Quotes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One live-quote chain — Tastytrade → Nasdaq → Yahoo → Frankfurt — used by the watchlist, portfolio, ticker page, `gather_data.fetch_stock_price` and the MCP.

**Architecture:** `quotes.py` gains a Nasdaq source (`fetch_nasdaq_quotes`) and the chain (`live_quotes`), both with injectable sources so tests stay offline. `broker_adapter.fetch_current_prices` becomes a thin wrapper over `live_quotes`; the ticker page switches to it; `gather_data.fetch_stock_price` asks Nasdaq before Yahoo, which carries the MCP along.

**Tech Stack:** Python 3.11+, urllib, ThreadPoolExecutor, pytest/unittest with mocks, Streamlit 1.54.

**Spec:** `docs/superpowers/specs/2026-09-25-live-quotes-design.md`

## Global Constraints

- Nasdaq endpoint: `https://api.nasdaq.com/api/quote/{SYM}/info?assetclass=stocks` then `assetclass=etf` when `data` is null; headers `{"User-Agent": "Mozilla/5.0", "Accept": "application/json"}`; timeout 5 s, no retries; max 8 parallel.
- Nasdaq only for symbols matching `^[A-Z]{1,5}$`; anything else → None without a request.
- Quote contract everywhere: `{"price": float, "previousClose": float|None, "asof": str|None, "venue": str}`; a missing quote is `None`, never 0.
- Every requested ticker is a key in the output (value may be None); order follows input, duplicates once.
- A failing source is logged (`logger.warning`) and treated as "found nothing"; it never raises out of the chain.
- `gather_data.fetch_stock_price(ticker)` keeps its `(price, 0, 0)` signature and returns `(0, 0, 0)` on total failure (existing callers rely on that).
- Tests run offline: no test may hit the network. `python3 -m ruff check .` must pass.
- Comments/docstrings in the style of the surrounding file (quotes.py is Dutch; broker_adapter/gather_data English).

---

### Task 1: Nasdaq source in quotes.py

**Files:**
- Modify: `quotes.py` (add after `quote_age_minutes`)
- Create: `tests/test_nasdaq_quotes.py`

**Interfaces:**
- Produces: `quotes.fetch_nasdaq_quotes(tickers, fetch=None, max_workers=8) -> dict[str, dict|None]`; `quotes.NASDAQ_URL`; `quotes._nasdaq_symbol_ok(ticker) -> bool`.

- [ ] **Step 1: Write the failing tests** — `tests/test_nasdaq_quotes.py`:

```python
"""Nasdaq-koersen, offline: de HTTP-kant is een geinjecteerde fetch."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quotes


def _info(price="$71.6993", change="-0.0207",
          ts="Sep 25, 2026 9:02 AM ET"):
    return {"data": {"primaryData": {
        "lastSalePrice": price, "netChange": change,
        "lastTradeTimestamp": ts, "isRealTime": True}}}


def _fetcher(by_url_part):
    calls = []

    def fetch(url):
        calls.append(url)
        for part, body in by_url_part.items():
            if part in url:
                if isinstance(body, Exception):
                    raise body
                return body
        return {"data": None}
    fetch.calls = calls
    return fetch


def test_parses_price_previous_close_and_asof():
    fetch = _fetcher({"/NFLX/info?assetclass=stocks": _info()})
    out = quotes.fetch_nasdaq_quotes(["NFLX"], fetch=fetch)
    q = out["NFLX"]
    assert q["price"] == 71.6993
    assert abs(q["previousClose"] - 71.72) < 1e-9
    assert q["asof"] == "Sep 25, 2026 9:02 AM ET"
    assert q["venue"] == "Nasdaq"


def test_commas_and_positive_change():
    fetch = _fetcher({"/BKNG/": _info(price="$5,123.40", change="+12.10")})
    q = quotes.fetch_nasdaq_quotes(["BKNG"], fetch=fetch)["BKNG"]
    assert q["price"] == 5123.40
    assert abs(q["previousClose"] - 5111.30) < 1e-9


def test_etf_is_asked_when_stocks_has_no_data():
    fetch = _fetcher({"/SPY/info?assetclass=etf": _info(price="$760.10",
                                                        change="1.10")})
    q = quotes.fetch_nasdaq_quotes(["SPY"], fetch=fetch)["SPY"]
    assert q["price"] == 760.10
    assert [u.split("assetclass=")[1] for u in fetch.calls] == ["stocks", "etf"]


def test_unknown_everywhere_is_none():
    fetch = _fetcher({})
    assert quotes.fetch_nasdaq_quotes(["ZZZZ"], fetch=fetch) == {"ZZZZ": None}


def test_non_us_symbols_are_skipped_without_a_request():
    fetch = _fetcher({})
    out = quotes.fetch_nasdaq_quotes(["ENX.PA", "BRK-B", "EURUSD=X", "^GSPC"],
                                     fetch=fetch)
    assert out == {"ENX.PA": None, "BRK-B": None, "EURUSD=X": None, "^GSPC": None}
    assert fetch.calls == []


def test_one_failure_does_not_take_the_others():
    fetch = _fetcher({"/MSFT/": RuntimeError("timeout"), "/NFLX/": _info()})
    out = quotes.fetch_nasdaq_quotes(["MSFT", "NFLX"], fetch=fetch)
    assert out["MSFT"] is None
    assert out["NFLX"]["price"] == 71.6993


def test_zero_or_garbage_price_is_none():
    fetch = _fetcher({"/AAA/": _info(price="N/A"), "/BBB/": _info(price="$0.00")})
    out = quotes.fetch_nasdaq_quotes(["AAA", "BBB"], fetch=fetch)
    assert out == {"AAA": None, "BBB": None}


def test_missing_change_gives_none_previous_close():
    fetch = _fetcher({"/NFLX/": _info(change="")})
    q = quotes.fetch_nasdaq_quotes(["NFLX"], fetch=fetch)["NFLX"]
    assert q["price"] == 71.6993 and q["previousClose"] is None


def test_duplicates_once_and_empty_input():
    fetch = _fetcher({"/NFLX/": _info()})
    out = quotes.fetch_nasdaq_quotes(["NFLX", "NFLX"], fetch=fetch)
    assert list(out) == ["NFLX"] and len(fetch.calls) == 1
    assert quotes.fetch_nasdaq_quotes([], fetch=fetch) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_nasdaq_quotes.py -q`
Expected: FAIL — `AttributeError: module 'quotes' has no attribute 'fetch_nasdaq_quotes'`

- [ ] **Step 3: Implement** — in `quotes.py`, add `import re` to the imports, then after `quote_age_minutes`:

```python
NASDAQ_URL = "https://api.nasdaq.com/api/quote/{symbol}/info?assetclass={cls}"
_NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_NASDAQ_TIMEOUT = 5
_NASDAQ_SYMBOL = re.compile(r"^[A-Z]{1,5}$")


def _nasdaq_symbol_ok(ticker) -> bool:
    """Alleen gewone VS-tickers. ENX.PA, BRK-B, EURUSD=X en ^GSPC kent Nasdaq
    niet (of onder een andere naam); daar gaat geen verzoek voor uit."""
    return isinstance(ticker, str) and bool(_NASDAQ_SYMBOL.match(ticker))


def _nasdaq_get(url: str) -> dict:
    """Eén verzoek, korte timeout en geen retries.

    Niet gather_data._http_get: die wacht 30 s en probeert vier keer, prima voor
    EDGAR maar te traag voor een koers die op een pagina moet staan. De
    SSL-context komt wel uit gather_data, om dezelfde reden als _http_get_json
    hierboven.
    """
    import json
    import urllib.request

    from gather_data import _ssl_ctx
    req = urllib.request.Request(url, headers=_NASDAQ_HEADERS)
    with urllib.request.urlopen(req, timeout=_NASDAQ_TIMEOUT,
                                context=_ssl_ctx) as resp:
        return json.loads(resp.read())


def _money(text) -> float | None:
    """'$5,123.40' / '+12.10' / '-0.02' naar float; alles anders None."""
    if not isinstance(text, str):
        return None
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _nasdaq_one(payload) -> dict | None:
    """Nasdaq-antwoord naar het koerscontract, of None."""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    primary = (data or {}).get("primaryData") if isinstance(data, dict) else None
    if not isinstance(primary, dict):
        return None
    price = _money(primary.get("lastSalePrice"))
    if price is None or price <= 0:
        return None
    change = _money(primary.get("netChange"))
    return {
        "price": price,
        "previousClose": price - change if change is not None else None,
        "asof": primary.get("lastTradeTimestamp"),
        "venue": "Nasdaq",
    }


def fetch_nasdaq_quotes(tickers, fetch=None, max_workers: int = 8) -> dict:
    """{ticker: {"price","previousClose","asof","venue"} | None} per ticker.

    Waarom Nasdaq: Yahoo blokkeert op user-agent en bron-IP (2026-09-25: 429
    vanaf Google Cloud), en de broker-feed werkt alleen voor wie met
    Tastytrade is ingelogd -- niet voor de MCP op Cloud Run en niet voor
    gebruikers zonder Tastytrade. Nasdaq geeft zonder sleutel een realtime
    koers en antwoordt vanaf Google Cloud.

    Eerst als aandeel, dan als ETF (SPY staat alleen onder etf). Elke
    gevraagde ticker staat in het antwoord, ook als hij niets opleverde.
    """
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}
    fetch = fetch or _nasdaq_get

    def one(ticker):
        if not _nasdaq_symbol_ok(ticker):
            return ticker, None
        try:
            for cls in ("stocks", "etf"):
                quote = _nasdaq_one(fetch(NASDAQ_URL.format(symbol=ticker, cls=cls)))
                if quote is not None:
                    return ticker, quote
        except Exception as e:
            logger.warning("Nasdaq-koers voor %s mislukt: %s: %s",
                           ticker, type(e).__name__, e)
        return ticker, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(pool.map(one, tickers))
```

- [ ] **Step 4: Run tests** — `python3 -m pytest tests/test_nasdaq_quotes.py tests/test_quotes.py -q` → all PASS; `python3 -m ruff check quotes.py tests/test_nasdaq_quotes.py` → clean.

- [ ] **Step 5: Commit**

```bash
git add quotes.py tests/test_nasdaq_quotes.py
git commit -m "quotes: Nasdaq live-quote source"
```

---

### Task 2: The chain, broker_adapter on top of it, ticker page on the chain

**Files:**
- Modify: `quotes.py` (add `live_quotes` after `fetch_nasdaq_quotes`)
- Modify: `broker_adapter.py:570-617` (`fetch_current_prices`)
- Modify: `streamlit_app.py` ticker-page `_price` (the `# ── Live price ──` block, ≈ line 5095-5107)
- Modify: `test_broker_adapter.py` (the class containing `test_broker_covers_everything_yahoo_not_called`, ≈ line 375)
- Create: `tests/test_live_quotes.py`

**Interfaces:**
- Consumes: `quotes.fetch_nasdaq_quotes(tickers)` (Task 1); `quotes.fetch_frankfurt_quotes(isin_by_ticker)`; `tastytrade_api.fetch_current_prices(tickers)` (Yahoo); `tastytrade_api.fetch_quotes_via_broker(tickers, refresh_token=...)`.
- Produces: `quotes.live_quotes(tickers, broker=None, isin_by_ticker=None, nasdaq=None, yahoo=None, frankfurt=None) -> dict[str, dict|None]`. `broker_adapter.fetch_current_prices(tickers, isin_by_ticker=None)` unchanged signature.

- [ ] **Step 1: Write failing chain tests** — `tests/test_live_quotes.py`:

```python
"""De koersketen: broker -> Nasdaq -> Yahoo -> Frankfurt, offline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quotes


def q(price):
    return {"price": price, "previousClose": price - 1, "asof": None, "venue": "x"}


class Source:
    def __init__(self, answers=None, boom=False):
        self.answers, self.boom, self.calls = answers or {}, boom, []

    def __call__(self, arg):
        self.calls.append(arg)
        if self.boom:
            raise RuntimeError("down")
        keys = arg.keys() if isinstance(arg, dict) else arg
        return {t: self.answers.get(t) for t in keys}


def test_each_step_only_gets_what_the_previous_missed():
    broker = Source({"MSFT": q(451)})
    nasdaq = Source({"NFLX": q(72)})
    yahoo = Source({"RMS.PA": q(1412)})
    frank = Source({})
    out = quotes.live_quotes(["MSFT", "NFLX", "RMS.PA"], broker=broker,
                             nasdaq=nasdaq, yahoo=yahoo, frankfurt=frank,
                             isin_by_ticker={"RMS.PA": "FR0000052292"})
    assert broker.calls == [["MSFT", "NFLX", "RMS.PA"]]
    assert nasdaq.calls == [["NFLX", "RMS.PA"]]
    assert yahoo.calls == [["RMS.PA"]]
    assert frank.calls == []
    assert [out[t]["price"] for t in ("MSFT", "NFLX", "RMS.PA")] == [451, 72, 1412]


def test_frankfurt_only_for_isin_tickers():
    frank = Source({"RMS.PA": q(1412)})
    out = quotes.live_quotes(["RMS.PA", "ENX.PA"], nasdaq=Source(), yahoo=Source(),
                             frankfurt=frank,
                             isin_by_ticker={"RMS.PA": "FR0000052292"})
    assert frank.calls == [{"RMS.PA": "FR0000052292"}]
    assert out["RMS.PA"]["price"] == 1412 and out["ENX.PA"] is None


def test_a_failing_step_falls_through():
    yahoo = Source({"NFLX": q(72)})
    out = quotes.live_quotes(["NFLX"], broker=Source(boom=True),
                             nasdaq=Source(boom=True), yahoo=yahoo,
                             frankfurt=Source())
    assert out["NFLX"]["price"] == 72


def test_zero_price_counts_as_missing():
    yahoo = Source({"NFLX": q(72)})
    out = quotes.live_quotes(["NFLX"], nasdaq=Source({"NFLX": q(0)}), yahoo=yahoo,
                             frankfurt=Source())
    assert yahoo.calls == [["NFLX"]] and out["NFLX"]["price"] == 72


def test_every_ticker_is_a_key_in_input_order_once():
    out = quotes.live_quotes(["B", "A", "B"], nasdaq=Source(), yahoo=Source(),
                             frankfurt=Source())
    assert list(out) == ["B", "A"] and out == {"B": None, "A": None}
    assert quotes.live_quotes([]) == {}


def test_no_broker_starts_at_nasdaq():
    nasdaq = Source({"NFLX": q(72)})
    yahoo = Source()
    quotes.live_quotes(["NFLX"], nasdaq=nasdaq, yahoo=yahoo, frankfurt=Source())
    assert nasdaq.calls == [["NFLX"]] and yahoo.calls == []
```

- [ ] **Step 2: Run** — `python3 -m pytest tests/test_live_quotes.py -q` → FAIL (`no attribute 'live_quotes'`).

- [ ] **Step 3: Implement `live_quotes`** in `quotes.py` after `fetch_nasdaq_quotes`:

```python
def _default_yahoo(tickers):
    import tastytrade_api  # laat: zwaar pakket, en de tests hebben het niet nodig
    return tastytrade_api.fetch_current_prices(tickers)


def live_quotes(tickers, broker=None, isin_by_ticker=None,
                nasdaq=None, yahoo=None, frankfurt=None) -> dict:
    """{ticker: quote | None}: de hele keten, elke bron alleen voor de gaten.

    Volgorde: broker-feed (alleen als meegegeven -- geauthenticeerd, dus niet
    aan een IP gebonden) -> Nasdaq (VS-tickers, zonder sleutel) -> Yahoo (vangt
    wat Nasdaq niet kent, zolang hij ons nog bedient) -> Frankfurt (alleen
    tickers met een ISIN, in de praktijk de Europese lijnen).

    Een bron die omvalt telt als "niets gevonden" en wordt gelogd; de keten
    zelf gooit nooit. Een koers van 0 telt niet als koers.
    """
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}
    isin_by_ticker = isin_by_ticker or {}
    steps = [("broker", broker),
             ("nasdaq", nasdaq or fetch_nasdaq_quotes),
             ("yahoo", yahoo or _default_yahoo)]

    out = dict.fromkeys(tickers)
    for name, source in steps:
        missing = [t for t in tickers if _live_price(out[t]) is None]
        if not missing or source is None:
            continue
        found = _ask(source, missing, name)
        for t in missing:
            if _live_price(found.get(t)) is not None:
                out[t] = found[t]

    gaps = {t: isin_by_ticker[t] for t in tickers
            if _live_price(out[t]) is None and isin_by_ticker.get(t)}
    if gaps:
        found = _ask(frankfurt or fetch_frankfurt_quotes, gaps, "frankfurt")
        for t in gaps:
            if _live_price(found.get(t)) is not None:
                out[t] = found[t]

    return {t: (out[t] if _live_price(out[t]) is not None else None)
            for t in tickers}
```

- [ ] **Step 4: Run** — `python3 -m pytest tests/test_live_quotes.py tests/test_nasdaq_quotes.py -q` → PASS.

- [ ] **Step 5: Rewrite `broker_adapter.fetch_current_prices`.** Keep the existing docstring's history paragraphs and add one line that Nasdaq now sits between broker and Yahoo. Body:

```python
    tickers = list(tickers)
    if not tickers:
        return {}

    broker = None
    if st.session_state.get("tt_refresh_token"):
        def broker(ts):
            return tastytrade_api.fetch_quotes_via_broker(
                ts, refresh_token=_get_refresh_token())

    # Yahoo via een lambda, zodat de lookup van tastytrade_api.fetch_current_prices
    # pas bij de aanroep gebeurt (tests patchen dat attribuut).
    return quotes.live_quotes(
        tickers, broker=broker, isin_by_ticker=isin_by_ticker,
        yahoo=lambda ts: tastytrade_api.fetch_current_prices(ts))
```

Add `import quotes` at the top of `broker_adapter.py` (module-level) and remove the function-local `import quotes`. Output contract stays `{t: quote|None}` for every requested ticker.

- [ ] **Step 6: Keep broker_adapter tests offline.** In `test_broker_adapter.py`, in the test class that contains `test_broker_covers_everything_yahoo_not_called`, add:

```python
    def setUp(self):
        # Nasdaq zit nu tussen broker en Yahoo; deze tests gaan over de
        # broker/Yahoo-splitsing, dus Nasdaq levert hier niets (en gaat niet
        # het net op).
        patcher = patch.object(broker_adapter.quotes, "fetch_nasdaq_quotes",
                               return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)
```

If the class already has a `setUp`, append the patcher lines to it. Then add one test to that class:

```python
    def test_nasdaq_fills_before_yahoo(self):
        with patch.object(broker_adapter, "st", self._st(connected=False)), \
             patch.object(broker_adapter.quotes, "fetch_nasdaq_quotes",
                          return_value={"NFLX": {"price": 72.0,
                                                 "previousClose": 71.0,
                                                 "asof": None,
                                                 "venue": "Nasdaq"}}), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_current_prices") as yahoo:
            out = broker_adapter.fetch_current_prices(["NFLX"])
        yahoo.assert_not_called()
        self.assertAlmostEqual(out["NFLX"]["price"], 72.0)
```

Note: `live_quotes` resolves `nasdaq or fetch_nasdaq_quotes` at call time from the `quotes` module globals, so patching `quotes.fetch_nasdaq_quotes` works. Verify `test_yahoo_fills_only_what_the_broker_missed` still asserts `yahoo.assert_called_once_with(["RMS.PA"])` and passes.

- [ ] **Step 7: Ticker page on the chain.** Replace the `_price` helper in the `# ── Live price ──` block of `streamlit_app.py` with:

```python
    # ── Live price ──
    # Dezelfde keten als de watchlist (broker -> Nasdaq -> Yahoo -> Frankfurt).
    # Dit vroeg eerst alleen Yahoo, en dat blokkeert Streamlit Cloud: de pagina
    # viel dan stil terug op de koers van de laatste config-schrijfbeurt.
    @st.cache_data(ttl=30)
    def _price(t, isin=None):
        try:
            q = fetch_current_prices([t], {t: isin} if isin else None).get(t)
            return float(q["price"]) if q and q.get("price") else 0.0
        except Exception as e:
            logger.debug("Stock price fetch failed for %s: %s", t, e)
            return 0.0

    live_price = _price(ticker, cfg.get("isin"))
```

(`fetch_current_prices` is already imported at the top of `streamlit_app.py` from `broker_adapter`.) Leave the following `if live_price > 0: cfg['stock_price'] = live_price` unchanged. If `fetch_stock_price` is no longer used anywhere in `streamlit_app.py`, leave its import alone only if ruff does not flag it; otherwise remove it from the import list.

- [ ] **Step 8: Run** — `python3 -m pytest test_broker_adapter.py tests/test_live_quotes.py tests/test_nasdaq_quotes.py tests/test_quotes.py -q` → PASS; `python3 -m ruff check .` → clean.

- [ ] **Step 9: Commit**

```bash
git add quotes.py broker_adapter.py streamlit_app.py test_broker_adapter.py tests/test_live_quotes.py
git commit -m "One quote chain: broker, Nasdaq, Yahoo, Frankfurt; ticker page uses it"
```

---

### Task 3: fetch_stock_price asks Nasdaq first; MCP and routine no longer need a price

**Files:**
- Modify: `gather_data.py:843-862` (`fetch_stock_price`) and the error message in `build_base_config` (≈ line 2561-2563)
- Modify: `mcp_server.py` (`_yahoo_quote` ≈ 397-400; `add_aspirant` docstring ≈ 1605-1610)
- Modify: `lazytheta-mcp-cloudrun/mcp_handler.py` (`add_aspirant` tool description ≈ line 712)
- Modify: `docs/routines/aspirant-weekly.md` (step 2a)
- Create: `tests/test_fetch_stock_price_chain.py`

**Interfaces:**
- Consumes: `quotes.fetch_nasdaq_quotes(tickers)` (Task 1).
- Produces: `gather_data.fetch_stock_price(ticker) -> (price, 0, 0)`; `mcp_server._live_quote(ticker)` (renamed from `_yahoo_quote`).

Ruling recorded here: the spec says fetch_stock_price goes "via live_quotes([ticker])". Without a broker and ISIN that chain is exactly Nasdaq → Yahoo; calling `fetch_nasdaq_quotes` then the existing Yahoo code directly gives the same behaviour without routing Yahoo through `tastytrade_api` (a second Yahoo implementation) and without an import cycle.

- [ ] **Step 1: Write failing tests** — `tests/test_fetch_stock_price_chain.py`:

```python
"""fetch_stock_price: Nasdaq eerst, Yahoo als terugval, (0,0,0) bij niets."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gather_data
import quotes


def test_nasdaq_hit_skips_yahoo(monkeypatch):
    monkeypatch.setattr(quotes, "fetch_nasdaq_quotes",
                        lambda ts: {"NFLX": {"price": 72.0}})
    def no_yahoo(*a, **k):
        raise AssertionError("Yahoo should not be asked")
    monkeypatch.setattr(gather_data, "_http_get_json", no_yahoo)
    assert gather_data.fetch_stock_price("NFLX") == (72.0, 0, 0)


def test_nasdaq_miss_falls_back_to_yahoo(monkeypatch):
    monkeypatch.setattr(quotes, "fetch_nasdaq_quotes", lambda ts: {"ENX.PA": None})
    monkeypatch.setattr(
        gather_data, "_http_get_json",
        lambda url, headers=None: {"chart": {"result": [
            {"meta": {"regularMarketPrice": 101.5}}]}})
    assert gather_data.fetch_stock_price("ENX.PA") == (101.5, 0, 0)


def test_nasdaq_blowing_up_falls_back_to_yahoo(monkeypatch):
    def boom(ts):
        raise RuntimeError("down")
    monkeypatch.setattr(quotes, "fetch_nasdaq_quotes", boom)
    monkeypatch.setattr(
        gather_data, "_http_get_json",
        lambda url, headers=None: {"chart": {"result": [
            {"meta": {"regularMarketPrice": 50.0}}]}})
    assert gather_data.fetch_stock_price("MSFT") == (50.0, 0, 0)


def test_everything_down_is_zero(monkeypatch):
    monkeypatch.setattr(quotes, "fetch_nasdaq_quotes", lambda ts: {"MSFT": None})
    def http_fail(url, headers=None):
        raise RuntimeError("429")
    monkeypatch.setattr(gather_data, "_http_get_json", http_fail)
    assert gather_data.fetch_stock_price("MSFT") == (0, 0, 0)
```

Before writing the last test, read the current `except` branch of `fetch_stock_price` (after line 859) and confirm it returns `(0, 0, 0)`; if it returns something else, match the test to the existing behaviour and note it in the report.

- [ ] **Step 2: Run** — `python3 -m pytest tests/test_fetch_stock_price_chain.py -q` → `test_nasdaq_hit_skips_yahoo` FAILS (Yahoo asked).

- [ ] **Step 3: Implement.** At the top of `fetch_stock_price`'s body (keep the Yahoo code below it unchanged), and update the docstring:

```python
def fetch_stock_price(ticker):
    """Fetch the current stock price: Nasdaq first, Yahoo chart API as fallback.

    Nasdaq answers from datacenter IPs (Cloud Run included) without a key;
    Yahoo blocks by user-agent and source IP and now mostly serves as the
    route for symbols Nasdaq does not know (European lines, FX pairs like
    EURUSD=X). Returns (price, 0, 0) — market cap and shares are calculated
    later from EDGAR data. (0, 0, 0) when nothing answers.
    """
    import quotes  # late: quotes imports gather_data for its SSL context
    try:
        quote = quotes.fetch_nasdaq_quotes([ticker]).get(ticker)
    except Exception as e:
        print(f"  WARNING: Nasdaq quote failed for {ticker}: {e}")
        quote = None
    if quote and quote.get("price"):
        print(f"[Nasdaq] {ticker}: ${quote['price']:.2f}")
        return quote["price"], 0, 0

    print(f"[Yahoo] Fetching stock price for {ticker}...")
    # ... existing Yahoo code unchanged ...
```

In `build_base_config`, change the error text to:
`f"No price for {ticker}: Nasdaq and Yahoo both failed; pass stock_price"`.

- [ ] **Step 4: MCP wording.** In `mcp_server.py` rename `_yahoo_quote` → `_live_quote` (update its one caller in `_get_watchlist_impl`), docstring: `"""Eén koers via fetch_stock_price (Nasdaq, dan Yahoo), in het koerscontract."""`. In `add_aspirant`'s docstring replace "Pass stock_price: Yahoo is blocked on this server." with "stock_price is optional: when omitted the server fetches it (Nasdaq, then Yahoo)." In `lazytheta-mcp-cloudrun/mcp_handler.py` replace "Pass stock_price (Yahoo is blocked here)." with "stock_price is optional; the server fetches it when omitted." Check `grep -n "_yahoo_quote" -r . --include=*.py` returns nothing afterwards (tests included — update any test reference).

- [ ] **Step 5: Routine doc.** In `docs/routines/aspirant-weekly.md` step 2a, replace the first two sentences with:
"a. Call `add_aspirant(ticker)`; the server fetches the price itself. Only if it returns an error mentioning `No price`, get the price with the SEC connector's `GetLiveQuote` and call `add_aspirant(ticker, stock_price)` once more."
Keep the rest of 2a (skip on error/already exists, never `set_category` etc.) unchanged.

- [ ] **Step 6: Run everything** — `python3 -m pytest -q --ignore=lazytheta-mcp-cloudrun --ignore=tastytrade-mcp-cloudrun` → only the known failure `tests/test_market_data.py::test_fetch_dividend_history_full_5y_payer` may fail; `cd lazytheta-mcp-cloudrun && python3 -m pytest -q` → PASS; `python3 -m ruff check .` → clean. Tests that patch `gather_data.fetch_stock_price` are unaffected. If any test calls the real `fetch_stock_price` with a mocked `_http_get_json` only (Yahoo shape), it would now reach the network via Nasdaq: patch `quotes.fetch_nasdaq_quotes` to `lambda ts: {}` in that test.

- [ ] **Step 7: Commit**

```bash
git add gather_data.py mcp_server.py lazytheta-mcp-cloudrun/mcp_handler.py docs/routines/aspirant-weekly.md tests/test_fetch_stock_price_chain.py
git commit -m "fetch_stock_price asks Nasdaq first; MCP no longer needs a passed price"
```

---

## Deploy (controller, after merge)

1. `git -c credential.helper='!gh auth git-credential' push -q origin main`; verify `git ls-remote origin main` = local HEAD.
2. `gcloud run deploy lazytheta-mcp --source . --region europe-west4 --project stock-analysis-489016`.
3. Live checks: MCP `get_watchlist` shows fresh prices; `build_dcf_config`-path via `add_aspirant` not needed. Ask the user to Reboot Streamlit, then open a ticker page and compare the price to the market.
