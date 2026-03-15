"""
Unit tests for ibkr_api.py and trade_utils.py.

All IBKR / Streamlit / yfinance / ibflex dependencies are mocked so tests run
without network access, broker credentials, or a Streamlit runtime.
"""

import ssl
import time
import unittest
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd


# ---------------------------------------------------------------------------
# Mock ibflex enums — lightweight stand-ins for the real enum members
# ---------------------------------------------------------------------------
class _MockAssetClass:
    STOCK = "STK"
    OPTION = "OPT"

class _MockBuySell:
    BUY = "BUY"
    SELL = "SELL"

class _MockPutCall:
    PUT = "P"
    CALL = "C"

class _MockCashAction:
    DEPOSITWITHDRAW = "DEPOSITWITHDRAW"
    BROKERINTPAID = "BROKERINTPAID"
    BROKERINTRCVD = "BROKERINTRCVD"
    DIVIDEND = "DIVIDEND"


def _make_trade(symbol="AAPL", asset_class=None, buy_sell=None, put_call=None,
                quantity=1, price=100.0, net_cash=100.0, trade_date=None,
                report_date=None, description="", multiplier=100,
                transaction_type=""):
    """Helper: build a SimpleNamespace that quacks like an ibflex Trade."""
    return SimpleNamespace(
        symbol=symbol,
        assetCategory=asset_class or _MockAssetClass.STOCK,
        buySell=buy_sell or _MockBuySell.BUY,
        putCall=put_call,
        quantity=Decimal(str(quantity)),
        tradePrice=Decimal(str(price)),
        netCash=Decimal(str(net_cash)),
        tradeDate=trade_date or date(2025, 6, 1),
        reportDate=report_date or date(2025, 6, 1),
        description=description,
        multiplier=Decimal(str(multiplier)),
        transactionType=transaction_type,
        position=Decimal(str(quantity)),
    )


def _make_position(symbol="AAPL", asset_class=None, quantity=100,
                   put_call=None, multiplier=100, description=""):
    return SimpleNamespace(
        symbol=symbol,
        assetCategory=asset_class or _MockAssetClass.STOCK,
        position=Decimal(str(quantity)),
        putCall=put_call,
        multiplier=Decimal(str(multiplier)),
        description=description or symbol,
    )


def _make_equity_summary(report_date, cash=5000, total=50000):
    return SimpleNamespace(
        reportDate=report_date,
        cash=Decimal(str(cash)),
        total=Decimal(str(total)),
    )


def _make_cash_tx(tx_type, amount, report_date, date_time=None):
    return SimpleNamespace(
        type=tx_type,
        amount=Decimal(str(amount)),
        reportDate=report_date,
        dateTime=date_time,
    )


