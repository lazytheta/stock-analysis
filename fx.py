"""Wisselkoersen van de ECB, met Yahoo als terugval.

Waarom: alles wat niet in dollars noteert -- de Trading 212-rekening, de
Europese watchlistnamen -- werd met één Yahoo-symbool (EURUSD=X) omgerekend.
Yahoo blokkeert op bron-IP en heeft die reeksen deze zomer twee keer
opgerekt tot de hosts waar deze app draait. Viel dat symbool weg, dan vielen
alle dollarbedragen van Trading 212 stil terug op euro's of ontbraken ze,
zonder dat iets dat zei. Eén bron voor een hele klasse getallen is te weinig.

De ECB publiceert dagkoersen gratis, zonder sleutel en zonder IP-blokkade:
referentiekoersen rond 16:00 CET, één per werkdag. Ze staan als "eenheden
per euro" (USD.EUR = 1,149 betekent 1 euro = 1,149 dollar). Deze module
draait dat om naar "dollar per eenheid van de valuta", de vorm die de rest
van het project verwacht: fetch_fx_rate("EUR") is dollars per euro.

Geen weekend- en feestdagwaarden; de aanroepers vullen gaten met de laatste
bekende koers (_on_or_before in t212_history), zoals ze bij Yahoo ook al
deden. Vandaag verschijnt pas 's middags; tot dan geldt gisteren.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

ECB_URL = ("https://data-api.ecb.europa.eu/service/data/EXR/"
           "D.{ccy}.EUR.SP00.A?format=csvdata")
_HEADERS = {"User-Agent": "lazytheta/1.0", "Accept": "text/csv"}


def _http_get_text(url: str) -> str:
    """Via gather_data, dat de SSL-context van dit project bijhoudt."""
    from gather_data import _http_get
    data = _http_get(url, _HEADERS)
    return data.decode("utf-8") if isinstance(data, bytes) else data


def parse_ecb_csv(text: str) -> dict:
    """{date: waarde} uit de ECB-csv. Rommelige regels worden overgeslagen."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}
    header = lines[0].split(",")
    try:
        i_t, i_v = header.index("TIME_PERIOD"), header.index("OBS_VALUE")
    except ValueError:
        return {}
    out = {}
    for ln in lines[1:]:
        cols = ln.split(",")
        if len(cols) <= max(i_t, i_v):
            continue
        try:
            out[datetime.strptime(cols[i_t], "%Y-%m-%d").date()] = float(cols[i_v])
        except ValueError:
            continue
    return out


def ecb_per_eur(ccy: str, start: date | None = None, last_n: int | None = None,
                fetch=None) -> dict:
    """{date: eenheden van `ccy` per euro}, of {} als de ECB niet antwoordt."""
    fetch = fetch or _http_get_text
    url = ECB_URL.format(ccy=ccy.upper())
    if start:
        url += f"&startPeriod={start.isoformat()}"
    if last_n:
        url += f"&lastNObservations={int(last_n)}"
    try:
        return parse_ecb_csv(fetch(url))
    except Exception as e:
        logger.warning("ECB-koers %s mislukt: %s: %s", ccy, type(e).__name__, e)
        return {}


def usd_per_unit(ccy: str, start: date | None = None, last_n: int | None = None,
                 fetch=None) -> dict:
    """{date: dollar per eenheid van `ccy`}.

    EUR komt rechtstreeks uit de USD-reeks. Elke andere valuta is een kruising
    over de euro: dollar per euro gedeeld door valuta per euro, op de dagen
    die beide reeksen delen.
    """
    code = (ccy or "USD").upper()
    if code == "USD":
        return {}
    usd = ecb_per_eur("USD", start, last_n, fetch)
    if code == "EUR":
        return usd
    other = ecb_per_eur(code, start, last_n, fetch)
    return {d: usd[d] / other[d] for d in usd if other.get(d)}


def spot(ccy: str, fetch=None) -> float | None:
    """Laatste bekende dollarkoers voor één eenheid van `ccy`, of None."""
    series = usd_per_unit(ccy, last_n=5, fetch=fetch)
    if not series:
        return None
    return series[max(series)]
