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
import re
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
        "currency": "EUR",     # Xetra noteert in EUR, ook voor een Tokio-lijn
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


def _nasdaq_block(block) -> tuple:
    """(price, netChange, asof) uit één data-blok (primary/secondaryData).

    None-tuple als het blok ontbreekt of geen bruikbare koers draagt --
    secondaryData is null zolang de gewone sessie nog loopt, en dat moet als
    "geen koers" lezen, niet als een crash op .get() van None."""
    if not isinstance(block, dict):
        return None, None, None
    price = _money(block.get("lastSalePrice"))
    if price is None or price <= 0:
        return None, None, None
    return price, _money(block.get("netChange")), block.get("lastTradeTimestamp")


def _nasdaq_one(payload) -> dict | None:
    """Nasdaq-antwoord naar het koerscontract, of None.

    Buiten de reguliere sessie is primaryData de laatste print van de
    pre-market/after-hours-handel, niet de koers van de vorige sluiting --
    en dat is dan precies de gevraagde koers. secondaryData draagt in dat
    geval de laatste koers van de reguliere sessie zelf, in dezelfde velden,
    en kan null zijn zolang die sessie nog bezig is. Bij "Market Open" (of een
    ontbrekende marketStatus, voor oudere/andere antwoorden) is primaryData
    juist de koers van de lopende sessie.
    """
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    primary = data.get("primaryData")
    secondary = data.get("secondaryData")
    status = data.get("marketStatus")

    if status and status != "Market Open":
        price, change, asof = _nasdaq_block(secondary)
        if price is None:
            price, change, asof = _nasdaq_block(primary)
    else:
        price, change, asof = _nasdaq_block(primary)

    if price is None:
        return None
    return {
        "price": price,
        "previousClose": price - change if change is not None else None,
        "asof": asof,
        "venue": "Nasdaq",
    }


# ---------------------------------------------------------------------------
# Breaker: Nasdaq drie minuten overslaan na drie storingen op rij
# ---------------------------------------------------------------------------
#
# Nasdaq kan blijven hangen of ons blokkeren zonder dat de timeout dat meteen
# laat merken; elke aanvraag betaalt dan zijn eigen _NASDAQ_TIMEOUT seconden
# voor niets, voor iedere ticker op de watchlist. Drie storingen op rij zijn
# geen toeval meer -- daarna wordt Nasdaq voor even overgeslagen in plaats van
# elke ticker opnieuw te laten wachten en falen.
_NASDAQ_BREAKER_THRESHOLD = 3
_NASDAQ_BREAKER_COOLDOWN = 300.0  # seconden

_nasdaq_breaker_lock = threading.Lock()
_nasdaq_breaker_fail_count = 0
_nasdaq_breaker_opened_at: float | None = None
_nasdaq_clock = time.monotonic  # injecteerbaar voor tests, geen sleep nodig


def _nasdaq_breaker_reset() -> None:
    """Teller en open-status terugzetten. Voor tests, zodat de storingen van
    de ene test de volgende niet in stilte meenemen."""
    global _nasdaq_breaker_fail_count, _nasdaq_breaker_opened_at
    with _nasdaq_breaker_lock:
        _nasdaq_breaker_fail_count = 0
        _nasdaq_breaker_opened_at = None


def _nasdaq_breaker_is_open() -> bool:
    with _nasdaq_breaker_lock:
        if _nasdaq_breaker_opened_at is None:
            return False
        return _nasdaq_clock() - _nasdaq_breaker_opened_at < _NASDAQ_BREAKER_COOLDOWN


def _nasdaq_breaker_record(ok: bool) -> None:
    """Eén ruw fetch-antwoord boeken. `ok` is of het verzoek is beantwoord --
    ook data:null telt als beantwoord, alleen een exceptie (timeout, verbroken
    verbinding) is een storing."""
    global _nasdaq_breaker_fail_count, _nasdaq_breaker_opened_at
    with _nasdaq_breaker_lock:
        if ok:
            _nasdaq_breaker_fail_count = 0
            _nasdaq_breaker_opened_at = None
            return
        _nasdaq_breaker_fail_count += 1
        if _nasdaq_breaker_fail_count >= _NASDAQ_BREAKER_THRESHOLD:
            _nasdaq_breaker_opened_at = _nasdaq_clock()


