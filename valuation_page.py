"""Valuation tab: the buy price for this company's type, and why.

Built from a type_valuation.evaluate() result (Notion: "Waarderingsmethode per
type bedrijf", v0.2). The page lays itself out after the type: only the cards
that type uses appear, and a missing input is one line, never a row of dashes.

Layout (house style of the Earnings tab): one "Buyable?" section with the
verdict chips, a price-vs-buy-price ladder and three tiles; one section that
shows how the buy price is built (for capital-light names the required and
achievable growth as two bars) with the control test at its foot; the authored
"why this type" and the DCF range folded away under Details.

Every dynamic string goes through question_cards.esc (no raw "$": Streamlit
reads two of them as LaTeX); no public function raises.
"""

from __future__ import annotations

import question_cards as qc
import type_valuation as tv

DASH = "—"
GOOD = "#2f8f4e"
WARN = "#c79a3a"
NEUTRAL = "#86868b"

_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"
_HAIR = "color-mix(in srgb, var(--text) 10%, transparent)"

STYLE = f"""<style>
.vp-head{{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 12px}}
.vp-chip{{font-size:.7rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;
  padding:4px 11px;border-radius:10px;white-space:nowrap}}
.vp-sentence{{margin:0;font-size:15px;line-height:1.55;color:var(--text);max-width:72ch}}
.vp-tiles{{display:grid;grid-template-columns:repeat(var(--vp-cols,3),minmax(0,1fr));gap:14px;
  margin-top:16px}}
@media (max-width:900px){{.vp-tiles{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (max-width:480px){{.vp-tiles{{grid-template-columns:minmax(0,1fr)}}}}
.vp-tile{{background:{_INNER};border-radius:16px;padding:14px 18px;min-width:0}}
.vp-lbl{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 3px}}
.vp-val{{font-size:22px;font-weight:700;color:var(--text);line-height:1.2;margin:2px 0 3px;
  font-variant-numeric:tabular-nums;overflow-wrap:anywhere}}
.vp-cap{{font-size:12px;color:var(--text-muted);line-height:1.35}}
.vp-ladder{{background:{_INNER};border-radius:16px;padding:40px 22px 34px;margin-top:16px}}
.vp-track{{position:relative;height:10px;border-radius:5px;
  background:color-mix(in srgb, var(--text) 10%, transparent)}}
.vp-zone{{position:absolute;left:0;top:0;bottom:0;border-radius:5px 0 0 5px;
  background:color-mix(in srgb, {GOOD} 35%, transparent)}}
.vp-mark{{position:absolute;top:-6px;width:3px;height:22px;border-radius:2px;
  transform:translateX(-50%)}}
.vp-tag{{position:absolute;transform:translateX(-50%);font-size:12px;font-weight:700;
  white-space:nowrap;font-variant-numeric:tabular-nums}}
.vp-tag.up{{top:-30px}}
.vp-tag.down{{top:20px;color:var(--text-muted)}}
.vp-bars{{background:{_INNER};border-radius:16px;padding:16px 20px;display:grid;
  grid-template-columns:minmax(110px,190px) minmax(0,1fr) 64px;gap:12px 14px;align-items:center}}
.vp-bars .n{{font-size:13px;color:var(--text-muted)}}
.vp-bars .v{{font-size:15px;font-weight:700;text-align:right;font-variant-numeric:tabular-nums}}
.vp-bar{{position:relative;height:14px}}
.vp-bar i{{position:absolute;top:0;bottom:0;border-radius:4px}}
.vp-bar b{{position:absolute;top:-4px;bottom:-4px;width:1px;
  background:color-mix(in srgb, var(--text) 35%, transparent)}}
.vp-note{{margin:12px 2px 0;font-size:12px;color:var(--text-muted);line-height:1.45}}
.vp-control{{display:flex;gap:12px;align-items:flex-start;margin-top:14px;padding-top:14px;
  border-top:1px solid {_HAIR}}}
.vp-control .ico{{flex:none;width:28px;height:28px;border-radius:50%;display:flex;
  align-items:center;justify-content:center;font-size:14px;font-weight:700;color:#fff}}
.vp-control .t{{font-size:14px;font-weight:700;color:var(--text)}}
.vp-control .s{{font-size:13px;color:var(--text-muted);margin-top:2px}}
.vp-details{{background:{_INNER};border-radius:14px;margin:0 0 10px}}
.vp-details:last-child{{margin-bottom:0}}
.vp-details summary{{cursor:pointer;list-style:none;padding:13px 18px;font-size:14px;
  font-weight:600;color:var(--text);display:flex;align-items:center;gap:10px;outline:none}}
.vp-details summary::-webkit-details-marker{{display:none}}
.vp-details summary::before{{content:"›";font-size:18px;line-height:1;color:var(--text-muted);
  transition:transform .15s ease}}
.vp-details[open] summary::before{{transform:rotate(90deg)}}
.vp-details summary span{{margin-left:auto;font-weight:400;font-size:12px;color:var(--text-muted)}}
.vp-details summary:focus-visible{{outline:none !important;
  box-shadow:inset 0 0 0 2px var(--accent);border-radius:14px}}
.vp-body{{padding:0 18px 16px 38px;font-size:14px;line-height:1.55;color:var(--text)}}
.vp-body p{{margin:0 0 8px;white-space:pre-wrap}}
.vp-body p:last-child{{margin:0}}
.vp-body .k{{font-weight:700}}
.vp-kv{{display:grid;grid-template-columns:max-content max-content;gap:6px 28px;font-size:14px}}
.vp-kv span:nth-child(odd){{color:var(--text-muted)}}
.vp-kv span:nth-child(even){{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}}
</style>"""


