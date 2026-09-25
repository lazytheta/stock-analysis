"""Tests for the multi-broker aggregation in broker_adapter."""

import types
import unittest
from unittest.mock import patch

import broker_adapter

# Outside a Streamlit script run there is no ScriptRunContext, so the real
# st.session_state is unusable. Swap the module's `st` for a stand-in holding a
# plain dict — that is the whole of what the adapter asks of it. Patched per
# test rather than installed in sys.modules, so the rest of the suite still
# gets the real streamlit.
_FAKE_ST = types.SimpleNamespace(session_state={})


def _pos(shares, avg, price):
    return {
        "shares_held": shares,
        "equity_cost": -(shares * avg),
        "cost_per_share": -avg,
        "purchase_price": avg,
        "broker_price": price,
        "total_pl": (price - avg) * shares,
        "trades": [],
        "wheels": [],
        "option_pl": 0,
        "dividends": 0,
    }


class TestFetchAll(unittest.TestCase):
    def setUp(self):
        p = patch.object(broker_adapter, "st", _FAKE_ST)
        p.start()
        self.addCleanup(p.stop)
        _FAKE_ST.session_state.clear()

    def _connect(self, *brokers):
        keys = {"tastytrade": "tt_refresh_token",
                "t212": "t212_credentials",
                "ibkr": "ibkr_credentials"}
        for b in brokers:
            broker_adapter.st.session_state[keys[b]] = {"x": 1}

    def test_only_connected_brokers_are_queried(self):
        """A disconnected broker is not called — no stale credentials, no
        spurious API error blocking the page."""
        self._connect("t212")
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"RDDT": _pos(6, 162.49, 158.66)}, "42")) as t212, \
             patch("tastytrade_api.fetch_portfolio_data") as tt:
            cb, _acct, failures = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(t212.call_count, 1)
        self.assertEqual(tt.call_count, 0)
        self.assertEqual(list(cb), ["RDDT"])
        self.assertEqual(failures, [])

    def test_positions_from_both_brokers_appear_once_each(self):
        self._connect("tastytrade", "t212")
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"RDDT": _pos(6, 162.49, 158.66)}, "42")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"MSFT": _pos(10, 400.0, 420.0)}, "TT1")):
            cb, _acct, _failures = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(sorted(cb), ["MSFT", "RDDT"])
        self.assertEqual(cb["RDDT"]["broker"], "Trading 212")
        self.assertEqual(cb["MSFT"]["broker"], "Tastytrade")
        # The bare ticker travels in the row, because the dict key is a display
        # key that may be disambiguated — price and logo lookups need the real
        # symbol.
        self.assertEqual(cb["RDDT"]["symbol"], "RDDT")

    def test_the_same_ticker_at_two_brokers_stays_two_rows(self):
        """Mid-transfer the user holds DECK at both. Blending the cost bases
        would invent a purchase price that was never paid; two rows is what
        actually happened. Both keys are disambiguated, not just the second —
        a bare 'DECK' next to 'DECK (Trading 212)' reads as if the first row
        were broker-less."""
        self._connect("tastytrade", "t212")
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"DECK": _pos(10, 96.87, 96.82)}, "42")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"DECK": _pos(4, 120.00, 96.82)}, "TT1")):
            cb, _, _ = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(sorted(cb), ["DECK (Tastytrade)", "DECK (Trading 212)"])
        self.assertEqual(cb["DECK (Trading 212)"]["shares_held"], 10)
        self.assertEqual(cb["DECK (Tastytrade)"]["shares_held"], 4)
        for row in cb.values():
            self.assertEqual(row["symbol"], "DECK")

    def test_a_failing_broker_is_reported_not_swallowed(self):
        """One dead broker must not silently shrink the portfolio: the other
        broker's rows still come through, and the caller is told which broker
        is missing so the total can be labelled incomplete rather than wrong."""
        self._connect("tastytrade", "t212")
        with patch("t212_api.fetch_portfolio_data",
                   side_effect=RuntimeError("429")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"MSFT": _pos(10, 400.0, 420.0)}, "TT1")):
            cb, _acct, failures = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(list(cb), ["MSFT"])
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0][0], "Trading 212")
        self.assertIn("429", str(failures[0][1]))

    def test_the_account_id_comes_from_the_active_broker(self):
        self._connect("tastytrade", "t212")
        broker_adapter.st.session_state["active_broker"] = "t212"
        with patch("t212_api.fetch_portfolio_data",
                   return_value=({"RDDT": _pos(6, 162.49, 158.66)}, "42")), \
             patch("tastytrade_api.fetch_portfolio_data",
                   return_value=({"MSFT": _pos(10, 400.0, 420.0)}, "TT1")):
            _, acct, _ = broker_adapter.fetch_all_portfolio_data()
        self.assertEqual(acct, "42")


