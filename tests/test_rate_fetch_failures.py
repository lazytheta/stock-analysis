"""Een mislukte rente-fetch mag geen plausibel getal teruggeven.

fetch_treasury_yield gaf 0.04 en fetch_tips_yield 0.02 bij elke storing. De
aanroeper kon dat niet onderscheiden van een echte 4,00% en schreef het als
risk_free_rate in de config -- met "10Y Treasury: 4,00%" in beeld alsof het
gemeten was.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gather_data


def _kill_network(monkeypatch):
    def boom(*a, **k):
        raise OSError("blocked")
    monkeypatch.setattr(gather_data, "_http_get", boom)
    monkeypatch.setattr(gather_data, "_http_get_json", boom)
    monkeypatch.setattr(gather_data.urllib.request, "urlopen", boom)


def test_a_failed_treasury_fetch_is_none_not_four_percent(monkeypatch):
    _kill_network(monkeypatch)
    assert gather_data.fetch_treasury_yield() is None


def test_a_failed_tips_fetch_is_none_not_two_percent(monkeypatch):
    _kill_network(monkeypatch)
    assert gather_data.fetch_tips_yield() is None


def test_the_named_default_is_still_available_for_callers():
    """De terugval blijft bestaan -- maar als expliciete keuze van de
    aanroeper, niet als verkleed antwoord van de fetch."""
    assert gather_data.RISK_FREE_RATE_DEFAULT == pytest.approx(0.0465)
    assert gather_data.TIPS_DEFAULT == pytest.approx(0.02)
