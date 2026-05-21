-- =============================================================================
-- MIGRATION 012 — `creative_assets` table (Phase 5B PROPOSAL)
-- =============================================================================
--
--   STATUS: **PROPOSAL ONLY — DO NOT APPLY YET.**
--
--   Successor to the original `creative_assets` sketch in
--   `docs/visual_asset_generation_plan.md` (Phase 4B), refined for the
--   Phase 5B storage path convention:
--     visual-assets/{workspace_id}/{content_item_id}/{template_id}/{theme_id}/{filename}
--
--   Why a new table (vs reusing `generated_assets`)?
--
--   - `generated_assets` rows ALWAYS carry a non-null
--     `generation_job_id` (FK to `generation_jobs`). Brief-driven
--     visual previews have no upstream paid job — forcing a fake job
--     id would couple the visual lifecycle to the video pipeline,
--     pollute the jobs dashboard, and break the cost-attribution
--     rollup in `getOwnerOverview.creditsByBrandId`.
--   - `generated_assets.kind` enum doesn't model carousel slides /
--     story frames / template + theme variants.
--   - Internal vs client-safe URL split is cleaner on a fresh table.
--
--   This migration is ADDITIVE only — every statement is idempotent
--   (`if not exists` / `do $$ if not exists $$`). Applying it twice
--   is safe. Reverting it is one `drop table public.creative_assets`
--   away (no data ever lands in this table during Phase 5B).
--
--   Application code in the current Phase 5B chunk does NOT read or
--   write this table. The Phase 5C upload action will be the first
--   real writer; the Phase 5C client-portal view extension will be
--   the first reader.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 012.1 Table
-- -----------------------------------------------------------------------------