def fetch_nasdaq_quotes(tickers, fetch=None, max_workers: int = 8) -> dict:
    """{ticker: {"price","previousClose","asof","venue"} | None} per ticker.

    Waarom Nasdaq: Yahoo blokkeert op user-agent en bron-IP (2026-09-25: 429
    vanaf Google Cloud), en de broker-feed werkt alleen voor wie met
    Tastytrade is ingelogd -- niet voor de MCP op Cloud Run en niet voor
    gebruikers zonder Tastytrade. Nasdaq geeft zonder sleutel een realtime
    koers en antwoordt vanaf Google Cloud.

    Eerst als aandeel, dan als ETF (SPY staat alleen onder etf). Elke
    gevraagde ticker staat in het antwoord, ook als hij niets opleverde.

    Staat de breaker open (drie storingen op rij, zie hierboven) dan gaat er
    geen verzoek uit en komt elke ticker als None terug.
    """
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}
    fetch = fetch or _nasdaq_get

    if _nasdaq_breaker_is_open():
        logger.warning("Nasdaq overgeslagen: breaker open na %d storingen op rij",
                       _NASDAQ_BREAKER_THRESHOLD)
        return dict.fromkeys(tickers)

    def one(ticker):
        if not _nasdaq_symbol_ok(ticker):
            return ticker, None
        try:
            for cls in ("stocks", "etf"):
                payload = fetch(NASDAQ_URL.format(symbol=ticker, cls=cls))
                _nasdaq_breaker_record(True)
                quote = _nasdaq_one(payload)
                if quote is not None:
                    return ticker, quote
        except Exception as e:
            _nasdaq_breaker_record(False)
            logger.warning("Nasdaq-koers voor %s mislukt: %s: %s",
                           ticker, type(e).__name__, e)
        return ticker, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(pool.map(one, tickers))


# ── stockanalysis.com: de buitenlandse lijnen ──────────────────────────
#
# Yahoo is op Cloud Run en Streamlit Cloud dicht, Nasdaq kent alleen
# VS-tickers en Frankfurt noteert alles in EUR -- voor 3064.T of RMV.L was er
# dus geen bron (2026-10-22). stockanalysis.com zet de koers in de pagina en
# zegt er de munt bij ("Price in GBX" / "Currency is JPY"). Getest vanaf Cloud
# Run; robots.txt staat /quote/ toe. Alleen de standaardbibliotheek.

SA_URL = "https://stockanalysis.com/quote/{exchange}/{symbol}/"
_SA_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126"}
_SA_TIMEOUT = 8
# Yahoo-suffix -> beurscode in de URL van stockanalysis.com
SA_EXCHANGE = {
    "T": "tyo", "L": "lon", "PA": "epa", "AS": "ams", "DE": "etr", "MI": "bit",
    "SW": "swx", "CO": "cph", "ST": "sto", "OL": "osl", "HE": "hel", "MC": "bme",
    "BR": "ebr", "TO": "tsx", "AX": "asx",
}
_SA_QUOTE = re.compile(r"quote:\{([^}]*)\}")
_SA_PRICE = re.compile(r"(?:^|,)p:([0-9.]+)")
_SA_ASOF = re.compile(r'(?:^|,)u:"([^"]*)"')
_SA_CCY = re.compile(r"Price in ([A-Z]{3})|Currency is ([A-Z]{3})")


def stockanalysis_path(ticker):
    """(beurscode, symbool) voor de URL, of None voor een VS-ticker of een
    beurs die we niet kennen. NOVO-B.CO -> ("cph", "NOVO.B")."""
    t = (ticker or "").upper()
    if "." not in t:
        return None
    base, suffix = t.rsplit(".", 1)
    exchange = SA_EXCHANGE.get(suffix)
    if not exchange or not base:
        return None
    return exchange, base.replace("-", ".")