# ── formatting ─────────────────────────────────────────────────────────────

def _money(x, sym="$"):
    return DASH if x is None else f"{sym}{x:,.2f}"


def _pct(x, signed=False):
    if x is None:
        return DASH
    return f"{x:+.1%}" if signed else f"{x:.1%}"


def _mult(x):
    return DASH if x is None else f"{x:.1f}×"


def _chip(text, colour):
    return (f'<span class="vp-chip" style="background:{colour}22;color:{colour}">'
            f'{qc.esc(text)}</span>')


def _tile(label, value, cap=""):
    cap_html = f'<div class="vp-cap">{qc.esc(cap)}</div>' if cap else ""
    return (f'<div class="vp-tile"><div class="vp-lbl">{qc.esc(label)}</div>'
            f'<div class="vp-val">{qc.esc(value)}</div>{cap_html}</div>')


def _tiles(tiles, cols=None):
    cols = cols or min(max(len(tiles), 1), 4)
    return f'<div class="vp-tiles" style="--vp-cols:{cols}">{"".join(tiles)}</div>'


def _kv(pairs):
    cells = "".join(f"<span>{qc.esc(k)}</span><span>{qc.esc(v)}</span>" for k, v in pairs)
    return f'<div class="vp-kv">{cells}</div>'


def _buyable(res):
    bp, price = res.get("buy_price"), res.get("price")
    return bool(price) and bp is not None and price <= bp


# ── the sentence under the verdict ─────────────────────────────────────────

