"""Tests for multi-lens fair value (Phase 1)."""
from unittest.mock import MagicMock

import pytest


def make_cfg(**overrides):
    cfg = {
        "company": "Test Co",
        "ticker": "TEST",
        "stock_price": 100.0,
        "equity_market_value": 100_000,
        "debt_market_value": 10_000,
        "risk_free_rate": 0.04,
        "erp": 0.05,
        "credit_spread": 0.01,
        "tax_rate": 0.21,
        "sector_betas": [("Sector", 1.0, 1.0)],
        "base_revenue": 50_000,
        "revenue_growth": [0.05] * 5,
        "op_margins": [0.20] * 5,
        "terminal_growth": 0.025,
        "terminal_margin": 0.18,
        "sales_to_capital": 1.5,
        "sbc_pct": 0.02,
        "shares_outstanding": 1_000,
        "buyback_rate": 0.0,
        "margin_of_safety": 0.20,
        "cash_bridge": 5_000,
        "securities": 0,
        "bull_growth_adj": 0.02,
        "bear_growth_adj": -0.04,
        "bull_margin_adj": 0.02,
        "bear_margin_adj": -0.02,
        "peers": [],
    }
    cfg.update(overrides)
    return cfg


def make_peer(**overrides):
    p = {
        "ticker": "PEER1",
        "name": "Peer Co",
        "ev_revenue": 5.0,
        "ev_ebitda": 12.0,
        "pe": 20.0,
        "fwd_pe": 18.0,
        "op_margin": 0.20,
        "rev_growth": 0.05,
        "roic": 0.15,
    }
    p.update(overrides)
    return p


SAMPLE_VALUATION_INPUTS = {
    "forward_eps": 5.0,
    "historical_fwd_pe": 20.0,
    "ttm_ebitda": 12_000.0,
    "target_dividend_yield": 0.02,
    "current_dividend": 2.0,
    "expected_dividend_growth": 0.07,
}

from scorecard_utils import parse_scorecard, parse_scorecard_json


# ---------------------------------------------------------------- scorecard

def test_parse_scorecard_json_fenced():
    raw = """
Some preamble.

```json
{"verdict": "deep_dive", "phase": {"number": 5, "name": "Capital Return"}}
```

trailing text
"""
    assert parse_scorecard_json(raw) == {
        "verdict": "deep_dive",
        "phase": {"number": 5, "name": "Capital Return"},
    }


def test_parse_scorecard_json_unfenced():
    raw = '{"verdict": "pass"}'
    assert parse_scorecard_json(raw) == {"verdict": "pass"}


def test_parse_scorecard_json_empty():
    assert parse_scorecard_json("") is None
    assert parse_scorecard_json(None) is None


def test_parse_scorecard_returns_verdict_and_phase():
    ai_notes = {
        "Scorecard": '```json\n{"verdict":"revisit","phase":{"number":4,"name":"Op. Lev."}}\n```'
    }
    assert parse_scorecard(ai_notes) == {"verdict": "revisit", "phase": 4}


def test_parse_scorecard_no_section_returns_nones():
    assert parse_scorecard({}) == {"verdict": None, "phase": None}
    assert parse_scorecard({"Other": "x"}) == {"verdict": None, "phase": None}


def test_parse_scorecard_section_unparseable_returns_nones():
    assert parse_scorecard({"Scorecard": "not json"}) == {"verdict": None, "phase": None}


def test_parse_scorecard_compact_phase():
    """Plain-int and string-digit `phase` values should be extracted, not silently dropped."""
    int_form = {"Scorecard": '```json\n{"verdict":"pass","phase":3}\n```'}
    assert parse_scorecard(int_form) == {"verdict": "pass", "phase": 3}

    str_form = {"Scorecard": '```json\n{"verdict":"pass","phase":"4"}\n```'}
    assert parse_scorecard(str_form) == {"verdict": "pass", "phase": 4}


# ---------------------------------------------------------------- config preservation