def _make_statement(**overrides):
    """Return a SimpleNamespace that mimics a FlexStatement."""
    defaults = {
        "accountId": "U12345",
        "ChangeInNAV": SimpleNamespace(endingValue=Decimal("100000")),
        "EquitySummaryInBase": [],
        "CashReport": [],
        "OpenPositions": [],
        "Trades": [],
        "CashTransactions": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Helpers for patching session_state cache
# ---------------------------------------------------------------------------
def _cache_with_stmt(stmt):
    """Return a session_state dict that holds a fresh Flex cache entry."""
    return {
        "_ibkr_flex_cache": (datetime.now(), stmt),
        "ibkr_credentials": {"ibkr_flex_token": "tok", "ibkr_flex_query_id": "123"},
    }


# We need to mock streamlit and ibflex before importing ibkr_api
_mock_st = MagicMock()
_mock_st.session_state = {}

_mock_ibflex_enums = MagicMock()
_mock_ibflex_enums.AssetClass = _MockAssetClass
_mock_ibflex_enums.BuySell = _MockBuySell
_mock_ibflex_enums.PutCall = _MockPutCall
_mock_ibflex_enums.CashAction = _MockCashAction

_mock_ibflex_parser = MagicMock()
_mock_ibflex_parser.parse_data_element = MagicMock()

_mock_ibflex_types = MagicMock()

import sys
# Force-replace modules so mocks work even when run after other test files
sys.modules["streamlit"] = _mock_st
sys.modules["ibflex"] = MagicMock()
sys.modules["ibflex.enums"] = _mock_ibflex_enums
sys.modules["ibflex.parser"] = _mock_ibflex_parser
sys.modules["ibflex.Types"] = _mock_ibflex_types
sys.modules["ibflex.client"] = MagicMock()
sys.modules["error_logger"] = SimpleNamespace(log_error=lambda *a, **kw: None)

# Force reimport with mocks in place
if "ibkr_api" in sys.modules:
    del sys.modules["ibkr_api"]
import ibkr_api
from trade_utils import detect_wheels


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeFlexTrade(unittest.TestCase):
    """Test _normalize_flex_trade label logic for all paths."""

    def _norm(self, **kw):
        with patch("ibkr_api.AssetClass", _MockAssetClass, create=True), \
             patch("ibkr_api.BuySell", _MockBuySell, create=True), \
             patch("ibkr_api.PutCall", _MockPutCall, create=True), \
             patch.dict("sys.modules", {"ibflex.enums": _mock_ibflex_enums}):
            return ibkr_api._normalize_flex_trade(_make_trade(**kw))

    def test_csp_label(self):
        rec = self._norm(asset_class=_MockAssetClass.OPTION,
                         buy_sell=_MockBuySell.SELL,
                         put_call=_MockPutCall.PUT,
                         net_cash=150)
        self.assertEqual(rec["label"], "CSP")
        self.assertEqual(rec["instrument_type"], "Option")

    def test_cc_label(self):
        rec = self._norm(asset_class=_MockAssetClass.OPTION,
                         buy_sell=_MockBuySell.SELL,
                         put_call=_MockPutCall.CALL,
                         net_cash=200)
        self.assertEqual(rec["label"], "CC")

    def test_btc_label(self):
        rec = self._norm(asset_class=_MockAssetClass.OPTION,
                         buy_sell=_MockBuySell.BUY,
                         net_cash=-150)
        self.assertEqual(rec["label"], "BTC")

    def test_buy_option_label(self):
        rec = self._norm(asset_class=_MockAssetClass.OPTION,
                         buy_sell=_MockBuySell.BUY,
                         net_cash=50)
        self.assertEqual(rec["label"], "Buy Option")

    def test_stock_buy(self):
        rec = self._norm(asset_class=_MockAssetClass.STOCK,
                         buy_sell=_MockBuySell.BUY)
        self.assertEqual(rec["label"], "Stock Buy")
        self.assertEqual(rec["instrument_type"], "Equity")

    def test_stock_sell(self):
        rec = self._norm(asset_class=_MockAssetClass.STOCK,
                         buy_sell=_MockBuySell.SELL)
        self.assertEqual(rec["label"], "Stock Sell")

    def test_quantity_is_absolute(self):
        rec = self._norm(quantity=-10)
        self.assertEqual(rec["quantity"], 10)

    def test_fields_present(self):
        rec = self._norm(symbol="MSFT", description="test desc", price=50.5)
        self.assertEqual(rec["symbol"], "MSFT")
        self.assertEqual(rec["description"], "test desc")
        self.assertAlmostEqual(rec["price"], 50.5)
        self.assertIn("date", rec)
        self.assertIn("net_value", rec)


class TestSSLContext(unittest.TestCase):
    """Test _ssl_context certifi and fallback paths."""

    def test_certifi_path(self):
        mock_certifi = MagicMock()
        mock_certifi.where.return_value = "/path/to/cert.pem"
        with patch.dict("sys.modules", {"certifi": mock_certifi}), \
             patch("ssl.create_default_context") as mock_ctx:
            ctx = ibkr_api._ssl_context()
            mock_ctx.assert_called_once_with(cafile="/path/to/cert.pem")

    def test_fallback_path(self):
        # Remove certifi from modules to trigger ImportError
        with patch.dict("sys.modules", {"certifi": None}):
            ctx = ibkr_api._ssl_context()
            self.assertIsInstance(ctx, ssl.SSLContext)
            self.assertFalse(ctx.check_hostname)


class TestPatchIbflexParser(unittest.TestCase):
    """Test _patch_ibflex_parser double-patch guard."""

    def test_double_patch_guard(self):
        mock_ibflex = MagicMock()
        mock_types = MagicMock()
        mock_ibflex.Types = mock_types

        # Set up an unpatched parse_data_element
        orig_fn = MagicMock()
        orig_fn._patched = False
        mock_ibflex.parser.parse_data_element = orig_fn

        with patch.dict("sys.modules", {
            "ibflex": mock_ibflex,
            "ibflex.parser": mock_ibflex.parser,
            "ibflex.Types": mock_types,
        }):
            ibkr_api._patch_ibflex_parser()
            new_fn = mock_ibflex.parser.parse_data_element
            self.assertTrue(getattr(new_fn, "_patched", False))

            # Second call: should be a no-op (guard prevents re-patching)
            ibkr_api._patch_ibflex_parser()
            self.assertIs(mock_ibflex.parser.parse_data_element, new_fn)


class TestFetchAccountBalances(unittest.TestCase):
    """Test fetch_account_balances with EquitySummaryInBase, fallback, and error."""

    def test_with_equity_summary(self):
        stmt = _make_statement(
            EquitySummaryInBase=[
                _make_equity_summary(date(2025, 5, 1), cash=3000, total=30000),
                _make_equity_summary(date(2025, 6, 1), cash=5000, total=50000),
            ],
        )
        _mock_st.session_state = _cache_with_stmt(stmt)
        result = ibkr_api.fetch_account_balances()
        self.assertEqual(result["cash_balance"], 5000)
        self.assertEqual(result["margin_equity"], 50000)
        self.assertEqual(result["net_liquidating_value"], 100000)

    def test_fallback_to_change_in_nav(self):
        stmt = _make_statement(EquitySummaryInBase=[])
        _mock_st.session_state = _cache_with_stmt(stmt)
        result = ibkr_api.fetch_account_balances()
        self.assertEqual(result["net_liquidating_value"], 100000)
        self.assertEqual(result["cash_balance"], 0)

    def test_cash_report_fallback(self):
        stmt = _make_statement(
            EquitySummaryInBase=[_make_equity_summary(date(2025, 6, 1), cash=0, total=50000)],
            CashReport=[SimpleNamespace(endingCash=Decimal("7777"))],
        )
        _mock_st.session_state = _cache_with_stmt(stmt)
        result = ibkr_api.fetch_account_balances()
        self.assertEqual(result["cash_balance"], 7777)

    def test_error_returns_zeros(self):
        _mock_st.session_state = {}  # no cache, no creds → error
        result = ibkr_api.fetch_account_balances()
        self.assertEqual(result["net_liquidating_value"], 0)
        self.assertEqual(result["cash_balance"], 0)


class TestFetchPortfolioData(unittest.TestCase):
    """Test fetch_portfolio_data position aggregation and cost basis math."""

    def test_stock_positions_and_trades(self):
        stmt = _make_statement(
            OpenPositions=[
                _make_position("AAPL", _MockAssetClass.STOCK, 100),
            ],
            Trades=[
                _make_trade("AAPL", _MockAssetClass.STOCK, _MockBuySell.BUY,
                            quantity=100, price=150, net_cash=-15000,
                            trade_date=date(2025, 1, 15)),
                _make_trade("AAPL", _MockAssetClass.OPTION, _MockBuySell.SELL,
                            put_call=_MockPutCall.PUT, quantity=1, price=3.0,
                            net_cash=300, trade_date=date(2025, 2, 1)),
            ],
        )
        _mock_st.session_state = _cache_with_stmt(stmt)
        cost_basis, account_id = ibkr_api.fetch_portfolio_data()

        self.assertEqual(account_id, "U12345")
        self.assertIn("AAPL", cost_basis)
        cb = cost_basis["AAPL"]
        self.assertEqual(cb["shares_held"], 100)
        self.assertEqual(cb["total_credits"], 300)
        self.assertEqual(cb["total_debits"], -15000)
        self.assertEqual(cb["option_pl"], 300)
        self.assertEqual(cb["equity_cost"], -15000)
        self.assertAlmostEqual(cb["adjusted_cost"], -15000 + 300)
        self.assertAlmostEqual(cb["cost_per_share"], (-15000 + 300) / 100)
        self.assertIsInstance(cb["trades"], list)
        self.assertIsInstance(cb["wheels"], list)

    def test_error_returns_empty(self):
        _mock_st.session_state = {}
        cost_basis, account_id = ibkr_api.fetch_portfolio_data()
        self.assertEqual(cost_basis, {})
        self.assertEqual(account_id, "")


class TestFetchNetLiqHistory(unittest.TestCase):
    """Test fetch_net_liq_history sorting, filtering, and fallback."""

    def test_sorted_and_filtered(self):
        today = date.today()
        entries = [
            _make_equity_summary(today - timedelta(days=400), total=40000),
            _make_equity_summary(today - timedelta(days=100), total=50000),
            _make_equity_summary(today - timedelta(days=10), total=60000),
        ]
        stmt = _make_statement(EquitySummaryInBase=entries)
        _mock_st.session_state = _cache_with_stmt(stmt)

        result = ibkr_api.fetch_net_liq_history("6m")
        # 400-day entry should be filtered out for 6m (180 days)
        self.assertEqual(len(result), 2)
        # Should be sorted by time
        self.assertLessEqual(result[0]["time"], result[1]["time"])

    def test_all_timeframe(self):
        today = date.today()
        entries = [
            _make_equity_summary(today - timedelta(days=400), total=40000),
            _make_equity_summary(today - timedelta(days=10), total=60000),
        ]
        stmt = _make_statement(EquitySummaryInBase=entries)
        _mock_st.session_state = _cache_with_stmt(stmt)

        result = ibkr_api.fetch_net_liq_history("all")
        self.assertEqual(len(result), 2)

    def test_fallback_to_change_in_nav(self):
        stmt = _make_statement(EquitySummaryInBase=None)
        stmt.EquitySummaryInBase = None  # force hasattr to fail
        del stmt.EquitySummaryInBase
        _mock_st.session_state = _cache_with_stmt(stmt)
        result = ibkr_api.fetch_net_liq_history()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["close"], 100000)

    def test_error_returns_empty(self):
        _mock_st.session_state = {}
        result = ibkr_api.fetch_net_liq_history()
        self.assertEqual(result, [])