def one_line(res, sym="$"):
    """What the buy price rests on, in one sentence."""
    m = res.get("main") or {}
    kind = res.get("type")
    hurdle = f"{tv.HURDLE:.0%}"
    if kind == "capital_light":
        req, g = m.get("required_growth"), m.get("achievable_growth")
        buf = m.get("buffer", tv.BUFFER)
        if req is not None and g is not None:
            ask = (f"EPS can shrink {abs(req):.1%} a year and still return {hurdle}"
                   if req < 0 else f"EPS has to grow {req:.1%} a year to return {hurdle}")
            return (f"At this price {ask}. The model expects {g:.1%}; after the "
                    f"{buf:.0%} buffer that is {g - buf:.1%}.")
    if kind == "capital_heavy":
        return (f"DCF fair value at the {hurdle} hurdle, with the model's growth "
                f"{tv.BUFFER:.0%} lower in every year. No further discount.")
    if kind == "cyclical" and m.get("normalised_eps") is not None:
        return (f"Earnings over a full cycle: normalised EPS {_money(m['normalised_eps'], sym)} "
                f"({m['normalised_margin']:.1%} net margin over {m['window_years']} years) "
                f"× {tv.CYCLICAL_PE:.0f}.")
    if kind == "financial" and m.get("justified_pb") is not None:
        return (f"Tangible book {_money(m.get('tangible_book_ps'), sym)} × a justified "
                f"{m['justified_pb']:.2f}× P/B, less {tv.BOOK_MOS:.0%}.")
    if kind == "asset" and m.get("asset_value_per_share") is not None:
        return (f"Asset value {_money(m['asset_value_per_share'], sym)} per share, "
                f"less {tv.BOOK_MOS:.0%}.")
    return ""


# ── Buyable? ───────────────────────────────────────────────────────────────

def _control_chip(res):
    agrees = (res.get("control") or {}).get("agrees")
    if agrees is True:
        return _chip("✓ Control agrees", GOOD)
    if agrees is False:
        return _chip("⚠ Control disagrees", WARN)
    return ""


def ladder_html(price, bp, sym="$"):
    """A track from 0 to a little past the higher of the two: the green zone
    runs up to the buy price, a mark for the buy price and one for today."""
    if not price or not bp or price <= 0 or bp <= 0:
        return ""
    top = max(price, bp) * 1.15

    def pos(x):
        return max(2.0, min(98.0, x / top * 100))

    p_col = GOOD if price <= bp else "var(--text)"
    return (
        '<div class="vp-ladder"><div class="vp-track">'
        f'<div class="vp-zone" style="width:{pos(bp):.1f}%"></div>'
        f'<div class="vp-mark" style="left:{pos(bp):.1f}%;background:{GOOD}"></div>'
        f'<div class="vp-mark" style="left:{pos(price):.1f}%;background:{p_col}"></div>'
        f'<div class="vp-tag up" style="left:{pos(price):.1f}%;color:{p_col}">'
        f'Now {qc.esc(_money(price, sym))}</div>'
        f'<div class="vp-tag down" style="left:{pos(bp):.1f}%">'
        f'Buy below {qc.esc(_money(bp, sym))}</div>'
        '</div></div>')


def hero_html(res, sym="$"):
    bp, price, dist = res.get("buy_price"), res.get("price"), res.get("distance")
    type_chip = _chip(res.get("label") or "", NEUTRAL) if res.get("label") else ""
    if bp is None:
        why = "; ".join(res.get("missing") or []) or res.get("note") or "Not priced"
        return qc.section_html(
            "Buyable?",
            f'<div class="vp-head">{_chip("No buy price", NEUTRAL)}{type_chip}</div>'
            f'<p class="vp-sentence">No buy price yet: {qc.esc(why)}.</p>')
    buyable = _buyable(res)
    verdict = _chip("Yes" if buyable else "Not at this price", GOOD if buyable else WARN)
    if dist is None:
        dist_cap = ""
    elif buyable:
        dist_cap = "below the buy price"
    else:
        dist_cap = f"has to fall {1 - bp / price:.0%} to reach it" if price else ""
    tiles = _tiles([_tile("Price", _money(price, sym), "today"),
                    _tile("Buy price", _money(bp, sym), res.get("label") or ""),
                    _tile("Price vs buy", _pct(dist, signed=True), dist_cap)], cols=3)
    return qc.section_html(
        "Buyable?",
        f'<div class="vp-head">{verdict}{type_chip}{_control_chip(res)}</div>'
        f'<p class="vp-sentence">{qc.esc(one_line(res, sym))}</p>'
        + ladder_html(price, bp, sym) + tiles)


# ── how the buy price is built ─────────────────────────────────────────────

