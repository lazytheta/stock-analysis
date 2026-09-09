"""Koersen van Deutsche Boerse, voor de lijnen die Yahoo niet levert.

Alles draait op een geinjecteerde fetch, dus zonder netwerk. Dezelfde opzet als
VaultStore(client) en compute_screener(universe, fetch): de HTTP-kant is een
argument, niet een import.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import quotes
from datetime import UTC

ISINS = {"RMS.PA": "FR0000052292", "RHM.DE": "DE0007030009"}


def _payload(last=1412, prev_abs=-31.0, ts="2026-09-09T15:28:06Z", **extra):
    body = {
        "isin": "FR0000052292",
        "lastPrice": last,
        "changeToPrevDayAbsolute": prev_abs,
        "timestampLastPrice": ts,
        "tradingStatus": "Continuous Trading",
    }
    body.update(extra)
    return body


def _fetcher(by_isin):
    """Doet alsof hij het net op gaat: sleutelt op de isin in de URL."""
    def fetch(url):
        isin = url.split("isin=")[1].split("&")[0]
        result = by_isin.get(isin)
        if isinstance(result, Exception):
            raise result
        if result is None:
            raise LookupError(isin)
        return result
    return fetch


def test_a_quote_carries_price_and_its_age():
    """De tijdstempel is het punt. Een koers zonder ouderdom kan niet zeggen
    dat hij oud is, en dan leest twee uur oud als nu."""
    out = quotes.fetch_frankfurt_quotes(
        {"RMS.PA": "FR0000052292"},
        fetch=_fetcher({"FR0000052292": _payload()}))
    quote = out["RMS.PA"]
    assert quote["price"] == 1412
    assert quote["asof"] == "2026-09-09T15:28:06Z"
    assert quote["venue"] == "XETR"


def test_previous_close_is_derived_from_the_absolute_change():
    out = quotes.fetch_frankfurt_quotes(
        {"RMS.PA": "FR0000052292"},
        fetch=_fetcher({"FR0000052292": _payload(last=1412, prev_abs=-31.0)}))
    assert out["RMS.PA"]["previousClose"] == pytest.approx(1443.0)


def test_a_missing_change_leaves_previous_close_unknown():
    """Niet 0 en niet de laatste koers: geen verandering bekend betekent geen
    vorige slotkoers bekend, en dat moet zichtbaar blijven."""
    out = quotes.fetch_frankfurt_quotes(
        {"RMS.PA": "FR0000052292"},
        fetch=_fetcher({"FR0000052292": _payload(changeToPrevDayAbsolute=None)}))
    assert out["RMS.PA"]["previousClose"] is None


def test_a_zero_price_is_not_a_quote():
    """Nul is geen koers maar een gat. Het hele punt van None in dit contract."""
    out = quotes.fetch_frankfurt_quotes(
        {"RMS.PA": "FR0000052292"},
        fetch=_fetcher({"FR0000052292": _payload(last=0)}))
    assert out["RMS.PA"] is None


def test_a_missing_price_field_is_not_a_quote():
    body = _payload()
    del body["lastPrice"]
    out = quotes.fetch_frankfurt_quotes(
        {"RMS.PA": "FR0000052292"}, fetch=_fetcher({"FR0000052292": body}))
    assert out["RMS.PA"] is None


def test_one_failure_does_not_take_down_the_others():
    """Een stuk of tien namen per lijst; er hoeft er maar een te haperen."""
    out = quotes.fetch_frankfurt_quotes(
        ISINS,
        fetch=_fetcher({
            "FR0000052292": OSError("connection reset"),
            "DE0007030009": _payload(last=1008.8, ts="2026-09-09T15:30:00Z"),
        }))
    assert out["RMS.PA"] is None
    assert out["RHM.DE"]["price"] == 1008.8


def test_every_requested_ticker_appears_in_the_answer():
    """Een ontbrekende sleutel dwingt de aanroeper tot .get() en dan glipt een
    gat er stil doorheen; expliciet None dwingt hem het onder ogen te zien."""
    out = quotes.fetch_frankfurt_quotes(
        ISINS, fetch=_fetcher({"DE0007030009": _payload()}))
    assert set(out) == set(ISINS)
    assert out["RMS.PA"] is None


def test_a_ticker_without_an_isin_is_not_requested():
    asked = []

    def fetch(url):
        asked.append(url)
        return _payload()

    out = quotes.fetch_frankfurt_quotes(
        {"RMS.PA": "FR0000052292", "GEEN.ISIN": None}, fetch=fetch)
    assert out["GEEN.ISIN"] is None
    assert len(asked) == 1


def test_an_empty_request_asks_nothing():
    def fetch(url):
        raise AssertionError("mocht niet gebeuren")

    assert quotes.fetch_frankfurt_quotes({}, fetch=fetch) == {}


def test_the_venue_is_recorded_and_selectable():
    """Welke beurs de koers gaf hoort bij de koers. Xetra is voor een Parijse
    notering een secundaire markt, en dat mag niet onzichtbaar zijn."""
    out = quotes.fetch_frankfurt_quotes(
        {"RMS.PA": "FR0000052292"}, mic="XFRA",
        fetch=_fetcher({"FR0000052292": _payload()}))
    assert out["RMS.PA"]["venue"] == "XFRA"


def test_the_isin_goes_into_the_url():
    seen = []

    def fetch(url):
        seen.append(url)
        return _payload()

    quotes.fetch_frankfurt_quotes({"RMS.PA": "FR0000052292"}, fetch=fetch)
    assert "isin=FR0000052292" in seen[0]
    assert "mic=XETR" in seen[0]


def test_quote_age_reads_the_timestamp():
    from datetime import datetime
    now = datetime(2026, 9, 9, 17, 30, tzinfo=UTC)
    assert quotes.quote_age_minutes(
        {"asof": "2026-09-09T15:30:00Z"}, now=now) == pytest.approx(120.0)


def test_quote_age_is_unknown_without_a_timestamp():
    assert quotes.quote_age_minutes({"price": 10}) is None
    assert quotes.quote_age_minutes(None) is None


def test_quote_age_survives_an_unparseable_timestamp():
    """Rommel in het tijdstempel mag de rij niet laten klappen; onbekende
    ouderdom is een geldig antwoord, een exceptie halverwege de tabel niet."""
    assert quotes.quote_age_minutes({"asof": "gisteren"}) is None


def test_a_failure_says_why_in_the_log(caplog):
    """Stil afvangen maakte van een ontbrekende CA-bundle een 'geen koers'.
    Wie None krijgt moet in de log kunnen vinden wat er werkelijk misging."""
    import logging
    with caplog.at_level(logging.WARNING):
        out = quotes.fetch_frankfurt_quotes(
            {"RMS.PA": "FR0000052292"},
            fetch=_fetcher({"FR0000052292": OSError("CERTIFICATE_VERIFY_FAILED")}))
    assert out["RMS.PA"] is None
    assert "RMS.PA" in caplog.text
    assert "CERTIFICATE_VERIFY_FAILED" in caplog.text


# ── apply_live_prices ──────────────────────────────────────────────────────

def _entry(ticker="RMS.PA", price=1613.5, isin="FR0000052292", fv_mid=989.31):
    return {"ticker": ticker, "stock_price": price, "isin": isin,
            "fv_mid": fv_mid, "current_vs_mid": 0.5733}


def test_the_primary_source_wins():
    out = quotes.apply_live_prices(
        [_entry()],
        primary=lambda ts: {"RMS.PA": {"price": 1400.0}},
        frankfurt=lambda m: {"RMS.PA": {"price": 1411.0, "asof": "x"}})
    assert out[0]["stock_price"] == 1400.0
    assert out[0]["price_source"] == "primary"
    assert out[0]["price_stale"] is False


def test_frankfurt_fills_the_gap_the_primary_source_leaves():
    """De hele reden dat deze module bestaat: Yahoo levert de Europese lijnen
    niet, en dan stond er tot nu toe een koers van weken oud."""
    out = quotes.apply_live_prices(
        [_entry()],
        primary=lambda ts: {"RMS.PA": None},
        frankfurt=lambda m: {"RMS.PA": {"price": 1411.0,
                                        "asof": "2026-09-09T15:28:06Z",
                                        "venue": "XETR"}})
    assert out[0]["stock_price"] == 1411.0
    assert out[0]["price_source"] == "XETR"
    assert out[0]["price_asof"] == "2026-09-09T15:28:06Z"
    assert out[0]["price_stale"] is False


def test_without_any_live_quote_the_stored_price_stays_but_is_flagged():
    """Terugvallen op 0 blankte de koerskolom, maar upside en FCF-yield rekenen
    uit dit getal en stortten mee in — een geblokkeerde rij las als een echt
    oordeel. De opgeslagen koers blijft dus staan, met een vlag."""
    out = quotes.apply_live_prices(
        [_entry()], primary=lambda ts: {"RMS.PA": None},
        frankfurt=lambda m: {"RMS.PA": None})
    assert out[0]["stock_price"] == 1613.5
    assert out[0]["price_stale"] is True
    assert out[0]["price_source"] == "stored"


def test_a_ticker_without_an_isin_is_never_asked_of_frankfurt():
    asked = {}

    def frankfurt(mapping):
        asked.update(mapping)
        return {}

    quotes.apply_live_prices(
        [_entry("MSFT", 451.1, isin=None, fv_mid=469.19)],
        primary=lambda ts: {"MSFT": None}, frankfurt=frankfurt)
    assert asked == {}


def test_upside_is_recomputed_from_the_live_price():
    """current_vs_mid stond bevroren op de koers van de laatste waardering en
    was daarmee nog ouder dan stock_price. Een verse koers naast een oude
    upside is erger dan twee oude: dan spreken twee getallen elkaar tegen."""
    out = quotes.apply_live_prices(
        [_entry()], primary=lambda ts: {"RMS.PA": {"price": 1411.0}},
        frankfurt=lambda m: {})
    assert out[0]["current_vs_mid"] == pytest.approx(1411.0 / 989.31 - 1, rel=1e-9)


def test_upside_is_left_alone_when_there_is_no_fair_value():
    out = quotes.apply_live_prices(
        [_entry(fv_mid=None)], primary=lambda ts: {"RMS.PA": {"price": 1411.0}},
        frankfurt=lambda m: {})
    assert out[0]["current_vs_mid"] is None


def test_a_stale_row_keeps_its_stored_upside():
    """Niets ververst, dus niets herrekenen — anders suggereert een nieuw
    berekend getal dat er verse data onder zit."""
    out = quotes.apply_live_prices(
        [_entry()], primary=lambda ts: {}, frankfurt=lambda m: {})
    assert out[0]["current_vs_mid"] == 0.5733


def test_a_zero_or_negative_quote_is_refused():
    out = quotes.apply_live_prices(
        [_entry()], primary=lambda ts: {"RMS.PA": {"price": 0}},
        frankfurt=lambda m: {"RMS.PA": None})
    assert out[0]["stock_price"] == 1613.5
    assert out[0]["price_stale"] is True


def test_a_broken_source_does_not_break_the_listing():
    """De lijst moet blijven staan als een bron eruit ligt; alle rijen vallen
    dan terug op hun opgeslagen koers, zichtbaar gevlagd."""
    def boom(_):
        raise OSError("bron plat")

    out = quotes.apply_live_prices([_entry()], primary=boom, frankfurt=boom)
    assert out[0]["stock_price"] == 1613.5
    assert out[0]["price_stale"] is True


def test_an_empty_watchlist_asks_nothing():
    assert quotes.apply_live_prices([], primary=None, frankfurt=None) == []


# ── parallel_quotes + cache ────────────────────────────────────────────────

def test_parallel_quotes_asks_each_ticker_once():
    calls = []

    def one(t):
        calls.append(t)
        return {"price": 100.0}

    out = quotes.parallel_quotes(["A", "B", "C"], one, cache=None)
    assert sorted(calls) == ["A", "B", "C"]
    assert out["B"]["price"] == 100.0


def test_parallel_quotes_turns_a_failure_into_none_not_a_crash():
    def one(t):
        if t == "B":
            raise OSError("429")
        return {"price": 1.0}

    out = quotes.parallel_quotes(["A", "B"], one, cache=None)
    assert out["A"]["price"] == 1.0
    assert out["B"] is None


class _Clock:
    """Instelbare klok. Een iterator over vaste waarden koppelt de test aan
    hoeveel keer de cache toevallig de tijd opvraagt, en dat is de
    implementatie testen in plaats van het gedrag."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_the_cache_spares_the_source_a_second_round():
    """87 tickers per aanroep is precies hoe Streamlit Cloud bij Yahoo op de
    zwarte lijst kwam. De cache is er om dat niet te herhalen."""
    calls = []

    def one(t):
        calls.append(t)
        return {"price": 1.0}

    cache = quotes.TtlCache(ttl_seconds=60)
    cache._now = _Clock(1000.0)

    quotes.parallel_quotes(["A"], one, cache=cache)
    cache._now.t = 1010.0          # binnen de ttl
    quotes.parallel_quotes(["A"], one, cache=cache)
    assert calls == ["A"]


def test_the_cache_lets_go_once_the_quote_is_old():
    calls = []

    def one(t):
        calls.append(t)
        return {"price": 1.0}

    cache = quotes.TtlCache(ttl_seconds=60)
    cache._now = _Clock(1000.0)

    quotes.parallel_quotes(["A"], one, cache=cache)
    cache._now.t = 1100.0          # ruim voorbij de ttl
    quotes.parallel_quotes(["A"], one, cache=cache)
    assert calls == ["A", "A"]


def test_a_cached_miss_is_remembered_too():
    """Anders vraagt elke aanroep opnieuw de namen die de bron toch niet kent —
    het duurste verkeer levert dan structureel niets op."""
    calls = []

    def one(t):
        calls.append(t)
        return None

    cache = quotes.TtlCache(ttl_seconds=60)
    cache._now = _Clock(1000.0)
    quotes.parallel_quotes(["A"], one, cache=cache)
    quotes.parallel_quotes(["A"], one, cache=cache)
    assert calls == ["A"]
