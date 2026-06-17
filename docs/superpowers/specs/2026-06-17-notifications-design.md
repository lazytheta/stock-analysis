# Notifications (Telegram + in-app) — Design

**Date:** 2026-06-17
**Status:** Approved — building Phase 1.

## Architecture change (2026-06-17, during build)

The app authenticates with the **anon key** (`auth.py`) — there is **no
service-role key in use**, so an external GitHub Actions Python worker can't read
across users (RLS blocks it). Re-architected the worker as a **Supabase Edge
Function** (`notify`) which gets `SUPABASE_SERVICE_ROLE_KEY` injected automatically
— no service key ever leaves Supabase. The function:
- **Webhook mode:** Telegram `setWebhook` → the function receives `/start <token>`
  updates and binds the chat to the user (instant linking).
- **Cron mode:** `pg_cron` + `pg_net` POST to the function URL hourly (guarded by a
  `cron_secret`); fires due custom reminders → `notifications` row + Telegram push.
- Bot token + cron secret live in a private `notify_config` table (RLS on, no
  policy → service-role only). The previous GitHub Actions + `notify_job.py`
  approach was removed.

## Phase 2 (done 2026-06-17) — price + earnings via Finnhub

TastyTrade was dropped as the cron source: quotes need DXLink websockets (heavy in
Deno) and per-user tokens, but **prices and earnings are public market data** — only
the buy-price threshold is per-user. The Edge Function uses a free **Finnhub** key
(in `notify_config.finnhub_api_key`):
- **Price alerts:** one `/quote` per unique watchlist ticker; fire when
  `price ≤ valuation_summary.buy_price`; dedupe `price:{user}:{ticker}`; **re-arm**
  by deleting the marker once price closes back above buy.
- **Earnings:** one `/calendar/earnings` range call (today→+14d), filtered to
  watchlist tickers; fire when within the user's `earnings_days_before` (default 3);
  dedupe `earnings:{user}:{ticker}:{date}` (once per event).
Verified live: priceFired=6 on first run (deep-value names below buy), earnings
calendar returns data (PEP 2026-07-09, ABT 07-15, NFLX 07-16 — fire ~3 days prior).

## Decisions (review 2026-06-17)
1. **Scheduler:** Supabase **pg_cron + Edge Function** (data-local, multi-user; the
   Edge Function carries service-role automatically — no external key needed).
2. **Price source (Phase 2):** **TastyTrade quotes** via the Vercel MCP. Caveat —
   needs per-user TT OAuth; IBKR-only / unconnected users get no price alerts, so
   keep stored-price (or a free API) as a multi-user fallback. Resolve in Phase 2.
3. **Phasing:** **Phase 1 first** — custom reminders + earnings reminders + in-app
   indicator + Telegram bot/linking. Price alerts in Phase 2.
4. **Telegram bot:** user creates it via @BotFather and supplies the token.
**Topic:** Push notifications via Telegram + an in-app watchlist indicator, plus
user-defined custom reminders (free text on a chosen date).

## Context & constraint

Streamlit Cloud runs the app **only while a user has it open** — there is no
background process. So any "fires even when the app is closed" notification needs
a **separate scheduled job**. Data already lives in Supabase (`watchlist_configs`
per `user_id`), which the job can read.

**Scope (per user request):**
1. **Buy-price / price alerts** — ticker drops below its `buy_price` (or hits an
   upside threshold).
2. **Earnings reminders** — N days before a ticker's earnings date.
3. **Custom reminders** — user-set free text on a chosen date.
4. **In-app indicator** — a subtle marker in the watchlist (not a full banner)
   showing there's an active/triggered notification.
- **Channel:** Telegram bot (+ the in-app indicator).

## ⚠️ Key dependency: a server-side price source

