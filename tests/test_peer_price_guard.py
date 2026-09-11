"""fetch_peer_data: een peer zonder koers mag geen multiples van 0 opleveren."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gather_data


def _stub_edgar(monkeypatch, revenue=1000.0):
    monkeypatch.setattr(gather_data.time, "sleep", lambda *_: None)
    monkeypatch.setattr(gather_data, "get_cik", lambda t: "0000000001")
    monkeypatch.setattr(gather_data, "fetch_company_facts", lambda cik: {})
    # Alle sleutels die de lus leest. Een ontbrekende sleutel gooit een
    # KeyError die de lus stil overslaat -- dan zegt de test niets.
    monkeypatch.setattr(gather_data, "parse_financials", lambda facts, n_years=3: {
        "revenue": [900.0, revenue], "operating_income": [200.0], "net_income": [150.0],
        "cash": [50.0], "shares": [10.0], "current_assets": [300.0],
        "current_liabilities": [100.0], "net_ppe": [400.0], "goodwill_intang": [50.0],
        "lt_debt_latest": 100.0, "st_debt_latest": 20.0,
    })


def test_a_peer_without_a_quote_is_skipped_not_zeroed(monkeypatch):
    """fetch_stock_price geeft (0, 0, 0) bij elke storing. Dat ging
    ongecontroleerd door: mkt_cap 0, pe 0.0, ev = -cash. Die getallen werden
    als `peers` in de config geschreven en voedden de peer-lens. Met Yahoo
    geblokkeerd op bron-IP was dit het actieve pad."""
    _stub_edgar(monkeypatch)
    monkeypatch.setattr(gather_data, "fetch_stock_price", lambda t: (0, 0, 0))
    assert gather_data.fetch_peer_data(["PEER"]) == []


def test_a_peer_with_a_quote_still_comes_through(monkeypatch):
    _stub_edgar(monkeypatch)
    monkeypatch.setattr(gather_data, "fetch_stock_price", lambda t: (50.0, 0, 0))
    out = gather_data.fetch_peer_data(["PEER"])
    assert len(out) == 1
    assert out[0]["ticker"] == "PEER"
