#!/usr/bin/env python3
"""
Automated DCF Data Gathering Pipeline
======================================
Fetches financial data from SEC EDGAR, Yahoo Finance, Treasury.gov,
and Damodaran's website to build a complete DCF config file.

Usage:
    python3 gather_data.py PANW --auto-peers
    python3 gather_data.py PANW --peers "CRWD,FTNT,ZS,S,OKTA,NET"
    python3 gather_data.py PANW --sectors "Software (System & Application):1.23:0.7,Computer Services:0.92:0.3" --peers auto
    python3 gather_data.py PANW --margin-of-safety 0.20

No API keys required. Uses only public endpoints.
"""

import argparse
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

# Create SSL context that works on macOS without cert install
_ssl_ctx = ssl.create_default_context()
try:
    import certifi
    _ssl_ctx.load_verify_locations(certifi.where())
except (ImportError, Exception):
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl.CERT_NONE

# ── Constants ──────────────────────────────────────────────────────────

EDGAR_BASE = "https://data.sec.gov"
EDGAR_HEADERS = {
    "User-Agent": "StockAnalysis/1.0 (research@example.com)",
    "Accept": "application/json",
}
YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
}
ERP_DEFAULT = 0.047  # Damodaran Jan 2026 estimate
TERMINAL_GROWTH_DEFAULT = 0.025
MARGIN_OF_SAFETY_DEFAULT = 0.20

# Damodaran interest coverage → synthetic credit rating mapping
# (min_coverage, max_coverage, rating, spread)
COVERAGE_TO_RATING = [
    (12.5,  float("inf"), "AAA",  0.0040),
    (9.5,   12.5,         "AA",   0.0055),
    (7.5,   9.5,          "A+",   0.0070),
    (6.0,   7.5,          "A",    0.0080),
    (4.5,   6.0,          "A-",   0.0100),
    (4.0,   4.5,          "BBB",  0.0125),
    (3.5,   4.0,          "BBB-", 0.0175),
    (3.0,   3.5,          "BB+",  0.0250),
    (2.5,   3.0,          "BB",   0.0300),
    (2.0,   2.5,          "BB-",  0.0375),
    (1.5,   2.0,          "B+",   0.0450),
    (1.25,  1.5,          "B",    0.0525),
    (0.8,   1.25,         "B-",   0.0650),
    (0.5,   0.8,          "CCC+", 0.0800),
    (0.2,   0.5,          "CCC",  0.1050),
    (-float("inf"), 0.2,  "D",    0.1400),
]

# SIC → Damodaran sector suggestion mapping
SIC_TO_SECTOR = {
    # Software & tech
    7372: ("Software (System & Application)", 1.23),
    7371: ("Software (System & Application)", 1.23),
    7374: ("Software (Internet)", 1.20),
    7379: ("Computer Services", 0.92),
    7373: ("Computer Services", 0.92),
    7377: ("Computer Services", 0.92),
    # Semiconductors
    3674: ("Semiconductor", 1.30),
    3672: ("Semiconductor Equip", 1.25),
    # Electronics
    3679: ("Electronics (General)", 1.00),
    3678: ("Electronics (General)", 1.00),
    3669: ("Electronics (General)", 1.00),
    # Telecom
    4813: ("Telecom (Wireless)", 0.70),
    4812: ("Telecom (Wireless)", 0.70),
    4899: ("Telecom. Equipment", 0.95),
    # Pharmaceuticals & Biotech
    2836: ("Pharma & Drugs", 0.95),
    2834: ("Pharma & Drugs", 0.95),
    2835: ("Pharma & Drugs", 0.95),
    2833: ("Pharma & Drugs", 0.95),
    2860: ("Pharma & Drugs", 0.95),
    2830: ("Pharma & Drugs", 0.95),
    8731: ("Biotechnology", 1.40),
    # Retail
    5411: ("Retail (Grocery and Food)", 0.55),
    5912: ("Retail (Grocery and Food)", 0.55),
    5331: ("Retail (General)", 0.75),
    5311: ("Retail (General)", 0.75),
    5961: ("Retail (Online)", 1.10),
    5944: ("Retail (Special Lines)", 0.85),
    # Financial
    6022: ("Banks (Regional)", 0.45),
    6021: ("Banks (Regional)", 0.45),
    6020: ("Banks (Regional)", 0.45),
    6035: ("Banks (Regional)", 0.45),
    6036: ("Banks (Regional)", 0.45),
    6199: ("Brokerage & Investment Banking", 0.80),
    6211: ("Brokerage & Investment Banking", 0.80),
    6311: ("Insurance (General)", 0.65),
    6411: ("Insurance (General)", 0.65),
    # Auto
    3711: ("Auto & Truck", 0.85),
    3714: ("Auto Parts", 0.90),
    # Energy
    1311: ("Oil/Gas (Production and Exploration)", 1.10),
    2911: ("Oil/Gas (Integrated)", 0.95),
    1381: ("Oilfield Svcs/Equip", 1.20),
    # Utilities
    4911: ("Utility (General)", 0.35),
    4931: ("Utility (General)", 0.35),
    4941: ("Utility (Water)", 0.30),
    # Aerospace & Defense
    3812: ("Aerospace/Defense", 0.85),
    3760: ("Aerospace/Defense", 0.85),
    # Entertainment / Media
    7812: ("Entertainment", 1.05),
    4841: ("Broadcasting", 0.80),
    2711: ("Publishing & Newspapers", 0.80),
    # Healthcare
    8062: ("Hospitals/Healthcare Facilities", 0.70),
    5047: ("Healthcare Products", 0.90),
    3841: ("Healthcare Products", 0.90),
    # Food & Beverage
    2000: ("Food Processing", 0.55),
    2024: ("Food Processing", 0.55),
    2040: ("Food Processing", 0.55),
    2080: ("Beverage (Alcoholic)", 0.55),
    2086: ("Beverage (Soft)", 0.60),
    # Real Estate
    6500: ("R.E.I.T.", 0.55),
    6512: ("R.E.I.T.", 0.55),
    6798: ("R.E.I.T.", 0.55),
    # Transportation
    4512: ("Air Transport", 0.90),
    4011: ("Railroad", 0.65),
    4213: ("Trucking", 0.75),
    # Machinery / Industrial
    3559: ("Machinery", 0.90),
    3561: ("Machinery", 0.90),
    3550: ("Machinery", 0.90),
    # Chemicals
    2860: ("Chemical (Basic)", 0.80),
    2821: ("Chemical (Specialty)", 0.85),
    # Construction
    1500: ("Engineering/Construction", 0.85),
    1520: ("Homebuilding", 0.95),
    1521: ("Homebuilding", 0.95),
}


# ── HTTP Helpers ───────────────────────────────────────────────────────

def _http_get(url, headers=None, retries=3, delay=0.5):
    """Make an HTTP GET request with retries. Returns bytes."""
    hdrs = headers or {}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            if attempt == retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{retries} for {url}: {e}")
            time.sleep(delay * (attempt + 1))


def _http_get_json(url, headers=None):
    """GET request returning parsed JSON."""
    data = _http_get(url, headers)
    return json.loads(data)


# ── SEC EDGAR Module ──────────────────────────────────────────────────

def get_cik(ticker):
    """Look up CIK number from ticker using SEC's company_tickers.json."""
    print(f"[EDGAR] Looking up CIK for {ticker}...")
    url = "https://www.sec.gov/files/company_tickers.json"
    data = _http_get_json(url, EDGAR_HEADERS)
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik = entry["cik_str"]
            print(f"  CIK: {cik} ({entry.get('title', '')})")
            return cik
    raise ValueError(f"Ticker '{ticker}' not found in SEC database")


def fetch_company_submissions(cik):
    """Fetch company submissions data (includes SIC code)."""
    cik_padded = str(cik).zfill(10)
    url = f"{EDGAR_BASE}/submissions/CIK{cik_padded}.json"
    return _http_get_json(url, EDGAR_HEADERS)


def fetch_company_facts(cik):
    """Fetch all XBRL facts for a company from SEC EDGAR."""
    cik_padded = str(cik).zfill(10)
    url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik_padded}.json"
    print(f"[EDGAR] Fetching company facts for CIK {cik_padded}...")
    return _http_get_json(url, EDGAR_HEADERS)


def _extract_annual_values(facts, tag, n_years=6, unit_key="USD", taxonomy="us-gaap"):
    """Extract annual values for a given XBRL tag, returning list of (year, value) tuples.

    Uses the 'end' date to determine fiscal year, not the 'fy' field (which
    indicates filing year, not data year). Prefers full-year duration entries
    over quarterly entries. Deduplicates by end-date-derived fiscal year.
    """
    try:
        tag_data = facts["facts"][taxonomy][tag]["units"][unit_key]
    except KeyError:
        return []

    # Filter for annual filings
    annual = []
    for entry in tag_data:
        form = entry.get("form", "")
        if form not in ("10-K", "10-K405", "10-K/A", "20-F", "20-F/A"):
            continue

        end = entry.get("end", "")
        if not end:
            continue

        # Calculate duration in days if start date available
        start = entry.get("start", "")
        duration_days = 0
        if start and end:
            try:
                d_start = datetime.strptime(start, "%Y-%m-%d")
                d_end = datetime.strptime(end, "%Y-%m-%d")
                duration_days = (d_end - d_start).days
            except ValueError:
                pass

        # Use end date as the unique key for deduplication
        # The end date uniquely identifies which fiscal period this data belongs to
        annual.append((end, entry.get("val", 0), duration_days))

    if not annual:
        return []

    # For each unique end date, pick the entry with longest duration (prefer full-year)
    by_end = {}
    for end, val, dur in annual:
        if end not in by_end or dur > by_end[end][1]:
            by_end[end] = (val, dur)

    # For income statement items (duration > 300 days), filter out quarterly snapshots
    # For balance sheet items (duration == 0), keep all
    has_durations = any(dur > 0 for _, (_, dur) in by_end.items())
    if has_durations:
        # Income statement: only keep full-year entries (> 300 days)
        by_end = {end: (val, dur) for end, (val, dur) in by_end.items() if dur > 300}

    # Convert end dates to fiscal years
    # Use the year from end date (handles non-calendar fiscal years correctly)
    by_year = {}
    for end, (val, dur) in by_end.items():
        year = int(end[:4])
        # If two end dates map to same year, prefer the later one
        if year not in by_year or end > by_year[year][1]:
            by_year[year] = (val, end)

    # Sort by year and take most recent n_years
    sorted_years = sorted(by_year.keys())
    recent = sorted_years[-n_years:]
    return [(y, by_year[y][0]) for y in recent]


def _try_tags(facts, tags, n_years=6, unit_key="USD", taxonomy="us-gaap"):
    """Try multiple XBRL tags, return the one with the most recent data.

    Prefers tags that have data in recent years over tags that only have
    older historical data. Also fills in gaps from other tags to maximize
    historical coverage (e.g., newer tag has 2024-2025, older tag has
    2015-2024 — use newer as primary but fill 2015-2023 from older).
    """
    best = []
    best_max_year = 0
    all_results = []
    for tag in tags:
        result = _extract_annual_values(facts, tag, n_years, unit_key, taxonomy)
        if result:
            all_results.append(result)
            max_year = max(y for y, _ in result)
            if max_year > best_max_year:
                best = result
                best_max_year = max_year

    # Fill gaps in primary tag from other tags (preserves consistency
    # for overlapping years while maximizing historical coverage)
    if best and len(all_results) > 1:
        best_years = {y for y, _ in best}
        for result in all_results:
            if result is not best:
                for yr, val in result:
                    if yr not in best_years:
                        best.append((yr, val))
                        best_years.add(yr)
        best.sort()

    return best


