"""Ontdubbeling van EDGAR-jaarwaarden: bij twee indieningen voor hetzelfde
boekjaar moet de herziene (nieuwste) waarde winnen, niet de oorspronkelijke."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gather_data

TAG = "AccountsReceivableNetCurrent"


def _facts(*entries):
    return {"facts": {"us-gaap": {TAG: {"units": {"USD": list(entries)}}}}}


def _e(val, filed, end="2017-06-30", start="2016-07-01", **extra):
    body = {"form": "10-K", "start": start, "end": end, "filed": filed}
    if val is not None:
        body["val"] = val
    body.update(extra)
    return body


def _values(*entries):
    return gather_data._extract_annual_values(_facts(*entries), TAG)


def test_a_restated_value_from_a_later_filing_wins():
    """Gemeten op MSFT: 167 tag/jaar-combinaties waarvan de 10-K van het jaar
    erna een andere waarde meldt (AccountsReceivableNetCurrent FY2017: 19,79
    mrd oorspronkelijk, 22,43 mrd herzien -- 13%). De strikte `dur >` hield
    het eerst geziene record, in EDGAR's volgorde de oudste indiening."""
    out = _values(_e(19_792_000_000, filed="2017-08-02"),
                  _e(22_431_000_000, filed="2018-08-03"))
    assert out == [(2017, 22_431_000_000)]


def test_order_of_arrival_does_not_matter():
    out = _values(_e(22_431_000_000, filed="2018-08-03"),
                  _e(19_792_000_000, filed="2017-08-02"))
    assert out == [(2017, 22_431_000_000)]


def test_a_full_year_still_beats_a_quarter_regardless_of_filing_date():
    """Duur blijft de eerste sleutel: een kwartaalsnapshot uit een nieuwere
    indiening mag een jaarcijfer uit een oudere niet verdringen."""
    out = _values(_e(100, filed="2017-08-02"),
                  _e(25, filed="2018-08-03", start="2017-04-01"))
    assert out == [(2017, 100)]


def test_a_missing_value_is_not_a_zero():
    """`entry.get("val", 0)` maakte van een ontbrekend cijfer een echte 0,
    die dan als jaarwaarde meetelt in groei en marges."""
    assert _values(_e(None, filed="2017-08-02")) == []


def test_a_missing_value_does_not_shadow_a_real_one():
    out = _values(_e(None, filed="2018-08-03"), _e(7, filed="2017-08-02"))
    assert out == [(2017, 7)]


def test_entries_without_a_filing_date_still_dedupe_on_duration():
    """Ouder testmateriaal en sommige feeds hebben geen `filed`; dan moet de
    oude regel (langste duur wint) gewoon blijven werken."""
    a = {"form": "10-K", "start": "2016-07-01", "end": "2017-06-30", "val": 1}
    b = {"form": "10-K", "start": "2017-04-01", "end": "2017-06-30", "val": 2}
    assert _values(a, b) == [(2017, 1)]
