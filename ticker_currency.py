"""In welke munt een ticker noteert, in welke hij rapporteert, en hoe een
koers van de een naar de ander gaat (owner-verzoek 2026-10-22).

Alles in een config rekent in de **rapportagemunt**: de fundamentals, de DCF,
de fair value. ASML rapporteert in euro's, dus staat ook de koers in euro's,
al handel je hem in New York in dollars. Alleen de koers wordt omgerekend.

    noteringsmunt    waarin de ruwe koers binnenkomt (USD voor ASML op
                     Nasdaq, GBX = pence voor Londen, JPY voor Tokio)
    rapportagemunt   waarin het bedrijf rapporteert en de config rekent

Pure functies; de wisselkoers komt van buiten (standaard
gather_data.fetch_fx_rate: hoeveel USD één eenheid koopt).
"""

# Beurssuffix -> noteringsmunt. Londen noteert in pence (GBX).
SUFFIX_CURRENCY = {
    "PA": "EUR", "DE": "EUR", "AS": "EUR", "MI": "EUR", "F": "EUR", "BR": "EUR",
    "MC": "EUR", "VI": "EUR", "HE": "EUR", "LS": "EUR", "IR": "EUR",
    "L": "GBX", "SW": "CHF", "ST": "SEK", "CO": "DKK", "OL": "NOK", "TO": "CAD",
    "T": "JPY", "HK": "HKD", "AX": "AUD",
}

SYMBOL = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CHF": "CHF ", "SEK": "kr ",
    "DKK": "kr ", "NOK": "kr ", "CAD": "C$", "HKD": "HK$", "AUD": "A$",
}

# Munten waarin de koers een factor honderd kleiner is dan het getal
# (pence, cent). Waarde: (echte munt, schaal).
_MINOR_UNITS = {"GBX": ("GBP", 0.01), "GBP_PENCE": ("GBP", 0.01)}


def _suffix(ticker) -> str:
    t = (ticker or "").upper()
    return t.rsplit(".", 1)[1] if "." in t else ""


def _norm(code) -> str:
    code = (code or "").strip()
    if code == "GBp":                      # Yahoo's schrijfwijze voor pence
        return "GBX"
    return code.upper()


def listing_currency(ticker, cfg=None) -> str:
    """De munt waarin de ruwe koers van deze ticker binnenkomt.

    `listing_currency` in de config wint. Daarna de oudere sleutel
    `currency`: die betekende "de munt van de rij", en bij een Londense
    notering staat daar "GBP" terwijl de beurs in pence noteert. Daarna het
    suffix, en zonder suffix USD.
    """
    cfg = cfg or {}
    code = _norm(cfg.get("listing_currency"))
    if code:
        return code
    legacy = _norm(cfg.get("currency"))
    suffix = _suffix(ticker)
    if legacy:
        return "GBX" if (legacy == "GBP" and suffix == "L") else legacy
    return SUFFIX_CURRENCY.get(suffix, "USD")


def reporting_currency(ticker, cfg=None) -> str:
    """De munt van alle bedragen in de config, en dus van de getoonde koers."""
    code = _norm((cfg or {}).get("reporting_currency"))
    if code:
        return code
    listed = listing_currency(ticker, cfg)
    return _MINOR_UNITS.get(listed, (listed, 1.0))[0]


def symbol(code) -> str:
    code = _norm(code)
    return SYMBOL.get(code, code + " ")


def fx_rate(from_ccy, to_ccy, usd_per=None):
    """Eenheden `to_ccy` per eenheid `from_ccy`, of None zonder koers.

    `usd_per(code)` geeft hoeveel USD één eenheid koopt (fetch_fx_rate).
    Pence en andere minor units rekenen via hun echte munt.
    """
    a = _MINOR_UNITS.get(_norm(from_ccy), (_norm(from_ccy), 1.0))
    b = _MINOR_UNITS.get(_norm(to_ccy), (_norm(to_ccy), 1.0))
    scale = a[1] / b[1]
    if a[0] == b[0]:
        return scale
    if usd_per is None:
        from gather_data import fetch_fx_rate as usd_per
    ua = 1.0 if a[0] == "USD" else usd_per(a[0])
    ub = 1.0 if b[0] == "USD" else usd_per(b[0])
    if not ua or not ub:
        return None
    return scale * ua / ub


def quote_factor(ticker, cfg=None, quote_currency=None, usd_per=None):
    """Waarmee een ruwe koers vermenigvuldigd wordt om in de rapportagemunt
    te staan, of None als de wisselkoers ontbreekt.

    `quote_currency` is de munt die de bron zelf opgeeft (Frankfurt: EUR,
    stockanalysis: GBX); zonder die opgave de noteringsmunt van de ticker.
    Een ADR hoeft niet door zijn verhouding gedeeld te worden: de share count
    in de config staat al in genoteerde stukken.
    """
    src = _norm(quote_currency) or listing_currency(ticker, cfg)
    return fx_rate(src, reporting_currency(ticker, cfg), usd_per)


def to_reporting_price(price, ticker, cfg=None, quote_currency=None, usd_per=None):
    """Ruwe koers -> koers in de rapportagemunt, of None."""
    if not price or price <= 0:
        return None
    f = quote_factor(ticker, cfg, quote_currency, usd_per)
    return price * f if f else None


def valuation_in_listing(item, usd_per=None):
    """A get_watchlist row with its amounts (fv_low/mid/high, buy_price,
    stock_price) restated in the listing currency, for comparison with a
    broker position. A row already in its listing currency comes back as is;
    one whose FX rate is missing loses those amounts (None) rather than mix
    two currencies."""
    rep = _norm(item.get("currency")) or "USD"
    lst = _norm(item.get("listing_currency")) or rep
    if rep == lst:
        return item
    rate = fx_rate(rep, lst, usd_per)
    out = dict(item)
    for key in ("fv_low", "fv_mid", "fv_high", "buy_price", "stock_price"):
        val = out.get(key)
        if isinstance(val, (int, float)):
            out[key] = val * rate if rate else None
    out["currency"] = lst
    return out
