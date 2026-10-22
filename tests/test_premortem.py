from unittest.mock import patch

import mcp_server
import premortem as pm

NEW = {
    "status": "cracks", "status_note": "Thesis intact; waiting-list buffer weakening.",
    "action": "hold",
    "sell": [{"label": "Scarcity breaks", "when": "Birkin 25 resale premium below 1.3x",
              "detail": "Or pristine quota bags below 1.5x.", "state": "near"},
             {"label": "Capital allocation", "when": "Large debt-funded acquisition",
              "state": "triggered", "date": "2026-09-11"}],
    "add": [{"label": "Akre test", "when": "FCF yield + FCF CAGR >= 9% (price <= EUR 1,170)"}],
    "ignore": ["Quarterly misses of a few percent"],
    "discipline": ["Recheck the threshold once a year, never mid-year"],
    "log": [{"date": "2026-10-22", "text": "Q3 +4.3% cc, miss vs 5.2%"},
            {"date": "2026-09-15", "text": "Akre test defined"}],
}


def test_new_shape_renders_status_triggers_and_updates():
    from datetime import date
    html = pm.tab_html(NEW, today=date(2026, 10, 22))
    assert "Cracks" in html and "Action: hold" in html
    assert "Triggered:</b> Capital allocation" in html
    assert ">Near<" in html and "<summary>More</summary>" in html
    assert html.index("22 Oct 2026") < html.index("15 Sep 2026")    # newest first
    assert "today" in html and "5 weeks ago" in html and "<em>Latest</em>" in html
    assert "Discipline (1)" in html


def test_legacy_strings_are_split_into_head_condition_and_detail():
    long = ("THESIS-BREAK (exit): (1) Birkin 25 resale-premie < 1,3x OF quota-bags < 1,5x. "
            "OF (2) recurring marge aanhoudend < 38%, OF (3) China in meerjarige krimp.")
    t = pm.normalize_trigger(long)
    assert t["label"] == "THESIS-BREAK (exit)"
    assert len(t["when"]) <= pm.MAX_WHEN and t["detail"]
    fired = pm.normalize_trigger("GETRIGGERD (11 sep 2026) — KAPITAALALLOCATIE: ACV-overname")
    assert fired["state"] == "triggered" and fired["label"] == "KAPITAALALLOCATIE"


def test_legacy_current_becomes_the_oldest_update():
    got = pm.normalize({"current": "Long journal text", "sell": ["A: b"],
                        "log": [{"date": "2026-10-22", "text": "new"}]})
    assert got["log"][-1] == {"date": "", "text": "Long journal text"}
    assert got["log"][0]["text"] == "new"


def test_validation_limits():
    bad = dict(NEW, action="panic",
               sell=[{"label": "x" * 41, "when": "y" * 161}, "a plain string"],
               discipline=["r"] * 9)
    errs = " | ".join(pm.validation_errors(bad))
    for part in ("action must be", "label over 40", "when over 160",
                 "not a string", "at most 8 discipline"):
        assert part in errs, part
    assert pm.validation_errors(NEW) == []


def test_position_from_portfolio_sums_brokers():
    data = {"RMS_t212": {"symbol": "RMS", "shares_held": 0.5, "current_price": 1400.0,
                         "trades": []},
            "RMS_tt": {"symbol": "RMS.PA", "shares_held": 0.3, "current_price": 1400.0,
                       "trades": []},
            "META": {"symbol": "META", "shares_held": 3, "current_price": 700.0}}
    got = pm.position_from_portfolio("RMS.PA", data, lambda trades: (800.0, 0.5))
    assert got["shares"] == 0.8 and got["price"] == 1400.0
    assert pm.position_from_portfolio("NVDA", data, lambda t: (0, 0)) is None
    assert pm.position_from_portfolio("META", None, lambda t: (0, 0)) is None


def test_summary_strip():
    html = pm.summary_strip_html(NEW)
    assert "Cracks" in html and "2 sell · 1 add triggers" in html and "1 triggered" in html
    assert pm.summary_strip_html(None) is None


def _set(stored, **kw):
    saved = {}
    with patch.object(mcp_server, "get_supabase_client", return_value=object()), \
            patch.object(mcp_server.config_store, "load_config", return_value=stored), \
            patch.object(mcp_server.config_store, "save_config",
                         side_effect=lambda c, t, cfg, user_id=None: saved.update(cfg)):
        out = mcp_server._set_premortem_impl("RMS.PA", user_id="u1", **kw)
    return out, saved.get("premortem")


def test_set_premortem_appends_the_log_and_refuses_essays():
    stored = {"premortem": {"log": [{"date": "2026-09-15", "text": "old"}]}}
    out, saved = _set(stored, status="intact", action="hold",
                      sell=[{"label": "L", "when": "W"}], log_entry="Q3 in line")
    assert out == "Saved pre-mortem for RMS.PA."
    assert [e["text"] for e in saved["log"]] == ["Q3 in line", "old"]
    out, saved = _set({}, status="intact", sell=[{"label": "L", "when": "w" * 200}])
    assert "when over 160" in out["error"] and saved is None


def test_set_premortem_still_accepts_old_style_calls():
    out, saved = _set({}, current="spot $720 → hold", sell=["A long legacy trigger"])
    assert out.startswith("Saved") and saved["sell"] == ["A long legacy trigger"]


def test_an_abbreviation_does_not_end_the_condition():
    t = pm.normalize_trigger("Each add: (1) test >= 9%, (2) moat check green incl. resale check, "
                             "(3) room under the cap. Then the rest.")
    assert "incl. resale check" in t["when"] and t["detail"] == "Then the rest."


def test_timeline_wording():
    from datetime import date
    t = date(2026, 10, 22)
    assert [pm._ago(d, t) for d in (date(2026, 10, 22), date(2026, 10, 21), date(2026, 10, 15),
                                    date(2026, 7, 22), date(2024, 10, 1))] == [
        "today", "yesterday", "7 days ago", "3 months ago", "2 years ago"]
    assert "Earlier note" in pm._event({"date": "", "text": "x"}, t)