Price alerts need *current* prices in the scheduled job. The app uses **yfinance,
which is rate-limited (429) on cloud IPs** (see open issue #2). A cron on Supabase
/ Cloud Run / GitHub Actions hits the same wall. Options:

| Source | Notes |
|--------|-------|
| **Free quote API key** (Finnhub / FMP / Alpha Vantage) | Reliable server-side, free tier ~enough for a daily/30-min check. **Recommended.** |
| **Reuse TastyTrade quotes** (Vercel MCP `get_quotes`) | Works server-side, but per-user OAuth + broker dependency |
| **Stored `stock_price`** from the last app session | No new infra, but stale between visits — only OK for a coarse check |

Earnings dates and custom reminders have **no** live-data dependency, so they can
ship first.

## Architecture

```
Scheduler (pg_cron / GitHub Actions / Cloud Scheduler)
   → notify job (Python): read Supabase → evaluate triggers → dedupe
   → Telegram sendMessage (per user chat_id)
   → write a row to `notifications` (status) for the in-app indicator
Streamlit app: reads `notifications` for the signed-in user → shows the marker
```

**Scheduler choice (decision):**
- **Supabase pg_cron + Edge Function** — data-local, multi-user, no extra infra. Recommended.
- **GitHub Actions cron** — simplest/free, but secrets + Supabase round-trip from GH.
- **Cloud Scheduler → Cloud Run** — reuses existing Cloud Run.

## Supabase schema (new tables)

```sql
-- per-user channel + preferences
notification_settings (
  user_id uuid pk,
  telegram_chat_id text,            -- set via bot linking (below)
  earnings_days_before int default 3,
  price_alerts_enabled bool default true,
  earnings_alerts_enabled bool default true,
  quiet_hours ...                   -- optional
)

-- user-defined custom reminders
custom_reminders (
  id uuid pk, user_id uuid, ticker text null,
  fire_date date, text_body text,
  sent_at timestamptz null
)

-- emitted notifications (drives in-app indicator + dedupe)
notifications (
  id uuid pk, user_id uuid, kind text,   -- 'price'|'earnings'|'custom'|'verdict'
  ticker text null, title text, body text,
  created_at timestamptz, read_at timestamptz null,
  dedupe_key text                          -- e.g. 'price:AVGO:2026-06-17'
)
```

**Dedupe:** a `dedupe_key` unique per (user, trigger, ~day) prevents re-sending
the same alert every run. Price alert re-arms only after the condition clears.

## Telegram setup (one-time)

1. Create a bot via **@BotFather** → bot token (stored as a Supabase/Cloud Run secret).
2. **Per-user linking:** app shows a deep link `https://t.me/<bot>?start=<token>`;
   the user taps "Start", the bot webhook (or the cron's getUpdates) captures their
   `chat_id` and writes it to `notification_settings`. A "Connect Telegram" button
   in the app settings drives this.
3. Sending: `POST api.telegram.org/bot<token>/sendMessage {chat_id, text}`.

## In-app indicator (watchlist)

- A small **dot/badge** (count of unread `notifications`) near the watchlist title,
  and a **per-row marker** (e.g. a 🔔 / coloured dot in the leftmost column) on
  tickers with an unread alert. Click → a compact popover listing the messages with
  a "mark read" action. No full banner.
- Reads `notifications` filtered by `user_id`, `read_at is null`.
- Custom-reminder setup: a small form ("text" + "date" + optional ticker) on the
  detail page or a Settings section → inserts into `custom_reminders`.

## Triggers (in the notify job)

- **Custom:** `fire_date <= today AND sent_at IS NULL` → send, stamp `sent_at`.
- **Earnings:** earnings_date − today ≤ `earnings_days_before` → send (dedupe per ticker/quarter).
- **Price:** `live_price <= buy_price` (or upside ≥ threshold) → send, re-arm on clear.
- (Later) **Verdict change / staleness.**

## Phasing

- **Phase 1 (no live-data dep):** schema + Telegram bot + linking + **custom
  reminders** + **earnings reminders** + in-app indicator. Fully shippable.
- **Phase 2:** **price alerts** — after the price-source decision (free API key
  recommended). Also fixes open issue #2's spirit (a reliable server-side feed).

## Open decisions for review

1. **Scheduler:** Supabase pg_cron (recommended) vs GitHub Actions vs Cloud Scheduler→Cloud Run.
2. **Price source for Phase 2:** free API key (Finnhub/FMP) vs TastyTrade quotes vs stored price.
3. **Cadence:** how often the job runs (e.g. daily 08:00 + intraday every 30 min for price).
4. **Phasing:** ship Phase 1 first (custom + earnings + in-app), then price?
5. **Telegram bot:** you create it via BotFather and hand me the token (stored as a secret).

## Effort (rough)

- Phase 1: ~1–1.5 day (schema, bot linking, cron for custom+earnings, in-app indicator).
- Phase 2 (price): ~0.5 day once the price source is chosen.
