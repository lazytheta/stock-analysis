"""
Unit tests for tastytrade_api.py.

All tastytrade SDK / Streamlit / dotenv dependencies are mocked so tests run
without network access, broker credentials, or a Streamlit runtime.
"""

import json
import ssl
import sys
import unittest
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Mock heavy dependencies before importing tastytrade_api
# ---------------------------------------------------------------------------
_mock_st = MagicMock()
_mock_st.session_state = {}
_mock_st.secrets = {}

sys.modules.setdefault("streamlit", _mock_st)
sys.modules.setdefault("dotenv", MagicMock())
sys.modules.setdefault("error_logger", SimpleNamespace(log_error=lambda *a, **kw: None))

# Mock the tastytrade SDK hierarchy
_mock_tastytrade = MagicMock()
_mock_dxfeed = MagicMock()
_mock_instruments = MagicMock()
_mock_metrics = MagicMock()
_mock_order = MagicMock()

sys.modules.setdefault("tastytrade", _mock_tastytrade)
sys.modules.setdefault("tastytrade.dxfeed", _mock_dxfeed)
sys.modules.setdefault("tastytrade.instruments", _mock_instruments)
sys.modules.setdefault("tastytrade.instruments", _mock_instruments)
sys.modules.setdefault("tastytrade.metrics", _mock_metrics)
sys.modules.setdefault("tastytrade.order", _mock_order)

import tastytrade_api


# ---------------------------------------------------------------------------
# Helpers — SimpleNamespace factories for Tastytrade objects
# ---------------------------------------------------------------------------

def _make_transaction(ticker="AAPL", symbol=None, inst_type="Equity",
                      txn_type="Trade", sub_type="", action="Buy to Open",
                      net_value=0, quantity=0, price=0, description="",
                      txn_date=None):
    """Build a SimpleNamespace that mimics a Tastytrade transaction."""
    return SimpleNamespace(
        underlying_symbol=ticker,
        symbol=symbol or ticker,
        instrument_type=SimpleNamespace(value=inst_type) if inst_type else None,
        transaction_type=txn_type,
        transaction_sub_type=sub_type,
        action=SimpleNamespace(value=action) if action else None,
        net_value=Decimal(str(net_value)),
        quantity=Decimal(str(quantity)),
        price=Decimal(str(price)),
        description=description,
        transaction_date=txn_date or date(2025, 6, 1),
    )


def _make_position(symbol="AAPL", inst_type="Equity", quantity=100,
                   direction="Long", underlying=None, multiplier=100,
                   mark_price=0):
    return SimpleNamespace(
        symbol=symbol,
        instrument_type=SimpleNamespace(value=inst_type),
        quantity=Decimal(str(quantity)),
        quantity_direction=direction,
        underlying_symbol=underlying or symbol,
        multiplier=multiplier,
        mark_price=Decimal(str(mark_price)) if mark_price else None,
    )