def parse_stockanalysis(html) -> dict | None:
    """De koers uit een quote-pagina, in het koerscontract, of None."""
    if not isinstance(html, str):
        return None
    m = _SA_QUOTE.search(html)
    if not m:
        return None
    price = _SA_PRICE.search(m.group(1))
    if not price:
        return None
    try:
        value = float(price.group(1))
    except ValueError:
        return None
    if value <= 0:
        return None
    asof = _SA_ASOF.search(m.group(1))
    # "Price in GBX" (de koers) gaat voor "Currency is GBP" (de rapportage).
    ccy = None
    for found in _SA_CCY.finditer(html):
        if found.group(1):
            ccy = found.group(1)
            break
        ccy = ccy or found.group(2)
    return {"price": value, "asof": asof.group(1) if asof else None,
            "venue": "stockanalysis", "currency": ccy}


def _sa_get(url: str) -> str:
    import urllib.request
    from gather_data import _ssl_ctx
    req = urllib.request.Request(url, headers=_SA_HEADERS)
    with urllib.request.urlopen(req, timeout=_SA_TIMEOUT, context=_ssl_ctx) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_stockanalysis_quotes(tickers, fetch=None, max_workers: int = 4) -> dict:
    """{ticker: quote} voor de buitenlandse tickers die we kunnen vinden.
    Een ticker zonder pagina of zonder koers valt er stil uit."""
    fetch = fetch or _sa_get
    paths = {t: stockanalysis_path(t) for t in tickers or []}
    paths = {t: p for t, p in paths.items() if p}
    if not paths:
        return {}

    def one(item):
        t, (exchange, sym) = item
        try:
            return t, parse_stockanalysis(fetch(SA_URL.format(exchange=exchange.lower(),
                                                              symbol=sym)))
        except Exception as e:
            logger.info("stockanalysis %s: %s", t, e)
            return t, None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        return {t: q for t, q in ex.map(one, paths.items()) if q}


def frankfurt_ok(ticker, cfg=None) -> bool:
    """Frankfurt noteert in EUR: alleen bruikbaar voor een EUR-noteringsmunt.
    Voor een Tokio- of Londen-lijn gaf hij een euro-koers die als yen of
    pence werd gelezen."""
    from ticker_currency import listing_currency
    return listing_currency(ticker, cfg) == "EUR"


def _default_yahoo(tickers):
    import tastytrade_api  # laat: zwaar pakket, en de tests hebben het niet nodig
    return tastytrade_api.fetch_current_prices(tickers)


def _broker_symbol_ok(ticker) -> bool:
    """Alleen gewone VS-stijl tickers gaan naar de broker.

    Tastytrade kent Europese lijnen (RMS.PA), forex (EURUSD=X) en indices
    (^GSPC) niet onder die naam; ze zouden er alleen de volle 10s-timeout van
    de broker-feed voor betalen zonder ooit een koers terug te krijgen. Ze
    gaan gewoon door naar Nasdaq/Yahoo/Frankfurt, zoals daarvoor."""
    return isinstance(ticker, str) and not any(c in ticker for c in ".=^")


