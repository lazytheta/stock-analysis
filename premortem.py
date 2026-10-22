"""The pre-mortem: the decision document for one position (owner request
2026-10-22; its own tab since then, a one-line strip on Summary).

The old shape was five free-text fields, and "current" -- meant as one line --
had grown into a journal of 400-800 characters mixing position, valuation,
news and thesis status, while sell/add triggers had become paragraphs. The new
shape separates what changes for different reasons:

    status        intact | cracks | broken, with a one-line status_note
    action        hold | add | trim | sell | wait
    sell, add     triggers: {label, when, detail, state, date}
                  state: watch | near | triggered
    ignore        signals that are NOT a reason to act (short strings)
    discipline    decision rules (short strings, at most MAX_DISCIPLINE)
    log           dated updates, newest first: {date, text}
    current       legacy free text; shown as the oldest update

The position itself is not stored here: the page reads it live from the
brokers. normalize() accepts every older shape, so nothing has to be migrated
before it renders.
"""

import re
from datetime import date

import question_cards as qc

STATUSES = ("intact", "cracks", "broken")
ACTIONS = ("hold", "add", "trim", "sell", "wait")
STATES = ("watch", "near", "triggered")
MAX_LABEL, MAX_WHEN, MAX_DETAIL = 40, 160, 600
MAX_NOTE, MAX_ITEM, MAX_LOG_TEXT = 200, 160, 280
MAX_DISCIPLINE = 8

_GREEN, _AMBER, _RED = "#2f8f4e", "#c79a3a", "#c0603f"
STATUS_TONE = {"intact": _GREEN, "cracks": _AMBER, "broken": _RED}
STATUS_LABEL = {"intact": "Thesis intact", "cracks": "Cracks", "broken": "Thesis broken"}
_TRIGGERED = re.compile(r"^\s*(GETRIGGERD|TRIGGERED)\b[^:—-]*[:—-]?\s*", re.I)


# ── shape ─────────────────────────────────────────────────────────────

def _split_head(text):
    """'HEAD: body' / 'HEAD — body' -> (head, body) when the head is short."""
    for sep in (": ", " — ", " -- "):
        head, found, body = text.partition(sep)
        if found and 3 <= len(head) <= MAX_LABEL + 20:
            return head.strip(), body.strip()
    return "", text.strip()


def _first_sentence(text, limit=MAX_WHEN):
    """(short, rest): the first sentence when it fits, else a cut at a word."""
    # A sentence ends at . or ; followed by a capital, digit or bracket, so
    # "groen incl. resale" is not cut after the abbreviation.
    m = re.match(r"(.+?[.;])\s+(?=[A-Z0-9(])(.*)", text, re.S)
    if m and len(m.group(1)) <= limit:
        return m.group(1).strip(), m.group(2).strip()
    if len(text) <= limit:
        return text, ""
    cut = text.rfind(" ", 0, limit)
    cut = cut if cut > limit // 2 else limit
    return text[:cut].rstrip(" ,") + "…", text[cut:].strip()


def normalize_trigger(item) -> dict | None:
    """A trigger dict from a new-style dict or an old free-text string."""
    if isinstance(item, dict):
        when = str(item.get("when") or "").strip()
        label = str(item.get("label") or "").strip()
        if not (when or label):
            return None
        state = str(item.get("state") or "watch").lower()
        return {"label": label, "when": when, "detail": str(item.get("detail") or "").strip(),
                "state": state if state in STATES else "watch",
                "date": str(item.get("date") or "").strip()}
    text = str(item or "").strip(" -•\t")
    if not text:
        return None
    state = "watch"
    if _TRIGGERED.match(text):
        state, text = "triggered", _TRIGGERED.sub("", text, count=1)
    label, body = _split_head(text)
    when, detail = _first_sentence(body)
    return {"label": label, "when": when, "detail": detail, "state": state, "date": ""}