def growth_bars_html(required, achievable, buffer):
    """Required EPS growth against achievable-after-buffer on one scale with
    a zero line, so a negative requirement reads as such."""
    if required is None or achievable is None:
        return ""
    net = achievable - (buffer or 0)
    lo = min(0.0, required, net)
    hi = max(0.0, required, net, achievable)
    span = (hi - lo) or 0.01
    zero = (0 - lo) / span * 100

    def bar(v, colour):
        left = (min(v, 0) - lo) / span * 100
        width = max(abs(v) / span * 100, 0.8)
        return (f'<div class="vp-bar"><b style="left:{zero:.1f}%"></b>'
                f'<i style="left:{left:.1f}%;width:{width:.1f}%;background:{colour}"></i></div>')

    ok = net >= required
    return (
        '<div class="vp-bars">'
        f'<span class="n">Required EPS growth for {tv.HURDLE:.0%}</span>'
        f'{bar(required, "var(--text-muted)")}<span class="v">{_pct(required)}</span>'
        f'<span class="n">Achievable, after {buffer or 0:.0%} buffer</span>'
        f'{bar(net, GOOD if ok else WARN)}<span class="v">{_pct(net)}</span>'
        '</div>'
        f'<div class="vp-note">Achievable before the buffer: {_pct(achievable)}. '
        + ("Achievable clears what the price asks." if ok
           else "Achievable falls short of what the price asks.") + '</div>')


def main_test_html(res, sym="$"):
    m = res.get("main") or {}
    kind = res.get("type")
    top = ""
    if kind in ("capital_light", "capital_heavy"):
        title = ("How the buy price is built · required vs achievable growth"
                 if kind == "capital_light"
                 else "How the buy price is built · DCF with a growth buffer")
        eps_cap = "clean, authored" if m.get("eps_overridden") else "latest year"
        tiles = [_tile("EPS", _money(m.get("eps"), sym), eps_cap),
                 _tile("P/E now", _mult(m.get("pe_now"))),
                 _tile("10-year median P/E", _mult(m.get("pe_median")), "own history"),
                 _tile("End P/E", _mult(m.get("pe_end")),
                       f"lower of median and {tv.PE_CAP:.0f}")]
        if kind == "capital_light":
            top = growth_bars_html(m.get("required_growth"), m.get("achievable_growth"),
                                   m.get("buffer", tv.BUFFER))
            if not top:
                tiles.append(_tile("Required EPS growth for 9%",
                                   _pct(m.get("required_growth"))))
        else:
            tiles = [t for t in tiles if DASH not in t] or tiles
    elif kind == "cyclical":
        title = "How the buy price is built · P/E on normalised earnings"
        margin_cap = ("authored" if m.get("margin_overridden")
                      else f"average over {m.get('window_years')} years")
        tiles = [_tile("Net margin", _pct(m.get("normalised_margin")), margin_cap),
                 _tile("Revenue per share", _money(m.get("revenue_per_share"), sym)),
                 _tile("Normalised EPS", _money(m.get("normalised_eps"), sym)),
                 _tile("P/E applied", _mult(m.get("target_pe")), "no-growth anchor")]
        if m.get("margin_overridden") and m.get("normalised_margin_auto") is not None:
            tiles.insert(1, _tile("10-year average margin",
                                  _pct(m["normalised_margin_auto"]), "for comparison"))
    elif kind == "financial":
        title = "How the buy price is built · price to book against return on equity"
        jpb = m.get("justified_pb")
        tiles = [_tile("Tangible book / share", _money(m.get("tangible_book_ps"), sym)),
                 _tile("Return on tangible equity", _pct(m.get("roe")), "sustainable"),
                 _tile("Book growth", _pct(m.get("book_growth"))),
                 _tile("Justified P/B", DASH if jpb is None else f"{jpb:.2f}×",
                       f"(ROE − g) ÷ ({tv.HURDLE:.0%} − g)"),
                 _tile("Discount", _pct(m.get("mos")))]
    elif kind == "asset":
        title = "How the buy price is built · value of the assets"
        tiles = [_tile("Asset value / share", _money(m.get("asset_value_per_share"), sym)),
                 _tile("Discount", _pct(m.get("mos")))]
    else:
        return ""
    note = ""
    if res.get("missing"):
        note = (f'<div class="vp-note">Still to set: '
                f'{qc.esc(", ".join(res["missing"]))}.</div>')
    elif res.get("note"):
        note = f'<div class="vp-note">{qc.esc(res["note"])}.</div>'
    return qc.section_html(title, top + _tiles(tiles) + note + control_html(res))