def test_save_config_preserves_valuation_keys():
    """save_config must merge in valuation_inputs/valuation_summary/lens_weights
    from the existing DB row when the caller's cfg omits them."""
    import config_store

    existing = {
        "company": "X",
        "ai_notes": {"foo": "bar"},
        "peers": [{"ticker": "P"}],
        "valuation_inputs": {"forward_eps": 5.0},
        "valuation_summary": {"weighted_fv_mid": 80.0},
        "lens_weights": {"dcf": 0.5},
    }
    new_cfg = {"company": "X", "stock_price": 100}

    captured = {}

    def upsert(row):
        captured["row"] = row
        return MagicMock(execute=lambda: None)

    fake_table = MagicMock()
    fake_table.upsert = upsert

    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    # Patch load_config to return our existing row
    orig_load = config_store.load_config
    config_store.load_config = lambda c, t, user_id=None: existing
    try:
        config_store.save_config(fake_client, "TEST", new_cfg, user_id="u1")
    finally:
        config_store.load_config = orig_load

    saved = captured["row"]["config"]
    assert saved["valuation_inputs"] == {"forward_eps": 5.0}
    assert saved["valuation_summary"] == {"weighted_fv_mid": 80.0}
    assert saved["lens_weights"] == {"dcf": 0.5}
    assert saved["ai_notes"] == {"foo": "bar"}
    assert saved["peers"] == [{"ticker": "P"}]


def test_save_config_recovers_explicit_null_or_empty_for_compute_only_keys():
    """For keys that are only ever populated by compute paths (not user
    intent), explicit None / empty dict triggers DB recovery.

    This caught the MSFT incident: some code path saved cfg with
    `valuation_summary: None`, and the original guard (which only checked
    `key not in cfg`) silently let the null overwrite the real summary.

    Note: `peers` and `lens_weights` are NOT in this group — empty values
    there represent intentional user actions (e.g. removing the last peer,
    reverting to default weights). See companion tests below.
    """
    import config_store

    existing = {
        "company": "X",
        "ai_notes": {"section": "real content"},
        "valuation_summary": {"weighted_fv_mid": 80.0},
    }
    # Explicit None / empty dict for compute-only keys — both trigger recovery
    bad_cfg = {
        "company": "X",
        "valuation_summary": None,
        "ai_notes": {},
    }

    captured = {}

    def upsert(row):
        captured["row"] = row
        return MagicMock(execute=lambda: None)

    fake_table = MagicMock()
    fake_table.upsert = upsert
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    orig_load = config_store.load_config
    config_store.load_config = lambda c, t, user_id=None: existing
    try:
        config_store.save_config(fake_client, "TEST", bad_cfg, user_id="u1")
    finally:
        config_store.load_config = orig_load

    saved = captured["row"]["config"]
    assert saved["valuation_summary"] == {"weighted_fv_mid": 80.0}
    assert saved["ai_notes"] == {"section": "real content"}


def test_save_config_allows_intentional_empty_peers():
    """Removing the last peer must persist as an empty list — the guard
    must NOT restore peers from the DB just because the new list is empty.

    Regression: Disney user couldn't remove the last peer "Ginny" because
    save_config saw `peers: []` and treated it as caller-forgot, restoring
    the old peers from DB on every save → user-deleted peer kept reappearing.
    """
    import config_store

    existing = {"company": "X", "peers": [{"ticker": "GINNY"}]}
    new_cfg = {"company": "X", "peers": []}  # explicit clear

    captured = {}

    def upsert(row):
        captured["row"] = row
        return MagicMock(execute=lambda: None)

    fake_table = MagicMock()
    fake_table.upsert = upsert
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    orig_load = config_store.load_config
    config_store.load_config = lambda c, t, user_id=None: existing
    try:
        config_store.save_config(fake_client, "TEST", new_cfg, user_id="u1")
    finally:
        config_store.load_config = orig_load

    assert captured["row"]["config"]["peers"] == []