create table if not exists public.creative_assets (
  id                       uuid primary key default gen_random_uuid(),
  workspace_id             uuid not null
    references public.workspaces(id) on delete cascade,
  content_item_id          uuid not null
    references public.content_items(id) on delete cascade,

  -- One of:
  --   carousel_slide | story_frame | feed_post | static_image |
  --   linkedin_image | reel_thumbnail | video_thumbnail
  asset_type               text not null,

  -- Resolved preview mode (mirrors VisualPreviewMode in
  -- web/lib/creative/visual-preview-types.ts).
  mode                     text not null,

  -- Recorded template + theme. Both nullable because legacy briefs
  -- (pre-Phase 4E) didn't carry them.
  template_id              text,
  theme_id                 text,

  -- Phase 5B storage convention. The storage_key is the canonical
  -- R2 object key:
  --   visual-assets/{workspace_id}/{content_item_id}/{template_id}/{theme_id}/{filename}
  -- Built by `buildVisualAssetPath` in
  -- web/lib/creative/visual-asset-paths.ts.
  storage_key              text not null,

  -- Internal URL the Worker uses to read the bytes. Operator-only.
  -- Never projected to the client portal view.
  internal_asset_url       text,

  -- Operator-prepared client-visible URL. Populated only by the
  -- future `prepareClientVisualPreviewAction` (Phase 5C). Stays NULL
  -- until the operator explicitly prepares the client share.
  client_safe_visual_url   text,

  -- Optional thumbnail (future Phase 5C+ feature).
  thumbnail_url            text,

  -- Lifecycle status. See CHECK constraint below for allowed values.
  status                   text not null default 'draft',

  -- Output specifics.
  export_format            text not null default 'png',
  width                    int,
  height                   int,

  -- Per-slide / per-frame indexing. Exactly one is set for carousels
  -- and stories; both NULL for single-card modes.
  slide_number             int,
  frame_number             int,

  -- Phase 5C — variant number for the same (slide|frame, template,
  -- theme) tuple. The operator may export the same slide multiple
  -- times to A/B different theme passes. NULL = the implied first
  -- variant; positive integers index re-exports.
  variant_number           int,

  -- Phase 5C — lifecycle timestamps split out from the single
  -- `status` enum so the queue reader can sort by "last approved",
  -- "last shared", etc. without scanning enums.
  approved_internal_at     timestamptz,
  client_shared_at         timestamptz,

  -- Phase 5C — client's decision once shared. NULL until the client
  -- portal records an `approved` / `changes_requested` decision via
  -- the future `content_approvals` row (same table the copy + video
  -- flows use; reusing it keeps a single approval ledger).
  client_decision_status   text,

  -- Audit + ownership.
  created_by               uuid references public.profiles(id) on delete set null,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

comment on table public.creative_assets is
  'Phase 5B (migration 012). Per-asset row for brief-driven visual '
  'exports (carousel slides, story frames, feed/static posts, '
  'LinkedIn companions, reel/video thumbnails). One row per slide/'
  'frame/single-card export. NOT a substitute for generated_assets '
  '— that table stays scoped to paid video pipeline outputs.';

comment on column public.creative_assets.storage_key is
  'Canonical R2 object key. Schema: '
  'visual-assets/{workspace_id}/{content_item_id}/{template_id}/{theme_id}/{filename}. '
  'Built deterministically by web/lib/creative/visual-asset-paths.ts.';

comment on column public.creative_assets.client_safe_visual_url is
  'Operator-prepared client-visible URL. NULL until the operator '
  'explicitly runs prepareClientVisualPreviewAction. The future '
  'client_creative_assets_v view projects this column only when '
  'visual_preview_status reaches client_preview_prepared / '
  'shared_with_client.';

-- -----------------------------------------------------------------------------
-- 012.2 CHECK constraints (idempotent)
-- -----------------------------------------------------------------------------

do $$
begin
  if not exists (
    select 1 from pg_constraint
     where conname = 'creative_assets_status_check'
  ) then
    alter table public.creative_assets
      add constraint creative_assets_status_check
      check (status in (
        'draft',
        'rendered_internal',
        'approved_internal',
        'client_preview_prepared',
        'shared_with_client',
        'approved_by_client',
        'changes_requested_by_client',
        'archived'
      ));
  end if;

  if not exists (
    select 1 from pg_constraint
     where conname = 'creative_assets_asset_type_check'
  ) then
    alter table public.creative_assets
      add constraint creative_assets_asset_type_check
      check (asset_type in (
        'carousel_slide',
        'story_frame',
        'feed_post',
        'static_image',
        'linkedin_image',
        'reel_thumbnail',
        'video_thumbnail'
      ));
  end if;

  if not exists (
    select 1 from pg_constraint
     where conname = 'creative_assets_format_check'
  ) then
    alter table public.creative_assets
      add constraint creative_assets_format_check
      check (export_format in ('png', 'jpg'));
  end if;

  if not exists (
    select 1 from pg_constraint
     where conname = 'creative_assets_slide_xor_frame_check'
  ) then
    alter table public.creative_assets
      add constraint creative_assets_slide_xor_frame_check
      check (not (slide_number is not null and frame_number is not null));
  end if;

  if not exists (
    select 1 from pg_constraint
     where conname = 'creative_assets_storage_key_shape_check'
  ) then
    alter table public.creative_assets
      add constraint creative_assets_storage_key_shape_check
      check (storage_key ~ '^visual-assets/[0-9a-f-]{36}/[0-9a-f-]{36}/');
  end if;

  if not exists (
    select 1 from pg_constraint
     where conname = 'creative_assets_variant_number_check'
  ) then
    alter table public.creative_assets
      add constraint creative_assets_variant_number_check
      check (variant_number is null or variant_number > 0);
  end if;

  if not exists (
    select 1 from pg_constraint
     where conname = 'creative_assets_client_decision_check'
  ) then
    alter table public.creative_assets
      add constraint creative_assets_client_decision_check
      check (
        client_decision_status is null
        or client_decision_status in (
          'approved',
          'changes_requested',
          'rejected'
        )
      );
  end if;
end$$;

-- -----------------------------------------------------------------------------
-- 012.3 Indexes (idempotent)
-- -----------------------------------------------------------------------------

create index if not exists creative_assets_content_item_idx
  on public.creative_assets (content_item_id);

create index if not exists creative_assets_workspace_status_idx
  on public.creative_assets (workspace_id, status);

create unique index if not exists creative_assets_storage_key_uq
  on public.creative_assets (storage_key);

-- -----------------------------------------------------------------------------
-- 012.4 RLS — operator-only writes / reads (mirror existing tables)
-- -----------------------------------------------------------------------------

alter table public.creative_assets enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies
     where schemaname = 'public'
       and tablename  = 'creative_assets'
       and policyname = 'creative_assets_operator_select'
  ) then
    create policy creative_assets_operator_select
      on public.creative_assets
      for select
      using (app.is_workspace_member(workspace_id));
  end if;

  if not exists (
    select 1 from pg_policies
     where schemaname = 'public'
       and tablename  = 'creative_assets'
       and policyname = 'creative_assets_operator_insert'
  ) then
    create policy creative_assets_operator_insert
      on public.creative_assets
      for insert
      with check (app.is_workspace_member(workspace_id));
  end if;

  if not exists (
    select 1 from pg_policies
     where schemaname = 'public'
       and tablename  = 'creative_assets'
       and policyname = 'creative_assets_operator_update'
  ) then
    create policy creative_assets_operator_update
      on public.creative_assets
      for update
      using (app.is_workspace_member(workspace_id))
      with check (app.is_workspace_member(workspace_id));
  end if;