def parse_financials(facts, n_years=6):
    """Extract all needed financial data from EDGAR Company Facts.

    Returns dict with lists of values aligned to fiscal years.
    All dollar values are in millions.
    """
    print("[EDGAR] Parsing financial statements...")
    M = 1_000_000  # Convert to millions

    # Revenue
    rev_data = _try_tags(facts, [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
    ], n_years)

    if not rev_data:
        raise ValueError("Could not find revenue data in EDGAR filings")

    years = [y for y, _ in rev_data]

    def _get_values(tags, unit="USD"):
        data = _try_tags(facts, tags, n_years, unit)
        val_by_year = {y: v for y, v in data}
        return [val_by_year.get(y, 0) for y in years]

    def _to_millions(values):
        return [round(v / M, 0) for v in values]

    # Income statement items
    revenue = _get_values(["RevenueFromContractWithCustomerExcludingAssessedTax",
                           "Revenues",
                           "RevenueFromContractWithCustomerIncludingAssessedTax",
                           "SalesRevenueNet"])
    operating_income = _get_values(["OperatingIncomeLoss"])
    net_income = _get_values(["NetIncomeLoss",
                               "ProfitLoss",
                               "NetIncomeLossAvailableToCommonStockholdersBasic"])
    cost_of_revenue = _get_values(["CostOfGoodsAndServicesSold",
                                    "CostOfRevenue",
                                    "CostOfGoodsSold"])
    sbc = _get_values(["ShareBasedCompensation",
                        "AllocatedShareBasedCompensationExpense",
                        "ShareBasedCompensationExpenseAfterTax"])
    shares = _get_values(["WeightedAverageNumberOfDilutedSharesOutstanding",
                           "CommonStockSharesOutstanding"], "shares")

    # Balance sheet items (point-in-time, not duration-based)
    current_assets = _get_values(["AssetsCurrent"])
    cash = _get_values(["CashAndCashEquivalentsAtCarryingValue",
                         "CashCashEquivalentsAndShortTermInvestments"])
    st_investments = _get_values(["ShortTermInvestments",
                                   "AvailableForSaleSecuritiesCurrent",
                                   "MarketableSecuritiesCurrent"])
    current_liabilities = _get_values(["LiabilitiesCurrent"])
    st_debt = _get_values(["ConvertibleDebtCurrent",
                            "ShortTermBorrowings",
                            "LongTermDebtCurrent",
                            "CurrentPortionOfLongTermDebt"])
    st_leases = _get_values(["OperatingLeaseLiabilityCurrent"])
    # Derive current lease if not directly reported: total - noncurrent
    if all(v == 0 for v in st_leases):
        op_lease_total = _get_values(["OperatingLeaseLiability"])
        op_lease_nc = _get_values(["OperatingLeaseLiabilityNoncurrent"])
        if op_lease_total and op_lease_nc:
            st_leases = [max(t - n, 0) for t, n in zip(op_lease_total, op_lease_nc)]

    net_ppe = _get_values(["PropertyPlantAndEquipmentNet",
                           "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"])
    goodwill = _get_values(["Goodwill"])
    intangibles = _get_values(["IntangibleAssetsNetExcludingGoodwill",
                                "FiniteLivedIntangibleAssetsNet"])
    goodwill_intang = [g + i for g, i in zip(goodwill, intangibles)]

    # Debt & related
    lt_debt = _get_values(["LongTermDebtNoncurrent", "LongTermDebt"])
    interest_expense = _get_values(["InterestExpense", "InterestExpenseDebt"])
    lt_leases = _get_values(["OperatingLeaseLiabilityNoncurrent"])
    finance_leases = _get_values(["FinanceLeaseLiability"])
    buyback = _get_values(["PaymentsForRepurchaseOfCommonStock"])

    # Tax rate from most recent year
    tax_provision = _get_values(["IncomeTaxExpenseBenefit"])
    pretax_income = _get_values(["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                                  "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"])

    result = {
        "years": years,
        "revenue": _to_millions(revenue),
        "operating_income": _to_millions(operating_income),
        "net_income": _to_millions(net_income),
        "cost_of_revenue": _to_millions(cost_of_revenue),
        "sbc": _to_millions(sbc),
        "shares": [round(v / M, 0) for v in shares],
        "current_assets": _to_millions(current_assets),
        "cash": _to_millions(cash),
        "st_investments": _to_millions(st_investments),
        "current_liabilities": _to_millions(current_liabilities),
        "st_debt": _to_millions(st_debt),
        "st_leases": _to_millions(st_leases),
        "net_ppe": _to_millions(net_ppe),
        "goodwill_intang": _to_millions(goodwill_intang),
        "lt_debt_latest": round(lt_debt[-1] / M, 0) if lt_debt and lt_debt[-1] else 0,
        "lt_leases_latest": round(lt_leases[-1] / M, 0) if lt_leases and lt_leases[-1] else 0,
        "st_debt_latest": round(st_debt[-1] / M, 0) if st_debt and st_debt[-1] else 0,
        "interest_expense_latest": round(interest_expense[-1] / M, 0) if interest_expense and interest_expense[-1] else 0,
        "finance_leases_latest": round(finance_leases[-1] / M, 0) if finance_leases and finance_leases[-1] else 0,
        "buyback": _to_millions(buyback),
        "tax_provision": _to_millions(tax_provision),
        "pretax_income": _to_millions(pretax_income),
    }

    n = len(years)
    print(f"  Found {n} years of data: {years[0]}-{years[-1]}")
    for key in ("revenue", "operating_income", "net_income"):
        vals = result[key]
        if vals:
            print(f"  {key}: {[f'{v:,.0f}' for v in vals]}")

    return result


# ── Market Data Module ────────────────────────────────────────────────

def fetch_stock_price(ticker):
    """Fetch current stock price from Yahoo Finance chart API.

    Returns (price, 0, 0) — market cap and shares are calculated later
    from EDGAR data since Yahoo quoteSummary requires authentication.
    """
    print(f"[Yahoo] Fetching stock price for {ticker}...")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1d&range=5d"
    )
    try:
        data = _http_get_json(url, YAHOO_HEADERS)
        meta = data["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice", 0)
        print(f"  Price: ${price:.2f}")
        return price, 0, 0

    except Exception as e:
        print(f"  WARNING: Yahoo Finance fetch failed: {e}")
        print("  You will need to manually set stock_price")
        return 0, 0, 0


def fetch_historical_prices(ticker, years):
    """Fetch historical year-end stock prices from Yahoo Finance chart API.

    Args:
        ticker: Stock ticker symbol
        years: List of years to get prices for

    Returns:
        Dict mapping year to year-end closing price, e.g. {2020: 150.0, 2021: 175.0}
    """
    if not years:
        return {}

    n_years = max(len(years) + 2, 12)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        f"?interval=1mo&range={n_years}y"
    )
    try:
        data = _http_get_json(url, YAHOO_HEADERS)
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]

        # Group by year, take last available month's close per year
        year_prices = {}
        for ts, close in zip(timestamps, closes):
            if close is None:
                continue
            dt = datetime.fromtimestamp(ts)
            year_prices[dt.year] = close  # overwrites earlier months, keeps latest

        # Filter to requested years only
        return {yr: round(year_prices[yr], 2) for yr in years if yr in year_prices}

    except Exception as e:
        print(f"[Yahoo] Warning: Historical prices fetch failed: {e}")
        return {}