def test_save_config_recovers_missing_peers_key():
    """If the caller's cfg omits the `peers` key entirely (key not in cfg),
    the guard must still restore peers from the DB. This preserves the
    original AI-Research-Section style protection against caller bugs."""
    import config_store

    existing = {"company": "X", "peers": [{"ticker": "AAPL"}]}
    new_cfg = {"company": "X"}  # peers key entirely missing

    captured = {}

    def upsert(row):
        captured["row"] = row
        return MagicMock(execute=lambda: None)

    fake_table = MagicMock()
    fake_table.upsert = upsert
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    orig_load = config_store.load_config
    config_store.load_config = lambda c, t, user_id=None: existing
    try:
        config_store.save_config(fake_client, "TEST", new_cfg, user_id="u1")
    finally:
        config_store.load_config = orig_load

    assert captured["row"]["config"]["peers"] == [{"ticker": "AAPL"}]


def test_save_config_allows_intentional_empty_lens_weights():
    """Setting `lens_weights = {}` reverts to default weights — must persist
    as empty dict, not get restored from DB."""
    import config_store

    existing = {"company": "X", "lens_weights": {"dcf": 0.7}}
    new_cfg = {"company": "X", "lens_weights": {}}  # explicit revert to defaults

    captured = {}

    def upsert(row):
        captured["row"] = row
        return MagicMock(execute=lambda: None)

    fake_table = MagicMock()
    fake_table.upsert = upsert
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    orig_load = config_store.load_config
    config_store.load_config = lambda c, t, user_id=None: existing
    try:
        config_store.save_config(fake_client, "TEST", new_cfg, user_id="u1")
    finally:
        config_store.load_config = orig_load

    assert captured["row"]["config"]["lens_weights"] == {}


# ---------------------------------------------------------------- valuation_lenses


import valuation_lenses


def test_dividend_lens_returns_none():
    assert valuation_lenses.compute_dividend_lens(make_cfg()) is None


def test_dcf_lens_basic_returns_band_around_intrinsic():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=False)
    base = lens["details"]["base_intrinsic"]
    assert lens["fv_mid"] == pytest.approx(base, rel=1e-9)
    assert lens["fv_low"] == pytest.approx(base * 0.85, rel=1e-9)
    assert lens["fv_high"] == pytest.approx(base * 1.15, rel=1e-9)
    assert lens["details"]["scenarios"] is None
    assert lens["details"]["wacc"] > 0


def test_dcf_lens_basic_intrinsic_positive_for_sample_cfg():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg)
    assert lens["fv_mid"] > 0
    assert lens["fv_low"] < lens["fv_mid"] < lens["fv_high"]


def test_dcf_lens_scenario_grid_uses_bull_bear_adjustments():
    cfg = make_cfg()
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=True)
    assert lens["details"]["scenarios"] is not None
    scenarios = lens["details"]["scenarios"]
    assert len(scenarios) == 16  # 4 growth offsets * 4 margin offsets
    base = lens["details"]["base_intrinsic"]
    assert lens["fv_mid"] == pytest.approx(base, rel=1e-9)
    assert lens["fv_low"] == min(scenarios)
    assert lens["fv_high"] == max(scenarios)
    assert lens["fv_low"] < lens["fv_high"]


def test_dcf_lens_scenario_grid_default_adjustments_when_missing():
    cfg = make_cfg()
    for key in ("bull_growth_adj", "bear_growth_adj",
                "bull_margin_adj", "bear_margin_adj"):
        cfg.pop(key, None)
    lens = valuation_lenses.compute_dcf_lens(cfg, scenario_grid=True)
    assert len(lens["details"]["scenarios"]) == 16


def test_reverse_dcf_lens_anchors_at_stock_price():
    cfg = make_cfg(stock_price=100.0)
    lens = valuation_lenses.compute_reverse_dcf_lens(cfg)
    assert lens["fv_low"] == 100.0
    assert lens["fv_mid"] == 100.0
    assert lens["fv_high"] == 100.0
    assert "implied_growth" in lens["details"]
    assert "implied_margin" in lens["details"]
    assert isinstance(lens["details"]["implied_growth"], float)


def test_multiples_lens_returns_none_when_no_inputs():
    cfg = make_cfg()  # no valuation_inputs, empty peers
    assert valuation_lenses.compute_multiples_lens(cfg) is None


