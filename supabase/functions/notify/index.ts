// LazyTheta notifications worker (Supabase Edge Function).
//
// Deployed to project dacmqkjvofqqjfsfrtlp as function `notify` (verify_jwt=false:
// it does its own auth). Two modes:
//   • Webhook mode  — Telegram POSTs updates; `/start <token>` links the chat to
//     the user (notification_settings.telegram_link_token → telegram_chat_id).
//   • Cron mode     — pg_cron (`notify-hourly`) POSTs `?secret=<cron_secret>`; fires
//     due custom reminders, buy-price alerts, and earnings reminders.
//
// Secrets live in the private notify_config table (service-role only):
//   telegram_bot_token, cron_secret, finnhub_api_key.
// Service-role key is injected automatically into Edge Functions.
// Source of truth for this function; redeploy with the Supabase MCP / CLI.
//
// See docs/superpowers/specs/2026-06-17-notifications-design.md
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const sb = createClient(
  Deno.env.get("SUPABASE_URL")!,
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
);

async function tgSend(token: string, chatId: string | number, text: string) {
  try {
    await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML" }),
    });
  } catch (e) {
    console.error("tgSend failed", e);
  }
}

function isoToday(offsetDays = 0): string {
  return new Date(Date.now() + offsetDays * 86400000).toISOString().slice(0, 10);
}

