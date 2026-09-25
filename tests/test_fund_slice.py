"""The watchlist's stored EDGAR slice.

The page needs three numbers per ticker and was downloading a 5 MB
companyfacts file per name to get them. These tests pin the two properties
that make storing a slice safe: it carries everything the calculations read,
and it never silently stands in for data that is not there.
"""

import pytest

import scorecard_utils as su


def _fund():
    """A fundamentals dict shaped like fetch_fundamentals returns."""
    n = 6
    return {
        "years": list(range(2020, 2020 + n)),
        "operating_income": [300.0] * n,
        "total_assets": [1500.0] * n,
        "current_liabilities": [500.0] * n,
        "net_income": [200.0] * n,
        "total_equity": [900.0] * n,
        "cash": [100.0] * n,
        "short_term_investments": [50.0] * n,
        "long_term_investments": [0.0] * n,
        "total_debt": [200.0] * n,
        "operating_lease_liabilities": [10.0] * n,
        "finance_lease_liabilities": [0.0] * n,
        "fcf": [250.0] * n,
        "shares": [1_000_000_000] * n,
        # Everything below is the 5 MB the page never reads.
        "revenue": [5000.0] * n,
        "gross_profit": [2000.0] * n,
        "rd": [400.0] * n,
        "eps_diluted": [0.2] * n,
        "capex": [-100.0] * n,
    }


def test_the_slice_reproduces_the_metric_exactly():
    """A stored slice has to give the same answer as the full fetch, or the
    watchlist would quietly start showing different numbers than the detail
    page — the two-answers-to-one-question problem this codebase keeps
    paying for."""
    full = _fund()
    sl = su.slim_fundamentals(full)
    assert su.compute_roce_metric(sl) == su.compute_roce_metric(full)


def test_the_slice_reproduces_per_year_roce():
    full = _fund()
    sl = su.slim_fundamentals(full)
    for i in range(len(full["years"])):
        assert su.roce_for_year(sl, i) == su.roce_for_year(full, i)


def test_the_slice_drops_what_the_page_never_reads():
    """The whole point is size. Revenue and the rest are 80% of the payload
    and nothing on the row uses them."""
    sl = su.slim_fundamentals(_fund())
    assert "revenue" not in sl
    assert "capex" not in sl
    assert set(sl) <= set(su.WATCHLIST_FUND_KEYS)


def test_the_slice_is_small():
    import json
    sl = su.slim_fundamentals(_fund())
    assert len(json.dumps(sl)) < 2000


def test_an_absent_series_stays_absent():
    """A filer that reports no debt and one we never asked about must not
    look identical. An empty list here would read as 'reports none'."""
    full = _fund()
    del full["total_debt"]
    sl = su.slim_fundamentals(full)
    assert "total_debt" not in sl


def test_no_fundamentals_gives_no_slice():
    assert su.slim_fundamentals(None) is None
    assert su.slim_fundamentals({}) is None


def test_a_slice_without_years_is_not_usable():
    """`slice_is_usable` is the gate that decides whether the page skips the
    network. A slice it accepts but cannot compute from would blank the
    ticker's metrics while looking like data."""
    assert not su.slice_is_usable(None)
    assert not su.slice_is_usable({})
    assert not su.slice_is_usable({"operating_income": [1.0]})
    assert su.slice_is_usable(su.slim_fundamentals(_fund()))


def test_a_usable_slice_actually_computes():
    sl = su.slim_fundamentals(_fund())
    assert su.slice_is_usable(sl)
    metric, avg = su.compute_roce_metric(sl)
    assert metric in ("ROCE", "ROE")
    assert avg is not None


