"""
Unit tests for t212_api.py — the read-only Trading 212 broker client.

All HTTP is mocked; tests run without network access or real credentials.
"""

import base64
import unittest
from unittest.mock import MagicMock, patch

import requests

import broker_adapter
import gather_data
import t212_api


_CREDS = {"t212_api_key": "KEY123", "t212_api_secret": "SECRET456"}

_META = [
    {"ticker": "AAPL_US_EQ", "shortName": "AAPL", "currencyCode": "USD",
     "isin": "US0378331005"},
    {"ticker": "ASML_NL_EQ", "shortName": "ASML", "currencyCode": "EUR",
     "isin": "NL0010273215"},
    # A real T212 code that suffix-stripping cannot decode: the Amundi ETF is
    # "WEBN1d_EQ", which strips to "WEBN1d". Only the metadata carries "WEBN".
    {"ticker": "WEBN1d_EQ", "shortName": "WEBN", "currencyCode": "EUR",
     "isin": "IE0003XJA0J9"},
]


class TestAuthHeader(unittest.TestCase):
    def test_auth_header_is_basic_base64_key_colon_secret(self):
        header = t212_api._auth_header(_CREDS)
        expected = base64.b64encode(b"KEY123:SECRET456").decode()
        self.assertEqual(header["Authorization"], f"Basic {expected}")


class TestGet(unittest.TestCase):
    def _resp(self, status=200, json_data=None, headers=None):
        r = MagicMock()
        r.status_code = status
        r.json.return_value = json_data if json_data is not None else {}
        r.headers = headers or {}
        r.raise_for_status.side_effect = (
            None if status < 400 else requests.HTTPError(f"{status}")
        )
        return r

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_sends_auth_and_returns_json(self, mock_get, _sleep):
        mock_get.return_value = self._resp(200, {"ok": True})
        out = t212_api._get("/equity/positions", _CREDS)
        self.assertEqual(out, {"ok": True})
        _, kwargs = mock_get.call_args
        self.assertIn("Authorization", kwargs["headers"])

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_retries_on_429_then_succeeds(self, mock_get, _sleep):
        mock_get.side_effect = [
            self._resp(429, headers={"Retry-After": "1"}),
            self._resp(200, {"ok": 1}),
        ]
        out = t212_api._get("/equity/account/cash", _CREDS, max_retries=3)
        self.assertEqual(out, {"ok": 1})
        self.assertEqual(mock_get.call_count, 2)

    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_get_raises_after_exhausting_retries(self, mock_get, _sleep):
        mock_get.return_value = self._resp(429, headers={"Retry-After": "1"})
        with self.assertRaises(requests.HTTPError):
            t212_api._get("/equity/positions", _CREDS, max_retries=2)


    @patch("t212_api.time.sleep", return_value=None)
    @patch("t212_api.requests.get")
    def test_a_next_page_path_carrying_the_api_prefix_is_not_doubled(self, mock_get, _sleep):
        """T212 geeft nextPagePath sinds 2026-09 mét /api/v0 terug. Daar de
        basis-URL voor plakken gaf .../api/v0/api/v0/... en een 404 op elke
        tweede pagina -- en daarmee, via de except, een lege cash-historie:
        geen Trading 212-stortingen en geen curve op de Results-pagina."""
        mock_get.return_value = self._resp(200, {"items": []})
        t212_api._get("/api/v0/equity/history/transactions?limit=50&cursor=abc", _CREDS)
        (url,), _ = mock_get.call_args
        self.assertEqual(
            url, "https://live.trading212.com/api/v0/equity/history/transactions?limit=50&cursor=abc")


class TestResolve(unittest.TestCase):
    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None  # reset module cache between tests

    @patch("t212_api._get")
    def test_resolves_us_and_non_us_codes(self, mock_get):
        mock_get.return_value = _META
        m = t212_api._resolve_instruments(_CREDS)
        self.assertEqual(m["AAPL_US_EQ"]["symbol"], "AAPL")
        self.assertEqual(m["AAPL_US_EQ"]["currency"], "USD")
        self.assertEqual(m["ASML_NL_EQ"]["symbol"], "ASML")
        self.assertEqual(m["ASML_NL_EQ"]["currency"], "EUR")
        self.assertEqual(m["ASML_NL_EQ"]["exchange"], "NL")

    @patch("t212_api._get")
    def test_metadata_fetched_once_and_cached(self, mock_get):
        mock_get.return_value = _META
        t212_api._resolve_instruments(_CREDS)
        t212_api._resolve_instruments(_CREDS)
        self.assertEqual(mock_get.call_count, 1)

    @patch("t212_api._get")
    def test_clean_falls_back_to_suffix_strip(self, mock_get):
        mock_get.return_value = _META
        info = t212_api._clean("TSLA_US_EQ", _CREDS)  # not in _META
        self.assertEqual(info["symbol"], "TSLA")
        self.assertEqual(info["exchange"], "US")