def test_historical_lens_own_pe_only():
    cfg = make_cfg(
        valuation_inputs={"forward_eps": 5.0, "historical_fwd_pe": 20.0},
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    # Only own_pe anchor (5.0 * 20.0 = 100.0); no trailing_pe/ev_ebitda data
    assert lens["fv_mid"] == pytest.approx(100.0)
    assert lens["fv_low"] == pytest.approx(100.0)
    assert lens["fv_high"] == pytest.approx(100.0)
    assert lens["details"]["fwd_pe_own"] == pytest.approx(100.0)
    assert any("historical_trailing_pe" in s for s in lens["details"]["skipped"])
    assert any("historical_ev_ebitda" in s for s in lens["details"]["skipped"])


def test_multiples_lens_peer_pe_and_ev_ebitda():
    peers = [
        make_peer(ticker="P1", fwd_pe=18.0, ev_ebitda=10.0,
                  op_margin=0.18, rev_growth=0.04),
        make_peer(ticker="P2", fwd_pe=20.0, ev_ebitda=12.0,
                  op_margin=0.20, rev_growth=0.05),
        make_peer(ticker="P3", fwd_pe=22.0, ev_ebitda=14.0,
                  op_margin=0.22, rev_growth=0.06),
    ]
    cfg = make_cfg(
        peers=peers,
        valuation_inputs=dict(SAMPLE_VALUATION_INPUTS),
    )
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is not None
    # Median fwd_pe = 20.0, median ev_ebitda = 12.0, forward_eps=5.0
    expected_pe_median = 20.0 * 5.0  # = 100
    expected_ev_median = (12.0 * 12_000.0 - (10_000 - 5_000 - 0)) / 1_000  # = (144000-5000)/1000 = 139
    assert lens["details"]["fwd_pe_peer_median"] == pytest.approx(expected_pe_median)
    assert lens["details"]["ev_ebitda_peer_median"] == pytest.approx(expected_ev_median)
    # closest_peer must be one of the peer tickers
    assert lens["details"]["closest_peer"] in {"P1", "P2", "P3"}
    # fv range spans all anchors
    assert lens["fv_low"] < lens["fv_high"]
    assert lens["fv_low"] <= lens["fv_mid"] <= lens["fv_high"]


def test_multiples_lens_partial_inputs_skips_components():
    cfg = make_cfg(
        peers=[make_peer(fwd_pe=None, ev_ebitda=12.0)],
        valuation_inputs={"ttm_ebitda": 12_000.0},  # only ev/ebitda usable
    )
    lens = valuation_lenses.compute_multiples_lens(cfg)
    assert lens is not None
    assert lens["details"]["fwd_pe_peer_median"] is None
    assert lens["details"]["ev_ebitda_peer_median"] is not None


# ---------------------------------------------------------------- Tukey filter

def test_tukey_filter_drops_extreme_outlier():
    """One value far above the rest → dropped."""
    kept, removed = valuation_lenses._tukey_filter([10, 12, 14, 16, 18, 100])
    assert 100 not in kept
    assert removed == [5]


def test_tukey_filter_preserves_normal_distribution():
    """Tightly clustered values → no removal."""
    kept, removed = valuation_lenses._tukey_filter([10, 12, 14, 16, 18])
    assert kept == [10, 12, 14, 16, 18]
    assert removed == []


def test_tukey_filter_too_few_points_no_op():
    """Fewer than 4 values → no filtering (insufficient data for IQR)."""
    kept, removed = valuation_lenses._tukey_filter([10, 100, 1000])
    assert kept == [10, 100, 1000]
    assert removed == []


def test_tukey_filter_falls_back_when_too_aggressive():
    """If filtering would leave < 2 values, return original list."""
    # Wildly dispersed — every value would be tagged outlier; fallback engages
    kept, _removed = valuation_lenses._tukey_filter([1, 50, 100, 10_000])
    # Either no filtering (fallback) OR ≥ 2 kept
    assert len(kept) >= 2


def test_multiples_lens_drops_outlier_peer_fwd_pe():
    """Outlier peer fwd_pe is excluded; ticker recorded in details.

    Tukey filtering needs ~5 non-outlier values to detect a single outlier
    reliably (otherwise the outlier itself contaminates Q3). Our typical
    peer set is 6 peers — match that here.
    """
    peers = [
        make_peer(ticker="P1", fwd_pe=18.0, ev_ebitda=10.0),
        make_peer(ticker="P2", fwd_pe=19.0, ev_ebitda=11.0),
        make_peer(ticker="P3", fwd_pe=20.0, ev_ebitda=12.0),
        make_peer(ticker="P4", fwd_pe=21.0, ev_ebitda=13.0),
        make_peer(ticker="P5", fwd_pe=22.0, ev_ebitda=14.0),
        make_peer(ticker="OUTLIER", fwd_pe=200.0, ev_ebitda=15.0),
    ]
    cfg = make_cfg(
        peers=peers,
        valuation_inputs=dict(SAMPLE_VALUATION_INPUTS),
    )
    lens = valuation_lenses.compute_multiples_lens(cfg)
    # OUTLIER's fwd_pe (200) is removed → reflected in details
    assert "OUTLIER" in lens["details"]["peer_fwd_pe_outliers_removed"]
    # And the peer median didn't get pulled up by it
    expected_median_without_outlier = 20.0  # median of [18, 19, 20, 21, 22]
    assert lens["details"]["fwd_pe_peer_median"] == pytest.approx(
        expected_median_without_outlier * SAMPLE_VALUATION_INPUTS["forward_eps"]
    )


def test_dcf_only_fallback_when_no_valuation_inputs():
    """Acceptance #1: config without valuation_inputs → DCF-only summary,
    weights renormalized to 1.0.

    Note: reverse_dcf now has weight 0.0 by default (anchors at stock price
    by construction), so it drops out of the weighted FV calculation. The
    lens is still computed and stored, but doesn't contribute to weighted_fv_mid.
    """
    cfg = make_cfg()  # no inputs, no peers with multiples
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)
    lenses = summary["lenses"]
    assert lenses["dcf"] is not None
    assert lenses["multiples"] is None
    assert lenses["reverse_dcf"] is not None  # always computed, but...
    assert lenses["reverse_dcf"]["weight_normalized"] == 0.0  # ...has zero weight
    # With reverse_dcf weight 0, only DCF contributes → weighted_fv ≈ dcf_fv
    # (rounded to 2 decimals in the summary)
    assert summary["weighted_fv_mid"] == pytest.approx(lenses["dcf"]["fv_mid"], abs=0.01)
    assert lenses["dcf"]["weight_normalized"] == pytest.approx(1.0)


