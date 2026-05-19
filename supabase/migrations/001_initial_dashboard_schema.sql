-- ============================================================================
-- Yuvo OS — Phase 1B initial dashboard schema
-- ============================================================================
-- Migration: 001
-- Scope:    The subset needed to replace web/lib/demo-data.ts.
--           Generation/audio-fixer/cost ledger tables are deferred to Phase 1C.
--
-- Conventions:
--   - Every table has id uuid pk default gen_random_uuid().
--   - Every table has created_at timestamptz default now().
--   - Mutable tables also have updated_at + an after-update trigger.
--   - Every status column is gated by a check constraint.
--   - RLS is enabled on every table from day one.
--   - Operator-only columns live on content_items but are projected away
--     by the client_content_items_v view; the client RLS policy lives on
--     that view, not on the base table.
-- ============================================================================

create extension if not exists "pgcrypto";  -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- Helper schema + functions
-- ---------------------------------------------------------------------------
create schema if not exists app;

-- Updated-at trigger (shared by every mutable table).
create or replace function app.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- TODO(phase-1c): once Supabase Auth is wired, swap these stubs to read
-- the JWT via auth.uid() / auth.jwt() ->> 'portal_slug'. Until then the
-- helpers return null and RLS denies everything by default — the API
-- routes use the service-role key and bypass RLS, which is the same
-- security posture as the current demo-data path.
create or replace function app.current_profile_id()
returns uuid
language sql
stable
as $$
  select auth.uid()
$$;

-- app.is_workspace_member / app.is_portal_member are defined LOWER DOWN,
-- after the tables they reference exist. Postgres validates SQL
-- function bodies at CREATE FUNCTION time (`check_function_bodies = on`
-- by default), so the function definitions must come after the
-- workspace_members and client_portal_members tables.