class TestPortfolio(unittest.TestCase):
    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()

    @patch("gather_data.fetch_fx_rate", return_value=1.0)
    @patch("t212_api._get")
    def test_positions_normalise_to_cost_basis(self, mock_get, _fx):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                # Shape copied verbatim from a live /equity/positions response
                # (2026-08-10). The instrument is NESTED and the money fields
                # are named differently from what the old fixture assumed —
                # that mismatch is why every T212 row rendered as $0.00 with a
                # blank ticker while the share count came through fine.
                return [
                    {"instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                    "isin": "US0378331005", "currency": "USD"},
                     "quantity": 10, "averagePricePaid": 150.0,
                     "currentPrice": 170.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 1500.0,
                                      "currentValue": 1700.0,
                                      "unrealizedProfitLoss": 200.0}},
                    {"instrument": {"ticker": "ASML_NL_EQ", "name": "ASML",
                                    "isin": "NL0010273215", "currency": "EUR"},
                     "quantity": 5, "averagePricePaid": 600.0,
                     "currentPrice": 590.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 3000.0,
                                      "currentValue": 2950.0,
                                      "unrealizedProfitLoss": -50.0}},
                ]
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            raise AssertionError(path)
        mock_get.side_effect = _router

        cb, acct = t212_api.fetch_portfolio_data(_CREDS)
        self.assertEqual(acct, "42")
        self.assertEqual(cb["AAPL"]["shares_held"], 10)
        # Negative by convention — see TestSignConvention.
        self.assertEqual(cb["AAPL"]["cost_per_share"], -150.0)
        self.assertEqual(cb["AAPL"]["adjusted_cost"], -1500.0)
        self.assertEqual(cb["AAPL"]["purchase_price"], 150.0)
        # total_pl is the net cash the name has moved, not its profit — the
        # same convention Tastytrade fills it with, because the portfolio page
        # finishes the sum with `total_pl + market_value`. This asserted the
        # finished profit until 2026-08-21, so the page added the market value
        # to a figure that already contained it and every Trading 212 holding
        # arrived among the top performers: META's -$20 loss was drawn as a
        # +$1,643 gain, which is exactly what its position was worth.
        self.assertEqual(cb["AAPL"]["total_pl"], -1500.0)
        # And the sum the page actually performs gives the profit back.
        self.assertEqual(cb["AAPL"]["total_pl"] + 10 * 170.0, 200.0)
        self.assertEqual(cb["AAPL"]["option_pl"], 0)
        self.assertEqual(cb["AAPL"]["trades"], [])
        # Per-share figures are in USD — converted where the instrument is
        # quoted in something else (see TestUsdNormalisation); the
        # account-currency copy rides along for reconciling against T212.
        self.assertEqual(cb["AAPL"]["currency"], "USD")
        self.assertEqual(cb["AAPL"]["purchase_price"], 150.0)      # USD, not EUR
        self.assertEqual(cb["AAPL"]["account_currency"], "EUR")
        self.assertEqual(cb["AAPL"]["account_cost"], 1500.0)
        self.assertEqual(cb["AAPL"]["account_value"], 1700.0)
        self.assertEqual(cb["ASML"]["native_currency"], "EUR")
        self.assertEqual(cb["ASML"]["exchange"], "NL")


class TestBalances(unittest.TestCase):
    def setUp(self):
        gather_data._FX_CACHE.clear()

    @patch("gather_data.fetch_fx_rate", return_value=1.0)
    @patch("t212_api._get")
    def test_cash_maps_to_balances_shape(self, mock_get, _fx):
        def _router(path, creds, **kw):
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "USD"}
            return {
                "free": 250.0, "total": 10250.0, "invested": 10000.0,
                "ppl": 250.0, "result": 0.0, "pieCash": 0.0, "blocked": 0.0,
            }
        mock_get.side_effect = _router
        b = t212_api.fetch_account_balances(_CREDS)
        self.assertEqual(b["net_liquidating_value"], 10250.0)
        self.assertEqual(b["cash_balance"], 250.0)
        self.assertEqual(b["margin_equity"], 10250.0)
        self.assertEqual(b["maintenance_requirement"], 0.0)
        # all expected keys present
        for k in ("equity_buying_power", "derivative_buying_power",
                  "maintenance_excess", "used_derivative_buying_power",
                  "reg_t_margin_requirement"):
            self.assertIn(k, b)

    @patch("t212_api._get")
    def test_a_euro_account_is_reported_in_dollars(self, mock_get):
        """T212 keeps a Dutch account in EUR. Adding that figure straight to a
        Tastytrade balance in USD would overstate nothing and understate the
        combined total by the whole FX difference — and the combined total is
        the number the portfolio header shows."""
        def _router(path, creds, **kw):
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            return {"free": 100.0, "total": 1000.0}
        mock_get.side_effect = _router
        with patch("gather_data.fetch_fx_rate", return_value=1.15):
            b = t212_api.fetch_account_balances(_CREDS)
        self.assertAlmostEqual(b["net_liquidating_value"], 1150.0)
        self.assertAlmostEqual(b["cash_balance"], 115.0)
        self.assertEqual(b["currency"], "USD")

    @patch("t212_api._get")
    def test_an_unknown_rate_reports_the_native_currency(self, mock_get):
        """Rather than pass off euros as dollars in a portfolio total."""
        def _router(path, creds, **kw):
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            return {"free": 100.0, "total": 1000.0}
        mock_get.side_effect = _router
        with patch("gather_data.fetch_fx_rate", return_value=None):
            b = t212_api.fetch_account_balances(_CREDS)
        self.assertAlmostEqual(b["net_liquidating_value"], 1000.0)
        self.assertEqual(b["currency"], "EUR")


