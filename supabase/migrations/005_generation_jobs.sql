-- ============================================================================
-- Yuvo OS — Phase 1F generation job tracking (mock-only, no paid calls)
-- ============================================================================
-- Migration: 005
-- Scope:    Add the job-tracking tables Phase 1G will fill with real
--           Enhancor / Seedance calls. Phase 1F itself is DRY-RUN ONLY:
--           rows in these tables are created by the operator console as
--           records of intent. No provider HTTP call is ever made by
--           the application code that writes here.
--
-- Tables added:
--   1. generation_batches      — operator action ↔ N jobs
--   2. generation_jobs         — one prompt_version → one requested run
--   3. generation_job_events   — append-only timeline per job
--   4. audio_fixer_jobs        — optional manual Audio Fixer (never auto)
-- Existing table extended:
--   5. generated_assets        — three new kinds (static_image,
--                                caption_pack, post_creative) +
--                                nullable links to generation_jobs and
--                                audio_fixer_jobs
--
-- Helpers added (all SECURITY DEFINER, set search_path = public,
-- matching the Phase 1E pattern in app.workspace_id_for_content):
--   app.workspace_id_for_generation_batch(uuid)
--   app.workspace_id_for_generation_job(uuid)
--   app.workspace_id_for_audio_fixer_job(uuid)
-- These are created AFTER the tables so check_function_bodies is happy.
--
-- RLS posture (operator-only across the board):
--   - Operators with workspace membership: full SELECT + ALL on the four
--     new tables.
--   - Clients: NO policy at all. The four new tables are operator-side
--     accounting; the client surface never reads them.
--   - generated_assets: existing operator + client (thumbnail-only)
--     policies from migration 002 continue to apply unchanged. The new
--     kinds don't widen the client gate because that policy still
--     filters on kind = 'thumbnail'.
--
-- Phase 1F intentionally OMITS:
--   - Any provider HTTP call.
--   - Any auto-creation of audio_fixer_jobs. Audio Fixer is manual; the
--     job row only appears when the operator clicks "Run Audio Fixer"
--     in Phase 1G+.
--   - The content_items.active_prompt_version_id FK column that was a
--     Phase 1E TODO. The partial unique index on prompt_versions still
--     answers "what would I generate with right now?".
-- ============================================================================

-- ---------------------------------------------------------------------------
-- generation_batches
--
-- One operator action ("generate this approved prompt version") creates
-- exactly one batch with one job in Phase 1F. The batch row exists for
-- forward-compatibility: Phase 1G+ may fan out to multiple resolutions
-- or multiple platform variants from the same operator click.
-- ---------------------------------------------------------------------------
create table if not exists public.generation_batches (
  id                       uuid primary key default gen_random_uuid(),
  workspace_id             uuid not null references public.workspaces(id) on delete restrict,
  brand_id                 uuid not null references public.brands(id) on delete restrict,
  campaign_id              uuid not null references public.campaigns(id) on delete restrict,
  created_by               uuid references public.profiles(id) on delete set null,
  label                    text,
  status                   text not null default 'draft'
                           check (status in (
                             'draft',
                             'ready',
                             'queued',
                             'processing',
                             'completed',
                             'failed',
                             'cancelled'
                           )),
  total_estimated_credits  integer,
  total_actual_credits     integer,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);
create index if not exists generation_batches_workspace_idx
  on public.generation_batches (workspace_id, created_at desc);
create index if not exists generation_batches_campaign_idx
  on public.generation_batches (campaign_id, created_at desc);
create index if not exists generation_batches_status_idx
  on public.generation_batches (status, created_at desc);