def live_quotes(tickers, broker=None, isin_by_ticker=None,
                nasdaq=None, yahoo=None, frankfurt=None, stockanalysis=None) -> dict:
    """{ticker: quote | None}: de hele keten, elke bron alleen voor de gaten.

    Volgorde: broker-feed (alleen als meegegeven -- geauthenticeerd, dus niet
    aan een IP gebonden -- en alleen voor VS-stijl tickers) -> Nasdaq
    (VS-tickers, zonder sleutel) -> Yahoo (vangt wat Nasdaq niet kent, zolang
    hij ons nog bedient) -> Frankfurt (alleen tickers met een ISIN, in de
    praktijk de Europese lijnen).

    Een bron die omvalt telt als "niets gevonden" en wordt gelogd; de keten
    zelf gooit nooit. Een koers van 0 telt niet als koers.
    """
    tickers = list(dict.fromkeys(t for t in tickers if t))
    if not tickers:
        return {}
    isin_by_ticker = isin_by_ticker or {}
    steps = [("broker", broker),
             ("nasdaq", nasdaq or fetch_nasdaq_quotes),
             ("yahoo", yahoo or _default_yahoo),
             ("stockanalysis", stockanalysis or fetch_stockanalysis_quotes)]

    out = dict.fromkeys(tickers)
    for name, source in steps:
        missing = [t for t in tickers if _live_price(out[t]) is None]
        if name == "broker":
            missing = [t for t in missing if _broker_symbol_ok(t)]
        if not missing or source is None:
            continue
        found = _ask(source, missing, name)
        for t in missing:
            if _live_price(found.get(t)) is not None:
                out[t] = found[t]

    gaps = {t: isin_by_ticker[t] for t in tickers
            if _live_price(out[t]) is None and isin_by_ticker.get(t)
            and frankfurt_ok(t)}
    if gaps:
        found = _ask(frankfurt or fetch_frankfurt_quotes, gaps, "frankfurt")
        for t in gaps:
            if _live_price(found.get(t)) is not None:
                out[t] = found[t]

    return {t: (out[t] if _live_price(out[t]) is not None else None)
            for t in tickers}


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


def apply_live_prices(entries: list, primary=None, frankfurt=None,
                      secondary=None, to_reporting=None) -> list:
    """Zet een live koers op elke watchlist-rij, met de herkomst erbij.

    `primary` is de gewone route (broker-feed of Yahoo) en gaat voor.
    `frankfurt` vult alleen de gaten die daar vallen, en alleen voor rijen die
    een ISIN dragen -- in de praktijk de Europese lijnen.

    Elke rij krijgt er drie velden bij:

        price_source  "primary" | de beurscode | "stored"
        price_asof    tijdstempel van de bron, of None
        price_stale   True zodra de opgeslagen koers is blijven staan

    `secondary` (stockanalysis.com) vult daarna de buitenlandse lijnen.
    `to_reporting(entry, price, quote)` zet een ruwe koers om naar de
    rapportagemunt van de rij (ticker_currency); None = geen bruikbare koers.

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
    missing = [t for t in tickers if _live_price(primary_quotes.get(t)) is None]
    if secondary is not None and missing:
        for t, q in _ask(secondary, missing, "secondary").items():
            if _live_price(q) is not None:
                primary_quotes[t] = q

    gaps = {e["ticker"]: e.get("isin") for e in entries
            if e.get("ticker") and e.get("isin")
            and _live_price(primary_quotes.get(e["ticker"])) is None
            and frankfurt_ok(e["ticker"], e)}
    frankfurt_quotes = _ask(frankfurt, gaps, "frankfurt") if gaps else {}

    for entry in entries:
        ticker = entry.get("ticker")
        quote = primary_quotes.get(ticker)
        price = _live_price(quote)
        source = (quote or {}).get("venue") or "primary"

        if price is None:
            quote = frankfurt_quotes.get(ticker)
            price = _live_price(quote)
            source = (quote or {}).get("venue") or "frankfurt"

        if price is not None and to_reporting is not None:
            price = to_reporting(entry, price, quote)

        if price is None:
            entry["price_source"] = "stored"
            entry["price_asof"] = None
            entry["price_stale"] = True
            continue

        entry["stock_price"] = price
        entry["price_source"] = source
        entry["price_asof"] = (quote or {}).get("asof")
        entry["price_stale"] = False

        # The return the LIVE price implies, from the valuation's stored
        # curve; the stored implied_return was for the valuation's price.
        curve = entry.get("implied_curve")
        if curve:
            from dcf_calculator import implied_from_curve
            entry["implied_return"] = implied_from_curve(curve, price)

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