class TestFetchPortfolioGreeks(unittest.TestCase):
    """Test fetch_portfolio_greeks: options only, direction, zero greeks."""

    def test_option_positions_only(self):
        stmt = _make_statement(
            OpenPositions=[
                _make_position("AAPL", _MockAssetClass.STOCK, 100),
                _make_position("AAPL 250620P00200000", _MockAssetClass.OPTION,
                               quantity=-1, put_call=_MockPutCall.PUT,
                               description="AAPL Jun25 200 Put"),
                _make_position("MSFT 250620C00400000", _MockAssetClass.OPTION,
                               quantity=2, put_call=_MockPutCall.CALL,
                               description="MSFT Jun25 400 Call"),
            ],
        )
        _mock_st.session_state = _cache_with_stmt(stmt)
        result = ibkr_api.fetch_portfolio_greeks()

        # Stock position should be excluded
        self.assertEqual(len(result["positions"]), 2)

        short_put = result["positions"][0]
        self.assertEqual(short_put["direction"], "Short")
        self.assertEqual(short_put["delta"], 0)

        long_call = result["positions"][1]
        self.assertEqual(long_call["direction"], "Long")
        self.assertEqual(long_call["gamma"], 0)

        # Totals all zero
        for key in ("delta", "theta", "gamma", "vega"):
            self.assertEqual(result["totals"][key], 0.0)