class TestAdapterT212(unittest.TestCase):
    """broker_adapter routing to t212_api + neutral empties for options-only gaps.

    Empty-return shapes below are verified against the real TT/IBKR
    implementations (not just the brief's illustrative examples):
      - fetch_margin_requirements: {} (both TT and IBKR return {} when empty)
      - fetch_margin_for_position: None (both TT docstring and IBKR return None)
      - fetch_net_liq_history: [] (both return a list of {time, close} dicts)
      - fetch_portfolio_greeks: {"positions": [], "totals": {delta/theta/gamma/vega: 0}}
        — NOT bare {}; both TT and IBKR always return this structured dict.
      - fetch_beta_weighted_delta: {"positions": [], "portfolio_bwd": 0,
        "spy_price": 0, "dollar_per_1pct": 0} — NOT bare {}, same reasoning.
      - fetch_greeks_and_bwd: tuple of the two empties above (TT/IBKR both
        return a (greeks, bwd) tuple, never a dict).
      - fetch_yearly_transfers: {} (both return a plain dict keyed by year)
      - fetch_margin_interest: {"current_month": 0, "ytd": 0, "total": 0,
        "monthly": {}} — NOT bare {}; both TT and IBKR always return this shape.
      - fetch_option_chain: {"underlying_price": fallback_price, "expirations": []}
        — NOT [], both TT and IBKR return this dict shape on empty/error.
      - fetch_earnings_dates: {ticker: None for ticker in tickers} — NOT bare
        {}; both TT and IBKR return a dict keyed by every requested ticker.
    """

    def _patch_active(self, broker):
        return patch("broker_adapter.get_active_broker", return_value=broker)

    @patch("broker_adapter._get_t212_creds", return_value=_CREDS)
    @patch("broker_adapter.t212_api")
    def test_portfolio_routes_to_t212(self, mock_t212, _creds):
        mock_t212.fetch_portfolio_data.return_value = ({"AAPL": {}}, "42")
        with self._patch_active("t212"):
            out = broker_adapter.fetch_portfolio_data()
        self.assertEqual(out, ({"AAPL": {}}, "42"))
        mock_t212.fetch_portfolio_data.assert_called_once_with(_CREDS)

    @patch("broker_adapter._get_t212_creds", return_value=_CREDS)
    @patch("broker_adapter.t212_api")
    def test_balances_routes_to_t212(self, mock_t212, _creds):
        mock_t212.fetch_account_balances.return_value = {"net_liquidating_value": 1.0}
        with self._patch_active("t212"):
            out = broker_adapter.fetch_account_balances()
        self.assertEqual(out, {"net_liquidating_value": 1.0})
        mock_t212.fetch_account_balances.assert_called_once_with(_CREDS)

    def test_gap_functions_return_empty_for_t212(self):
        with self._patch_active("t212"):
            self.assertEqual(broker_adapter.fetch_margin_requirements(), {})
            self.assertIsNone(broker_adapter.fetch_margin_for_position("AAPL", 1))
            self.assertEqual(broker_adapter.fetch_net_liq_history(), [])
            self.assertEqual(
                broker_adapter.fetch_portfolio_greeks(),
                {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}},
            )
            self.assertEqual(
                broker_adapter.fetch_beta_weighted_delta(),
                {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0},
            )
            greeks, bwd = broker_adapter.fetch_greeks_and_bwd()
            self.assertEqual(
                greeks,
                {"positions": [], "totals": {"delta": 0, "theta": 0, "gamma": 0, "vega": 0}},
            )
            self.assertEqual(
                bwd,
                {"positions": [], "portfolio_bwd": 0, "spy_price": 0, "dollar_per_1pct": 0},
            )
            self.assertEqual(broker_adapter.fetch_yearly_transfers(), {})
            self.assertEqual(
                broker_adapter.fetch_margin_interest(),
                {"current_month": 0, "ytd": 0, "total": 0, "monthly": {}},
            )
            self.assertEqual(
                broker_adapter.fetch_option_chain("AAPL"),
                {"underlying_price": 0.0, "expirations": []},
            )
            self.assertEqual(
                broker_adapter.fetch_option_chain("AAPL", fallback_price=123.0),
                {"underlying_price": 123.0, "expirations": []},
            )
            self.assertEqual(
                broker_adapter.fetch_earnings_dates(["AAPL", "MSFT"]),
                {"AAPL": None, "MSFT": None},
            )

    def test_get_active_broker_detects_t212_alone(self):
        with patch.object(
            broker_adapter.st, "session_state",
            {"t212_credentials": _CREDS},
        ):
            self.assertEqual(broker_adapter.get_active_broker(), "t212")

    def test_has_active_broker_true_for_t212_only(self):
        with patch.object(
            broker_adapter.st, "session_state",
            {"t212_credentials": _CREDS},
        ):
            self.assertTrue(broker_adapter.has_active_broker())


