-- Shared earnings-surprise history (no user_id). Written daily by the
-- price-history Cloud Run Job with the service key (upsert, never delete, so
-- quarters older than Nasdaq's four-quarter window accumulate); read by any
-- logged-in user.
create table if not exists public.earnings_history (
  ticker text not null,
  fiscal_qtr_end date not null,
  date_reported date,
  eps numeric,
  eps_consensus numeric,
  surprise_pct numeric,
  source text not null default 'nasdaq',
  updated_at timestamptz not null default now(),
  primary key (ticker, fiscal_qtr_end)
);
alter table public.earnings_history enable row level security;
drop policy if exists earnings_history_read on public.earnings_history;
create policy earnings_history_read on public.earnings_history
  for select to authenticated using (true);
