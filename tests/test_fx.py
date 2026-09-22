"""ECB als wisselkoersbron: één Yahoo-symbool was te weinig voor een hele
klasse getallen (alles van Trading 212, elke Europese watchlistnaam)."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import fx

_USD = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-18,1.146,A
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-21,1.149,A
"""
_GBP = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-09-18,0.8588,A
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2026-09-21,0.8578,A
"""


def _fetch(url):
    if "D.USD.EUR" in url:
        return _USD
    if "D.GBP.EUR" in url:
        return _GBP
    raise RuntimeError("onbekende reeks: " + url)


def test_the_csv_becomes_a_dated_series():
    assert fx.parse_ecb_csv(_USD) == {date(2026, 9, 18): 1.146,
                                      date(2026, 9, 21): 1.149}


def test_eur_is_dollars_per_euro_straight_from_the_usd_series():
    out = fx.usd_per_unit("EUR", fetch=_fetch)
    assert out[date(2026, 9, 21)] == pytest.approx(1.149)


def test_gbp_crosses_over_the_euro():
    out = fx.usd_per_unit("GBP", fetch=_fetch)
    # 1 pond = 1/0.8578 euro = 1.1657 euro = 1.3394 dollar
    assert out[date(2026, 9, 21)] == pytest.approx(1.149 / 0.8578)


def test_usd_needs_no_series():
    assert fx.usd_per_unit("USD", fetch=_fetch) == {}


def test_spot_is_the_latest_observation():
    assert fx.spot("EUR", fetch=_fetch) == pytest.approx(1.149)


def test_a_dead_feed_is_none_not_a_number():
    def _down(url):
        raise OSError("503")
    assert fx.spot("EUR", fetch=_down) is None
    assert fx.usd_per_unit("EUR", fetch=_down) == {}


def test_rubbish_rows_are_skipped():
    text = _USD + "EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-22,,M\n"
    assert date(2026, 9, 22) not in fx.parse_ecb_csv(text)