class TestSignConvention(unittest.TestCase):
    """equity_cost / cost_per_share are negative — cash that left the account.

    The portfolio page computes unrealized P/L as `market_value + equity_cost`,
    matching how Tastytrade sums signed trade values. Returning a positive cost
    turned that subtraction into an addition: RDDT showed +$1,797 and +212.78%
    on a position that was actually down.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()

    @patch("t212_api._get")
    def test_costs_are_negative_and_pl_adds_up(self, mock_get):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [{"instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                        "isin": "US0378331005", "currency": "USD"},
                         "quantity": 10, "averagePricePaid": 150.0,
                         "currentPrice": 170.0,
                         "walletImpact": {"currency": "EUR", "totalCost": 1500.0,
                                          "currentValue": 1700.0,
                                          "unrealizedProfitLoss": 200.0}}]
            if path == "/equity/account/info":
                return {"id": 42}
            raise AssertionError(path)
        mock_get.side_effect = _router

        d = t212_api.fetch_portfolio_data(_CREDS)[0]["AAPL"]
        self.assertEqual(d["equity_cost"], -1500.0)
        self.assertEqual(d["cost_per_share"], -150.0)
        self.assertEqual(d["purchase_price"], 150.0)   # positive, for display
        self.assertTrue(d["buy_and_hold"])

        # The page's formula must now yield the real P/L, not cost + value.
        market_value = 10 * 170.0
        self.assertEqual(market_value + d["equity_cost"], 200.0)
        # ...and the return must be +13.3%, not +213%.
        self.assertAlmostEqual(
            (market_value + d["equity_cost"]) / abs(d["equity_cost"]) * 100,
            13.333, places=2)


class TestFxRate(unittest.TestCase):
    """USD conversion for a multi-currency portfolio.

    ECB is de eerste bron sinds 2026-09-22; deze tests zetten hem stil zodat
    de Yahoo-terugval getest wordt. TestFxRateEcb test de voorkeursroute.
    """

    def setUp(self):
        gather_data._FX_CACHE.clear()
        import fx
        self._ecb = patch.object(fx, "spot", return_value=None)
        self._ecb.start()
        self.addCleanup(self._ecb.stop)

    @patch("gather_data.fetch_stock_price")
    def test_usd_needs_no_lookup(self, mock_price):
        self.assertEqual(gather_data.fetch_fx_rate("USD"), 1.0)
        mock_price.assert_not_called()

    @patch("gather_data.fetch_stock_price", return_value=(1.1546, 0, 0))
    def test_eur_uses_the_pair_ticker(self, mock_price):
        self.assertAlmostEqual(gather_data.fetch_fx_rate("EUR"), 1.1546)
        mock_price.assert_called_once_with("EURUSD=X")

    @patch("gather_data.fetch_stock_price", return_value=(1.1546, 0, 0))
    def test_rate_is_cached_per_currency(self, mock_price):
        gather_data.fetch_fx_rate("EUR")
        gather_data.fetch_fx_rate("EUR")
        self.assertEqual(mock_price.call_count, 1)

    @patch("gather_data.fetch_stock_price", return_value=(0, 0, 0))
    def test_a_failed_lookup_returns_none_rather_than_a_wrong_number(self, _p):
        """Silently falling back to 1.0 would add euros to dollars as if they
        were the same unit — the caller must be able to tell it doesn't know."""
        self.assertIsNone(gather_data.fetch_fx_rate("EUR"))

    @patch("gather_data.fetch_stock_price", side_effect=RuntimeError("boom"))
    def test_an_exception_is_not_swallowed_into_a_rate(self, _p):
        self.assertIsNone(gather_data.fetch_fx_rate("EUR"))


class TestFxRateEcb(unittest.TestCase):
    def setUp(self):
        gather_data._FX_CACHE.clear()

    def test_the_ecb_answers_first_and_yahoo_is_not_asked(self):
        import fx
        with patch.object(fx, "spot", return_value=1.149) as ecb, \
             patch("gather_data.fetch_stock_price") as yahoo:
            self.assertAlmostEqual(gather_data.fetch_fx_rate("EUR"), 1.149)
        ecb.assert_called_once_with("EUR")
        yahoo.assert_not_called()

    def test_history_prefers_the_ecb_series(self):
        import fx
        from datetime import date
        series = {date(2026, 9, 21): 1.149}
        with patch.object(fx, "usd_per_unit", return_value=series), \
             patch("gather_data.fetch_daily_closes") as yahoo:
            self.assertEqual(gather_data.fetch_fx_history("EUR", 1), series)
        yahoo.assert_not_called()

    def test_history_falls_back_to_yahoo_when_the_ecb_is_silent(self):
        import fx
        with patch.object(fx, "usd_per_unit", return_value={}), \
             patch("gather_data.fetch_daily_closes", return_value={"x": 1}) as yahoo:
            self.assertEqual(gather_data.fetch_fx_history("EUR", 1), {"x": 1})
        yahoo.assert_called_once_with("EURUSD=X", 1)


