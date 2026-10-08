"""HTML for the Management tab: the white "Management" section (four fact
tiles, recent C-suite changes, as-of and source) and the three "Management
questions" flip cards, both from the "Management Cards" pre-scan section.

Pure string builders, no Streamlit import. Every dynamic text goes through
question_cards.esc (a bare `$` pair renders as LaTeX), every <style> block is
emitted on one line, a missing value shows as "—", and neither render
function raises: bad data degrades to the card set's notice.
"""

import logging

import question_cards as qc
from management_cards import TITLE, parse_for_display

logger = logging.getLogger(__name__)

DASH = "—"
MINUS = "−"
NO_CHANGES = "No C-suite changes reported."

_INNER = "var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)))"
_HAIRLINE = "color-mix(in srgb, var(--text) 10%, transparent)"

STYLE = f"""<style>
.mg-tiles{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}}
@media (max-width:1000px){{.mg-tiles{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media (max-width:520px){{.mg-tiles{{grid-template-columns:minmax(0,1fr)}}}}
.mg-tile{{background:{_INNER};border-radius:16px;padding:16px 18px;box-sizing:border-box;
  min-width:0}}
.mg-lbl{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 3px}}
.mg-val{{font-size:22px;font-weight:700;color:var(--text);line-height:1.2;margin:2px 0 2px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.mg-ceo{{background:{_INNER};border-radius:16px;padding:16px 18px;margin-top:16px}}
.mg-ceo-name{{font-size:16px;font-weight:700;color:var(--text);margin:2px 0 10px}}
.mg-since{{font-size:12px;font-weight:400;color:var(--text-muted);margin-left:8px}}
.mg-ceo-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}
@media (max-width:760px){{.mg-ceo-grid{{grid-template-columns:minmax(0,1fr)}}}}
.mg-sub{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--text-muted);margin:0 0 4px}}
.mg-ceo p{{margin:0;font-size:14px;color:var(--text);line-height:1.5}}
.mg-cap{{font-size:12px;color:var(--text-muted);line-height:1.35}}
.mg-changes{{background:{_INNER};border-radius:16px;padding:16px 18px;margin-top:16px}}
.mg-changes ul{{margin:0;padding:0;list-style:none}}
.mg-changes li{{font-size:14px;color:var(--text);line-height:1.45;padding:7px 0;
  border-bottom:1px solid {_HAIRLINE}}}
.mg-changes li:last-child{{border-bottom:none}}
.mg-date{{color:var(--text-muted);font-variant-numeric:tabular-nums;margin-right:10px}}
.mg-none{{margin:0;font-size:14px;color:var(--text-muted)}}
.mg-src{{margin-top:12px;font-size:12px;color:var(--text-muted)}}
.mg-guide{{background:{_INNER};border-radius:16px;padding:12px 18px;margin-top:16px;
  font-size:14px;color:var(--text);display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}}
.mg-guide .mg-lbl{{margin:0}}
</style>"""


def _parse(content):
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        return parse_for_display(content)
    except Exception:
        return None


def _pct(x):
    if x is None:
        return DASH
    return f"{x:.2f}%" if 0 < x < 1 else f"{x:.1f}%".replace(".0%", "%")


def _usd(x, digits=None):
    """$28M, $1.3M, $950K, $1.2B; `digits` fixes the decimals for millions,
    otherwise one decimal below $10M and none above."""
    a = abs(x)
    if a >= 1e9:
        return f"${a / 1e9:.1f}B"
    if a >= 1e6:
        d = digits if digits is not None else (1 if a < 1e7 else 0)
        return f"${a / 1e6:.{d}f}M"
    if a >= 1e3:
        return f"${a / 1e3:.0f}K"
    return f"${a:.0f}"


def _signed_usd(x):
    if x is None:
        return DASH
    sign = MINUS if x < 0 else "+" if x > 0 else ""
    return sign + _usd(x)


def _n(x):
    return DASH if x is None else str(x)


def _tile(label, value, caption):
    return (f'<div class="mg-tile"><div class="mg-lbl">{qc.esc(label.upper())}</div>'
            f'<div class="mg-val">{qc.esc(value)}</div>'
            f'<div class="mg-cap">{qc.esc(caption)}</div></div>')


def _trading(f):
    """The net amount as the figure; who bought and sold, and how much of the
    selling was pre-planned, in the caption -- the counts in the figure made
    it wrap onto two lines (owner, 2026-10-08)."""
    value = _signed_usd(f["insider_net_12m_usd"])
    bits = []
    if f["buyers"] is not None or f["sellers"] is not None:
        bits.append(f"{_n(f['buyers'])} buyers · {_n(f['sellers'])} sellers")
    if f["planned_sell_pct"] is not None:
        bits.append(f"{_pct(f['planned_sell_pct'])} of sales pre-planned")
    return value, " · ".join(bits) or "open-market buys − sells"