class TestFetchBetaWeightedDelta(unittest.TestCase):
    """Test BWD formula with mocked SPY price and yfinance betas."""

    @patch("urllib.request.urlopen")
    @patch.dict("sys.modules", {"yfinance": MagicMock()})
    def test_bwd_formula(self, mock_urlopen):
        import json as _json

        # Mock SPY price
        spy_price = 500.0
        spy_response = MagicMock()
        spy_response.read.return_value = _json.dumps({
            "chart": {"result": [{"meta": {"regularMarketPrice": spy_price}}]}
        }).encode()
        spy_response.__enter__ = lambda s: s
        spy_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = spy_response

        # Mock yfinance
        mock_yf = MagicMock()
        def _mock_ticker(t):
            m = MagicMock()
            if t == "AAPL":
                m.info = {"regularMarketPrice": 200, "beta": 1.2}
            else:
                m.info = {"regularMarketPrice": 100, "beta": 0.8}
            return m
        mock_yf.Ticker = _mock_ticker
        sys.modules["yfinance"] = mock_yf

        # 100 shares AAPL stock + 1 short put (delta approx -0.5 * -1 * 100 = +50)
        stmt = _make_statement(
            OpenPositions=[
                _make_position("AAPL", _MockAssetClass.STOCK, 100),
                _make_position("AAPL 250620P200", _MockAssetClass.OPTION,
                               quantity=-1, put_call=_MockPutCall.PUT),
            ],
        )
        _mock_st.session_state = _cache_with_stmt(stmt)

        result = ibkr_api.fetch_beta_weighted_delta()

        self.assertAlmostEqual(result["spy_price"], spy_price)
        self.assertTrue(len(result["positions"]) > 0)

        # AAPL: raw_delta = 100 shares + (-0.5 * -1 * 100) = 150
        aapl_pos = [p for p in result["positions"] if p["ticker"] == "AAPL"][0]
        self.assertAlmostEqual(aapl_pos["raw_delta"], 150.0)
        expected_bwd = 150 * 1.2 * 200 / spy_price
        self.assertAlmostEqual(aapl_pos["bwd"], expected_bwd)

    def test_error_returns_default(self):
        _mock_st.session_state = {}
        result = ibkr_api.fetch_beta_weighted_delta()
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["portfolio_bwd"], 0)


