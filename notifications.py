"""Notifications data access — per-user settings, custom reminders, and the
emitted-notifications feed that drives the in-app indicator.

Mirrors config_store conventions: every function takes the Supabase `client`
first and an optional `user_id` (falls back to the signed-in user). Tables are
created in the 2026-06-17 notifications migration; see
docs/superpowers/specs/2026-06-17-notifications-design.md.
"""
from __future__ import annotations

import secrets

from config_store import _get_user_id

_SETTINGS_DEFAULTS = {
    "telegram_chat_id": None,
    "telegram_link_token": None,
    "earnings_days_before": 3,
    "price_alerts_enabled": True,
    "earnings_alerts_enabled": True,
    "custom_reminders_enabled": True,
}


# ── Settings ────────────────────────────────────────────────────────────────

def get_settings(client, user_id=None):
    """Return the user's notification settings, merged over defaults."""
    user_id = user_id or _get_user_id(client)
    resp = (client.table("notification_settings")
            .select("*").eq("user_id", user_id).limit(1).execute())
    row = (resp.data or [None])[0]
    return {**_SETTINGS_DEFAULTS, **(row or {})}


def save_settings(client, settings, user_id=None):
    """Upsert a partial settings dict (only the keys provided are written)."""
    user_id = user_id or _get_user_id(client)
    allowed = set(_SETTINGS_DEFAULTS)
    row = {k: v for k, v in settings.items() if k in allowed}
    row["user_id"] = user_id
    client.table("notification_settings").upsert(row).execute()


def ensure_link_token(client, user_id=None):
    """Return a stable one-time Telegram link token, creating it if absent.
    Used to build the t.me/<bot>?start=<token> deep link so the bot can map the
    Telegram chat back to this user_id."""
    user_id = user_id or _get_user_id(client)
    s = get_settings(client, user_id)
    token = s.get("telegram_link_token")
    if not token:
        token = secrets.token_urlsafe(16)
        save_settings(client, {"telegram_link_token": token}, user_id)
    return token


def telegram_connected(client, user_id=None):
    return bool(get_settings(client, user_id).get("telegram_chat_id"))


# ── Custom reminders ──────────────────────────────────────────────────────────

def add_custom_reminder(client, fire_date, text_body, ticker=None, user_id=None):
    """Schedule a free-text reminder for a date. fire_date: 'YYYY-MM-DD' or date."""
    user_id = user_id or _get_user_id(client)
    client.table("custom_reminders").insert({
        "user_id": user_id,
        "ticker": (ticker or None),
        "fire_date": str(fire_date),
        "text_body": text_body,
    }).execute()


def list_custom_reminders(client, user_id=None, include_sent=False):
    """Reminders for the user, soonest first. Pending-only unless include_sent."""
    user_id = user_id or _get_user_id(client)
    q = (client.table("custom_reminders").select("*")
         .eq("user_id", user_id).order("fire_date"))
    if not include_sent:
        q = q.is_("sent_at", "null")
    return (q.execute().data or [])


def delete_custom_reminder(client, reminder_id, user_id=None):
    user_id = user_id or _get_user_id(client)
    (client.table("custom_reminders").delete()
     .eq("id", reminder_id).eq("user_id", user_id).execute())


# ── Per-ticker alert opt-in (price + earnings) ────────────────────────────────

def list_yes_tickers(client, user_id=None):
    """Watchlist tickers in the 'Yes' category with their per-ticker alert flag.
    Only these are eligible for price/earnings alerts. Returns
    [{ticker, enabled}] sorted by ticker (enabled defaults to True)."""
    user_id = user_id or _get_user_id(client)
    rows = (client.table("watchlist_configs").select("ticker, config")
            .eq("user_id", user_id).execute().data or [])
    out = []
    for r in rows:
        cfg = r.get("config") or {}
        if cfg.get("category") == "Yes":
            out.append({"ticker": r["ticker"], "enabled": cfg.get("notify", True) is not False})
    return sorted(out, key=lambda x: x["ticker"])


def set_ticker_alert(client, ticker, enabled, user_id=None):
    """Set the per-ticker alert opt-in flag (config['notify']). Reads the full
    config and writes it back so nothing else is lost."""
    user_id = user_id or _get_user_id(client)
    rows = (client.table("watchlist_configs").select("config")
            .eq("user_id", user_id).eq("ticker", ticker).limit(1).execute().data or [])
    if not rows:
        return
    cfg = rows[0].get("config") or {}
    cfg["notify"] = bool(enabled)
    (client.table("watchlist_configs").update({"config": cfg})
     .eq("user_id", user_id).eq("ticker", ticker).execute())


# ── Emitted notifications (in-app feed) ───────────────────────────────────────

def list_notifications(client, user_id=None, unread_only=False, limit=50):
    user_id = user_id or _get_user_id(client)
    q = (client.table("notifications").select("*")
         .eq("user_id", user_id).order("created_at", desc=True).limit(limit))
    if unread_only:
        q = q.is_("read_at", "null")
    return (q.execute().data or [])


def unread_count(client, user_id=None):
    """Count of unread notifications (drives the watchlist bell badge)."""
    user_id = user_id or _get_user_id(client)
    resp = (client.table("notifications").select("id", count="exact")
            .eq("user_id", user_id).is_("read_at", "null").execute())
    return resp.count or 0


def mark_all_read(client, user_id=None):
    from datetime import UTC, datetime
    user_id = user_id or _get_user_id(client)
    (client.table("notifications").update({"read_at": datetime.now(UTC).isoformat()})
     .eq("user_id", user_id).is_("read_at", "null").execute())


def mark_read(client, notification_id, user_id=None):
    from datetime import UTC, datetime
    user_id = user_id or _get_user_id(client)
    (client.table("notifications").update({"read_at": datetime.now(UTC).isoformat()})
     .eq("id", notification_id).eq("user_id", user_id).execute())
