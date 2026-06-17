"""LazyTheta notification worker — runs on a schedule (GitHub Actions cron).

Phase 1b scope:
  1. Telegram account linking — poll getUpdates for `/start <token>` and map the
     chat to the user (via notification_settings.telegram_link_token).
  2. Custom reminders — fire any due reminder (fire_date <= today, not yet sent):
     write a notifications row (drives the in-app indicator) and push to Telegram.

Earnings + price alerts are Phase 2 (broker-sourced, per-user TastyTrade OAuth)
and are intentionally not here yet — see
docs/superpowers/specs/2026-06-17-notifications-design.md.

Reads config from env (set as GitHub Actions secrets):
  SUPABASE_URL, SUPABASE_SERVICE_KEY, TELEGRAM_BOT_TOKEN
Run: `python notify_job.py`
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, date, datetime

import requests
from supabase import create_client

_TG = os.environ.get("TELEGRAM_BOT_TOKEN")
_URL = os.environ.get("SUPABASE_URL")
_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
if not (_TG and _URL and _KEY):
    # Secrets not configured yet — exit clean so scheduled runs don't fail red.
    print("[notify] missing TELEGRAM_BOT_TOKEN / SUPABASE_URL / SUPABASE_SERVICE_KEY — skipping.")
    sys.exit(0)

_TG_API = f"https://api.telegram.org/bot{_TG}"
_sb = create_client(_URL, _KEY)

_KIND_EMOJI = {"custom": "📅", "earnings": "📊", "price": "🔔", "verdict": "⚖️"}


def _now_iso():
    return datetime.now(UTC).isoformat()


def tg_send(chat_id, text):
    try:
        requests.post(f"{_TG_API}/sendMessage",
                      json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                      timeout=20)
    except Exception as e:  # never let a send failure abort the run
        print(f"[notify] telegram send failed: {e}")


def link_telegram_chats():
    """Poll getUpdates; for each `/start <token>`, bind the chat to the user.
    Acks processed updates at the end so they aren't re-read next run."""
    try:
        resp = requests.get(f"{_TG_API}/getUpdates", timeout=20).json()
    except Exception as e:
        print(f"[notify] getUpdates failed: {e}")
        return
    updates = resp.get("result", []) or []
    max_id = None
    for u in updates:
        max_id = u["update_id"]
        msg = u.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat_id = (msg.get("chat") or {}).get("id")
        if not (chat_id and text.startswith("/start")):
            continue
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""
        if not token:
            continue
        found = (_sb.table("notification_settings").select("user_id")
                 .eq("telegram_link_token", token).limit(1).execute().data or [])
        if found:
            (_sb.table("notification_settings")
             .update({"telegram_chat_id": str(chat_id), "updated_at": _now_iso()})
             .eq("user_id", found[0]["user_id"]).execute())
            tg_send(chat_id, "✅ Linked to your LazyTheta watchlist. "
                             "You'll get your alerts here.")
        else:
            tg_send(chat_id, "This link has expired. Open LazyTheta → Watchlist → "
                             "Notifications → Connect Telegram for a fresh link.")
    if max_id is not None:  # ack so they drop from the queue
        try:
            requests.get(f"{_TG_API}/getUpdates",
                         params={"offset": max_id + 1}, timeout=20)
        except Exception:
            pass


def _settings_by_user():
    rows = _sb.table("notification_settings").select("*").execute().data or []
    return {r["user_id"]: r for r in rows}


def _emit(user_id, kind, title, body=None, ticker=None, dedupe_key=None, chat_id=None):
    """Insert a notification (idempotent via the unique dedupe index) and, if the
    user has Telegram linked, push it. Returns True if newly emitted."""
    try:
        _sb.table("notifications").insert({
            "user_id": user_id, "kind": kind, "title": title, "body": body,
            "ticker": ticker, "dedupe_key": dedupe_key,
        }).execute()
    except Exception:
        return False  # dedupe collision → already sent earlier
    if chat_id:
        emoji = _KIND_EMOJI.get(kind, "🔔")
        tk = f" · {ticker}" if ticker else ""
        tg_send(chat_id, f"{emoji} <b>{title}</b>{tk}" + (f"\n{body}" if body else ""))
    return True


def run_custom_reminders(today, settings):
    rows = (_sb.table("custom_reminders").select("*")
            .lte("fire_date", today.isoformat()).is_("sent_at", "null")
            .execute().data or [])
    fired = 0
    for rem in rows:
        s = settings.get(rem["user_id"], {})
        if not s.get("custom_reminders_enabled", True):
            continue
        ok = _emit(rem["user_id"], "custom", rem["text_body"],
                   ticker=rem.get("ticker"), dedupe_key=f"custom:{rem['id']}",
                   chat_id=s.get("telegram_chat_id"))
        (_sb.table("custom_reminders").update({"sent_at": _now_iso()})
         .eq("id", rem["id"]).execute())
        fired += int(ok)
    return fired


def main():
    link_telegram_chats()
    settings = _settings_by_user()
    fired = run_custom_reminders(date.today(), settings)
    print(f"[notify] custom reminders fired: {fired}")
    # Phase 2 hooks (broker-sourced, per-user TastyTrade OAuth):
    #   run_earnings_reminders(...) and run_price_alerts(...)


if __name__ == "__main__":
    main()
