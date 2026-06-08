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


def _mjds_facts():
    """Minimal EDGAR companyfacts for a Canadian MJDS filer (Form 40-F, USD).
    Models Wheaton Precious Metals (WPM) post-2024 reporting shape."""
    def usd_annual_40f(*pairs):
        return {"units": {"USD": [
            {"form": "40-F", "start": f"{y}-01-01", "end": f"{y}-12-31", "val": v}
            for y, v in pairs
        ]}}
    return {"facts": {
        "ifrs-full": {
            "Revenue": usd_annual_40f((2023, 1_017_000_000), (2024, 1_285_000_000),
                                       (2025, 2_314_000_000)),
            "CostOfSales": usd_annual_40f((2025, 720_000_000)),
            "ProfitLossFromOperatingActivities": usd_annual_40f((2025, 1_310_000_000)),
            "ProfitLoss": usd_annual_40f((2025, 1_270_000_000)),
            "Assets": usd_annual_40f((2025, 9_200_000_000)),
            "CurrentLiabilities": usd_annual_40f((2025, 35_000_000)),
            "CashAndCashEquivalents": usd_annual_40f((2025, 1_010_000_000)),
            "PropertyPlantAndEquipment": usd_annual_40f((2025, 6_000_000_000)),
        },
        "dei": {
            "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                {"form": "40-F", "end": "2025-12-31", "val": 453_000_000},
            ]}},
        },
    }}


def test_mjds_40f_canadian_filer_parses_correctly(monkeypatch):
    """Canadian MJDS filer (Form 40-F) was previously rejected by
    _extract_annual_values' form filter, causing run_analysis to bail with
    'Could not find IFRS revenue data'. With 40-F in the allow-list the
    same IFRS branch handles it identically to a 20-F."""
    monkeypatch.setattr(g, "get_cik", lambda t: "0001323404")  # WPM CIK
    monkeypatch.setattr(g, "fetch_company_facts", lambda cik: _mjds_facts())

    f = g.fetch_fundamentals("WPM", n_years=6)
    i = f["years"].index(2025)
    assert f["revenue"][i] == 2_314         # 2.314B USD → 2,314 $M
    assert f["operating_income"][i] == 1_310
    assert f["net_income"][i] == 1_270
    assert f["total_assets"][i] == 9_200
    # Shares from 40-F dei entry must be picked up (regression guard for
    # the form-filter widening)
    shares = [s for s in f["shares"] if s]
    assert shares, "expected a share count from 40-F filing"
    assert shares[-1] == 453_000_000