def normalize(pm) -> dict | None:
    """Every stored shape -> the canonical dict, or None when empty."""
    if isinstance(pm, str):
        pm = {"current": pm} if pm.strip() else None
    if not isinstance(pm, dict):
        return None

    def strings(key):
        return [str(i).strip(" -•\t") for i in (pm.get(key) or []) if str(i).strip(" -•\t")]

    log = [{"date": str(e.get("date") or ""), "text": str(e.get("text") or "").strip()}
           for e in (pm.get("log") or []) if isinstance(e, dict) and str(e.get("text") or "").strip()]
    log.sort(key=lambda e: e["date"], reverse=True)
    current = str(pm.get("current") or "").strip()
    if current:
        log.append({"date": "", "text": current})        # oldest: the legacy note
    status = str(pm.get("status") or "").lower()
    action = str(pm.get("action") or "").lower()
    out = {
        "status": status if status in STATUSES else None,
        "status_note": str(pm.get("status_note") or "").strip(),
        "action": action if action in ACTIONS else None,
        "sell": [t for t in map(normalize_trigger, pm.get("sell") or []) if t],
        "add": [t for t in map(normalize_trigger, pm.get("add") or []) if t],
        "ignore": strings("ignore"),
        "discipline": strings("discipline"),
        "log": log,
    }
    if not any((out["status"], out["action"], out["sell"], out["add"], out["ignore"],
                out["discipline"], out["log"])):
        return None
    return out


def validation_errors(pm: dict) -> list:
    """What set_premortem refuses: the limits that keep the page readable."""
    errs = []
    if pm.get("status") and pm["status"] not in STATUSES:
        errs.append(f"status must be one of {', '.join(STATUSES)}")
    if pm.get("action") and pm["action"] not in ACTIONS:
        errs.append(f"action must be one of {', '.join(ACTIONS)}")
    if len(pm.get("status_note") or "") > MAX_NOTE:
        errs.append(f"status_note over {MAX_NOTE} characters")
    for side in ("sell", "add"):
        for i, t in enumerate(pm.get(side) or []):
            if not isinstance(t, dict):
                errs.append(f"{side}[{i}]: use {{label, when, detail, state}}, not a string")
                continue
            if len(t.get("label") or "") > MAX_LABEL:
                errs.append(f"{side}[{i}].label over {MAX_LABEL} characters")
            if not (t.get("when") or "").strip():
                errs.append(f"{side}[{i}].when is required")
            if len(t.get("when") or "") > MAX_WHEN:
                errs.append(f"{side}[{i}].when over {MAX_WHEN} characters (move the rest to detail)")
            if len(t.get("detail") or "") > MAX_DETAIL:
                errs.append(f"{side}[{i}].detail over {MAX_DETAIL} characters")
            if (t.get("state") or "watch") not in STATES:
                errs.append(f"{side}[{i}].state must be one of {', '.join(STATES)}")
    for key in ("ignore", "discipline"):
        for i, s in enumerate(pm.get(key) or []):
            if len(str(s)) > MAX_ITEM:
                errs.append(f"{key}[{i}] over {MAX_ITEM} characters")
    if len(pm.get("discipline") or []) > MAX_DISCIPLINE:
        errs.append(f"at most {MAX_DISCIPLINE} discipline rules")
    for i, e in enumerate(pm.get("log") or []):
        if len(e.get("text") or "") > MAX_LOG_TEXT:
            errs.append(f"log[{i}] over {MAX_LOG_TEXT} characters")
    return errs


# ── page ──────────────────────────────────────────────────────────────

