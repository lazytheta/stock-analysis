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