class TestFetchYearlyTransfers(unittest.TestCase):
    """Test fetch_yearly_transfers grouping by year/month, DEPOSITWITHDRAW only."""

    def test_groups_deposits(self):
        stmt = _make_statement(
            CashTransactions=[
                _make_cash_tx(_MockCashAction.DEPOSITWITHDRAW, 10000, date(2025, 1, 15)),
                _make_cash_tx(_MockCashAction.DEPOSITWITHDRAW, 5000, date(2025, 3, 10)),
                _make_cash_tx(_MockCashAction.DEPOSITWITHDRAW, 2000, date(2024, 12, 1)),
                # Non-deposit should be ignored
                _make_cash_tx(_MockCashAction.DIVIDEND, 100, date(2025, 1, 15)),
            ],
        )
        _mock_st.session_state = _cache_with_stmt(stmt)
        result = ibkr_api.fetch_yearly_transfers()

        self.assertIn(2025, result)
        self.assertAlmostEqual(result[2025]["total"], 15000)
        self.assertAlmostEqual(result[2025]["months"][1], 10000)
        self.assertAlmostEqual(result[2025]["months"][3], 5000)
        self.assertIn(2024, result)
        self.assertAlmostEqual(result[2024]["total"], 2000)

    def test_error_returns_empty(self):
        _mock_st.session_state = {}
        result = ibkr_api.fetch_yearly_transfers()
        self.assertEqual(result, {})