drop trigger if exists generation_batches_set_updated_at on public.generation_batches;
create trigger generation_batches_set_updated_at
  before update on public.generation_batches
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- generation_jobs
--
-- One requested generation from one prompt_version. raw_asset_id is the
-- generated_assets row that holds the resulting raw video once Phase 1G
-- writes it. The FK is added lower down because generated_assets is
-- extended with backlink columns first.
-- ---------------------------------------------------------------------------
create table if not exists public.generation_jobs (
  id                    uuid primary key default gen_random_uuid(),
  batch_id              uuid not null references public.generation_batches(id) on delete cascade,
  content_item_id       uuid not null references public.content_items(id) on delete restrict,
  prompt_version_id     uuid not null references public.prompt_versions(id) on delete restrict,
  provider              text not null
                        check (provider in (
                          'enhancor_seedance',
                          'enhancor_audio_fixer',
                          'enhancor_other',
                          'mock'
                        )),
  provider_mode         text,
  quality_tier          text not null
                        check (quality_tier in (
                          'draft_480p',
                          'standard_720p',
                          'premium_1080p'
                        )),
  resolution            text check (resolution is null or resolution in ('480p', '720p', '1080p')),
  duration_seconds      integer check (duration_seconds is null or duration_seconds between 1 and 180),
  status                text not null default 'draft'
                        check (status in (
                          'draft',
                          'queued',
                          'submitted',
                          'processing',
                          'completed',
                          'failed',
                          'cancelled'
                        )),
  estimated_credits     integer,
  actual_credits        integer,
  provider_request_id   text,
  result_url            text,
  thumbnail_url         text,
  raw_asset_id          uuid,                            -- FK added below after generated_assets is extended
  error_message         text,
  raw_request_json      jsonb,
  raw_response_json     jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index if not exists generation_jobs_batch_idx
  on public.generation_jobs (batch_id, created_at desc);
create index if not exists generation_jobs_content_idx
  on public.generation_jobs (content_item_id, created_at desc);
create index if not exists generation_jobs_prompt_version_idx
  on public.generation_jobs (prompt_version_id, created_at desc);
create index if not exists generation_jobs_status_idx
  on public.generation_jobs (status, created_at desc);
create index if not exists generation_jobs_provider_request_idx
  on public.generation_jobs (provider_request_id) where provider_request_id is not null;

drop trigger if exists generation_jobs_set_updated_at on public.generation_jobs;
create trigger generation_jobs_set_updated_at
  before update on public.generation_jobs
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- generation_job_events  (append-only timeline)
--
-- Every state transition + every operator note is written here. Reads
-- are operator-only; writes are operator-only. The data layer treats
-- this as a write-once log — there is no update or delete pathway in
-- the Phase 1F actions.
-- ---------------------------------------------------------------------------
create table if not exists public.generation_job_events (
  id                    uuid primary key default gen_random_uuid(),
  generation_job_id     uuid not null references public.generation_jobs(id) on delete cascade,
  event_type            text not null
                        check (event_type in (
                          'created',
                          'queued',
                          'submitted',
                          'status_polled',
                          'completed',
                          'failed',
                          'cancelled',
                          'operator_note'
                        )),
  message               text,
  raw_payload           jsonb,
  created_at            timestamptz not null default now()
);
create index if not exists generation_job_events_job_idx
  on public.generation_job_events (generation_job_id, created_at);

-- ---------------------------------------------------------------------------
-- audio_fixer_jobs  (manual-only — never auto-created)
--
-- Audio Fixer is opt-in. An audio_fixer_jobs row only exists once the
-- operator deliberately clicks "Run Audio Fixer" in a later phase.
-- Phase 1F leaves this table empty (the seed inserts a historical row
-- for the previous 1080p Pai run for parity with demo-data.ts).
-- ---------------------------------------------------------------------------
create table if not exists public.audio_fixer_jobs (
  id                    uuid primary key default gen_random_uuid(),
  generation_job_id     uuid not null references public.generation_jobs(id) on delete cascade,
  input_asset_id        uuid references public.generated_assets(id) on delete set null,
  provider              text not null default 'enhancor_audio_fixer'
                        check (provider in (
                          'enhancor_audio_fixer',
                          'mock'
                        )),
  status                text not null default 'available'
                        check (status in (
                          'not_needed',
                          'available',
                          'queued',
                          'submitted',
                          'processing',
                          'completed',
                          'failed',
                          'skipped_by_operator'
                        )),
  estimated_credits     integer,
  actual_credits        integer,
  provider_request_id   text,
  result_url            text,
  output_asset_id       uuid references public.generated_assets(id) on delete set null,
  error_message         text,
  raw_request_json      jsonb,
  raw_response_json     jsonb,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index if not exists audio_fixer_jobs_generation_idx
  on public.audio_fixer_jobs (generation_job_id, created_at desc);
create index if not exists audio_fixer_jobs_status_idx
  on public.audio_fixer_jobs (status, created_at desc);

drop trigger if exists audio_fixer_jobs_set_updated_at on public.audio_fixer_jobs;
create trigger audio_fixer_jobs_set_updated_at
  before update on public.audio_fixer_jobs
  for each row execute function app.set_updated_at();

-- ---------------------------------------------------------------------------
-- generated_assets — extend kind enum + add nullable backlinks
--
-- The kind CHECK constraint from migration 001 covered five values:
--   raw_video, audio_fixed_video, thumbnail, caption_srt, export
-- Phase 1F adds three more:
--   static_image  — single-frame product still
--   caption_pack  — packaged set of platform captions (text only)
--   post_creative — assembled post creative (video + caption pack)
--
-- The new generation_job_id / audio_fixer_job_id columns are nullable
-- because historical assets (the Pai 1080p artefacts) predate the job
-- tables — they will be linked retroactively in seed.sql below.
-- ---------------------------------------------------------------------------
do $$
declare
  cname text;
begin
  select tc.constraint_name
    into cname
  from information_schema.table_constraints tc
  where tc.table_schema = 'public'
    and tc.table_name   = 'generated_assets'
    and tc.constraint_type = 'CHECK'
    and exists (
      select 1
      from information_schema.check_constraints cc
      where cc.constraint_schema = tc.constraint_schema
        and cc.constraint_name   = tc.constraint_name
        and cc.check_clause ilike '%raw_video%'
    );
  if cname is not null then
    execute format('alter table public.generated_assets drop constraint %I', cname);
  end if;
end$$;

alter table public.generated_assets
  add constraint generated_assets_kind_check
  check (kind in (
    'raw_video',
    'audio_fixed_video',
    'thumbnail',
    'caption_srt',
    'export',
    'static_image',
    'caption_pack',
    'post_creative'
  ));

alter table public.generated_assets
  add column if not exists generation_job_id  uuid;

alter table public.generated_assets
  add column if not exists audio_fixer_job_id uuid;

-- ---------------------------------------------------------------------------
-- Cross-table foreign keys (deferred to here so every target exists).
-- All three guards make the migration safe to re-apply.
-- ---------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1
    from information_schema.table_constraints
    where table_schema    = 'public'
      and table_name      = 'generated_assets'
      and constraint_name = 'generated_assets_generation_job_fkey'
  ) then
    alter table public.generated_assets
      add constraint generated_assets_generation_job_fkey
      foreign key (generation_job_id)
      references public.generation_jobs(id)
      on delete set null;
  end if;
end$$;

do $$
begin
  if not exists (
    select 1
    from information_schema.table_constraints
    where table_schema    = 'public'
      and table_name      = 'generated_assets'
      and constraint_name = 'generated_assets_audio_fixer_job_fkey'
  ) then
    alter table public.generated_assets
      add constraint generated_assets_audio_fixer_job_fkey
      foreign key (audio_fixer_job_id)
      references public.audio_fixer_jobs(id)
      on delete set null;
  end if;
end$$;

do $$
begin
  if not exists (
    select 1
    from information_schema.table_constraints
    where table_schema    = 'public'
      and table_name      = 'generation_jobs'
      and constraint_name = 'generation_jobs_raw_asset_fkey'
  ) then
    alter table public.generation_jobs
      add constraint generation_jobs_raw_asset_fkey
      foreign key (raw_asset_id)
      references public.generated_assets(id)
      on delete set null;
  end if;
end$$;

create index if not exists generated_assets_generation_job_idx
  on public.generated_assets (generation_job_id) where generation_job_id is not null;
create index if not exists generated_assets_audio_fixer_job_idx
  on public.generated_assets (audio_fixer_job_id) where audio_fixer_job_id is not null;

-- ---------------------------------------------------------------------------
-- Workspace-id resolvers used by the new RLS policies. Each one walks
-- one foreign key chain back to generation_batches.workspace_id so the
-- policies stay readable. security_definer + an explicit search_path
-- match the Phase 1E pattern in app.workspace_id_for_content. Created
-- AFTER the tables so check_function_bodies validates cleanly.
-- ---------------------------------------------------------------------------
create or replace function app.workspace_id_for_generation_batch(target uuid)
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select gb.workspace_id
  from public.generation_batches gb
  where gb.id = target
$$;

create or replace function app.workspace_id_for_generation_job(target uuid)
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select gb.workspace_id
  from public.generation_jobs gj
  join public.generation_batches gb on gb.id = gj.batch_id
  where gj.id = target
$$;

create or replace function app.workspace_id_for_audio_fixer_job(target uuid)
returns uuid
language sql
stable
security definer
set search_path = public
as $$
  select gb.workspace_id
  from public.audio_fixer_jobs af
  join public.generation_jobs gj on gj.id = af.generation_job_id
  join public.generation_batches gb on gb.id = gj.batch_id
  where af.id = target
$$;

-- ---------------------------------------------------------------------------
-- RLS — generation_batches  (operator-only, NO client policy)
-- ---------------------------------------------------------------------------
alter table public.generation_batches enable row level security;

drop policy if exists generation_batches_operator_select on public.generation_batches;
create policy generation_batches_operator_select on public.generation_batches
  for select using (app.is_workspace_member(workspace_id));

drop policy if exists generation_batches_operator_modify on public.generation_batches;
create policy generation_batches_operator_modify on public.generation_batches
  for all using (app.is_workspace_member(workspace_id))
         with check (app.is_workspace_member(workspace_id));

-- ---------------------------------------------------------------------------
-- RLS — generation_jobs  (operator-only, NO client policy)
-- ---------------------------------------------------------------------------
alter table public.generation_jobs enable row level security;

drop policy if exists generation_jobs_operator_select on public.generation_jobs;
create policy generation_jobs_operator_select on public.generation_jobs
  for select using (
    app.is_workspace_member(app.workspace_id_for_generation_batch(batch_id))
  );

drop policy if exists generation_jobs_operator_modify on public.generation_jobs;
create policy generation_jobs_operator_modify on public.generation_jobs
  for all using (
    app.is_workspace_member(app.workspace_id_for_generation_batch(batch_id))
  )
  with check (
    app.is_workspace_member(app.workspace_id_for_generation_batch(batch_id))
  );

-- ---------------------------------------------------------------------------
-- RLS — generation_job_events  (operator-only, NO client policy)
-- ---------------------------------------------------------------------------
alter table public.generation_job_events enable row level security;

drop policy if exists generation_job_events_operator_select on public.generation_job_events;
create policy generation_job_events_operator_select on public.generation_job_events
  for select using (
    app.is_workspace_member(app.workspace_id_for_generation_job(generation_job_id))
  );

drop policy if exists generation_job_events_operator_modify on public.generation_job_events;
create policy generation_job_events_operator_modify on public.generation_job_events
  for all using (
    app.is_workspace_member(app.workspace_id_for_generation_job(generation_job_id))
  )
  with check (
    app.is_workspace_member(app.workspace_id_for_generation_job(generation_job_id))
  );

-- ---------------------------------------------------------------------------
-- RLS — audio_fixer_jobs  (operator-only, NO client policy)
-- ---------------------------------------------------------------------------
alter table public.audio_fixer_jobs enable row level security;

drop policy if exists audio_fixer_jobs_operator_select on public.audio_fixer_jobs;
create policy audio_fixer_jobs_operator_select on public.audio_fixer_jobs
  for select using (
    app.is_workspace_member(app.workspace_id_for_generation_job(generation_job_id))
  );

drop policy if exists audio_fixer_jobs_operator_modify on public.audio_fixer_jobs;
create policy audio_fixer_jobs_operator_modify on public.audio_fixer_jobs
  for all using (
    app.is_workspace_member(app.workspace_id_for_generation_job(generation_job_id))
  )
  with check (
    app.is_workspace_member(app.workspace_id_for_generation_job(generation_job_id))
  );

-- generated_assets keeps its existing migration-002 policies unchanged.
-- The new kinds (static_image, caption_pack, post_creative) and the new
-- nullable FK columns do not expand client visibility: the client
-- policy from 002 still filters on kind = 'thumbnail'.

-- ============================================================================
-- TODO(phase-1g):
--   1. Replace the mock-only application code with real Enhancor /
--      Seedance submissions. The job rows already exist; Phase 1G just
--      flips status from 'draft' → 'submitted' and stores
--      provider_request_id + raw_request_json.
--   2. content_items.active_prompt_version_id FK (rolled over from
--      Phase 1E TODO).
--   3. Cost-ledger table aggregating actual_credits across
--      generation_jobs + audio_fixer_jobs per workspace per month.
--   4. Owner-only enforcement on workspace_members + write paths
--      (rolled over from Phase 1B TODO).
--   5. Realtime updates on generation_jobs so the dashboard reflects
--      provider polls without manual refresh.
-- ============================================================================