class TestCombinedBalances(unittest.TestCase):
    def setUp(self):
        p = patch.object(broker_adapter, "st", _FAKE_ST)
        p.start()
        self.addCleanup(p.stop)
        _FAKE_ST.session_state.clear()

    def test_net_liq_sums_across_brokers(self):
        broker_adapter.st.session_state["tt_refresh_token"] = "x"
        broker_adapter.st.session_state["t212_credentials"] = {"x": 1}
        with patch("t212_api.fetch_account_balances",
                   return_value={"net_liquidating_value": 1000.0,
                                 "cash_balance": 100.0}), \
             patch("tastytrade_api.fetch_account_balances",
                   return_value={"net_liquidating_value": 5000.0,
                                 "cash_balance": 200.0}):
            total, per_broker, failures = broker_adapter.fetch_all_net_liq()
        self.assertEqual(total, 6000.0)
        self.assertEqual(per_broker["Trading 212"], 1000.0)
        self.assertEqual(per_broker["Tastytrade"], 5000.0)
        self.assertEqual(failures, [])

    def test_a_failing_broker_leaves_the_total_flagged(self):
        broker_adapter.st.session_state["tt_refresh_token"] = "x"
        broker_adapter.st.session_state["t212_credentials"] = {"x": 1}
        with patch("t212_api.fetch_account_balances",
                   side_effect=RuntimeError("down")), \
             patch("tastytrade_api.fetch_account_balances",
                   return_value={"net_liquidating_value": 5000.0}):
            total, _per_broker, failures = broker_adapter.fetch_all_net_liq()
        self.assertEqual(total, 5000.0)
        self.assertEqual([f[0] for f in failures], ["Trading 212"])


class TestEarningsProxy(unittest.TestCase):
    """Een Europese lijn krijgt zijn earnings-datum via de Amerikaanse ADR."""

    def test_a_paris_line_is_looked_up_under_its_adr_and_reported_as_itself(self):
        with patch.object(broker_adapter, "_fetch_earnings_dates_raw",
                          return_value={"HESAY": {"date": "2026-10-01"},
                                        "MSFT": {"date": "2026-10-28"}}) as raw:
            out = broker_adapter.fetch_earnings_dates(["RMS.PA", "MSFT"])
        raw.assert_called_once_with(["HESAY", "MSFT"])
        self.assertEqual(out, {"RMS.PA": {"date": "2026-10-01"},
                               "MSFT": {"date": "2026-10-28"}})

    def test_a_name_without_a_proxy_passes_through(self):
        with patch.object(broker_adapter, "_fetch_earnings_dates_raw",
                          return_value={"AAPL": None}):
            self.assertEqual(broker_adapter.fetch_earnings_dates(["AAPL"]), {"AAPL": None})


if __name__ == "__main__":
    unittest.main()