def fetch_balance_sheet(ticker, n_years=11):
    """Fetch historical balance sheet line items from yfinance + EDGAR fallback.

    Returns dict with sorted years and aligned lists (all values in $M).
    """
    M = 1_000_000
    data_by_year = {}

    def _safe(val):
        if val is None:
            return None
        try:
            v = float(val)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    # ── Primary: yfinance ──
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        bs = t.balance_sheet
        if bs is not None and not bs.empty:
            _bs_items = [
                # Assets
                ("Cash And Cash Equivalents", "cash"),
                ("Other Short Term Investments", "short_term_investments"),
                ("Available For Sale Securities", "short_term_investments"),
                ("Accounts Receivable", "accounts_receivable"),
                ("Receivables", "accounts_receivable"),
                ("Other Receivables", "accounts_receivable"),
                ("Inventory", "inventories"),
                ("Other Current Assets", "other_current_assets"),
                ("Prepaid Assets", "other_current_assets"),
                ("Current Assets", "total_current_assets"),
                ("Investments And Advances", "investments"),
                ("Long Term Equity Investment", "investments"),
                ("Other Investments", "investments"),
                ("Net PPE", "ppe"),
                ("Goodwill", "goodwill"),
                ("Other Intangible Assets", "intangibles"),
                ("Leases", "leases"),
                ("Non Current Deferred Taxes Assets", "deferred_tax_assets"),
                ("Non Current Deferred Assets", "deferred_tax_assets"),
                ("Other Non Current Assets", "other_assets"),
                ("Total Assets", "total_assets"),
                # Liabilities
                ("Accounts Payable", "accounts_payable"),
                ("Payables And Accrued Expenses", "accounts_payable"),
                ("Income Tax Payable", "tax_payable"),
                ("Total Tax Payable", "tax_payable"),
                ("Tax Payable", "tax_payable"),
                ("Current Accrued Expenses", "accrued_liabilities"),
                ("Current Debt", "short_term_debt"),
                ("Current Debt And Capital Lease Obligation", "short_term_debt"),
                ("Current Capital Lease Obligation", "current_capital_leases"),
                ("Current Deferred Revenue", "deferred_revenue_current"),
                ("Current Deferred Liabilities", "deferred_revenue_current"),
                ("Other Current Liabilities", "other_current_liabilities"),
                ("Current Liabilities", "total_current_liabilities"),
                ("Long Term Debt", "long_term_debt"),
                ("Long Term Debt And Capital Lease Obligation", "long_term_debt"),
                ("Long Term Capital Lease Obligation", "capital_leases"),
                ("Non Current Deferred Revenue", "deferred_revenue_noncurrent"),
                ("Non Current Deferred Liabilities", "deferred_revenue_noncurrent"),
                ("Other Non Current Liabilities", "other_liabilities"),
                ("Total Liabilities Net Minority Interest", "total_liabilities"),
                # Equity
                ("Retained Earnings", "retained_earnings"),
                ("Capital Stock", "common_stock"),
                ("Common Stock Equity", "common_stock"),
                ("Gains Losses Not Affecting Retained Earnings", "aoci"),
                ("Other Equity Adjustments", "aoci"),
                ("Total Equity Gross Minority Interest", "shareholders_equity"),
                ("Stockholders Equity", "shareholders_equity"),
            ]
            for col in bs.columns:
                yr = col.year
                if yr not in data_by_year:
                    data_by_year[yr] = {}
                d = data_by_year[yr]
                for label, key in _bs_items:
                    if label in bs.index and key not in d:
                        v = _safe(bs.at[label, col])
                        if v is not None:
                            d[key] = round(v / M, 0)
    except Exception as e:
        print(f"[yfinance] Balance sheet warning: {e}")

    # ── Fallback: EDGAR XBRL ──
    _edgar_tags = {
        # ── Assets ──
        "cash": ["CashAndCashEquivalentsAtCarryingValue",
                 "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
        "short_term_investments": ["ShortTermInvestments",
                                   "MarketableSecuritiesCurrent",
                                   "AvailableForSaleSecuritiesCurrent",
                                   "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
        "accounts_receivable": ["AccountsReceivableNetCurrent",
                                "AccountsReceivableNet",
                                "NontradeReceivablesCurrent"],
        "inventories": ["InventoryNet"],
        "other_current_assets": ["OtherAssetsCurrent",
                                 "PrepaidExpenseAndOtherAssetsCurrent",
                                 "PrepaidExpenseAndOtherAssets"],
        "total_current_assets": ["AssetsCurrent"],
        "investments": ["LongTermInvestments",
                        "InvestmentsAndAdvances",
                        "MarketableSecuritiesNoncurrent",
                        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent"],
        "ppe": ["PropertyPlantAndEquipmentNet",
                "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
        "goodwill": ["Goodwill"],
        "intangibles": ["IntangibleAssetsNetExcludingGoodwill",
                        "FiniteLivedIntangibleAssetsNet"],
        "leases": ["OperatingLeaseRightOfUseAsset"],
        "deferred_tax_assets": ["DeferredIncomeTaxAssetsNet",
                                "DeferredTaxAssetsNet",
                                "DeferredTaxAssetsLiabilitiesNet"],
        "other_assets": ["OtherAssetsNoncurrent",
                         "OtherAssets"],
        "total_assets": ["Assets"],
        # ── Liabilities ──
        "accounts_payable": ["AccountsPayableCurrent",
                             "AccountsPayableAndAccruedLiabilitiesCurrent"],
        "tax_payable": ["AccruedIncomeTaxesCurrent",
                        "TaxesPayableCurrent",
                        "IncomeTaxesPayable"],
        "accrued_liabilities": ["AccruedLiabilitiesCurrent",
                                "EmployeeRelatedLiabilitiesCurrent",
                                "OtherAccruedLiabilitiesCurrent"],
        "short_term_debt": ["ShortTermBorrowings",
                            "LongTermDebtCurrent",
                            "ShortTermDebtWeightedAverageInterestRateOverTime"],
        "current_capital_leases": ["OperatingLeaseLiabilityCurrent",
                                   "FinanceLeaseLiabilityCurrent"],
        "deferred_revenue_current": ["DeferredRevenueCurrent",
                                     "ContractWithCustomerLiabilityCurrent"],
        "other_current_liabilities": ["OtherLiabilitiesCurrent"],
        "total_current_liabilities": ["LiabilitiesCurrent"],
        "long_term_debt": ["LongTermDebtNoncurrent",
                           "LongTermDebt"],
        "capital_leases": ["OperatingLeaseLiabilityNoncurrent",
                           "FinanceLeaseLiabilityNoncurrent"],
        "deferred_revenue_noncurrent": ["DeferredRevenueNoncurrent",
                                        "ContractWithCustomerLiabilityNoncurrent"],
        "other_liabilities": ["OtherLiabilitiesNoncurrent",
                              "OtherLiabilitiesNoncurrentOther",
                              "AccruedIncomeTaxesNoncurrent"],
        "total_liabilities": ["Liabilities"],
        # ── Equity ──
        "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
        "common_stock": ["CommonStocksIncludingAdditionalPaidInCapital",
                         "AdditionalPaidInCapital",
                         "CommonStockValue"],
        "aoci": ["AccumulatedOtherComprehensiveIncomeLossNetOfTax"],
        "shareholders_equity": ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                                "StockholdersEquity"],
    }
    try:
        cik = get_cik(ticker)
        facts = fetch_company_facts(cik)
        for our_key, tags in _edgar_tags.items():
            tag_data = _try_tags(facts, tags, n_years)
            for yr_val, val in tag_data:
                if yr_val not in data_by_year:
                    data_by_year[yr_val] = {}
                d = data_by_year[yr_val]
                if our_key not in d or d[our_key] is None:
                    d[our_key] = round(val / M, 0)
    except Exception as e:
        print(f"[EDGAR] Balance sheet warning: {e}")

    # ── Align to sorted years ──
    all_keys = [
        'cash', 'short_term_investments', 'accounts_receivable', 'inventories',
        'other_current_assets', 'total_current_assets', 'investments', 'ppe',
        'goodwill', 'intangibles', 'leases', 'deferred_tax_assets',
        'other_assets', 'total_assets',
        'accounts_payable', 'tax_payable', 'accrued_liabilities', 'short_term_debt',
        'current_capital_leases', 'deferred_revenue_current', 'other_current_liabilities',
        'total_current_liabilities', 'long_term_debt', 'capital_leases',
        'deferred_revenue_noncurrent', 'other_liabilities', 'total_liabilities',
        'retained_earnings', 'common_stock', 'aoci', 'shareholders_equity',
    ]

    current_year = datetime.now().year
    years_sorted = sorted(yr for yr in data_by_year if yr <= current_year)
    if len(years_sorted) > n_years:
        years_sorted = years_sorted[-n_years:]

    result = {'years': years_sorted}
    for key in all_keys:
        result[key] = [data_by_year.get(yr, {}).get(key) for yr in years_sorted]

    # ── Computed fallbacks for totals ──
    n = len(years_sorted)

    # Total Liabilities = Total Assets - Shareholders' Equity (if missing)
    for i in range(n):
        if result['total_liabilities'][i] is None:
            ta = result['total_assets'][i]
            se = result['shareholders_equity'][i]
            if ta is not None and se is not None:
                result['total_liabilities'][i] = ta - se

    # Total Current Assets = sum of current asset items (if missing)
    for i in range(n):
        if result['total_current_assets'][i] is None:
            parts = [result[k][i] for k in ('cash', 'short_term_investments',
                     'accounts_receivable', 'inventories', 'other_current_assets')]
            valid = [p for p in parts if p is not None]
            if len(valid) >= 2:
                result['total_current_assets'][i] = sum(valid)

    # Total Current Liabilities = sum of current liability items (if missing)
    for i in range(n):
        if result['total_current_liabilities'][i] is None:
            parts = [result[k][i] for k in ('accounts_payable', 'tax_payable',
                     'accrued_liabilities', 'short_term_debt',
                     'current_capital_leases', 'deferred_revenue_current',
                     'other_current_liabilities')]
            valid = [p for p in parts if p is not None]
            if len(valid) >= 2:
                result['total_current_liabilities'][i] = sum(valid)

    # ── "Other" rows as residuals so totals always reconcile ──
    def _residual(total_key, part_keys):
        """Recompute last key (the 'other' bucket) as total minus known parts."""
        other_key = part_keys[-1]
        known_keys = part_keys[:-1]
        for i in range(n):
            total = result[total_key][i]
            if total is None:
                continue
            known = sum(result[k][i] or 0 for k in known_keys)
            result[other_key][i] = total - known

    # Other Current Assets = Total Current Assets - (cash + ST investments + AR + inventories)
    _residual('total_current_assets',
              ['cash', 'short_term_investments', 'accounts_receivable',
               'inventories', 'other_current_assets'])

    # Other Assets = Total Assets - (current + investments + PPE + goodwill + intangibles + leases + DTA)
    _residual('total_assets',
              ['total_current_assets', 'investments', 'ppe',
               'goodwill', 'intangibles', 'leases', 'deferred_tax_assets',
               'other_assets'])

    # Other Current Liabilities = Total Current Liab - (AP + tax + accrued + ST debt + leases + def rev)
    _residual('total_current_liabilities',
              ['accounts_payable', 'tax_payable', 'accrued_liabilities',
               'short_term_debt', 'current_capital_leases',
               'deferred_revenue_current', 'other_current_liabilities'])

    # Other Liabilities = Total Liab - (current liab + LT debt + leases + def rev)
    _residual('total_liabilities',
              ['total_current_liabilities', 'long_term_debt', 'capital_leases',
               'deferred_revenue_noncurrent', 'other_liabilities'])

    # Shareholders' Equity = Total Assets - Total Liabilities (ensure reconciliation)
    # Absorbs minority interest, redeemable preferred stock, mezzanine items
    for i in range(n):
        ta = result['total_assets'][i]
        tl = result['total_liabilities'][i]
        if ta is not None and tl is not None:
            result['shareholders_equity'][i] = ta - tl

    # Common Stock = Shareholders' Equity - (retained earnings + AOCI)
    # This absorbs APIC, treasury stock, minority interest, etc.
    _residual('shareholders_equity',
              ['retained_earnings', 'aoci', 'common_stock'])

    return result


def fetch_cashflow_statement(ticker, n_years=11):
    """Fetch historical cash flow statement from yfinance + EDGAR fallback.

    Returns dict with sorted years and aligned lists (all values in $M).
    Signs follow cash flow convention: positive = inflow, negative = outflow.
    """
    M = 1_000_000
    data_by_year = {}

    def _safe(val):
        if val is None:
            return None
        try:
            v = float(val)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    # ── Primary: yfinance ──
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cf = t.cashflow
        if cf is not None and not cf.empty:
            _cf_items = [
                # Operating
                ("Net Income From Continuing Operations", "net_income_cf"),
                ("Depreciation And Amortization", "da_cf"),
                ("Depreciation Amortization Depletion", "da_cf"),
                ("Stock Based Compensation", "sbc"),
                ("Deferred Tax", "deferred_tax"),
                ("Deferred Income Tax", "deferred_tax"),
                ("Other Non Cash Items", "other_noncash"),
                ("Change In Working Capital", "change_wc"),
                ("Change In Receivables", "change_receivables"),
                ("Changes In Account Receivables", "change_receivables"),
                ("Change In Inventory", "change_inventory"),
                ("Change In Payables And Accrued Expense", "change_payables"),
                ("Change In Account Payable", "change_payables"),
                ("Change In Other Working Capital", "change_other_wc"),
                ("Operating Cash Flow", "operating_cf"),
                ("Cash Flow From Continuing Operating Activities", "operating_cf"),
                # Investing
                ("Capital Expenditure", "capex"),
                ("Purchase Of PPE", "capex"),
                ("Purchase Of Business", "acquisitions"),
                ("Purchase Of Investment", "purchases_investments"),
                ("Sale Of Investment", "sales_investments"),
                ("Net Other Investing Changes", "other_investing"),
                ("Investing Cash Flow", "investing_cf"),
                ("Cash Flow From Continuing Investing Activities", "investing_cf"),
                # Financing
                ("Issuance Of Debt", "debt_issuance"),
                ("Long Term Debt Issuance", "debt_issuance"),
                ("Repayment Of Debt", "debt_repayment"),
                ("Long Term Debt Payments", "debt_repayment"),
                ("Repurchase Of Capital Stock", "stock_buybacks"),
                ("Common Stock Payments", "stock_buybacks"),
                ("Cash Dividends Paid", "dividends_paid"),
                ("Common Stock Dividend Paid", "dividends_paid"),
                ("Common Stock Issuance", "stock_issuance"),
                ("Issuance Of Capital Stock", "stock_issuance"),
                ("Proceeds From Stock Option Exercised", "stock_issuance"),
                ("Net Other Financing Charges", "other_financing"),
                ("Financing Cash Flow", "financing_cf"),
                ("Cash Flow From Continuing Financing Activities", "financing_cf"),
                # Summary
                ("Effect Of Exchange Rate Changes", "fx_effect"),
                ("Changes In Cash", "net_change_cash"),
                ("Beginning Cash Position", "beginning_cash"),
                ("End Cash Position", "ending_cash"),
                ("Free Cash Flow", "fcf"),
            ]
            for col in cf.columns:
                yr = col.year
                if yr not in data_by_year:
                    data_by_year[yr] = {}
                d = data_by_year[yr]
                for label, key in _cf_items:
                    if label in cf.index and key not in d:
                        v = _safe(cf.at[label, col])
                        if v is not None:
                            d[key] = round(v / M, 0)
    except Exception as e:
        print(f"[yfinance] Cashflow warning: {e}")

    # ── Fallback: EDGAR XBRL ──
    _edgar_tags = {
        "net_income_cf": ["NetIncomeLoss", "ProfitLoss"],
        "da_cf": ["DepreciationDepletionAndAmortization",
                  "DepreciationAndAmortization", "Depreciation"],
        "sbc": ["ShareBasedCompensation",
                "AllocatedShareBasedCompensationExpense"],
        "deferred_tax": ["DeferredIncomeTaxExpenseBenefit",
                         "DeferredIncomeTaxesAndTaxCredits"],
        "operating_cf": ["NetCashProvidedByUsedInOperatingActivities",
                         "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
        "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
                  "PaymentsToAcquireProductiveAssets",
                  "PaymentsForCapitalImprovements"],
        "acquisitions": ["PaymentsToAcquireBusinessesNetOfCashAcquired",
                         "PaymentsToAcquireBusinessesAndInterestInAffiliates"],
        "purchases_investments": ["PaymentsToAcquireInvestments",
                                  "PaymentsToAcquireAvailableForSaleSecuritiesDebt",
                                  "PaymentsToAcquireMarketableSecurities"],
        "sales_investments": ["ProceedsFromSaleOfAvailableForSaleSecuritiesDebt",
                              "ProceedsFromSaleAndMaturityOfMarketableSecurities",
                              "ProceedsFromMaturitiesPrepaymentsAndCallsOfAvailableForSaleSecurities",
                              "ProceedsFromSaleMaturityAndCollectionsOfInvestments",
                              "ProceedsFromSaleOfAvailableForSaleSecurities"],
        "investing_cf": ["NetCashProvidedByUsedInInvestingActivities",
                         "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"],
        "debt_issuance": ["ProceedsFromIssuanceOfLongTermDebt",
                          "ProceedsFromDebtNetOfIssuanceCosts",
                          "ProceedsFromConvertibleDebt",
                          "ProceedsFromLinesOfCredit"],
        "debt_repayment": ["RepaymentsOfLongTermDebt",
                           "RepaymentsOfDebt",
                           "RepaymentsOfConvertibleDebt",
                           "RepaymentsOfLongTermCapitalLeaseObligations"],
        "stock_buybacks": ["PaymentsForRepurchaseOfCommonStock",
                           "PaymentsForRepurchaseOfEquity"],
        "dividends_paid": ["PaymentsOfDividendsCommonStock",
                           "PaymentsOfDividends"],
        "stock_issuance": ["ProceedsFromIssuanceOfCommonStock",
                           "ProceedsFromStockOptionsExercised"],
        "financing_cf": ["NetCashProvidedByUsedInFinancingActivities",
                         "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"],
        "fx_effect": ["EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
                      "EffectOfExchangeRateOnCashAndCashEquivalents"],
    }
    # EDGAR reports outflows as positive (e.g. "Payments..." tags).
    # Negate to match yfinance convention (negative = outflow).
    _edgar_negate = {
        'capex', 'acquisitions', 'purchases_investments',
        'debt_repayment', 'stock_buybacks', 'dividends_paid',
    }
    try:
        cik = get_cik(ticker)
        facts = fetch_company_facts(cik)
        for our_key, tags in _edgar_tags.items():
            tag_data = _try_tags(facts, tags, n_years)
            for yr_val, val in tag_data:
                if yr_val not in data_by_year:
                    data_by_year[yr_val] = {}
                d = data_by_year[yr_val]
                if our_key not in d or d[our_key] is None:
                    v = round(val / M, 0)
                    d[our_key] = -abs(v) if our_key in _edgar_negate else v
    except Exception as e:
        print(f"[EDGAR] Cashflow warning: {e}")

    # ── Align to sorted years ──
    all_keys = [
        # Operating
        'net_income_cf', 'da_cf', 'sbc', 'deferred_tax', 'other_noncash',
        'change_wc', 'change_receivables', 'change_inventory', 'change_payables',
        'change_other_wc', 'operating_cf',
        # Investing
        'capex', 'acquisitions', 'purchases_investments', 'sales_investments',
        'other_investing', 'investing_cf',
        # Financing
        'debt_issuance', 'debt_repayment', 'stock_buybacks', 'dividends_paid',
        'stock_issuance', 'other_financing', 'financing_cf',
        # Summary
        'fx_effect', 'net_change_cash', 'beginning_cash', 'ending_cash', 'fcf',
    ]

    current_year = datetime.now().year
    years_sorted = sorted(yr for yr in data_by_year if yr <= current_year)
    if len(years_sorted) > n_years:
        years_sorted = years_sorted[-n_years:]

    result = {'years': years_sorted}
    for key in all_keys:
        result[key] = [data_by_year.get(yr, {}).get(key) for yr in years_sorted]

    n = len(years_sorted)

    # ── Computed fields & residuals ──

    # FCF = Operating CF + CapEx (CapEx is negative)
    for i in range(n):
        ocf = result['operating_cf'][i]
        cx = result['capex'][i]
        if ocf is not None and cx is not None:
            result['fcf'][i] = ocf + cx

    # Other Non-Cash = Operating CF - Net Income - D&A - SBC - Deferred Tax - WC
    for i in range(n):
        ocf = result['operating_cf'][i]
        ni = result['net_income_cf'][i]
        if ocf is not None and ni is not None:
            known = sum(result[k][i] or 0 for k in
                        ('da_cf', 'sbc', 'deferred_tax', 'change_wc'))
            result['other_noncash'][i] = ocf - ni - known

    # Other Working Capital = WC Total - Receivables - Inventory - Payables
    for i in range(n):
        wc = result['change_wc'][i]
        if wc is not None:
            known = sum(result[k][i] or 0 for k in
                        ('change_receivables', 'change_inventory', 'change_payables'))
            result['change_other_wc'][i] = wc - known

    # Other Investing = Investing CF - CapEx - Acquisitions - Purchases - Sales
    for i in range(n):
        icf = result['investing_cf'][i]
        if icf is not None:
            known = sum(result[k][i] or 0 for k in
                        ('capex', 'acquisitions', 'purchases_investments',
                         'sales_investments'))
            result['other_investing'][i] = icf - known

    # Other Financing = Financing CF - Debt Issuance - Repayment - Buybacks - Divs - Stock Issuance
    for i in range(n):
        fcf_fin = result['financing_cf'][i]
        if fcf_fin is not None:
            known = sum(result[k][i] or 0 for k in
                        ('debt_issuance', 'debt_repayment', 'stock_buybacks',
                         'dividends_paid', 'stock_issuance'))
            result['other_financing'][i] = fcf_fin - known

    return result


def fetch_income_statement(ticker, n_years=11):
    """Fetch historical income statement line items from yfinance + EDGAR fallback.

    Returns dict with sorted years and aligned lists.
    Dollar amounts in $M, EPS in raw dollars, shares as raw count.
    Extras: dynamically discovered line items per company.
    """
    M = 1_000_000
    data_by_year = {}
    extras_raw = {}  # {yf_row_name: {year: val_in_M}} for dynamic extras

    def _safe(val):
        if val is None:
            return None
        try:
            v = float(val)
            return None if v != v else v
        except (TypeError, ValueError):
            return None

    # ── Primary: yfinance ──
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        inc = t.income_stmt
        if inc is not None and not inc.empty:
            _inc_items = [
                ("Total Revenue", "revenue"),
                ("Operating Revenue", "revenue"),
                ("Cost Of Revenue", "cost_of_revenue"),
                ("Reconciled Cost Of Revenue", "cost_of_revenue"),
                ("Gross Profit", "gross_profit"),
                ("Research And Development", "rd"),
                ("Selling General And Administration", "sga"),
                ("Selling And Marketing Expense", "selling_marketing"),
                ("General And Administrative Expense", "general_admin"),
                ("Other Operating Expenses", "other_operating"),
                ("Operating Expense", "total_operating_expense"),
                ("Operating Income", "operating_income"),
                ("Interest Income", "interest_income"),
                ("Interest Income Non Operating", "interest_income"),
                ("Interest Expense", "interest_expense"),
                ("Interest Expense Non Operating", "interest_expense"),
                ("Other Income Expense", "other_income"),
                ("Other Non Operating Income Expenses", "other_income"),
                ("Pretax Income", "pretax_income"),
                ("Tax Provision", "tax_provision"),
                ("Net Income", "net_income"),
                ("Net Income Common Stockholders", "net_income"),
                ("EBITDA", "ebitda"),
                ("Reconciled Depreciation", "da"),
                ("Basic EPS", "eps_basic"),
                ("Diluted EPS", "eps_diluted"),
                ("Basic Average Shares", "shares_basic"),
                ("Diluted Average Shares", "shares_diluted"),
            ]
            for col in inc.columns:
                yr = col.year
                if yr not in data_by_year:
                    data_by_year[yr] = {}
                d = data_by_year[yr]
                for label, key in _inc_items:
                    if label in inc.index and key not in d:
                        v = _safe(inc.at[label, col])
                        if v is not None:
                            if key in ('eps_basic', 'eps_diluted'):
                                d[key] = v
                            elif key in ('shares_basic', 'shares_diluted'):
                                d[key] = v
                            else:
                                d[key] = round(v / M, 0)

            # ── Dynamic extras: capture unmapped yfinance rows ──
            _yf_mapped = {label for label, _ in _inc_items}
            _yf_ignore = {
                # Computed/derived metrics (not actual line items)
                "EBIT", "Normalized EBITDA", "Normalized Income",
                "Total Expenses", "Total Operating Income As Reported",
                "Tax Rate For Calcs", "Tax Effect Of Unusual Items",
                "Total Unusual Items", "Total Unusual Items Excluding Goodwill",
                "Diluted NI Availto Com Stockholders",
                "Net Income Continuous Operations",
                "Net Income From Continuing And Discontinued Operation",
                "Net Income From Continuing Operation Net Minority Interest",
                "Net Income Including Noncontrolling Interests",
                "Net Interest Income", "Net Non Operating Interest Income Expense",
                "Average Dilution Earnings",
                "Otherunder Preferred Stock Dividend",
                "Minority Interests",
                "Total Other Finance Cost",
                # Sub-components (already included in parent items)
                "Special Income Charges",  # = -(Restructuring + Write Off)
                "Other Gand A",  # part of SGA
                "Salaries And Wages",  # part of SGA/OpEx
                "Rent Expense Supplemental",  # supplemental disclosure
                "Amortization",  # detail of D&A
                "Amortization Of Intangibles Income Statement",
                "Depreciation Amortization Depletion Income Statement",
                "Depreciation And Amortization In Income Statement",
            }
            for row_name in inc.index:
                if row_name in _yf_mapped or row_name in _yf_ignore:
                    continue
                for col in inc.columns:
                    v = _safe(inc.at[row_name, col])
                    if v is not None:
                        yr = col.year
                        if row_name not in extras_raw:
                            extras_raw[row_name] = {}
                        extras_raw[row_name][yr] = round(v / M, 0)
    except Exception as e:
        print(f"[yfinance] Income statement warning: {e}")

    # ── Fallback: EDGAR XBRL ──
    _edgar_tags = {
        "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax",
                     "Revenues", "SalesRevenueNet",
                     "SalesRevenueGoodsNet", "SalesRevenueServicesNet"],
        "cost_of_revenue": ["CostOfGoodsAndServicesSold", "CostOfRevenue",
                            "CostOfGoodsSold"],
        "gross_profit": ["GrossProfit"],
        "rd": ["ResearchAndDevelopmentExpense",
               "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
        "sga": ["SellingGeneralAndAdministrativeExpense"],
        "operating_income": ["OperatingIncomeLoss"],
        "interest_income": ["InvestmentIncomeInterest",
                            "InterestIncomeOther", "InterestIncome"],
        "interest_expense": ["InterestExpense",
                             "InterestExpenseDebt"],
        "other_income": ["OtherNonoperatingIncomeExpense",
                         "NonoperatingIncomeExpense"],
        "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"],
        "tax_provision": ["IncomeTaxExpenseBenefit"],
        "net_income": ["NetIncomeLoss",
                       "ProfitLoss"],
        "da": ["DepreciationDepletionAndAmortization",
               "DepreciationAndAmortization"],
        "eps_basic": ["EarningsPerShareBasic"],
        "eps_diluted": ["EarningsPerShareDiluted",
                        "EarningsPerShareBasicAndDiluted"],
        "shares_basic": ["WeightedAverageNumberOfSharesOutstandingBasic"],
        "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    }
    try:
        cik = get_cik(ticker)
        facts = fetch_company_facts(cik)
        for our_key, tags in _edgar_tags.items():
            if our_key in ('eps_basic', 'eps_diluted'):
                tag_data = _try_tags(facts, tags, n_years, unit_key="USD/shares")
            elif our_key in ('shares_basic', 'shares_diluted'):
                tag_data = _try_tags(facts, tags, n_years, unit_key="shares")
            else:
                tag_data = _try_tags(facts, tags, n_years)
            for yr_val, val in tag_data:
                if yr_val not in data_by_year:
                    data_by_year[yr_val] = {}
                d = data_by_year[yr_val]
                if our_key not in d or d[our_key] is None:
                    if our_key in ('eps_basic', 'eps_diluted'):
                        d[our_key] = val
                    elif our_key in ('shares_basic', 'shares_diluted'):
                        d[our_key] = val
                    else:
                        d[our_key] = round(val / M, 0)
    except Exception as e:
        print(f"[EDGAR] Income statement warning: {e}")

    # ── Align to sorted years ──
    all_keys = [
        'revenue', 'cost_of_revenue', 'gross_profit',
        'rd', 'sga', 'selling_marketing', 'general_admin',
        'other_operating', 'total_operating_expense', 'operating_income',
        'interest_income', 'interest_expense', 'other_income',
        'pretax_income', 'tax_provision', 'net_income',
        'ebitda', 'da',
        'eps_basic', 'eps_diluted', 'shares_basic', 'shares_diluted',
    ]

    current_year = datetime.now().year
    years_sorted = sorted(yr for yr in data_by_year if yr <= current_year)
    if len(years_sorted) > n_years:
        years_sorted = years_sorted[-n_years:]

    result = {'years': years_sorted}
    for key in all_keys:
        result[key] = [data_by_year.get(yr, {}).get(key) for yr in years_sorted]

    n = len(years_sorted)

    # ── Computed fields & residuals ──
    for i in range(n):
        # Force Gross Profit = Revenue - COGS (avoids $1M rounding diffs)
        r, c = result['revenue'][i], result['cost_of_revenue'][i]
        if r is not None and c is not None:
            result['gross_profit'][i] = r - c

        # Force EBITDA = Operating Income + D&A (avoids rounding diffs)
        oi, da = result['operating_income'][i], result['da'][i]
        if oi is not None and da is not None:
            result['ebitda'][i] = oi + da

    # ── Align dynamic extras to sorted years ──
    # NOTE: OpEx extras (Restructuring, Write-Off) are sub-components of SGA in
    # yfinance data — showing them separately would double-count. Only non-operating
    # extras (Gain on Sale, Equity Method Earnings) are genuine additional items.
    _extra_non_op_names = {
        "Gain On Sale Of Security", "Gain On Sale Of Business",
        "Earnings From Equity Interest",
    }
    _extra_display_map = {
        "Gain On Sale Of Security": "Gain on Sale of Securities",
        "Gain On Sale Of Business": "Gain on Sale of Business",
        "Earnings From Equity Interest": "Equity Method Earnings",
    }
    _extras_non_op = []
    for yf_name, yr_vals in extras_raw.items():
        if yf_name not in _extra_non_op_names:
            # Heuristic for unknown items
            lower = yf_name.lower()
            if not any(kw in lower for kw in ('gain', 'loss', 'equity interest',
                                               'investment income')):
                continue
        display = _extra_display_map.get(yf_name, yf_name)
        aligned = [yr_vals.get(yr) for yr in years_sorted]
        if any(v is not None and v != 0 for v in aligned):
            _extras_non_op.append((display, aligned))
    result['extras_non_operating'] = _extras_non_op

    # Other Operating Expenses (residual) = GP - OI - R&D - SGA
    for i in range(n):
        gp = result['gross_profit'][i]
        oi = result['operating_income'][i]
        if gp is not None and oi is not None:
            known = sum(result[k][i] or 0 for k in ('rd', 'sga'))
            result['other_operating'][i] = gp - oi - known

    # Other Income/Expense (residual) = Pretax - OI - interest_income + interest_expense - extras
    for i in range(n):
        pti = result['pretax_income'][i]
        oi = result['operating_income'][i]
        if pti is not None and oi is not None:
            ii = result['interest_income'][i] or 0
            ie = result['interest_expense'][i] or 0
            extras_sum = sum(vals[i] or 0 for _, vals in _extras_non_op)
            result['other_income'][i] = pti - oi - ii + ie - extras_sum

    return result


def fetch_treasury_yield():
    """Fetch current US 10-Year Treasury yield from Treasury.gov XML feed."""
    print("[Treasury] Fetching 10Y Treasury yield...")

    url = "https://home.treasury.gov/sites/default/files/interest-rates/yield.xml"

    try:
        data = _http_get(url, {"User-Agent": "StockAnalysis/1.0"})
        text = data.decode("utf-8")

        # Parse the XML — find the most recent BC_10YEAR value
        # The XML has entries like <BC_10YEAR>4.29</BC_10YEAR>
        matches = re.findall(r"<BC_10YEAR>([\d.]+)</BC_10YEAR>", text)
        if matches:
            # Last entry is most recent
            rate = float(matches[-1]) / 100
            print(f"  10Y Treasury: {rate:.4f} ({rate*100:.2f}%)")
            return rate

        raise ValueError("Could not find BC_10YEAR in XML feed")

    except Exception as e:
        # Fallback: try the CSV feed for prior year
        print(f"  XML feed failed: {e}, trying CSV fallback...")
        try:
            year = datetime.now().year - 1
            csv_url = (
                f"https://home.treasury.gov/resource-center/data-chart-center/"
                f"interest-rates/daily-treasury-rates.csv/{year}/all"
                f"?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
            )
            csv_data = _http_get(csv_url, {"User-Agent": "StockAnalysis/1.0"})
            lines = csv_data.decode("utf-8").strip().split("\n")
            if len(lines) >= 2:
                header = lines[0].split(",")
                idx_10y = None
                for i, col in enumerate(header):
                    if "10 yr" in col.lower() or "10 year" in col.lower():
                        idx_10y = i
                        break
                if idx_10y is not None:
                    # Most recent = first data line (sorted desc by date)
                    fields = lines[1].split(",")
                    if len(fields) > idx_10y and fields[idx_10y].strip():
                        rate = float(fields[idx_10y].strip()) / 100
                        print(f"  10Y Treasury (last available): {rate:.4f} ({rate*100:.2f}%)")
                        return rate
        except Exception:
            pass

        default = 0.04
        print(f"  WARNING: All Treasury fetches failed. Using default: {default:.2%}")
        return default


# ── Damodaran Module ──────────────────────────────────────────────────

def synthetic_credit_rating(operating_income, interest_expense):
    """Derive credit rating from interest coverage ratio using Damodaran method."""
    if interest_expense <= 0:
        # No interest expense → effectively AAA
        return "AAA", 0.0040

    coverage = operating_income / interest_expense
    print(f"[Rating] Interest coverage: {coverage:.2f}x (OI={operating_income:,.0f} / IE={interest_expense:,.0f})")

    for min_cov, max_cov, rating, spread in COVERAGE_TO_RATING:
        if min_cov <= coverage < max_cov:
            print(f"  Synthetic rating: {rating} (spread: {spread:.2%})")
            return rating, spread

    return "D", 0.14


def fetch_sector_betas():
    """Fetch unlevered betas by sector from Damodaran's website."""
    print("[Damodaran] Fetching sector betas...")
    url = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"

    try:
        data = _http_get(url, {"User-Agent": "StockAnalysis/1.0"})
        html = data.decode("utf-8", errors="replace")

        # Parse HTML table rows for sector betas
        betas = {}
        # Look for table rows with sector data
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(cells) >= 4:
                sector = re.sub(r"<[^>]+>", "", cells[0]).strip()
                sector = re.sub(r"\s+", " ", sector)  # Normalize whitespace
                sector = sector.replace("&amp;", "&")  # Decode HTML entities
                try:
                    # Unlevered beta is typically in one of the later columns
                    # Try column index 3 (unlevered beta) — layout varies
                    for idx in (3, 4, 5, 2):
                        val_str = re.sub(r"<[^>]+>", "", cells[idx]).strip()
                        val_str = val_str.replace(",", "")
                        if val_str and re.match(r"^[\d.]+$", val_str):
                            beta = float(val_str)
                            if 0.1 < beta < 3.0:  # Sanity check
                                betas[sector] = beta
                                break
                except (ValueError, IndexError):
                    continue

        if betas:
            print(f"  Found {len(betas)} sector betas")
        else:
            print("  WARNING: Could not parse sector betas from Damodaran")

        return betas

    except Exception as e:
        print(f"  WARNING: Damodaran fetch failed: {e}")
        return {}


def fetch_sector_margins():
    """Fetch operating margins by sector from Damodaran's margin.html page.

    Returns dict[sector_name → operating_margin (float, e.g. 0.15)].
    Uses 'Pre-tax Unadjusted Operating Margin' (column index 5).
    """
    print("[Damodaran] Fetching sector operating margins...")
    url = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/margin.html"

    try:
        data = _http_get(url, {"User-Agent": "StockAnalysis/1.0"})
        html = data.decode("utf-8", errors="replace")

        margins = {}
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(cells) >= 6:
                sector = re.sub(r"<[^>]+>", "", cells[0]).strip()
                sector = re.sub(r"\s+", " ", sector)
                sector = sector.replace("&amp;", "&")  # Decode HTML entities
                margin_str = re.sub(r"<[^>]+>", "", cells[5]).strip()
                margin_str = margin_str.replace("%", "").replace(",", "").strip()
                try:
                    if margin_str and sector and sector != "Industry Name":
                        margin = float(margin_str) / 100
                        if -1.0 < margin < 1.0:  # Sanity check
                            margins[sector] = margin
                except ValueError:
                    continue

        if margins:
            print(f"  Found margins for {len(margins)} sectors")
        else:
            print("  WARNING: Could not parse sector margins")

        return margins

    except Exception as e:
        print(f"  WARNING: Damodaran margins fetch failed: {e}")
        return {}


def fetch_sector_s2c():
    """Fetch Sales/Invested Capital by sector from Damodaran's capex page.

    Returns dict[sector_name → sales_to_capital (float)].
    """
    print("[Damodaran] Fetching sector Sales/Invested Capital...")
    url = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/capex.html"

    try:
        data = _http_get(url, {"User-Agent": "StockAnalysis/1.0"})
        html = data.decode("utf-8", errors="replace")

        s2c = {}
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL | re.IGNORECASE)
        for row in rows:
            cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL | re.IGNORECASE)
            if len(cells) >= 10:
                sector = re.sub(r"<[^>]+>", "", cells[0]).strip()
                sector = re.sub(r"\s+", " ", sector)
                sector = sector.replace("&amp;", "&")
                val_str = re.sub(r"<[^>]+>", "", cells[9]).strip()
                val_str = val_str.replace(",", "")
                try:
                    if val_str and sector and sector not in ("Industry Name", "Total Market"):
                        val = float(val_str)
                        if 0.01 < val < 50.0:
                            s2c[sector] = val
                except ValueError:
                    continue

        if s2c:
            print(f"  Found S/C for {len(s2c)} sectors")
        else:
            print("  WARNING: Could not parse sector S/C from Damodaran")

        return s2c

    except Exception as e:
        print(f"  WARNING: Damodaran S/C fetch failed: {e}")
        return {}


def fetch_consensus_estimates(ticker):
    """Fetch analyst consensus revenue estimates from Yahoo Finance via yfinance.

    Returns dict with:
      - 'rev_est_current_year': revenue estimate for current fiscal year ($M)
      - 'rev_est_next_year': revenue estimate for next fiscal year ($M)
      - 'growth_current_year': implied YoY revenue growth (decimal)
      - 'growth_next_year': implied YoY revenue growth (decimal)
      - 'n_analysts': number of analysts covering
    Returns empty dict if unavailable.
    """
    print(f"[Yahoo] Fetching analyst consensus for {ticker}...")

    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        rev_est = t.revenue_estimate

        if rev_est is None or rev_est.empty:
            print("  No consensus estimates available")
            return {}

        result = {}

        # revenue_estimate has columns: avg, low, high, numberOfAnalysts, yearAgoRevenue, growth
        # Index is period labels like '0y', '+1y' (or date strings)
        periods = list(rev_est.index)

        for i, period in enumerate(periods):
            row = rev_est.loc[period]
            avg_rev = row.get("avg", 0)
            growth = row.get("growth", 0)
            n_analysts = row.get("numberOfAnalysts", 0)

            if avg_rev and avg_rev > 0:
                rev_m = round(avg_rev / 1e6, 0)  # Convert to millions
                if i == 0:
                    result["rev_est_current_year"] = rev_m
                    result["growth_current_year"] = round(float(growth), 4) if growth else 0
                    result["n_analysts"] = int(n_analysts) if n_analysts else 0
                elif i == 1:
                    result["rev_est_next_year"] = rev_m
                    result["growth_next_year"] = round(float(growth), 4) if growth else 0

        if result:
            if "rev_est_current_year" in result:
                print(f"  Current year: ${result['rev_est_current_year']:,.0f}M (growth: {result.get('growth_current_year', 0):.1%})")
            if "rev_est_next_year" in result:
                print(f"  Next year:    ${result['rev_est_next_year']:,.0f}M (growth: {result.get('growth_next_year', 0):.1%})")
            print(f"  Analysts: {result.get('n_analysts', '?')}")
        else:
            print("  No usable estimates found")

        return result

    except ImportError:
        print("  yfinance not installed, skipping consensus estimates")
        return {}
    except Exception as e:
        print(f"  WARNING: Consensus fetch failed: {e}")
        return {}


# ── Peer Discovery Module ─────────────────────────────────────────────

def _fetch_exchange_tickers():
    """Fetch CIK → ticker mapping from SEC company_tickers_exchange.json."""
    url = "https://www.sec.gov/files/company_tickers_exchange.json"
    data = _http_get_json(url, EDGAR_HEADERS)
    # fields: [cik, name, ticker, exchange]
    cik_to_info = {}
    for row in data.get("data", []):
        cik, name, ticker, exchange = row[0], row[1], row[2], row[3]
        cik_to_info[str(cik).zfill(10)] = {
            "name": name,
            "ticker": ticker,
            "exchange": exchange,
        }
    return cik_to_info


def _fetch_sic_companies(sic_code, max_companies=200):
    """Fetch list of companies with a given SIC code from EDGAR browse endpoint."""
    companies = []
    start = 0
    count = 100

    while start < max_companies:
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&SIC={sic_code}&owner=include"
            f"&count={count}&start={start}&output=atom"
        )
        try:
            data = _http_get(url, EDGAR_HEADERS)
            text = data.decode("utf-8", errors="replace")

            # Parse Atom XML — extract CIK and company name
            ns = "http://www.w3.org/2005/Atom"
            entries = re.findall(
                rf"<entry>.*?</entry>", text, re.DOTALL
            )
            if not entries:
                break

            for entry_xml in entries:
                cik_match = re.search(rf"<{{?{ns}}}?cik>(.*?)</", entry_xml)
                name_match = re.search(rf"<{{?{ns}}}?name>(.*?)</", entry_xml)
                if not cik_match:
                    # Try without namespace
                    cik_match = re.search(r"<cik[^>]*>(.*?)</cik>", entry_xml)
                    name_match = re.search(r"<name[^>]*>(.*?)</name>", entry_xml)

                if cik_match:
                    companies.append({
                        "cik": cik_match.group(1).strip(),
                        "name": name_match.group(1).strip() if name_match else "",
                    })

            if len(entries) < count:
                break  # No more pages

            start += count
            time.sleep(0.3)

        except Exception as e:
            print(f"  WARNING: SIC company fetch failed at offset {start}: {e}")
            break

    return companies