def test_all_lenses_active_weighted_in_range():
    """Acceptance #2: full config → 4 active lenses, weighted FV in [min,max]
    of individual lens mids."""
    peers = [
        make_peer(ticker="P1", fwd_pe=18.0, ev_ebitda=10.0),
        make_peer(ticker="P2", fwd_pe=20.0, ev_ebitda=12.0),
        make_peer(ticker="P3", fwd_pe=22.0, ev_ebitda=14.0),
    ]
    cfg = make_cfg(
        peers=peers,
        valuation_inputs={
            **dict(SAMPLE_VALUATION_INPUTS),
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,
        },
    )
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)
    lenses = summary["lenses"]
    active = [n for n in ("dcf", "multiples", "historical", "reverse_dcf") if lenses[n] is not None]
    assert active == ["dcf", "multiples", "historical", "reverse_dcf"]

    mids = [lenses[n]["fv_mid"] for n in active]
    assert min(mids) <= summary["weighted_fv_mid"] <= max(mids)
    assert summary["buy_price"] == pytest.approx(
        summary["weighted_fv_mid"] * (1 - cfg["margin_of_safety"]), abs=0.01
    )
    # current_vs_mid signed correctly
    expected_cvm = (cfg["stock_price"] - summary["weighted_fv_mid"]) / summary["weighted_fv_mid"]
    assert summary["current_vs_mid"] == pytest.approx(expected_cvm, rel=1e-3)
    # weights sum to 1.0
    total_norm = sum(lenses[n]["weight_normalized"] for n in active)
    assert total_norm == pytest.approx(1.0)
    # dividend lens stays None
    assert lenses["dividend"] is None