class TestFetchMarginInterest(unittest.TestCase):
    """Test margin interest current_month/ytd/total with PAID and RCVD types."""

    def test_interest_split(self):
        now = datetime.now()
        stmt = _make_statement(
            CashTransactions=[
                # Current month, current year
                _make_cash_tx(_MockCashAction.BROKERINTPAID, -50, date(now.year, now.month, 1)),
                # Same year, different month
                _make_cash_tx(_MockCashAction.BROKERINTRCVD, 10, date(now.year, max(1, now.month - 1), 15)),
                # Different year
                _make_cash_tx(_MockCashAction.BROKERINTPAID, -200, date(now.year - 1, 6, 1)),
                # Non-interest — ignored
                _make_cash_tx(_MockCashAction.DEPOSITWITHDRAW, 1000, date(now.year, now.month, 1)),
            ],
        )
        _mock_st.session_state = _cache_with_stmt(stmt)
        result = ibkr_api.fetch_margin_interest()

        self.assertAlmostEqual(result["current_month"], -50)
        # YTD = current_month entry + prior month entry (if same year)
        if now.month > 1:
            self.assertAlmostEqual(result["ytd"], -50 + 10)
        else:
            self.assertAlmostEqual(result["ytd"], -50)
        self.assertAlmostEqual(result["total"], -50 + 10 + (-200))

    def test_error_returns_zeros(self):
        _mock_st.session_state = {}
        result = ibkr_api.fetch_margin_interest()
        self.assertEqual(result["current_month"], 0)
        self.assertEqual(result["ytd"], 0)
        self.assertEqual(result["total"], 0)


class TestFetchOptionChain(unittest.TestCase):
    """Test option chain DTE filtering and strike structure."""

    @patch.dict("sys.modules", {"yfinance": MagicMock()})
    def test_dte_filtering_and_structure(self):
        today = date.today()
        valid_exp = (today + timedelta(days=30)).isoformat()
        too_soon = (today + timedelta(days=3)).isoformat()
        too_far = (today + timedelta(days=90)).isoformat()

        mock_yf = MagicMock()
        mock_stock = MagicMock()
        mock_stock.info = {"regularMarketPrice": 150.0}
        mock_stock.options = [valid_exp, too_soon, too_far]

        puts_df = pd.DataFrame({
            "strike": [145.0, 150.0, 155.0],
            "bid": [2.0, 3.0, 5.0],
            "ask": [2.5, 3.5, 5.5],
            "impliedVolatility": [0.30, 0.28, 0.25],
        })
        chain = SimpleNamespace(puts=puts_df, calls=pd.DataFrame())
        mock_stock.option_chain.return_value = chain
        mock_yf.Ticker.return_value = mock_stock
        sys.modules["yfinance"] = mock_yf

        result = ibkr_api.fetch_option_chain("AAPL", option_type="Put",
                                             min_dte=7, max_dte=60, num_strikes=3)

        self.assertAlmostEqual(result["underlying_price"], 150.0)
        # Only the valid_exp should pass DTE filter
        self.assertEqual(len(result["expirations"]), 1)
        exp = result["expirations"][0]
        self.assertEqual(exp["expiration_date"], valid_exp)
        self.assertEqual(len(exp["strikes"]), 3)

        strike = exp["strikes"][0]
        self.assertIn("strike", strike)
        self.assertIn("bid", strike)
        self.assertIn("ask", strike)
        self.assertIn("mid", strike)
        self.assertIn("iv", strike)
        # IV should be percentage (e.g. 30, not 0.30)
        self.assertGreater(strike["iv"], 1)

    def test_error_returns_fallback(self):
        _mock_st.session_state = {}
        # yfinance not available triggers error path
        with patch.dict("sys.modules", {"yfinance": None}):
            result = ibkr_api.fetch_option_chain("AAPL", fallback_price=123.0)
        self.assertAlmostEqual(result["underlying_price"], 123.0)
        self.assertEqual(result["expirations"], [])


class TestFetchEarningsDates(unittest.TestCase):
    """Test earnings date fetching and missing yfinance handling."""

    def test_returns_date_dict(self):
        mock_yf = MagicMock()
        mock_ticker = MagicMock()
        earnings_dt = datetime(2025, 7, 25)
        mock_ticker.calendar = {"Earnings Date": [earnings_dt]}
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            result = ibkr_api.fetch_earnings_dates(["AAPL"])

        self.assertIn("AAPL", result)
        self.assertEqual(result["AAPL"]["date"], date(2025, 7, 25))
        self.assertTrue(result["AAPL"]["estimated"])

    def test_missing_yfinance(self):
        with patch.dict("sys.modules", {"yfinance": None}):
            # Force reimport path where yfinance is unavailable
            result = ibkr_api.fetch_earnings_dates(["AAPL", "MSFT"])
        # Should return None for each ticker
        for t in ["AAPL", "MSFT"]:
            self.assertIn(t, result)

    def test_no_calendar_data(self):
        mock_yf = MagicMock()
        mock_ticker = MagicMock()
        mock_ticker.calendar = None
        mock_yf.Ticker.return_value = mock_ticker

        with patch.dict("sys.modules", {"yfinance": mock_yf}):
            result = ibkr_api.fetch_earnings_dates(["AAPL"])
        self.assertIsNone(result["AAPL"])


