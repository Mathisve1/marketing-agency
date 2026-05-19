-- ============================================================================
-- Yuvo OS — multi-clip ad duration planning
-- ============================================================================
-- Migration: 007
-- Status:    FILE ONLY — NOT YET APPLIED. Applying this is a schema
--            change to the live Supabase project and therefore a
--            "deploy"; it requires explicit operator approval. The
--            planning model (web/lib/planning/duration-plan.ts +
--            agents/producer/dashboard/duration_plan.py) works without
--            it; this migration only persists a chosen plan.
--
-- Scope:    The minimal, fully backwards-compatible additions needed to
--           represent a 20/25/30s ad as multiple connected clips under
--           one batch:
--             1. generation_batches.target_duration_seconds (int)
--             2. generation_batches.clip_plan (jsonb)
--             3. generation_jobs.clip_number (int)
--             4. generation_jobs.clip_role (text)
--             5. widen generation_jobs.status CHECK to add 'stitched'
--             6. widen generated_assets.kind CHECK to add
--                'stitched_video' + 'final_video'
--
-- Backwards compatibility (existing Pai V4 / 15s jobs MUST keep working):
--   - Every new column is nullable with no default → existing rows are
--     untouched. A NULL target_duration_seconds / clip_number means
--     "legacy single 15s clip" everywhere in the read path.
--   - CHECK widening only ADDS allowed values; every existing value
--     stays valid. No row is rewritten.
--   - No data migration. No backfill. No trigger changes.
--
-- Idempotency: add-column-if-not-exists + drop/add named CHECK guarded
-- by a catalog lookup, so re-running is safe.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. generation_batches: the whole-ad plan lives on the batch.
--    One batch = one operator "make a 30s ad" action = N clip jobs.
-- ---------------------------------------------------------------------------
alter table public.generation_batches
  add column if not exists target_duration_seconds integer;

alter table public.generation_batches
  add column if not exists clip_plan jsonb;

comment on column public.generation_batches.target_duration_seconds is
  'Operator-chosen ad length: 15 | 20 | 25 | 30. NULL = legacy single '
  '15s batch. Drives clip_plan.';

comment on column public.generation_batches.clip_plan is
  'Compact JSON from web/lib/planning/duration-plan.ts::toClipPlanJson '
  '(mirror: agents/producer/dashboard/duration_plan.py). Records the '
  'clip split, continuation roles, stitch plan, and product-reference '
  'strategy decided BEFORE clip 1 is generated.';

-- Optional sanity bound — only the four supported targets, but NULL
-- still allowed for legacy rows. Wrapped so re-runs do not error.
do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints
    where table_schema = 'public'
      and table_name = 'generation_batches'
      and constraint_name = 'generation_batches_target_duration_ck'
  ) then
    alter table public.generation_batches
      add constraint generation_batches_target_duration_ck
      check (
        target_duration_seconds is null
        or target_duration_seconds in (15, 20, 25, 30)
      );
  end if;
end$$;

-- ---------------------------------------------------------------------------
-- 2. generation_jobs: each clip is still ONE job row (1 paid Seedance
--    call = 1 clip). clip_number orders them within the batch;
--    clip_role mirrors the planner's continuation_role.
-- ---------------------------------------------------------------------------
alter table public.generation_jobs
  add column if not exists clip_number integer;

alter table public.generation_jobs
  add column if not exists clip_role text;

comment on column public.generation_jobs.clip_number is
  '1-based clip index within the batch. NULL = legacy single-clip job '
  '(treated as clip 1 of 1). Clip N is its own paid generation.';

comment on column public.generation_jobs.clip_role is
  'standalone | open_loop | close_loop — mirrors the planner''s '
  'ContinuationRole. NULL = legacy standalone.';