end$$;

-- NOTE: NO delete policy. Visual assets are append-only from the
-- dashboard's perspective; `archived` is the soft-delete signal.

-- -----------------------------------------------------------------------------
-- 012.5 Touch trigger (mirror existing tables)
-- -----------------------------------------------------------------------------

do $$
begin
  if not exists (
    select 1 from pg_trigger
     where tgname = 'creative_assets_set_updated_at'
  ) then
    create trigger creative_assets_set_updated_at
      before update on public.creative_assets
      for each row execute function app.set_updated_at();
  end if;
end$$;

-- -----------------------------------------------------------------------------
-- 012.6 Client-safe scoped view — PROPOSAL (Phase 5C, NOT applied)
-- -----------------------------------------------------------------------------
--
-- The view below is the operator-only-by-default projection the
-- future client portal will read from. It is intentionally kept in
-- the SAME migration as the table so future operators apply both
-- together — projecting a column that does not exist on the base
-- table would be a deployment trap.
--
-- The view exposes ONLY four columns + the foreign key. It DOES NOT
-- project:
--   - storage_key            (internal)
--   - internal_asset_url     (internal)
--   - creative_brief_*       (operator-only)
--   - template_id / theme_id (internal direction)
--   - status                 (internal lifecycle)
--   - approved_internal_at   (internal sign-off)
--   - created_by             (PII / audit)
--   - any column from prompt_summary (operator-only by the
--     existing `client_content_items_v` discipline)
--
-- It is gated by `client_shared_at is not null` so a row that the
-- operator has prepared but not yet shared is invisible to the
-- client portal even though it exists.

create or replace view public.client_creative_assets_v as
select
  ca.id,
  ca.content_item_id,
  ca.asset_type,
  ca.slide_number,
  ca.frame_number,
  ca.variant_number,
  ca.client_safe_visual_url,
  ca.thumbnail_url,
  ca.client_decision_status,
  ca.client_shared_at
from public.creative_assets ca
where ca.client_shared_at is not null
  and ca.client_safe_visual_url is not null;

comment on view public.client_creative_assets_v is
  'Phase 5C (migration 012, NOT applied). Client-portal projection '
  'of creative_assets. Projects ONLY columns the client may legally '
  'see. Gated by client_shared_at IS NOT NULL — rows the operator '
  'has prepared but not explicitly shared are invisible. NEVER '
  'projects storage_key, internal_asset_url, template_id, theme_id, '
  'status, approved_internal_at, created_by, or anything from '
  'prompt_summary.';

-- -----------------------------------------------------------------------------
-- 012.7 Rollback notes
-- -----------------------------------------------------------------------------
--
-- This migration is reversible without data loss for as long as the
-- table is empty:
--
--   drop view if exists public.client_creative_assets_v;
--   drop table if exists public.creative_assets cascade;
--
-- Once the operator starts inserting rows, the rollback becomes a
-- normal data-aware decision (archive vs delete). Phase 5C does NOT
-- insert any rows; Phase 5D may.
--
-- =============================================================================
-- DO NOT APPLY — proposal only. Pin a release-notes entry and explicit
-- operator approval before pasting this into the Supabase SQL editor.
-- =============================================================================