class TestStubs(unittest.TestCase):
    """Test stub functions that always return empty values."""

    def test_fetch_margin_requirements(self):
        self.assertEqual(ibkr_api.fetch_margin_requirements(), {})

    def test_fetch_margin_for_position(self):
        self.assertIsNone(ibkr_api.fetch_margin_for_position("AAPL", 100))


class TestDetectWheels(unittest.TestCase):
    """Test detect_wheels from trade_utils."""

    def _trade(self, label, action, quantity, net_value, inst_type="Equity",
               txn_type="", d=None):
        return {
            "date": d or date(2025, 1, 1),
            "label": label, "type": txn_type, "sub_type": "",
            "description": "", "symbol": "AAPL", "action": action,
            "quantity": quantity, "price": 0, "net_value": net_value,
            "instrument_type": inst_type,
        }

    def test_completed_cycle(self):
        trades = [
            self._trade("CSP", "SELL", 1, 200, "Option", d=date(2025, 1, 1)),
            self._trade("Stock Buy", "Buy", 100, -15000, "Equity", d=date(2025, 2, 1)),
            self._trade("CC", "SELL", 1, 150, "Option", d=date(2025, 2, 15)),
            self._trade("Stock Sell", "Sell", 100, 15500, "Equity", d=date(2025, 3, 1)),
        ]
        cycles = detect_wheels(trades)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["status"], "completed")
        self.assertAlmostEqual(cycles[0]["pl"], 200 + (-15000) + 150 + 15500)
        self.assertEqual(cycles[0]["num_trades"], 4)

    def test_active_cycle(self):
        trades = [
            self._trade("CSP", "SELL", 1, 200, "Option", d=date(2025, 1, 1)),
            self._trade("Stock Buy", "Buy", 100, -15000, "Equity", d=date(2025, 2, 1)),
            self._trade("CC", "SELL", 1, 150, "Option", d=date(2025, 2, 15)),
        ]
        cycles = detect_wheels(trades)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["status"], "active")

    def test_options_only(self):
        trades = [
            self._trade("CSP", "SELL", 1, 200, "Option", d=date(2025, 1, 1)),
            self._trade("BTC", "Buy", 1, -150, "Option", d=date(2025, 1, 15)),
        ]
        cycles = detect_wheels(trades)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["status"], "options_only")

    def test_same_date_cleanup_absorbed(self):
        """Zero-value trade on same date as wheel completion stays in cycle."""
        trades = [
            self._trade("Stock Buy", "Buy", 100, -15000, "Equity", d=date(2025, 1, 1)),
            self._trade("Stock Sell", "Sell", 100, 15500, "Equity", d=date(2025, 2, 1)),
            # Same-date zero-value cleanup (e.g. option removal)
            self._trade("BTC", "Buy", 1, 0.0, "Option", d=date(2025, 2, 1)),
        ]
        cycles = detect_wheels(trades)
        self.assertEqual(len(cycles), 1)
        self.assertEqual(cycles[0]["status"], "completed")
        self.assertEqual(cycles[0]["num_trades"], 3)  # cleanup absorbed


class TestFetchGreeksAndBwd(unittest.TestCase):
    """Test the combined greeks+bwd function."""

    def test_returns_tuple(self):
        # Simple: both sub-functions will hit error path → defaults
        _mock_st.session_state = {}
        greeks, bwd = ibkr_api.fetch_greeks_and_bwd()
        self.assertIn("positions", greeks)
        self.assertIn("totals", greeks)
        self.assertIn("positions", bwd)
        self.assertIn("portfolio_bwd", bwd)


if __name__ == "__main__":
    unittest.main()
