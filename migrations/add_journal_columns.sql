-- Run in Supabase SQL Editor
-- Adds journal columns, H1 screenshots, and post-exit tracking

-- Journal columns (if not already added)
alter table trades add column if not exists notes              text;
alter table trades add column if not exists tags               jsonb  default '[]';
alter table trades add column if not exists screenshot_entry   text;
alter table trades add column if not exists screenshot_exit    text;
alter table trades add column if not exists ai_analysis        text;

-- H1 screenshot columns
alter table trades add column if not exists screenshot_h1_entry text;
alter table trades add column if not exists screenshot_h1_exit  text;

-- Post-exit tracking
alter table trades add column if not exists post_exit_tracked   boolean default false;
alter table trades add column if not exists post_exit_high      numeric(15,5);
alter table trades add column if not exists post_exit_low       numeric(15,5);
alter table trades add column if not exists post_exit_time      timestamptz;
alter table trades add column if not exists exit_quality        text;  -- PERFECT, EARLY, LATE

-- Index for post-exit tracking query
create index if not exists idx_trades_post_exit 
  on trades(tenant_id, status, post_exit_tracked, close_time)
  where status = 'CLOSED' and post_exit_tracked = false;