STYLE = f"""<style>
.pm2-top{{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;margin-bottom:18px}}
@media (min-width:800px){{.pm2-top.pm2-two{{grid-template-columns:minmax(0,3fr) minmax(0,2fr)}}}}
.pm2-card{{background:var(--qc-inner, color-mix(in srgb, var(--text) 4%, var(--card)));border-radius:16px;padding:16px 18px;min-width:0}}
.pm2-lbl{{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--text-muted);margin:0 0 8px}}
.pm2-status{{display:flex;align-items:center;gap:8px;font-size:18px;font-weight:700;color:var(--text)}}
.pm2-dot{{width:11px;height:11px;border-radius:50%;flex:none}}
.pm2-note{{font-size:14px;color:var(--text);line-height:1.5;margin:6px 0 0}}
.pm2-act{{display:inline-block;margin-top:12px;padding:3px 10px;border-radius:8px;font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}}
.pm2-pos{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 16px}}
.pm2-pos b{{display:block;font-size:18px;color:var(--text);line-height:1.25}}
.pm2-pos span{{font-size:12px;color:var(--text-muted)}}
.pm2-alert{{border-radius:12px;padding:12px 14px;margin:0 0 18px;font-size:14px;line-height:1.5;color:var(--text)}}
.pm2-cols{{display:grid;grid-template-columns:minmax(0,1fr);gap:18px}}
@media (min-width:800px){{.pm2-cols{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.pm2-h{{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;margin:0 0 6px}}
.pm2-h i{{width:9px;height:9px;border-radius:50%;display:inline-block}}
.pm2-h em{{font-style:normal;color:var(--text-muted);font-weight:600}}
.pm2-panel{{border-radius:16px;padding:14px 14px 4px;min-width:0}}
.pm2-panel .pm2-h{{font-size:13px;margin:2px 4px 12px}}
.pm2-item{{background:var(--card);border-radius:12px;border-left:4px solid;padding:12px 14px;margin:0 0 10px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.pm2-num{{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:50%;font-size:11px;font-weight:700;color:#fff;margin-right:8px;flex:none}}
.pm2-it{{display:flex;align-items:center;gap:0;min-width:0}}
.pm2-it b{{font-size:15px;color:var(--text);font-weight:700}}
.pm2-item .pm2-when{{font-size:14px;margin-top:6px}}
.pm2-row{{padding:10px 0;border-bottom:1px solid color-mix(in srgb, var(--text) 10%, transparent)}}
.pm2-row:last-child{{border-bottom:none}}
.pm2-rt{{display:flex;justify-content:space-between;gap:10px;align-items:baseline}}
.pm2-rt b{{font-size:14px;color:var(--text);font-weight:600}}
.pm2-when{{font-size:13.5px;color:var(--text);line-height:1.45;margin-top:2px}}
.pm2-pill{{flex:none;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:2px 8px;border-radius:7px;color:#fff}}
.pm2-row details,.pm2-item details{{margin-top:6px}}
.pm2-item summary{{cursor:pointer;font-size:12px;color:var(--text-muted)}}
.pm2-row summary,.pm2-more summary{{cursor:pointer;font-size:12px;color:var(--text-muted)}}
.pm2-det{{font-size:13px;color:var(--text-muted);line-height:1.5;margin-top:4px}}
.pm2-chips{{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}}
.pm2-chip{{padding:4px 10px;border-radius:8px;font-size:13px;color:var(--text-muted);background:color-mix(in srgb, var(--text) 6%, var(--card))}}
.pm2-sec{{margin-top:22px}}
.pm2-tl{{position:relative;margin:4px 0 0 6px;padding-left:22px;border-left:2px solid color-mix(in srgb, var(--text) 14%, transparent)}}
.pm2-ev{{position:relative;margin:0 0 14px}}
.pm2-ev::before{{content:"";position:absolute;left:-29px;top:4px;width:12px;height:12px;border-radius:50%;background:var(--card);border:2px solid color-mix(in srgb, var(--text) 35%, transparent)}}
.pm2-ev.pm2-new::before{{background:var(--accent, #81b29a);border-color:var(--accent, #81b29a)}}
.pm2-evd{{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:0 0 5px}}
.pm2-evd b{{font-size:14px;color:var(--text);font-weight:700}}
.pm2-evd span{{font-size:12px;color:var(--text-muted)}}
.pm2-evd em{{font-style:normal;font-size:10.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:1px 7px;border-radius:6px;color:#fff;background:var(--accent, #81b29a)}}
.pm2-evt{{background:var(--card);border-radius:12px;padding:10px 14px;font-size:14px;line-height:1.5;color:var(--text);box-shadow:0 1px 2px rgba(0,0,0,.04)}}
.pm2-more{{margin-top:8px}}
</style>"""


def _pill(state):
    if state == "triggered":
        return f'<span class="pm2-pill" style="background:{_RED}">Triggered</span>'
    if state == "near":
        return f'<span class="pm2-pill" style="background:{_AMBER}">Near</span>'
    return ""


