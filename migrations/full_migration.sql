-- ═══════════════════════════════════════
--  TradePattrnly — Full Migration
--  Run in Supabase SQL Editor
-- ═══════════════════════════════════════

-- Account setup / profile
alter table accounts add column if not exists account_type      text default 'personal';  -- personal, prop
alter table accounts add column if not exists timezone          text default 'UTC';
alter table accounts add column if not exists setup_complete    boolean default false;

-- Personal account rules
alter table accounts add column if not exists daily_profit_target  numeric(10,2);
alter table accounts add column if not exists daily_loss_cap       numeric(10,2);
alter table accounts add column if not exists risk_per_trade_pct   numeric(5,2) default 1.0;

-- Prop firm rules
alter table accounts add column if not exists prop_max_loss_per_trade  numeric(10,2);
alter table accounts add column if not exists prop_daily_max_loss      numeric(10,2);
alter table accounts add column if not exists prop_5day_max_loss       numeric(10,2);
alter table accounts add column if not exists prop_profit_cap          numeric(10,2);
alter table accounts add column if not exists prop_challenge_target    numeric(10,2);
alter table accounts add column if not exists prop_firm_name           text;

-- Tenant: weekly insights tracking
alter table tenants add column if not exists last_insights_run    timestamptz;
alter table tenants add column if not exists insights_run_count   int default 0;
alter table tenants add column if not exists subscription         text default 'free';

-- News events table
create table if not exists news_events (
    id           uuid primary key default uuid_generate_v4(),
    event_name   text not null,
    currency     text not null,
    impact       text not null,  -- High, Medium, Low
    event_time   timestamptz not null,
    actual       text,
    forecast     text,
    previous     text,
    fetched_date date default current_date,
    created_at   timestamptz default now()
);
create index if not exists idx_news_time on news_events(event_time);
create index if not exists idx_news_date on news_events(fetched_date);

-- Alerts table
create table if not exists alerts (
    id           uuid primary key default uuid_generate_v4(),
    tenant_id    uuid references tenants(id) on delete cascade,
    account_id   uuid references accounts(id),
    alert_type   text not null,  -- NEWS, REVENGE_TRADE, DAILY_LOSS, DAILY_PROFIT, PROP_LIMIT, LOT_SPIKE
    message      text not null,
    data         jsonb,
    seen         boolean default false,
    sent_to_mt5  boolean default false,
    created_at   timestamptz default now()
);
create index if not exists idx_alerts_tenant on alerts(tenant_id, seen, created_at desc);

-- Insights table
create table if not exists insights (
    id              uuid primary key default uuid_generate_v4(),
    tenant_id       uuid references tenants(id) on delete cascade,
    account_id      uuid references accounts(id),
    analysis        text,
    patterns        jsonb,
    year_end_projection numeric(10,2),
    behaviour_flags jsonb,
    trade_count     int,
    period_start    date,
    period_end      date,
    created_at      timestamptz default now()
);

-- Trade entry reasoning (for AI analysis)
alter table trades add column if not exists entry_reasoning text;

-- RLS
alter table news_events disable row level security;
alter table alerts     enable row level security;
alter table insights   enable row level security;

create policy if not exists "tenant_alerts" on alerts
    for all using (tenant_id = (
        select id from tenants where id::text = current_setting('request.jwt.claims', true)::json->>'sub'
    ));

create policy if not exists "tenant_insights" on insights
    for all using (tenant_id = (
        select id from tenants where id::text = current_setting('request.jwt.claims', true)::json->>'sub'
    ));