def find_peers(sic_code, target_ticker, target_market_cap, n_peers=6):
    """Auto-discover peer companies based on SIC code and market cap similarity.

    1. Fetches all companies with the same SIC code from EDGAR
    2. Cross-references with exchange tickers to get ticker symbols
    3. Gets stock prices to estimate market caps
    4. Selects peers closest in market cap to the target
    """
    print(f"\n[Peers] Auto-discovering peers (SIC {sic_code})...")

    # Step 1: Get companies with same SIC
    sic_companies = _fetch_sic_companies(sic_code)
    print(f"  Found {len(sic_companies)} companies with SIC {sic_code}")

    if not sic_companies:
        print("  No companies found, cannot auto-select peers")
        return []

    # Step 2: Cross-reference with exchange tickers
    print("  Cross-referencing with exchange-listed tickers...")
    exchange_data = _fetch_exchange_tickers()

    candidates = []
    for comp in sic_companies:
        cik_padded = comp["cik"].zfill(10)
        if cik_padded in exchange_data:
            info = exchange_data[cik_padded]
            ticker = info["ticker"]
            if ticker.upper() == target_ticker.upper():
                continue  # Skip the target company itself
            # Only include major US exchanges
            if info["exchange"] in ("NYSE", "Nasdaq"):
                candidates.append({
                    "cik": comp["cik"],
                    "ticker": ticker,
                    "name": info["name"],
                    "exchange": info["exchange"],
                })

    print(f"  {len(candidates)} exchange-listed candidates (excl. {target_ticker})")

    if not candidates:
        print("  No exchange-listed peers found")
        return []

    # Step 3: Get market caps for candidates (batch stock price lookups)
    # Limit to a reasonable number to avoid too many API calls
    sample_size = min(len(candidates), 30)
    candidates = candidates[:sample_size]

    print(f"  Fetching market data for {len(candidates)} candidates...")
    scored = []
    for cand in candidates:
        try:
            price, _, _ = fetch_stock_price(cand["ticker"])
            if price <= 0:
                continue

            # Get shares from EDGAR company facts
            time.sleep(0.2)  # SEC rate limit: 10 req/sec
            cik_padded = cand["cik"].zfill(10)
            try:
                facts_url = f"{EDGAR_BASE}/api/xbrl/companyfacts/CIK{cik_padded}.json"
                facts_data = _http_get_json(facts_url, EDGAR_HEADERS)
                shares_tag = _try_tags(facts_data, [
                    "WeightedAverageNumberOfDilutedSharesOutstanding",
                    "CommonStockSharesOutstanding",
                ], n_years=2, unit_key="shares")
                if shares_tag:
                    shares = shares_tag[-1][1] / 1e6  # to millions
                    mkt_cap = price * shares
                else:
                    continue
            except Exception:
                continue

            if mkt_cap < 500:  # Skip micro-caps (< $500M)
                continue

            # Score by market cap proximity (log scale)
            if target_market_cap > 0 and mkt_cap > 0:
                log_ratio = abs(math.log10(mkt_cap / target_market_cap))
            else:
                log_ratio = 10  # Large penalty

            scored.append({
                "ticker": cand["ticker"],
                "name": cand["name"],
                "market_cap": mkt_cap,
                "log_distance": log_ratio,
            })
            print(f"    {cand['ticker']:6s}  ${mkt_cap:>10,.0f}M  (log dist: {log_ratio:.2f})")

        except Exception:
            continue

    if not scored:
        print("  Could not determine market caps for any candidates")
        return []

    # Step 4: Sort by market cap proximity, take top N
    scored.sort(key=lambda x: x["log_distance"])
    selected = scored[:n_peers]

    print(f"\n  Selected {len(selected)} peers (closest by market cap):")
    for s in selected:
        print(f"    {s['ticker']:6s}  ${s['market_cap']:>10,.0f}M")

    return [s["ticker"] for s in selected]