Deno.serve(async (req: Request) => {
  const { data: cfg } = await sb.from("notify_config").select("*").eq("id", 1).single();
  if (!cfg) return new Response("no config", { status: 500 });
  const token: string = cfg.telegram_bot_token;

  let body: any = null;
  try { body = await req.json(); } catch { /* no body */ }

  // ── Webhook mode: Telegram update ──
  if (body && body.message) {
    const msg = body.message;
    const chatId = msg.chat?.id;
    const text: string = (msg.text || "").trim();
    if (chatId && text.startsWith("/start")) {
      const tok = text.split(/\s+/)[1] || "";
      if (tok) {
        const { data: found } = await sb.from("notification_settings")
          .select("user_id").eq("telegram_link_token", tok).limit(1);
        if (found && found.length) {
          await sb.from("notification_settings")
            .update({ telegram_chat_id: String(chatId), updated_at: new Date().toISOString() })
            .eq("user_id", found[0].user_id);
          await tgSend(token, chatId, "✅ Linked to your LazyTheta watchlist. You'll get your alerts here.");
        } else {
          await tgSend(token, chatId, "This link has expired — reopen Connect Telegram in LazyTheta for a fresh one.");
        }
      } else {
        await tgSend(token, chatId, "Open LazyTheta → Watchlist → Notifications → Connect Telegram to link this chat.");
      }
    }
    return new Response("ok");
  }

  // ── Cron mode (shared secret) ──
  const url = new URL(req.url);
  if (url.searchParams.get("secret") !== cfg.cron_secret) {
    return new Response("forbidden", { status: 403 });
  }

  const { data: setRows } = await sb.from("notification_settings").select("*");
  const settings: Record<string, any> = {};
  for (const r of setRows || []) settings[r.user_id] = r;

  // 1) Due custom reminders
  let fired = 0;
  {
    const today = isoToday();
    const { data: rems } = await sb.from("custom_reminders").select("*")
      .lte("fire_date", today).is("sent_at", null);
    for (const rem of rems || []) {
      const s = settings[rem.user_id] || {};
      if (s.custom_reminders_enabled === false) continue;
      const { error } = await sb.from("notifications").insert({
        user_id: rem.user_id, kind: "custom", title: rem.text_body,
        ticker: rem.ticker, dedupe_key: `custom:${rem.id}`,
      });
      if (!error && s.telegram_chat_id) {
        const tk = rem.ticker ? ` · ${rem.ticker}` : "";
        await tgSend(token, s.telegram_chat_id, `📅 <b>${rem.text_body}</b>${tk}`);
        fired++;
      }
      await sb.from("custom_reminders").update({ sent_at: new Date().toISOString() }).eq("id", rem.id);
    }
  }

  // 2) Price + earnings alerts (Finnhub; prices/earnings are public market data)
  let priceFired = 0, earnFired = 0;
  const finnhub: string | null = cfg.finnhub_api_key;
  if (finnhub) {
    const { data: wl } = await sb.from("watchlist_configs").select("user_id, ticker, config");
    const rows = wl || [];
    const tickers = [...new Set(rows.map((r: any) => r.ticker))];

    // Quotes (one /quote call per unique ticker; skip on error/limit)
    const quote: Record<string, number> = {};
    for (const t of tickers) {
      try {
        const r = await fetch(`https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(t)}&token=${finnhub}`);
        if (r.ok) {
          const j = await r.json();
          if (typeof j.c === "number" && j.c > 0) quote[t] = j.c;
        }
      } catch (_) { /* skip this ticker this run */ }
    }
    for (const row of rows) {
      const s = settings[row.user_id] || {};
      if (s.price_alerts_enabled === false) continue;
      const buy = row.config?.valuation_summary?.buy_price;
      const px = quote[row.ticker];
      if (typeof buy !== "number" || !px) continue;
      const key = `price:${row.user_id}:${row.ticker}`;
      if (px <= buy) {
        const { error } = await sb.from("notifications").insert({
          user_id: row.user_id, kind: "price", ticker: row.ticker,
          title: `${row.ticker} hit your buy price`,
          body: `$${px.toFixed(2)} ≤ buy $${buy.toFixed(2)}`, dedupe_key: key,
        });
        if (!error && s.telegram_chat_id) {
          await tgSend(token, s.telegram_chat_id,
            `🔔 <b>${row.ticker} hit your buy price</b>\n$${px.toFixed(2)} ≤ buy $${buy.toFixed(2)}`);
          priceFired++;
        }
      } else {
        // re-arm: clear the marker so it can fire again on the next dip
        await sb.from("notifications").delete().eq("user_id", row.user_id).eq("dedupe_key", key);
      }
    }

    // Earnings calendar (single range call, filter to our tickers)
    const today = isoToday();
    const earnDate: Record<string, string> = {};
    try {
      const r = await fetch(`https://finnhub.io/api/v1/calendar/earnings?from=${today}&to=${isoToday(14)}&token=${finnhub}`);
      if (r.ok) {
        const j = await r.json();
        const tset = new Set(tickers);
        for (const e of j.earningsCalendar || []) {
          if (tset.has(e.symbol) && (!earnDate[e.symbol] || e.date < earnDate[e.symbol])) {
            earnDate[e.symbol] = e.date;
          }
        }
      }
    } catch (_) { /* skip earnings this run */ }
    for (const row of rows) {
      const s = settings[row.user_id] || {};
      if (s.earnings_alerts_enabled === false) continue;
      const d = earnDate[row.ticker];
      if (!d) continue;
      const days = Math.round((Date.parse(d + "T00:00:00Z") - Date.parse(today + "T00:00:00Z")) / 86400000);
      const before = s.earnings_days_before ?? 3;
      if (days < 0 || days > before) continue;
      const key = `earnings:${row.user_id}:${row.ticker}:${d}`;
      const label = days === 0 ? "today" : `in ${days}d`;
      const { error } = await sb.from("notifications").insert({
        user_id: row.user_id, kind: "earnings", ticker: row.ticker,
        title: `${row.ticker} earnings ${label}`, body: `Reports ${d}`, dedupe_key: key,
      });
      if (!error && s.telegram_chat_id) {
        await tgSend(token, s.telegram_chat_id, `📊 <b>${row.ticker} earnings ${label}</b>\nReports ${d}`);
        earnFired++;
      }
    }
  }

  return new Response(JSON.stringify({ ok: true, fired, priceFired, earnFired }), {
    headers: { "content-type": "application/json" },
  });
});
