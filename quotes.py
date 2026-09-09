"""Koersen van Deutsche Boerse, voor de lijnen die Yahoo niet levert.

Waarom deze module bestaat: Yahoo blokkeert op bron-IP en heeft die reeksen
opgerekt -- Cloud Run in juni, de Streamlit-app in september. De broker-feed
vangt dat op voor de VS-noteringen, want die is geauthenticeerd en niet aan een
IP gebonden. Wat overbleef waren de Europese lijnen: RMS.PA, ENX.PA, AIR.PA en
RHM.DE hadden geen tweede bron en vielen dus terug op de koers uit hun laatste
config-schrijfbeurt. Op 2026-09-09 wees de watchlist voor Hermes 1613,50
terwijl de markt op 1412 stond -- 14% ernaast, en niets zei dat.

Let op wat hier *niet* staat: Yahoo kan die Europese tickers prima leveren.
Vanaf een IP dat hij nog bedient komen RMS.PA en RHM.DE gewoon binnen, op een
paar tienden na gelijk aan Xetra. Dit is dus geen dekkingsprobleem maar een
herkomstprobleem, en deze module is het vangnet voor de hosts waar Yahoo
dichtzit -- niet zijn vervanger.

Deutsche Boerse levert die koersen wel, zonder sleutel. Twee dingen om in de
gaten te houden:

1. **Xetra is voor een Parijse notering een secundaire markt.** De koers wijkt
   licht af van de thuismarkt. Bij liquide namen is dat een fractie van een
   procent; het alternatief was een koers van weken oud.
2. **Dunne lijnen lopen achter.** ENX.PA's laatste Xetra-transactie was ruim
   twee uur oud terwijl de rest bijna real-time was. Daarom draagt elke koers
   zijn eigen `asof` en is er quote_age_minutes: de aanroeper kan "twee uur oud"
   laten zien in plaats van het te verzwijgen. Een oude koers die zich voordoet
   als verse is precies hoe de watchlist in stilte 14% naast de markt kwam.

De valuta staat niet in het antwoord van de beurs -- Xetra noteert in EUR en
zegt dat niet. `venue` gaat daarom mee, zodat de aanroeper weet waar de koers
vandaan komt in plaats van het af te leiden.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

QUOTE_URL = "https://api.boerse-frankfurt.de/v1/data/quote_box/single"
DEFAULT_MIC = "XETR"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json",
}
_TIMEOUT = 8

# Sentinel: None is een geldige gecachete waarde (een bekende misser), dus
# "niet in de cache" heeft een eigen teken nodig.
_MISSING = object()


def _http_get_json(url: str) -> dict:
    """Via gather_data, dat de SSL-context van dit project bijhoudt.

    Niet zelf een context bouwen: gather_data laadt certifi met een terugval
    voor macOS zonder cert-install, en een tweede kopie van diezelfde waarheid
    heeft in dit project al drie keer een bug opgeleverd. Laat geimporteerd,
    zodat de tests deze module zonder dat zware pakket kunnen laden.
    """
    from gather_data import _http_get_json as _get
    return _get(url, _HEADERS)


def _one(payload: dict, mic: str) -> dict | None:
    """Eén beursantwoord naar het koerscontract, of None.

    None en niet 0: dit project rekent upside, P/E en FCF-yield uit de koers,
    en een 0 zakt daar ongemerkt doorheen als een echt getal. Zie de
    docstring van _resolve_watchlist_price -- daar is die fout al eens gemaakt.
    """
    if not isinstance(payload, dict):
        return None
    last = payload.get("lastPrice")
    if not isinstance(last, (int, float)) or last <= 0:
        return None

    change = payload.get("changeToPrevDayAbsolute")
    previous = (float(last) - float(change)
                if isinstance(change, (int, float)) else None)

    return {
        "price": float(last),
        "previousClose": previous,
        "asof": payload.get("timestampLastPrice"),
        "venue": mic,
    }


def fetch_frankfurt_quotes(isin_by_ticker: dict, mic: str = DEFAULT_MIC,
                           fetch=None, max_workers: int = 8) -> dict:
    """{ticker: {"price","previousClose","asof","venue"} | None} per ticker.

    `isin_by_ticker` mapt ticker op ISIN -- de beurs kent geen tickers. Een
    ticker zonder ISIN levert None zonder dat er een verzoek uitgaat.

    Elke gevraagde ticker staat in het antwoord, ook als hij niets opleverde.
    Een ontbrekende sleutel dwingt de aanroeper tot .get() en dan glipt een gat
    er stil doorheen; een expliciete None dwingt hem het onder ogen te zien.
    """
    if not isin_by_ticker:
        return {}
    fetch = fetch or _http_get_json

    def one(item):
        ticker, isin = item
        if not isin:
            return ticker, None
        try:
            payload = fetch(f"{QUOTE_URL}?isin={isin}&mic={mic}")
        except Exception as e:
            # Eén hapering mag de andere namen niet meenemen; de aanroeper
            # ziet None en valt terug op wat hij verder heeft.
            #
            # Maar wél luidruchtig. Deze except ving eerst alles stil af, en
            # een ontbrekende CA-bundle las daardoor als "deze beurs kent die
            # notitie niet" -- een storing vermomd als data, dezelfde fout die
            # notes-mcp op 2026-09-08 een sessie lang de verkeerde bron in
            # stuurde. Wie None ziet moet in de log kunnen vinden waarom.
            logger.warning("Frankfurt-koers voor %s (%s) mislukt: %s: %s",
                           ticker, isin, type(e).__name__, e)
            return ticker, None
        return ticker, _one(payload, mic)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(pool.map(one, isin_by_ticker.items()))


def quote_age_minutes(quote: dict | None, now: datetime | None = None):
    """Hoe oud is deze koers, in minuten? None als dat niet te zeggen is.

    Onbekende ouderdom is een geldig antwoord. Een exceptie halverwege het
    opbouwen van de tabel is dat niet, dus rommel in het tijdstempel levert
    None op en geen crash.
    """
    if not isinstance(quote, dict):
        return None
    stamp = quote.get("asof")
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return (now - parsed).total_seconds() / 60.0


def _live_price(quote) -> float | None:
    """De koers uit een bronantwoord, of None. Nul telt niet als koers."""
    if not isinstance(quote, dict):
        return None
    price = quote.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        return None
    return float(price)


def _ask(source, argument, what: str):
    """Een bron bevragen zonder dat zijn uitval de lijst meeneemt."""
    if source is None:
        return {}
    try:
        return source(argument) or {}
    except Exception as e:
        logger.warning("koersbron %s mislukt: %s: %s", what, type(e).__name__, e)
        return {}


def apply_live_prices(entries: list, primary=None, frankfurt=None) -> list:
    """Zet een live koers op elke watchlist-rij, met de herkomst erbij.

    `primary` is de gewone route (broker-feed of Yahoo) en gaat voor.
    `frankfurt` vult alleen de gaten die daar vallen, en alleen voor rijen die
    een ISIN dragen -- in de praktijk de Europese lijnen.

    Elke rij krijgt er drie velden bij:

        price_source  "primary" | de beurscode | "stored"
        price_asof    tijdstempel van de bron, of None
        price_stale   True zodra de opgeslagen koers is blijven staan

    Lukt geen van beide bronnen, dan blijft de opgeslagen koers staan met
    `price_stale: True`. Niet 0: upside, P/E en FCF-yield worden uit dit getal
    gerekend en stortten met een 0 mee in, waardoor een rij zonder koers als
    een echt oordeel las.

    `current_vs_mid` wordt herrekend zodra er een verse koers is. Dat veld
    stond bevroren op de koers van de laatste wáárdering en was daarmee nog
    ouder dan stock_price zelf; een verse koers naast een oude upside laat twee
    getallen op dezelfde regel elkaar tegenspreken. Bij een stale rij blijft
    het staan zoals het was.
    """
    if not entries:
        return entries

    tickers = [e.get("ticker") for e in entries if e.get("ticker")]
    primary_quotes = _ask(primary, tickers, "primary")

    gaps = {e["ticker"]: e.get("isin") for e in entries
            if e.get("ticker") and e.get("isin")
            and _live_price(primary_quotes.get(e["ticker"])) is None}
    frankfurt_quotes = _ask(frankfurt, gaps, "frankfurt") if gaps else {}

    for entry in entries:
        ticker = entry.get("ticker")
        quote = primary_quotes.get(ticker)
        price = _live_price(quote)
        source = "primary"

        if price is None:
            quote = frankfurt_quotes.get(ticker)
            price = _live_price(quote)
            source = (quote or {}).get("venue") or "frankfurt"

        if price is None:
            entry["price_source"] = "stored"
            entry["price_asof"] = None
            entry["price_stale"] = True
            continue

        entry["stock_price"] = price
        entry["price_source"] = source
        entry["price_asof"] = (quote or {}).get("asof")
        entry["price_stale"] = False

        fv_mid = entry.get("fv_mid")
        entry["current_vs_mid"] = (price / fv_mid - 1
                                   if isinstance(fv_mid, (int, float)) and fv_mid
                                   else None)
    return entries


class TtlCache:
    """Koersen kort onthouden, inclusief de missers.

    Zonder dit doet elke get_watchlist een ronde langs elke ticker. Bij een
    watchlist van tegen de negentig namen is dat precies het verkeer waarmee
    Streamlit Cloud bij Yahoo op de zwarte lijst kwam -- en die blokkade is de
    reden dat deze module er is. Hem herhalen vanaf Cloud Run zou het laatste
    werkende pad ook dichtgooien.

    Missers worden net zo goed onthouden: anders wordt juist het verkeer dat
    niets oplevert bij elke aanroep opnieuw gedaan.
    """

    def __init__(self, ttl_seconds: float = 60.0):
        self._ttl = ttl_seconds
        self._entries: dict = {}
        self._lock = threading.Lock()
        self._now = time.monotonic

    def get(self, key, default=_MISSING):
        with self._lock:
            hit = self._entries.get(key)
            if hit is None or self._now() - hit[0] >= self._ttl:
                return default
            return hit[1]

    def put(self, key, value):
        with self._lock:
            self._entries[key] = (self._now(), value)


def parallel_quotes(tickers, fetch_one, cache: TtlCache | None = None,
                    max_workers: int = 8) -> dict:
    """{ticker: quote|None} door fetch_one per ticker, parallel en begrensd.

    `max_workers` is bewust laag. De bron die dit bedient blokkeert op bron-IP,
    en een burst van negentig gelijktijdige verzoeken is precies waar dat op
    afgaat.
    """
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}

    out, todo = {}, []
    for ticker in tickers:
        hit = cache.get(ticker) if cache is not None else _MISSING
        if hit is _MISSING:
            todo.append(ticker)
        else:
            out[ticker] = hit

    def one(ticker):
        try:
            return ticker, fetch_one(ticker)
        except Exception as e:
            logger.warning("koers voor %s mislukt: %s: %s",
                           ticker, type(e).__name__, e)
            return ticker, None

    if todo:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for ticker, quote in pool.map(one, todo):
                out[ticker] = quote
                if cache is not None:
                    cache.put(ticker, quote)
    return out
