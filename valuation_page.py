"""Valuation tab: the buy price for this company's type, and why.

Built from a type_valuation.evaluate() result (Notion: "Waarderingsmethode per
type bedrijf", v0.2). The page lays itself out after the type: only the cards
that type uses appear, and a missing input is one line, never a row of dashes.

House style as capital_return: question_cards.section_html sections with flat
inner cards, every dynamic string through question_cards.esc, never raises.
"""

from __future__ import annotations

import question_cards as qc
import type_valuation as tv

DASH = "—"

STYLE = """<style>
.vp-hero{display:flex;flex-wrap:wrap;gap:28px;align-items:baseline}
.vp-verdict{font-size:26px;font-weight:700}
.vp-fig{font-size:13px;color:var(--text-muted)}
.vp-fig b{display:block;font-size:20px;color:var(--text);font-weight:700}
.vp-line{margin-top:10px;font-size:14px;color:var(--text)}
.vp-rows{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 24px;font-size:14px}
.vp-rows span:nth-child(odd){color:var(--text-muted)}
.vp-rows span:nth-child(even){text-align:right;font-weight:600;white-space:nowrap}
.vp-note{font-size:13px;color:var(--text-muted);margin-top:8px}
.vp-details summary{cursor:pointer;font-size:14px;font-weight:600;margin:6px 0}
.vp-details p{font-size:14px;white-space:pre-wrap;margin:4px 0 0}
</style>"""


def _money(x, sym="$"):
    return DASH if x is None else f"{sym}{x:,.2f}"


def _pct(x, signed=False):
    if x is None:
        return DASH
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def _mult(x):
    return DASH if x is None else f"{x:.1f}×"


def _rows(pairs):
    cells = "".join(f"<span>{qc.esc(k)}</span><span>{qc.esc(v)}</span>" for k, v in pairs)
    return f'<div class="vp-rows">{cells}</div>'


def one_line(res, sym="$"):
    """The sentence under the verdict: what the buy price rests on."""
    m = res.get("main") or {}
    kind = res.get("type")
    if kind == "capital_light":
        req, g = m.get("required_growth"), m.get("achievable_growth")
        if req is not None and g is not None:
            return (f"The price asks {req:.1%} EPS growth a year; achievable is "
                    f"{g:.1%}, minus a {tv.BUFFER:.0%} buffer.")
    if kind == "capital_heavy":
        return (f"DCF at the {tv.HURDLE:.0%} hurdle with the model's growth "
                f"{tv.BUFFER:.0%} lower each year, no further discount.")
    if kind == "cyclical" and m.get("normalised_eps") is not None:
        return (f"Normalised EPS {_money(m['normalised_eps'], sym)} "
                f"({m['normalised_margin']:.1%} net margin over {m['window_years']} years) "
                f"× {tv.CYCLICAL_PE:.0f}.")
    if kind == "financial" and m.get("justified_pb") is not None:
        return (f"Tangible book {_money(m.get('tangible_book_ps'), sym)} × justified "
                f"{m['justified_pb']:.2f}× P/B, less {tv.BOOK_MOS:.0%}.")
    if kind == "asset" and m.get("asset_value_per_share") is not None:
        return (f"Asset value {_money(m['asset_value_per_share'], sym)} per share, "
                f"less {tv.BOOK_MOS:.0%}.")
    return ""


def hero_html(res, sym="$"):
    bp, price, dist = res.get("buy_price"), res.get("price"), res.get("distance")
    if bp is None:
        why = "; ".join(res.get("missing") or []) or res.get("note") or "Not priced"
        return qc.section_html(
            "Buyable?",
            f'<div class="vp-verdict">{DASH}</div>'
            f'<div class="vp-line">No buy price yet: {qc.esc(why)}.</div>')
    buyable = bool(price) and price <= bp
    verdict = "Yes" if buyable else "Not at this price"
    colour = "var(--accent, #2f6f4f)" if buyable else "var(--text)"
    figs = (f'<div class="vp-fig">Price<b>{qc.esc(_money(price, sym))}</b></div>'
            f'<div class="vp-fig">Buy price<b>{qc.esc(_money(bp, sym))}</b></div>'
            f'<div class="vp-fig">Price vs buy<b>{_pct(dist, signed=True)}</b></div>')
    return qc.section_html(
        f"Buyable? · {res.get('label') or ''}",
        f'<div class="vp-hero"><div class="vp-verdict" style="color:{colour}">'
        f'{qc.esc(verdict)}</div>{figs}</div>'
        f'<div class="vp-line">{qc.esc(one_line(res, sym))}</div>')