def test_lens_weights_override_from_config():
    cfg = make_cfg(
        peers=[make_peer(fwd_pe=20.0, ev_ebitda=12.0)],
        valuation_inputs=dict(SAMPLE_VALUATION_INPUTS),
        lens_weights={"dcf": 0.5, "multiples": 0.5, "historical": 0.0, "reverse_dcf": 0.0, "dividend": 0.0},
    )
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)
    # reverse_dcf has weight 0 → normalized 0 → drops out of weighted FV
    assert summary["lenses"]["reverse_dcf"]["weight_normalized"] == 0.0
    assert summary["lenses"]["dcf"]["weight_normalized"] == pytest.approx(0.5)
    assert summary["lenses"]["multiples"]["weight_normalized"] == pytest.approx(0.5)


def test_list_watchlist_enriched_shape():
    """Acceptance #3: list_watchlist returns dicts with all new fields,
    None when missing rather than absent."""
    import config_store

    summary = {
        "weighted_fv_low": 60.0,
        "weighted_fv_mid": 80.0,
        "weighted_fv_high": 100.0,
        "buy_price": 64.0,
        "current_vs_mid": 0.10,
        "lenses": {"dcf": {}, "multiples": {}, "historical": {}, "reverse_dcf": {}, "dividend": None},
    }
    rows = [
        {
            "ticker": "WITH",
            "company": "With Co",
            "stock_price": 90.0,
            "updated_at": "2026-05-05T00:00:00Z",
            "config": {
                "valuation_summary": summary,
                "ai_notes": {
                    "Scorecard": '```json\n{"verdict":"deep_dive","phase":{"number":3,"name":"S"}}\n```'
                },
            },
        },
        {
            "ticker": "WITHOUT",
            "company": "Without Co",
            "stock_price": 50.0,
            "updated_at": "2026-05-05T00:00:00Z",
            "config": {},  # no valuation_summary, no ai_notes
        },
    ]

    fake_resp = MagicMock(data=rows)
    fake_query = MagicMock()
    fake_query.eq.return_value = fake_query
    fake_query.execute.return_value = fake_resp
    fake_table = MagicMock()
    fake_table.select.return_value = fake_query
    fake_client = MagicMock()
    fake_client.table.return_value = fake_table

    out = config_store.list_watchlist(fake_client, user_id="u1")
    expected_keys = {
        "ticker", "company", "updated", "stock_price",
        "fv_low", "fv_mid", "fv_high", "buy_price",
        "current_vs_mid", "lens_count", "verdict", "phase",
    }
    for row in out:
        assert set(row.keys()) == expected_keys

    with_row = next(r for r in out if r["ticker"] == "WITH")
    assert with_row["fv_mid"] == 80.0
    assert with_row["fv_low"] == 60.0
    assert with_row["fv_high"] == 100.0
    assert with_row["buy_price"] == 64.0
    assert with_row["current_vs_mid"] == 0.10
    assert with_row["lens_count"] == 3  # dcf + multiples + historical (reverse_dcf and dividend excluded from count)
    assert with_row["verdict"] == "deep_dive"
    assert with_row["phase"] == 3

    without_row = next(r for r in out if r["ticker"] == "WITHOUT")
    assert without_row["fv_mid"] is None
    assert without_row["fv_low"] is None
    assert without_row["fv_high"] is None
    assert without_row["buy_price"] is None
    assert without_row["current_vs_mid"] is None
    assert without_row["lens_count"] == 0
    assert without_row["verdict"] is None
    assert without_row["phase"] is None