def _shares(n):
    if n >= 1e9:
        return f"{n / 1e9:.2f}B shares"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M shares"
    if n >= 1e3:
        return f"{n / 1e3:.0f}K shares"
    return f"{n:,} shares"


def _pay(f):
    if not f["ceo_pay"]:
        return DASH, "last fiscal year"
    last = max(f["ceo_pay"], key=lambda p: p["fy"])
    caption = last["fy"]
    if last["stock_pct"] is not None:
        caption += f" · {_pct(last['stock_pct'])} in stock"
    return _usd(last["total_usd"], digits=1), caption


def _tiles_html(f):
    # The CEO's name and tenure now sit in the "The CEO" panel below; the
    # tile says how many shares that percentage is.
    shares = f.get("ceo_shares")
    ceo_cap = _shares(shares) if shares else (f["ceo"]["name"] if f["ceo"] else "CEO not reported")
    trading, trading_cap = _trading(f)
    pay, pay_cap = _pay(f)
    tiles = (_tile("Insider ownership", _pct(f["insider_ownership_pct"]),
                   "officers + directors, % of shares"),
             _tile("CEO ownership", _pct(f["ceo_ownership_pct"]), ceo_cap),
             _tile("Net insider trading (12m)", trading, trading_cap),
             _tile("CEO pay", pay, pay_cap))
    return f'<div class="mg-tiles">{"".join(tiles)}</div>'


def _ceo_html(f):
    """Who runs the company and how they are seen; nothing without a bio."""
    bio, ceo = f.get("ceo_bio"), f.get("ceo")
    if not bio:
        return ""
    head = qc.esc(ceo["name"]) if ceo else "The CEO"
    if ceo and ceo.get("since"):
        head += f' <span class="mg-since">CEO since {ceo["since"]}</span>'
    return (f'<div class="mg-ceo"><div class="mg-lbl">The CEO</div>'
            f'<div class="mg-ceo-name">{head}</div>'
            f'<div class="mg-ceo-grid">'
            f'<div><div class="mg-sub">Background</div><p>{qc.esc(bio["background"])}</p></div>'
            f'<div><div class="mg-sub">How they are seen</div><p>{qc.esc(bio["reputation"])}</p></div>'
            f'</div></div>')


def _changes_html(changes):
    head = '<div class="mg-lbl">Recent leadership changes</div>'
    if not changes:
        return f'<div class="mg-changes">{head}<p class="mg-none">{qc.esc(NO_CHANGES)}</p></div>'
    items = "".join(
        f'<li><span class="mg-date">{qc.esc(c["date"])}</span><b>{qc.esc(c["person"])}</b>, '
        f'{qc.esc(c["role"])}: {qc.esc(c["action"])}</li>'
        for c in sorted(changes, key=lambda c: c["date"], reverse=True))
    return f'<div class="mg-changes">{head}<ul>{items}</ul></div>'


def _guidance_html(record):
    """Does management deliver what it guides? One line; nothing when the
    record is unknown."""
    if not record:
        return ""
    met, total = record["met"], record["total"]
    return (f'<div class="mg-guide"><span class="mg-lbl">Guidance kept</span>'
            f'<b>{met} of {total}</b> times results met or beat the company\'s own '
            f'guidance</div>')


def _notice_section(theme):
    return qc.section_html("Management", qc.notice_html(TITLE, theme))


def management_section_html(content, theme) -> str:
    """The white "Management" section: four tiles, the C-suite changes and
    the as-of/source line; the card set's notice when the section is missing
    or invalid."""
    parsed = _parse(content)
    if parsed is None:
        return _notice_section(theme)
    try:
        f = parsed["facts"]
        inner = (f'{qc.css(STYLE)}{_tiles_html(f)}{_ceo_html(f)}{_changes_html(f["changes"])}'
                 f'{_guidance_html(f.get("guidance_record"))}'
                 f'<div class="mg-src">As of {qc.esc(f["as_of"])} · Source: '
                 f'{qc.esc(f["source"])}</div>')
        return qc.section_html("Management", inner)
    except Exception as e:
        logger.warning("Management section failed: %s", e)
        return _notice_section(theme)


def cards_section_html(content, theme) -> str:
    """The three Management flip cards, or "" when there are none (the
    Management section above already shows the notice)."""
    parsed = _parse(content)
    if parsed is None:
        return ""
    try:
        return qc.section_html("Management questions",
                               qc.grid_html(parsed["card_set"], parsed["cards"], theme))
    except Exception as e:
        logger.warning("Management cards failed: %s", e)
        return ""