class TestMergeNetLiq(unittest.TestCase):
    """Adding two brokers' account curves into one.

    Each broker reports on its own dates — Tastytrade snapshots, Trading 212
    one point per calendar day — so the union of dates is the only honest grid,
    and each series carries its last value forward across the gaps.
    """

    A = [{"time": "2026-07-01", "close": 100.0},
         {"time": "2026-07-03", "close": 120.0}]
    B = [{"time": "2026-07-02", "close": 50.0},
         {"time": "2026-07-03", "close": 60.0}]

    def test_it_sums_on_the_union_of_dates(self):
        out = broker_adapter.merge_net_liq_series([self.A, self.B])
        self.assertEqual([p["time"] for p in out],
                         ["2026-07-01", "2026-07-02", "2026-07-03"])
        self.assertAlmostEqual(out[-1]["close"], 180.0)

    def test_a_series_carries_its_last_value_across_a_gap(self):
        """Tastytrade does not print on a weekend. Treating the gap as zero
        would drop the whole account out of the curve for two days."""
        out = broker_adapter.merge_net_liq_series([self.A, self.B])
        self.assertAlmostEqual(out[1]["close"], 150.0)   # 100 carried + 50

    def test_an_account_counts_only_from_its_first_point(self):
        """The T212 account did not exist before July. Back-filling it would
        invent money, and back-filling zero is the same thing said quietly."""
        out = broker_adapter.merge_net_liq_series([self.A, self.B])
        self.assertAlmostEqual(out[0]["close"], 100.0)

    def test_two_brokers_that_stamp_dates_differently_still_line_up(self):
        """Tastytrade returns a timestamp, Trading 212 a bare date. Compared as
        strings those are different days, so the union doubled up and every
        second point counted one account. They are the same day and must key
        as one."""
        tt = [{"time": "2026-07-01T21:00:00.000+00:00", "close": 100.0},
              {"time": "2026-07-02T21:00:00.000+00:00", "close": 110.0}]
        t212 = [{"time": "2026-07-01", "close": 50.0},
                {"time": "2026-07-02", "close": 55.0}]
        out = broker_adapter.merge_net_liq_series([tt, t212])
        self.assertEqual([p["time"] for p in out], ["2026-07-01", "2026-07-02"])
        self.assertAlmostEqual(out[0]["close"], 150.0)
        self.assertAlmostEqual(out[1]["close"], 165.0)

    def test_several_snapshots_on_one_day_collapse_to_the_last(self):
        """Tastytrade prints intraday. Summing them would count the account
        as many times as it was sampled."""
        tt = [{"time": "2026-07-01T09:00:00Z", "close": 100.0},
              {"time": "2026-07-01T21:00:00Z", "close": 120.0}]
        t212 = [{"time": "2026-07-01", "close": 50.0}]
        out = broker_adapter.merge_net_liq_series([tt, t212])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["close"], 170.0)

    def test_a_datetime_object_is_accepted_too(self):
        """The Tastytrade SDK hands back datetimes, not always strings."""
        from datetime import datetime
        tt = [{"time": datetime(2026, 7, 1, 21, 0), "close": 100.0}]
        t212 = [{"time": "2026-07-01", "close": 50.0}]
        out = broker_adapter.merge_net_liq_series([tt, t212])
        self.assertEqual(out[0]["time"], "2026-07-01")
        self.assertAlmostEqual(out[0]["close"], 150.0)

    def test_one_series_passes_through(self):
        self.assertEqual(broker_adapter.merge_net_liq_series([self.A]), self.A)

    def test_empty_input_yields_empty(self):
        self.assertEqual(broker_adapter.merge_net_liq_series([]), [])
        self.assertEqual(broker_adapter.merge_net_liq_series([[], []]), [])


class TestMergeTransfers(unittest.TestCase):
    def test_a_transfer_between_your_own_brokers_nets_out(self):
        """Moving money from Tastytrade to Trading 212 is a withdrawal there
        and a deposit here. Summed, it is what it is: not new money."""
        tt = {2026: {"total": -5000.0, "months": {8: -5000.0}}}
        t212 = {2026: {"total": 5000.0, "months": {8: 5000.0}}}
        out = broker_adapter.merge_yearly_transfers([tt, t212])
        self.assertAlmostEqual(out[2026]["total"], 0.0)
        self.assertAlmostEqual(out[2026]["months"][8], 0.0)

    def test_years_and_months_combine(self):
        a = {2025: {"total": 100.0, "months": {1: 100.0}},
             2026: {"total": 200.0, "months": {3: 200.0}}}
        b = {2026: {"total": 50.0, "months": {3: 20.0, 4: 30.0}}}
        out = broker_adapter.merge_yearly_transfers([a, b])
        self.assertAlmostEqual(out[2025]["total"], 100.0)
        self.assertAlmostEqual(out[2026]["total"], 250.0)
        self.assertAlmostEqual(out[2026]["months"][3], 220.0)
        self.assertAlmostEqual(out[2026]["months"][4], 30.0)


