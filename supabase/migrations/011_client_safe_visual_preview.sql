-- =============================================================================
-- MIGRATION 011 — Client-safe visual preview lifecycle (Phase 4F PROPOSAL)
-- =============================================================================
--
--   STATUS: **PROPOSAL ONLY — DO NOT APPLY YET.**
--
--   This file documents the schema change Phase 4F is proposing to ship
--   when client-visible visual sharing lands. It is NOT applied in the
--   current Phase 4F build chunk. To apply it later, the operator must
--   paste it manually into the Supabase SQL editor and confirm.
--
--   Implements Option A from `docs/client_safe_visual_preview_plan.md`:
--   extend `content_items` with three columns that mirror the existing
--   `client_safe_copy_preview` / `shared_with_client` pattern (Phase 2G,
--   migration 009).
--
--   - client_safe_visual_url            — operator-prepared client-visible
--                                         visual asset URL (PNG/JPG hosted
--                                         on R2 by the future export pipe).
--                                         NULL until the operator
--                                         explicitly prepares it via the
--                                         future server action
--                                         `prepareClientVisualPreviewAction`.
--   - client_safe_visual_thumbnail_url  — optional small thumbnail for
--                                         the portal list view. Same
--                                         operator-prepared discipline as
--                                         the full URL.
--   - visual_preview_status             — explicit status enum mirroring
--                                         the Phase 4E lifecycle. NULL
--                                         until the operator engages.
--
--   No DELETE policy is added. The view extension is folded into a
--   future migration that updates `client_content_items_v` (so the
--   client portal can actually render the new field). This file
--   focuses on the additive table change only.
--
--   SAFETY: every statement is idempotent (`if not exists` / `do $$ if
--   not exists $$`) so re-running is safe. No data is migrated. No row
--   is touched. No paid step.
--
--   APPLICATION CODE: nothing reads or writes these columns in the
--   current build chunk. They become live only after both (a) this
--   migration is applied AND (b) the future Phase 4F server actions
--   (`prepareClientVisualPreviewAction` /
--   `shareVisualPreviewWithClientAction`) land.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 011.1 Columns
-- -----------------------------------------------------------------------------

alter table public.content_items
  add column if not exists client_safe_visual_url           text,
  add column if not exists client_safe_visual_thumbnail_url text,
  add column if not exists visual_preview_status            text;

comment on column public.content_items.client_safe_visual_url is
  'Phase 4F (migration 011). Operator-prepared client-visible visual '
  'asset URL for non-video formats (carousel cover, story cover, '
  'feed / static post, LinkedIn companion). NULL until the operator '
  'explicitly prepares it via prepareClientVisualPreviewAction. '
  'Only the client view should project this column.';

comment on column public.content_items.client_safe_visual_thumbnail_url is
  'Phase 4F (migration 011). Optional small thumbnail URL for portal '
  'list rendering. Same operator-prepared discipline as the full URL.';

comment on column public.content_items.visual_preview_status is
  'Phase 4F (migration 011). Lifecycle of the visual preview: '
  'draft / rendered_internal / approved_internal / '
  'client_preview_prepared / shared_with_client / '
  'approved_by_client / changes_requested_by_client / archived. '
  'NULL until the lifecycle engages.';

-- -----------------------------------------------------------------------------
-- 011.2 Status CHECK constraint (idempotent)
-- -----------------------------------------------------------------------------

do $$
begin
  if not exists (
    select 1
      from pg_constraint
     where conname = 'content_items_visual_preview_status_check'
  ) then
    alter table public.content_items
      add constraint content_items_visual_preview_status_check
      check (
        visual_preview_status is null
        or visual_preview_status in (
          'draft',
          'rendered_internal',
          'approved_internal',
          'client_preview_prepared',
          'shared_with_client',
          'approved_by_client',
          'changes_requested_by_client',
          'archived'
        )
      );
  end if;
end$$;

-- -----------------------------------------------------------------------------
-- 011.3 NOTE — client view update is intentionally separate
-- -----------------------------------------------------------------------------
--
-- A follow-up migration (proposed 012) would `create or replace view
-- public.client_content_items_v` to also project:
--   ci.client_safe_visual_url
--   ci.client_safe_visual_thumbnail_url
-- gated by `where (ci.shared_with_client = true)`.
--
-- Doing the view update in a separate migration keeps the column
-- addition reversible and lets the operator inspect the new fields
-- via service-role queries before any client-visible behaviour
-- changes.
--
-- -----------------------------------------------------------------------------
-- 011.4 RLS — no change in this migration
-- -----------------------------------------------------------------------------
--
-- `public.content_items` already has workspace-scoped RLS via
-- `app.is_workspace_member(workspace_id)`. The three new columns
-- inherit that. The view extension (012, future) is where the
-- client-portal projection will be gated.
--
-- =============================================================================
-- DO NOT APPLY — proposal only. Pin a release-notes entry and explicit
-- operator approval before pasting this into the Supabase SQL editor.
-- =============================================================================