def test_round_trip_calculate_and_persist(monkeypatch):
    """Acceptance #4: compute → save → list shows the same fv_mid."""
    import mcp_server

    # In-memory "Supabase": one config row keyed by ticker
    storage = {
        "TEST": {
            "company": "Test Co",
            "ticker": "TEST",
            "stock_price": 100.0,
            "ai_notes": {},
            "peers": [],
            **make_cfg(),
        },
    }

    def fake_load(client, ticker, user_id=None):
        return dict(storage[ticker.upper()])

    def fake_save(client, ticker, cfg, user_id=None):
        storage[ticker.upper()] = dict(cfg)

    def fake_list(client, user_id=None):
        out = []
        from scorecard_utils import parse_scorecard
        for tk, cfg in storage.items():
            summary = cfg.get("valuation_summary") or {}
            lenses = summary.get("lenses") or {}
            lens_count = sum(1 for v in lenses.values() if v is not None)
            sc = parse_scorecard(cfg.get("ai_notes"))
            out.append({
                "ticker": tk,
                "company": cfg.get("company", tk),
                "updated": "",
                "stock_price": cfg.get("stock_price", 0),
                "fv_low": summary.get("weighted_fv_low"),
                "fv_mid": summary.get("weighted_fv_mid"),
                "fv_high": summary.get("weighted_fv_high"),
                "buy_price": summary.get("buy_price"),
                "current_vs_mid": summary.get("current_vs_mid"),
                "lens_count": lens_count,
                "verdict": sc["verdict"],
                "phase": sc["phase"],
            })
        return out

    monkeypatch.setattr(mcp_server, "get_supabase_client", lambda: MagicMock())
    monkeypatch.setattr(mcp_server.config_store, "load_config", fake_load)
    monkeypatch.setattr(mcp_server.config_store, "save_config", fake_save)
    monkeypatch.setattr(mcp_server.config_store, "list_watchlist", fake_list)
    monkeypatch.setattr(mcp_server, "USER_ID", "u1")

    import json as _json
    result_json = mcp_server._calculate_multi_lens_valuation_impl("TEST", scenario_grid=False)
    result = _json.loads(result_json)
    expected_mid = result["weighted_fv_mid"]

    # round trip via list_watchlist
    listed = _json.loads(mcp_server._get_watchlist_impl())
    test_row = next(r for r in listed if r["ticker"] == "TEST")
    assert test_row["fv_mid"] == expected_mid
    assert test_row["lens_count"] >= 1


def test_calculate_valuation_impl_shape_unchanged():
    """Acceptance #5: existing single-DCF calculate_valuation() output
    keys/shape unchanged by this change."""
    import json as _json
    import mcp_server

    cfg = make_cfg()
    out = _json.loads(mcp_server._calculate_valuation_impl(cfg))
    expected_keys = {
        "wacc", "intrinsic_value", "buy_price", "enterprise_value",
        "equity_value", "tv_pct", "implied_growth", "implied_margin",
        "market_price", "valuation_basis",
    }
    # closest_growth/margin are added when reverse closest is found — optional
    assert expected_keys.issubset(set(out.keys()))
    assert isinstance(out["wacc"], float)
    assert isinstance(out["intrinsic_value"], float)
    assert out["valuation_basis"] == "nominal"