# ── Peer Data Module ──────────────────────────────────────────────────

def fetch_peer_data(peer_tickers):
    """Fetch financial data for peer companies and calculate multiples."""
    if not peer_tickers:
        return []

    print(f"\n[Peers] Gathering data for {len(peer_tickers)} peers...")
    peers = []

    for pticker in peer_tickers:
        pticker = pticker.strip().upper()
        print(f"\n  --- {pticker} ---")

        try:
            # Get EDGAR data
            time.sleep(0.2)  # Rate limiting for SEC
            cik = get_cik(pticker)
            time.sleep(0.2)
            facts = fetch_company_facts(cik)
            fin = parse_financials(facts, n_years=3)

            # Get stock price
            price, mkt_cap, shares = fetch_stock_price(pticker)

            if not fin["revenue"] or fin["revenue"][-1] == 0:
                print(f"  Skipping {pticker}: no revenue data")
                continue

            # Latest year values
            rev = fin["revenue"][-1]
            oi = fin["operating_income"][-1] if fin["operating_income"] else 0
            ni = fin["net_income"][-1] if fin["net_income"] else 0
            cogs = fin["cost_of_revenue"][-1] if fin["cost_of_revenue"] else 0
            debt = fin["lt_debt_latest"]
            cash_val = fin["cash"][-1] if fin["cash"] else 0

            # Use EDGAR shares if Yahoo didn't provide
            if shares == 0 and fin["shares"] and fin["shares"][-1] > 0:
                shares = fin["shares"][-1]
                mkt_cap = shares * price

            # Enterprise Value
            ev = mkt_cap + debt - cash_val

            # Approximate EBITDA (OI + D&A estimated as ~30% markup on OI)
            ebitda_approx = oi * 1.3 if oi > 0 else 0

            # Revenue growth (if 2+ years)
            rev_growth = 0
            if len(fin["revenue"]) >= 2 and fin["revenue"][-2] > 0:
                rev_growth = (fin["revenue"][-1] / fin["revenue"][-2]) - 1

            # Operating margin
            op_margin = oi / rev if rev > 0 else 0

            # ROIC estimate: NOPAT / Invested Capital
            # Invested Capital ≈ total assets - cash - current liabilities (rough)
            ca = fin["current_assets"][-1] if fin["current_assets"] else 0
            cl = fin["current_liabilities"][-1] if fin["current_liabilities"] else 0
            ppe = fin["net_ppe"][-1] if fin["net_ppe"] else 0
            gi = fin["goodwill_intang"][-1] if fin["goodwill_intang"] else 0
            invested_capital = (ca - cash_val) + ppe + gi - (cl - fin["st_debt_latest"])
            tax_rate = 0.21  # Statutory default for ROIC calc
            nopat = oi * (1 - tax_rate)
            roic = nopat / invested_capital if invested_capital > 0 else 0

            # Calculate multiples
            ev_revenue = ev / rev if rev > 0 else 0
            ev_ebitda = ev / ebitda_approx if ebitda_approx > 0 else 0
            pe = (mkt_cap / ni) if ni > 0 else 0

            # Get company name from EDGAR
            try:
                subs = fetch_company_submissions(cik)
                name = subs.get("name", pticker)
                # Shorten the name
                name = name.split(",")[0].split(" Inc")[0].split(" Corp")[0].split(" Ltd")[0]
                name = name.split(" Holdings")[0].split(" Group")[0]
                name = name.strip()
            except Exception:
                name = pticker

            peer_entry = {
                "ticker": pticker,
                "name": name,
                "ev_revenue": round(ev_revenue, 1),
                "ev_ebitda": round(ev_ebitda, 1),
                "pe": round(pe, 1),
                "op_margin": round(op_margin, 3),
                "rev_growth": round(rev_growth, 3),
                "roic": round(roic, 2),
            }
            peers.append(peer_entry)
            print(f"  {pticker}: EV/Rev={ev_revenue:.1f}x, P/E={pe:.1f}x, Margin={op_margin:.1%}")

        except Exception as e:
            print(f"  ERROR processing {pticker}: {e}")
            continue

        time.sleep(0.3)  # Rate limiting

    return peers


# ── Config Builder ────────────────────────────────────────────────────