def _trigger_card(t, n, tone):
    """One reason as its own card: number, bold label, state pill, the
    condition in full, the explanation folded under More."""
    esc = qc.esc
    title = t["label"] or t["when"]
    when = t["when"] if t["label"] else ""
    date = f' · {esc(t["date"])}' if t.get("date") else ""
    det = (f'<details><summary>More</summary><div class="pm2-det">{esc(t["detail"])}</div></details>'
           if t["detail"] else "")
    return (f'<div class="pm2-item" style="border-left-color:{tone}">'
            f'<div class="pm2-rt"><span class="pm2-it"><span class="pm2-num" '
            f'style="background:{tone}">{n}</span><b>{esc(title)}{date}</b></span>'
            f'{_pill(t["state"])}</div>'
            + (f'<div class="pm2-when">{esc(when)}</div>' if when else "") + det + "</div>")


def _column(title, items, tone):
    """A tinted panel per side, so Sell and Add read as two separate lists."""
    if not items:
        return ""
    cards = "".join(_trigger_card(t, i, tone) for i, t in enumerate(items, start=1))
    return (f'<div class="pm2-panel" style="background:color-mix(in srgb, {tone} 7%, transparent)">'
            f'<div class="pm2-h" style="color:{tone}"><i style="background:{tone}"></i>'
            f'{qc.esc(title)} <em>{len(items)}</em></div>{cards}</div>')


def _ago(d, today):
    days = (today - d).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    if days < 365:
        return f"{round(days / 30.44)} months ago"
    years = days / 365.25
    return f"{years:.0f} year{'s' if years >= 1.5 else ''} ago"


def _event(e, today, latest=False):
    """One update on the timeline: the date in words and how long ago."""
    esc = qc.esc
    try:
        d = date.fromisoformat(e["date"][:10])
        head = f'<b>{d.day} {d:%b %Y}</b><span>{_ago(d, today)}</span>'
    except (ValueError, TypeError):
        head = "<b>Earlier note</b><span>no date</span>"
    tag = "<em>Latest</em>" if latest else ""
    return (f'<div class="pm2-ev{" pm2-new" if latest else ""}">'
            f'<div class="pm2-evd">{head}{tag}</div>'
            f'<div class="pm2-evt">{esc(e["text"])}</div></div>')


def _status_card(pm):
    esc = qc.esc
    status = pm["status"]
    tone = STATUS_TONE.get(status, "var(--text-muted)")
    head = (f'<div class="pm2-status"><span class="pm2-dot" style="background:{tone}"></span>'
            f'{esc(STATUS_LABEL.get(status, "No status yet"))}</div>')
    note = f'<p class="pm2-note">{esc(pm["status_note"])}</p>' if pm["status_note"] else ""
    act = ""
    if pm["action"]:
        act_tone = {"add": _GREEN, "sell": _RED, "trim": _AMBER}.get(pm["action"], "var(--text-muted)")
        act = (f'<span class="pm2-act" style="color:{act_tone};'
               f'background:color-mix(in srgb, {act_tone} 14%, transparent)">'
               f'Action: {esc(pm["action"])}</span>')
    return f'<div class="pm2-card"><div class="pm2-lbl">Status</div>{head}{note}{act}</div>'


def _position_card(pos):
    esc = qc.esc
    ret = pos.get("return_pct")
    tone = _GREEN if (ret or 0) >= 0 else _RED
    cells = [
        (f'{pos["shares"]:,.4g}', "shares"),
        (f'${pos["avg_cost"]:,.2f}' if pos.get("avg_cost") else "—", "average cost"),
        (f'${pos["price"]:,.2f}' if pos.get("price") else "—", "price now"),
        (f'<span style="color:{tone}">{ret:+.1f}%</span>' if ret is not None else "—",
         "on your cost"),
    ]
    body = "".join(f'<div><b>{v}</b><span>{esc(c)}</span></div>' for v, c in cells)
    return (f'<div class="pm2-card"><div class="pm2-lbl">Your position · at your broker, '
            f'in dollars</div><div class="pm2-pos">{body}</div></div>')


