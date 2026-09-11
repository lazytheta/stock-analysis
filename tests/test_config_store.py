"""Tests for config_store.load_config transient-retry behaviour."""
import pytest

import config_store


class _FakeQuery:
    def __init__(self, behavior):
        self._behavior = behavior

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return self._behavior()


class _FakeClient:
    def __init__(self, behavior):
        self._behavior = behavior

    def table(self, *a, **k):
        return _FakeQuery(self._behavior)


class _Resp:
    def __init__(self, config):
        self.data = {"config": config}


def test_load_config_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(config_store.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    class RemoteProtocolError(Exception):
        pass

    def behavior():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RemoteProtocolError("Server disconnected without sending a response.")
        return _Resp({"ticker": "X", "company": "Acme"})

    cfg = config_store.load_config(_FakeClient(behavior), "x")
    assert cfg["company"] == "Acme"
    assert calls["n"] == 2  # one retry


def test_load_config_pgrst116_returns_none_without_retry():
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        raise Exception("PGRST116: 0 rows returned")

    assert config_store.load_config(_FakeClient(behavior), "x") is None
    assert calls["n"] == 1  # no retry for a legitimate 0-rows


def test_load_config_persistent_transient_raises_after_retries(monkeypatch):
    monkeypatch.setattr(config_store.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        raise Exception("RemoteProtocolError: Server disconnected")

    with pytest.raises(Exception, match="RemoteProtocolError"):
        config_store.load_config(_FakeClient(behavior), "x")
    assert calls["n"] == 3  # 3 attempts total


def test_load_config_non_transient_raises_immediately():
    calls = {"n": 0}

    def behavior():
        calls["n"] += 1
        raise ValueError("schema mismatch")

    with pytest.raises(ValueError):
        config_store.load_config(_FakeClient(behavior), "x")
    assert calls["n"] == 1  # no retry for a non-transient error


# ── save_config merges: omitting a key must not delete it ──────────────────────

class _CapturingClient:
    """Records what save_config upserts, and serves a stored config to the
    load_config lookup that the merge performs."""

    def __init__(self, existing):
        self.existing = existing
        self.saved = None
        self._t = self

    def table(self, *a, **k):
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def maybe_single(self):
        return self

    def upsert(self, payload, *a, **k):
        self.saved = payload
        return self

    def execute(self):
        if self.saved is not None:
            return None
        return _Resp(self.existing)


def _save(existing, incoming):
    c = _CapturingClient(existing)
    config_store.save_config(c, "TEST", incoming, user_id="u")
    return c.saved["config"]


def test_save_config_preserves_keys_the_caller_omitted():
    """A partial save must not wipe the rest of the config. Every field is a
    deliberate input, and a caller that sends only the two it changed used to
    silently delete the other forty — the recurring 'my values changed by
    themselves' complaint."""
    existing = {"margin_of_safety": 0.275, "sector_betas": [["Shoe", 1.14, 1.0]],
                "cash_bridge": 1907, "terminal_growth": 0.03}
    out = _save(existing, {"margin_of_safety": 0.27})

    assert out["margin_of_safety"] == 0.27          # the caller's change wins
    assert out["sector_betas"] == [["Shoe", 1.14, 1.0]]   # untouched, preserved
    assert out["cash_bridge"] == 1907
    assert out["terminal_growth"] == 0.03


def test_save_config_lets_the_caller_overwrite_with_a_falsy_value():
    """Preserving must not block a deliberate 0 / False / empty string."""
    out = _save({"minority_interest": 500}, {"minority_interest": 0})
    assert out["minority_interest"] == 0


def test_save_config_still_allows_deleting_the_derived_wacc_cache():
    """wacc_per_year / terminal_wacc are persisted only when overridden; the
    editor pops them so they fall back to a live compute. Merging them back
    would resurrect the frozen-WACC drift fixed in 2026-07."""
    existing = {"wacc_per_year": [0.0798] * 10, "terminal_wacc": 0.0798,
                "roce_metric_override": "roe", "cash_bridge": 1907}
    out = _save(existing, {"cash_bridge": 1907})

    assert "wacc_per_year" not in out
    assert "terminal_wacc" not in out
    assert "roce_metric_override" not in out
    assert out["cash_bridge"] == 1907


def test_save_config_writes_a_brand_new_ticker_unchanged():
    c = _CapturingClient(None)
    config_store.save_config(c, "NEW", {"cash_bridge": 10}, user_id="u")
    assert c.saved["config"] == {"cash_bridge": 10}


# ── Bulk loading: one round-trip instead of one per ticker ─────────────────────

class _BulkClient:
    """Counts how many queries the caller issues."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def table(self, *a, **k):
        return self

    def select(self, *a, **k):
        self.queries += 1
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        class _R:
            data = self.rows
        return _R()


def test_load_all_configs_issues_a_single_query():
    """The watchlist called load_config once per ticker — 64 round-trips for
    2.5 MB that one query returns. Latency, not payload, was the cost."""
    rows = [{"ticker": "AAPL", "config": {"company": "Apple"}},
            {"ticker": "MSFT", "config": {"company": "Microsoft"}}]
    c = _BulkClient(rows)
    out = config_store.load_all_configs(c, user_id="u")

    assert c.queries == 1
    assert set(out) == {"AAPL", "MSFT"}
    assert out["AAPL"]["company"] == "Apple"


def test_load_all_configs_restores_tuples_like_load_config():
    """Must return configs shaped exactly as load_config does — sector_betas
    come back as tuples, not lists, or the DCF unpacking breaks."""
    rows = [{"ticker": "T", "config": {"sector_betas": [["Telecom", 0.39, 1.0]],
                                       "debt_breakdown": [["Long-Term Debt", 100]]}}]
    out = config_store.load_all_configs(_BulkClient(rows), user_id="u")
    assert out["T"]["sector_betas"] == [("Telecom", 0.39, 1.0)]


def test_load_all_configs_skips_rows_without_a_config():
    rows = [{"ticker": "AAPL", "config": {"company": "Apple"}},
            {"ticker": "BAD", "config": None}]
    out = config_store.load_all_configs(_BulkClient(rows), user_id="u")
    assert set(out) == {"AAPL"}


# ── list_watchlist selects only the fields it renders ─────────────────────────

class _SelectCapturingClient:
    def __init__(self, rows):
        self.rows, self.select_arg = rows, None

    def table(self, *a, **k):
        return self

    def select(self, arg, *a, **k):
        self.select_arg = arg
        return self

    def eq(self, *a, **k):
        return self

    def execute(self):
        class _R:
            data = self.rows
        return _R()


def test_list_watchlist_does_not_pull_the_whole_config():
    """ai_notes is 80% of the config payload (2.0 MB of 2.5 MB across the
    watchlist) and list_watchlist reads exactly one key from it — the Scorecard
    — to resolve a verdict. Selecting the whole column made the listing carry
    every prescan section on every load."""
    c = _SelectCapturingClient([])
    config_store.list_watchlist(c, user_id="u")

    assert "config->ai_notes->Scorecard" in c.select_arg
    assert "config->valuation_summary" in c.select_arg
    # the whole column must not be requested
    assert not any(part.strip() == "config" for part in c.select_arg.split(","))


def test_list_watchlist_builds_rows_from_the_narrow_selection():
    rows = [{
        "ticker": "AAPL", "company": "Apple", "stock_price": 200.0,
        "updated_at": "2026-08-07",
        "valuation_summary": {"weighted_fv_mid": 180.0, "buy_price": 144.0,
                              "weighted_fv_low": 160.0, "weighted_fv_high": 200.0,
                              "current_vs_mid": 0.11,
                              "lenses": {"dcf": {"fv_mid": 180.0}, "multiples": None}},
        "robustness": {"verdict_mapped": "deep_dive"},
        "Scorecard": '```json\n{"phase": {"number": 5, "name": "Capital Return"}}\n```',
    }]
    out = config_store.list_watchlist(_SelectCapturingClient(rows), user_id="u")

    assert len(out) == 1
    e = out[0]
    assert e["ticker"] == "AAPL"
    assert e["fv_mid"] == 180.0
    assert e["buy_price"] == 144.0
    assert e["lens_count"] == 1          # only dcf is non-null
    assert e["verdict"] == "deep_dive"   # robustness wins over the scorecard
    assert e["phase"] == 5


def test_list_watchlist_tolerates_a_row_with_nothing_computed():
    rows = [{"ticker": "NEW", "company": "New Co", "stock_price": 10.0,
             "updated_at": "", "valuation_summary": None,
             "robustness": None, "Scorecard": None}]
    out = config_store.list_watchlist(_SelectCapturingClient(rows), user_id="u")
    assert out[0]["ticker"] == "NEW"
    assert out[0]["fv_mid"] is None
    assert out[0]["lens_count"] == 0


def test_list_watchlist_carries_the_isin_for_a_second_quote_source():
    """Deutsche Boerse werkt op ISIN, niet op ticker. Zonder dit veld is er
    voor de Europese lijnen geen alternatief als Yahoo ze niet levert."""
    c = _SelectCapturingClient([
        {"ticker": "RMS.PA", "company": "Hermes", "stock_price": 1613.5,
         "updated_at": "", "valuation_summary": None, "robustness": None,
         "Scorecard": None, "isin": "FR0000052292", "quote_venue": "XETR"}])
    out = config_store.list_watchlist(c, user_id="u")
    assert "config->isin" in c.select_arg
    assert out[0]["isin"] == "FR0000052292"
    assert out[0]["quote_venue"] == "XETR"


def test_list_watchlist_leaves_the_isin_empty_when_there_is_none():
    c = _SelectCapturingClient([
        {"ticker": "MSFT", "company": "Microsoft", "stock_price": 451.1,
         "updated_at": "", "valuation_summary": None, "robustness": None,
         "Scorecard": None}])
    assert config_store.list_watchlist(c, user_id="u")[0]["isin"] is None


class _LoadRaisesClient(_CapturingClient):
    """De merge-read faalt met een echte fout (geen 'geen rij')."""

    def execute(self):
        if self.saved is not None:
            return None
        raise ConnectionError("Server disconnected mid-read")


def test_a_failed_merge_read_refuses_to_save_instead_of_wiping():
    """save_config leest de bestaande config om onaangeroerde velden terug te
    mergen. Elke fout daarin werd 'existing = None', waarna de merge werd
    overgeslagen en upsert de hele config verving door de drie velden die de
    aanroeper meestuurde. Eén Supabase-hik = veertig velden weg, onomkeerbaar.

    'Geen rij' meldt load_config zelf al als None (PGRST116 / '0 rows'), dus
    een exceptie hier is altijd een storing -- en een storing mag niet als
    'nieuwe ticker' lezen."""
    c = _LoadRaisesClient({"margin_of_safety": 0.275, "cash_bridge": 1907})
    with pytest.raises(ConnectionError):
        config_store.save_config(c, "TEST", {"margin_of_safety": 0.27}, user_id="u")
    assert c.saved is None, "er is geüpsert terwijl de merge-read faalde"


class _NoRowClient(_CapturingClient):
    """maybe_single() zonder treffer: data is None, geen rij met config None."""

    def execute(self):
        if self.saved is not None:
            return None

        class _Empty:
            data = None
        return _Empty()


def test_a_genuinely_new_ticker_still_saves():
    """De tegenproef: load_config geeft None voor een ticker die er niet is,
    en dat pad moet blijven werken."""
    c = _NoRowClient(None)
    config_store.save_config(c, "NEW", {"margin_of_safety": 0.3}, user_id="u")
    assert c.saved["config"]["margin_of_safety"] == 0.3