class TestUsdNormalisation(unittest.TestCase):
    """Positions quoted in another currency are converted, not relabelled."""

    def setUp(self):
        # ECB stil: deze tests meten de Yahoo-koers die ze zelf stubben.
        import fx
        self._ecb = patch.object(fx, "spot", return_value=None)
        self._ecb.start()
        self.addCleanup(self._ecb.stop)
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()
        gather_data._FX_CACHE.clear()

    def _fetch(self):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [
                    {"instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                    "isin": "US0378331005", "currency": "USD"},
                     "quantity": 10, "averagePricePaid": 150.0, "currentPrice": 170.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 1300.0,
                                      "currentValue": 1473.0,
                                      "unrealizedProfitLoss": 173.0}},
                    {"instrument": {"ticker": "ASML_NL_EQ", "name": "ASML",
                                    "isin": "NL0010273215", "currency": "EUR"},
                     "quantity": 5, "averagePricePaid": 600.0, "currentPrice": 620.0,
                     "walletImpact": {"currency": "EUR", "totalCost": 3000.0,
                                      "currentValue": 3100.0,
                                      "unrealizedProfitLoss": 100.0}},
                ]
            if path == "/equity/account/info":
                return {"id": 42}
            raise AssertionError(path)
        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_stock_price", return_value=(1.20, 0, 0)):
            return t212_api.fetch_portfolio_data(_CREDS)[0]

    def test_a_usd_instrument_is_untouched(self):
        d = self._fetch()["AAPL"]
        self.assertEqual(d["purchase_price"], 150.0)
        self.assertEqual(d["broker_price"], 170.0)
        self.assertEqual(d["equity_cost"], -1500.0)
        self.assertEqual(d["currency"], "USD")

    def test_a_eur_instrument_is_converted_at_the_rate(self):
        """EUR 600 at 1.20 is USD 720 — the number changes, not just the sign
        in front of it. Leaving it at 600 under a $ label understates the
        holding and skews every weight in the table."""
        d = self._fetch()["ASML"]
        self.assertAlmostEqual(d["purchase_price"], 720.0)
        self.assertAlmostEqual(d["broker_price"], 744.0)
        self.assertAlmostEqual(d["equity_cost"], -3600.0)
        self.assertEqual(d["currency"], "USD")
        # The original is preserved so the row can say where it came from.
        self.assertEqual(d["native_currency"], "EUR")
        self.assertAlmostEqual(d["native_purchase_price"], 600.0)

    def test_an_unknown_rate_leaves_the_position_in_its_own_currency(self):
        """Better a row that says EUR than a dollar figure that is really
        euros."""
        with patch("gather_data.fetch_fx_rate", return_value=None):
            def _router(path, creds, **kw):
                if path == "/equity/metadata/instruments":
                    return _META
                if path == "/equity/positions":
                    return [{"instrument": {"ticker": "ASML_NL_EQ", "name": "ASML",
                                            "isin": "NL0010273215", "currency": "EUR"},
                             "quantity": 5, "averagePricePaid": 600.0,
                             "currentPrice": 620.0,
                             "walletImpact": {"currency": "EUR", "totalCost": 3000.0,
                                              "currentValue": 3100.0,
                                              "unrealizedProfitLoss": 100.0}}]
                if path == "/equity/account/info":
                    return {"id": 42}
                raise AssertionError(path)
            with patch("t212_api._get", side_effect=_router):
                d = t212_api.fetch_portfolio_data(_CREDS)[0]["ASML"]
        self.assertEqual(d["currency"], "EUR")
        self.assertAlmostEqual(d["purchase_price"], 600.0)