def tab_html(pm, position=None, today=None) -> str:
    """The Pre-mortem tab: status and position, fired triggers, sell/add
    triggers, what is not a reason, dated updates, discipline folded away."""
    esc = qc.esc
    today = today or date.today()
    pm = normalize(pm)
    if pm is None and not position:
        return qc.section_html("Pre-mortem", '<div class="pm2-note" style="color:var(--text-muted)">'
                               "No pre-mortem yet. Ask Claude via the MCP (set_premortem) "
                               "to write the triggers for this ticker.</div>")
    pm = pm or normalize({"status": "intact"}) or {}
    parts = [qc.css(STYLE)]
    top = [_status_card(pm)] + ([_position_card(position)] if position else [])
    parts.append(f'<div class="pm2-top{" pm2-two" if position else ""}">{"".join(top)}</div>')

    fired = [t for t in pm["sell"] + pm["add"] if t["state"] == "triggered"]
    if fired:
        names = "; ".join(t["label"] or t["when"] for t in fired)
        parts.append(f'<div class="pm2-alert" style="background:color-mix(in srgb, {_RED} 12%, '
                     f'transparent)"><b>Triggered:</b> {esc(names)}</div>')

    cols = _column("Sell when", pm["sell"], _RED) + _column("Add when", pm["add"], _GREEN)
    if cols:
        parts.append(f'<div class="pm2-cols">{cols}</div>')
    if pm["ignore"]:
        chips = "".join(f'<span class="pm2-chip">{esc(s)}</span>' for s in pm["ignore"])
        parts.append(f'<div class="pm2-sec"><div class="pm2-h" style="color:var(--text-muted)">'
                     f'Not a reason <em>{len(pm["ignore"])}</em></div>'
                     f'<div class="pm2-chips">{chips}</div></div>')
    if pm["log"]:
        shown, rest = pm["log"][:3], pm["log"][3:]
        more = (f'<details class="pm2-more"><summary>Older updates ({len(rest)})</summary>'
                f'<div class="pm2-tl" style="margin-top:10px">'
                f'{"".join(_event(e, today) for e in rest)}</div></details>' if rest else "")
        events = "".join(_event(e, today, latest=(i == 0)) for i, e in enumerate(shown))
        parts.append(f'<div class="pm2-sec"><div class="pm2-h" style="color:var(--text-muted)">'
                     f'Updates <em>{len(pm["log"])}</em></div>'
                     f'<div class="pm2-tl">{events}</div>{more}</div>')
    if pm["discipline"]:
        items = "".join(f'<div class="pm2-row pm2-when">{esc(s)}</div>' for s in pm["discipline"])
        parts.append(f'<details class="pm2-sec pm2-more"><summary>Discipline '
                     f'({len(pm["discipline"])})</summary>{items}</details>')
    return qc.section_html("Pre-mortem", "".join(parts))


def summary_strip_html(pm) -> str | None:
    """One line for the Summary tab, or None without a pre-mortem."""
    esc = qc.esc
    pm = normalize(pm)
    if pm is None:
        return None
    bits = []
    if pm["status"]:
        bits.append(f'<span style="color:{STATUS_TONE[pm["status"]]};font-weight:700">● '
                    f'{esc(STATUS_LABEL[pm["status"]])}</span>')
    if pm["action"]:
        bits.append(f'Action: <b>{esc(pm["action"])}</b>')
    bits.append(f'{len(pm["sell"])} sell · {len(pm["add"])} add triggers')
    fired = sum(t["state"] == "triggered" for t in pm["sell"] + pm["add"])
    if fired:
        bits.append(f'<span style="color:{_RED};font-weight:700">{fired} triggered</span>')
    body = (f'<div style="font-size:14px;color:var(--text);line-height:1.6">{" · ".join(bits)}'
            f' <span style="color:var(--text-muted)">— details on the Pre-mortem tab</span></div>')
    return qc.section_html("Pre-mortem", body)


def position_from_portfolio(ticker, portfolio_data, held_share_cost) -> dict | None:
    """The live position for `ticker` from the session's broker data
    (summed over brokers), or None when it is not held / not loaded."""
    if not portfolio_data:
        return None
    base = ticker.split(".")[0].upper()
    shares = cost = 0.0
    price = None
    for key, row in portfolio_data.items():
        sym = str(row.get("symbol") or key).upper()
        if sym not in (ticker.upper(), base):
            continue
        held = row.get("shares_held") or 0.0
        if held <= 0:
            continue
        c, n = held_share_cost(row.get("trades") or [])
        shares += held
        cost += c if n else 0.0
        price = row.get("current_price") or price
    if shares <= 0:
        return None
    avg = cost / shares if cost else None
    ret = (price / avg - 1) * 100 if (price and avg) else None
    return {"shares": shares, "avg_cost": avg, "price": price, "return_pct": ret}