do $$
begin
  if not exists (
    select 1 from information_schema.table_constraints
    where table_schema = 'public'
      and table_name = 'generation_jobs'
      and constraint_name = 'generation_jobs_clip_role_ck'
  ) then
    alter table public.generation_jobs
      add constraint generation_jobs_clip_role_ck
      check (
        clip_role is null
        or clip_role in ('standalone', 'open_loop', 'close_loop')
      );
  end if;
  if not exists (
    select 1 from information_schema.table_constraints
    where table_schema = 'public'
      and table_name = 'generation_jobs'
      and constraint_name = 'generation_jobs_clip_number_ck'
  ) then
    alter table public.generation_jobs
      add constraint generation_jobs_clip_number_ck
      check (clip_number is null or clip_number between 1 and 8);
  end if;
end$$;

-- ---------------------------------------------------------------------------
-- 3. Widen generation_jobs.status to add 'stitched'.
--    The column was created with an inline CHECK in migration 005; it
--    has the default name generation_jobs_status_check. We drop + re-add
--    with the extra value. Every existing value stays valid.
-- ---------------------------------------------------------------------------
do $$
declare
  ck_name text;
begin
  select tc.constraint_name into ck_name
  from information_schema.table_constraints tc
  join information_schema.check_constraints cc
    on cc.constraint_name = tc.constraint_name
   and cc.constraint_schema = tc.table_schema
  where tc.table_schema = 'public'
    and tc.table_name = 'generation_jobs'
    and tc.constraint_type = 'CHECK'
    and cc.check_clause like '%submitted%'
    and cc.check_clause like '%cancelled%'
  limit 1;

  if ck_name is not null then
    execute format(
      'alter table public.generation_jobs drop constraint %I', ck_name
    );
  end if;

  alter table public.generation_jobs
    add constraint generation_jobs_status_check
    check (status in (
      'draft',
      'queued',
      'submitted',
      'processing',
      'completed',
      'failed',
      'cancelled',
      'stitched'
    ));
end$$;

-- ---------------------------------------------------------------------------
-- 4. Widen generated_assets.kind to add the stitched final outputs.
--    Raw per-clip videos remain kind='raw_video'. The concatenated
--    deliverable is kind='stitched_video' (alias 'final_video' allowed
--    for forward flexibility).
-- ---------------------------------------------------------------------------
do $$
declare
  ck_name text;
begin
  select tc.constraint_name into ck_name
  from information_schema.table_constraints tc
  join information_schema.check_constraints cc
    on cc.constraint_name = tc.constraint_name
   and cc.constraint_schema = tc.table_schema
  where tc.table_schema = 'public'
    and tc.table_name = 'generated_assets'
    and tc.constraint_type = 'CHECK'
    and cc.check_clause like '%raw_video%'
    and cc.check_clause like '%thumbnail%'
  limit 1;

  if ck_name is not null then
    execute format(
      'alter table public.generated_assets drop constraint %I', ck_name
    );
  end if;

  alter table public.generated_assets
    add constraint generated_assets_kind_check
    check (kind in (
      'raw_video',
      'audio_fixed_video',
      'thumbnail',
      'caption_srt',
      'export',
      'stitched_video',
      'final_video'
    ));
end$$;

-- ---------------------------------------------------------------------------
-- 5. Index: list a batch's clips in order without a sort.
-- ---------------------------------------------------------------------------
create index if not exists generation_jobs_batch_clip_idx
  on public.generation_jobs (batch_id, clip_number);

-- ============================================================================
-- POST-APPLY VERIFICATION (run manually after applying):
--   select column_name from information_schema.columns
--    where table_name='generation_batches'
--      and column_name in ('target_duration_seconds','clip_plan');   -- 2 rows
--   select column_name from information_schema.columns
--    where table_name='generation_jobs'
--      and column_name in ('clip_number','clip_role');                -- 2 rows
--   -- existing Pai V4 job 4e4e must still read back unchanged:
--   select id,status,clip_number,clip_role from public.generation_jobs
--    where id='4e4e4e4e-4e4e-4e4e-4e4e-4e4e4e4e4e4e';
--      -- expect status=completed, clip_number=NULL, clip_role=NULL
-- ============================================================================