class TestParallelBrokerFetch(unittest.TestCase):
    """Brokers are fetched at once, not one after another.

    Serially a cold load cost the sum of every broker: 1.4s of Tastytrade plus
    2.1s of Trading 212 where 2.1s would do (measured 2026-08-27). Wall clock
    is not asserted here — that would be a flaky test on a shared runner — but
    the behaviour that has to survive parallelism is: order preserved, one
    broker's failure not taking the other's positions down with it.
    """

    def setUp(self):
        self.st = types.SimpleNamespace(session_state={
            "tt_refresh_token": "rt", "t212_credentials": {"k": "v"},
        })

    def _run(self, fetch_one):
        with patch.object(broker_adapter, "st", self.st), \
             patch.object(broker_adapter, "_fetch_one", fetch_one), \
             patch.object(broker_adapter, "_get_refresh_token", lambda: "rt"), \
             patch.object(broker_adapter, "_get_t212_creds", lambda: {"k": "v"}):
            return broker_adapter.fetch_all_portfolio_data()

    def test_both_brokers_land_and_keep_display_order(self):
        def _one(broker):
            if broker == "tastytrade":
                return {"AAPL": _pos(10, 100.0, 110.0)}, "TT-1"
            return {"ASML": _pos(5, 600.0, 590.0)}, "T212-1"

        merged, account_id, failures = self._run(_one)
        self.assertEqual(failures, [])
        self.assertEqual(set(merged), {"AAPL", "ASML"})
        self.assertEqual(merged["AAPL"]["broker"], broker_adapter.BROKER_NAMES["tastytrade"])
        self.assertEqual(merged["ASML"]["broker"], broker_adapter.BROKER_NAMES["t212"])
        self.assertTrue(account_id)

    def test_one_broker_failing_leaves_the_other_intact(self):
        def _one(broker):
            if broker == "t212":
                raise RuntimeError("T212 unreachable")
            return {"AAPL": _pos(10, 100.0, 110.0)}, "TT-1"

        merged, _account_id, failures = self._run(_one)
        self.assertEqual(set(merged), {"AAPL"})
        self.assertEqual([n for n, _ in failures],
                         [broker_adapter.BROKER_NAMES["t212"]])

    def test_a_ticker_at_both_brokers_still_gets_suffixed_rows(self):
        def _one(broker):
            if broker == "tastytrade":
                return {"DECK": _pos(3, 90.0, 95.0)}, "TT-1"
            return {"DECK": _pos(2, 80.0, 95.0)}, "T212-1"

        merged, _a, _f = self._run(_one)
        self.assertEqual(
            set(merged),
            {f"DECK ({broker_adapter.BROKER_NAMES['tastytrade']})",
             f"DECK ({broker_adapter.BROKER_NAMES['t212']})"},
        )


class TestSliceNetLiq(unittest.TestCase):
    """The Results page held the full curve and fetched the selected period
    anyway, rebuilding from the same fills — 3.65s of a 10.35s load."""

    def setUp(self):
        from datetime import date, timedelta
        today = date.today()
        self.series = [
            {"time": (today - timedelta(days=d)).strftime("%Y-%m-%d"),
             "close": 1000.0 + d}
            for d in (400, 200, 100, 20, 5, 0)
        ]

    def test_a_window_keeps_only_its_own_days(self):
        out = broker_adapter.slice_net_liq(self.series, "1m")
        self.assertEqual(len(out), 3)  # 20, 5 and 0 days back
        self.assertEqual(out[-1]["close"], 1000.0)

    def test_all_returns_everything(self):
        self.assertEqual(broker_adapter.slice_net_liq(self.series, "all"),
                         self.series)

    def test_a_year_drops_the_point_beyond_it(self):
        out = broker_adapter.slice_net_liq(self.series, "1y")
        self.assertEqual(len(out), 5)  # the 400-day-old point falls away

    def test_an_unknown_period_shows_more_rather_than_nothing(self):
        # Too much history is cosmetic; an empty chart is a bug.
        self.assertEqual(broker_adapter.slice_net_liq(self.series, "decade"),
                         self.series)

    def test_no_series_is_no_window(self):
        self.assertEqual(broker_adapter.slice_net_liq([], "1m"), [])
        self.assertEqual(broker_adapter.slice_net_liq(None, "1m"), [])

    def test_iso_timestamps_slice_the_same_as_bare_dates(self):
        # Tastytrade stamps ISO, Trading 212 a bare date, and a merged series
        # carries both. _day_key is what makes them comparable.
        from datetime import date, timedelta
        recent = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d")
        old = (date.today() - timedelta(days=300)).strftime("%Y-%m-%d")
        mixed = [{"time": f"{old}T12:00:00Z", "close": 1.0},
                 {"time": recent, "close": 2.0}]
        out = broker_adapter.slice_net_liq(mixed, "1m")
        self.assertEqual([p["close"] for p in out], [2.0])


