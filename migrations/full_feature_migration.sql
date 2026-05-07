-- ═══════════════════════════════════════════════════
--  TradePattrnly — Full feature migration
--  Run in Supabase SQL Editor
-- ═══════════════════════════════════════════════════

-- Account setup / prop config
alter table accounts add column if not exists account_type    text default 'personal';  -- personal, prop
alter table accounts add column if not exists timezone        text default 'UTC';
alter table accounts add column if not exists setup_complete  boolean default false;
alter table accounts add column if not exists daily_profit_target  numeric(15,2);
alter table accounts add column if not exists daily_loss_cap       numeric(15,2);
alter table accounts add column if not exists risk_per_trade_pct   numeric(5,2) default 1.0;
alter table accounts add column if not exists prop_max_loss_per_trade  numeric(15,2);
alter table accounts add column if not exists prop_daily_max_loss      numeric(15,2);
alter table accounts add column if not exists prop_5day_max_loss       numeric(15,2);
alter table accounts add column if not exists prop_profit_cap          numeric(15,2);
alter table accounts add column if not exists prop_challenge_target    numeric(15,2);

-- Tenant subscription tracking
alter table tenants add column if not exists subscription_id   text;
alter table tenants add column if not exists subscription_status text default 'active';
alter table tenants add column if not exists analysis_count_week int default 0;
alter table tenants add column if not exists analysis_week_reset timestamptz default now();
alter table tenants add column if not exists trade_analysis_count int default 0;

-- Insights
create table if not exists insights (
    id            uuid primary key default uuid_generate_v4(),
    tenant_id     uuid references tenants(id) on delete cascade,
    account_id    uuid references accounts(id),
    analysis      text,
    patterns      jsonb,
    year_end_pnl  numeric(15,2),
    trade_count   int,
    generated_at  timestamptz default now()
);

-- News events
create table if not exists news_events (
    id            uuid primary key default uuid_generate_v4(),
    event_date    date not null,
    event_time    time,
    currency      text,
    impact        text,  -- High, Medium, Low
    title         text,
    actual        text,
    forecast      text,
    previous      text,
    fetched_at    timestamptz default now()
);
create index if not exists idx_news_date on news_events(event_date, impact);

-- Alerts
create table if not exists alerts (
    id            uuid primary key default uuid_generate_v4(),
    tenant_id     uuid references tenants(id) on delete cascade,
    account_id    uuid references accounts(id),
    type          text,  -- REVENGE_TRADE, NEWS, DAILY_LOSS, DAILY_PROFIT, PROP_DRAWDOWN
    message       text,
    data          jsonb,
    read          boolean default false,
    created_at    timestamptz default now()
);
create index if not exists idx_alerts_tenant on alerts(tenant_id, read, created_at);

-- Journal columns (if not already added)
alter table trades add column if not exists notes              text;
alter table trades add column if not exists tags               jsonb default '[]';
alter table trades add column if not exists screenshot_entry   text;
alter table trades add column if not exists screenshot_exit    text;
alter table trades add column if not exists screenshot_h1_entry text;
alter table trades add column if not exists screenshot_h1_exit  text;
alter table trades add column if not exists ai_analysis        text;
alter table trades add column if not exists post_exit_tracked  boolean default false;
alter table trades add column if not exists post_exit_high     numeric(15,5);
alter table trades add column if not exists post_exit_low      numeric(15,5);
alter table trades add column if not exists exit_quality       text;