def _make_balance(**overrides):
    defaults = {
        "net_liquidating_value": Decimal("100000"),
        "cash_balance": Decimal("25000"),
        "equity_buying_power": Decimal("50000"),
        "derivative_buying_power": Decimal("50000"),
        "maintenance_requirement": Decimal("30000"),
        "maintenance_excess": Decimal("20000"),
        "margin_equity": Decimal("75000"),
        "used_derivative_buying_power": Decimal("10000"),
        "reg_t_margin_requirement": Decimal("35000"),
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_margin_entry(sym="AAPL", desc="AAPL shares", margin_req=5000,
                       maint=4000, initial=6000, bp=45000, calc_type="Reg T",
                       ponr=None, expected_down=None):
    return SimpleNamespace(
        underlying_symbol=sym,
        code=sym,
        description=desc,
        margin_requirement=Decimal(str(margin_req)),
        maintenance_requirement=Decimal(str(maint)) if maint else None,
        initial_requirement=Decimal(str(initial)) if initial else None,
        buying_power=Decimal(str(bp)),
        margin_calculation_type=calc_type,
        point_of_no_return_percent=Decimal(str(ponr)) if ponr else None,
        expected_price_range_down_percent=Decimal(str(expected_down)) if expected_down else None,
    )


def _make_greek(event_symbol, delta=0.5, theta=-0.02, gamma=0.01, vega=0.05,
                volatility=0.30, price=3.0):
    return SimpleNamespace(
        event_symbol=event_symbol,
        delta=delta,
        theta=theta,
        gamma=gamma,
        vega=vega,
        volatility=volatility,
        price=price,
    )


def _make_quote(event_symbol, bid=3.0, ask=3.5):
    return SimpleNamespace(
        event_symbol=event_symbol,
        bid_price=bid,
        ask_price=ask,
    )


def _mock_session_and_account(account_number="ABC123"):
    """Return (mock_session, mock_account) with async context manager support."""
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_account = AsyncMock()
    mock_account.account_number = account_number

    return mock_session, mock_account


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCalculateCostBasis(unittest.TestCase):
    """Test calculate_cost_basis — pure function, no mocking needed."""

    def test_csp_label(self):
        txns = [_make_transaction(
            inst_type="Equity Option", action="Sell to Open",
            description="AAPL Put 200", net_value=300, quantity=1,
        )]
        result = tastytrade_api.calculate_cost_basis(txns)
        self.assertIn("AAPL", result)
        trade = result["AAPL"]["trades"][0]
        self.assertEqual(trade["label"], "CSP")

    def test_cc_label(self):
        txns = [_make_transaction(
            inst_type="Equity Option", action="Sell to Open",
            description="AAPL Call 200", net_value=300, quantity=1,
        )]
        result = tastytrade_api.calculate_cost_basis(txns)
        self.assertEqual(result["AAPL"]["trades"][0]["label"], "CC")

    def test_btc_label(self):
        txns = [_make_transaction(
            inst_type="Equity Option", action="Buy to Close",
            description="AAPL Put 200", net_value=-150, quantity=1,
        )]
        result = tastytrade_api.calculate_cost_basis(txns)
        self.assertEqual(result["AAPL"]["trades"][0]["label"], "BTC CSP")

    def test_dividend_label(self):
        txns = [_make_transaction(
            inst_type="Equity", action=None,
            sub_type="Dividend", net_value=50, quantity=0,
        )]
        result = tastytrade_api.calculate_cost_basis(txns)
        self.assertEqual(result["AAPL"]["trades"][0]["label"], "Dividend")
        self.assertAlmostEqual(result["AAPL"]["dividends"], 50)

    def test_stock_buy_sell_and_shares(self):
        txns = [
            _make_transaction(
                inst_type="Equity", action="Buy to Open",
                net_value=-15000, quantity=100, price=150,
                txn_date=date(2025, 1, 1),
            ),
            _make_transaction(
                inst_type="Equity", action="Sell to Close",
                net_value=8000, quantity=50, price=160,
                txn_date=date(2025, 2, 1),
            ),
        ]
        result = tastytrade_api.calculate_cost_basis(txns)
        cb = result["AAPL"]
        self.assertEqual(cb["shares_held"], 50)
        self.assertAlmostEqual(cb["equity_cost"], -15000 + 8000)
        self.assertEqual(cb["trades"][0]["label"], "Stock Buy")
        self.assertEqual(cb["trades"][1]["label"], "Stock Sell")

    def test_assignment_label(self):
        txns = [_make_transaction(
            inst_type="Equity Option", action=None,
            sub_type="Assignment", net_value=0, quantity=1,
        )]
        result = tastytrade_api.calculate_cost_basis(txns)
        self.assertEqual(result["AAPL"]["trades"][0]["label"], "Assignment")

    def test_sort_order_newest_first(self):
        txns = [
            _make_transaction(ticker="MSFT", net_value=-5000, quantity=50,
                              txn_date=date(2025, 1, 1)),
            _make_transaction(ticker="AAPL", net_value=-10000, quantity=100,
                              txn_date=date(2025, 6, 1)),
        ]
        result = tastytrade_api.calculate_cost_basis(txns)
        keys = list(result.keys())
        self.assertEqual(keys[0], "AAPL")  # more recent → first

    def test_empty_ticker_skipped(self):
        txn = _make_transaction(ticker="", net_value=100)
        txn.underlying_symbol = ""
        txn.symbol = ""
        result = tastytrade_api.calculate_cost_basis([txn])
        self.assertEqual(result, {})

    def test_cost_per_share_calculation(self):
        txns = [
            _make_transaction(inst_type="Equity", action="Buy to Open",
                              net_value=-10000, quantity=100, txn_date=date(2025, 1, 1)),
            _make_transaction(inst_type="Equity Option", action="Sell to Open",
                              description="AAPL Put 100", net_value=500, quantity=1,
                              txn_date=date(2025, 2, 1)),
        ]
        result = tastytrade_api.calculate_cost_basis(txns)
        cb = result["AAPL"]
        self.assertEqual(cb["shares_held"], 100)
        self.assertAlmostEqual(cb["option_pl"], 500)
        self.assertAlmostEqual(cb["equity_cost"], -10000)
        self.assertAlmostEqual(cb["adjusted_cost"], -10000 + 500)
        self.assertAlmostEqual(cb["cost_per_share"], (-10000 + 500) / 100)


class TestGetSecret(unittest.TestCase):
    """Test _get_secret env var and st.secrets fallback."""

    @patch.dict("os.environ", {"MY_KEY": "  secret_val  "})
    def test_env_var(self):
        result = tastytrade_api._get_secret("MY_KEY")
        self.assertEqual(result, "secret_val")

    @patch.dict("os.environ", {}, clear=True)
    def test_streamlit_fallback(self):
        mock_st = MagicMock()
        mock_st.secrets = {"MY_KEY": "  cloud_val  "}
        with patch.dict("sys.modules", {"streamlit": mock_st}):
            result = tastytrade_api._get_secret("MY_KEY")
        self.assertEqual(result, "cloud_val")


class TestFetchAccountBalances(unittest.TestCase):
    """Test fetch_account_balances with mocked Account.get_balances."""

    @patch("tastytrade_api._get_session")
    def test_all_balance_fields(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        balance = _make_balance()
        acct.get_balances = AsyncMock(return_value=balance)

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_account_balances()

        self.assertAlmostEqual(result["net_liquidating_value"], 100000)
        self.assertAlmostEqual(result["cash_balance"], 25000)
        self.assertAlmostEqual(result["equity_buying_power"], 50000)
        self.assertAlmostEqual(result["derivative_buying_power"], 50000)
        self.assertAlmostEqual(result["maintenance_requirement"], 30000)
        self.assertAlmostEqual(result["maintenance_excess"], 20000)
        self.assertAlmostEqual(result["margin_equity"], 75000)
        self.assertAlmostEqual(result["used_derivative_buying_power"], 10000)
        self.assertAlmostEqual(result["reg_t_margin_requirement"], 35000)

    @patch("tastytrade_api._get_session")
    def test_returns_all_nine_fields(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session
        acct.get_balances = AsyncMock(return_value=_make_balance())

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_account_balances()

        self.assertEqual(len(result), 9)


class TestFetchPortfolioData(unittest.TestCase):
    """Test fetch_portfolio_data calls calculate_cost_basis and returns account_number."""

    @patch("tastytrade_api._get_session")
    def test_returns_cost_basis_and_account(self, mock_get_session):
        session, acct = _mock_session_and_account("U99999")
        mock_get_session.return_value = session

        txns = [
            _make_transaction(inst_type="Equity", action="Buy to Open",
                              net_value=-10000, quantity=100,
                              txn_date=date(2025, 1, 1)),
        ]
        acct.get_history = AsyncMock(return_value=txns)

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            cost_basis, acct_num = tastytrade_api.fetch_portfolio_data()

        self.assertEqual(acct_num, "U99999")
        self.assertIn("AAPL", cost_basis)
        self.assertEqual(cost_basis["AAPL"]["shares_held"], 100)

    @patch("tastytrade_api._get_session")
    def test_empty_transactions(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session
        acct.get_history = AsyncMock(return_value=[])

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            cost_basis, acct_num = tastytrade_api.fetch_portfolio_data()

        self.assertEqual(cost_basis, {})


class TestFetchYearlyTransfers(unittest.TestCase):
    """Test fetch_yearly_transfers: year/month grouping, only Money Movement."""

    @patch("tastytrade_api._get_session")
    def test_groups_deposits_and_withdrawals(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        txns = [
            _make_transaction(txn_type="Money Movement", sub_type="Deposit",
                              net_value=10000, txn_date=date(2025, 1, 15)),
            _make_transaction(txn_type="Money Movement", sub_type="Withdrawal",
                              net_value=-3000, txn_date=date(2025, 3, 10)),
            _make_transaction(txn_type="Money Movement", sub_type="Deposit",
                              net_value=2000, txn_date=date(2024, 12, 1)),
            # Non-transfer should be ignored
            _make_transaction(txn_type="Trade", sub_type="",
                              net_value=500, txn_date=date(2025, 1, 15)),
        ]
        acct.get_history = AsyncMock(return_value=txns)

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_yearly_transfers()

        self.assertIn(2025, result)
        self.assertAlmostEqual(result[2025]["total"], 7000)
        self.assertAlmostEqual(result[2025]["months"][1], 10000)
        self.assertAlmostEqual(result[2025]["months"][3], -3000)
        self.assertIn(2024, result)
        self.assertAlmostEqual(result[2024]["total"], 2000)

    @patch("tastytrade_api._get_session")
    def test_ignores_non_money_movement(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        txns = [
            _make_transaction(txn_type="Trade", sub_type="Buy",
                              net_value=-5000, txn_date=date(2025, 1, 1)),
        ]
        acct.get_history = AsyncMock(return_value=txns)

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_yearly_transfers()

        self.assertEqual(result, {})


class TestFetchMarginInterest(unittest.TestCase):
    """Test fetch_margin_interest: current_month/ytd/total, only Debit Interest."""

    @patch("tastytrade_api._get_session")
    def test_interest_split(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        today = date.today()
        txns = [
            # Current month
            _make_transaction(txn_type="Other", sub_type="Debit Interest",
                              net_value=-50, txn_date=date(today.year, today.month, 1)),
            # Same year, prior month (if possible)
            _make_transaction(txn_type="Other", sub_type="Debit Interest",
                              net_value=-30,
                              txn_date=date(today.year, max(1, today.month - 1), 15)),
            # Prior year
            _make_transaction(txn_type="Other", sub_type="Debit Interest",
                              net_value=-200, txn_date=date(today.year - 1, 6, 1)),
            # Non-interest — ignored
            _make_transaction(txn_type="Money Movement", sub_type="Deposit",
                              net_value=1000, txn_date=date(today.year, today.month, 1)),
        ]
        acct.get_history = AsyncMock(return_value=txns)

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_margin_interest()

        self.assertAlmostEqual(result["current_month"], -50)
        if today.month > 1:
            self.assertAlmostEqual(result["ytd"], -80)
        else:
            self.assertAlmostEqual(result["ytd"], -50)
        self.assertAlmostEqual(result["total"], -280)

    @patch("tastytrade_api._get_session")
    def test_no_interest(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session
        acct.get_history = AsyncMock(return_value=[])

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_margin_interest()

        self.assertAlmostEqual(result["current_month"], 0)
        self.assertAlmostEqual(result["ytd"], 0)
        self.assertAlmostEqual(result["total"], 0)


class TestFetchMarginRequirements(unittest.TestCase):
    """Test fetch_margin_requirements per-position fields and error path."""

    @patch("tastytrade_api._get_session")
    def test_per_position_fields(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        report = SimpleNamespace(groups=[
            _make_margin_entry("AAPL", margin_req=5000, maint=4000,
                               initial=6000, bp=45000),
            _make_margin_entry("MSFT", margin_req=3000, maint=2500,
                               initial=3500, bp=47000),
        ])
        acct.get_margin_requirements = AsyncMock(return_value=report)

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_margin_requirements()

        self.assertIn("AAPL", result)
        self.assertAlmostEqual(result["AAPL"]["margin_requirement"], 5000)
        self.assertAlmostEqual(result["AAPL"]["maintenance_requirement"], 4000)
        self.assertIn("MSFT", result)

    @patch("tastytrade_api._get_session")
    def test_error_returns_empty(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session
        acct.get_margin_requirements = AsyncMock(side_effect=Exception("fail"))

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_margin_requirements()
        self.assertEqual(result, {})


class TestFetchMarginForPosition(unittest.TestCase):
    """Test fetch_margin_for_position dry-run and error path."""

    @patch("tastytrade_api._get_session")
    def test_dry_run_returns_margin_fields(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        bp_effect = SimpleNamespace(
            change_in_margin_requirement=Decimal("5000"),
            change_in_buying_power=Decimal("-5000"),
            current_buying_power=Decimal("50000"),
            new_buying_power=Decimal("45000"),
            isolated_order_margin_requirement=Decimal("5000"),
        )
        fee_calc = SimpleNamespace(total_fees=Decimal("1.50"))
        resp = SimpleNamespace(buying_power_effect=bp_effect, fee_calculation=fee_calc)
        acct.place_order = AsyncMock(return_value=resp)

        mock_equity = MagicMock()
        mock_equity.build_leg = MagicMock(return_value="leg")

        with patch("tastytrade_api.Account") as MockAccount, \
             patch("tastytrade_api.Equity") as MockEquity, \
             patch("tastytrade_api.NewOrder") as MockNewOrder:
            MockAccount.get = AsyncMock(return_value=[acct])
            MockEquity.get = AsyncMock(return_value=mock_equity)
            MockNewOrder.return_value = "order"
            result = tastytrade_api.fetch_margin_for_position("AAPL", 100)

        self.assertAlmostEqual(result["change_in_margin"], 5000)
        self.assertAlmostEqual(result["change_in_buying_power"], -5000)
        self.assertAlmostEqual(result["total_fees"], 1.50)

    @patch("tastytrade_api._get_session")
    def test_error_returns_none(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session
        acct.place_order = AsyncMock(side_effect=Exception("timeout"))

        with patch("tastytrade_api.Account") as MockAccount, \
             patch("tastytrade_api.Equity") as MockEquity, \
             patch("tastytrade_api.NewOrder"):
            MockAccount.get = AsyncMock(return_value=[acct])
            mock_eq = MagicMock()
            mock_eq.build_leg = MagicMock(return_value="leg")
            MockEquity.get = AsyncMock(return_value=mock_eq)
            result = tastytrade_api.fetch_margin_for_position("AAPL", 100)
        self.assertIsNone(result)


class TestFetchNetLiqHistory(unittest.TestCase):
    """Test fetch_net_liq_history returns list of {time, close}."""

    @patch("tastytrade_api._get_session")
    def test_returns_history(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        snapshots = [
            SimpleNamespace(time="2025-01-01", close=Decimal("90000")),
            SimpleNamespace(time="2025-06-01", close=Decimal("100000")),
        ]
        acct.get_net_liquidating_value_history = AsyncMock(return_value=snapshots)

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_net_liq_history("1y")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["time"], "2025-01-01")
        self.assertAlmostEqual(result[0]["close"], 90000)
        self.assertAlmostEqual(result[1]["close"], 100000)


class TestFetchYearlyReturns(unittest.TestCase):
    """Test _fetch_yearly_returns Yahoo chart parsing and error path."""

    @patch("urllib.request.urlopen")
    def test_returns_yearly_pct(self, mock_urlopen):
        # Two years of monthly data: Jan 2024 close=100, Dec 2024 close=110,
        # Jan 2025 close=115
        timestamps = [
            int(datetime(2024, 1, 15).timestamp()),
            int(datetime(2024, 12, 15).timestamp()),
            int(datetime(2025, 1, 15).timestamp()),
        ]
        closes = [100.0, 110.0, 115.0]
        payload = json.dumps({
            "chart": {"result": [{
                "timestamp": timestamps,
                "indicators": {"quote": [{"close": closes}]},
            }]}
        }).encode()

        resp = MagicMock()
        resp.read.return_value = payload
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = tastytrade_api._fetch_yearly_returns("SPY")
        # 2024 last close = 110, 2025 last close = 115
        # year_close: {2024: 110, 2025: 115}  (last close per year wins)
        # returns[2025] = (115 - 110) / 110 * 100
        self.assertIn(2025, result)
        self.assertAlmostEqual(result[2025], (115 - 110) / 110 * 100, places=2)

    @patch("urllib.request.urlopen")
    def test_error_returns_empty(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("network error")
        result = tastytrade_api._fetch_yearly_returns("SPY")
        self.assertEqual(result, {})


class TestFetchBenchmarkReturns(unittest.TestCase):
    """Test fetch_benchmark_returns returns 3 benchmark keys."""

    @patch("tastytrade_api._fetch_yearly_returns")
    def test_three_benchmarks(self, mock_fetch):
        mock_fetch.return_value = {2024: 10.0}
        result = tastytrade_api.fetch_benchmark_returns()
        self.assertIn("S&P 500", result)
        self.assertIn("NASDAQ 100", result)
        self.assertIn("MSCI World", result)
        self.assertEqual(len(result), 3)


class TestFetchTickerProfiles(unittest.TestCase):
    """Test fetch_ticker_profiles exchange→country mapping and error path."""

    @patch("urllib.request.urlopen")
    def test_exchange_mapping(self, mock_urlopen):
        payload = json.dumps({
            "quotes": [{"quoteType": "EQUITY", "sector": "Technology", "exchange": "NMS"}]
        }).encode()
        resp = MagicMock()
        resp.read.return_value = payload
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = tastytrade_api.fetch_ticker_profiles(["AAPL"])
        self.assertEqual(result["AAPL"]["country"], "United States")
        self.assertEqual(result["AAPL"]["sector"], "Technology")

    @patch("urllib.request.urlopen")
    def test_error_returns_unknown(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("timeout")
        result = tastytrade_api.fetch_ticker_profiles(["AAPL"])
        self.assertEqual(result["AAPL"]["country"], "Unknown")
        self.assertEqual(result["AAPL"]["sector"], "Unknown")


class TestFetchCurrentPrices(unittest.TestCase):
    """Test fetch_current_prices returns {ticker: {price, previousClose}}."""

    @patch("urllib.request.urlopen")
    def test_returns_prices(self, mock_urlopen):
        payload = json.dumps({
            "chart": {"result": [{"meta": {
                "regularMarketPrice": 150.0,
                "chartPreviousClose": 148.0,
            }}]}
        }).encode()
        resp = MagicMock()
        resp.read.return_value = payload
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = resp

        result = tastytrade_api.fetch_current_prices(["AAPL"])
        self.assertAlmostEqual(result["AAPL"]["price"], 150.0)
        self.assertAlmostEqual(result["AAPL"]["previousClose"], 148.0)

    @patch("urllib.request.urlopen")
    def test_error_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("timeout")
        result = tastytrade_api.fetch_current_prices(["AAPL"])
        self.assertIsNone(result["AAPL"])


class TestFetchEarningsDates(unittest.TestCase):
    """Test fetch_earnings_dates via Tastytrade market metrics."""

    @patch("tastytrade_api._get_session")
    def test_returns_date_dict(self, mock_get_session):
        session, _ = _mock_session_and_account()
        mock_get_session.return_value = session

        earnings = SimpleNamespace(
            expected_report_date=date(2025, 7, 25),
            time_of_day="AMC",
            estimated=True,
        )
        metric = SimpleNamespace(symbol="AAPL", earnings=earnings)

        with patch("tastytrade_api.get_market_metrics", new_callable=AsyncMock) as mock_mm:
            mock_mm.return_value = [metric]
            result = tastytrade_api.fetch_earnings_dates(["AAPL"])

        self.assertIn("AAPL", result)
        self.assertEqual(result["AAPL"]["date"], date(2025, 7, 25))
        self.assertTrue(result["AAPL"]["estimated"])

    @patch("tastytrade_api._get_session")
    def test_error_returns_none_per_ticker(self, mock_get_session):
        mock_get_session.side_effect = Exception("connection failed")
        result = tastytrade_api.fetch_earnings_dates(["AAPL", "MSFT"])
        self.assertIsNone(result["AAPL"])
        self.assertIsNone(result["MSFT"])


class TestFetchPortfolioGreeks(unittest.TestCase):
    """Test fetch_portfolio_greeks: dollar-weighted greeks, no options → empty."""

    @patch("tastytrade_api._get_session")
    def test_dollar_weighted_greeks(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        pos = _make_position(
            symbol="AAPL  250620P00200000", inst_type="Equity Option",
            quantity=1, direction="Short", underlying="AAPL", multiplier=100,
        )
        acct.get_positions = AsyncMock(return_value=[pos])

        greek = _make_greek(".AAPL250620P200", delta=-0.4, theta=0.05,
                            gamma=0.02, vega=0.10)

        mock_streamer = AsyncMock()
        mock_streamer.__aenter__ = AsyncMock(return_value=mock_streamer)
        mock_streamer.__aexit__ = AsyncMock(return_value=False)
        mock_streamer.subscribe = AsyncMock()

        async def _listen_greeks(event_type):
            yield greek

        mock_streamer.listen = MagicMock(side_effect=lambda et: _listen_greeks(et))

        with patch("tastytrade_api.Account") as MockAccount, \
             patch("tastytrade_api.Option") as MockOption, \
             patch("tastytrade_api.DXLinkStreamer", return_value=mock_streamer):
            MockAccount.get = AsyncMock(return_value=[acct])
            MockOption.occ_to_streamer_symbol = MagicMock(return_value=".AAPL250620P200")
            result = tastytrade_api.fetch_portfolio_greeks()

        self.assertEqual(len(result["positions"]), 1)
        p = result["positions"][0]
        # Short position: qty = -1, mult = 100
        # delta = -0.4 * -1 * 100 = 40
        self.assertAlmostEqual(p["delta"], -0.4 * -1 * 100)
        self.assertEqual(p["direction"], "Short")

    @patch("tastytrade_api._get_session")
    def test_no_options_returns_empty(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        stock_pos = _make_position("AAPL", inst_type="Equity", quantity=100)
        acct.get_positions = AsyncMock(return_value=[stock_pos])

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_portfolio_greeks()

        self.assertEqual(result["positions"], [])
        for key in ("delta", "theta", "gamma", "vega"):
            self.assertAlmostEqual(result["totals"][key], 0.0)


class TestFetchGreeksAndBwd(unittest.TestCase):
    """Test fetch_greeks_and_bwd combined result and error path."""

    @patch("tastytrade_api._get_session")
    def test_combined_result(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        stock = _make_position("AAPL", inst_type="Equity", quantity=100,
                               mark_price=200)
        acct.get_positions = AsyncMock(return_value=[stock])

        # Metrics with beta
        metric_aapl = SimpleNamespace(symbol="AAPL", beta=1.2)
        metric_spy = SimpleNamespace(symbol="SPY", beta=1.0)

        # DXLink streamer returning SPY quote
        spy_quote = _make_quote("SPY", bid=500, ask=500)
        aapl_quote = _make_quote("AAPL", bid=200, ask=200)

        mock_streamer = AsyncMock()
        mock_streamer.__aenter__ = AsyncMock(return_value=mock_streamer)
        mock_streamer.__aexit__ = AsyncMock(return_value=False)
        mock_streamer.subscribe = AsyncMock()

        # listen returns quotes in sequence
        call_count = [0]
        async def _listen(event_type):
            call_count[0] += 1
            if call_count[0] == 1:
                # Quote events
                yield aapl_quote
                yield spy_quote
            # No greeks needed (no option positions)

        mock_streamer.listen = MagicMock(side_effect=_listen)

        with patch("tastytrade_api.Account") as MockAccount, \
             patch("tastytrade_api.get_market_metrics", new_callable=AsyncMock) as mock_mm, \
             patch("tastytrade_api.DXLinkStreamer", return_value=mock_streamer), \
             patch("tastytrade_api.Option") as MockOption:
            MockAccount.get = AsyncMock(return_value=[acct])
            mock_mm.return_value = [metric_aapl, metric_spy]
            MockOption.occ_to_streamer_symbol = MagicMock(return_value=None)
            greeks, bwd = tastytrade_api.fetch_greeks_and_bwd()

        self.assertIn("positions", greeks)
        self.assertIn("totals", greeks)
        self.assertIn("positions", bwd)
        self.assertAlmostEqual(bwd["spy_price"], 500)

    @patch("tastytrade_api._get_session")
    def test_error_returns_empty(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session
        acct.get_positions = AsyncMock(side_effect=Exception("fail"))

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            greeks, bwd = tastytrade_api.fetch_greeks_and_bwd()
        self.assertEqual(greeks["positions"], [])
        self.assertEqual(bwd["positions"], [])
        self.assertEqual(bwd["portfolio_bwd"], 0)


class TestFetchOptionChain(unittest.TestCase):
    """Test fetch_option_chain DTE filter + strike structure and error path."""

    @patch("tastytrade_api._get_session")
    def test_dte_filter_and_structure(self, mock_get_session):
        session, _ = _mock_session_and_account()
        mock_get_session.return_value = session

        # Build nested chain with two expirations: one valid (30 DTE), one too far (90 DTE)
        strike = SimpleNamespace(
            strike_price=Decimal("145"),
            put_streamer_symbol=".AAPL250715P145",
            call_streamer_symbol=".AAPL250715C145",
        )
        valid_exp = SimpleNamespace(
            days_to_expiration=30, expiration_date="2025-07-15",
            expiration_type="Regular", strikes=[strike],
        )
        far_exp = SimpleNamespace(
            days_to_expiration=90, expiration_date="2025-09-15",
            expiration_type="Regular", strikes=[strike],
        )
        chain = SimpleNamespace(expirations=[valid_exp, far_exp])

        # Single streamer for option quotes + greeks (parallel)
        mock_streamer = AsyncMock()
        mock_streamer.__aenter__ = AsyncMock(return_value=mock_streamer)
        mock_streamer.__aexit__ = AsyncMock(return_value=False)
        mock_streamer.subscribe = AsyncMock()

        # Route listen() based on event_type parameter
        from tastytrade_api import QuoteEvent, GreeksEvent

        async def _listen(event_type):
            if event_type is QuoteEvent:
                yield _make_quote(".AAPL250715P145", bid=2.0, ask=2.5)
            elif event_type is GreeksEvent:
                yield _make_greek(".AAPL250715P145", delta=-0.3, volatility=0.25)

        mock_streamer.listen = MagicMock(side_effect=_listen)

        # Mock Yahoo Finance for underlying price
        _yf_response = json.dumps({"chart": {"result": [{"meta": {"regularMarketPrice": 150.0}}]}}).encode()
        mock_urlopen = MagicMock()
        mock_urlopen.__enter__ = MagicMock(return_value=SimpleNamespace(read=lambda: _yf_response))
        mock_urlopen.__exit__ = MagicMock(return_value=False)

        with patch("tastytrade_api.NestedOptionChain") as MockChain, \
             patch("tastytrade_api.DXLinkStreamer", return_value=mock_streamer), \
             patch("tastytrade_api.urllib.request.urlopen", return_value=mock_urlopen):
            MockChain.get = AsyncMock(return_value=[chain])
            result = tastytrade_api.fetch_option_chain(
                "AAPL", option_type="Put", min_dte=7, max_dte=60, num_strikes=3)

        self.assertAlmostEqual(result["underlying_price"], 150.0)
        # Only valid_exp (30 DTE) should pass; far_exp (90 DTE) filtered out
        self.assertEqual(len(result["expirations"]), 1)
        exp = result["expirations"][0]
        self.assertEqual(exp["expiration_date"], "2025-07-15")
        self.assertEqual(len(exp["strikes"]), 1)
        s = exp["strikes"][0]
        self.assertIn("strike", s)
        self.assertIn("bid", s)
        self.assertIn("mid", s)
        self.assertIn("delta", s)

    @patch("tastytrade_api._get_session")
    def test_error_returns_empty(self, mock_get_session):
        session, _ = _mock_session_and_account()
        mock_get_session.return_value = session

        with patch("tastytrade_api.NestedOptionChain") as MockChain, \
             patch("tastytrade_api.DXLinkStreamer"):
            MockChain.get = AsyncMock(side_effect=Exception("fail"))
            result = tastytrade_api.fetch_option_chain("AAPL", fallback_price=123.0)
        self.assertEqual(result["expirations"], [])


class TestFetchBetaWeightedDelta(unittest.TestCase):
    """Test fetch_beta_weighted_delta BWD formula and error path."""

    @patch("tastytrade_api._get_session")
    def test_bwd_formula(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session

        stock = _make_position("AAPL", inst_type="Equity", quantity=100,
                               mark_price=200)
        acct.get_positions = AsyncMock(return_value=[stock])

        metric_aapl = SimpleNamespace(symbol="AAPL", beta=1.2)
        metric_spy = SimpleNamespace(symbol="SPY", beta=1.0)

        spy_quote = _make_quote("SPY", bid=500, ask=500)
        aapl_quote = _make_quote("AAPL", bid=200, ask=200)

        mock_streamer = AsyncMock()
        mock_streamer.__aenter__ = AsyncMock(return_value=mock_streamer)
        mock_streamer.__aexit__ = AsyncMock(return_value=False)
        mock_streamer.subscribe = AsyncMock()

        async def _listen(event_type):
            yield aapl_quote
            yield spy_quote

        mock_streamer.listen = MagicMock(side_effect=lambda et: _listen(et))

        with patch("tastytrade_api.Account") as MockAccount, \
             patch("tastytrade_api.get_market_metrics", new_callable=AsyncMock) as mock_mm, \
             patch("tastytrade_api.DXLinkStreamer", return_value=mock_streamer), \
             patch("tastytrade_api.Option") as MockOption:
            MockAccount.get = AsyncMock(return_value=[acct])
            mock_mm.return_value = [metric_aapl, metric_spy]
            MockOption.occ_to_streamer_symbol = MagicMock(return_value=None)
            result = tastytrade_api.fetch_beta_weighted_delta()

        self.assertAlmostEqual(result["spy_price"], 500)
        self.assertTrue(len(result["positions"]) > 0)

        aapl_pos = [p for p in result["positions"] if p["ticker"] == "AAPL"][0]
        self.assertAlmostEqual(aapl_pos["raw_delta"], 100)
        # BWD = 100 * 1.2 * (200 / 500) = 48.0
        expected_bwd = 100 * 1.2 * (200 / 500)
        self.assertAlmostEqual(aapl_pos["bwd"], expected_bwd)

    @patch("tastytrade_api._get_session")
    def test_error_returns_empty(self, mock_get_session):
        session, acct = _mock_session_and_account()
        mock_get_session.return_value = session
        acct.get_positions = AsyncMock(side_effect=Exception("fail"))

        with patch("tastytrade_api.Account") as MockAccount:
            MockAccount.get = AsyncMock(return_value=[acct])
            result = tastytrade_api.fetch_beta_weighted_delta()
        self.assertEqual(result["positions"], [])
        self.assertEqual(result["portfolio_bwd"], 0)


if __name__ == "__main__":
    unittest.main()