def build_config(ticker, financials, stock_price, market_cap, shares_yahoo,
                 risk_free_rate, sector_betas, credit_spread, credit_rating,
                 peers, company_name, margin_of_safety=None, terminal_growth=None,
                 sector_margin=None, consensus=None):
    """Assemble all gathered data into the exact config dict for build_dcf_model().

    Uses 5 smart assumption methods:
      1. Margin trend extrapolation (historical trajectory before converging)
      2. Sector median margin from Damodaran as terminal anchor
      3. Exponential growth decay (not linear)
      4. Size-adjusted growth ceiling (large-cap penalty)
      5. Consensus estimates for year 1-2 (if available from yfinance)
    """

    print("\n[Config] Building configuration...")

    years = financials["years"]
    n = len(years)
    rev = financials["revenue"]
    oi = financials["operating_income"]
    ni = financials["net_income"]

    # Base year = most recent
    base_year = years[-1]
    base_revenue = rev[-1]
    base_oi = oi[-1] if oi[-1] else 0
    base_op_margin = round(base_oi / base_revenue, 3) if base_revenue > 0 else 0

    # Shares: prefer EDGAR diluted, fall back to Yahoo
    shares = financials["shares"][-1] if financials["shares"] and financials["shares"][-1] > 0 else shares_yahoo
    if shares == 0:
        shares = market_cap / stock_price if stock_price > 0 else 0

    # Recalculate market cap if needed
    if market_cap == 0 and stock_price > 0 and shares > 0:
        market_cap = stock_price * shares

    term_growth = terminal_growth or TERMINAL_GROWTH_DEFAULT
    consensus = consensus or {}

    # ── [IMPROVEMENT 1 & 3 & 4 & 5] Revenue growth assumptions ──
    print("  [Growth] Deriving revenue growth curve...")

    # Historical CAGRs at different horizons
    cagr_1y = (rev[-1] / rev[-2] - 1) if len(rev) >= 2 and rev[-2] > 0 else 0.05
    cagr_3y = ((rev[-1] / rev[-4]) ** (1/3) - 1) if len(rev) >= 4 and rev[-4] > 0 else cagr_1y
    cagr_5y = ((rev[-1] / rev[-6]) ** (1/5) - 1) if len(rev) >= 6 and rev[-6] > 0 else cagr_3y

    # Detect acceleration/deceleration trend
    if cagr_1y > cagr_3y > 0:
        trend = "accelerating"
        start_growth = cagr_1y  # Use recent momentum
    elif cagr_1y < cagr_3y:
        trend = "decelerating"
        start_growth = (cagr_1y + cagr_3y) / 2  # Blend
    else:
        trend = "stable"
        start_growth = cagr_3y

    # [IMPROVEMENT 5] Use consensus estimates for year 1-2 if available
    consensus_y1 = consensus.get("growth_current_year")
    consensus_y2 = consensus.get("growth_next_year")
    if consensus_y1 and consensus_y1 > 0:
        print(f"    Using analyst consensus for Y1: {consensus_y1:.1%} ({consensus.get('n_analysts', '?')} analysts)")
        start_growth = consensus_y1

    # [IMPROVEMENT 4] Size-adjusted growth ceiling
    # Larger companies can't sustain high growth as easily
    if market_cap > 0:
        if market_cap > 1_000_000:      # > $1T
            growth_cap = 0.15
        elif market_cap > 500_000:      # > $500B
            growth_cap = 0.20
        elif market_cap > 100_000:      # > $100B
            growth_cap = 0.25
        elif market_cap > 10_000:       # > $10B
            growth_cap = 0.35
        else:
            growth_cap = 0.50
        if start_growth > growth_cap:
            print(f"    Size cap applied: {start_growth:.1%} → {growth_cap:.1%} (mkt cap ${market_cap:,.0f}M)")
            start_growth = growth_cap

    # Floor
    start_growth = max(start_growth, 0.02)

    # [IMPROVEMENT 3] Exponential decay curve instead of linear
    # g(t) = terminal + (start - terminal) * e^(-lambda * t)
    # lambda controls speed of decay: higher = faster decay to terminal
    decay_lambda = 0.35  # ~65% of excess growth remains after 1 year
    if trend == "decelerating":
        decay_lambda = 0.45  # Faster decay for decelerating companies
    elif trend == "accelerating":
        decay_lambda = 0.25  # Slower decay — momentum persists

    revenue_growth = []
    for i in range(10):
        g = term_growth + (start_growth - term_growth) * math.exp(-decay_lambda * i)
        # [IMPROVEMENT 5] Override year 2 with consensus if available
        if i == 1 and consensus_y2 and consensus_y2 > 0:
            g = max(consensus_y2, term_growth)
        revenue_growth.append(round(g, 3))

    print(f"    Trend: {trend}, CAGR 1y={cagr_1y:.1%} 3y={cagr_3y:.1%} 5y={cagr_5y:.1%}")
    print(f"    Growth: {revenue_growth[0]:.1%} → {revenue_growth[4]:.1%} → {revenue_growth[9]:.1%} (exp decay λ={decay_lambda})")

    # ── [IMPROVEMENT 1 & 2] Operating margin trajectory ──
    print("  [Margins] Deriving operating margin trajectory...")

    recent_margin = base_op_margin

    # [IMPROVEMENT 1] Detect margin trend from history
    hist_margins = []
    for r, o in zip(rev, oi):
        if r > 0:
            hist_margins.append(o / r)
        else:
            hist_margins.append(0)

    # Linear regression slope over available years (simple OLS)
    if len(hist_margins) >= 3:
        x_vals = list(range(len(hist_margins)))
        x_mean = sum(x_vals) / len(x_vals)
        y_mean = sum(hist_margins) / len(hist_margins)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, hist_margins))
        den = sum((x - x_mean) ** 2 for x in x_vals)
        margin_slope = num / den if den > 0 else 0  # pp per year
    else:
        margin_slope = 0

    margin_trend = "expanding" if margin_slope > 0.005 else ("contracting" if margin_slope < -0.005 else "stable")

    # [IMPROVEMENT 2] Terminal margin = blend of sector median and current margin
    # If the company is well above sector median, terminal is a weighted blend
    # (sector dominance doesn't fully erode, but mean reversion pulls)
    if sector_margin is not None:
        if recent_margin > sector_margin:
            # Company outperforms sector — blend 60% sector, 40% current
            term_margin = round(sector_margin * 0.6 + recent_margin * 0.4, 3)
        else:
            # Company underperforms sector — converge toward sector
            term_margin = round(sector_margin * 0.7 + recent_margin * 0.3, 3)
        print(f"    Sector median: {sector_margin:.1%}, terminal margin (blended): {term_margin:.1%}")
    else:
        # Fallback: compress from current level
        if recent_margin > 0.25:
            term_margin = round(recent_margin - 0.05, 3)
        elif recent_margin > 0.10:
            term_margin = round(recent_margin - 0.03, 3)
        else:
            term_margin = round(max(recent_margin, 0.05), 3)

    # [IMPROVEMENT 1] Extrapolate margin trend before converging
    # Phase 1 (years 1-3): continue historical trend (capped)
    # Phase 2 (years 4-10): converge to terminal margin
    max_trend_extension = min(abs(margin_slope), 0.03)  # Cap at 3pp/year change
    if margin_slope > 0:
        margin_slope_capped = max_trend_extension
    else:
        margin_slope_capped = -max_trend_extension

    op_margins = []
    for i in range(10):
        if i < 3:
            # Phase 1: extrapolate trend from current level (capped)
            projected = recent_margin + margin_slope_capped * (i + 1)
            # Cap: don't go more than 5pp above current or 5pp below terminal
            if margin_slope_capped > 0:
                projected = min(projected, recent_margin + 0.05)
            else:
                projected = max(projected, term_margin - 0.05)
            op_margins.append(round(projected, 3))
        else:
            # Phase 2: converge from year-3 level to terminal
            y3_margin = op_margins[2]
            t = (i - 3) / 6  # 0 at year 4, 1 at year 10
            m = y3_margin + (term_margin - y3_margin) * t
            op_margins.append(round(m, 3))

    print(f"    Trend: {margin_trend} (slope: {margin_slope:+.1%}/yr)")
    print(f"    Margins: {op_margins[0]:.1%} → {op_margins[4]:.1%} → {op_margins[9]:.1%} (terminal: {term_margin:.1%})")

    # ── SBC % ──
    sbc_vals = financials["sbc"]
    if sbc_vals and rev:
        # Average of last 3 years
        recent_sbc = sbc_vals[-3:] if len(sbc_vals) >= 3 else sbc_vals
        recent_rev = rev[-3:] if len(rev) >= 3 else rev
        sbc_ratios = [s / r for s, r in zip(recent_sbc, recent_rev) if r > 0 and s > 0]
        sbc_pct = round(sum(sbc_ratios) / len(sbc_ratios), 4) if sbc_ratios else 0.004
    else:
        sbc_pct = 0.004

    # ── Buyback rate ──
    # Divide each year's buyback by that year's *estimated* market cap
    # (using current P/E applied to historical earnings as proxy)
    buyback_vals = financials["buyback"]
    hist_shares = financials["shares"]
    if buyback_vals and market_cap > 0 and ni and ni[-1] > 0:
        current_pe = market_cap / ni[-1]
        yearly_rates = []
        for i in range(len(buyback_vals)):
            bb = abs(buyback_vals[i])
            if bb > 0 and i < len(ni) and ni[i] > 0:
                est_mc = ni[i] * current_pe
                yearly_rates.append(bb / est_mc)
            elif bb > 0 and i < len(rev) and rev[i] > 0:
                # Fallback: use revenue-based proxy (P/S) if earnings negative
                current_ps = market_cap / rev[-1]
                est_mc = rev[i] * current_ps
                yearly_rates.append(bb / est_mc)
        if yearly_rates:
            # Use last 3 years if available
            recent_rates = yearly_rates[-3:] if len(yearly_rates) >= 3 else yearly_rates
            buyback_rate = round(sum(recent_rates) / len(recent_rates), 3)
            buyback_rate = min(buyback_rate, 0.05)  # Cap at 5%
        else:
            buyback_rate = 0.01
    else:
        buyback_rate = 0.01

    # ── Tax rate ──
    tax_prov = financials["tax_provision"]
    pretax = financials["pretax_income"]
    if tax_prov and pretax and pretax[-1] > 0 and tax_prov[-1] > 0:
        tax_rate = round(tax_prov[-1] / pretax[-1], 2)
        tax_rate = min(max(tax_rate, 0.05), 0.35)  # Sanity bounds
    else:
        tax_rate = 0.21  # US statutory

    # ── Sales-to-Capital ratio ──
    # Average change in revenue / change in invested capital
    s2c_ratios = []
    for i in range(1, n):
        rev_change = rev[i] - rev[i-1]
        ca = financials["current_assets"]
        cs = financials["cash"]
        si = financials["st_investments"]
        cl = financials["current_liabilities"]
        sd = financials["st_debt"]
        sl = financials["st_leases"]
        pp = financials["net_ppe"]
        gi = financials["goodwill_intang"]

        # Non-cash working capital
        ncwc_now = (ca[i] - cs[i] - si[i]) - (cl[i] - sd[i] - sl[i])
        ncwc_prev = (ca[i-1] - cs[i-1] - si[i-1]) - (cl[i-1] - sd[i-1] - sl[i-1])
        delta_ncwc = ncwc_now - ncwc_prev

        # Invested capital change
        delta_ppe = pp[i] - pp[i-1]
        delta_gi = gi[i] - gi[i-1]
        ic_change = delta_ncwc + delta_ppe + delta_gi

        if ic_change > 0 and rev_change != 0:
            s2c_ratios.append(rev_change / ic_change)

    if s2c_ratios:
        # Use median to avoid outliers
        s2c_ratios.sort()
        sales_to_capital = round(s2c_ratios[len(s2c_ratios) // 2], 2)
        sales_to_capital = min(max(sales_to_capital, 0.20), 5.0)  # Sanity bounds
    else:
        sales_to_capital = 1.0

    # ── Debt breakdown ──
    lt_debt = financials["lt_debt_latest"]
    lt_leases = financials["lt_leases_latest"]
    st_debt_val = financials["st_debt_latest"]
    fin_leases = financials["finance_leases_latest"]
    total_debt = lt_debt + lt_leases + st_debt_val + fin_leases

    debt_breakdown = []
    if lt_debt > 0:
        debt_breakdown.append(("Long-Term Debt", lt_debt))
    if fin_leases > 0:
        debt_breakdown.append(("Finance Leases", fin_leases))
    if lt_leases > 0:
        debt_breakdown.append(("Operating Leases", lt_leases))
    if st_debt_val > 0:
        debt_breakdown.append(("Short-Term Debt", st_debt_val))
    if not debt_breakdown:
        debt_breakdown.append(("Total Debt", 0))
        total_debt = 0

    # ── Cash bridge ──
    cash_bridge = financials["cash"][-1] if financials["cash"] else 0
    securities = financials["st_investments"][-1] if financials["st_investments"] else 0

    # ── Build config dict ──
    cfg = {
        "company": company_name,
        "ticker": ticker.upper(),
        "valuation_date": datetime.now().strftime("%b %Y"),

        "stock_price": stock_price,
        "equity_market_value": market_cap,
        "debt_market_value": total_debt,

        "risk_free_rate": risk_free_rate,
        "erp": ERP_DEFAULT,
        "credit_spread": credit_spread,
        "tax_rate": tax_rate,

        "sector_betas": sector_betas,
        "debt_breakdown": debt_breakdown,

        "base_year": base_year,
        "base_revenue": base_revenue,
        "base_oi": base_oi,
        "base_op_margin": base_op_margin,

        "revenue_growth": revenue_growth,
        "op_margins": op_margins,

        "terminal_growth": term_growth,
        "terminal_margin": term_margin,
        "sales_to_capital": sales_to_capital,
        "sbc_pct": sbc_pct,

        "shares_outstanding": shares,
        "buyback_rate": buyback_rate,
        "margin_of_safety": margin_of_safety or MARGIN_OF_SAFETY_DEFAULT,

        "cash_bridge": cash_bridge,
        "securities": securities,

        "ic_years": years,
        "current_assets": financials["current_assets"],
        "cash": financials["cash"],
        "st_investments": financials["st_investments"],
        "operating_cash": [0] * n,
        "current_liabilities": financials["current_liabilities"],
        "st_debt": financials["st_debt"],
        "st_leases": financials["st_leases"],
        "net_ppe": financials["net_ppe"],
        "goodwill_intang": financials["goodwill_intang"],

        "hist_revenue": rev,
        "hist_operating_income": oi,
        "hist_net_income": ni,
        "hist_cost_of_revenue": financials["cost_of_revenue"],
        "hist_sbc_values": sbc_vals,
        "hist_shares": financials["shares"],

        "bull_growth_adj": 0.02,
        "bull_margin_adj": 0.02,
        "bear_growth_adj": -0.04,
        "bear_margin_adj": -0.02,

        "peers": peers,
    }

    # Print summary
    print(f"\n  Company:            {company_name} ({ticker.upper()})")
    print(f"  Base Year:          {base_year}")
    print(f"  Revenue:            ${base_revenue:,.0f}M")
    print(f"  Operating Income:   ${base_oi:,.0f}M ({base_op_margin:.1%})")
    print(f"  Stock Price:        ${stock_price:.2f}")
    print(f"  Market Cap:         ${market_cap:,.0f}M")
    print(f"  Credit Rating:      {credit_rating} (spread: {credit_spread:.2%})")
    print(f"  Tax Rate:           {tax_rate:.1%}")
    print(f"  SBC %:              {sbc_pct:.2%}")
    print(f"  Buyback Rate:       {buyback_rate:.1%}")
    print(f"  Sales/Capital:      {sales_to_capital:.2f}")
    print(f"  Starting Growth:    {revenue_growth[0]:.1%} → Terminal: {term_growth:.1%}")
    print(f"  Starting Margin:    {op_margins[0]:.1%} → Terminal: {term_margin:.1%}")
    print(f"  Peers:              {len(peers)}")

    return cfg


def write_config(cfg, output_path):
    """Write config dict to a Python file matching msft_config.py format."""
    ticker = cfg["ticker"]

    lines = []
    lines.append(f'"""')
    lines.append(f'{cfg["company"]} ({ticker}) -- DCF Configuration')
    lines.append(f"{'=' * 52}")
    lines.append(f"Auto-generated: {datetime.now().strftime('%B %d, %Y')}")
    lines.append(f"Source: EDGAR XBRL + Yahoo Finance + Treasury.gov")
    lines.append(f"")
    lines.append(f"Usage:")
    lines.append(f"    exec(open('dcf_template.py').read())")
    lines.append(f"    exec(open('configs/{ticker.lower()}_config.py').read())")
    lines.append(f"    build_dcf_model(cfg, 'output/{ticker}_DCF.xlsx')")
    lines.append(f"")
    lines.append(f"All values in $M unless noted. Blue cells in Excel = editable assumptions.")
    lines.append(f'"""')
    lines.append(f"")
    lines.append(f"cfg = {{")

    def _fmt_val(v, indent=4):
        """Format a value for Python config output."""
        if isinstance(v, str):
            return f"'{v}'"
        elif isinstance(v, float):
            if abs(v) < 0.001 and v != 0:
                return f"{v}"
            elif abs(v) < 1:
                # Percentage-like values
                return f"{v}"
            else:
                # Large numbers: use underscores for readability
                if abs(v) >= 1000:
                    return f"{v:_.0f}".replace(".", "")
                else:
                    return f"{v:.2f}" if v != int(v) else f"{v:.0f}"
        elif isinstance(v, int):
            if abs(v) >= 1000:
                return f"{v:_}"
            return str(v)
        elif isinstance(v, list):
            return None  # Handled separately
        elif isinstance(v, tuple):
            return None  # Handled separately
        return repr(v)

    def _fmt_number(v):
        """Format a number for a list."""
        if isinstance(v, float):
            if v == 0:
                return "0"
            if abs(v) < 1:
                # Small decimals (percentages, ratios)
                if v == round(v, 3):
                    return f"{v:.3f}".rstrip("0").rstrip(".")
                    # Actually keep precision for growth/margin arrays
                return f"{v}"
            if abs(v) >= 1000:
                return f"{v:_.0f}"
            if v == int(v):
                return f"{int(v)}"
            return f"{v:.1f}"
        if isinstance(v, int):
            if abs(v) >= 1000:
                return f"{v:_}"
            return str(v)
        return repr(v)

    # Section: Company Info
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # COMPANY INFO")
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    'company': {_fmt_val(cfg['company'])},")
    lines.append(f"    'ticker': {_fmt_val(cfg['ticker'])},")
    lines.append(f"    'valuation_date': {_fmt_val(cfg['valuation_date'])},")
    lines.append(f"")

    # Section: Market Data
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # MARKET DATA")
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    'stock_price': {_fmt_val(cfg['stock_price'])},")
    lines.append(f"    'equity_market_value': {_fmt_val(cfg['equity_market_value'])},")
    lines.append(f"    'debt_market_value': {_fmt_val(cfg['debt_market_value'])},")
    lines.append(f"")

    # Section: WACC
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # WACC INPUTS")
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    'risk_free_rate': {cfg['risk_free_rate']},")
    lines.append(f"    'erp': {cfg['erp']},")
    lines.append(f"    'credit_spread': {cfg['credit_spread']},")
    lines.append(f"    'tax_rate': {cfg['tax_rate']},")
    lines.append(f"")

    # Sector betas
    lines.append(f"    'sector_betas': [")
    for sector, beta, weight in cfg["sector_betas"]:
        lines.append(f"        ('{sector}', {beta}, {weight}),")
    lines.append(f"    ],")
    lines.append(f"")

    # Debt breakdown
    lines.append(f"    'debt_breakdown': [")
    for label, amount in cfg["debt_breakdown"]:
        lines.append(f"        ('{label}', {_fmt_number(amount)}),")
    lines.append(f"    ],")
    lines.append(f"")

    # Section: DCF Assumptions
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # DCF ASSUMPTIONS")
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    'base_year': {cfg['base_year']},")
    lines.append(f"    'base_revenue': {_fmt_val(cfg['base_revenue'])},")
    lines.append(f"    'base_oi': {_fmt_val(cfg['base_oi'])},")
    lines.append(f"    'base_op_margin': {cfg['base_op_margin']},")
    lines.append(f"")

    # Revenue growth
    lines.append(f"    'revenue_growth': [")
    for i, g in enumerate(cfg["revenue_growth"]):
        yr = cfg["base_year"] + 1 + i
        lines.append(f"        {g},  # FY{yr}")
    lines.append(f"    ],")
    lines.append(f"")

    # Op margins
    lines.append(f"    'op_margins': [")
    for i, m in enumerate(cfg["op_margins"]):
        yr = cfg["base_year"] + 1 + i
        lines.append(f"        {m},  # FY{yr}")
    lines.append(f"    ],")
    lines.append(f"")

    lines.append(f"    'terminal_growth': {cfg['terminal_growth']},")
    lines.append(f"    'terminal_margin': {cfg['terminal_margin']},")
    lines.append(f"    'sales_to_capital': {cfg['sales_to_capital']},")
    lines.append(f"    'sbc_pct': {cfg['sbc_pct']},")
    lines.append(f"")

    # Shares & Buybacks
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # SHARES & BUYBACKS")
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    'shares_outstanding': {_fmt_val(cfg['shares_outstanding'])},")
    lines.append(f"    'buyback_rate': {cfg['buyback_rate']},")
    lines.append(f"    'margin_of_safety': {cfg['margin_of_safety']},")
    lines.append(f"")

    # Equity Bridge
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # EQUITY BRIDGE")
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    'cash_bridge': {_fmt_val(cfg['cash_bridge'])},")
    lines.append(f"    'securities': {_fmt_val(cfg['securities'])},")
    lines.append(f"")

    # Historical Balance Sheet
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # HISTORICAL BALANCE SHEET (FY{cfg['ic_years'][0]}-FY{cfg['ic_years'][-1]})")
    lines.append(f"    # {'─' * 46}")

    bs_keys = [
        ("ic_years", "ic_years"),
        ("current_assets", "current_assets"),
        ("cash", "cash"),
        ("st_investments", "st_investments"),
        ("operating_cash", "operating_cash"),
        ("current_liabilities", "current_liabilities"),
        ("st_debt", "st_debt"),
        ("st_leases", "st_leases"),
        ("net_ppe", "net_ppe"),
        ("goodwill_intang", "goodwill_intang"),
    ]

    # Find max key length for alignment
    max_key_len = max(len(k) for k, _ in bs_keys)

    for key, cfg_key in bs_keys:
        vals = cfg[cfg_key]
        # ic_years are plain years, don't underscore-format them
        if key == "ic_years":
            formatted = [str(v) for v in vals]
        else:
            formatted = [_fmt_number(v) for v in vals]
        padding = " " * (max_key_len - len(key))
        val_str = ", ".join(f"{v:>10}" for v in formatted)
        lines.append(f"    '{key}':{padding} [{val_str}],")

    lines.append(f"")

    # Historical Income Statement
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # HISTORICAL INCOME STATEMENT")
    lines.append(f"    # {'─' * 46}")

    is_keys = [
        ("hist_revenue", "hist_revenue"),
        ("hist_operating_income", "hist_operating_income"),
        ("hist_net_income", "hist_net_income"),
        ("hist_cost_of_revenue", "hist_cost_of_revenue"),
        ("hist_sbc_values", "hist_sbc_values"),
        ("hist_shares", "hist_shares"),
    ]

    max_key_len = max(len(k) for k, _ in is_keys)

    for key, cfg_key in is_keys:
        vals = cfg[cfg_key]
        formatted = [_fmt_number(v) for v in vals]
        padding = " " * (max_key_len - len(key))
        val_str = ", ".join(f"{v:>10}" for v in formatted)
        lines.append(f"    '{key}':{padding} [{val_str}],")

    lines.append(f"")

    # Scenario Adjustments
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    # SCENARIO ADJUSTMENTS")
    lines.append(f"    # {'─' * 46}")
    lines.append(f"    'bull_growth_adj': {cfg['bull_growth_adj']},")
    lines.append(f"    'bull_margin_adj': {cfg['bull_margin_adj']},")
    lines.append(f"    'bear_growth_adj': {cfg['bear_growth_adj']},")
    lines.append(f"    'bear_margin_adj': {cfg['bear_margin_adj']},")
    lines.append(f"")

    # Peers
    if cfg["peers"]:
        lines.append(f"    # {'─' * 46}")
        lines.append(f"    # PEER COMPARISON DATA")
        lines.append(f"    # {'─' * 46}")
        lines.append(f"    'peers': [")
        for p in cfg["peers"]:
            lines.append(f"        {{")
            lines.append(f"            'ticker': '{p['ticker']}', 'name': '{p['name']}',")
            lines.append(f"            'ev_revenue': {p['ev_revenue']}, 'ev_ebitda': {p['ev_ebitda']}, 'pe': {p['pe']},")
            lines.append(f"            'op_margin': {p['op_margin']}, 'rev_growth': {p['rev_growth']}, 'roic': {p['roic']},")
            lines.append(f"        }},")
        lines.append(f"    ],")
    else:
        lines.append(f"    'peers': [],")

    lines.append(f"}}")
    lines.append(f"")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\nConfig saved to: {output_path}")


# ── Fundamentals Fetcher (for Streamlit app) ─────────────────────────

def fetch_fundamentals(ticker, n_years=10):
    """Fetch historical financial fundamentals from yfinance + EDGAR fallback.

    Returns dict with sorted years and aligned lists:
        years, revenue, operating_income, net_income, cost_of_revenue,
        tax_provision, pretax_income  (all $M)
        total_equity, total_debt, cash  (all $M)
        shares  (raw count, NOT millions)
        capex, cfo, fcf  (all $M)
    """
    M = 1_000_000

    # Collect data keyed by year
    data_by_year = {}  # year -> {metric: value}

    metrics = [
        "revenue", "operating_income", "net_income", "cost_of_revenue",
        "tax_provision", "pretax_income",
        "total_equity", "total_debt", "cash", "shares",
        "capex", "cfo",
        "total_assets", "current_liabilities", "goodwill", "intangibles",
        "ppe", "da", "gross_profit", "eps", "dividends_per_share",
    ]

    def _safe(val):
        """Return None if val is NaN or None, else float."""
        if val is None:
            return None
        try:
            v = float(val)
            if v != v:  # NaN check
                return None
            return v
        except (TypeError, ValueError):
            return None

    # ── Primary: yfinance ──────────────────────────────────────────
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)

        # Income statement — columns are dates, rows are line items
        inc = t.income_stmt
        if inc is not None and not inc.empty:
            for col in inc.columns:
                yr = col.year
                if yr not in data_by_year:
                    data_by_year[yr] = {}
                d = data_by_year[yr]

                for label, key in [
                    ("Total Revenue", "revenue"),
                    ("Operating Income", "operating_income"),
                    ("Net Income", "net_income"),
                    ("Cost Of Revenue", "cost_of_revenue"),
                    ("Tax Provision", "tax_provision"),
                    ("Pretax Income", "pretax_income"),
                    ("Gross Profit", "gross_profit"),
                    ("Diluted EPS", "eps"),
                ]:
                    if label in inc.index:
                        v = _safe(inc.at[label, col])
                        if v is not None:
                            if key == "eps":
                                d[key] = v  # per-share value, no conversion
                            else:
                                d[key] = round(v / M, 0)

        # Balance sheet
        bs = t.balance_sheet
        if bs is not None and not bs.empty:
            for col in bs.columns:
                yr = col.year
                if yr not in data_by_year:
                    data_by_year[yr] = {}
                d = data_by_year[yr]

                for label, key in [
                    ("Stockholders Equity", "total_equity"),
                    ("Total Debt", "total_debt"),
                    ("Cash And Cash Equivalents", "cash"),
                    ("Ordinary Shares Number", "shares"),
                    ("Total Assets", "total_assets"),
                    ("Current Liabilities", "current_liabilities"),
                    ("Goodwill", "goodwill"),
                    ("Intangible Assets", "intangibles"),
                    ("Net PPE", "ppe"),
                ]:
                    if label in bs.index:
                        v = _safe(bs.at[label, col])
                        if v is not None:
                            if key == "shares":
                                d[key] = v  # raw count, no conversion
                            else:
                                d[key] = round(v / M, 0)

        # Cash flow
        cf = t.cashflow
        if cf is not None and not cf.empty:
            for col in cf.columns:
                yr = col.year
                if yr not in data_by_year:
                    data_by_year[yr] = {}
                d = data_by_year[yr]

                for label, key in [
                    ("Operating Cash Flow", "cfo"),
                    ("Capital Expenditure", "capex"),
                    ("Depreciation And Amortization", "da"),
                ]:
                    if label in cf.index:
                        v = _safe(cf.at[label, col])
                        if v is not None:
                            d[key] = round(v / M, 0)

    except Exception as e:
        print(f"[yfinance] Warning: {e}")

    # ── Fallback: EDGAR XBRL ──────────────────────────────────────
    try:
        cik = get_cik(ticker)
        facts = fetch_company_facts(cik)
        edgar = parse_financials(facts, n_years)

        edgar_years = edgar.get("years", [])
        edgar_map = {
            "revenue": "revenue",
            "operating_income": "operating_income",
            "net_income": "net_income",
            "cost_of_revenue": "cost_of_revenue",
            "tax_provision": "tax_provision",
            "pretax_income": "pretax_income",
            "cash": "cash",
        }
        # shares in parse_financials are in millions — multiply by 1e6
        # for raw count

        for i, yr in enumerate(edgar_years):
            if yr not in data_by_year:
                data_by_year[yr] = {}
            d = data_by_year[yr]

            for edgar_key, our_key in edgar_map.items():
                if our_key not in d or d[our_key] is None:
                    vals = edgar.get(edgar_key, [])
                    if i < len(vals) and vals[i] is not None:
                        d[our_key] = vals[i]  # already in millions

            # shares: EDGAR gives millions, we need raw count
            if "shares" not in d or d["shares"] is None:
                shares_list = edgar.get("shares", [])
                if i < len(shares_list) and shares_list[i] is not None:
                    d["shares"] = shares_list[i] * M  # millions → raw

        # Direct XBRL fallback for metrics not in parse_financials
        _extra_tags = {
            "total_equity": ["StockholdersEquity",
                             "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
            "total_debt": ["LongTermDebt", "LongTermDebtAndCapitalLeaseObligations"],
            "cfo": ["NetCashProvidedByOperatingActivities",
                    "NetCashProvidedByUsedInOperatingActivities"],
            "capex": ["PaymentsToAcquirePropertyPlantAndEquipment",
                      "PaymentsToAcquireProductiveAssets"],
            "total_assets": ["Assets"],
            "current_liabilities": ["LiabilitiesCurrent"],
            "goodwill": ["Goodwill"],
            "intangibles": ["IntangibleAssetsNetExcludingGoodwill"],
            "ppe": ["PropertyPlantAndEquipmentNet",
                    "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"],
            "da": ["DepreciationDepletionAndAmortization",
                   "DepreciationAndAmortization"],
            "gross_profit": ["GrossProfit"],
        }
        for our_key, tags in _extra_tags.items():
            tag_data = _try_tags(facts, tags, n_years)
            for yr_val, val in tag_data:
                if yr_val not in data_by_year:
                    data_by_year[yr_val] = {}
                d = data_by_year[yr_val]
                if our_key not in d or d[our_key] is None:
                    if our_key == "capex":
                        d[our_key] = -round(val / M, 0)  # negate: EDGAR reports as positive
                    else:
                        d[our_key] = round(val / M, 0)

        # Extended pass: try each tag individually to fill older years
        # (_try_tags picks only the best tag, missing older-named variants)
        _extended_tags = [
            ("revenue", "SalesRevenueNet", "USD"),
            ("revenue", "Revenues", "USD"),
            ("operating_income", "OperatingIncomeLoss", "USD"),
            ("net_income", "NetIncomeLoss", "USD"),
            ("cost_of_revenue", "CostOfGoodsAndServicesSold", "USD"),
            ("cost_of_revenue", "CostOfRevenue", "USD"),
            ("pretax_income", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "USD"),
            ("tax_provision", "IncomeTaxExpenseBenefit", "USD"),
        ]
        for our_key, tag, unit in _extended_tags:
            data = _extract_annual_values(facts, tag, n_years, unit)
            for yr_val, val in data:
                if yr_val not in data_by_year:
                    data_by_year[yr_val] = {}
                d = data_by_year[yr_val]
                if our_key not in d or d[our_key] is None:
                    d[our_key] = round(val / M, 0)

        # Shares: separate fallback with unit_key="shares" (raw count, not USD)
        _shares_tags = ["WeightedAverageNumberOfDilutedSharesOutstanding",
                        "CommonStockSharesOutstanding"]
        shares_data = _try_tags(facts, _shares_tags, n_years, unit_key="shares")
        for yr_val, val in shares_data:
            if yr_val not in data_by_year:
                data_by_year[yr_val] = {}
            d = data_by_year[yr_val]
            if "shares" not in d or d["shares"] is None:
                d["shares"] = val  # already raw count

        # EPS: separate fallback with unit_key="USD/shares"
        _eps_tags = ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"]
        eps_data = _try_tags(facts, _eps_tags, n_years, unit_key="USD/shares")
        for yr_val, val in eps_data:
            if yr_val not in data_by_year:
                data_by_year[yr_val] = {}
            d = data_by_year[yr_val]
            if "eps" not in d or d["eps"] is None:
                d["eps"] = val  # already per-share dollar value

        # Dividends per share: separate fallback with unit_key="USD/shares"
        _dps_tags = ["CommonStockDividendsPerShareDeclared",
                     "CommonStockDividendsPerShareCashPaid"]
        dps_data = _try_tags(facts, _dps_tags, n_years, unit_key="USD/shares")
        for yr_val, val in dps_data:
            if yr_val not in data_by_year:
                data_by_year[yr_val] = {}
            d = data_by_year[yr_val]
            if "dividends_per_share" not in d or d["dividends_per_share"] is None:
                d["dividends_per_share"] = val

    except Exception as e:
        print(f"[EDGAR] Warning: {e}")

    # ── Assemble result ───────────────────────────────────────────
    all_years = sorted(data_by_year.keys())
    if len(all_years) > n_years:
        all_years = all_years[-n_years:]

    result = {"years": all_years}
    for key in metrics:
        result[key] = [data_by_year[yr].get(key) for yr in all_years]

    # Adjust shares for stock splits — walk backwards from most recent,
    # detect jumps > 1.5x and apply cumulative split ratio
    shares = result["shares"]
    for i in range(len(shares) - 1, 0, -1):
        if shares[i] is not None and shares[i - 1] is not None and shares[i - 1] > 0:
            ratio = shares[i] / shares[i - 1]
            if ratio > 1.5:
                # Stock split detected — adjust all prior years
                for j in range(i):
                    if shares[j] is not None:
                        shares[j] = round(shares[j] * ratio)

    # Compute FCF = CFO + CapEx (capex is already negative from yfinance)
    result["fcf"] = []
    for cfo_val, capex_val in zip(result["cfo"], result["capex"]):
        if cfo_val is not None and capex_val is not None:
            result["fcf"].append(round(cfo_val + capex_val, 0))
        else:
            result["fcf"].append(None)

    # Compute gross_profit from revenue - cost_of_revenue if not directly available
    for i in range(len(result["gross_profit"])):
        if result["gross_profit"][i] is None and result["revenue"][i] is not None and result["cost_of_revenue"][i] is not None:
            result["gross_profit"][i] = round(result["revenue"][i] - result["cost_of_revenue"][i], 0)

    return result


# ── CLI Entry Point ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Automated DCF data gathering pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 gather_data.py PANW --auto-peers
  python3 gather_data.py PANW --peers "CRWD,FTNT,ZS,S"
  python3 gather_data.py PANW --sectors "Software (System & Application):1.23:1.0" --peers auto
  python3 gather_data.py MSFT --peers "AAPL,GOOGL,AMZN,META" --margin-of-safety 0.25
        """,
    )
    parser.add_argument("ticker", help="Stock ticker symbol (e.g., PANW, MSFT)")
    parser.add_argument(
        "--sectors",
        help='Sector betas as "Name:beta:weight,Name2:beta2:weight2" (overrides SIC auto-detect)',
    )
    parser.add_argument(
        "--peers",
        help='Comma-separated peer tickers (e.g., "CRWD,FTNT,ZS,S"). Use "auto" for auto-discovery.',
    )
    parser.add_argument(
        "--auto-peers",
        action="store_true",
        help="Auto-discover peers from SIC code (same as --peers auto)",
    )
    parser.add_argument(
        "--n-peers",
        type=int,
        default=6,
        help="Number of peers to auto-select (default: 6)",
    )
    parser.add_argument(
        "--margin-of-safety",
        type=float,
        default=None,
        help=f"Margin of safety (default: {MARGIN_OF_SAFETY_DEFAULT*100:.0f}%%)",
    )
    parser.add_argument(
        "--terminal-growth",
        type=float,
        default=None,
        help=f"Terminal growth rate (default: {TERMINAL_GROWTH_DEFAULT*100:.1f}%%)",
    )
    parser.add_argument(
        "--output",
        help="Output file path (default: configs/<ticker>_config.py)",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Also build the DCF Excel model after generating the config",
    )

    args = parser.parse_args()
    ticker = args.ticker.upper()

    print(f"{'=' * 60}")
    print(f"  DCF Data Gathering Pipeline — {ticker}")
    print(f"{'=' * 60}\n")

    # ── Step 1: SEC EDGAR — Company lookup & financials ──
    cik = get_cik(ticker)

    time.sleep(0.2)

    # Get SIC code and company name from submissions
    submissions = fetch_company_submissions(cik)
    company_name = submissions.get("name", ticker)
    sic_code = int(submissions.get("sic", 0))
    sic_desc = submissions.get("sicDescription", "")

    print(f"  Company: {company_name}")
    print(f"  SIC: {sic_code} — {sic_desc}")

    time.sleep(0.2)

    # ── Step 2: Determine sector betas ──
    if args.sectors:
        # Parse user-provided sectors
        sector_betas = []
        for part in args.sectors.split(","):
            fields = part.strip().rsplit(":", 2)
            if len(fields) == 3:
                name, beta, weight = fields[0], float(fields[1]), float(fields[2])
                sector_betas.append((name, beta, weight))
            else:
                print(f"  WARNING: Invalid sector format: '{part}' — expected 'Name:beta:weight'")
        if not sector_betas:
            print("  ERROR: No valid sectors parsed. Use format: 'Name:beta:weight'")
            sys.exit(1)
    else:
        # Auto-detect from SIC code
        if sic_code in SIC_TO_SECTOR:
            sector_name, sector_beta = SIC_TO_SECTOR[sic_code]
            print(f"\n  SIC {sic_code} → Suggested: {sector_name}, beta {sector_beta}")
            sector_betas = [(sector_name, sector_beta, 1.0)]
        else:
            # Try to fetch from Damodaran
            print(f"\n  SIC {sic_code} not in lookup table, trying Damodaran...")
            dam_betas = fetch_sector_betas()
            if dam_betas:
                # Try fuzzy match on SIC description
                best_match = None
                best_score = 0
                sic_words = set(sic_desc.lower().split())
                for sector, beta in dam_betas.items():
                    sector_words = set(sector.lower().split())
                    overlap = len(sic_words & sector_words)
                    if overlap > best_score:
                        best_score = overlap
                        best_match = (sector, beta)

                if best_match and best_score > 0:
                    sector_name, sector_beta = best_match
                    print(f"  Best match: {sector_name}, beta {sector_beta}")
                    sector_betas = [(sector_name, sector_beta, 1.0)]
                else:
                    print("  No sector match found, using market beta 1.0")
                    sector_betas = [("Market", 1.0, 1.0)]
            else:
                print("  Using market beta 1.0")
                sector_betas = [("Market", 1.0, 1.0)]

    print(f"\n  Sector betas: {sector_betas}")

    # ── Step 3: Fetch financials from EDGAR ──
    facts = fetch_company_facts(cik)
    financials = parse_financials(facts, n_years=6)

    # ── Step 4: Market data ──
    stock_price, market_cap, shares_yahoo = fetch_stock_price(ticker)
    risk_free_rate = fetch_treasury_yield()

    # ── Step 5: Credit rating ──
    oi_latest = financials["operating_income"][-1] if financials["operating_income"] else 0
    ie_latest = financials["interest_expense_latest"]
    credit_rating, credit_spread = synthetic_credit_rating(oi_latest, ie_latest)

    # Compute market cap from EDGAR shares if Yahoo didn't provide it
    if market_cap == 0 and stock_price > 0:
        edgar_shares = financials["shares"][-1] if financials["shares"] and financials["shares"][-1] > 0 else 0
        if edgar_shares > 0:
            market_cap = round(stock_price * edgar_shares, 0)
            print(f"  Market Cap (EDGAR shares): ${market_cap:,.0f}M")

    # ── Step 5b: Sector median margin from Damodaran ──
    sector_margin = None
    sector_name_for_margin = sector_betas[0][0] if sector_betas else ""
    if sector_name_for_margin:
        dam_margins = fetch_sector_margins()
        if dam_margins:
            # Try exact match first, then fuzzy
            if sector_name_for_margin in dam_margins:
                sector_margin = dam_margins[sector_name_for_margin]
            else:
                # Fuzzy: find best overlap
                target_words = set(sector_name_for_margin.lower().replace("/", " ").split())
                best_match, best_score = None, 0
                for sec_name, sec_margin in dam_margins.items():
                    sec_words = set(sec_name.lower().replace("/", " ").split())
                    overlap = len(target_words & sec_words)
                    if overlap > best_score:
                        best_score = overlap
                        best_match = (sec_name, sec_margin)
                if best_match and best_score > 0:
                    sector_margin = best_match[1]
                    print(f"  Sector margin match: '{best_match[0]}' → {sector_margin:.1%}")

    # ── Step 5c: Consensus estimates ──
    consensus = fetch_consensus_estimates(ticker)

    # ── Step 6: Peer data ──
    peer_tickers = []
    auto_peers = args.auto_peers or (args.peers and args.peers.strip().lower() == "auto")

    if auto_peers:
        # Auto-discover peers from SIC code + market cap similarity
        peer_tickers = find_peers(
            sic_code=sic_code,
            target_ticker=ticker,
            target_market_cap=market_cap,
            n_peers=args.n_peers,
        )
    elif args.peers and args.peers.strip().lower() != "auto":
        peer_tickers = [t.strip().upper() for t in args.peers.split(",") if t.strip()]

    peers = fetch_peer_data(peer_tickers)

    # ── Step 7: Build config ──
    cfg = build_config(
        ticker=ticker,
        financials=financials,
        stock_price=stock_price,
        market_cap=market_cap,
        shares_yahoo=shares_yahoo,
        risk_free_rate=risk_free_rate,
        sector_betas=sector_betas,
        credit_spread=credit_spread,
        credit_rating=credit_rating,
        peers=peers,
        company_name=company_name,
        margin_of_safety=args.margin_of_safety,
        terminal_growth=args.terminal_growth,
        sector_margin=sector_margin,
        consensus=consensus,
    )

    # ── Step 8: Write config file ──
    script_dir = os.path.dirname(os.path.abspath(__file__))
    configs_dir = os.path.join(script_dir, "configs")
    os.makedirs(configs_dir, exist_ok=True)

    output_path = args.output or os.path.join(configs_dir, f"{ticker.lower()}_config.py")
    write_config(cfg, output_path)

    print(f"\n{'=' * 60}")
    print(f"  Config ready at: {output_path}")
    print(f"{'=' * 60}")

    # ── Step 9: Build DCF Excel model ──
    if args.build:
        output_dir = os.path.expanduser("~/Desktop/DCF Output")
        os.makedirs(output_dir, exist_ok=True)
        excel_path = os.path.join(output_dir, f"{ticker}_DCF.xlsx")

        print(f"\n[Build] Generating DCF model → {excel_path}")
        template_path = os.path.join(script_dir, "dcf_template.py")

        try:
            ns = {}
            exec(open(template_path).read(), ns)
            ns["build_dcf_model"](cfg, excel_path)
            print(f"\n{'=' * 60}")
            print(f"  DCF model saved: {excel_path}")
            print(f"{'=' * 60}")
        except Exception as e:
            print(f"\n  ERROR building DCF: {e}")
            print(f"  Config was saved — you can build manually:")
            print(f"    exec(open('dcf_template.py').read())")
            print(f"    exec(open('{output_path}').read())")
            print(f"    build_dcf_model(cfg, '{excel_path}')")
    else:
        print(f"\nNext steps:")
        print(f"  python3 gather_data.py {ticker} --build   # or manually:")
        print(f"  exec(open('dcf_template.py').read())")
        print(f"  exec(open('{output_path}').read())")
        print(f"  build_dcf_model(cfg, os.path.expanduser('~/Desktop/DCF Output/{ticker}_DCF.xlsx'))")


if __name__ == "__main__":
    main()