_ORDERS = {
    "items": [
        {"order": {"ticker": "WEBN1d_EQ", "side": "BUY",
                   "instrument": {"ticker": "WEBN1d_EQ", "name": "Amundi",
                                  "isin": "IE0003XJA0J9", "currency": "EUR"}},
         "fill": {"quantity": 10.0, "price": 12.00,
                  "filledAt": "2026-05-04T09:12:31.000+02:00",
                  "walletImpact": {"currency": "EUR", "netValue": -120.0}}},
        {"order": {"ticker": "WEBN1d_EQ", "side": "BUY",
                   "instrument": {"ticker": "WEBN1d_EQ", "name": "Amundi",
                                  "isin": "IE0003XJA0J9", "currency": "EUR"}},
         "fill": {"quantity": 13.73, "price": 13.00,
                  "filledAt": "2026-06-18T11:02:00.000+02:00",
                  "walletImpact": {"currency": "EUR", "netValue": -178.49}}},
        {"order": {"ticker": "AAPL_US_EQ", "side": "SELL",
                   "instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                  "isin": "US0378331005", "currency": "USD"}},
         "fill": {"quantity": 2.0, "price": 200.0,
                  "filledAt": "2026-07-01T15:30:00.000+02:00",
                  "walletImpact": {"currency": "EUR", "netValue": 346.0}}},
    ],
    "nextPagePath": None,
}


class TestSellQuantitySign(unittest.TestCase):
    """T212 geeft de hoeveelheid van een verkoop negatief terug.

    Ruw overgenomen telde een verkoop als een extra aankoop: CPRT stond op
    -2.002 in plaats van +111 gerealiseerd, en in de curve gingen de
    aandelen na de verkoop juist omhoog -- 64 CPRT en 47 WEBN als
    spookposities, ~$2.800 te veel op de Results-pagina (2026-09-21)."""

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = {}
        gather_data._FX_CACHE.clear()

    def test_a_sell_has_a_positive_quantity_and_brings_cash_in(self):
        item = {"order": {"ticker": "CPRT_US_EQ", "side": "SELL",
                          "instrument": {"ticker": "CPRT_US_EQ"}},
                "fill": {"quantity": -32.0, "price": 33.01,
                         "filledAt": "2026-09-11T15:30:00.000Z",
                         "walletImpact": {"currency": "EUR", "netValue": 905.0}}}
        with patch.object(gather_data, "fetch_fx_rate", return_value=1.0):
            trade = t212_api._fill_to_trade(item, _CREDS)
        self.assertEqual(trade["quantity"], 32.0)
        self.assertAlmostEqual(trade["net_value"], 32 * 33.01)
        self.assertAlmostEqual(trade["wallet_net_value"], 905.0)
        self.assertEqual(trade["action"], "Sell to Close")
        self.assertIn("Sold 32", trade["description"])


class TestTrades(unittest.TestCase):
    """T212 fills become the same trade shape Tastytrade produces.

    Without them a T212 position has an average price and no history, so it
    cannot be measured against the index, given a FIFO basis, or told apart
    from a single purchase — and WEBN was bought twice.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()
        gather_data._FX_CACHE.clear()

    def _trades(self, rate=1.0):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path.startswith("/equity/history/orders"):
                return _ORDERS
            raise AssertionError(path)
        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_fx_rate", return_value=rate):
            return t212_api.fetch_trades(_CREDS)

    def test_each_fill_becomes_a_dated_lot(self):
        webn = self._trades()["WEBN"]
        self.assertEqual([t["quantity"] for t in webn], [10.0, 13.73])
        self.assertEqual([t["date"].isoformat() for t in webn],
                         ["2026-05-04", "2026-06-18"])
        self.assertEqual([t["instrument_type"] for t in webn], ["Equity"] * 2)

    def test_the_wallet_impact_is_signed_by_direction(self):
        """T212 reports it as a magnitude. Taken at face value a purchase adds
        the money it just spent, which inflated the rebuilt account curve by
        the whole cost of the portfolio."""
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path.startswith("/equity/history/orders"):
                return {"items": [
                    {"order": {"ticker": "AAPL_US_EQ", "side": "BUY",
                               "instrument": {"ticker": "AAPL_US_EQ",
                                              "currency": "USD"}},
                     "fill": {"quantity": 2.0, "price": 150.0,
                              "filledAt": "2026-01-05T10:00:00.000Z",
                              "walletImpact": {"currency": "EUR",
                                               "netValue": 260.0}}},
                    {"order": {"ticker": "AAPL_US_EQ", "side": "SELL",
                               "instrument": {"ticker": "AAPL_US_EQ",
                                              "currency": "USD"}},
                     "fill": {"quantity": 2.0, "price": 170.0,
                              "filledAt": "2026-02-05T10:00:00.000Z",
                              "walletImpact": {"currency": "EUR",
                                               "netValue": 295.0}}},
                ], "nextPagePath": None}
            raise AssertionError(path)
        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_fx_rate", return_value=1.0):
            trades = t212_api.fetch_trades(_CREDS)["AAPL"]
        self.assertAlmostEqual(trades[0]["wallet_net_value"], -260.0)
        self.assertAlmostEqual(trades[1]["wallet_net_value"], 295.0)

    def test_a_buy_costs_cash_and_a_sale_returns_it(self):
        """net_value carries the app's sign convention, since every downstream
        walk reads the sign to tell a purchase from a sale."""
        webn = self._trades()["WEBN"]
        self.assertLess(webn[0]["net_value"], 0)
        self.assertIn("Buy", webn[0]["action"])
        aapl = self._trades()["AAPL"][0]
        self.assertGreater(aapl["net_value"], 0)
        self.assertIn("Sell", aapl["action"])

    def test_prices_are_converted_like_the_positions_are(self):
        """A EUR fill next to a USD cost basis would misplace the lot by the
        whole FX difference."""
        webn = self._trades(rate=1.15)["WEBN"]
        self.assertAlmostEqual(webn[0]["price"], 13.80)
        self.assertAlmostEqual(webn[0]["net_value"], -138.00)

    def test_trades_arrive_oldest_first(self):
        """FIFO retires the oldest lot, so the order the broker returns them in
        cannot be trusted to be the order they happened in."""
        webn = self._trades()["WEBN"]
        self.assertEqual(webn, sorted(webn, key=lambda t: t["date"]))

    def test_pagination_follows_the_next_page(self):
        seen = []

        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            seen.append(path)
            if len(seen) == 1:
                return {"items": _ORDERS["items"][:1],
                        "nextPagePath": "/equity/history/orders?cursor=2"}
            return {"items": _ORDERS["items"][1:], "nextPagePath": None}

        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_fx_rate", return_value=1.0):
            out = t212_api.fetch_trades(_CREDS)
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(out["WEBN"]), 2)

    def test_an_unreachable_history_yields_nothing_rather_than_failing(self):
        """The positions themselves still render; they just keep the broker's
        average price and no purchase date."""
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            raise RuntimeError("429")
        with patch("t212_api._get", side_effect=_router):
            self.assertEqual(t212_api.fetch_trades(_CREDS), {})


class TestClosedPositions(unittest.TestCase):
    """A position sold in full leaves /equity/positions entirely.

    Tastytrade reconstructs everything from transactions, so a closed name
    still has a card. T212 hands back only what is currently held, so without
    this a name you sold would vanish from the app the moment you sold it —
    exactly when the closed-position view has something to say about it.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()
        gather_data._FX_CACHE.clear()

    def _fetch(self):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [
                    {"instrument": {"ticker": "AAPL_US_EQ", "name": "Apple",
                                    "isin": "US0378331005", "currency": "USD"},
                     "quantity": 10, "averagePricePaid": 150.0,
                     "currentPrice": 170.0,
                     "walletImpact": {"currency": "USD", "totalCost": 1500.0,
                                      "currentValue": 1700.0,
                                      "unrealizedProfitLoss": 200.0}},
                ]
            if path.startswith("/equity/history/orders"):
                return {"items": [
                    {"order": {"ticker": "AAPL_US_EQ", "side": "BUY",
                               "instrument": {"ticker": "AAPL_US_EQ",
                                              "currency": "USD"}},
                     "fill": {"quantity": 10.0, "price": 150.0,
                              "filledAt": "2026-01-05T10:00:00.000Z"}},
                    {"order": {"ticker": "ASML_NL_EQ", "side": "BUY",
                               "instrument": {"ticker": "ASML_NL_EQ",
                                              "currency": "EUR"}},
                     "fill": {"quantity": 5.0, "price": 600.0,
                              "filledAt": "2026-02-01T10:00:00.000Z"}},
                    {"order": {"ticker": "ASML_NL_EQ", "side": "SELL",
                               "instrument": {"ticker": "ASML_NL_EQ",
                                              "currency": "EUR"}},
                     "fill": {"quantity": 5.0, "price": 700.0,
                              "filledAt": "2026-06-01T10:00:00.000Z"}},
                ], "nextPagePath": None}
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "USD"}
            raise AssertionError(path)
        with patch("t212_api._get", side_effect=_router), \
             patch("gather_data.fetch_fx_rate", return_value=1.0):
            return t212_api.fetch_portfolio_data(_CREDS)[0]

    def test_a_fully_sold_name_still_appears(self):
        self.assertIn("ASML", self._fetch())

    def test_it_holds_no_shares_and_no_value(self):
        """So the page files it under closed and never counts it as money."""
        d = self._fetch()["ASML"]
        self.assertEqual(d["shares_held"], 0)
        self.assertEqual(d["equity_cost"], 0.0)
        self.assertEqual(d["purchase_price"], 0.0)

    def test_it_keeps_the_trades_that_tell_its_story(self):
        d = self._fetch()["ASML"]
        self.assertEqual(len(d["trades"]), 2)
        self.assertEqual(d["total_pl"], 500.0)      # (700 - 600) x 5

    def test_open_positions_are_untouched(self):
        d = self._fetch()["AAPL"]
        self.assertEqual(d["shares_held"], 10)
        self.assertEqual(d["purchase_price"], 150.0)


