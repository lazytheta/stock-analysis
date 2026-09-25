-- Shared daily closes (no user_id). Written by the price-history Cloud Run Job
-- with the service key; read by any logged-in user.
create table if not exists public.price_history (
  ticker text not null,
  day date not null,
  close numeric not null,
  primary key (ticker, day)
);
alter table public.price_history enable row level security;
drop policy if exists price_history_read on public.price_history;
create policy price_history_read on public.price_history
  for select to authenticated using (true);