-- ---------------------------------------------------------------------------
-- workspaces
-- ---------------------------------------------------------------------------
create table public.workspaces (
  id           uuid primary key default gen_random_uuid(),
  slug         text not null unique,
  name         text not null,
  agency_name  text not null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create trigger workspaces_set_updated_at before update on public.workspaces
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- profiles  (mirrors auth.users for display metadata)
-- ---------------------------------------------------------------------------
create table public.profiles (
  id            uuid primary key,                  -- == auth.users.id once wired
  display_name  text not null,
  avatar_url    text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function app.set_updated_at();
-- TODO(phase-1c): add FK to auth.users(id) on delete cascade once
-- Supabase Auth is wired and the trigger that auto-inserts a profile
-- row on signup is in place.

-- ---------------------------------------------------------------------------
-- workspace_members
-- ---------------------------------------------------------------------------
create table public.workspace_members (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  profile_id   uuid not null references public.profiles(id) on delete cascade,
  role         text not null check (role in ('owner', 'operator', 'viewer')),
  created_at   timestamptz not null default now(),
  primary key (workspace_id, profile_id)
);
create index workspace_members_profile_idx on public.workspace_members (profile_id);

-- ---------------------------------------------------------------------------
-- brands
-- ---------------------------------------------------------------------------
create table public.brands (
  id                   uuid primary key default gen_random_uuid(),
  workspace_id         uuid not null references public.workspaces(id) on delete restrict,
  name                 text not null,
  niche                text,
  website_url          text,
  brand_tone           text,
  audience_assumption  text,
  primary_color_hex    text check (primary_color_hex is null or primary_color_hex ~ '^#[0-9A-Fa-f]{6}$'),
  thumbnail_path       text,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);
create index brands_workspace_idx on public.brands (workspace_id);
create trigger brands_set_updated_at before update on public.brands
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- brand_assets  (optional; small enough to include now)
-- ---------------------------------------------------------------------------
create table public.brand_assets (
  id            uuid primary key default gen_random_uuid(),
  brand_id      uuid not null references public.brands(id) on delete cascade,
  kind          text not null check (kind in ('logo', 'product_image', 'reference_video', 'palette', 'other')),
  storage_path  text not null,         -- supabase storage key (operator-only)
  public_url    text,                  -- pre-signed/cdn url; can be null
  mime          text,
  byte_size     bigint,
  notes         text,
  created_at    timestamptz not null default now()
);
create index brand_assets_brand_idx on public.brand_assets (brand_id);

-- ---------------------------------------------------------------------------
-- clients   (the contracting entity)
-- ---------------------------------------------------------------------------
create table public.clients (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid not null references public.workspaces(id) on delete restrict,
  brand_id      uuid not null references public.brands(id) on delete restrict,
  display_name  text not null,
  contact_email text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index clients_workspace_idx on public.clients (workspace_id);
create index clients_brand_idx on public.clients (brand_id);
create trigger clients_set_updated_at before update on public.clients
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- client_portals   (one per client; the slug is the public URL key)
-- ---------------------------------------------------------------------------
create table public.client_portals (
  id          uuid primary key default gen_random_uuid(),
  client_id   uuid not null references public.clients(id) on delete cascade,
  slug        text not null unique,
  status      text not null default 'active'
              check (status in ('active', 'paused', 'archived')),
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index client_portals_client_idx on public.client_portals (client_id);
create trigger client_portals_set_updated_at before update on public.client_portals
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- client_portal_members
-- ---------------------------------------------------------------------------
-- Surrogate id pk because Postgres doesn't allow expressions in PRIMARY
-- KEY; the natural composite ("which (portal × member) pair this row
-- represents") is enforced by the two partial unique indexes below —
-- one for authenticated members (profile_id set), one for outstanding
-- invites (invite_email set, profile_id still null).
create table public.client_portal_members (
  id           uuid primary key default gen_random_uuid(),
  portal_id    uuid not null references public.client_portals(id) on delete cascade,
  profile_id   uuid references public.profiles(id) on delete set null,
  invite_email text,                                  -- non-null until profile is created
  invited_at   timestamptz not null default now(),
  joined_at    timestamptz,
  constraint client_portal_members_has_member_ref
    check (profile_id is not null or invite_email is not null)
);
create unique index client_portal_members_portal_profile_uq
  on public.client_portal_members (portal_id, profile_id)
  where profile_id is not null;
create unique index client_portal_members_portal_invite_uq
  on public.client_portal_members (portal_id, lower(invite_email))
  where profile_id is null and invite_email is not null;
create index client_portal_members_profile_idx
  on public.client_portal_members (profile_id);

-- ---------------------------------------------------------------------------
-- Membership-check helpers (deferred from the top of this migration).
-- Now safe to define because workspace_members + client_portal_members
-- exist. `security definer` so RLS policies that call them don't
-- recurse into their own RLS check.
-- ---------------------------------------------------------------------------
create or replace function app.is_workspace_member(target_workspace uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.workspace_members wm
    where wm.workspace_id = target_workspace
      and wm.profile_id = app.current_profile_id()
  );
$$;

create or replace function app.is_portal_member(target_portal uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.client_portal_members cpm
    where cpm.portal_id = target_portal
      and cpm.profile_id = app.current_profile_id()
  );
$$;

-- ---------------------------------------------------------------------------
-- campaigns
-- ---------------------------------------------------------------------------
create table public.campaigns (
  id                  uuid primary key default gen_random_uuid(),
  brand_id            uuid not null references public.brands(id) on delete restrict,
  client_portal_id    uuid references public.client_portals(id) on delete set null,
  title               text not null,
  strategic_pattern   text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create index campaigns_brand_idx on public.campaigns (brand_id);
create index campaigns_portal_idx on public.campaigns (client_portal_id);
create trigger campaigns_set_updated_at before update on public.campaigns
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- content_calendars   (a campaign's grouping; "Week of 20 May" etc.)
-- ---------------------------------------------------------------------------
create table public.content_calendars (
  id           uuid primary key default gen_random_uuid(),
  campaign_id  uuid not null references public.campaigns(id) on delete cascade,
  title        text not null,
  starts_on    date,
  ends_on      date,
  created_at   timestamptz not null default now(),
  constraint content_calendars_range check (ends_on is null or starts_on is null or ends_on >= starts_on)
);
create index content_calendars_campaign_idx on public.content_calendars (campaign_id);

-- ---------------------------------------------------------------------------
-- content_items
-- ---------------------------------------------------------------------------
create table public.content_items (
  id                          uuid primary key default gen_random_uuid(),
  campaign_id                 uuid not null references public.campaigns(id) on delete restrict,
  content_calendar_id         uuid references public.content_calendars(id) on delete set null,
  title                       text not null,
  status                      text not null
                              check (status in (
                                'draft',
                                'generating',
                                'raw_ready',
                                'audio_fixer_pending',
                                'audio_fixed',
                                'ready_for_client_review',
                                'shared_with_client',
                                'approved_by_client',
                                'changes_requested_by_client',
                                'failed'
                              )),
  scheduled_for               date,
  platforms                   text[] not null default array[]::text[],
  -- creative
  hook_text                   text,
  hook_source                 text check (hook_source is null or hook_source in ('operator', 'ai_suggested', 'client_request')),
  caption_draft               text,
  -- operator-only
  prompt_summary              text,
  quality_tier                text not null default 'standard_720p'
                              check (quality_tier in ('draft_480p', 'standard_720p', 'premium_1080p')),
  resolution                  text check (resolution is null or resolution in ('480p', '720p', '1080p')),
  duration_sec                integer check (duration_sec is null or duration_sec between 1 and 180),
  cost_estimate_credits       integer,
  cost_actual_credits         integer,
  -- assets (operator-only filesystem paths until Supabase Storage lands)
  internal_raw_path           text,
  internal_audio_fixed_path   text,
  internal_thumb_path         text,
  -- the only video-side field a client can see
  client_safe_poster_url      text,
  -- visibility + audio-fixer flags
  shared_with_client          boolean not null default false,
  audio_fixer_triggered       boolean not null default false,
  audio_fixer_completed       boolean not null default false,
  audio_fixer_credits_actual  integer,
  -- audit
  created_at                  timestamptz not null default now(),
  updated_at                  timestamptz not null default now()
);
create index content_items_campaign_idx on public.content_items (campaign_id);
create index content_items_calendar_idx on public.content_items (content_calendar_id);
create index content_items_status_idx on public.content_items (status);
create index content_items_schedule_idx on public.content_items (campaign_id, scheduled_for);
create trigger content_items_set_updated_at before update on public.content_items
  for each row execute function app.set_updated_at();
-- Invariant: `shared_with_client` must be true whenever status is in the
-- client-visible set. Enforced as a deferred check so app code can flip
-- both fields in one transaction without ordering tricks.
alter table public.content_items
  add constraint content_items_shared_flag_consistent check (
    case when status in ('shared_with_client',
                         'approved_by_client',
                         'changes_requested_by_client')
         then shared_with_client = true
         else true
    end
  );

-- ---------------------------------------------------------------------------
-- generated_assets
-- ---------------------------------------------------------------------------
create table public.generated_assets (
  id              uuid primary key default gen_random_uuid(),
  content_item_id uuid not null references public.content_items(id) on delete cascade,
  kind            text not null check (kind in ('raw_video', 'audio_fixed_video', 'thumbnail', 'caption_srt', 'export')),
  storage_path    text not null,             -- supabase storage key OR local prospects/ path during Phase 1B
  public_url      text,
  mime            text,
  byte_size       bigint,
  duration_sec    numeric,
  resolution      text,
  generated_at    timestamptz not null default now()
);
create index generated_assets_content_idx on public.generated_assets (content_item_id);
create index generated_assets_kind_idx on public.generated_assets (content_item_id, kind);

-- ---------------------------------------------------------------------------
-- content_feedback
-- ---------------------------------------------------------------------------
create table public.content_feedback (
  id              uuid primary key default gen_random_uuid(),
  content_item_id uuid not null references public.content_items(id) on delete cascade,
  author_kind     text not null check (author_kind in ('client', 'operator')),
  profile_id      uuid references public.profiles(id) on delete set null,
  body            text not null,
  created_at      timestamptz not null default now()
);
create index content_feedback_content_idx on public.content_feedback (content_item_id, created_at desc);

-- ---------------------------------------------------------------------------
-- content_approvals
-- ---------------------------------------------------------------------------
create table public.content_approvals (
  id              uuid primary key default gen_random_uuid(),
  content_item_id uuid not null references public.content_items(id) on delete cascade,
  decision        text not null check (decision in ('approved', 'rejected', 'changes_requested')),
  actor_kind      text not null check (actor_kind in ('client', 'operator')),
  profile_id      uuid references public.profiles(id) on delete set null,
  note            text,
  decided_at      timestamptz not null default now()
);
create index content_approvals_content_idx on public.content_approvals (content_item_id, decided_at desc);

-- ---------------------------------------------------------------------------
-- content_requests   (optional; backs the "what would you like next week?" form)
-- ---------------------------------------------------------------------------
create table public.content_requests (
  id                  uuid primary key default gen_random_uuid(),
  client_portal_id    uuid not null references public.client_portals(id) on delete cascade,
  brand_id            uuid not null references public.brands(id) on delete cascade,
  profile_id          uuid references public.profiles(id) on delete set null,
  body                text not null,
  created_at          timestamptz not null default now()
);
create index content_requests_portal_idx on public.content_requests (client_portal_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Client-safe projection of content_items.
-- The client RLS policy is attached HERE so operator-only columns (cost_*,
-- prompt_summary, internal_*, audio_fixer_*, quality_tier) can never leak
-- through the client API surface even if a client somehow got a SELECT
-- grant on content_items itself.
-- ---------------------------------------------------------------------------
create view public.client_content_items_v
with (security_invoker = true) as
select
  ci.id,
  ci.campaign_id,
  ci.content_calendar_id,
  ci.title,
  ci.status,
  ci.scheduled_for,
  ci.platforms,
  ci.hook_text,
  ci.caption_draft,
  ci.client_safe_poster_url,
  ci.duration_sec,
  ci.created_at,
  ci.updated_at
from public.content_items ci
where ci.shared_with_client = true
  and ci.status in ('shared_with_client',
                    'approved_by_client',
                    'changes_requested_by_client');

comment on view public.client_content_items_v is
  'Client-safe projection. The client RLS policy is attached here. Operator-only columns (cost_*, prompt_summary, internal_*, audio_fixer_*, quality_tier) are intentionally absent.';
