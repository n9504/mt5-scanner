-- Run in Supabase SQL Editor
-- New tables: insights, news_events, alerts
-- New config columns for account setup + prop firm

-- ── CONFIG new columns ──
alter table configs add column if not exists account_type       text;
alter table configs add column if not exists daily_target       numeric(15,2);
alter table configs add column if not exists daily_loss_cap     numeric(15,2);
alter table configs add column if not exists risk_per_trade     numeric(5,2) default 1.0;
alter table configs add column if not exists firm_name          text;
alter table configs add column if not exists max_loss_trade     numeric(15,2);
alter table configs add column if not exists daily_max_loss     numeric(15,2);
alter table configs add column if not exists five_day_max_loss  numeric(15,2);
alter table configs add column if not exists profit_cap         numeric(15,2);
alter table configs add column if not exists challenge_target   numeric(15,2);
alter table configs add column if not exists timezone           text default '+00:00';

-- ── INSIGHTS ──
create table if not exists insights (
    id           uuid primary key default uuid_generate_v4(),
    tenant_id    uuid references tenants(id) on delete cascade,
    content      jsonb,
    trade_count  int,
    created_at   timestamptz default now()
);
create index if not exists idx_insights_tenant on insights(tenant_id, created_at desc);

-- ── NEWS EVENTS ──
create table if not exists news_events (
    id           uuid primary key default uuid_generate_v4(),
    event_date   date not null,
    event_time   text,
    currency     text,
    event_name   text,
    impact       text default 'High',
    forecast     text,
    previous     text,
    actual       text,
    created_at   timestamptz default now()
);
create index if not exists idx_news_date on news_events(event_date, currency);

-- ── ALERTS ──
create table if not exists alerts (
    id           uuid primary key default uuid_generate_v4(),
    tenant_id    uuid references tenants(id) on delete cascade,
    type         text not null,
    symbol       text,
    message      text,
    data         jsonb,
    read         boolean default false,
    created_at   timestamptz default now()
);
create index if not exists idx_alerts_tenant on alerts(tenant_id, read, created_at desc);

-- ── RLS ──
alter table insights    enable row level security;
alter table alerts      enable row level security;
alter table news_events disable row level security;

-- News is public (no tenant isolation needed)
-- Insights and alerts use service key so RLS won't block
