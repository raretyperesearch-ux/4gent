-- ============================================================
-- 4GENT SUPABASE SCHEMA
-- ============================================================

-- ── Extensions ──────────────────────────────────────────────
create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";


-- ============================================================
-- AGENTS
-- Core table. One row per deployed agent.
-- ============================================================
create table agents (
  id                  uuid primary key default uuid_generate_v4(),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  -- Identity
  name                text not null,
  ticker              text not null,
  archetype           text not null check (archetype in ('degen','analyst','narrator','schemer','researcher','custom')),
  prompt              text not null,
  image_url           text not null,

  -- Owner (connected wallet from wizard)
  owner_wallet        text not null,
  claim_code          text unique,                    -- one-time code to DM @4GentBot
  claim_code_expires  timestamptz,
  owner_claimed       boolean not null default false,

  -- Agent wallet (managed by 4Gent, used to sign txs)
  agent_wallet        text unique,
  agent_wallet_enc    text,                           -- encrypted private key (Railway env key used to decrypt)

  -- ERC-8004 registration
  erc8004_nft_id      text,
  erc8004_tx_hash     text,
  erc8004_registered  boolean not null default false,

  -- Token
  token_address       text unique,
  token_tx_hash       text,
  token_deployed      boolean not null default false,

  -- Telegram
  tg_channel_link     text not null,
  tg_channel_id       text,                           -- resolved numeric channel ID
  tg_bot_id           uuid references bot_pool(id),
  tg_verified         boolean not null default false,

  -- Trading config
  trading_enabled     boolean not null default false,
  max_trade_bnb       numeric(10,4) not null default 0.1,
  daily_limit_bnb     numeric(10,4) not null default 1.0,
  stop_loss_pct       numeric(5,2) not null default 50.0,
  daily_spent_bnb     numeric(10,4) not null default 0,
  raise_amount_bnb    numeric(10,4) not null default 0,  -- BNB seeded by creator at launch
  daily_reset_at      timestamptz,

  -- Runtime state
  status              text not null default 'pending'
                        check (status in ('pending','preparing','awaiting_tx','launching','active','paused','error','deleted')),
  error_message       text,
  last_active_at      timestamptz,

  -- OpenFang integration
  openfang_id         text,                           -- OpenFang agent instance ID

  -- Stats
  total_posts         integer not null default 0,
  total_trades        integer not null default 0,
  total_fees_bnb      numeric(16,8) not null default 0
);

create index agents_owner_wallet_idx on agents(owner_wallet);
create index agents_status_idx on agents(status);
create index agents_token_address_idx on agents(token_address);


-- ============================================================
-- BOT POOL
-- Pre-created Telegram bots available for assignment.
-- ============================================================
create table bot_pool (
  id                  uuid primary key default uuid_generate_v4(),
  created_at          timestamptz not null default now(),

  bot_username        text not null unique,           -- e.g. 4GentAgent7Bot
  bot_token           text not null unique,           -- encrypted Telegram bot token
  assigned_agent_id   uuid references agents(id),
  assigned_at         timestamptz,

  -- Status
  available           boolean not null default true
);

create index bot_pool_available_idx on bot_pool(available);


-- ============================================================
-- AGENT POSTS
-- Log of every post sent to a Telegram channel.
-- ============================================================
create table agent_posts (
  id                  uuid primary key default uuid_generate_v4(),
  created_at          timestamptz not null default now(),

  agent_id            uuid not null references agents(id) on delete cascade,
  post_type           text not null check (post_type in ('intro','call','analysis','update','custom')),
  content             text not null,
  token_ref           text,                           -- token address this post is about (if any)
  tg_message_id       text,                           -- Telegram message ID for edits/deletes
  posted              boolean not null default false
);

create index agent_posts_agent_id_idx on agent_posts(agent_id);
create index agent_posts_created_at_idx on agent_posts(created_at desc);


-- ============================================================
-- AGENT TRADES
-- Log of every trade executed by an autonomous trading agent.
-- ============================================================
create table agent_trades (
  id                  uuid primary key default uuid_generate_v4(),
  created_at          timestamptz not null default now(),

  agent_id            uuid not null references agents(id) on delete cascade,
  token_address       text not null,
  token_name          text,
  token_symbol        text,

  direction           text not null check (direction in ('buy','sell')),
  amount_bnb          numeric(10,4) not null,
  tx_hash             text unique,
  success             boolean not null default false,
  error_message       text,

  -- Price at time of trade
  price_usd           numeric(20,8),
  slippage_pct        numeric(5,2)
);

create index agent_trades_agent_id_idx on agent_trades(agent_id);
create index agent_trades_created_at_idx on agent_trades(created_at desc);


-- ============================================================
-- SEEN TOKENS
-- Per-agent dedup table. Prevents calling the same token twice.
-- ============================================================
create table seen_tokens (
  id                  uuid primary key default uuid_generate_v4(),
  agent_id            uuid not null references agents(id) on delete cascade,
  token_address       text not null,
  seen_at             timestamptz not null default now(),
  acted               boolean not null default false, -- did agent post/trade on this?

  unique(agent_id, token_address)
);