def control_html(res):
    """The second opinion, at the foot of the main test."""
    c = res.get("control") or {}
    if not c.get("text"):
        return ""
    agrees = c.get("agrees")
    if agrees is True:
        ico, colour, head = "✓", GOOD, "Control test agrees"
    elif agrees is False:
        ico, colour, head = "!", WARN, "Control test disagrees: find the assumption that differs"
    else:
        ico, colour, head = "–", NEUTRAL, "Control test: no verdict without a buy price"
    return (f'<div class="vp-control"><div class="ico" style="background:{colour}">{ico}</div>'
            f'<div><div class="t">{qc.esc(head)}</div>'
            f'<div class="s">{qc.esc(c["text"])}</div></div></div>')


# ── details ────────────────────────────────────────────────────────────────

def details_html(vt, summary, sym="$"):
    """Folded away: why this type (the authored stap-1 answers) and the DCF range."""
    parts = []
    vt = vt or {}
    why = vt.get("rationale")
    if why:
        paras = [f"<p>{qc.esc(why)}</p>"]
        if vt.get("growth_basis"):
            paras.append(f'<p><span class="k">Achievable growth.</span> '
                         f'{qc.esc(vt["growth_basis"])}</p>')
        if vt.get("eps_override_reason"):
            paras.append(f'<p><span class="k">Clean EPS.</span> '
                         f'{qc.esc(vt["eps_override_reason"])}</p>')
        stamp = f'<span>set {qc.esc(vt["set_at"])}</span>' if vt.get("set_at") else ""
        parts.append(f'<details class="vp-details"><summary>Why this type{stamp}</summary>'
                     f'<div class="vp-body">{"".join(paras)}</div></details>')
    s = summary or {}
    if s.get("weighted_fv_mid"):
        parts.append(
            '<details class="vp-details"><summary>DCF range'
            f'<span>base {qc.esc(_money(s.get("weighted_fv_mid"), sym))}</span></summary>'
            '<div class="vp-body">'
            + _kv([("Bear", _money(s.get("weighted_fv_low"), sym)),
                   ("Base", _money(s.get("weighted_fv_mid"), sym)),
                   ("Bull", _money(s.get("weighted_fv_high"), sym)),
                   ("DCF buy price (base − margin of safety)",
                    _money(s.get("dcf_buy_price", s.get("buy_price")), sym)),
                   ("DCF implied return", _pct(s.get("implied_return")))])
            + "</div></details>")
    return qc.section_html("Details", "".join(parts)) if parts else ""


def no_type_html(summary, sym="$"):
    s = summary or {}
    return qc.section_html(
        "Buyable?",
        f'<div class="vp-head">{_chip("No type set", NEUTRAL)}</div>'
        '<p class="vp-sentence">No company type set yet, so the buy price is the '
        f'DCF\'s: {qc.esc(_money(s.get("buy_price"), sym))} (fair value less the margin '
        'of safety). Set the type to price it by its own test.</p>')


def page_html(res, vt, summary, sym="$"):
    """The whole tab above the detail sub-tabs."""
    try:
        if not res or res.get("type") not in tv.TYPES:
            return STYLE + no_type_html(summary, sym) + details_html(vt, summary, sym)
        return (STYLE + hero_html(res, sym) + main_test_html(res, sym)
                + details_html(vt, summary, sym))
    except Exception:   # never break the ticker page over a card
        return STYLE + qc.section_html("Buyable?", '<p class="vp-sentence">Valuation '
                                       'unavailable right now.</p>')
