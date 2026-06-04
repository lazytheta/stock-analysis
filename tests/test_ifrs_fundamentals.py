"""IFRS (Form 20-F) fundamentals extraction + ADR share-ratio handling."""
import gather_data as g


def _ifrs_facts():
    """Minimal EDGAR companyfacts for a foreign IFRS filer (USD values)."""
    def usd_annual(*pairs):
        return {"units": {"USD": [
            {"form": "20-F", "start": f"{y}-01-01", "end": f"{y}-12-31", "val": v}
            for y, v in pairs
        ]}}
    return {"facts": {
        "ifrs-full": {
            "Revenue": usd_annual((2023, 70_000_000_000), (2024, 88_000_000_000)),
            "CostOfSales": usd_annual((2024, 39_000_000_000)),
            "ProfitLossFromOperatingActivities": usd_annual((2024, 40_000_000_000)),
            "ProfitLoss": usd_annual((2024, 35_000_000_000)),
            "Assets": usd_annual((2024, 204_000_000_000)),
            "CurrentLiabilities": usd_annual((2024, 39_000_000_000)),
            "CashAndCashEquivalents": usd_annual((2024, 64_000_000_000)),
            "PropertyPlantAndEquipment": usd_annual((2024, 98_000_000_000)),
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                {"form": "20-F", "end": "2024-12-31", "val": 25_000_000_000},
            ]}},
        },
    }}


def _patch(monkeypatch):
    monkeypatch.setattr(g, "get_cik", lambda t: "0001046179")
    monkeypatch.setattr(g, "fetch_company_facts", lambda cik: _ifrs_facts())


def test_ifrs_metrics_extracted_in_usd_millions(monkeypatch):
    _patch(monkeypatch)
    f = g.fetch_fundamentals("TSM", n_years=6)
    i = f["years"].index(2024)
    assert f["revenue"][i] == 88_000          # 88e9 USD -> 88,000 $M
    assert f["operating_income"][i] == 40_000
    assert f["net_income"][i] == 35_000
    assert f["cost_of_revenue"][i] == 39_000
    assert f["total_assets"][i] == 204_000
    assert f["cash"][i] == 64_000


def test_adr_ratio_divides_ordinary_shares(monkeypatch):
    _patch(monkeypatch)
    f = g.fetch_fundamentals("TSM", n_years=6)
    # 25.0bn ordinary shares / 5 (TSM ADR ratio) = 5.0bn ADR-equivalent
    shares = [s for s in f["shares"] if s]
    assert shares, "expected a share count"
    assert shares[-1] == 5_000_000_000


def test_non_adr_ticker_shares_unchanged(monkeypatch):
    """A ticker not in ADR_SHARE_RATIOS keeps the raw ordinary count."""
    _patch(monkeypatch)
    f = g.fetch_fundamentals("XYZ", n_years=6)  # not an ADR
    shares = [s for s in f["shares"] if s]
    assert shares[-1] == 25_000_000_000