def test_historical_lens_uses_historical_trailing_pe():
    """Sub-anchor A.2: historical_trailing_pe × ttm_eps contributes to fv_anchors."""
    cfg = make_cfg(
        valuation_inputs={
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,
            # Other inputs missing → A.2 is the only sub-anchor that fires
        },
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    # 25.0 * 4.0 = 100.0
    assert lens["details"]["historical_trailing_pe_fv"] == pytest.approx(100.0)
    # Single anchor → low/mid/high all equal
    assert lens["fv_low"] == pytest.approx(100.0)
    assert lens["fv_mid"] == pytest.approx(100.0)
    assert lens["fv_high"] == pytest.approx(100.0)


def test_historical_lens_uses_historical_ev_ebitda():
    """Sub-anchor D: historical_ev_ebitda × ttm_ebitda - net_debt → /shares."""
    cfg = make_cfg(
        valuation_inputs={
            "historical_ev_ebitda": 15.0,
            "ttm_ebitda": 10_000.0,  # in $M
            # ttm_eps missing → A.2 doesn't fire
        },
    )
    # net_debt = debt(10_000) - cash(5_000) - securities(0) = 5_000
    # ev = 15.0 * 10_000 = 150_000  (in $M)
    # equity = ev - net_debt = 145_000  (in $M)
    # per share = 145_000 / 1_000 shares_outstanding = 145.0
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    assert lens["details"]["historical_ev_ebitda_fv"] == pytest.approx(145.0)


def test_historical_lens_uses_all_three_subanchors():
    """All inputs present → 3 anchors collected (A + A.2 + D)."""
    cfg = make_cfg(
        valuation_inputs={
            "forward_eps": 5.0,
            "historical_fwd_pe": 20.0,             # → A: 100
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,                         # → A.2: 100
            "historical_ev_ebitda": 15.0,
            "ttm_ebitda": 10_000.0,                 # → D: depends on net_debt + shares
        },
    )
    # net_debt = 10_000 - 5_000 - 0 = 5_000
    # D fv = (15.0 * 10_000 - 5_000) / 1_000 = 145.0
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    assert lens["details"]["fwd_pe_own"] == pytest.approx(100.0)
    assert lens["details"]["historical_trailing_pe_fv"] == pytest.approx(100.0)
    assert lens["details"]["historical_ev_ebitda_fv"] == pytest.approx(145.0)
    # 3 anchors collected: 100, 100, 145
    assert lens["fv_low"] == pytest.approx(100.0)
    assert lens["fv_high"] == pytest.approx(145.0)
    # Mid is the mean: (100 + 100 + 145) / 3 = 115
    assert lens["fv_mid"] == pytest.approx(115.0, abs=0.5)


def test_historical_lens_returns_none_when_no_inputs():
    """Empty valuation_inputs → all three sub-anchors skip → lens returns None."""
    cfg = make_cfg()  # default has empty valuation_inputs
    assert valuation_lenses.compute_historical_lens(cfg) is None


def test_historical_lens_only_a2_active():
    """Only A.2 inputs present → lens returns single-anchor result."""
    cfg = make_cfg(
        valuation_inputs={"historical_trailing_pe": 30.0, "ttm_eps": 5.0},
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    assert lens["details"]["fwd_pe_own"] is None
    assert lens["details"]["historical_trailing_pe_fv"] == pytest.approx(150.0)
    assert lens["details"]["historical_ev_ebitda_fv"] is None
    # Single anchor → fv_low == fv_mid == fv_high
    assert lens["fv_low"] == lens["fv_mid"] == lens["fv_high"] == pytest.approx(150.0)


def test_historical_lens_only_d_active():
    """Only D (own EV/EBITDA) inputs present → lens returns single-anchor."""
    cfg = make_cfg(
        valuation_inputs={"historical_ev_ebitda": 20.0, "ttm_ebitda": 5_000.0},
    )
    lens = valuation_lenses.compute_historical_lens(cfg)
    assert lens is not None
    # net_debt = 5_000, shares = 1_000 (defaults)
    # fv = (20.0 * 5_000 - 5_000) / 1_000 = 95.0
    assert lens["details"]["historical_ev_ebitda_fv"] == pytest.approx(95.0)
    assert lens["fv_mid"] == pytest.approx(95.0)


def test_orchestrator_includes_historical_lens():
    """Full config produces a valuation_summary with 4 active lenses
    (dcf, multiples, historical, reverse_dcf), dividend stays None."""
    peers = [
        make_peer(ticker="P1", fwd_pe=18.0, ev_ebitda=10.0),
        make_peer(ticker="P2", fwd_pe=20.0, ev_ebitda=12.0),
        make_peer(ticker="P3", fwd_pe=22.0, ev_ebitda=14.0),
    ]
    cfg = make_cfg(
        peers=peers,
        valuation_inputs={
            **dict(SAMPLE_VALUATION_INPUTS),
            "historical_trailing_pe": 25.0,
            "ttm_eps": 4.0,
            "historical_ev_ebitda": 15.0,
        },
    )
    summary = valuation_lenses.calculate_multi_lens_valuation(cfg)

    lenses = summary["lenses"]
    assert lenses["dcf"] is not None
    assert lenses["multiples"] is not None
    assert lenses["historical"] is not None
    assert lenses["reverse_dcf"] is not None
    assert lenses["dividend"] is None  # Phase 2-C stub


def test_default_lens_weights_post_split():
    assert valuation_lenses.DEFAULT_LENS_WEIGHTS == {
        "dcf":         0.50,
        "multiples":   0.25,
        "historical":  0.25,
        "reverse_dcf": 0.0,
        "dividend":    0.00,
    }