def main_test_html(res, sym="$"):
    m = res.get("main") or {}
    kind = res.get("type")
    if kind in ("capital_light", "capital_heavy"):
        title = ("Main test: required vs achievable growth" if kind == "capital_light"
                 else "Main test: DCF with a growth buffer")
        eps_label = "EPS (clean, authored)" if m.get("eps_overridden") else "EPS, latest year"
        pairs = [(eps_label, _money(m.get("eps"), sym)),
                 ("P/E now", _mult(m.get("pe_now"))),
                 ("Own 10-year median P/E", _mult(m.get("pe_median"))),
                 (f"End P/E (lower of median and {tv.PE_CAP:.0f})", _mult(m.get("pe_end"))),
                 ("Required EPS growth for 9%", _pct(m.get("required_growth"))),
                 ("Achievable growth", _pct(m.get("achievable_growth"))),
                 ("Buffer", _pct(m.get("buffer")))]
    elif kind == "cyclical":
        title = "Main test: P/E on normalised earnings"
        margin_label = ("Normalised net margin (authored)" if m.get("margin_overridden")
                        else f"Average net margin, {m.get('window_years')} years")
        pairs = [(margin_label, _pct(m.get("normalised_margin"))),
                 ("Revenue per share", _money(m.get("revenue_per_share"), sym)),
                 ("Normalised EPS", _money(m.get("normalised_eps"), sym)),
                 ("P/E applied (no-growth anchor)", _mult(m.get("target_pe")))]
        if m.get("margin_overridden") and m.get("normalised_margin_auto") is not None:
            pairs.insert(1, ("10-year average, for comparison",
                             _pct(m["normalised_margin_auto"])))
    elif kind == "financial":
        title = "Main test: price to book against return on equity"
        pairs = [("Tangible book per share", _money(m.get("tangible_book_ps"), sym)),
                 ("Sustainable return on tangible equity", _pct(m.get("roe"))),
                 ("Book growth", _pct(m.get("book_growth"))),
                 ("Justified P/B  (ROE − g) ÷ (9% − g)",
                  DASH if m.get("justified_pb") is None else f"{m['justified_pb']:.2f}×"),
                 ("Discount", _pct(m.get("mos")))]
    elif kind == "asset":
        title = "Main test: value of the assets"
        pairs = [("Asset value per share", _money(m.get("asset_value_per_share"), sym)),
                 ("Discount", _pct(m.get("mos")))]
    else:
        return ""
    note = ""
    if res.get("missing"):
        note = (f'<div class="vp-note">Still to set: '
                f'{qc.esc(", ".join(res["missing"]))}.</div>')
    elif res.get("note"):
        note = f'<div class="vp-note">{qc.esc(res["note"])}.</div>'
    return qc.section_html(title, _rows(pairs) + note)


def control_html(res):
    c = res.get("control") or {}
    if not c.get("text"):
        return ""
    mark = {True: "✓ Agrees", False: "⚠ Disagrees — find the assumption that differs"}.get(
        c.get("agrees"), "No verdict without a buy price")
    return qc.section_html("Control test",
                           f'<div class="vp-line"><b>{qc.esc(mark)}</b></div>'
                           f'<div class="vp-note">{qc.esc(c["text"])}</div>')


def details_html(vt, summary, sym="$"):
    """Collapsed: why this type (the authored stap-1 answers) and the DCF range."""
    parts = []
    vt = vt or {}
    why = vt.get("rationale")
    if why:
        extra = ""
        if vt.get("growth_basis"):
            extra += f"\n\nAchievable growth: {vt['growth_basis']}"
        if vt.get("eps_override_reason"):
            extra += f"\n\nClean EPS: {vt['eps_override_reason']}"
        stamp = f" (set {vt['set_at']})" if vt.get("set_at") else ""
        parts.append(f'<details class="vp-details"><summary>Why this type{qc.esc(stamp)}'
                     f'</summary><p>{qc.esc(why + extra)}</p></details>')
    s = summary or {}
    if s.get("weighted_fv_mid"):
        parts.append(
            '<details class="vp-details"><summary>DCF range</summary>'
            + _rows([("Bear", _money(s.get("weighted_fv_low"), sym)),
                     ("Base", _money(s.get("weighted_fv_mid"), sym)),
                     ("Bull", _money(s.get("weighted_fv_high"), sym)),
                     ("DCF buy price (base − margin of safety)",
                      _money(s.get("dcf_buy_price", s.get("buy_price")), sym)),
                     ("DCF implied return", _pct(s.get("implied_return")))])
            + "</details>")
    return qc.section_html("Details", "".join(parts)) if parts else ""


def no_type_html(summary, sym="$"):
    s = summary or {}
    return qc.section_html(
        "Buyable?",
        '<div class="vp-line">No company type set yet, so the buy price is the '
        f'DCF\'s: {qc.esc(_money(s.get("buy_price"), sym))} (fair value less the margin '
        'of safety). Set the type to price it by its own test.</div>')


def page_html(res, vt, summary, sym="$"):
    """The whole tab above the detail sub-tabs."""
    try:
        if not res or res.get("type") not in tv.TYPES:
            return STYLE + no_type_html(summary, sym) + details_html(vt, summary, sym)
        return (STYLE + hero_html(res, sym) + main_test_html(res, sym)
                + control_html(res) + details_html(vt, summary, sym))
    except Exception:   # never break the ticker page over a card
        return STYLE + qc.section_html("Buyable?", '<div class="vp-line">Valuation '
                                       'unavailable right now.</div>')