class TestFetchCurrentPricesLayering(unittest.TestCase):
    """Broker feed first, Yahoo only for the gaps.

    Yahoo blocks by source IP and has been widening the datacenter ranges it
    refuses, so it cannot be the primary source for a cloud-hosted app. The
    broker feed is authenticated and unaffected, but only lists US equities —
    so both paths have to work, and the split has to be by symbol.
    """

    def setUp(self):
        # Nasdaq zit nu tussen broker en Yahoo; deze tests gaan over de
        # broker/Yahoo-splitsing, dus Nasdaq levert hier niets (en gaat niet
        # het net op).
        patcher = patch.object(broker_adapter.quotes, "fetch_nasdaq_quotes",
                               return_value={})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _st(self, connected=True):
        return types.SimpleNamespace(
            session_state={"tt_refresh_token": "rt"} if connected else {})

    def test_broker_covers_everything_yahoo_not_called(self):
        broker = {"MSFT": {"price": 451.0, "previousClose": 450.0}}
        with patch.object(broker_adapter, "st", self._st()), \
             patch.object(broker_adapter, "_get_refresh_token",
                          return_value="rt"), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_quotes_via_broker", return_value=broker), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_current_prices") as yahoo:
            out = broker_adapter.fetch_current_prices(["MSFT"])
        yahoo.assert_not_called()
        self.assertAlmostEqual(out["MSFT"]["price"], 451.0)

    def test_yahoo_fills_only_what_the_broker_missed(self):
        # RMS.PA is a Paris line; Tastytrade does not carry it.
        broker = {"MSFT": {"price": 451.0, "previousClose": 450.0}}
        with patch.object(broker_adapter, "st", self._st()), \
             patch.object(broker_adapter, "_get_refresh_token",
                          return_value="rt"), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_quotes_via_broker", return_value=broker), \
             patch.object(
                 broker_adapter.tastytrade_api, "fetch_current_prices",
                 return_value={"RMS.PA": {"price": 1613.5,
                                          "previousClose": 1600.0}}) as yahoo:
            out = broker_adapter.fetch_current_prices(["MSFT", "RMS.PA"])
        yahoo.assert_called_once_with(["RMS.PA"])
        self.assertAlmostEqual(out["MSFT"]["price"], 451.0)
        self.assertAlmostEqual(out["RMS.PA"]["price"], 1613.5)

    def test_no_broker_connected_uses_yahoo_for_all(self):
        with patch.object(broker_adapter, "st", self._st(connected=False)), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_quotes_via_broker") as feed, \
             patch.object(
                 broker_adapter.tastytrade_api, "fetch_current_prices",
                 return_value={"MSFT": {"price": 451.0,
                                        "previousClose": 450.0}}):
            out = broker_adapter.fetch_current_prices(["MSFT"])
        feed.assert_not_called()
        self.assertAlmostEqual(out["MSFT"]["price"], 451.0)

    def test_broker_blowing_up_does_not_take_prices_down(self):
        with patch.object(broker_adapter, "st", self._st()), \
             patch.object(broker_adapter, "_get_refresh_token",
                          return_value="rt"), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_quotes_via_broker",
                          side_effect=RuntimeError("socket died")), \
             patch.object(
                 broker_adapter.tastytrade_api, "fetch_current_prices",
                 return_value={"MSFT": {"price": 451.0,
                                        "previousClose": 450.0}}):
            out = broker_adapter.fetch_current_prices(["MSFT"])
        self.assertAlmostEqual(out["MSFT"]["price"], 451.0)

    def test_every_requested_ticker_is_a_key(self):
        # The watchlist reads by .get(); a missing key and a None must not be
        # two different things downstream.
        with patch.object(broker_adapter, "st", self._st(connected=False)), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_current_prices", return_value={}):
            out = broker_adapter.fetch_current_prices(["MSFT", "RMS.PA"])
        self.assertEqual(set(out), {"MSFT", "RMS.PA"})
        self.assertIsNone(out["MSFT"])

    def test_nasdaq_fills_before_yahoo(self):
        with patch.object(broker_adapter, "st", self._st(connected=False)), \
             patch.object(broker_adapter.quotes, "fetch_nasdaq_quotes",
                          return_value={"NFLX": {"price": 72.0,
                                                 "previousClose": 71.0,
                                                 "asof": None,
                                                 "venue": "Nasdaq"}}), \
             patch.object(broker_adapter.tastytrade_api,
                          "fetch_current_prices") as yahoo:
            out = broker_adapter.fetch_current_prices(["NFLX"])
        yahoo.assert_not_called()
        self.assertAlmostEqual(out["NFLX"]["price"], 72.0)