class TestHistoryIntegration(unittest.TestCase):
    """The layer between the API and the pure reconstruction.

    Both bugs that reached the live account lived exactly here: a wallet
    impact taken at face value, and a symbol Yahoo does not answer to. Neither
    was reachable from a test of the arithmetic alone.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()
        gather_data._FX_CACHE.clear()

    def _router(self, calls):
        def _r(path, creds, **kw):
            calls.append(path)
            if path == "/equity/metadata/instruments":
                return _META
            if path.startswith("/equity/history/orders"):
                return {"items": [
                    {"order": {"ticker": "AAPL_US_EQ", "side": "BUY",
                               "instrument": {"ticker": "AAPL_US_EQ",
                                              "currency": "USD"}},
                     "fill": {"quantity": 2.0, "price": 100.0,
                              "filledAt": "2026-07-02T10:00:00.000Z",
                              "walletImpact": {"currency": "EUR",
                                               "netValue": 200.0}}},
                ], "nextPagePath": None}
            if path.startswith("/equity/history/transactions"):
                return {"items": [
                    {"dateTime": "2026-07-01T09:00:00.000Z", "amount": 1000.0,
                     "type": "DEPOSIT", "currency": "EUR", "reference": "d1"},
                ], "nextPagePath": None}
            raise AssertionError(path)
        return _r

    def _run(self, calls):
        from datetime import date
        closes = {date(2026, 7, 1): 100.0, date(2026, 7, 2): 100.0,
                  date(2026, 7, 3): 100.0}
        def _daily(sym, years):
            return closes if sym == "AAPL" else {}
        with patch("t212_api._get", side_effect=self._router(calls)), \
             patch("gather_data.fetch_daily_closes", side_effect=_daily), \
             patch("gather_data.fetch_fx_history",
                   return_value={date(2026, 7, 1): 1.0, date(2026, 7, 2): 1.0,
                                 date(2026, 7, 3): 1.0}), \
             patch("gather_data.fetch_fx_rate", return_value=1.0):
            # "all": the window must reach back to the first movement, not to
            # a month before today.
            return t212_api.fetch_net_liq_history(_CREDS, "all")

    def test_buying_shares_does_not_increase_the_account(self):
        """The whole point. A purchase moves value from cash into stock; if the
        wallet impact is read unsigned it adds the money back and the curve
        jumps by the cost of the trade."""
        series = self._run([])
        by_day = {p["time"]: p["close"] for p in series}
        self.assertAlmostEqual(by_day["2026-07-01"], 1000.0, places=2)
        self.assertAlmostEqual(by_day["2026-07-02"], 1000.0, places=2)

    def test_the_history_is_fetched_once_per_run(self):
        """fetch_portfolio_data and this both want the fills, and every page
        costs six seconds of throttle. Fetching twice made a Results load wait
        for the same data it already had."""
        calls = []
        self._run(calls)
        t212_api.fetch_trades(_CREDS)
        t212_api.fetch_cash_movements(_CREDS)
        orders = [c for c in calls if c.startswith("/equity/history/orders")]
        cash = [c for c in calls if c.startswith("/equity/history/transactions")]
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(cash), 1)


class TestTotalPlMeansNetCash(unittest.TestCase):
    """`total_pl` holds net cash moved, not profit.

    Tastytrade sums signed transaction values into it and the portfolio page
    finishes with `total_pl + market_value`. Trading 212 used to put the
    finished unrealized P/L there, so the page added the market value to a
    number that already contained it: every T212 holding showed its position's
    worth as its gain and filled the Top Performers list, four of them while
    losing money.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()

    def _cost_basis(self, mock_get, quantity, paid, now):
        def _router(path, creds, **kw):
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return [{"instrument": {"ticker": "AAPL_US_EQ",
                                        "isin": "US0378331005"},
                         "quantity": quantity, "averagePricePaid": paid,
                         "currentPrice": now,
                         "walletImpact": {"currency": "EUR"}}]
            if path == "/equity/account/info":
                return {"id": 42, "currencyCode": "EUR"}
            raise AssertionError(path)
        mock_get.side_effect = _router
        cb, _ = t212_api.fetch_portfolio_data(_CREDS)
        return cb["AAPL"]

    @patch("gather_data.fetch_fx_rate", return_value=1.0)
    @patch("t212_api._get")
    def test_a_losing_position_stays_losing(self, mock_get, _fx):
        """The bug's signature: bought at 150, now 100, and the page must not
        report a gain the size of the holding."""
        d = self._cost_basis(mock_get, 10, 150.0, 100.0)
        market_value = d["shares_held"] * d["broker_price"]
        self.assertAlmostEqual(d["total_pl"] + market_value, -500.0)

    @patch("gather_data.fetch_fx_rate", return_value=1.0)
    @patch("t212_api._get")
    def test_it_matches_the_equity_cost_convention(self, mock_get, _fx):
        """Both fields describe cash that left, so for a plain holding they
        agree. The closed-position branch already assumed as much."""
        d = self._cost_basis(mock_get, 10, 150.0, 170.0)
        self.assertAlmostEqual(d["total_pl"], d["equity_cost"])

    @patch("gather_data.fetch_fx_rate", return_value=1.0)
    @patch("t212_api._get")
    def test_the_sign_stays_negative_however_well_it_did(self, mock_get, _fx):
        """A held name has only ever cost money. A positive figure here is
        what turned the page's addition into a double count."""
        d = self._cost_basis(mock_get, 10, 150.0, 900.0)
        self.assertLess(d["total_pl"], 0)
        market_value = d["shares_held"] * d["broker_price"]
        self.assertAlmostEqual(d["total_pl"] + market_value, 7500.0)