create index seen_tokens_agent_idx on seen_tokens(agent_id, token_address);
create index seen_tokens_seen_at_idx on seen_tokens(seen_at desc);  -- Q-08: time-based queries


-- ============================================================
-- FEES
-- Tracks 3% tax accumulation per agent token.
-- Payouts happen daily via fees.py (not yet built).
-- ============================================================
create table fee_records (
  id                  uuid primary key default uuid_generate_v4(),
  created_at          timestamptz not null default now(),

  agent_id            uuid not null references agents(id) on delete cascade,
  token_address       text not null,
  tx_hash             text not null,

  -- Fee split
  gross_bnb           numeric(16,8) not null,
  platform_cut_bnb    numeric(16,8) not null,         -- 2% → 4Gent treasury
  owner_cut_bnb       numeric(16,8) not null,         -- 1% → agent owner

  paid_out            boolean not null default false,
  payout_tx_hash      text
);

create index fee_records_agent_id_idx on fee_records(agent_id);
create index fee_records_paid_out_idx on fee_records(paid_out) where paid_out = false;


-- ============================================================
-- OWNER COMMANDS LOG
-- Audit trail of /pause /resume /updateprompt etc via @4GentBot
-- ============================================================
create table owner_commands (
  id                  uuid primary key default uuid_generate_v4(),
  created_at          timestamptz not null default now(),

  agent_id            uuid not null references agents(id) on delete cascade,
  command             text not null,                  -- /pause /resume /stats etc
  params              jsonb,
  tg_user_id          text not null,
  success             boolean not null default true
);


-- ============================================================
-- RLS — Row Level Security
-- Basic policies. Tighten before production.
-- ============================================================
alter table agents          enable row level security;
alter table bot_pool        enable row level security;
alter table agent_posts     enable row level security;
alter table agent_trades    enable row level security;
alter table seen_tokens     enable row level security;
alter table fee_records     enable row level security;
alter table owner_commands  enable row level security;

-- Service role bypasses all RLS (used by Railway backend)
-- Anon/public gets nothing by default


-- ============================================================
-- UPDATED_AT trigger
-- ============================================================
create or replace function update_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger agents_updated_at
  before update on agents
  for each row execute function update_updated_at();


-- ============================================================
-- HELPER VIEWS
-- ============================================================

-- Active agents with bot assignment
create view active_agents as
  select
    a.*,
    bp.bot_username,
    bp.bot_token
  from agents a
  left join bot_pool bp on bp.id = a.tg_bot_id
  where a.status = 'active';

-- Unpaid fees summary
create view pending_payouts as
  select
    f.agent_id,
    a.name as agent_name,
    a.owner_wallet,
    sum(f.owner_cut_bnb) as total_owner_bnb,
    sum(f.platform_cut_bnb) as total_platform_bnb,
    count(*) as fee_count
  from fee_records f
  join agents a on a.id = f.agent_id
  where f.paid_out = false
  group by f.agent_id, a.name, a.owner_wallet;


-- ============================================================
-- B-05 FIX: increment_agent_stat
-- Called by scheduler after every post and trade.
-- Uses dynamic column name to avoid separate functions per stat.
-- ============================================================
CREATE OR REPLACE FUNCTION increment_agent_stat(
    p_agent_id uuid,
    p_column   text
) RETURNS void AS $$
BEGIN
    -- Whitelist allowed columns to prevent SQL injection
    IF p_column NOT IN ('total_posts', 'total_trades') THEN
        RAISE EXCEPTION 'increment_agent_stat: column % not allowed', p_column;
    END IF;
    EXECUTE format(
        'UPDATE agents SET %I = %I + 1 WHERE id = $1',
        p_column, p_column
    ) USING p_agent_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ============================================================
-- B-14 FIX: claim_bot_from_pool
-- Atomic bot assignment using FOR UPDATE SKIP LOCKED.
-- Prevents TOCTOU race between concurrent launches.
-- ============================================================
CREATE OR REPLACE FUNCTION claim_bot_from_pool(
    p_agent_id uuid
) RETURNS TABLE(id uuid, bot_username text, bot_token text) AS $$
DECLARE
    v_bot_id uuid;
BEGIN
    -- Lock and claim in one atomic operation
    UPDATE bot_pool bp
    SET
        available         = false,
        assigned_agent_id = p_agent_id,
        assigned_at       = now()
    WHERE bp.id = (
        SELECT b.id
        FROM   bot_pool b
        WHERE  b.available = true
          AND  b.assigned_agent_id IS NULL
        LIMIT  1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING bp.id INTO v_bot_id;

    IF v_bot_id IS NULL THEN
        RETURN; -- pool exhausted, returns empty result set
    END IF;

    RETURN QUERY
        SELECT bp.id, bp.bot_username, bp.bot_token
        FROM   bot_pool bp
        WHERE  bp.id = v_bot_id;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;


-- ============================================================
-- MIGRATION: two-phase launch flow
-- Add 'preparing' and 'awaiting_tx' to status enum
-- ============================================================
ALTER TABLE agents DROP CONSTRAINT IF EXISTS agents_status_check;
ALTER TABLE agents ADD CONSTRAINT agents_status_check
  CHECK (status IN ('pending','preparing','awaiting_tx','launching','active','paused','error','deleted'));