class TestSaveGuard:
    """save_config must not let a caller wipe the slice by passing an empty
    one — the page would silently go back to downloading 380 MB."""

    def test_the_slice_is_a_guarded_key(self):
        import config_store
        assert "fund_slice" in config_store._GUARDED_KEYS_RESTORE_EMPTY

    def test_an_omitted_slice_survives_a_partial_save(self, monkeypatch):
        import config_store
        bestaand = {"ticker": "TST", "fund_slice": {"years": [2024]},
                    "stock_price": 10.0}
        opgeslagen = {}

        monkeypatch.setattr(config_store, "load_config",
                            lambda c, t, user_id=None: dict(bestaand))
        monkeypatch.setattr(config_store, "_get_user_id", lambda c: "u1")

        class _Tabel:
            def upsert(self, row, **kw):
                opgeslagen.update(row)
                return self

            def execute(self):
                return type("R", (), {"data": [opgeslagen]})()

        monkeypatch.setattr(config_store, "logger",
                            type("L", (), {"warning": lambda *a, **k: None,
                                           "info": lambda *a, **k: None,
                                           "debug": lambda *a, **k: None})())
        client = type("C", (), {"table": lambda self, n: _Tabel()})()

        config_store.save_config(client, "TST", {"stock_price": 12.0},
                                 user_id="u1")
        assert opgeslagen["config"]["fund_slice"] == {"years": [2024]}



class TestSlimConfigLoad:
    """The watchlist loads configs without ai_notes: 79% of the payload for a
    key the rows never read. The risk is not the read but a write that
    follows one — a partial config saved as if it were complete."""

    def test_the_slim_load_reads_a_different_source(self, monkeypatch):
        import config_store
        gezien = {}

        class _Q:
            def select(self, *a):
                return self

            def eq(self, *a):
                return self

            def execute(self):
                return type("R", (), {"data": []})()

        class _C:
            def table(self, naam):
                gezien["bron"] = naam
                return _Q()

        config_store.load_all_configs(_C(), user_id="u1")
        assert gezien["bron"] == "watchlist_configs"

        config_store.load_all_configs(_C(), user_id="u1", include_ai_notes=False)
        assert gezien["bron"] == "watchlist_configs_no_notes"

    def test_the_watchlist_rows_never_write_a_config(self):
        """A partial config handed to save_config is how the prescan sections
        got wiped once before. save_config's merge would survive it, but the
        rendering path has no business saving at all — this pins that.

        A user-click action (e.g. the Aspirant row's NO button) may still
        write, but only by calling out to a module-level helper — such as
        `_reject_aspirant` — that loads the complete config with
        `load_config` before saving it. Saving any config obtained from the
        display-only `load_all_configs(include_ai_notes=False)` load used to
        build these rows remains forbidden."""
        import re
        src = open("streamlit_app.py").read().split("\n")
        start = next(i for i, line in enumerate(src) if "_cached_watchlist" in line)
        einde = start
        for i in range(start, min(start + 900, len(src))):
            if re.match(r"^def |^# ══", src[i]) and i > start + 50:
                einde = i
                break
        blok = "\n".join(src[start:einde])
        assert "save_config" not in blok

if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestNewTickerGetsASlice:
    """A ticker added today must arrive with a slice. Without one the
    watchlist falls back to a 5 MB fetch for it on every cold load until the
    next Refresh All — the old behaviour, for that one name."""

    def _run_analysis_source(self):
        src = open("streamlit_app.py").read().split("\n")
        start = next(i for i, line in enumerate(src)
                     if line.startswith("def run_analysis("))
        einde = next(i for i in range(start + 1, len(src))
                     if src[i].startswith("def ") or src[i].startswith("# ══"))
        return "\n".join(src[start:einde])

    def test_run_analysis_builds_the_slice(self):
        blok = self._run_analysis_source()
        assert "slim_fundamentals(fetch_fundamentals(" in blok
        assert 'cfg["fund_slice"]' in blok

    def test_it_uses_the_ten_year_raw_share_source(self):
        """parse_financials returns six years and shares in millions;
        fetch_fundamentals ten years and a raw count. Reading the wrong one
        put every EDGAR-derived FCF yield at 0.0% once already, so the slice
        must come from fetch_fundamentals with n_years=10."""
        blok = self._run_analysis_source()
        assert "fetch_fundamentals(ticker, n_years=10)" in blok

    def test_a_slice_failure_does_not_lose_the_analysis(self):
        """The DCF that just succeeded is worth more than the cache. A failed
        slice must degrade to the fallback, not raise."""
        blok = self._run_analysis_source()
        i = blok.index("slim_fundamentals(fetch_fundamentals(")
        omgeving = blok[max(0, i - 400):i + 800]
        assert "try:" in omgeving and "except Exception" in omgeving