class TestAccountInfoCache(unittest.TestCase):
    """/equity/account/info is the strictest-limited endpoint T212 exposes.

    A cold portfolio load asked for it twice — once for the account id, once
    for the base currency — and paid 32 seconds of 429 back-off for the pair,
    which was 76% of the page's total load time (measured 2026-08-27). Neither
    field changes, so one call has to serve both.
    """

    def setUp(self):
        t212_api._INSTRUMENTS_CACHE = None
        t212_api._clear_history_cache()

    @patch("t212_api._get")
    def test_repeat_calls_hit_the_cache(self, mock_get):
        mock_get.return_value = {"id": 42, "currencyCode": "EUR"}
        first = t212_api.fetch_account_info(_CREDS)
        second = t212_api.fetch_account_info(_CREDS)
        self.assertEqual(first, second)
        self.assertEqual(mock_get.call_count, 1)

    @patch("gather_data.fetch_fx_rate", return_value=1.1)
    @patch("t212_api._get")
    def test_balances_reuse_the_portfolio_load_s_account_info(self, mock_get, _fx):
        calls = []

        def _router(path, creds, **kw):
            calls.append(path)
            if path == "/equity/metadata/instruments":
                return _META
            if path == "/equity/positions":
                return []
            if path == "/equity/history/orders?limit=50":
                return {"items": [], "nextPagePath": None}
            if path == "/equity/account/info":
                return {"id": 7, "currencyCode": "EUR"}
            if path == "/equity/account/cash":
                return {"total": 100.0, "free": 25.0}
            return {}

        mock_get.side_effect = _router
        _cb, account_id = t212_api.fetch_portfolio_data(_CREDS)
        balances = t212_api.fetch_account_balances(_CREDS)

        self.assertEqual(account_id, "7")
        self.assertAlmostEqual(balances["net_liquidating_value"], 110.0)
        self.assertEqual(calls.count("/equity/account/info"), 1)

    @patch("t212_api._get")
    def test_clear_history_cache_also_drops_account_info(self, mock_get):
        mock_get.return_value = {"id": 1, "currencyCode": "USD"}
        t212_api.fetch_account_info(_CREDS)
        t212_api._clear_history_cache()
        t212_api.fetch_account_info(_CREDS)
        self.assertEqual(mock_get.call_count, 2)
